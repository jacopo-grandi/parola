"""The EN→IT test set of ToTTI (decision 0038, plan section T1): draft and import.

``draft`` pulls English sentences out of the recorded calls into a text file to translate by
hand, like ``parola annotate draft`` does for references. ``load`` turns the filled file into
the frozen test set (JSONL), refusing holes. The pairs come from the person's own recordings
(consent given) and live under ``data/private`` — never in git.

The sentences are picked round-robin across the calls, so no single call dominates; exact
duplicates (case and spacing folded) are dropped, very short lines too, and so are segments
where many words are still doubtful: the source of a test pair must be text someone stands
behind, not a guess of the model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from parola.schema import Transcript

MIN_WORDS = 4
# A sentence with more than this share of doubtful words is not trusted as a source.
MAX_DOUBTFUL = 0.2

HEADER = """\
# Test set EN→IT di ToTTI (decisione 0038, T1): riferimenti da scrivere a mano.
# Sotto ogni frase inglese scrivi la traduzione italiana sulla riga "IT:".
# Non cambiare le righe [n] ed "EN:"; ogni frase deve avere il suo "IT:" per l'import.
# Fonte: registrazioni proprie con consenso. Questo file resta in data/private, mai in git.
"""


class EvalSetError(RuntimeError):
    pass


@dataclass
class Pair:
    id: str
    call: str
    segment: int
    en: str
    it: str = ""


def _fold(text: str) -> str:
    return " ".join(text.lower().split())


def _english_sentences(folder: Path) -> dict[str, list[tuple[int, str]]]:
    """Per call, the (segment index, text) of the English sentences worth translating."""
    out: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith((".capture.json", ".asr.json")):
            continue
        try:
            t = Transcript.load(path)
        except Exception:
            continue  # not a transcript (e.g. a cache file)
        rows = []
        for i, seg in enumerate(t.segments):
            if (seg.lang or t.metadata.language_detected) != "en":
                continue
            text = seg.text.strip()
            if len(text.split()) < MIN_WORDS:
                continue
            if seg.words and sum(w.doubtful for w in seg.words) / len(seg.words) > MAX_DOUBTFUL:
                continue
            rows.append((i, text))
        if rows:
            out[path.stem] = rows
    return out


def pick(folder: Path, limit: int = 300) -> list[Pair]:
    """Up to ``limit`` sentences, round-robin across calls, without duplicates."""
    per_call = _english_sentences(folder)
    queues = {call: list(rows) for call, rows in sorted(per_call.items())}
    picked: list[Pair] = []
    seen: set[str] = set()
    while queues and len(picked) < limit:
        for call in sorted(queues):
            if len(picked) >= limit:
                break
            rows = queues[call]
            while rows:
                i, text = rows.pop(0)
                if _fold(text) in seen:
                    continue
                seen.add(_fold(text))
                picked.append(Pair(id=f"{len(picked) + 1:03d}", call=call, segment=i, en=text))
                break
            if not rows:
                del queues[call]
    return picked


def to_draft(pairs: list[Pair]) -> str:
    blocks = [HEADER]
    for p in pairs:
        blocks.append(f"[{p.id}] {p.call} · segmento {p.segment}\nEN: {p.en}\nIT: {p.it}")
    return "\n\n".join(blocks) + "\n"


def from_draft(text: str) -> list[Pair]:
    """The filled draft back as pairs; a missing or empty IT line is an error."""
    pairs: list[Pair] = []
    block: dict | None = None

    def close(block: dict | None) -> None:
        if block is None:
            return
        if not block.get("en"):
            raise EvalSetError(f"[{block['id']}]: EN line missing")
        if not block.get("it"):
            raise EvalSetError(f"[{block['id']}]: IT line missing or empty")
        pairs.append(Pair(**block))

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            close(block)
            head, _, rest = line.partition("]")
            call, _, seg = rest.partition("·")
            try:
                segment = int(seg.replace("segmento", "").strip())
            except ValueError as e:
                raise EvalSetError(f"{line!r}: not a '[id] call · segmento n' line") from e
            block = {"id": head.lstrip("["), "call": call.strip(), "segment": segment}
        elif line.startswith("EN:") and block is not None:
            block["en"] = line[3:].strip()
        elif line.startswith("IT:") and block is not None:
            block["it"] = line[3:].strip()
        elif block is not None and block.get("it") is not None:
            block["it"] = f"{block['it']} {line}".strip()  # a reference wrapped over two lines
        else:
            raise EvalSetError(f"unexpected line: {line!r}")
    close(block)
    if not pairs:
        raise EvalSetError("no pairs in the file")
    return pairs


def save(pairs: list[Pair], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p.__dict__, ensure_ascii=False) + "\n")
    return out


def load(path: Path) -> list[Pair]:
    return [Pair(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]  # fmt: skip
