"""Distillation data for ToTTI (decision 0038, plan section T3).

AMI and ICSI ship their transcripts as NXT XML (CC BY 4.0, verified in 0004 and in the
packages' own LICENCE.txt): one ``*.words.xml`` per meeting and speaker with the word tokens,
and one segments file whose children reference word-id ranges. This module turns them back
into sentences — keeping the verbatim feel, fillers included, because that is the domain the
student will translate — and builds the source list the teacher translates.

The train/dev split is by meeting series (the letterless prefix, ``ES2002a`` → ``ES2002``):
the same people speak through a whole series, so a series split keeps speakers disjoint
without chasing the participant metadata.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

NITE = "{http://nite.sourceforge.net/}"
# The same bounds as the hand-made test set, plus a cap against runaway segments.
MIN_WORDS = 4
MAX_WORDS = 60
_HREF = re.compile(r"#id\(([^)]+)\)(?:\.\.id\(([^)]+)\))?$")


@dataclass
class Sentence:
    corpus: str
    meeting: str
    speaker: str
    text: str

    @property
    def series(self) -> str:
        return re.sub(r"[a-z]$", "", self.meeting)


def _words(path: Path) -> tuple[dict[str, int], list[tuple[str, bool]]]:
    """Document-order word list of a ``*.words.xml``: id → position, and (text, is punctuation)."""
    order: dict[str, int] = {}
    tokens: list[tuple[str, bool]] = []
    for el in ET.parse(path).getroot():
        if el.tag != "w" or el.text is None:
            continue  # vocalsound, nonvocalsound, comment, pauses: not words
        order[el.get(f"{NITE}id", "")] = len(tokens)
        tokens.append((el.text.strip(), el.get("punc") == "true"))
    return order, tokens


def _join(tokens: list[tuple[str, bool]]) -> str:
    out: list[str] = []
    for text, punc in tokens:
        if not text:
            continue
        if punc and out:
            out[-1] += text
        else:
            out.append(text)
    return " ".join(out)


def segment_texts(words_xml: Path, segments_xml: Path) -> list[str]:
    """The text of each segment, words joined and punctuation attached."""
    order, tokens = _words(words_xml)
    texts = []
    for seg in ET.parse(segments_xml).getroot():
        parts: list[tuple[str, bool]] = []
        for child in seg:
            if m := _HREF.search(child.get("href", "")):
                first, last = m.group(1), m.group(2) or m.group(1)
                if first in order and last in order:
                    parts.extend(tokens[order[first] : order[last] + 1])
        if text := _join(parts):
            texts.append(text)
    return texts


def _pairs(
    words_dir: Path, segs_dir: Path, segs_suffix: str
) -> Iterator[tuple[str, str, Path, Path]]:
    for words_xml in sorted(words_dir.glob("*.words.xml")):
        meeting, speaker = words_xml.name.split(".")[:2]
        segments_xml = segs_dir / f"{meeting}.{speaker}{segs_suffix}"
        if segments_xml.exists():
            yield meeting, speaker, words_xml, segments_xml


def ami_sentences(root: Path) -> Iterator[Sentence]:
    for meeting, speaker, w, s in _pairs(root / "words", root / "segments", ".segments.xml"):
        for text in segment_texts(w, s):
            yield Sentence("ami", meeting, speaker, text)


def icsi_sentences(root: Path) -> Iterator[Sentence]:
    icsi = root / "ICSI"
    for meeting, speaker, w, s in _pairs(icsi / "Words", icsi / "Segments", ".segs.xml"):
        for text in segment_texts(w, s):
            yield Sentence("icsi", meeting, speaker, text)


def usable(sentences: Iterator[Sentence]) -> Iterator[Sentence]:
    """Length-filtered and deduplicated, first occurrence wins."""
    seen: set[str] = set()
    for s in sentences:
        n = len(s.text.split())
        if not MIN_WORDS <= n <= MAX_WORDS:
            continue
        key = " ".join(s.text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        yield s


def save_sources(sentences: list[Sentence], out: Path, dev_series: int = 2) -> dict:
    """Write the source list with a series-level train/dev split; returns the stats."""
    series = sorted({s.series for s in sentences})
    # The last series of each corpus go to dev: arbitrary but stable.
    dev = set()
    for corpus in ("ami", "icsi"):
        own = [x for x in series if any(s.corpus == corpus and s.series == x for s in sentences)]
        dev.update(own[-dev_series:])
    out.parent.mkdir(parents=True, exist_ok=True)
    stats = {"total": len(sentences), "train": 0, "dev": 0, "series": len(series)}
    with out.open("w", encoding="utf-8") as f:
        for i, s in enumerate(sentences):
            split = "dev" if s.series in dev else "train"
            stats[split] += 1
            f.write(json.dumps({"id": f"{s.corpus}-{i:06d}", "corpus": s.corpus,
                                "meeting": s.meeting, "speaker": s.speaker, "split": split,
                                "en": s.text}, ensure_ascii=False) + "\n")  # fmt: skip
    return stats


def translate_sources(sources: Path, out: Path, translate, batch: int = 64,
                      progress=None) -> int:  # fmt: skip
    """The teacher pass, resumable: ids already in ``out`` are kept, the rest appended.

    ``translate`` maps a list of English texts to their Italian; the file is flushed after
    every batch, so the job can be stopped and started again without losing or repeating
    work (a correction of the sources changes the ids, which invalidates nothing done).
    """
    done: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["id"])
    rows = [json.loads(x) for x in sources.read_text(encoding="utf-8").splitlines() if x.strip()]
    todo = [r for r in rows if r["id"] not in done]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for at in range(0, len(todo), batch):
            chunk = todo[at : at + batch]
            for row, it in zip(chunk, translate([r["en"] for r in chunk]), strict=True):
                record = {"id": row["id"], "split": row["split"], "en": row["en"], "it": it}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            if progress is not None:
                progress(min(at + batch, len(todo)), len(todo))
    return len(todo)


# A translation shorter or longer than this, in words over the source's, is distrusted:
# EN→IT grows a little; far outside the band the teacher copied, dropped or invented.
RATIO_LOW, RATIO_HIGH = 0.4, 1.6


def clean(pairs: Iterator[dict]) -> tuple[list[dict], Counter]:
    """The distilled pairs fit for training, and the counts of what was dropped and why.

    The teacher leaks HTML entities (&apos; for every Italian apostrophe, &quot; around
    quotes — caught by Jacopo's sample review, 2026-10-19): those are good translations with
    an encoding artifact, so they are unescaped, not dropped ("unescaped" counts them).
    Three declared drop filters: an empty translation, a copy of the source left
    untranslated, and a length ratio outside [RATIO_LOW, RATIO_HIGH]. No language detector:
    the copy and ratio filters catch what one would, without a new dependency.
    """
    import html

    kept: list[dict] = []
    dropped: Counter = Counter()
    for p in pairs:
        en, it = p["en"].strip(), p["it"].strip()
        if re.search(r"&(?:#\d+|[a-z]+);", it):
            it = html.unescape(it)
            p = {**p, "it": it}
            dropped["unescaped"] += 1  # kept, only counted
        words = len(en.split())
        if not it:
            dropped["empty"] += 1
        elif " ".join(it.lower().split()) == " ".join(en.lower().split()):
            dropped["copy"] += 1
        elif not words or not RATIO_LOW <= len(it.split()) / words <= RATIO_HIGH:
            dropped["length_ratio"] += 1
        else:
            kept.append(p)
    return kept, dropped
