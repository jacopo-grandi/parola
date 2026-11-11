"""Subtitles without recording (decision 0042): the ring buffer, the streaming helper, the API."""

import stat
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from parola.app.server import create_app
from parola.capture.session import RecordingSession
from parola.totti.listen import AudioRing, Listener, write_clip
from parola.totti.translator import Translator
from tests.conftest import requires_ffmpeg
from tests.test_session import FAKE_HELPER, Fake, wait_for
from tests.test_totti import FakeBackend

# Mimics `parola-capture --stream --levels`: a loud 16 kHz mono 16-bit square wave on stdout
# in 0.1 s chunks, levels on stderr, until SIGINT; nothing written to disk.
STREAM_HELPER = """#!{python}
import signal, sys, time
assert sys.argv[1:] == ["--stream", "--levels"], sys.argv
stop = False
def handler(*_):
    global stop
    stop = True
signal.signal(signal.SIGINT, handler)
print("parola-capture: recording", file=sys.stderr, flush=True)
chunk = (b"\\x28\\x23" + b"\\xd8\\xdc") * 800  # +-9000, 1600 samples
while not stop:
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
    print('LEVELS {{"system": -11.0}}', file=sys.stderr, flush=True)
    time.sleep(0.05)
"""


def script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def streamer(tmp_path):
    return script(tmp_path, "fake-stream", STREAM_HELPER.format(python=sys.executable))


def until(check, timeout=10.0):
    deadline = time.time() + timeout
    while not check() and time.time() < deadline:
        time.sleep(0.02)
    return check()


def pcm(*values: int) -> bytes:
    return np.array(values, dtype="<i2").tobytes()


# ---------------------------------------------------------------- the ring buffer


def test_ring_keeps_the_last_seconds_in_order():
    ring = AudioRing(seconds=1.0, rate=4)  # four samples
    ring.append(pcm(1, 2, 3))
    assert ring.tail(10).tolist() == [1, 2, 3]  # never more than was heard
    ring.append(pcm(4, 5))
    assert ring.tail(10).tolist() == [2, 3, 4, 5]
    assert ring.tail(0.5).tolist() == [4, 5]
    ring.append(pcm(6, 7, 8, 9, 10, 11))  # more than the ring holds at once
    assert ring.tail(1).tolist() == [8, 9, 10, 11]
    ring.append(b"")
    assert ring.tail(1).tolist() == [8, 9, 10, 11]


def test_ring_joins_a_sample_split_across_two_reads():
    ring = AudioRing(seconds=1.0, rate=4)
    data = pcm(300, -300, 7)
    ring.append(data[:3])
    ring.append(data[3:])
    assert ring.tail(1).tolist() == [300, -300, 7]


def test_ring_clear_forgets_everything():
    ring = AudioRing(seconds=1.0, rate=4)
    ring.append(pcm(1, 2) + b"\x01")
    ring.clear()
    assert ring.tail(1).tolist() == []
    ring.append(pcm(5))
    assert ring.tail(1).tolist() == [5]  # the stray byte is gone too


def test_write_clip_gives_seconds_and_peak(tmp_path):
    out = tmp_path / "clip.wav"
    seconds, peak = write_clip(np.array([0, 16384, -32768, 5], dtype=np.int16), out, rate=4)
    assert seconds == 1.0 and peak == 1.0  # abs(-32768) does not wrap around
    with wave.open(str(out), "rb") as f:
        shape = (f.getframerate(), f.getnchannels(), f.getsampwidth(), f.getnframes())
    assert shape == (4, 1, 2, 4)
    assert write_clip(np.zeros(0, dtype=np.int16), out, rate=4) == (0.0, 0.0)


# ---------------------------------------------------------------- the listener


def test_listener_keeps_the_stream_in_memory_only(tmp_path, streamer):
    before = set(tmp_path.rglob("*"))
    listener = Listener(binary=streamer)
    listener.start()
    assert until(lambda: listener.ring.tail(1).size >= 3200)
    assert until(lambda: listener.levels == {"system": -11.0})
    clip = tmp_path / "clip.wav"
    seconds, peak, heard = listener.tail(15, clip)
    assert heard >= seconds  # the audio clock counts everything ever appended
    assert 0.2 <= seconds <= 15 and abs(peak - 9000 / 32768) < 1e-6
    clip.unlink()
    listener.stop()
    assert not listener.active and listener.ring.tail(15).size == 0 and listener.levels == {}
    assert set(tmp_path.rglob("*")) == before  # nothing written beside the test's own clip


def test_listener_reports_a_helper_that_fails(tmp_path):
    failing = script(tmp_path, "fail", f"#!{sys.executable}\nimport sys\n"
                     "print('no permission', file=sys.stderr)\nsys.exit(1)\n")  # fmt: skip
    listener = Listener(binary=failing)
    listener.start()
    assert until(lambda: "no permission" in listener.error)
    assert not listener.active


# ---------------------------------------------------------------- the API


@pytest.fixture
def translator_on_disk(monkeypatch):
    monkeypatch.setattr(Translator, "available", lambda self: True)
    monkeypatch.setattr(Translator, "translate", lambda self, texts: [f"IT:{t}" for t in texts])


@pytest.fixture
def app(tmp_path, streamer, translator_on_disk):
    helper = script(tmp_path, "fake-capture", FAKE_HELPER.format(python=sys.executable))
    folder = tmp_path / "calls"
    session = RecordingSession(folder, binary=helper,
                               backends=lambda: (Fake("ciao"), Fake("ciao")))  # fmt: skip
    listener = Listener(binary=streamer)
    client = TestClient(create_app(folder, session=session, listener=listener,
                                   live_backend=lambda: FakeBackend(["Hello there."])))  # fmt: skip
    yield client, session, listener, folder
    listener.stop()
    session.abort()


def test_listen_start_shows_subtitles_and_stop_ends_them(app):
    client, _, listener, folder = app
    st = client.post("/api/listen/start").json()
    assert st["state"] == "idle" and st["listening"] and st["subtitles"]
    assert until(lambda: client.get("/api/subtitles").json()["lines"])
    assert client.get("/api/subtitles").json()["lines"] == [
        {"en": "Hello there.", "it": "IT:Hello there."}
    ]
    assert until(lambda: client.get("/api/record/status").json()["levels"] == {"system": -11.0})
    # Listening is not a recording: nothing in the calls folder, no call in the list.
    assert not folder.exists() or not any(folder.iterdir())
    assert client.get("/api/recordings").json() == []
    st = client.post("/api/listen/stop").json()
    assert not st["listening"] and not st["subtitles"] and st["state"] == "idle"
    assert listener.ring.tail(15).size == 0


def test_listening_and_importing_exclude_each_other(app):
    client, *_ = app
    client.post("/api/listen/start")
    r = client.post("/api/import?filename=a.wav", content=b"RIFF")
    assert r.status_code == 409 and "listening" in r.json()["detail"]


@requires_ffmpeg
def test_the_dot_turns_listening_into_a_recording_from_now(app):
    client, session, listener, _ = app
    client.post("/api/listen/start")
    assert until(lambda: client.get("/api/subtitles").json()["lines"])
    before = client.get("/api/subtitles").json()["revision"]
    st = client.post("/api/record/start", json={"name": "c1"}).json()
    # A recording now, the listening is over, the subtitles stay on (on the new system wav).
    assert st["state"] == "recording" and not st["listening"] and st["subtitles"]
    assert not listener.active
    assert client.get("/api/subtitles").json()["revision"] > before  # the clients redraw
    assert client.post("/api/listen/start").status_code == 409  # not while recording
    time.sleep(0.3)  # the fake helper installs its SIGINT handler
    st = client.post("/api/record/stop").json()
    assert st["state"] == "transcribing" and not st["subtitles"]
    assert wait_for(session, "done")["state"] == "done"
    assert not client.get("/api/record/status").json()["listening"]


@requires_ffmpeg
def test_a_plain_recording_starts_with_subtitles_off(app):
    client, session, *_ = app
    st = client.post("/api/record/start", json={"name": "c2"}).json()
    assert st["state"] == "recording" and not st["subtitles"]
    assert client.post("/api/subtitles/start").json()["active"]
    assert client.get("/api/record/status").json()["subtitles"]
    client.post("/api/subtitles/stop")
    assert not client.get("/api/record/status").json()["subtitles"]
    time.sleep(0.3)  # the fake helper installs its SIGINT handler
    client.post("/api/record/stop")
    wait_for(session, "done")


def test_subtitles_language_toggle(app):
    client, *_ = app
    assert client.post("/api/subtitles/lang/fr").status_code == 404
    assert client.post("/api/subtitles/lang/en").json()["lang"] == "en"
    client.post("/api/listen/start")
    assert until(lambda: client.get("/api/subtitles").json()["lines"])
    st = client.get("/api/subtitles").json()
    # The EN view: no translation, and the still-provisional tail is part of the status.
    assert st["lang"] == "en" and st["lines"] == [{"en": "Hello there.", "it": ""}]
    assert "tail" in st
    # Back to Italian: the next tick translates what is already committed.
    assert client.post("/api/subtitles/lang/it").json()["lang"] == "it"
    assert until(lambda: client.get("/api/subtitles").json()["lines"][0]["it"])
    # The fake hears the phrase in every clip of the growing stream, so lines may repeat;
    # what matters is that the committed English got its Italian after the switch.
    assert client.get("/api/subtitles").json()["lines"][0] == {
        "en": "Hello there.",
        "it": "IT:Hello there.",
    }
