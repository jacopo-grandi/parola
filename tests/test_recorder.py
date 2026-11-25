import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parola.asr import ASRResult
from parola.capture import recorder
from parola.schema import Metadata, Segment, Transcript, Word
from tests.conftest import requires_ffmpeg


def part(*segments):
    return Transcript(
        metadata=Metadata(audio_file="x.wav", duration=10, backend="fake", model="m",
                          created_at=datetime(2026, 10, 15, tzinfo=UTC), parola_version="0"),
        segments=list(segments),
    )  # fmt: skip


def seg(start, end, text):
    return Segment(start=start, end=end, text=text, lang="it",
                   words=[Word(text=t) for t in text.split()])  # fmt: skip


def test_merge_channels_sorts_and_labels():
    mic = part(seg(0, 2, "ciao a tutti"), seg(5, 6, "sì"))
    system = part(seg(2.5, 4, "ciao Jacopo"), seg(5, 7, "allora iniziamo"))
    merged = recorder.merge_channels({"mic": mic, "system": system}, "call.*.wav", 7.0)
    assert [(s.start, s.channel) for s in merged.segments] == [
        (0, "mic"), (2.5, "system"), (5, "mic"), (5, "system"),
    ]  # fmt: skip
    assert merged.metadata.duration == 7.0
    assert recorder.to_dialogue(merged).splitlines() == [
        "[00:00:00.000 - 00:00:02.000] Io: ciao a tutti",
        "[00:00:02.500 - 00:00:04.000] Altri: ciao Jacopo",
        "[00:00:05.000 - 00:00:06.000] Io: sì",
        "[00:00:05.000 - 00:00:07.000] Altri: allora iniziamo",
    ]


def _write(path: Path, source: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    source, "-ac", "1", "-ar", "48000", str(path)], check=True)  # fmt: skip


@requires_ffmpeg
def test_peak_detects_digital_silence(tmp_path):
    _write(tmp_path / "tone.wav", "sine=frequency=440:duration=1")
    _write(tmp_path / "silence.wav", "anullsrc=r=48000:cl=mono:d=1")
    assert recorder.peak(tmp_path / "tone.wav") > 1000
    assert recorder.peak(tmp_path / "silence.wav") < recorder.SILENCE_PEAK


class FakeBackend:
    name, model = "fake", "fake-model"

    def __init__(self):
        self.calls = 0

    def transcribe(self, wav_path, language=None):
        self.calls += 1
        return ASRResult(segments=[seg(0, 1, f"canale {self.calls}")], language="it")


@requires_ffmpeg
def test_transcribe_recording_skips_silent_channel(tmp_path, capsys):
    _write(tmp_path / "c.mic.wav", "sine=frequency=440:duration=1")
    _write(tmp_path / "c.system.wav", "anullsrc=r=48000:cl=mono:d=1")
    rec = recorder.Recording("c", tmp_path, {"mic": {"seconds": 1.0}, "system": {"seconds": 1.0}})
    backend = FakeBackend()
    json_path, txt_path = recorder.transcribe_recording(rec, backend)
    assert backend.calls == 1  # the silent system channel is not transcribed
    assert "system channel is silent" in capsys.readouterr().err
    t = Transcript.load(json_path)
    assert [s.channel for s in t.segments] == ["mic"]
    assert txt_path.read_text().startswith("[00:00:00.000 - 00:00:01.000] Io: canale 1")
    assert not list(tmp_path.glob(".c.*"))  # temporary folders removed


def _write_dead_mic(path: Path, seconds: int = 30, rate: int = 16000) -> None:
    """Room rustle with one pop: passes the peak check, holds no speech (the 2026-10-23 mic)."""
    import wave

    import numpy as np

    x = np.random.default_rng(0).normal(0, 30, rate * seconds).astype("<i2")  # ~-61 dBFS
    x[rate : rate + rate // 5] = 8000  # a 0.2 s pop, peak well above SILENCE_PEAK
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(x.tobytes())


@requires_ffmpeg
def test_speech_share_separates_speech_from_rustle(tmp_path):
    _write(tmp_path / "tone.wav", "sine=frequency=440:duration=5")
    _write_dead_mic(tmp_path / "dead.wav")
    assert recorder.speech_share(tmp_path / "tone.wav") == 1.0
    assert recorder.speech_share(tmp_path / "dead.wav") < recorder.SPEECH_SHARE_MIN
    assert recorder.peak(tmp_path / "dead.wav") > recorder.SILENCE_PEAK  # the pop fools peak()


@requires_ffmpeg
def test_a_channel_with_no_speech_is_skipped_and_said_on_the_call(tmp_path, capsys):
    _write_dead_mic(tmp_path / "c.mic.wav")
    _write(tmp_path / "c.system.wav", "sine=frequency=440:duration=30")
    rec = recorder.Recording("c", tmp_path, {
        "mic": {"seconds": 30.0, "input_device": "RØDE Unify Virtual"},
        "system": {"seconds": 30.0},
    })  # fmt: skip
    backend = FakeBackend()
    json_path, _ = recorder.transcribe_recording(rec, backend)
    assert backend.calls == 1  # the dead mic never reaches Whisper: no hallucinated segments
    err = capsys.readouterr().err
    assert "almost no speech" in err and "RØDE Unify Virtual" in err
    t = Transcript.load(json_path)
    assert [s.channel for s in t.segments] == ["system"]
    # The warning is persisted where the app reads it, with the device that was recorded.
    cap = json.loads((tmp_path / "c.capture.json").read_text())
    [w] = cap["warnings"]
    assert (w["channel"], w["kind"], w["device"]) == ("mic", "no_speech", "RØDE Unify Virtual")
    assert w["share"] < recorder.SPEECH_SHARE_MIN


@requires_ffmpeg
def test_transcribe_recording_all_silent(tmp_path):
    _write(tmp_path / "c.mic.wav", "anullsrc=r=48000:cl=mono:d=1")
    rec = recorder.Recording("c", tmp_path, {"mic": {"seconds": 1.0}})
    with pytest.raises(recorder.CaptureError, match="silent"):
        recorder.transcribe_recording(rec, FakeBackend())


def test_helper_is_not_rebuilt_when_up_to_date(monkeypatch, tmp_path):
    binary = tmp_path / "parola-capture"
    binary.write_text("")
    monkeypatch.setattr(recorder, "BINARY", binary)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("rebuilt"))
    future = max(p.stat().st_mtime for p in recorder.SWIFT_DIR.iterdir()) + 10
    import os

    os.utime(binary, (future, future))
    assert recorder.build_helper() == binary


def test_marked_text_groups_doubtful_runs_and_keeps_punctuation():
    words = [
        Word(text="ci"),
        Word(text="vediamo", doubtful=True),
        Word(text="lunedì,", doubtful=True),
        Word(text="ok"),
        Word(text="grazie.", doubtful=True),
    ]
    s = Segment(start=0, end=3, text=" ".join(w.text for w in words), words=words)
    assert recorder.marked_text(s) == "ci (vediamo lunedì?), ok (grazie?)."


def test_marked_text_roundtrips_as_uncertain_in_annotation():
    from parola.annotate import from_annotation

    words = [Word(text="ci"), Word(text="vediamo", doubtful=True), Word(text="lunedì.")]
    line = recorder.marked_text(Segment(start=0, end=3, text="", words=words))
    t = from_annotation(f"# status: reviewed\n# audio_file: x.wav\n# duration: 3\n"
                        f"[00:00:00.000 - 00:00:03.000] lang=it\n{line}\n")  # fmt: skip
    assert [(w.text, "uncertain" in w.tags) for w in t.segments[0].words] == [
        ("ci", False),
        ("vediamo", True),
        ("lunedì.", False),
    ]


def test_mark_doubts():
    primary = part(seg(0, 2, "ci vediamo lunedì"), seg(3, 4, "va bene"))
    second = part(seg(0, 2, "ci vediamo martedì"), seg(3, 4, "va bene"))
    assert recorder.mark_doubts(primary, second) == 1
    assert [w.doubtful for s in primary.segments for w in s.words] == [
        False,
        False,
        True,
        False,
        False,
    ]


@requires_ffmpeg
def test_transcribe_recording_with_second_opinion(tmp_path):
    _write(tmp_path / "c.mic.wav", "sine=frequency=440:duration=1")
    rec = recorder.Recording("c", tmp_path, {"mic": {"seconds": 1.0}})

    class Second:
        name, model = "second", "m"

        def transcribe(self, wav_path, language=None):
            return ASRResult(segments=[seg(0, 1, "canale due")], language="it")

    json_path, txt_path = recorder.transcribe_recording(rec, FakeBackend(), second=Second())
    t = Transcript.load(json_path)
    assert [(w.text, w.doubtful) for w in t.segments[0].words] == [("canale", False), ("1", True)]
    assert txt_path.read_text().strip().endswith("Io: canale (1?)")


def test_drop_echo_removes_mic_segments_repeating_the_other_side():
    system = part(
        Segment(
            start=10,
            end=13,
            text="ci sono domande?",
            lang="it",
            words=[
                Word(text="ci", start=10.0, end=10.3),
                Word(text="sono", start=10.4, end=10.8),
                Word(text="domande?", start=10.9, end=11.6),
            ],
        )
    )
    mic = part(
        Segment(start=10.2, end=11.8, text="ci sono domande?", lang="it", words=[
            Word(text="ci", start=10.2, end=10.4), Word(text="sono", start=10.5, end=10.9),
            Word(text="domande?", start=11.0, end=11.8)]),
        Segment(start=20, end=21, text="io ho una domanda", lang="it", words=[
            Word(text=t, start=20.0, end=21.0) for t in ["io", "ho", "una", "domanda"]]),
    )  # fmt: skip
    assert recorder.drop_echo(mic, system) == 1
    assert [s.text for s in mic.segments] == ["io ho una domanda"]


@requires_ffmpeg
def test_transcribe_recording_merges_close_segments(tmp_path):
    _write(tmp_path / "c.mic.wav", "sine=frequency=440:duration=1")
    rec = recorder.Recording("c", tmp_path, {"mic": {"seconds": 1.0}})

    class Fragmented:
        name, model = "fake", "m"

        def transcribe(self, wav_path, language=None):
            return ASRResult(segments=[seg(0, 0.4, "ciao"), seg(0.6, 0.9, "a tutti"),
                                       seg(5, 6, "dopo")], language="it")  # fmt: skip

    json_path, _ = recorder.transcribe_recording(rec, Fragmented())
    assert [s.text for s in Transcript.load(json_path).segments] == ["ciao a tutti", "dopo"]


@requires_ffmpeg
def test_preview_comes_after_whisper_and_again_with_the_voices(tmp_path, monkeypatch):
    """Decisions 0031, 0058: the text after Whisper, then with the voices, the doubts last;
    a preview is never changed by the steps after it."""
    monkeypatch.setattr(recorder, "_speaker_models", lambda: True)
    monkeypatch.setattr("parola.diarize.diarize", lambda path, progress=None: [(0.0, 1.0, 0)])
    _write(tmp_path / "c.mixed.wav", "sine=frequency=440:duration=1")
    rec = recorder.Recording("c", tmp_path, {"mixed": {"seconds": 1.0}})

    class Second:
        name, model = "second", "m"

        def transcribe(self, wav_path, language=None):
            return ASRResult(segments=[seg(0, 1, "canale due")], language="it")

    seen = []
    json_path, _ = recorder.transcribe_recording(
        rec, FakeBackend(), second=Second(), preview=lambda stage, t: seen.append((stage, t))
    )
    assert [stage for stage, _ in seen] == ["transcript", "voices"]
    first, voices = seen[0][1], seen[1][1]
    assert all(s.speaker is None for s in first.segments)
    assert [s.speaker for s in voices.segments] == ["mixed:1"]
    # Copies: the doubtful words marked afterwards never reach them.
    assert not any(w.doubtful for s in [*first.segments, *voices.segments] for w in s.words)
    final = Transcript.load(json_path)
    assert [s.speaker for s in final.segments] == ["mixed:1"]
    assert [w.doubtful for s in final.segments for w in s.words] == [False, True]


@requires_ffmpeg
def test_one_preview_when_there_are_no_voices_to_wait_for(tmp_path, monkeypatch):
    # Without voices the text is final right after Whisper: only the doubts are to come.
    monkeypatch.setattr(recorder, "_speaker_models", lambda: False)
    _write(tmp_path / "c.mixed.wav", "sine=frequency=440:duration=1")
    rec = recorder.Recording("c", tmp_path, {"mixed": {"seconds": 1.0}})
    seen = []
    recorder.transcribe_recording(rec, FakeBackend(), second=FakeBackend(),
                                  preview=lambda stage, t: seen.append(stage))  # fmt: skip
    assert seen == ["voices"]


@requires_ffmpeg
def test_two_people_skips_the_voices_and_keeps_the_doubts(tmp_path, monkeypatch):
    """Decision 0058: with only two people nobody is told apart, the other side is one voice
    that can be named at once, and the doubtful words still come, last."""
    monkeypatch.setattr(recorder, "_speaker_models", lambda: pytest.fail("models looked up"))
    monkeypatch.setattr("parola.diarize.diarize", lambda *a, **k: pytest.fail("diarized"))
    for c in ("mic", "system"):
        _write(tmp_path / f"c.{c}.wav", "sine=frequency=440:duration=1")
    rec = recorder.Recording("c", tmp_path, {"mic": {"seconds": 1.0}, "system": {"seconds": 1.0}})

    class Sides(FakeBackend):
        """The mic first, then the other side, as the channels are transcribed."""

        def __init__(self, other):
            super().__init__()
            self.texts = ["ciao come va", other]

        def transcribe(self, wav_path, language=None):
            text = self.texts.pop(0)
            start = 0 if text.startswith("ciao") else 2
            return ASRResult(segments=[seg(start, start + 1, text)], language="it")

    seen = []
    json_path, txt_path = recorder.transcribe_recording(
        rec, Sides("tutto bene grazie"), second=Sides("tutto bene grazia"), two_people=True,
        preview=lambda stage, t: seen.append((stage, t)))  # fmt: skip
    assert [stage for stage, _ in seen] == ["voices"]
    t = Transcript.load(json_path)
    assert [(s.channel, s.speaker) for s in t.segments] == [("mic", None), ("system", "system:1")]
    assert [w.text for w in t.segments[1].words if w.doubtful] == ["grazie"]
    assert "Altri: tutto bene (grazie?)" in txt_path.read_text()


@requires_ffmpeg
def test_whisper_runs_on_both_sides_before_the_second_opinion(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "_speaker_models", lambda: False)
    for c in ("mic", "system"):
        _write(tmp_path / f"c.{c}.wav", "sine=frequency=440:duration=1")
    rec = recorder.Recording("c", tmp_path, {"mic": {"seconds": 1.0}, "system": {"seconds": 1.0}})
    order = []

    class Logged(FakeBackend):
        def __init__(self, label):
            super().__init__()
            self.label = label

        def transcribe(self, wav_path, language=None):
            order.append((self.label, Path(wav_path).name))
            return super().transcribe(wav_path, language)

    seen = []

    def preview(stage, t):
        seen.append((len(order), t))

    whisper, parakeet = Logged("whisper"), Logged("parakeet")
    recorder.transcribe_recording(rec, whisper, second=parakeet, preview=preview)
    assert [label for label, _ in order] == ["whisper", "whisper", "parakeet", "parakeet"]
    assert seen[0][0] == 2  # shown after the two Whisper runs
    assert {s.channel for s in seen[0][1].segments} == {"mic", "system"}


@requires_ffmpeg
def test_the_text_grows_while_whisper_works(tmp_path, monkeypatch):
    """Decision 0033: what Whisper has written so far, before the whole channel is done."""
    monkeypatch.setattr(recorder, "_speaker_models", lambda: False)
    monkeypatch.setattr(recorder, "PARTIAL_EVERY", 0.0)
    _write(tmp_path / "c.mixed.wav", "sine=frequency=440:duration=1")
    rec = recorder.Recording("c", tmp_path, {"mixed": {"seconds": 1.0}})

    class Growing(FakeBackend):
        on_segments = None

        def transcribe(self, wav_path, language=None):
            done = [seg(0, 1, "prima frase"), seg(1, 2, "seconda frase")]
            for k in (1, 2):  # one window, then two
                self.on_segments(done[:k])
            return ASRResult(segments=done, language="it")

    seen = []
    backend = Growing()
    recorder.transcribe_recording(rec, backend, second=FakeBackend(),
                                  preview=lambda stage, t: seen.append(
                                      (stage, [s.text for s in t.segments])))  # fmt: skip
    # Close sentences are joined as in the final transcript (merge_segments).
    assert seen[:2] == [("partial", ["prima frase"]), ("partial", ["prima frase seconda frase"])]
    assert seen[-1][0] == "voices"
    assert backend.on_segments is None  # unhooked when the channel is done


@requires_ffmpeg
def test_a_failing_preview_never_costs_the_transcript(tmp_path, monkeypatch, capsys):
    """A 25-minute call once aborted mid-decode, its text lost, because one partial segment
    list failed the sort validation inside the preview path: the preview is best effort."""
    monkeypatch.setattr(recorder, "_speaker_models", lambda: False)
    monkeypatch.setattr(recorder, "PARTIAL_EVERY", 0.0)
    _write(tmp_path / "c.mixed.wav", "sine=frequency=440:duration=1")
    rec = recorder.Recording("c", tmp_path, {"mixed": {"seconds": 1.0}})

    class OutOfOrder(FakeBackend):
        on_segments = None

        def transcribe(self, wav_path, language=None):
            done = [seg(0.94, 2, "prima"), seg(1, 2, "seconda")]
            self.on_segments([done[1], done[0]])  # a 60 ms inversion, as on the real call
            return ASRResult(segments=done, language="it")

    seen = []
    json_path, _ = recorder.transcribe_recording(rec, OutOfOrder(), second=FakeBackend(),
                                                 preview=lambda s, t: seen.append(s))  # fmt: skip
    assert json_path.exists()  # the call is saved despite the broken preview
    assert "partial preview skipped" in capsys.readouterr().err
    assert seen[-1] == "voices"  # the previews after the decode still arrive
