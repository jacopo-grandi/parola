import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from parola.app.server import create_app
from parola.schema import Metadata, Segment, Transcript, Word
from tests.conftest import requires_ffmpeg


@pytest.fixture
def folder(tmp_path):
    t = Transcript(
        metadata=Metadata(audio_file="call.*.wav", duration=12.0, backend="mlx-whisper",
                          model="turbo", created_at=datetime(2026, 10, 16, tzinfo=UTC),
                          parola_version="0"),
        segments=[
            Segment(start=0, end=2, text="ciao a tutti", lang="it", channel="mic", words=[
                Word(text="ciao", start=0.1, end=0.4), Word(text="a", start=0.5, end=0.6),
                Word(text="tutti", start=0.7, end=1.2, doubtful=True)]),
            Segment(start=3, end=4, text="buongiorno", lang="it", channel="system",
                    words=[Word(text="buongiorno", start=3.1, end=3.8)]),
        ],
    )  # fmt: skip
    t.save(tmp_path / "call.json")
    (tmp_path / "call.mic.wav").write_bytes(b"RIFF" + b"\0" * 1000)
    (tmp_path / "call.system.wav").write_bytes(b"RIFF" + b"\0" * 1000)
    (tmp_path / "call.capture.json").write_text(json.dumps({"started_at": "2026-10-16T09:00:00Z"}))
    # Not a recording: a transcript without audio files.
    t.save(tmp_path / "other.json")
    return tmp_path


@pytest.fixture
def client(folder):
    return TestClient(create_app(folder))


def test_index_and_static(client):
    assert "P.A.R.O.L.A." in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_list_recordings(client):
    [rec] = client.get("/api/recordings").json()
    assert rec["name"] == "call" and rec["doubtful"] == 1 and rec["words"] == 4
    assert rec["to_review"] == 1
    assert rec["channels"] == ["mic", "system"] and rec["started_at"] == "2026-10-16T09:00:00Z"


def test_a_corrupt_transcript_does_not_hide_the_other_calls(client, folder):
    # Out-of-order segments (as a 2026-10-23 pipeline bug wrote them): the file fails
    # validation, but the call must stay listed and the others must stay visible.
    broken = json.loads((folder / "call.json").read_text())
    broken["segments"].reverse()
    (folder / "rotta.json").write_text(json.dumps(broken))
    (folder / "rotta.mic.wav").write_bytes(b"RIFF" + b"\0" * 1000)
    rows = client.get("/api/recordings").json()
    assert [r["name"] for r in rows] == ["rotta", "call"]  # newest (file mtime) first
    rotta = rows[0]
    assert "sorted" in rotta["error"] and rotta["segments"] == 0
    # The raw json still opens: segments readable, audio playable, summary degraded.
    data = client.get("/api/recordings/rotta").json()
    assert len(data["segments"]) == 2 and data["summary"]["error"]
    # Search skips it instead of failing.
    hits = client.get("/api/search", params={"q": "buongiorno"}).json()
    assert [r["name"] for r in hits] == ["call"]


def test_output_devices_are_listed_and_chosen(client, monkeypatch):
    from parola.capture import devices
    from parola.capture.recorder import CaptureError

    rows = [{"uid": "spk", "name": "Altoparlanti", "kind": "Built-in, 48000 Hz", "current": True},
            {"uid": "cuffie", "name": "Cuffie", "kind": "Built-in, 48000 Hz",
             "current": False}]  # fmt: skip
    chosen = []

    def set_output(uid):
        if uid not in {r["uid"] for r in rows}:
            raise CaptureError(f"no output device with UID {uid}")
        chosen.append(uid)
        for r in rows:
            r["current"] = r["uid"] == uid

    monkeypatch.setattr(devices, "outputs", lambda: rows)
    monkeypatch.setattr(devices, "set_output", set_output)
    assert client.get("/api/outputs").json() == rows
    out = client.post("/api/outputs", json={"uid": "cuffie"}).json()
    assert chosen == ["cuffie"] and [r["uid"] for r in out if r["current"]] == ["cuffie"]
    assert client.post("/api/outputs", json={"uid": "boh"}).status_code == 400

    def unavailable():
        raise CaptureError("swiftc not found")

    monkeypatch.setattr(devices, "outputs", unavailable)
    assert client.get("/api/outputs").status_code == 503


def test_channel_warnings_reach_the_summary(client, folder):
    cap = json.loads((folder / "call.capture.json").read_text())
    cap["warnings"] = [{"channel": "mic", "kind": "no_speech", "share": 0.009, "device": "X"}]
    (folder / "call.capture.json").write_text(json.dumps(cap))
    [row] = [r for r in client.get("/api/recordings").json() if r["name"] == "call"]
    assert row["warnings"][0]["channel"] == "mic"
    data = client.get("/api/recordings/call").json()
    assert data["summary"]["warnings"][0]["device"] == "X"


def test_get_recording(client):
    data = client.get("/api/recordings/call").json()
    assert data["audio"] == {"mic": "/audio/call/mic", "system": "/audio/call/system"}
    assert data["loudness"] == {}  # the fake wavs cannot be measured: played as recorded
    assert data["segments"][0]["words"][2]["doubtful"] is True


def test_audio_supports_ranges(client):
    r = client.get("/audio/call/mic", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"RIFF"


def test_exports(client):
    txt = client.get("/export/call.txt")
    assert "Io: ciao a (tutti?)" in txt.text
    assert 'filename="call.txt"' in txt.headers["content-disposition"]
    assert "**Altri**" in client.get("/export/call.md").text
    assert client.get("/export/call.json").json()["metadata"]["model"] == "turbo"


@pytest.mark.parametrize(
    "url",
    [
        "/api/recordings/..%2Fsecret",
        "/api/recordings/missing",
        "/audio/call/other",
        "/audio/..%2Fx/mic",
        "/export/call.pdf",
    ],
)
def test_bad_paths_are_404(client, url):
    assert client.get(url).status_code == 404


def test_correct_confirm_undo_endpoints(client, folder):
    r = client.post("/api/recordings/call/correct",
                    json={"segment": 0, "start": 2, "end": 3, "text": "tutto"})  # fmt: skip
    assert r.status_code == 200
    data = r.json()
    assert data["segments"][0]["text"] == "ciao a tutto"
    assert data["summary"]["doubtful"] == 0 and data["summary"]["edits"] == 1
    assert (folder / "call.asr.json").exists()
    assert "Io: ciao a tutto" in (folder / "call.txt").read_text()
    r = client.post("/api/recordings/call/confirm", json={"segment": 1, "start": 0, "end": 1})
    assert r.json()["summary"]["checked"] == 2
    assert client.post("/api/recordings/call/undo").json()["summary"]["edits"] == 1
    assert client.post("/api/recordings/call/undo").json()["segments"][0]["text"] == "ciao a tutti"
    assert client.post("/api/recordings/call/undo").status_code == 400
    assert (
        client.post(
            "/api/recordings/call/correct", json={"segment": 5, "start": 0, "end": 1, "text": "x"}
        ).status_code
        == 400
    )
    # The kept model output is not listed as a recording.
    assert [r["name"] for r in client.get("/api/recordings").json()] == ["call"]


def test_docx_export(client):
    r = client.get("/export/call.docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    from io import BytesIO

    from docx import Document

    doc = Document(BytesIO(r.content))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "ciao a tutti" in text and "buongiorno" in text


def test_rename(client):
    r = client.post("/api/recordings/call/rename", json={"title": "  Riunione   LED Q4 "})
    assert r.json()["summary"]["title"] == "Riunione LED Q4"
    assert client.get("/api/recordings").json()[0]["title"] == "Riunione LED Q4"
    assert (
        client.post("/api/recordings/call/rename", json={"title": ""}).json()["summary"]["title"]
        == "call"
    )


def test_exports_are_named_after_the_title(client):
    client.post("/api/recordings/call/rename", json={"title": "Riunione: LED/Q4"})
    r = client.get("/export/call.md")
    disposition = r.headers["content-disposition"]
    assert 'filename="call.md"' in disposition
    assert "filename*=UTF-8''Riunione%20LEDQ4.md" in disposition
    assert r.text.startswith("# Riunione: LED/Q4")


def test_delete_moves_the_call_to_the_trash_and_restore_brings_it_back(client, folder):
    (folder / "call.txt").write_text("x")
    (folder / "call.2.json").write_text("{}")  # another recording's file: must not move
    r = client.delete("/api/recordings/call")
    entry = r.json()["trashed"]
    assert client.get("/api/recordings").json() == []
    moved = sorted(p.name for p in (folder / ".trash" / entry).iterdir())
    assert moved == [
        "call.capture.json",
        "call.json",
        "call.mic.wav",
        "call.system.wav",
        "call.txt",
    ]
    assert (folder / "call.2.json").exists() and (folder / "other.json").exists()
    assert client.delete("/api/recordings/call").status_code == 404
    assert client.post(f"/api/trash/{entry}/restore").json()["name"] == "call"
    assert [r["name"] for r in client.get("/api/recordings").json()] == ["call"]
    assert not (folder / ".trash" / entry).exists()
    assert client.post(f"/api/trash/{entry}/restore").status_code == 404
    assert client.post("/api/trash/..--x/restore").status_code == 404


def test_title_suggestion_saves_nothing(client, folder):
    before = (folder / "call.json").read_text()
    r = client.get("/api/recordings/call/title-suggestion").json()
    assert r["title"] is None  # four words: too short for a title
    assert r["phrases"] == []  # "ciao", "buongiorno": greetings, not topics
    assert (folder / "call.json").read_text() == before


def test_search_across_calls(client):
    r = client.get("/api/search", params={"q": "TUTTI"}).json()
    assert [c["name"] for c in r] == ["call"] and r[0]["count"] == 1
    hit = r[0]["hits"][0]
    assert (hit["segment"], hit["word"], hit["match"], hit["before"]) == (0, 2, "tutti", "ciao a")
    assert client.get("/api/search", params={"q": "assente"}).json() == []
    assert client.get("/api/search", params={"q": " "}).json() == []
    client.post("/api/recordings/call/rename", json={"title": "Cena di sabato"})
    r = client.get("/api/search", params={"q": "sabato"}).json()
    assert r[0]["count"] == 0 and r[0]["title"] == "Cena di sabato"  # found by its title


@requires_ffmpeg
def test_a_compressed_call_is_listed_and_played_as_wav(client, folder):
    import subprocess

    for c in ("mic", "system"):
        (folder / f"call.{c}.wav").unlink()
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=duration=1:sample_rate=16000", "-c:a", "flac",
                        str(folder / f"call.{c}.flac")], check=True)  # fmt: skip
    assert [r["name"] for r in client.get("/api/recordings").json()] == ["call"]
    data = client.get("/api/recordings/call").json()
    assert set(data["audio"]) == {"mic", "system"}
    # The channels' loudness rides along, for the player's per-channel gain.
    assert set(data["loudness"]) == {"mic", "system"}
    assert all(-70 < v < 0 for v in data["loudness"].values())
    r = client.get("/audio/call/mic", headers={"Range": "bytes=0-3"})
    # The player gets WAV, which WebKit can seek in, and can ask for parts of it.
    assert r.status_code == 206 and r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFF"
    assert client.get("/audio/call/nope").status_code == 404
    assert len(list((folder / ".playback").glob("call.mic.*.wav"))) == 1
    client.delete("/api/recordings/call")
    assert list((folder / ".playback").glob("call.*.wav")) == []  # deleted with the call


def test_suggestions_come_only_from_your_own_corrections(client):
    assert client.get("/api/suggestions", params={"text": "tutti"}).json() == []  # starts empty
    client.post("/api/recordings/call/correct", json={"segment": 0, "start": 2, "end": 3,
                                                      "text": "Orto-Lab"})  # fmt: skip
    assert client.get("/api/suggestions", params={"text": "tutti"}).json() == []  # once: not yet
    client.post("/api/recordings/call/correct", json={"segment": 0, "start": 0, "end": 1,
                                                      "text": "Orto-Lab"})  # fmt: skip
    (s,) = client.get("/api/suggestions", params={"text": "tutti"}).json()
    assert (s["term"], s["count"], s["exact"]) == ("Orto-Lab", 2, True)
    rec = client.get("/api/recordings/call").json()
    assert rec["segments"][0]["text"] == "Orto-Lab a Orto-Lab"  # nothing changed by itself


def test_voices_can_be_renamed_and_turns_moved(client, folder):
    from parola.schema import Transcript

    t = Transcript.load(folder / "call.json")
    t.segments[1].speaker = "system:1"
    t.save(folder / "call.json")
    rec = client.get("/api/recordings/call").json()
    assert rec["voices"] == {"system:1": "Altri"}  # the only voice of its side (decision 0058)
    rec = client.post(
        "/api/recordings/call/voices", json={"id": "system:1", "name": " Giulia "}
    ).json()
    assert rec["voices"] == {"system:1": "Giulia"} and rec["summary"]["edits"] == 0
    assert "Giulia: buongiorno" in (folder / "call.txt").read_text()
    rec = client.post("/api/recordings/call/speaker", json={"segment": 1, "speaker": "new"}).json()
    assert rec["segments"][1]["speaker"] == "system:2" and rec["voices"]["system:2"] == "Altri"
    assert (
        client.post(
            "/api/recordings/call/speaker", json={"segment": 0, "speaker": "new"}
        ).status_code
        == 400
    )
    assert client.post("/api/recordings/call/speaker",
                       json={"segment": 1, "speaker": "mixed:1"}).status_code == 400  # fmt: skip
    rec = client.post("/api/recordings/call/undo").json()
    assert rec["segments"][1]["speaker"] == "system:1"
