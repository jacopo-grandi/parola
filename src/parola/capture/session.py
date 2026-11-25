"""A recording started and stopped from the app, transcribed in the background.

One session at a time: ``start`` launches the capture helper with live levels, ``pause`` and
``resume`` cut a stretch out of both tracks, ``stop`` ends the capture and transcribes the call in
a thread (same pipeline as ``parola record``), ``status`` tells the UI what is happening.
``import_file`` runs the same transcription on an audio or video file, as one ``mixed`` track.
"""

from __future__ import annotations

import contextlib
import json
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from parola.asr import release_models
from parola.audio import extract_audio, wav_duration
from parola.capture.recorder import Recording, build_helper, to_dialogue, transcribe_recording
from parola.schema import Transcript

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def default_backends() -> tuple:
    from parola.asr import DEFAULT_MODEL, MLXWhisperBackend
    from parola.asr.parakeet_backend import PARAKEET_MODEL, ParakeetBackend
    from parola.models import offline_if_cached

    offline_if_cached([DEFAULT_MODEL, PARAKEET_MODEL])
    return MLXWhisperBackend(vad=True), ParakeetBackend()


class SessionError(RuntimeError):
    pass


class RecordingSession:
    def __init__(self, folder: Path, binary: Path | None = None,
                 backends: Callable[[], tuple] = default_backends) -> None:  # fmt: skip
        self.folder = folder
        self._binary = binary
        self._backends = backends
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.state = "idle"  # idle | recording | transcribing | done | error
        self.name: str | None = None
        self.language: str | None = None
        self.started: float | None = None
        self.paused_at: float | None = None  # set while paused
        self.paused_total = 0.0
        self._ready = False  # the helper said it is recording: its signal handlers are installed
        self.levels: dict[str, float] = {}
        self.message = ""
        self.step = (0, 0)
        self.source: str | None = None  # file name of an import, None for a recording
        # The transcript so far while transcribing, with its stage (decision 0031): kept in
        # memory only, so nothing can edit it and nothing stale is left if the app stops.
        self._preview: tuple[str, Transcript] | None = None
        self._revision = 0
        # Only the person recording and one other (decision 0058): no telling voices apart.
        # Chosen per call, before the transcription starts; every new call starts without it.
        self.two_people = False
        # Names given to voices while the call is still being transcribed: the file does not
        # exist yet, so they wait here and are written with it (decision 0058).
        self._voice_names: dict[str, str] = {}

    # ---------------------------------------------------------------- recording

    def start(self, name: str | None = None, language: str | None = None, mic: bool = True,
              system: bool = True) -> dict:  # fmt: skip
        with self._lock:
            if self.state in ("recording", "transcribing"):
                raise SessionError(f"already {self.state}")
            name = name or datetime.now().strftime("call-%Y%m%d-%H%M%S")
            if not _NAME.match(name) or (self.folder / f"{name}.json").exists():
                raise SessionError(f"invalid or existing name: {name!r}")
            binary = self._binary or build_helper()
            cmd = [str(binary), "--out-dir", str(self.folder), "--name", name, "--levels"]
            if not mic:
                cmd.append("--no-mic")
            if not system:
                cmd.append("--no-system")
            self.folder.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True)  # fmt: skip
            self.state, self.name, self.language = "recording", name, language
            self.source = None
            self.started, self.levels, self.message, self.step = time.time(), {}, "", (0, 0)
            self.paused_at, self.paused_total, self._ready = None, 0.0, False
            self._preview, self.two_people, self._voice_names = None, False, {}
            threading.Thread(target=self._read_levels, args=(self._proc,), daemon=True).start()
        return self.status()

    def _read_levels(self, proc: subprocess.Popen) -> None:
        tail: list[str] = []
        for line in proc.stderr:
            if line.startswith("LEVELS "):
                if self.paused_at is not None:
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    self.levels = json.loads(line[7:])
            else:
                self._ready = self._ready or "recording" in line
                tail = [*tail, line.strip()][-5:]
        returncode = proc.wait()
        # The helper ended on its own (not through stop): permission or device problem.
        if self.state == "recording" and proc is self._proc and returncode != 0:
            self.state, self.message = "error", " ".join(tail) or "the capture stopped"

    def pause(self) -> dict:
        with self._lock:
            if self.state != "recording" or self._proc is None or self.paused_at is not None:
                raise SessionError("not recording")
            if not self._ready:
                raise SessionError("still starting")
            self._proc.send_signal(signal.SIGUSR1)
            self.paused_at, self.levels = time.time(), {}
        return self.status()

    def resume(self) -> dict:
        with self._lock:
            if self.state != "recording" or self._proc is None or self.paused_at is None:
                raise SessionError("not paused")
            self._proc.send_signal(signal.SIGUSR2)
            self.paused_total += time.time() - self.paused_at
            self.paused_at = None
        return self.status()

    def set_two_people(self, on: bool) -> dict:
        with self._lock:
            if self.state != "recording":
                raise SessionError("not recording")
            self.two_people = on
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self.state != "recording" or self._proc is None:
                raise SessionError("not recording")
            proc = self._proc
            if self.paused_at is not None:
                self.paused_total += time.time() - self.paused_at
                self.paused_at = None
            proc.send_signal(signal.SIGINT)
            # stderr is read by the levels thread: read only stdout here.
            out = proc.stdout.read()
            proc.wait(timeout=60)
            self._proc = None
            if proc.returncode != 0:
                self.state, self.message = "error", f"capture exited with {proc.returncode}"
                return self.status()
            capture = json.loads(out)
            self.state, self.levels = "transcribing", {}
            rec = Recording(self.name, self.folder, capture)
            threading.Thread(target=self._transcribe, args=(rec,), daemon=True).start()
        return self.status()

    def _transcribe(self, rec: Recording) -> None:
        def progress(message: str, done: int, total: int) -> None:
            self.message, self.step = message, (done, total)

        def preview(stage: str, transcript: Transcript) -> None:
            self._preview = (stage, transcript)
            self._revision += 1  # the app fetches the text again when this changes

        try:
            backend, second = self._backends()
            json_path, txt_path = transcribe_recording(
                rec, backend, self.language, second=second, progress=progress, preview=preview,
                two_people=self.two_people)  # fmt: skip
            with self._lock:  # a name given now waits for the lock, then goes to the file
                if self._voice_names:
                    _write_voice_names(json_path, txt_path, self._voice_names)
                self.state, self.message = "done", ""
        except Exception as e:  # shown in the UI; the audio files stay on disk
            # Before the state: a status read between the two assignments must never pair
            # "error" with leftover provisional text (it flaked exactly so on a slow CI runner).
            self._preview = None
            self.state, self.message = "error", f"transcription failed: {e}"
        finally:
            self._preview = None
            # The server waits for the next call: the models have no reason to stay in memory.
            release_models()

    # ---------------------------------------------------------------- importing a file

    def import_file(self, upload: Path, original: str, language: str | None = None,
                    started_at: datetime | None = None) -> dict:  # fmt: skip
        """Transcribe an audio or video file like a recording (decision 0021).

        ``upload`` is a temporary copy: it becomes ``<name>.mixed.wav`` (16 kHz mono, all voices
        on one track) and is then removed. The name comes from the file's name.
        """
        with self._lock:
            if self.state in ("recording", "transcribing"):
                raise SessionError(f"already {self.state}")
            name = self.free_name(original)
            self.state, self.name, self.language = "transcribing", name, language
            self.source = original
            self.started, self.levels, self.message, self.step = None, {}, "conversione", (0, 0)
            self.paused_at, self.paused_total, self._preview = None, 0.0, None
            self.two_people, self._voice_names = False, {}
        threading.Thread(target=self._import, args=(upload, original, name, started_at),
                         daemon=True).start()  # fmt: skip
        return self.status()

    def free_name(self, original: str) -> str:
        """A file name like "Intervista Marco (1).m4a" as an unused recording name."""
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original).stem).strip("-._")[:60] or "file"
        name, k = stem, 2
        while any((self.folder / f"{name}{s}").exists() for s in (".json", ".mixed.wav")):
            name, k = f"{stem}-{k}", k + 1
        return name

    def _import(self, upload: Path, original: str, name: str,
                started_at: datetime | None) -> None:  # fmt: skip
        wav = self.folder / f"{name}.mixed.wav"
        try:
            extract_audio(upload, wav)
            capture = {
                "started_at": (started_at or datetime.now(UTC)).isoformat(),
                "source": original,
                "mixed": {"file": wav.name, "seconds": wav_duration(wav)},
            }
            (self.folder / f"{name}.capture.json").write_text(json.dumps(capture, indent=2))
        except Exception as e:  # not audio, or no audio track: say so, keep nothing
            wav.unlink(missing_ok=True)
            reason = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
            self.state, self.message = "error", f"import failed: {reason}"
            return
        finally:
            upload.unlink(missing_ok=True)
        self._transcribe(Recording(name, self.folder, capture))

    def abort(self) -> None:
        """Stop the helper without transcribing (server shutdown)."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    # ---------------------------------------------------------------- status

    def provisional(self, name: str) -> tuple[str, Transcript] | None:
        """The stage and transcript so far of the call being transcribed, if it is ``name``,
        with the names already given to its voices."""
        preview = self._preview  # one read: the thread may replace it meanwhile
        if preview is None or self.name != name or self.state != "transcribing":
            return None
        stage, t = preview
        if self._voice_names:
            speakers = _named(t.metadata.speakers, self._voice_names)
            metadata = t.metadata.model_copy(update={"speakers": speakers})
            t = t.model_copy(update={"metadata": metadata})
        return stage, t

    def name_voice(self, name: str, voice: str, title: str) -> bool:
        """Keep a voice's name for the call being transcribed, if it is ``name`` and its voices
        are already known; False when the name belongs in the file (decision 0058)."""
        with self._lock:
            preview = self._preview
            if preview is None or self.name != name or self.state != "transcribing":
                return False
            if not any(s.speaker == voice for s in preview[1].segments):
                raise SessionError(f"no voice {voice!r}")
            self._voice_names[voice] = title
            return True

    def status(self) -> dict:
        elapsed = 0.0
        if self.state == "recording" and self.started:
            now = self.paused_at or time.time()
            elapsed = now - self.started - self.paused_total
        preview = self._preview
        return {
            "state": self.state,
            "name": self.name,
            "paused": self.paused_at is not None,
            "source": self.source,
            "elapsed": round(elapsed, 1),
            # The levels thread may still store a line read just before the pause.
            "levels": {} if self.paused_at is not None else self.levels,
            "message": self.message,
            "step": list(self.step),
            # "partial" while Whisper works (decision 0033), "transcript" once it is done and
            # the voices are still to come, "voices" when only the doubtful words are missing
            # (decision 0058), else None.
            "preview": preview[0] if preview else None,
            "revision": self._revision,
            "two_people": self.two_people,
        }


def _named(speakers: dict[str, str], names: dict[str, str]) -> dict[str, str]:
    """``speakers`` with ``names`` applied: an empty name gives the voice its default back."""
    out = dict(speakers)
    for voice, title in names.items():
        if title:
            out[voice] = title
        else:
            out.pop(voice, None)
    return out


def _write_voice_names(json_path: Path, txt_path: Path, names: dict[str, str]) -> None:
    t = Transcript.load(json_path)
    t.metadata.speakers = _named(t.metadata.speakers, names)
    t.save(json_path)
    txt_path.write_text(to_dialogue(t), encoding="utf-8")
