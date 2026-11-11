"""ToTTI (decision 0038): reading the tail of a growing wav, the loop, the translator."""

import struct
import wave
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from parola.asr.base import ASRResult
from parola.schema import Segment
from parola.totti.live import SubtitleLoop
from parola.totti.stable import StablePrefix
from parola.totti.tail import WavFormatError, tail, wav_stream
from parola.totti.translator import MODEL_DIR, Translator
from tests.conftest import requires_ffmpeg

# ---------------------------------------------------------------- building test files


def pcm16_wav(path: Path, samples: np.ndarray, rate: int = 16_000) -> Path:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples.astype("<i2").tobytes())
    return path


def float32_wav(path: Path, samples: np.ndarray, rate: int = 48_000, channels: int = 2,
                stale_sizes: bool = False) -> Path:  # fmt: skip
    """A float32 wav built by hand, like the capture helper's; ``stale_sizes`` writes the
    zeroed chunk sizes a file still being written has."""
    data = samples.astype("<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * channels * 4, channels * 4, 32)
    riff_size = 4 + 8 + len(fmt) + 8 + len(data)
    out = b"RIFF" + struct.pack("<I", 0 if stale_sizes else riff_size) + b"WAVE"
    out += b"fmt " + struct.pack("<I", len(fmt)) + fmt
    out += b"data" + struct.pack("<I", 0 if stale_sizes else len(data)) + data
    path.write_bytes(out)
    return path


# ---------------------------------------------------------------- tail


def test_wav_stream_reads_pcm16(tmp_path: Path):
    path = pcm16_wav(tmp_path / "a.wav", np.zeros(16_000))
    s = wav_stream(path)
    assert (s.rate, s.channels, s.raw_format, s.bytes_per_frame) == (16_000, 1, "s16le", 2)
    assert path.stat().st_size - s.data_offset == 32_000


def test_wav_stream_rejects_other_files(tmp_path: Path):
    bad = tmp_path / "b.wav"
    bad.write_bytes(b"not a riff file at all")
    with pytest.raises(WavFormatError):
        wav_stream(bad)


def test_wav_stream_float32_with_stale_sizes(tmp_path: Path):
    path = float32_wav(tmp_path / "c.wav", np.zeros(4800 * 2), stale_sizes=True)
    s = wav_stream(path)
    assert (s.rate, s.channels, s.raw_format, s.bytes_per_frame) == (48_000, 2, "f32le", 8)


@requires_ffmpeg
def test_tail_takes_the_last_seconds(tmp_path: Path):
    # 3 s: two silent, then one of full-scale square wave. A 1 s tail is only the loud part.
    rate = 16_000
    samples = np.concatenate([np.zeros(2 * rate), np.tile([12_000, -12_000], rate // 2)])
    path = pcm16_wav(tmp_path / "a.wav", samples, rate)
    out = tmp_path / "tail.wav"
    available, peak, total = tail(path, 1.0, out)
    assert available == pytest.approx(1.0)
    assert total == pytest.approx(3.0)  # the audio clock: the whole file, not just the clip
    assert peak == pytest.approx(12_000 / 32_768, abs=0.01)
    with wave.open(str(out), "rb") as f:
        assert (f.getnchannels(), f.getframerate()) == (1, 16_000)
        got = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    assert got.size == pytest.approx(rate, abs=rate * 0.01)
    assert np.abs(got).max() > 8_000  # the tail is the loud second, not the silence


@requires_ffmpeg
def test_tail_of_a_short_file_is_what_there_is(tmp_path: Path):
    rate = 16_000
    path = pcm16_wav(tmp_path / "a.wav", np.zeros(rate // 2), rate)
    available, peak, total = tail(path, 15.0, tmp_path / "tail.wav")
    assert available == pytest.approx(0.5)
    assert total == pytest.approx(0.5)
    assert peak == 0.0


@requires_ffmpeg
def test_tail_reads_data_the_header_does_not_declare(tmp_path: Path):
    # A growing file: stale (zero) sizes in the header, and more frames appended after it was
    # written. The tail must come from the appended audio, not from the declared sizes.
    rate = 48_000
    path = float32_wav(tmp_path / "grow.wav", np.zeros(rate * 2, dtype=np.float32),
                       channels=1, stale_sizes=True)  # fmt: skip
    loud = np.full(rate, 0.5, dtype="<f4")
    with path.open("ab") as f:
        f.write(loud.tobytes())
    available, peak, total = tail(path, 1.0, tmp_path / "tail.wav")
    assert available == pytest.approx(1.0)
    assert total == pytest.approx(3.0)  # two declared seconds plus the appended one
    assert peak == pytest.approx(0.5, abs=0.01)


@requires_ffmpeg
def test_tail_downmixes_stereo_to_16k_mono(tmp_path: Path):
    rate = 48_000
    t = np.arange(rate, dtype=np.float32) / rate
    tone = 0.4 * np.sin(2 * np.pi * 440 * t)
    stereo = np.repeat(tone, 2)  # interleaved L R
    path = float32_wav(tmp_path / "s.wav", stereo, rate=rate, channels=2)
    out = tmp_path / "tail.wav"
    available, _, _ = tail(path, 5.0, out)
    assert available == pytest.approx(1.0)
    with wave.open(str(out), "rb") as f:
        assert (f.getnchannels(), f.getframerate(), f.getsampwidth()) == (1, 16_000, 2)
        assert f.getnframes() == pytest.approx(16_000, abs=160)


# ---------------------------------------------------------------- the loop


class FakeBackend:
    """One segment per text, one second each, anchored to the END of the audio.

    The loop may hand over a trimmed clip (the stable prefix pins the decode's left edge):
    like the real backend, this fake "hears" only what the clip holds, at times relative to
    the clip — so the same audio keeps the same absolute times across ticks.
    """

    def __init__(self, texts):
        self.texts = texts
        self.calls = 0

    def transcribe(self, wav_path, language=None):
        assert language == "en"
        self.calls += 1
        with wave.open(str(wav_path), "rb") as f:
            clip = f.getnframes() / f.getframerate()
        left = max(len(self.texts) - clip, 0.0)  # what was cut off the clip's left
        segments = [Segment(start=max(i - left, 0.0), end=i + 1 - left, text=t)
                    for i, t in enumerate(self.texts) if i + 1 - left > 0.5]  # fmt: skip
        return ASRResult(segments=segments, language="en")


class FakeTranslator:
    name = "fake"

    def __init__(self):
        self.calls: list[list[str]] = []

    def translate(self, texts):
        self.calls.append(list(texts))
        return [f"IT:{t}" for t in texts]


@requires_ffmpeg
def test_tick_translates_and_caches(tmp_path: Path):
    rate = 16_000
    path = pcm16_wav(tmp_path / "a.wav", np.tile([9_000, -9_000], rate), rate)
    backend = FakeBackend(["Hello there.", "How are you?"])
    translator = FakeTranslator()
    loop = SubtitleLoop(path, translator, backend, running=lambda: True)
    loop.tick()
    assert loop.lines == []  # the first decode commits nothing: no second opinion yet
    loop.tick()  # the second decode agrees with the first: the words are committed
    assert loop.lines == [{"en": "Hello there.", "it": "IT:Hello there."},
                          {"en": "How are you?", "it": "IT:How are you?"}]  # fmt: skip
    revision = loop.revision
    # The same window again: nothing new to commit or translate, no new revision.
    loop.tick()
    assert translator.calls == [["Hello there.", "How are you?"]]
    assert loop.revision == revision


@requires_ffmpeg
def test_tick_skips_silence(tmp_path: Path):
    rate = 16_000
    path = pcm16_wav(tmp_path / "a.wav", np.zeros(2 * rate), rate)
    backend = FakeBackend(["should never be called"])
    loop = SubtitleLoop(path, FakeTranslator(), backend, running=lambda: True)
    loop.tick()
    assert backend.calls == 0
    assert loop.lines == []


def test_tick_waits_for_the_file(tmp_path: Path):
    loop = SubtitleLoop(tmp_path / "missing.wav", FakeTranslator(), FakeBackend([]),
                        running=lambda: True)  # fmt: skip
    loop.tick()  # no file yet: no error, no lines
    assert loop.lines == []


# ---------------------------------------------------------------- the translator


requires_model = pytest.mark.skipif(
    not (MODEL_DIR / "model.bin").exists(), reason="opus-mt-en-it not converted on this machine"
)


def test_translator_available_is_the_model_dir(tmp_path: Path):
    assert not Translator(tmp_path / "nope").available()


@requires_model
def test_translator_translates_english_to_italian():
    t = Translator()
    out = t.translate(["Good morning, everyone.", "The tomatoes need water in five minutes."])
    assert len(out) == 2
    assert all(o and o not in ("Good morning, everyone.",) for o in out)
    joined = " ".join(out).lower()
    assert "minuti" in joined  # "five minutes" must come out in Italian


@requires_model
def test_translator_empty_list_is_free():
    assert Translator().translate([]) == []


# ---------------------------------------------------------------- whole-call translation

from datetime import UTC, datetime  # noqa: E402

from parola.schema import Metadata, Transcript, Word  # noqa: E402
from parola.totti.document import DocumentTranslation, targets  # noqa: E402


def call(folder: Path, name: str, langs: list[str | None], detected: str | None = None) -> Path:
    meta = Metadata(audio_file=f"{name}.system.wav", duration=float(len(langs)),
                    created_at=datetime(2026, 10, 19, tzinfo=UTC), parola_version="0",
                    language_detected=detected)  # fmt: skip
    segments = [Segment(start=i, end=i + 1, text=f"line {i}", lang=lang,
                        words=[Word(text=f"line {i}".split()[0], start=i, end=i + 0.5)])
                for i, lang in enumerate(langs)]  # fmt: skip
    t = Transcript(metadata=meta, segments=segments)
    path = folder / f"{name}.json"
    t.save(path)
    return path


def test_targets_are_the_english_segments(tmp_path: Path):
    call(tmp_path, "a", ["en", "it", "en"])
    assert targets(Transcript.load(tmp_path / "a.json")) == [0, 2]


def test_targets_fall_back_to_the_detected_language(tmp_path: Path):
    call(tmp_path, "a", [None, None], detected="en")
    assert targets(Transcript.load(tmp_path / "a.json")) == [0, 1]
    call(tmp_path, "b", [None, None], detected="it")
    assert targets(Transcript.load(tmp_path / "b.json")) == []


def test_translate_caches_until_the_transcript_changes(tmp_path: Path):
    path = call(tmp_path, "a", ["en", "it", "en"])
    translator = FakeTranslator()
    doc = DocumentTranslation(tmp_path, translator)
    data = doc.translate("a")
    assert data["lines"] == {"0": "IT:line 0", "2": "IT:line 2"}
    assert doc.status("a") == {"state": "ready", "lines": data["lines"], "engine": "fake"}
    # Fresh cache: starting again answers from disk without translating.
    assert doc.start("a")["state"] == "ready"
    assert len(translator.calls) == 1
    # A correction rewrites the transcript: the cache is stale and the state is missing.
    t = Transcript.load(path)
    t.segments[0].text = "corrected line"
    t.save(path)
    import os

    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 2))
    assert doc.status("a") == {"state": "missing"}
    assert doc.translate("a")["lines"]["0"] == "IT:corrected line"


def test_translate_prunes_caches_of_deleted_calls(tmp_path: Path):
    call(tmp_path, "gone", ["en"])
    doc = DocumentTranslation(tmp_path, FakeTranslator())
    doc.translate("gone")
    (tmp_path / "gone.json").unlink()
    call(tmp_path, "kept", ["en"])
    doc.translate("kept")
    names = sorted(p.name for p in (tmp_path / ".translations").glob("*/*.json"))
    assert names == ["kept.json"]  # inside the engine's own folder


def test_start_reports_progress_and_finishes(tmp_path: Path):
    import time

    call(tmp_path, "a", ["en"] * 40)

    class SlowTranslator(FakeTranslator):
        def translate(self, texts):
            time.sleep(0.02)
            return super().translate(texts)

    doc = DocumentTranslation(tmp_path, SlowTranslator())
    st = doc.start("a")
    assert st["state"] == "working"
    for _ in range(200):
        st = doc.status("a")
        if st["state"] == "ready":
            break
        assert st["state"] == "working"
        time.sleep(0.01)
    assert st["state"] == "ready" and len(st["lines"]) == 40


# ---------------------------------------------------------------- distillation sources (T3)

import json  # noqa: E402

from parola.totti import distill  # noqa: E402

WORDS_XML = """<?xml version="1.0"?>
<nite:root nite:id="M1.A.words" xmlns:nite="http://nite.sourceforge.net/">
   <vocalsound nite:id="M1.vocalsound.1" description="laugh"/>
   <w nite:id="M1.A.words0" starttime="1.0" endtime="1.2">Okay</w>
   <w nite:id="M1.A.words1" starttime="1.2" endtime="1.2" punc="true">,</w>
   <w nite:id="M1.A.words2" starttime="1.3" endtime="1.5">let's</w>
   <w nite:id="M1.A.words3" starttime="1.5" endtime="1.8">start</w>
   <w nite:id="M1.A.words4" starttime="1.8" endtime="1.8" punc="true">.</w>
   <w nite:id="M1.A.words5" starttime="2.0" endtime="2.3">Fine</w>
</nite:root>
"""

SEGS_XML = """<?xml version="1.0"?>
<nite:root nite:id="M1.A.segs" xmlns:nite="http://nite.sourceforge.net/">
   <segment nite:id="M1.sync.1" transcriber_start="1.0" transcriber_end="1.8">
      <nite:child href="M1.A.words.xml#id(M1.A.words0)..id(M1.A.words4)"/>
   </segment>
   <segment nite:id="M1.sync.2" transcriber_start="2.0" transcriber_end="2.3">
      <nite:child href="M1.A.words.xml#id(M1.A.words5)"/>
   </segment>
   <segment nite:id="M1.sync.3" transcriber_start="3.0" transcriber_end="3.1">
      <nite:child href="M1.A.words.xml#id(M1.A.missing)"/>
   </segment>
</nite:root>
"""


def test_segment_texts_join_words_and_attach_punctuation(tmp_path: Path):
    w = tmp_path / "M1.A.words.xml"
    s = tmp_path / "M1.A.segments.xml"
    w.write_text(WORDS_XML)
    s.write_text(SEGS_XML)
    assert distill.segment_texts(w, s) == ["Okay, let's start.", "Fine"]


def test_usable_filters_and_dedups():
    mk = lambda meeting, text: distill.Sentence("ami", meeting, "A", text)  # noqa: E731
    sentences = [mk("ES2002a", "too short here"),
                 mk("ES2002a", "this sentence is long enough to keep"),
                 mk("ES2002b", "This sentence is LONG enough to keep"),
                 mk("ES2002b", "w " * 100)]  # fmt: skip
    kept = list(distill.usable(iter(sentences)))
    assert [s.meeting for s in kept] == ["ES2002a"]


def test_series_strips_the_meeting_letter():
    assert distill.Sentence("ami", "ES2002a", "A", "x").series == "ES2002"
    assert distill.Sentence("icsi", "Bdb001", "A", "x").series == "Bdb001"


def test_save_sources_splits_by_series(tmp_path: Path):
    meetings = ["ES2002a", "ES2002b", "EN2001a", "IS1000a", "TS3005c"]
    sentences = [distill.Sentence("ami", m, "A", f"sentence number {i} long enough here")
                 for i, m in enumerate(meetings)]  # fmt: skip
    stats = distill.save_sources(sentences, tmp_path / "src.jsonl", dev_series=2)
    rows = [json.loads(x) for x in (tmp_path / "src.jsonl").read_text().splitlines()]
    assert stats["total"] == 5 and stats["train"] + stats["dev"] == 5
    by_series = {}
    for r in rows:
        key = distill.Sentence(r["corpus"], r["meeting"], r["speaker"], r["en"]).series
        by_series.setdefault(key, set()).add(r["split"])
    # No series sits in both splits.
    assert all(len(v) == 1 for v in by_series.values())
    # The last two series are dev.
    assert {k for k, v in by_series.items() if v == {"dev"}} == {"IS1000", "TS3005"}


def test_translate_sources_is_resumable(tmp_path: Path):
    src = tmp_path / "src.jsonl"
    src.write_text(
        "\n".join(
            json.dumps({"id": f"x-{i}", "split": "train", "en": f"sentence {i}"}) for i in range(5)
        )
    )
    out = tmp_path / "out.jsonl"
    calls = []

    def translate(texts):
        calls.append(list(texts))
        return [t.upper() for t in texts]

    assert distill.translate_sources(src, out, translate, batch=2) == 5
    assert len(out.read_text().splitlines()) == 5
    # A second run has nothing left to do and translates nothing.
    assert distill.translate_sources(src, out, translate, batch=2) == 0
    assert sum(len(c) for c in calls) == 5


def test_a_correction_retranslates_only_the_changed_segment(tmp_path: Path):
    import os

    path = call(tmp_path, "a", ["en", "en", "en"])
    translator = FakeTranslator()
    doc = DocumentTranslation(tmp_path, translator)
    doc.translate("a")
    t = Transcript.load(path)
    t.segments[1].text = "fixed line"
    t.save(path)
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 2))
    data = doc.translate("a")
    # Only the corrected segment went back to the model; the others came from the cache.
    assert translator.calls[-1] == ["fixed line"]
    assert data["lines"] == {"0": "IT:line 0", "1": "IT:fixed line", "2": "IT:line 2"}


def test_clean_drops_copies_empties_and_bad_ratios():
    pairs = [
        {"id": "1", "en": "Good morning everyone today.", "it": "Buongiorno a tutti oggi."},
        {"id": "2", "en": "Good morning everyone today.", "it": "good  morning EVERYONE today."},
        {"id": "3", "en": "Good morning everyone today.", "it": ""},
        {"id": "4", "en": "Good morning everyone today.", "it": "Sì."},
        {
            "id": "5",
            "en": "Good morning everyone today.",
            "it": "una traduzione molto più lunga del dovuto che continua e continua e basta",
        },
        {
            "id": "6",
            "en": "Good morning everyone today.",
            "it": "&quot; Buongiorno &quot; a tutti.",
        },
    ]
    kept, dropped = distill.clean(iter(pairs))
    # The entity pair is unescaped and kept, not dropped.
    assert [p["id"] for p in kept] == ["1", "6"]
    assert kept[1]["it"] == '" Buongiorno " a tutti.'
    assert dropped == {"copy": 1, "empty": 1, "length_ratio": 2, "unescaped": 1}


def test_default_model_dir_prefers_the_newest_student(tmp_path: Path, monkeypatch):
    from parola.totti import translator as tr

    base = tmp_path / "base"
    v0 = tmp_path / "v0"
    student = tmp_path / "student"
    base.mkdir()
    (base / "model.bin").write_bytes(b"x")
    monkeypatch.setattr(tr, "BASE_DIR", base)
    monkeypatch.setattr(tr, "V0_DIR", v0)
    monkeypatch.setattr(tr, "STUDENT_DIR", student)
    assert tr.default_model_dir() == base  # no student on disk: the base
    v0.mkdir()
    (v0 / "model.bin").write_bytes(b"x")
    assert tr.default_model_dir() == v0  # only the old student: still an accepted engine
    student.mkdir()
    (student / "model.bin").write_bytes(b"x")
    assert tr.default_model_dir() == student  # v1 wins over v0


def test_switching_engine_invalidates_the_cache(tmp_path: Path):
    call(tmp_path, "a", ["en", "en"])
    first = FakeTranslator()
    DocumentTranslation(tmp_path, first).translate("a")

    class Teacher(FakeTranslator):
        name = "teacher"

        def translate(self, texts):
            self.calls.append(list(texts))
            return [f"T:{t}" for t in texts]

    teacher = Teacher()
    doc = DocumentTranslation(tmp_path, teacher)
    assert doc.status("a") == {"state": "missing"}  # the fake's cache is not the teacher's
    data = doc.translate("a")
    assert data["engine"] == "teacher"
    assert all(v.startswith("T:") for v in data["lines"].values())
    assert sum(len(c) for c in teacher.calls) == 2  # nothing reused across engines


# ---------------------------------------------------------------- review page (move A)

from parola.totti import review  # noqa: E402


def encall(folder: Path, name: str, texts: list[str]) -> None:
    """A call whose segments carry the given English texts (the review page's shape)."""
    segments = [Segment(start=i, end=i + 1, text=text, lang="en", channel="mixed",
                        words=[Word(text=text.split()[0], start=i, end=i + 0.5)])
                for i, text in enumerate(texts)]  # fmt: skip
    meta = Metadata(audio_file=f"{name}.mixed.wav", duration=float(len(texts)),
                    created_at=datetime(2026, 10, 20, tzinfo=UTC), parola_version="0")  # fmt: skip
    Transcript(metadata=meta, segments=segments).save(folder / f"{name}.json")


def test_pending_excludes_frozen_pairs(tmp_path: Path):
    encall(tmp_path, "a", ["one two three four five", "six seven eight nine ten"])
    frozen = tmp_path / "frozen.jsonl"
    frozen.write_text(json.dumps({"call": "a", "segment": 0, "en": "x", "it": "y"}) + "\n")
    left = review.pending(tmp_path, frozen)
    assert [(p.call, p.segment) for p in left] == [("a", 1)]


def test_pending_without_a_frozen_file(tmp_path: Path):
    encall(tmp_path, "a", ["one two three four five"])
    assert len(review.pending(tmp_path, tmp_path / "missing.jsonl")) == 1


def test_clip_command_pads_and_pipes():
    cmd = review.clip_command(Path("x.flac"), 3.0, 5.5)
    assert cmd[cmd.index("-ss") + 1] == "2.80"
    assert cmd[cmd.index("-to") + 1] == "5.70"
    assert cmd[-1] == "pipe:1" and "-nostdin" in cmd


def test_clip_command_never_starts_before_zero():
    cmd = review.clip_command(Path("x.flac"), 0.1, 1.0)
    assert cmd[cmd.index("-ss") + 1] == "0.00"


# ------------------------------------------------------------- the stable prefix (live fix)


def seg(start: float, end: float, text: str) -> Segment:
    return Segment(start=start, end=end, text=text)


def test_nothing_commits_on_the_first_decode():
    sp = StablePrefix()
    sp.update([seg(0, 2, "hello there everyone")], offset=0.0)
    assert sp.lines() == []


def test_two_agreeing_decodes_commit_the_words():
    sp = StablePrefix()
    sp.update([seg(0, 2, "hello there everyone")], offset=0.0)
    sp.update([seg(0, 2, "hello there everyone")], offset=0.0)
    assert sp.lines() == ["hello there everyone"]


def test_disagreement_stops_the_commit_at_that_word():
    sp = StablePrefix()
    sp.update([seg(0, 3, "hello there every one")], offset=0.0)
    sp.update([seg(0, 3, "hello there anyone else")], offset=0.0)
    assert sp.lines() == ["hello there"]


def test_agreement_ignores_case_and_punctuation():
    sp = StablePrefix()
    sp.update([seg(0, 2, "hello, There")], offset=0.0)
    sp.update([seg(0, 2, "Hello there")], offset=0.0)
    assert sp.lines() == ["Hello there"]  # the latest surface form is kept


def test_a_word_that_drifts_too_far_does_not_commit():
    sp = StablePrefix()
    sp.update([seg(0, 2, "hello there")], offset=0.0)
    sp.update([seg(0, 2, "hello there")], offset=3.0)  # same text, 3 s later on the clock
    assert sp.lines() == []


def test_sentence_punctuation_closes_a_line():
    sp = StablePrefix()
    sp.update([seg(0, 4, "that is done. next topic")], offset=0.0)
    sp.update([seg(0, 4, "that is done. next topic")], offset=0.0)
    assert sp.lines() == ["that is done.", "next topic"]
    assert sp.closed == ["that is done."]


def test_committed_words_never_recommit_as_the_window_slides():
    sp = StablePrefix()
    sp.update([seg(0, 2, "one two three four")], offset=0.0)
    sp.update([seg(0, 2, "one two three four")], offset=0.0)
    # The window slid: the same words now sit earlier in the clip, plus a new one.
    sp.update([seg(0, 2.5, "one two three four five")], offset=0.0)
    sp.update([seg(0, 2.5, "one two three four five")], offset=0.0)
    assert sp.lines() == ["one two three four five"]


def test_the_shown_lines_only_grow_under_agreement():
    sp = StablePrefix()
    shown: list[list[str]] = []
    for upto in (2, 3, 4, 5, 6):
        text = " ".join(f"word{i}" for i in range(upto))
        sp.update([seg(0, float(upto), text)], offset=0.0)
        shown.append(sp.lines())
    for before, after in pairwise(shown):
        for i, line in enumerate(before):
            assert after[i].startswith(line)  # refined by growth, never rewritten


def test_a_long_line_closes_without_punctuation():
    sp = StablePrefix()
    text = " ".join(f"blah{i:02d}" for i in range(30))  # 30 words, no full stop
    sp.update([seg(0, 30, text)], offset=0.0)
    sp.update([seg(0, 30, text)], offset=0.0)
    assert sp.closed and all(len(x) <= 130 for x in sp.closed)


def test_rest_clears_the_display_and_moves_the_clock():
    sp = StablePrefix()
    sp.update([seg(0, 2, "hello there.")], offset=0.0)
    sp.update([seg(0, 2, "hello there.")], offset=0.0)
    sp.rest(at=20.0)
    assert sp.lines() == [] and sp.frontier == 20.0 and sp.anchor == 20.0
    # What was heard before the silence cannot come back.
    sp.update([seg(0, 2, "hello there.")], offset=0.0)
    sp.update([seg(0, 2, "hello there.")], offset=0.0)
    assert sp.lines() == []


def test_words_past_the_deadline_commit_without_agreement():
    sp = StablePrefix()
    sp.update([seg(0, 4, "one two three four")], offset=0.0, deadline=2.0)
    assert sp.lines() == ["one two"]  # the first decode is their last chance


def test_a_garbled_head_does_not_block_the_stable_words_behind_it():
    sp = StablePrefix()
    sp.update([seg(0, 5, "tomato beds that we water")], offset=0.0)
    sp.update([seg(0, 5, "raised beds that we water")], offset=0.0)
    # The head disagreed, the next three words agree: the head goes through as last heard.
    assert sp.lines() == ["raised beds that we water"]


def test_a_closed_line_advances_the_anchor():
    sp = StablePrefix()
    sp.update([seg(0, 4, "that is done. next")], offset=0.0)
    sp.update([seg(0, 4, "that is done. next")], offset=0.0)
    # Four words over four seconds: "done." ends at 3.0 and closes the line there.
    assert sp.anchor == pytest.approx(3.0)
    assert sp.frontier == pytest.approx(4.0)


def test_pending_is_the_uncommitted_tail():
    sp = StablePrefix()
    sp.update([seg(0, 2, "hello there everyone")], offset=0.0)
    assert sp.pending() == "hello there everyone"  # first decode: all provisional
    sp.update([seg(0, 2, "hello there everyone")], offset=0.0)
    assert sp.pending() == ""  # second decode: all committed


@requires_ffmpeg
def test_en_mode_skips_translation_and_publishes_the_tail(tmp_path: Path):
    rate = 16_000
    path = pcm16_wav(tmp_path / "a.wav", np.tile([9_000, -9_000], rate), rate)
    backend = FakeBackend(["Hello there.", "How are you?"])
    translator = FakeTranslator()
    loop = SubtitleLoop(path, translator, backend, running=lambda: True, lang="en")
    loop.tick()
    assert loop.lines == [] and loop.tail == "Hello there. How are you?"
    assert loop.revision == 1  # the tail alone is worth publishing in the EN view
    loop.tick()
    assert loop.lines == [{"en": "Hello there.", "it": ""},
                          {"en": "How are you?", "it": ""}]  # fmt: skip
    assert loop.tail == ""
    assert translator.calls == []  # the EN view never translates


@requires_ffmpeg
def test_switching_to_italian_translates_the_committed_lines(tmp_path: Path):
    rate = 16_000
    path = pcm16_wav(tmp_path / "a.wav", np.tile([9_000, -9_000], rate), rate)
    backend = FakeBackend(["Hello there."])
    translator = FakeTranslator()
    loop = SubtitleLoop(path, translator, backend, running=lambda: True, lang="en")
    loop.tick()
    loop.tick()
    assert loop.lines == [{"en": "Hello there.", "it": ""}]
    loop.lang = "it"
    loop.tick()
    assert loop.lines == [{"en": "Hello there.", "it": "IT:Hello there."}]
    assert translator.calls == [["Hello there."]]


@requires_ffmpeg
def test_nothing_is_published_after_stop(tmp_path: Path):
    rate = 16_000
    path = pcm16_wav(tmp_path / "a.wav", np.tile([9_000, -9_000], rate), rate)
    backend = FakeBackend(["Hello there."])
    loop = SubtitleLoop(path, FakeTranslator(), backend, running=lambda: True)
    loop.tick()
    frozen = (loop.lines, loop.tail, loop.revision)
    loop.stop()  # the strip closed: whatever a tick in flight finds stays unpublished
    loop.tick()
    assert (loop.lines, loop.tail, loop.revision) == frozen


def test_the_loop_warms_the_backend_before_the_first_tick(tmp_path: Path):
    class WarmBackend(FakeBackend):
        def __init__(self, texts):
            super().__init__(texts)
            self.warmed = 0

        def warm(self):
            self.warmed += 1

    backend = WarmBackend([])
    loop = SubtitleLoop(tmp_path / "missing.wav", FakeTranslator(), backend,
                        running=lambda: False)  # fmt: skip
    loop.start()
    loop.join(5)
    assert backend.warmed == 1 and backend.calls == 0


def test_a_cache_without_a_declared_engine_is_never_served(tmp_path: Path):
    call(tmp_path, "a", ["en"])
    translator = FakeTranslator()
    doc = DocumentTranslation(tmp_path, translator)
    doc.translate("a")
    cache = tmp_path / ".translations" / "fake" / "a.json"
    data = json.loads(cache.read_text())
    del data["engine"]  # an old or hand-touched file: no engine on record
    cache.write_text(json.dumps(data))
    assert doc.status("a") == {"state": "missing"}  # not served, whatever it contains
    doc.translate("a")  # regenerates, labeled again
    assert json.loads(cache.read_text())["engine"] == "fake"
    assert doc.status("a")["engine"] == "fake"


def test_a_cache_in_the_wrong_engine_folder_is_never_served(tmp_path: Path):
    call(tmp_path, "a", ["en"])
    translator = FakeTranslator()
    doc = DocumentTranslation(tmp_path, translator)
    doc.translate("a")
    cache = tmp_path / ".translations" / "fake" / "a.json"
    data = json.loads(cache.read_text())
    data["engine"] = "someone-else"  # copied from another engine's folder
    cache.write_text(json.dumps(data))
    assert doc.status("a") == {"state": "missing"}
