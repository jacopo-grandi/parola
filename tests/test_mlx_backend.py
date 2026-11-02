from parola.asr.mlx_whisper_backend import convert_result

# Shape of the dict returned by mlx_whisper.transcribe(..., word_timestamps=True).
SAMPLE = {
    "text": " Ehm, allora iniziamo.",
    "language": "it",
    "segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 2.4,
            "text": " Ehm, allora iniziamo.",
            "words": [
                {"word": " Ehm,", "start": 0.0, "end": 0.5, "probability": 0.61},
                {"word": " allora", "start": 0.6, "end": 1.2, "probability": 0.97},
                {"word": " ", "start": 1.2, "end": 1.2, "probability": 0.1},
                {"word": " iniziamo.", "start": 1.3, "end": 1.29, "probability": 0.95},
            ],
        }
    ],
}


def test_convert_result():
    result = convert_result(SAMPLE)
    assert result.language == "it"
    [seg] = result.segments
    assert seg.text == "Ehm, allora iniziamo."
    assert seg.lang == "it"
    assert [w.text for w in seg.words] == ["Ehm,", "allora", "iniziamo."]
    assert seg.words[0].probability == 0.61


def test_convert_result_clamps_inverted_word_times():
    last = convert_result(SAMPLE).segments[0].words[-1]
    assert last.start == 1.3
    assert last.end == 1.3


def test_convert_result_empty():
    result = convert_result({"text": "", "segments": [], "language": "en"})
    assert result.segments == []
    assert result.language == "en"


def test_convert_result_merges_elisions():
    raw = {
        "language": "it",
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": " Sei d'accordo?",
                "words": [
                    {"word": " Sei", "start": 0.0, "end": 0.3, "probability": 0.9},
                    {"word": " d", "start": 0.4, "end": 0.5, "probability": 0.8},
                    {"word": "'accordo?", "start": 0.5, "end": 1.1, "probability": 0.6},
                ],
            }
        ],
    }
    [seg] = convert_result(raw).segments
    assert [w.text for w in seg.words] == ["Sei", "d'accordo?"]
    merged = seg.words[1]
    assert (merged.start, merged.end, merged.probability) == (0.4, 1.1, 0.6)
    assert " ".join(w.text for w in seg.words) == seg.text


def test_convert_result_keeps_segments_sorted():
    raw = {
        "language": "it",
        "segments": [
            {"start": 10.0, "end": 10.5, "text": " creare", "words": []},
            {"start": 9.0, "end": 9.4, "text": " passare", "words": []},
            {"start": 11.0, "end": 12.0, "text": " poi", "words": []},
        ],
    }
    segs = convert_result(raw).segments
    assert [s.text for s in segs] == ["creare", "passare", "poi"]
    assert [(s.start, s.end) for s in segs] == [(10.0, 10.5), (10.0, 10.0), (11.0, 12.0)]


def test_sound_share():
    from parola.asr.mlx_whisper_backend import sound_share

    regions = [(1.0, 2.0), (4.0, 6.0)]
    assert sound_share(0.0, 4.0, regions) == 0.25
    assert sound_share(4.5, 5.5, regions) == 1.0
    assert sound_share(2.5, 3.5, regions) == 0.0
    assert sound_share(1.5, 1.5, regions) == 1.0 and sound_share(3.0, 3.0, regions) == 0.0


def _seg(text: str, start: float, end: float, words=None):
    from parola.schema import Segment

    return Segment(start=start, end=end, text=text, words=words or [])


def _word(text: str, start: float, end: float):
    from parola.schema import Word

    return Word(text=text, start=start, end=end)


def test_uncovered_regions():
    from pytest import approx

    from parola.asr.mlx_whisper_backend import uncovered_regions

    regions = [(0.0, 5.0), (10.0, 36.0), (40.0, 42.0), (50.0, 60.0)]
    segments = [
        _seg(
            "ciao a tutti",
            0.5,
            4.0,
            [_word("ciao", 0.5, 1.0), _word("a", 1.2, 1.4), _word("tutti", 1.5, 4.0)],
        ),
        # Word times lost: the segment span covers its stretch, and what follows it in the
        # region ((55.3, 60), 4.7 s) is still a gap.
        _seg("va bene", 51.0, 55.0),
    ]
    # (10, 36) has no word on it; (40, 42) is shorter than GAP_MIN_SECONDS.
    assert uncovered_regions(segments, regions) == approx([(10.0, 36.0), (55.3, 60.0)])
    # A word at the region's edge covers its tolerance, not the whole region.
    near = [*segments, _seg("si", 9.5, 9.9, [_word("si", 9.5, 9.9)])]
    assert uncovered_regions(near, regions) == approx([(10.0, 36.0), (55.3, 60.0)])
    assert uncovered_regions([], regions) == [(0.0, 5.0), (10.0, 36.0), (50.0, 60.0)]


def test_a_region_covered_only_on_its_tail_still_repairs_its_head():
    """The real call of 2026-11-02: one sound region of 40 s, words only from second 28 - the
    23 skipped seconds at its head must come back as a gap."""
    from pytest import approx

    from parola.asr.mlx_whisper_backend import uncovered_regions

    words = [_word(f"w{k}", 28.0 + k, 28.6 + k) for k in range(17)]
    segments = [_seg("parole in coda", 28.0, 45.2, words)]
    (gap,) = uncovered_regions(segments, [(4.5, 45.2)])
    assert gap == approx((4.5, 28.0))


def test_shifted():
    from parola.asr.mlx_whisper_backend import shifted

    [seg] = shifted([_seg("ciao", 1.0, 2.0, [_word("ciao", 1.0, 2.0)])], 178.5)
    assert (seg.start, seg.end) == (179.5, 180.5)
    assert (seg.words[0].start, seg.words[0].end) == (179.5, 180.5)
    assert seg.text == "ciao"


def test_write_clip(tmp_path):
    import wave

    import numpy as np

    from parola.asr.mlx_whisper_backend import write_clip

    rate = 16_000
    samples = np.arange(rate, dtype=np.int16)  # 1 s, recognisable values
    src = tmp_path / "src.wav"
    with wave.open(str(src), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples.tobytes())

    clip = tmp_path / "clip.wav"
    write_clip(src, 0.25, 0.5, clip)
    with wave.open(str(clip), "rb") as f:
        assert f.getframerate() == rate
        got = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    assert np.array_equal(got, samples[rate // 4 : rate // 2])

    # Bounds outside the file are clamped, not an error.
    write_clip(src, 0.9, 2.0, clip)
    with wave.open(str(clip), "rb") as f:
        assert f.getnframes() == rate - int(0.9 * rate)


def test_repair_gaps(tmp_path, monkeypatch):
    """The skipped region is decoded on its own clip and grafted back at the right offset."""
    import sys
    import wave
    from types import SimpleNamespace

    import numpy as np

    from parola.asr.mlx_whisper_backend import GAP_MARGIN_SECONDS, MLXWhisperBackend

    rate = 16_000
    wav = tmp_path / "audio.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(np.zeros(rate * 30, dtype=np.int16).tobytes())

    calls: list[str] = []

    def fake_transcribe(path, **kwargs):
        calls.append(path)
        with wave.open(path, "rb") as f:
            assert f.getframerate() == rate
        # Clip-local times; "fuori" lies in the margin, outside the gap region.
        return {
            "language": "it",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": " fuori",
                 "words": [{"word": " fuori", "start": 0.0, "end": 1.0, "probability": 0.9}]},
                {"start": 2.0, "end": 4.0, "text": " dentro la regione",
                 "words": [{"word": " dentro", "start": 2.0, "end": 2.5, "probability": 0.9},
                           {"word": " la", "start": 2.6, "end": 2.8, "probability": 0.9},
                           {"word": " regione", "start": 2.9, "end": 4.0, "probability": 0.9}]},
            ],
        }  # fmt: skip

    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(transcribe=fake_transcribe))

    backend = MLXWhisperBackend(vad=True)
    before = [
        _seg("prima", 1.0, 2.0, [_word("prima", 1.0, 2.0)]),
        _seg("dopo", 25.0, 26.0, [_word("dopo", 25.0, 26.0)]),
    ]
    regions = [(0.5, 2.5), (10.0, 20.0), (24.5, 26.5)]
    repaired = backend._repair_gaps(wav, before, regions, "it")

    assert len(calls) == 1  # only the (10, 20) gap is decoded again
    assert [s.text for s in repaired] == ["prima", "dentro la regione", "dopo"]
    grafted = repaired[1]
    offset = 10.0 - GAP_MARGIN_SECONDS
    assert grafted.start == offset + 2.0 and grafted.end == offset + 4.0
    assert grafted.words[0].start == offset + 2.0

    # Nothing uncovered: no decoding at all.
    calls.clear()
    assert backend._repair_gaps(wav, before, [(0.5, 2.5)], "it") == before
    assert calls == []


def test_repair_gaps_cap(tmp_path, monkeypatch):
    import sys
    import wave
    from types import SimpleNamespace

    import numpy as np

    from parola.asr.mlx_whisper_backend import MAX_GAP_REPAIRS, MLXWhisperBackend

    rate = 16_000
    wav = tmp_path / "audio.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(np.zeros(rate * 120, dtype=np.int16).tobytes())

    calls: list[str] = []

    def fake_transcribe(path, **kwargs):
        calls.append(path)
        return {"language": "it", "segments": []}

    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(transcribe=fake_transcribe))

    regions = [(i * 10.0, i * 10.0 + 5.0) for i in range(MAX_GAP_REPAIRS + 4)]
    MLXWhisperBackend(vad=True)._repair_gaps(wav, [], regions, "it")
    assert len(calls) == MAX_GAP_REPAIRS


def test_has_sound_keeps_long_segments_with_some_speech():
    from parola.asr.mlx_whisper_backend import has_sound
    from parola.schema import Segment, Word

    regions = [(10.0, 15.0)]
    long_seg = Segment(
        start=0,
        end=30,
        text="a b",
        words=[Word(text="a", start=11.0, end=11.5), Word(text="b", start=12.0, end=12.4)],
    )
    assert has_sound(long_seg, regions)  # little sound overall, but its words are on sound
    invented = Segment(
        start=0,
        end=8,
        text="Grazie a tutti.",
        words=[
            Word(text="Grazie", start=0.0, end=3.0),
            Word(text="a", start=3.0, end=5.0),
            Word(text="tutti.", start=5.0, end=8.0),
        ],
    )
    assert not has_sound(invented, regions)
    assert has_sound(
        Segment(start=9.8, end=9.9, text="x", words=[Word(text="x", start=9.75, end=9.8)]), regions
    )
