"""Key phrases and suggested titles, on hand-built calls."""

from datetime import UTC, datetime
from itertools import product

import pytest

from parola.keywords import _nlp, key_phrases, suggest_title
from parola.schema import Metadata, Segment, Transcript, Word

# The Italian tagger is an optional, non-commercial install (pyproject group "titles"):
# without it the grammar filter stays out of the way and its tests make no sense.
needs_it_tagger = pytest.mark.skipif(_nlp("it") is None, reason="it_core_news_sm not installed")


def call(*lines: str, doubtful: set[str] = frozenset(), lang: str = "it") -> Transcript:
    """One segment per line; words in ``doubtful`` are flagged as doubtful."""
    segments = []
    t = 0.0
    for line in lines:
        words = []
        for tok in line.split():
            words.append(
                Word(text=tok, start=t, end=t + 0.3, doubtful=tok.strip(".,?") in doubtful)
            )
            t += 0.4
        segments.append(Segment(start=words[0].start, end=words[-1].end, text=line, lang=lang,
                                words=words))  # fmt: skip
    meta = Metadata(audio_file="x.wav", duration=t, created_at=datetime(2026, 10, 16, tzinfo=UTC),
                    parola_version="0")  # fmt: skip
    return Transcript(metadata=meta, segments=segments)


# Distinct content words, each said once: enough words for a title, none of them a key phrase.
FILLER = " ".join("k" + a + b for a, b in product("bcdfgjlmnpqrstvz", repeat=2))


def texts(t: Transcript, limit: int = 10) -> list[str]:
    return [p.text for p in key_phrases(t, limit)]


def test_stop_words_and_fillers_cut_phrases():
    t = call("allora eh il vaso rialzato è pronto", "quindi ehm il vaso rialzato di ottobre")
    assert texts(t)[0] == "vaso rialzato"
    assert not {"allora", "eh", "ehm", "quindi", "il", "è"} & {
        w for p in texts(t) for w in p.split()
    }


def test_punctuation_breaks_a_phrase():
    t = call("abbiamo il mulching. Il raccolto tardivo", "il mulching, il raccolto tardivo")
    assert "mulching raccolto tardivo" not in texts(t)
    assert {"mulching", "raccolto tardivo"} <= set(texts(t))


def test_repeated_phrase_beats_a_long_one_said_once():
    t = call("il baking soda", "il baking soda della domenica", "il baking soda",
             "una grande idea strana")  # fmt: skip
    assert texts(t)[0] == "baking soda"


def test_elided_article_is_dropped():
    t = call("l'impatto sul concime", "dell'impatto sul concime")
    assert "impatto" in " ".join(texts(t)) and "l'impatto" not in texts(t)


def test_verbs_and_generic_nouns_do_not_name_a_call():
    t = call("stiamo spingendo il concime", "facciamo una cosa sul concime", "per esempio")
    words = {w for p in texts(t) for w in p.split()}
    assert "concime" in words and not {"stiamo", "spingendo", "facciamo", "cosa", "esempio"} & words


def test_a_doubtful_word_said_once_is_left_out_but_a_repeated_one_counts():
    once = call("parliamo di Ortolab oggi", "il concime", "il concime", doubtful={"Ortolab"})
    assert "Ortolab" not in " ".join(texts(once))
    lines = ["il mulching sale"] * 3
    repeated = call(*lines, doubtful={"mulching"})
    assert "mulching" in " ".join(texts(repeated))


def test_acronyms_and_names_keep_their_capitals():
    t = call("i LED di Giulia", "i LED di Giulia", "il mulching")
    assert {"LED", "Giulia"} <= set(texts(t)) or "LED" in texts(t)[0]
    assert all(p == p.lower() for p in texts(t) if p.startswith("mulching"))


def test_title_is_none_for_a_short_call():
    assert suggest_title(call("prova prova uno due tre", "prova")) is None


def test_title_from_the_top_phrases():
    filler = FILLER
    t = call(
        "il mulching naturale.", "il mulching naturale.", "il mulching naturale.",
        "i semi antichi.", "i semi antichi.", "il meteo.", "il meteo.", filler,
    )  # fmt: skip
    assert suggest_title(t) == "Mulching naturale e semi antichi"


def test_title_stays_short():
    long = "la piattaforma integrazione sistemi"
    filler = FILLER
    lines = [*[f"{long}."] * 3, *["l'architettura distribuita."] * 3,
             *["la roadmap trimestrale."] * 3, filler]  # fmt: skip
    t = call(*lines)
    title = suggest_title(t)
    assert title is not None and len(title) <= 60


def test_di_and_of_can_join_a_phrase():
    t = call("la raccolta delle olive.", "sulla raccolta delle olive.", "the number of seeds.",
             "number of seeds.")  # fmt: skip
    assert {"raccolta delle olive", "number of seeds"} <= set(texts(t))


def test_adverbs_do_not_name_a_call():
    t = call("semplicemente il concime.", "basically il concime.", "semplicemente")
    assert texts(t) == ["concime"]


def test_singular_and_plural_count_together():
    t = call("il pomodoro.", "i pomodori.", "i pomodori.", "un vaso.", "il glossario.")
    assert key_phrases(t, 1)[0].count == 3  # pomodoro + pomodori


def test_a_phrase_inside_a_longer_one_still_counts():
    t = call("il vaso rialzato.", "il vaso rialzato di ottobre.")
    assert key_phrases(t, 1)[0].text == "vaso rialzato"
    assert key_phrases(t, 1)[0].count == 2


def test_a_specific_phrase_said_twice_beats_its_head_word():
    t = call(*["la pizza."] * 5, "la pizza margherita.", "la pizza margherita.")
    assert texts(t)[0] == "pizza margherita"


def test_acronyms_written_in_capitals_once_stay_in_capitals():
    t = call("il dop.", "il DOP.", "il dop.")
    assert texts(t) == ["DOP"]


def test_title_coordinates_phrases_with_the_same_head():
    t = call(*["i peperoni rossi."] * 3, *["i peperoni gialli."] * 2, *["il meteo."] * 2, FILLER)
    assert suggest_title(t) == "Peperoni rossi e gialli"


def test_english_title_uses_and():
    lines = [*["the ripest tomato."] * 3, *["the variety of seeds."] * 2, FILLER]
    assert suggest_title(call(*lines, lang="en")) == "Ripest tomato and variety of seeds"


def test_only_noun_phrases_name_a_call():
    # "esatto", "ciao" never follow an article: they are discourse markers, not topics.
    lines = ["esatto, il concime.", "esatto.", "ciao ciao", "esatto il concime.", "ciao"]
    assert texts(call(*lines)) == ["concime"]


def test_names_count_without_an_article():
    t = call("siamo andati in Portogallo.", "in Portogallo il concime.", "il concime.")
    assert "Portogallo" in texts(t)


def test_very_frequent_words_break_phrases():
    t = call("the ripest tomato because.", "the ripest tomato because.")
    assert texts(t) == ["ripest tomato"]


def test_a_hallucinated_thank_you_does_not_name_a_call():
    # Whisper writes "Thank you." on quiet stretches, one sentence per stretch: the capital
    # on "Thank" is forced by the sentence, not the mark of a name.
    lines = [*["the ripest tomato."] * 3, *["the variety of seeds."] * 2,
             *["Thank you."] * 10, *["Thanks for watching."] * 4, FILLER]  # fmt: skip
    t = call(*lines, lang="en")
    words = {w.lower() for p in texts(t) for w in p.split()}
    assert not {"thank", "thanks", "watching"} & words
    assert suggest_title(t) == "Ripest tomato and variety of seeds"


def test_spoken_hedges_do_not_name_an_english_call():
    # "kind", "things", "look": written frequency between Zipf 5.4 and 6, said constantly in
    # meetings. Repeated far more often than the topic, they must still lose.
    lines = [*["the kind of things, look."] * 10, *["the vulnerability tier."] * 3, FILLER]
    t = call(*lines, lang="en")
    words = {w.lower() for p in texts(t) for w in p.split()}
    assert not {"kind", "things", "look"} & words
    assert suggest_title(t) == "Vulnerability tier"


def test_spoken_hedges_do_not_name_an_italian_call():
    # "lavoro", "momento": the same band in Italian.
    lines = [*["il lavoro, il momento."] * 10, *["il concime biologico."] * 3, FILLER]
    t = call(*lines)
    words = {w.lower() for p in texts(t) for w in p.split()}
    assert not {"lavoro", "momento"} & words
    assert suggest_title(t) == "Concime biologico"


def test_a_capitalized_word_at_the_start_of_a_sentence_is_not_a_name():
    # "Concime" opens every sentence it is in: no article and no free capital, not a topic.
    t = call("Concime dappertutto.", "Concime dappertutto.", "il basilico.", "il basilico.")
    assert "concime" not in " ".join(texts(t)).lower()


def test_a_name_at_the_start_of_a_sentence_keeps_its_capital():
    # One "Giulia" away from the start of a sentence is enough to show it is a name.
    t = call("Giulia pota.", "la lavanda di Giulia.", "la lavanda.")
    assert "Giulia" in " ".join(texts(t))


@needs_it_tagger
def test_meeting_verbs_do_not_name_a_call():
    """ "ti mando il documento": the sending is how the meeting runs, not its subject."""
    t = call("ti mando il documento firmato", "ti mando il documento richiesto",
             "domani ti mando il documento")  # fmt: skip
    assert all("mando" not in p for p in texts(t))
    assert any("documento" in p for p in texts(t))


@needs_it_tagger
def test_a_persons_name_alone_does_not_name_a_call():
    """ "Maria" is no topic; "progetto di Maria" is one, the person only specifies it."""
    t = call("il progetto di Maria procede", "Maria ha chiuso il progetto",
             "parliamo del progetto di Maria")  # fmt: skip
    got = texts(t)
    assert "Maria" not in got
    assert "progetto di Maria" in got


@needs_it_tagger
def test_a_place_is_not_a_person():
    t = call("il viaggio in Etiopia comincia", "la carestia in Etiopia",
             "la storia della Etiopia")  # fmt: skip
    assert any("Etiopia" in p for p in texts(t))


def test_a_bare_adjective_rides_its_noun_phrase_or_vanishes():
    """A quality with nothing it qualifies ("the legal one", "la prossima volta") is no title
    on its own: at most it carries its noun phrase up (the real en-002 case)."""
    t = call("the legal one matters", "the legal one wins", "the legal reasoning convinces",
             "the legal reasoning holds", lang="en")  # fmt: skip
    got = texts(t)
    assert "legal" not in got
    assert any("legal reasoning" in p for p in got)


@needs_it_tagger
def test_a_bare_temporal_adjective_never_names_an_italian_call():
    t = call("la prossima volta decidiamo il budget", "la prossima volta vediamo il budget",
             "la prossima volta chiudiamo il budget")  # fmt: skip
    assert "prossima" not in texts(t)
    assert any("budget" in p for p in texts(t))


@needs_it_tagger
def test_un_attimo_is_flow_not_subject():
    t = call("aspetta un attimo il documento", "un attimo e il documento arriva",
             "un attimo ancora per il documento")  # fmt: skip
    assert all("attimo" not in p for p in texts(t))


def test_without_spacy_models_phrases_still_work(monkeypatch):
    """A machine without the taggers falls back to the frequency filters alone."""
    import parola.keywords as K

    monkeypatch.setattr(K, "_nlp", lambda lang: None)
    t = call("allora il vaso rialzato è pronto", "quindi il vaso rialzato di ottobre")
    assert texts(t)[0] == "vaso rialzato"


def test_a_hallucinated_loop_never_names_a_call():
    """The real call of 2026-11-02: Whisper looped "Ciao" eleven times on the goodbyes, and
    the mid-sentence capitals passed for a name. Consecutive repeats count as one saying."""
    t = call("Grappolo Grappolo Grappolo Grappolo Grappolo Grappolo Grappolo",
             "il vaso rialzato è pronto", "il vaso rialzato di ottobre")  # fmt: skip
    assert all("grappolo" not in p.lower() for p in texts(t))


def test_greetings_never_name_a_call():
    t = call("ciao a tutti e benvenuti", "ciao ciao ci sentiamo dopo il meeting",
             "Ciao Ciao Ciao Ciao Ciao")  # fmt: skip
    assert all("ciao" not in p.lower() for p in texts(t))
