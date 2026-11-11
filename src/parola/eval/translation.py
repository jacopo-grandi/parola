"""Translation metrics for ToTTI (decision 0038, plan section T1).

chrF is the main metric: character level, so Italian morphology (pianificheremo vs
pianifichiamo) costs a little, not a whole word. BLEU is reported alongside because every MT
paper speaks it. Both come from sacrebleu at corpus level, deterministic. Next to the scores
go two declared counts the metrics cannot see — subtitles must not invent:

- **copies**: hypotheses that are the source left untranslated (a common MT failure);
- **inflated**: hypotheses long enough, next to their source, to suggest added content.

The counts are heuristics for the report, not quality scores: a long Italian line can be a
fine translation, and the report says how often it happens, not that it is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

# Hypothesis tokens over source tokens beyond this suggest content was added. EN→IT normally
# grows a little (articles, clitics); 1.6 is well past that.
LENGTH_RATIO = 1.6


def chrf(hypotheses: list[str], references: list[str]) -> float:
    """Corpus chrF, 0..100."""
    import sacrebleu

    return sacrebleu.corpus_chrf(hypotheses, [references]).score


def bleu(hypotheses: list[str], references: list[str]) -> float:
    """Corpus BLEU, 0..100."""
    import sacrebleu

    return sacrebleu.corpus_bleu(hypotheses, [references]).score


def _fold(text: str) -> str:
    return " ".join(text.lower().split())


def copies(sources: list[str], hypotheses: list[str]) -> list[int]:
    """Indices of the hypotheses that are the source, untranslated."""
    return [i for i, (s, h) in enumerate(zip(sources, hypotheses, strict=True))
            if _fold(s) == _fold(h)]  # fmt: skip


def inflated(sources: list[str], hypotheses: list[str],
             ratio: float = LENGTH_RATIO) -> list[int]:  # fmt: skip
    """Indices of the hypotheses whose length suggests added content."""
    out = []
    for i, (s, h) in enumerate(zip(sources, hypotheses, strict=True)):
        if len(s.split()) and len(h.split()) / len(s.split()) > ratio:
            out.append(i)
    return out


@dataclass
class TranslationScores:
    pairs: int
    chrf: float
    bleu: float
    copies: int
    inflated: int


def score(sources: list[str], hypotheses: list[str], references: list[str]) -> TranslationScores:
    """Every number of the report for one system on one test set."""
    if not (len(sources) == len(hypotheses) == len(references)):
        raise ValueError("sources, hypotheses and references must be aligned")
    if not sources:
        raise ValueError("empty test set")
    return TranslationScores(
        pairs=len(sources),
        chrf=chrf(hypotheses, references),
        bleu=bleu(hypotheses, references),
        copies=len(copies(sources, hypotheses)),
        inflated=len(inflated(sources, hypotheses)),
    )
