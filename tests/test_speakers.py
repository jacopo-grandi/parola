"""Voices on a channel: splitting segments where the voice changes, names, edits."""

from datetime import UTC, datetime

import numpy as np

from parola import editing
from parola.diarize import spectral_clusters
from parola.schema import Metadata, Segment, Transcript, Word
from parola.speakers import assign, label, name


def call(channel="system") -> Transcript:
    # "ciao a tutti" 0-1.2 s, "buongiorno sono Marco" 1.4-3.0 s, one Whisper segment.
    words = [Word(text=t, start=s, end=s + 0.3) for t, s in
             [("ciao", 0.0), ("a", 0.4), ("tutti", 0.8), ("buongiorno", 1.4), ("sono", 2.0),
              ("Marco", 2.6)]]  # fmt: skip
    seg = Segment(start=0, end=3, text=" ".join(w.text for w in words), channel=channel,
                  words=words, lang="it")  # fmt: skip
    mic = Segment(
        start=3.2, end=4, text="ok", channel="mic", words=[Word(text="ok", start=3.2, end=3.5)]
    )
    meta = Metadata(audio_file="x", duration=4, created_at=datetime(2026, 10, 16, tzinfo=UTC),
                    parola_version="0")  # fmt: skip
    return Transcript(metadata=meta, segments=[seg, mic])


def test_a_segment_is_split_where_the_voice_changes():
    t = assign(call(), "system", [(0.0, 1.3, 0), (1.3, 3.0, 1)])
    sys_segs = [s for s in t.segments if s.channel == "system"]
    assert [(s.text, s.speaker) for s in sys_segs] == [
        ("ciao a tutti", "system:1"), ("buongiorno sono Marco", "system:2")]  # fmt: skip
    assert sys_segs[0].start == 0 and sys_segs[1].end == 3
    assert t.segments[-1].speaker is None  # the microphone is "Io", no voice id


def test_a_word_between_turns_takes_the_nearest():
    t = assign(call(), "system", [(0.0, 0.5, 0), (1.5, 3.0, 1)])  # "tutti" (0.8-1.1) in a gap
    assert [s.speaker for s in t.segments if s.channel == "system"] == ["system:1", "system:2"]


def test_split_segments_stay_sorted_with_the_other_channel():
    # A mic segment inside the span of the split system segment (a real call, 2026-10-23):
    # the pieces of the split must not push it out of start order, or the transcript
    # fails validation on the next load and the call disappears from the app.
    t = call()
    mic = Segment(start=0.5, end=0.9, text="ok", channel="mic",
                  words=[Word(text="ok", start=0.5, end=0.8)])  # fmt: skip
    t = t.model_copy(update={"segments": [t.segments[0], mic]})
    t = assign(t, "system", [(0.0, 1.3, 0), (1.3, 3.0, 1)])
    starts = [s.start for s in t.segments]
    assert starts == sorted(starts)
    assert [(s.channel, s.start) for s in t.segments] == [
        ("system", 0), ("mic", 0.5), ("system", 1.4)]  # fmt: skip
    Transcript.model_validate(t.model_dump())  # the saved file must load again


def test_no_turns_leaves_the_call_as_it_was():
    assert assign(call(), "system", []).segments == call().segments


def test_names_and_labels():
    t = assign(call("mixed"), "mixed", [(0, 3, 0)])
    seg = t.segments[0]
    assert name(t, "mixed:1") == "Voce"  # the only voice of its channel (decision 0058)
    two = assign(call("mixed"), "mixed", [(0.0, 1.3, 0), (1.3, 3.0, 1)])
    assert name(two, "mixed:1") == "Voce 1" and name(two, "mixed:2") == "Voce 2"
    t.metadata.speakers["mixed:1"] = "Giulia"
    assert label(t, seg, {"mixed": ""}) == "Giulia"
    assert label(t, t.segments[-1], {"mic": "Io"}) == "Io"


def test_changing_a_voice_can_be_undone():
    t = assign(call(), "system", [(0.0, 1.3, 0), (1.3, 3.0, 1)])
    e = editing.set_speaker(t, 1, "system:1")
    assert (e.kind, e.previous, e.text) == ("speaker", "system:2", "system:1")
    assert t.segments[1].speaker == "system:1"
    editing.undo(t)
    assert t.segments[1].speaker == "system:2" and not t.edits


def test_spectral_clusters_finds_how_many_voices():
    rng = np.random.default_rng(0)
    centers = rng.normal(size=(3, 32))
    X = np.vstack([c + 0.15 * rng.normal(size=(40, 32)) for c in centers])
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    labels = spectral_clusters(X)
    assert len(set(labels)) == 3
    assert all(len(set(labels[i * 40 : (i + 1) * 40])) == 1 for i in range(3))


def test_a_call_with_two_people_has_one_other_voice():
    from parola.speakers import single_voice

    t = single_voice(call(), "system")
    voices = {s.speaker for s in t.segments if s.channel == "system"}
    assert voices == {"system:1"} and all(s.speaker is None for s in t.segments
                                          if s.channel != "system")  # fmt: skip
    assert name(t, "system:1") == "Altri"
    t.metadata.speakers["system:1"] = "Marta"
    assert name(t, "system:1") == "Marta"
