"""Whole-call EN→IT translation (ToTTI, decision 0038: the non-simultaneous tool).

The translation is a derived, declared layer: computed from the verbatim segments, cached in
``.translations/<engine>/<name>.json`` next to the calls and keyed by the transcript file's
mtime and size — a correction rewrites the transcript, so the translation is derived again.
Every engine keeps its own cache, so switching between the student and the teacher never
throws away work. The translation never touches the transcript itself.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from parola.schema import Transcript
from parola.totti.translator import Translator

# Segments translated per translate_batch call: small enough for live progress.
CHUNK = 16


def targets(t: Transcript) -> list[int]:
    """The indices of the segments to translate: the English ones."""
    return [i for i, s in enumerate(t.segments)
            if (s.lang or t.metadata.language_detected) == "en" and s.text.strip()]  # fmt: skip


class DocumentTranslation:
    """Translates one call at a time in a thread and caches the result on disk.

    ``translator`` is the default engine; every method takes an ``engine`` override, so the
    person can choose the fast student or the slower, better teacher per request.
    """

    def __init__(self, folder: Path, translator: Translator) -> None:
        self.folder = folder
        self.translator = translator
        self._lock = threading.Lock()
        self._name: str | None = None  # the call being translated now
        self._engine_name = ""
        self._done = 0
        self._total = 0
        self._errors: dict[str, str] = {}

    # ---------------------------------------------------------------- state

    def _cache_path(self, name: str, engine_name: str) -> Path:
        return self.folder / ".translations" / engine_name / f"{name}.json"

    def _fresh(self, name: str, engine_name: str) -> dict | None:
        """The cached translation, only while the transcript is still the one it came from."""
        transcript = self.folder / f"{name}.json"
        cache = self._cache_path(name, engine_name)
        if not transcript.exists() or not cache.exists():
            return None
        st = transcript.stat()
        try:
            data = json.loads(cache.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("mtime") != st.st_mtime or data.get("size") != st.st_size:
            return None
        # A cache that does not declare the engine that produced it is never served: the
        # view must always be able to say who translated (and a file in the wrong engine's
        # folder must not pass for that engine). It regenerates instead.
        if data.get("engine") != engine_name:
            return None
        return data

    def status(self, name: str, engine=None) -> dict:
        engine = engine or self.translator
        with self._lock:
            if self._name == name:
                return {"state": "working", "done": self._done, "total": self._total,
                        "engine": self._engine_name}  # fmt: skip
            if error := self._errors.get(name):
                return {"state": "error", "message": error}
        if data := self._fresh(name, getattr(engine, "name", "unknown")):
            return {"state": "ready", "lines": data["lines"], "engine": data.get("engine")}
        return {"state": "missing"}

    # ---------------------------------------------------------------- translating

    def start(self, name: str, engine=None) -> dict:
        """Translate ``name`` in a thread, unless a fresh translation is already on disk."""
        engine = engine or self.translator
        engine_name = getattr(engine, "name", "unknown")
        if data := self._fresh(name, engine_name):
            return {"state": "ready", "lines": data["lines"], "engine": data.get("engine")}
        with self._lock:
            if self._name == name:
                return {"state": "working", "done": self._done, "total": self._total,
                        "engine": self._engine_name}  # fmt: skip
            if self._name is not None:
                raise RuntimeError(f"already translating {self._name}")
            self._name = name
            self._engine_name = engine_name
            self._done, self._total = 0, 0
            self._errors.pop(name, None)
        threading.Thread(target=self._run, args=(name, engine), daemon=True).start()
        return {"state": "working", "done": 0, "total": 0, "engine": engine_name}

    def _run(self, name: str, engine) -> None:
        try:
            self.translate(name, engine)
        except Exception as e:  # shown in the UI; asking again retries
            self._errors[name] = str(e)
        finally:
            # A heavy engine (the teacher) frees its memory once the job is done.
            getattr(engine, "unload", lambda: None)()
            with self._lock:
                self._name = None
                self._engine_name = ""

    def translate(self, name: str, engine=None) -> dict:
        """The whole translation, synchronously: segment index → Italian, cached on disk.

        The cache also keeps every line keyed by its source text, so after a correction only
        the segments whose text actually changed go back to the model: the rest is reused.
        """
        engine = engine or self.translator
        engine_name = getattr(engine, "name", "unknown")
        transcript = self.folder / f"{name}.json"
        st = transcript.stat()  # before reading: a rewrite during the run leaves a stale key
        t = Transcript.load(transcript)
        todo = targets(t)
        cache = self._cache_path(name, engine_name)
        known: dict[str, str] = {}  # source text → Italian, from this engine's stale cache
        if cache.exists():
            try:
                known = json.loads(cache.read_text()).get("by_text", {})
            except (OSError, json.JSONDecodeError):
                known = {}
        lines: dict[str, str] = {}
        by_text: dict[str, str] = {}
        missing: list[int] = []
        for i in todo:
            text = t.segments[i].text.strip()
            if text in known:
                lines[str(i)] = by_text[text] = known[text]
            else:
                missing.append(i)
        with self._lock:
            self._total = len(missing)
        for at in range(0, len(missing), CHUNK):
            chunk = missing[at : at + CHUNK]
            texts = [t.segments[i].text.strip() for i in chunk]
            for i, it in zip(chunk, engine.translate(texts), strict=True):
                lines[str(i)] = it
                by_text[t.segments[i].text.strip()] = it
            with self._lock:
                self._done = at + len(chunk)
        data = {
            "mtime": st.st_mtime,
            "size": st.st_size,
            "engine": engine_name,
            "lines": lines,
            "by_text": by_text,
        }
        cache.parent.mkdir(parents=True, exist_ok=True)
        self._prune()
        cache.write_text(json.dumps(data, ensure_ascii=False))
        return data

    def _prune(self) -> None:
        """Drop cached translations of calls that no longer exist, and the pre-engine files."""
        folder = self.folder / ".translations"
        for legacy in folder.glob("*.json"):  # flat caches from before the per-engine layout
            legacy.unlink(missing_ok=True)
        for cache in folder.glob("*/*.json"):
            if not (self.folder / cache.name).exists():
                cache.unlink(missing_ok=True)
