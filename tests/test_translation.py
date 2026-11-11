"""Translation metrics and the EN→IT test set tooling (ToTTI, decision 0038, T1)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from parola.eval import translation
from parola.schema import Metadata, Segment, Transcript, Word
from parola.totti import evalset
from parola.totti.evalset import EvalSetError, Pair

# ---------------------------------------------------------------- metrics


def test_chrf_is_100_on_identical_text():
    refs = ["Buongiorno a tutti.", "I pomodori vanno annaffiati tra cinque minuti."]
    assert translation.chrf(refs, refs) == pytest.approx(100.0)


def test_chrf_ranks_the_closer_hypothesis_higher():
    refs = ["I pomodori vanno annaffiati tra cinque minuti."]
    close = translation.chrf(["I pomodori vanno bagnati tra cinque minuti."], refs)
    far = translation.chrf(["Il gatto dorme sul divano."], refs)
    assert close > far > 0


def test_bleu_is_100_on_identical_text():
    refs = ["i pomodori vanno annaffiati tra cinque minuti adesso"]
    assert translation.bleu(refs, refs) == pytest.approx(100.0)


def test_copies_spot_untranslated_sources():
    sources = ["Good morning everyone.", "See you tomorrow."]
    hyps = ["good  morning everyone.", "Ci vediamo domani."]
    assert translation.copies(sources, hyps) == [0]


def test_inflated_spots_added_content():
    sources = ["Thanks for the seeds.", "The feature is ready."]
    hyps = ["Grazie per questi bellissimi semi antichi che pianteremo nell'orto oggi pomeriggio.",
            "La funzione è pronta."]  # fmt: skip
    assert translation.inflated(sources, hyps) == [0]


def test_score_requires_aligned_non_empty_lists():
    with pytest.raises(ValueError):
        translation.score(["a"], ["b"], [])
    with pytest.raises(ValueError):
        translation.score([], [], [])
    s = translation.score(["Good morning."], ["Buongiorno."], ["Buongiorno."])
    assert s.pairs == 1 and s.chrf == pytest.approx(100.0) and s.copies == 0


# ---------------------------------------------------------------- the test set tooling


def call(folder: Path, name: str, texts: list[str], lang: str = "en",
         doubtful: list[int] | None = None) -> None:  # fmt: skip
    """A transcript whose segment ``i`` has ``texts[i]``; ``doubtful`` marks whole segments."""
    segments = []
    for i, text in enumerate(texts):
        words = [Word(text=w, start=i + k * 0.1, end=i + k * 0.1 + 0.05,
                      doubtful=i in (doubtful or []))
                 for k, w in enumerate(text.split())]  # fmt: skip
        segments.append(Segment(start=i, end=i + 1, text=text, lang=lang, words=words))
    meta = Metadata(audio_file=f"{name}.system.wav", duration=float(len(texts)),
                    created_at=datetime(2026, 10, 19, tzinfo=UTC), parola_version="0")  # fmt: skip
    Transcript(metadata=meta, segments=segments).save(folder / f"{name}.json")


def test_pick_round_robins_dedups_and_filters(tmp_path: Path):
    call(tmp_path, "a", ["one two three four five", "too short", "one two three four five",
                         "shared sentence across calls here"])  # fmt: skip
    call(tmp_path, "b", ["shared sentence across calls here", "another good long sentence here"])
    call(tmp_path, "c", ["frase italiana lunga a sufficienza"], lang="it")
    call(tmp_path, "d", ["doubtful words everywhere in here"], doubtful=[0])
    pairs = evalset.pick(tmp_path, limit=10)
    texts = [p.en for p in pairs]
    # One copy of the shared sentence, no short line, no Italian, no doubtful segment.
    assert sorted(texts) == ["another good long sentence here", "one two three four five",
                             "shared sentence across calls here"]  # fmt: skip
    # Round-robin: the first pick of each call comes before the seconds.
    assert [p.call for p in pairs] == ["a", "b", "b"] or [p.call for p in pairs] == ["a", "b", "a"]
    assert [p.id for p in pairs] == ["001", "002", "003"]


def test_pick_respects_the_limit(tmp_path: Path):
    call(tmp_path, "a", [f"sentence number {i} is long enough" for i in range(10)])
    assert len(evalset.pick(tmp_path, limit=4)) == 4


def test_draft_roundtrip(tmp_path: Path):
    pairs = [Pair(id="001", call="a", segment=0, en="Good morning everyone today."),
             Pair(id="002", call="b", segment=3, en="The feature is ready now.")]  # fmt: skip
    text = evalset.to_draft(pairs)
    filled = text.replace("IT: \n", "IT: riga uno\n", 1).replace("IT: ", "IT: riga due ", 1)
    # The draft ends with "IT: " on the last block too: fill both.
    filled = "\n".join(line if not line.rstrip().endswith("IT:") else f"{line}riga"
                       for line in filled.splitlines())  # fmt: skip
    got = evalset.from_draft(filled)
    assert [p.en for p in got] == [p.en for p in pairs]
    assert all(p.it for p in got)
    out = evalset.save(got, tmp_path / "set.jsonl")
    assert [p.en for p in evalset.load(out)] == [p.en for p in pairs]


def test_from_draft_refuses_an_empty_it_line():
    text = "[001] a · segmento 0\nEN: Good morning everyone today.\nIT: \n"
    with pytest.raises(EvalSetError, match="IT line"):
        evalset.from_draft(text)


def test_from_draft_joins_a_wrapped_reference():
    text = (
        "[001] a · segmento 0\nEN: Good morning everyone today.\n"
        "IT: buongiorno a tutti\n   quanti oggi.\n"
    )
    (pair,) = evalset.from_draft(text)
    assert pair.it == "buongiorno a tutti quanti oggi."
