"""Recording from the app, with a fake capture helper and fake ASR backends."""

import json
import stat
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parola.app.server import create_app
from parola.asr import ASRResult
from parola.capture.session import RecordingSession, SessionError
from parola.schema import Segment, Transcript, Word
from tests.conftest import requires_ffmpeg

FAKE_HELPER = '''#!{python}
"""Mimics parola-capture: levels on stderr until SIGINT, then two tone files and the summary."""
import json, signal, subprocess, sys, time
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
out, name = args["--out-dir"], args["--name"]
stop = paused = False
def handler(*_):
    global stop
    stop = True
def pause(sig, _):
    global paused
    paused = sig == signal.SIGUSR1
signal.signal(signal.SIGINT, handler)
signal.signal(signal.SIGUSR1, pause)
signal.signal(signal.SIGUSR2, pause)
print("parola-capture: recording", file=sys.stderr, flush=True)
while not stop:
    if not paused:
        print('LEVELS {{"mic": -20.0, "system": -12.5}}', file=sys.stderr, flush=True)
    time.sleep(0.05)
for c in ("mic", "system"):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", f"{{out}}/{{name}}.{{c}}.wav"], check=True)
print(json.dumps({{"mic": {{"seconds": 1.0}}, "system": {{"seconds": 1.0}}}}))
'''


class Fake:
    name, model = "fake", "m"

    def __init__(self, text):
        self.text = text

    def transcribe(self, wav_path, language=None):
        words = [
            Word(text=t, start=0.1 * k, end=0.1 * k + 0.08) for k, t in enumerate(self.text.split())
        ]
        return ASRResult(segments=[Segment(start=0, end=1, text=self.text, lang="it", words=words)],
                         language="it")  # fmt: skip


@pytest.fixture
def helper(tmp_path):
    path = tmp_path / "fake-capture"
    path.write_text(FAKE_HELPER.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def wait_for(session, state, timeout=20):
    deadline = time.time() + timeout
    while session.status()["state"] != state and time.time() < deadline:
        time.sleep(0.05)
    return session.status()


@requires_ffmpeg
def test_session_records_and_transcribes(tmp_path, helper):
    folder = tmp_path / "calls"
    backends = lambda: (Fake("ciao a tutti"), Fake("ciao a voi"))  # noqa: E731
    session = RecordingSession(folder, binary=helper, backends=backends)
    st = session.start(name="riunione")
    assert st["state"] == "recording" and st["name"] == "riunione"
    time.sleep(0.4)
    assert session.status()["levels"] == {"mic": -20.0, "system": -12.5}
    with pytest.raises(SessionError):
        session.start()
    with pytest.raises(SessionError):
        session.resume()
    st = session.pause()
    assert st["paused"] and st["levels"] == {}
    time.sleep(0.3)
    assert session.status()["elapsed"] == st["elapsed"]  # the clock stops while paused
    with pytest.raises(SessionError):
        session.pause()
    assert not session.resume()["paused"]
    assert session.stop()["state"] == "transcribing"
    st = wait_for(session, "done")
    assert st["state"] == "done", st
    t = Transcript.load(folder / "riunione.json")
    words = [w for s in t.segments for w in s.words]
    # Both fake channels say the same thing at the same time: the mic one is dropped as echo.
    assert {s.channel for s in t.segments} == {"system"}
    assert any(w.doubtful for w in words)  # "tutti" is not confirmed by the second opinion
    with pytest.raises(SessionError):
        session.stop()


def test_bad_names_are_refused(tmp_path, helper):
    (tmp_path / "taken.json").write_text("{}")
    session = RecordingSession(tmp_path, binary=helper, backends=lambda: (None, None))
    for name in ("../x", "taken"):
        with pytest.raises(SessionError):
            session.start(name=name)


@requires_ffmpeg
def test_api_start_status_stop(tmp_path, helper):
    backends = lambda: (Fake("buongiorno"), Fake("buongiorno"))  # noqa: E731
    session = RecordingSession(tmp_path, binary=helper, backends=backends)
    client = TestClient(create_app(tmp_path, session=session))
    assert client.get("/api/record/status").json()["state"] == "idle"
    assert (
        client.post("/api/record/start", json={"name": "c1", "lang": "it"}).json()["state"]
        == "recording"
    )
    assert client.post("/api/record/start", json={}).status_code == 409
    time.sleep(0.3)
    assert client.post("/api/record/pause").json()["paused"]
    assert client.post("/api/record/pause").status_code == 409
    assert not client.post("/api/record/resume").json()["paused"]
    assert client.post("/api/record/rewind").status_code == 404
    assert client.post("/api/record/stop").json()["state"] == "transcribing"
    assert wait_for(session, "done")["state"] == "done"
    assert [r["name"] for r in client.get("/api/recordings").json()] == ["c1"]
    assert client.post("/api/record/stop").status_code == 409


def test_helper_failure_is_reported(tmp_path):
    failing = tmp_path / "fail"
    failing.write_text(
        f"#!{sys.executable}\nimport sys\nprint('no permission', file=sys.stderr)\nsys.exit(1)\n"
    )
    failing.chmod(failing.stat().st_mode | stat.S_IEXEC)
    session = RecordingSession(tmp_path, binary=Path(failing), backends=lambda: (None, None))
    session.start(name="x")
    st = wait_for(session, "error", timeout=5)
    assert st["state"] == "error" and "no permission" in st["message"]


def tone(path: Path, seconds: int = 1) -> Path:
    import subprocess

    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", str(path)], check=True)  # fmt: skip
    return path


@requires_ffmpeg
def test_import_a_file_like_a_recording(tmp_path, helper):
    folder = tmp_path / "calls"
    folder.mkdir()
    backends = lambda: (Fake("ciao a tutti"), Fake("ciao a voi"))  # noqa: E731
    session = RecordingSession(folder, binary=helper, backends=backends)
    upload = tone(tmp_path / "upload.m4a")
    started = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    st = session.import_file(upload, "Intervista Marco (1).m4a", "it", started)
    assert st["state"] == "transcribing" and st["name"] == "Intervista-Marco-1"
    with pytest.raises(SessionError):
        session.start()
    assert wait_for(session, "done")["state"] == "done"
    t = Transcript.load(folder / "Intervista-Marco-1.json")
    assert {s.channel for s in t.segments} == {"mixed"}
    assert any(w.doubtful for s in t.segments for w in s.words)
    capture = json.loads((folder / "Intervista-Marco-1.capture.json").read_text())
    assert capture["source"] == "Intervista Marco (1).m4a"
    assert capture["started_at"].startswith("2026-10-01T09:30")
    assert (folder / "Intervista-Marco-1.mixed.flac").exists() and not upload.exists()
    assert not (folder / "Intervista-Marco-1.mixed.wav").exists()  # compressed (decision 0024)
    assert "] ciao a (tutti?)" in (folder / "Intervista-Marco-1.txt").read_text()  # no side label
    assert session.free_name("Intervista Marco (1).m4a") == "Intervista-Marco-1-2"


@requires_ffmpeg
def test_a_file_without_audio_is_reported(tmp_path, helper):
    session = RecordingSession(tmp_path, binary=helper, backends=lambda: (None, None))
    bad = tmp_path / "notes.m4a"
    bad.write_text("not audio")
    session.import_file(bad, "notes.m4a")
    st = wait_for(session, "error", timeout=10)
    assert st["state"] == "error" and st["message"].startswith("import failed")
    assert not list(tmp_path.glob("*.mixed.wav")) and not bad.exists()


@requires_ffmpeg
def test_api_import(tmp_path, helper):
    backends = lambda: (Fake("buongiorno a tutti"), Fake("buongiorno a tutti"))  # noqa: E731
    session = RecordingSession(tmp_path, binary=helper, backends=backends)
    client = TestClient(create_app(tmp_path, session=session))
    assert client.post("/api/import?filename=notes.txt", content=b"x").status_code == 415
    audio = tone(tmp_path / "src.mp3").read_bytes()
    r = client.post("/api/import?filename=riunione.mp3&modified=1790000000000&lang=it",
                    content=audio)  # fmt: skip
    assert r.json()["state"] == "transcribing" and r.json()["name"] == "riunione"
    assert client.post("/api/import?filename=altra.mp3", content=audio).status_code == 409
    assert wait_for(session, "done")["state"] == "done"
    rec = client.get("/api/recordings/riunione").json()
    assert rec["summary"]["source"] == "riunione.mp3"
    assert list(rec["audio"]) == ["mixed"]
    assert client.get("/audio/riunione/mixed").status_code == 200
    assert not list((tmp_path / ".uploads").iterdir())
    md = client.get("/export/riunione.md").text
    assert "**" not in md.split("\n", 2)[2]  # no side label on a single-track file


@requires_ffmpeg
def test_the_suggested_title_is_kept_apart(tmp_path, helper, capsys):
    from parola.cli import main

    long_text = " ".join(["il mulching naturale."] * 3 + ["i semi antichi."] * 2
                         + [f"k{a}{b}" for a in "bcdfgjlm" for b in "npqrstvz"])  # fmt: skip
    backends = lambda: (Fake(long_text), Fake(long_text))  # noqa: E731
    session = RecordingSession(tmp_path, binary=helper, backends=backends)
    session.import_file(tone(tmp_path / "a.m4a"), "a.m4a")
    assert wait_for(session, "done")["state"] == "done"
    meta = Transcript.load(tmp_path / "a.json").metadata
    assert meta.title_suggested == meta.title == "Mulching naturale e semi antichi"
    assert main(["titles", "--dir", str(tmp_path)]) == 0
    assert "1 of 1 suggested titles kept" in capsys.readouterr().out


class Reporting(Fake):
    """A fake backend that reports its progress in four steps, like Whisper's 30 s windows."""

    def __init__(self, text):
        super().__init__(text)
        self.on_progress = None

    def transcribe(self, wav_path, language=None):
        for share in (0.25, 0.5, 0.75, 1.0):
            if self.on_progress:
                self.on_progress(share)
        return super().transcribe(wav_path, language)


@requires_ffmpeg
def test_progress_moves_during_each_step(tmp_path, monkeypatch):
    from parola.capture.recorder import WEIGHTS, Recording, transcribe_recording

    monkeypatch.setattr("parola.capture.recorder._speaker_models", lambda: True)

    def diarize(path, progress=None):
        progress(1.0)
        return []

    monkeypatch.setattr("parola.diarize.diarize", diarize)
    tone(tmp_path / "c.mixed.wav")
    rec = Recording("c", tmp_path, {"mixed": {"seconds": 1.0}})
    seen = []
    first, second = Reporting("ciao a tutti"), Reporting("ciao a voi")

    def progress(msg, done, total):
        seen.append((msg, done / total))

    transcribe_recording(rec, first, "it", second=second, progress=progress)
    shares = [round(s, 2) for _, s in seen]
    assert shares == sorted(shares) and shares[0] == 0 and shares[-1] == 1.0
    # The steps weigh what they took on a 1 h file: voices as long as Whisper, Parakeet less
    # than half, so the bar does not crawl at the end (decision 0031).
    assert WEIGHTS["speakers"] >= WEIGHTS["primary"] > 2 * WEIGHTS["second"]
    total = sum(WEIGHTS.values())
    reached = [(m, round(s, 2)) for m, s in seen]
    assert ("trascrizione", round(WEIGHTS["primary"] / 2 / total, 2)) in reached
    # The voices right after Whisper, the second opinion last (decision 0058).
    assert ("chi parla", round(WEIGHTS["primary"] / total, 2)) in reached
    after_voices = round((WEIGHTS["primary"] + WEIGHTS["speakers"]) / total, 2)
    assert ("parole dubbie", after_voices) in reached
    assert first.on_progress is None and second.on_progress is None  # handed back


@requires_ffmpeg
def test_status_names_the_imported_file(tmp_path, helper):
    session = RecordingSession(tmp_path, binary=helper,
                               backends=lambda: (Fake("ciao"), Fake("ciao")))  # fmt: skip
    st = session.import_file(tone(tmp_path / "u.m4a"), "Vacanze, intervista.m4a")
    assert st["source"] == "Vacanze, intervista.m4a"
    wait_for(session, "done")


@requires_ffmpeg
def test_voices_are_told_apart_on_an_imported_file(tmp_path, monkeypatch):
    from parola.capture.recorder import Recording, transcribe_recording
    from parola.schema import Transcript

    monkeypatch.setattr("parola.capture.recorder._speaker_models", lambda: True)
    # Fake turns: the first voice until 0.15 s, then a second one.
    monkeypatch.setattr("parola.diarize.diarize",
                        lambda path, progress=None: [(0.0, 0.15, 0), (0.15, 1.0, 1)])  # fmt: skip
    tone(tmp_path / "c.mixed.wav")
    rec = Recording("c", tmp_path, {"mixed": {"seconds": 1.0}})
    seen = []
    transcribe_recording(rec, Fake("ciao a tutti"), "it", second=Fake("ciao a tutti"),
                         progress=lambda m, d, t: seen.append(m))  # fmt: skip
    t = Transcript.load(tmp_path / "c.json")
    assert [(s.text, s.speaker) for s in t.segments] == [
        ("ciao a", "mixed:1"),
        ("tutti", "mixed:2"),
    ]
    assert "chi parla" in seen
    assert "] Voce 1: ciao a" in (tmp_path / "c.txt").read_text()


@requires_ffmpeg
def test_each_engine_step_releases_the_models(tmp_path, monkeypatch):
    from parola.capture import recorder
    from parola.capture.recorder import Recording, transcribe_recording

    released = []
    monkeypatch.setattr(recorder, "release_models", lambda: released.append(1))
    tone(tmp_path / "c.mixed.wav")
    rec = Recording("c", tmp_path, {"mixed": {"seconds": 1.0}})
    transcribe_recording(rec, Fake("ciao a tutti"), "it", second=Fake("ciao a voi"))
    assert len(released) == 2  # after Whisper and after Parakeet: one engine in memory at a time


class Waiting(Fake):
    """A fake second opinion that waits until the test lets it go."""

    def __init__(self, text):
        super().__init__(text)
        self.go = threading.Event()

    def transcribe(self, wav_path, language=None):
        assert self.go.wait(10)
        return super().transcribe(wav_path, language)


def wait_preview(session, stage, timeout=10):
    deadline = time.time() + timeout
    while session.status()["preview"] != stage and time.time() < deadline:
        time.sleep(0.02)
    return session.status()


@requires_ffmpeg
def test_the_text_shows_while_the_rest_runs_and_cannot_be_edited(tmp_path, helper, monkeypatch):
    """Decisions 0031, 0058: Whisper's text, read-only but exportable, then the voices, which
    can be named at once, then the doubtful words and the final file."""
    monkeypatch.setattr("parola.capture.recorder._speaker_models", lambda: True)
    voices_go = threading.Event()

    def diarize(path, progress=None):
        assert voices_go.wait(10)
        return [(0.0, 0.15, 0), (0.15, 1.0, 1)]

    monkeypatch.setattr("parola.diarize.diarize", diarize)
    second = Waiting("ciao a voi")
    session = RecordingSession(tmp_path, binary=helper,
                               backends=lambda: (Fake("ciao a tutti"), second))  # fmt: skip
    client = TestClient(create_app(tmp_path, session=session))
    session.import_file(tone(tmp_path / "u.m4a"), "lezione.m4a", "it")

    # Whisper is done, the voices are waiting: the text is there, without doubts or voices.
    st = wait_preview(session, "transcript")
    assert st["state"] == "transcribing" and st["preview"] == "transcript"
    rec = client.get("/api/recordings/lezione").json()
    assert rec["provisional"] == "transcript"
    assert " ".join(s["text"] for s in rec["segments"]) == "ciao a tutti"
    assert not any(w["doubtful"] for s in rec["segments"] for w in s["words"])
    assert rec["voices"] == {} and rec["audio"] == {"mixed": "/audio/lezione/mixed"}
    assert rec["summary"]["title"] == "lezione" and rec["summary"]["source"] == "lezione.m4a"
    assert client.get("/audio/lezione/mixed").status_code == 200  # it can be listened to
    # It can already go out: the words are final.
    assert client.get("/export/lezione.txt").text.strip().endswith("] ciao a tutti")
    # Nothing can change it: the final file replaces it.
    span = {"segment": 0, "start": 0, "end": 1, "text": "salve"}
    assert client.post("/api/recordings/lezione/correct", json=span).status_code == 404
    assert client.post("/api/recordings/lezione/rename", json={"title": "x"}).status_code == 404
    assert client.delete("/api/recordings/lezione").status_code == 404
    assert client.get("/api/recordings").json() == []  # listed once it is final
    assert not (tmp_path / "lezione.json").exists()

    # The voices are done, Parakeet is waiting: the voices are there, not the doubts yet.
    voices_go.set()
    assert wait_preview(session, "voices")["preview"] == "voices"
    rec = client.get("/api/recordings/lezione").json()
    assert rec["provisional"] == "voices" and set(rec["voices"]) == {"mixed:1", "mixed:2"}
    assert not any(w["doubtful"] for s in rec["segments"] for w in s["words"])
    assert client.post("/api/recordings/lezione/correct", json=span).status_code == 404
    # A voice can be named now: the name waits for the file.
    marta = {"id": "mixed:2", "name": "Marta"}
    rec = client.post("/api/recordings/lezione/voices", json=marta).json()
    assert rec["voices"]["mixed:2"] == "Marta" and rec["provisional"] == "voices"
    assert "Marta: tutti" in client.get("/export/lezione.txt").text
    assert client.post("/api/recordings/lezione/voices",
                       json={"id": "mixed:9", "name": "X"}).status_code == 400  # fmt: skip

    # The final transcript: written, editable, with the doubts and the name given meanwhile.
    second.go.set()
    st = wait_for(session, "done")
    assert st["state"] == "done" and st["preview"] is None
    rec = client.get("/api/recordings/lezione").json()
    assert rec["provisional"] is None and rec["voices"]["mixed:2"] == "Marta"
    words = [(w["text"], w["doubtful"]) for s in rec["segments"] for w in s["words"]]
    assert words == [("ciao", False), ("a", False), ("tutti", True)]
    assert "Marta: (tutti?)" in (tmp_path / "lezione.txt").read_text()
    assert client.post("/api/recordings/lezione/correct", json=span).status_code == 200


@requires_ffmpeg
def test_a_failure_after_whisper_leaves_no_provisional_text(tmp_path, helper, monkeypatch):
    monkeypatch.setattr("parola.capture.recorder._speaker_models", lambda: False)

    class Broken(Fake):
        def transcribe(self, wav_path, language=None):
            raise RuntimeError("out of memory")

    session = RecordingSession(tmp_path, binary=helper,
                               backends=lambda: (Fake("ciao"), Broken("ciao")))  # fmt: skip
    client = TestClient(create_app(tmp_path, session=session))
    session.import_file(tone(tmp_path / "u.m4a"), "lezione.m4a", "it")
    st = wait_for(session, "error")
    assert st["state"] == "error" and st["preview"] is None
    assert client.get("/api/recordings/lezione").status_code == 404


@requires_ffmpeg
def test_two_people_is_chosen_per_call(tmp_path, helper, monkeypatch):
    """Decision 0058: chosen while recording, it skips the voices of that call only."""
    monkeypatch.setattr("parola.capture.recorder._speaker_models", lambda: True)
    monkeypatch.setattr("parola.diarize.diarize", lambda *a, **k: pytest.fail("diarized"))
    backends = lambda: (Fake("tutto bene"), Fake("tutto bene"))  # noqa: E731
    session = RecordingSession(tmp_path, binary=helper, backends=backends)
    client = TestClient(create_app(tmp_path, session=session))
    with pytest.raises(SessionError):
        session.set_two_people(True)  # nothing being recorded
    assert client.post("/api/record/start", json={"name": "uno"}).json()["two_people"] is False
    time.sleep(0.3)
    assert client.post("/api/record/two").json()["two_people"] is True
    assert client.post("/api/record/many").json()["two_people"] is False
    assert client.post("/api/record/two").json()["two_people"] is True
    session.stop()
    assert wait_for(session, "done")["state"] == "done"
    t = Transcript.load(tmp_path / "uno.json")
    assert {s.speaker for s in t.segments if s.channel == "system"} == {"system:1"}
    assert client.post("/api/record/two").status_code == 409  # done: too late for this call
    # The next call starts without it.
    assert session.start(name="due")["two_people"] is False
    session.abort()
