"""Local web UI for recorded calls: list, transcript with doubtful words, click to listen.

Everything is served from this machine (no CDN, no external requests) and bound to 127.0.0.1.
A recording is the set of files written by ``parola record`` in one folder:
``<name>.json`` (merged transcript), ``<name>.mic.flac``, ``<name>.system.flac`` (``.wav`` until
compressed, decision 0024) and
``<name>.capture.json``.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from parola import editing
from parola.asr import release_models
from parola.audio import channel_file
from parola.audio.loudness import channel_loudness
from parola.audio.playback import forget, playable
from parola.capture.recorder import CHANNELS, LABELS, CaptureError, marked_text, to_dialogue
from parola.capture.session import RecordingSession, SessionError
from parola.models import ModelDownload
from parola.schema import Transcript, format_timestamp
from parola.speakers import label as speaker_label
from parola.speakers import name as speaker_name

STATIC = Path(__file__).resolve().parent / "static"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Deleted calls are moved here, one folder per deletion, never removed by the app (decision 0017).
TRASH = ".trash"
_TRASHED = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)--\d{8}-\d{6}-\d{6}$")
# Every file a recording owns. Listed, not globbed: "prova.*" would also catch "prova.2.json".
OWNED = (
    ".json",
    ".asr.json",
    ".capture.json",
    ".txt",
    *(f".{c}{ext}" for c in ("mic", "system", "mixed") for ext in (".wav", ".flac")),
)


def _check(name: str) -> str:
    if not _NAME.match(name) or ".." in name:
        raise HTTPException(status_code=404, detail="no such recording")
    return name


def summary(folder: Path, name: str, t: Transcript | None = None) -> dict:
    t = t or Transcript.load(folder / f"{name}.json")
    words = [w for s in t.segments for w in s.words]
    capture_path = folder / f"{name}.capture.json"
    capture = json.loads(capture_path.read_text()) if capture_path.exists() else {}
    return {
        "name": name,
        # No title yet: an imported file shows its own name ("Intervista Marco"), not the slug.
        "title": t.metadata.title
        or (Path(capture["source"]).stem if capture.get("source") else name),
        "source": capture.get("source"),  # name of the imported file, if it was one
        "started_at": capture.get("started_at") or t.metadata.created_at.isoformat(),
        "duration": t.metadata.duration,
        "channels": sorted({s.channel for s in t.segments if s.channel}),
        "segments": len(t.segments),
        "words": len(words),
        "doubtful": sum(w.doubtful for w in words),
        "to_review": len(editing.doubt_runs(t)),  # runs of doubtful words, as the UI counts them
        "checked": sum(w.checked for w in words),
        "edits": len(t.edits),
        "model": t.metadata.model,
        # A channel that recorded silence or the wrong device (recorder.save_warnings): the
        # call view says it, so a dead mic is not discovered weeks later.
        "warnings": capture.get("warnings") or [],
    }


def summary_or_broken(folder: Path, name: str) -> dict:
    """``summary``, or a degraded row when the transcript does not load: the call stays in the
    list (its audio is still on disk) instead of taking every other call down with a 500."""
    try:
        return summary(folder, name)
    except Exception as e:
        print(f"warning: {name}.json does not load ({e}); listed without its transcript",
              file=sys.stderr)  # fmt: skip
        capture = {}
        capture_path = folder / f"{name}.capture.json"
        if capture_path.exists():
            with contextlib.suppress(Exception):
                capture = json.loads(capture_path.read_text())
        json_path = folder / f"{name}.json"
        mtime = datetime.fromtimestamp(json_path.stat().st_mtime, tz=UTC) if json_path.exists() \
            else datetime.now(UTC)  # fmt: skip
        return {
            "name": name,
            "title": Path(capture["source"]).stem if capture.get("source") else name,
            "source": capture.get("source"),
            "started_at": capture.get("started_at") or mtime.isoformat(),
            "duration": capture.get("elapsed_seconds") or 0.0,
            "channels": [],
            "segments": 0,
            "words": 0,
            "doubtful": 0,
            "to_review": 0,
            "checked": 0,
            "edits": 0,
            "model": None,
            "error": str(e),
        }


class RecordRequest(BaseModel):
    name: str | None = None
    lang: str | None = None
    mic: bool = True
    system: bool = True


class Rename(BaseModel):
    title: str


class OutputChoice(BaseModel):
    uid: str


class SpeakerChange(BaseModel):
    segment: int
    speaker: str | None = None  # an existing voice id, "new" for a new voice, None for none


class VoiceName(BaseModel):
    id: str
    name: str


class Span(BaseModel):
    segment: int
    start: int
    end: int
    text: str | None = None


def recordings(folder: Path) -> list[str]:
    """Names of the recordings in ``folder``: transcripts with at least one channel file."""
    names = []
    for p in sorted(folder.glob("*.json")):
        name = p.stem
        if name.endswith((".capture", ".asr")) or not _NAME.match(name):
            continue
        if any(channel_file(folder, name, c) for c in CHANNELS):
            names.append(name)
    return names


# Audio and video that ffmpeg reads; anything else is refused before it is uploaded.
IMPORT_EXTENSIONS = frozenset([
    ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".oga", ".webm", ".aiff", ".aif",
    ".caf", ".wma", ".amr", ".mp4", ".mov", ".m4v", ".mkv", ".avi",
    ".qta",  # iPhone Voice Memos (QuickTime audio)
])  # fmt: skip


def owned_files(folder: Path, name: str) -> list[Path]:
    return [folder / f"{name}{s}" for s in OWNED if (folder / f"{name}{s}").exists()]


def to_markdown(t: Transcript, name: str) -> str:
    lines = [f"# {name}", ""]
    for s in t.segments:
        label = speaker_label(t, s, LABELS)
        who = f"**{label}** · " if label else ""
        lines.append(f"{who}{format_timestamp(s.start)[:8]} — {marked_text(s)}")
        lines.append("")
    return "\n".join(lines)


def create_app(folder: Path, session: RecordingSession | None = None,
               models: ModelDownload | None = None, listener=None,
               live_backend=None) -> FastAPI:  # fmt: skip
    """``listener`` and ``live_backend`` (the Whisper of the live subtitles) are for the tests."""
    from contextlib import asynccontextmanager

    from parola.totti.listen import Listener

    session = session or RecordingSession(folder)
    models = models or ModelDownload()
    # Live subtitles without a recording (decision 0042): the system audio kept in memory.
    listener = listener or Listener()
    # Visible versioning (decision 0045): the engine says which commit it booted from, and
    # the status poll says when the repo has moved on (a process older than the code on
    # disk cost wasted diagnoses; now every surface declares itself).
    from parola.buildinfo import BuildInfo
    from parola.totti.translator import REPO_ROOT

    build = BuildInfo(REPO_ROOT)
    print(f"PAROLA engine at commit {build.booted or 'unknown'}", flush=True)
    # "on": the person wants subtitles, while a recording or the listening lasts. The server
    # holds it, so the web bar and the native floater cannot disagree. "lang" likewise: "it"
    # (translated, committed lines only) or "en" (the original, with the provisional tail).
    subtitles: dict = {"loop": None, "on": False, "lang": "it"}

    @asynccontextmanager
    async def lifespan(app):
        yield
        session.abort()  # never leave the capture helper running
        listener.stop()

    app = FastAPI(
        title="parola", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    @app.get("/api/models")
    def models_status() -> dict:
        """Whether the speech models are on disk, and the first download's progress."""
        return models.status()

    @app.post("/api/models/download")
    def models_download() -> dict:
        """The first download, only when the person asked for it in the app."""
        return models.start()

    def require_models() -> None:
        # Nothing is downloaded behind the person's back: the app asks first (decision 0029).
        if models.status()["state"] != "ready":
            raise HTTPException(status_code=409, detail="models not downloaded yet")

    @app.get("/api/record/status")
    def record_status() -> dict:
        # The models' state too: the menu bar of the native app reads only this.
        st = {**session.status(), "models": models.status()["state"],
              "build": build.status()}  # fmt: skip
        listening = listener.active
        if listening and st["state"] != "recording":
            st["levels"] = listener.levels
        st["listening"] = listening
        st["listen_error"] = listener.error
        st["subtitles"] = subtitles["on"] and (st["state"] == "recording" or listening)
        return st

    @app.get("/api/outputs")
    def audio_outputs() -> list[dict]:
        """The Mac's output devices, for the settings gear: {uid, name, kind, current} rows."""
        from parola.capture import devices

        try:
            return devices.outputs()
        except CaptureError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @app.post("/api/outputs")
    def choose_output(req: OutputChoice) -> list[dict]:
        """Make ``uid`` the Mac's default output (what Control Center's switcher does): the
        playback in the WebView follows it, WebKit has no per-element ``setSinkId``."""
        from parola.capture import devices

        try:
            devices.set_output(req.uid)
        except CaptureError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return devices.outputs()

    @app.post("/api/record/start")
    def record_start(req: RecordRequest) -> dict:
        require_models()
        lang = req.lang if req.lang in ("it", "en") else None
        # From listening to recording (decision 0042): the recording starts now, nothing heard
        # before goes into it, and the subtitles carry on on the new system wav.
        was_listening = listener.active
        old = subtitles["loop"]
        if was_listening:
            stop_listening(release=False)
        try:
            session.start(req.name or None, lang, req.mic, req.system)
        except SessionError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        subtitles["on"] = was_listening
        if was_listening:
            name = session.status()["name"]
            start_loop(folder / f"{name}.system.wav",
                       lambda: session.status()["state"] == "recording", after=old)  # fmt: skip
        return record_status()

    @app.post("/api/record/{action}")
    def record_control(action: str) -> dict:
        controls = {"stop": session.stop, "pause": session.pause, "resume": session.resume}
        if action not in controls:
            raise HTTPException(status_code=404, detail="no such action")
        try:
            controls[action]()
            return record_status()
        except SessionError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    # ------------------------------------------------ ToTTI: live subtitles (decision 0038)

    from parola.totti import DocumentTranslation, SubtitleLoop, Translator

    translator = Translator()  # the student (or the base): the only live engine

    def start_loop(source, running, after: SubtitleLoop | None = None) -> SubtitleLoop:
        """A subtitle loop on ``source``; it starts once ``after`` (the loop it replaces) has
        finished its last tick, so two never run Whisper at the same time."""
        if live_backend is not None:
            backend = live_backend()
        else:
            from parola.asr import MLXWhisperBackend

            backend = MLXWhisperBackend(vad=True)
        loop = SubtitleLoop(source, translator, backend, running=running,
                            lang=subtitles["lang"])  # fmt: skip
        if after is not None:
            loop.revision = after.revision + 1  # the clients redraw on a revision change
        subtitles["loop"] = loop

        def go() -> None:
            if after is not None:
                after.stop()
                after.join(10)
            loop.start()

        if after is None:
            go()
        else:
            threading.Thread(target=go, daemon=True).start()
        return loop

    def stop_listening(release: bool) -> None:
        loop = subtitles["loop"]
        listener.stop()  # the loop sees it and ends by itself
        subtitles["on"] = False
        if loop is None:
            return
        loop.stop()
        if release:
            # The speech models have no reason to stay in memory (decision 0027), once the
            # loop's last tick is over and if nothing else came to use them meanwhile.
            def free() -> None:
                loop.join(10)
                busy = session.status()["state"] in ("recording", "transcribing")
                if not busy and not listener.active:
                    release_models()

            threading.Thread(target=free, daemon=True).start()

    # ToTTI v1 is the only user-facing translation engine (decision 0044): the teacher
    # stays on disk for distillation only, never behind an endpoint. Translations cached
    # under other engines' folders in .translations/ are simply never read again.
    translations = DocumentTranslation(folder, translator)

    @app.get("/api/recordings/{name}/translation")
    def translation_status(name: str) -> dict:
        # "available" says whether the translation model is on disk, like /api/subtitles.
        status = translations.status(_check(name), translator)
        status.setdefault("engine", translator.name)
        return {"available": translator.available(), **status}

    @app.post("/api/recordings/{name}/translate")
    def translation_start(name: str) -> dict:
        if not translator.available():
            raise HTTPException(status_code=409, detail="translation model not on disk")
        if not (folder / f"{_check(name)}.json").exists():
            raise HTTPException(status_code=404, detail="no such recording")
        try:
            status = translations.start(name, translator)
            status.setdefault("engine", translator.name)
            return {"available": True, **status}
        except RuntimeError as e:  # another call is being translated right now
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/api/subtitles")
    def subtitles_status() -> dict:
        loop = subtitles["loop"]
        state = loop.status() if loop else {"active": False, "lines": [], "tail": "",
                                            "lang": subtitles["lang"], "revision": 0,
                                            "error": ""}  # fmt: skip
        # "available" says whether the translation model is on disk: the UI hides the
        # toggle without it.
        return {"available": translator.available(), **state}

    @app.post("/api/subtitles/lang/{lang}")
    def subtitles_lang(lang: str) -> dict:
        """The strip's language: "it" (translated) or "en" (original, with the live tail)."""
        if lang not in ("it", "en"):
            raise HTTPException(status_code=404, detail="no such language")
        subtitles["lang"] = lang
        if subtitles["loop"] is not None:
            subtitles["loop"].lang = lang
        return subtitles_status()

    @app.post("/api/subtitles/start")
    def subtitles_start() -> dict:
        if session.status()["state"] != "recording":
            raise HTTPException(status_code=409, detail="not recording")
        if not translator.available():
            raise HTTPException(status_code=409, detail="translation model not on disk")
        require_models()
        loop = subtitles["loop"]
        if loop is None or not loop.active:
            name = session.status()["name"]
            start_loop(folder / f"{name}.system.wav",
                       lambda: session.status()["state"] == "recording")  # fmt: skip
        subtitles["on"] = True
        return subtitles_status()

    @app.post("/api/subtitles/stop")
    def subtitles_stop() -> dict:
        subtitles["on"] = False
        if subtitles["loop"] is not None:
            subtitles["loop"].stop()
        return subtitles_status()

    @app.post("/api/listen/start")
    def listen_start() -> dict:
        """Subtitles without recording (decision 0042): the system audio, kept in memory."""
        if not translator.available():
            raise HTTPException(status_code=409, detail="translation model not on disk")
        require_models()
        state = session.status()["state"]
        if state in ("recording", "transcribing"):
            raise HTTPException(status_code=409, detail=f"already {state}")
        if not listener.active:
            try:
                listener.start()
            except RuntimeError as e:  # the helper could not be built
                raise HTTPException(status_code=409, detail=str(e)) from e
        loop = subtitles["loop"]
        if loop is None or not loop.active:
            start_loop(listener.tail, lambda: listener.active)
        subtitles["on"] = True
        return record_status()

    @app.post("/api/listen/stop")
    def listen_stop() -> dict:
        if listener.active:
            stop_listening(release=True)
        return record_status()

    @app.post("/api/import")
    async def import_file(request: Request, filename: str, lang: str | None = None,
                          modified: int | None = None) -> dict:  # fmt: skip
        """Body = the raw file. ``modified`` (ms) is the file's date, used as the call's date."""
        ext = Path(filename).suffix.lower()
        if ext not in IMPORT_EXTENSIONS:
            raise HTTPException(status_code=415, detail=f"not an audio or video file: {filename}")
        require_models()
        if session.status()["state"] in ("recording", "transcribing"):
            raise HTTPException(status_code=409, detail=f"already {session.status()['state']}")
        if listener.active:  # two Whisper at once would fight for the GPU (decision 0042)
            raise HTTPException(status_code=409, detail="already listening")
        uploads = folder / ".uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        tmp = uploads / f"{datetime.now():%Y%m%d-%H%M%S-%f}{ext}"
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        started = datetime.fromtimestamp(modified / 1000, UTC) if modified else None
        language = lang if lang in ("it", "en") else None
        try:
            return session.import_file(tmp, Path(filename).name, language, started)
        except SessionError as e:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    cache: dict[Path, tuple[float, Transcript]] = {}

    def cached(path: Path) -> Transcript:
        """Transcripts parsed once per change, so a search per keystroke stays fast."""
        mtime = path.stat().st_mtime
        if path not in cache or cache[path][0] != mtime:
            cache[path] = (mtime, Transcript.load(path))
        return cache[path][1]

    learned: dict = {"sig": None, "terms": {}}

    @app.get("/api/suggestions")
    def suggestions(text: str) -> list[dict]:
        """Terms learned from the person's own corrections that ``text`` may be (decision 0025).
        Read from the calls' edit logs, recomputed when a call changes."""
        from dataclasses import asdict

        from parola.learned import learn, suggest

        paths = [folder / f"{n}.json" for n in recordings(folder)]
        sig = tuple((p.name, p.stat().st_mtime) for p in paths)
        if sig != learned["sig"]:
            learned["terms"] = learn([cached(p) for p in paths])
            learned["sig"] = sig
        return [asdict(s) for s in suggest(text, learned["terms"])]

    @app.get("/api/search")
    def search(q: str, limit: int = 3) -> list[dict]:
        """Calls whose words (or title) match ``q``, newest first, with their first hits."""
        from dataclasses import asdict

        from parola.search import find, fold, query_tokens

        if not query_tokens(q):
            return []
        wanted = " ".join(query_tokens(q))
        out = []
        for name in recordings(folder):
            try:
                hits = find(cached(folder / f"{name}.json"), q)
                s = summary(folder, name)
            except Exception:  # unloadable transcript: listed by list_recordings, not searchable
                continue
            in_title = wanted in " ".join(fold(w) for w in s["title"].split())
            if hits or in_title:
                out.append({**s, "count": len(hits), "hits": [asdict(h) for h in hits[:limit]]})
        return sorted(out, key=lambda r: r["started_at"], reverse=True)

    @app.get("/api/recordings")
    def list_recordings() -> list[dict]:
        out = [summary_or_broken(folder, n) for n in recordings(folder)]
        return sorted(out, key=lambda r: r["started_at"], reverse=True)

    @app.get("/api/recordings/{name}")
    def get_recording(name: str) -> dict:
        name = _check(name)
        path = folder / f"{name}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            # Degraded summary when the transcript does not validate: the raw segments are
            # still readable and the audio playable.
            data["summary"] = summary_or_broken(folder, name)
            data["provisional"] = None
        elif preview := session.provisional(name):
            # Still being transcribed: the text so far, read-only (decision 0031). The edit
            # endpoints read the file on disk, which does not exist yet.
            stage, t = preview
            data = json.loads(t.to_json())
            data["summary"] = summary(folder, name, t)
            data["provisional"] = stage
        else:
            raise HTTPException(status_code=404, detail="no such recording")
        try:
            t = Transcript.model_validate(data)
            ids = sorted({sg.speaker for sg in t.segments if sg.speaker},
                         key=lambda v: (v.split(":")[0], int(v.split(":")[1])))  # fmt: skip
            data["voices"] = {v: speaker_name(t, v) for v in ids}
        except ValidationError:  # a file that does not validate is still shown, without voices
            data["voices"] = {}
        files = {c: p for c in CHANNELS if (p := channel_file(folder, name, c))}
        data["audio"] = {c: f"/audio/{name}/{c}" for c in files}
        # How loud each channel is: the player raises a quiet mic track to the others' level
        # at playback (static/helpers.js). Not while recording: the file is still growing.
        data["loudness"] = {} if data["provisional"] else channel_loudness(folder, files)
        return data

    def _load(name: str) -> tuple[Transcript, Path]:
        name = _check(name)
        path = folder / f"{name}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="no such recording")
        return Transcript.load(path), path

    def _store(t: Transcript, path: Path, name: str) -> dict:
        editing.save(t, path)
        (folder / f"{name}.txt").write_text(to_dialogue(t), encoding="utf-8")
        return get_recording(name)

    @app.post("/api/recordings/{name}/correct")
    def correct(name: str, span: Span) -> dict:
        t, path = _load(name)
        try:
            editing.correct(t, span.segment, span.start, span.end, span.text or "")
        except editing.EditError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _store(t, path, name)

    @app.post("/api/recordings/{name}/confirm")
    def confirm(name: str, span: Span) -> dict:
        t, path = _load(name)
        try:
            editing.confirm(t, span.segment, span.start, span.end)
        except editing.EditError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _store(t, path, name)

    @app.post("/api/recordings/{name}/rename")
    def rename(name: str, req: Rename) -> dict:
        t, path = _load(name)
        title = " ".join(req.title.split())[:120]
        t.metadata.title = title or None
        t.save(path)  # a display change: not an edit of the transcript, no .asr.json needed
        return get_recording(name)

    @app.delete("/api/recordings/{name}")
    def delete(name: str) -> dict:
        """Move the call's files to the trash folder: the app never deletes audio for real."""
        _load(name)
        st = session.status()
        if st["name"] == name and st["state"] in ("recording", "transcribing"):
            raise HTTPException(status_code=409, detail="the call is still being recorded")
        entry = f"{name}--{datetime.now():%Y%m%d-%H%M%S-%f}"
        dest = folder / TRASH / entry
        dest.mkdir(parents=True)
        for f in owned_files(folder, name):
            shutil.move(f, dest / f.name)
        forget(folder, name, CHANNELS)  # the copy for the player goes too
        return {"name": name, "trashed": entry}

    @app.post("/api/trash/{entry}/restore")
    def restore(entry: str) -> dict:
        m = _TRASHED.match(entry)
        src = folder / TRASH / entry
        if not m or not src.is_dir():
            raise HTTPException(status_code=404, detail="not in the trash")
        name = m.group(1)
        if (folder / f"{name}.json").exists():
            raise HTTPException(status_code=409, detail=f"a call named {name} exists")
        for f in src.iterdir():
            shutil.move(f, folder / f.name)
        src.rmdir()
        return summary(folder, name)

    @app.get("/api/recordings/{name}/title-suggestion")
    def title_suggestion(name: str) -> dict:
        """A title from the call's key phrases; nothing is saved (decision 0020)."""
        from parola.keywords import key_phrases, suggest_title

        t, _ = _load(name)
        return {"title": suggest_title(t), "phrases": [p.text for p in key_phrases(t, 8)]}

    @app.post("/api/recordings/{name}/speaker")
    def change_speaker(name: str, req: SpeakerChange) -> dict:
        """The person moves one turn to another voice (decision 0026); undone like any edit."""
        t, path = _load(name)
        if not 0 <= req.segment < len(t.segments):
            raise HTTPException(status_code=400, detail=f"no segment {req.segment}")
        seg = t.segments[req.segment]
        if seg.channel not in ("system", "mixed"):
            raise HTTPException(status_code=400, detail="the microphone is always you")
        speaker = req.speaker
        if speaker == "new":
            used = [int(v.split(":")[1]) for v in {sg.speaker for sg in t.segments}
                    if v and v.startswith(f"{seg.channel}:")]  # fmt: skip
            speaker = f"{seg.channel}:{max(used, default=0) + 1}"
        elif speaker is not None and not speaker.startswith(f"{seg.channel}:"):
            raise HTTPException(status_code=400, detail="a voice of another channel")
        editing.set_speaker(t, req.segment, speaker)
        return _store(t, path, name)

    @app.post("/api/recordings/{name}/voices")
    def name_voice(name: str, req: VoiceName) -> dict:
        """A name for a voice, for the whole call: a label, not an edit of the transcript."""
        t, path = _load(name)
        title = " ".join(req.name.split())[:60]
        if title:
            t.metadata.speakers[req.id] = title
        else:
            t.metadata.speakers.pop(req.id, None)
        t.save(path)
        (folder / f"{name}.txt").write_text(to_dialogue(t), encoding="utf-8")
        return get_recording(name)

    @app.post("/api/recordings/{name}/undo")
    def undo(name: str) -> dict:
        t, path = _load(name)
        if editing.undo(t) is None:
            raise HTTPException(status_code=400, detail="nothing to undo")
        return _store(t, path, name)

    @app.get("/audio/{name}/{channel}")
    def audio(name: str, channel: str) -> FileResponse:
        name = _check(name)
        path = channel_file(folder, name, channel) if channel in CHANNELS else None
        if path is None:
            raise HTTPException(status_code=404, detail="no such audio")
        # WAV, not the stored FLAC: WebKit (the native app) cannot seek in FLAC.
        return FileResponse(playable(folder, path), media_type="audio/wav")

    @app.get("/export/{name}.{fmt}")
    def export(name: str, fmt: str):
        name = _check(name)
        path = folder / f"{name}.json"
        if not path.exists() or fmt not in ("txt", "md", "json", "docx"):
            raise HTTPException(status_code=404, detail="no such export")
        t = Transcript.load(path)
        title = t.metadata.title or name
        # The file is named after the title the user gave; plain name as the ASCII fallback.
        stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title).strip() or name
        disposition = f"attachment; filename=\"{name}.{fmt}\"; filename*=UTF-8''{quote(stem)}.{fmt}"
        headers = {"Content-Disposition": disposition}
        if fmt == "json":
            return PlainTextResponse(t.to_json(), media_type="application/json", headers=headers)
        if fmt == "docx":
            from fastapi.responses import Response

            from parola.app.export import to_docx

            return Response(
                to_docx(t, title),
                headers=headers,
                media_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            )
        text = to_dialogue(t) if fmt == "txt" else to_markdown(t, title)
        return PlainTextResponse(text, media_type="text/plain; charset=utf-8", headers=headers)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
