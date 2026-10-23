"""ASR systems compared in the baseline (Phase 2 of the plan).

A system is a backend plus decoding choices: model, forced or detected language, initial
prompt. Variants of the PLAN: forced language vs automatic detection, with and without an
initial prompt rich in disfluencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from parola.asr import DEFAULT_MODEL, ASRBackend, MLXWhisperBackend
from parola.asr.mlx_whisper_backend import SEED

LARGE_V3 = "mlx-community/whisper-large-v3-mlx"

# Whisper imitates the style of the prompt: these show fillers, repetitions and a false
# start, written as docs/conventions.md wants them.
DISFLUENCY_PROMPTS = {
    "it": "Ehm, allora, eh, cioè... sì, sì, il, il progetto, ehm, lo fa- lo facciamo, eh, domani.",
    "en": "Um, so, uh, I mean... yeah, yeah, the, the project, um, we'll ha- we'll do it, uh, "
    "tomorrow.",
}


@dataclass(frozen=True)
class System:
    name: str
    description: str
    model: str = DEFAULT_MODEL
    force_language: bool = False
    prompt: bool = False
    kind: Literal["mlx-whisper", "parakeet", "crisper"] = "mlx-whisper"
    vad: bool = False
    gap_repair: bool = True
    glossary: bool = False
    glossary_carried: bool = True

    def backend(self, lang: str, seed: int = SEED) -> ASRBackend:
        # Optional backends are imported lazily: they need `uv sync --group baselines`.
        if self.kind == "parakeet":
            from parola.asr.parakeet_backend import ParakeetBackend

            return ParakeetBackend(self.model)
        if self.kind == "crisper":
            from parola.asr.crisper_backend import CrisperBackend

            return CrisperBackend(self.model, seed=seed)
        prompt = DISFLUENCY_PROMPTS.get(lang) if self.prompt else None
        glossary = None
        if self.glossary:
            from parola import glossary as g

            glossary = g.prompt_text(g.load())
        if glossary and not self.glossary_carried:
            prompt, glossary = glossary, None
        return MLXWhisperBackend(self.model, initial_prompt=prompt, seed=seed, vad=self.vad,
                                 glossary=glossary, gap_repair=self.gap_repair)  # fmt: skip

    def language(self, lang: str) -> str | None:
        """Language passed to the backend for a file whose manifest language is ``lang``."""
        return lang if self.force_language and lang in ("it", "en") else None


SYSTEMS = {
    s.name: s
    for s in [
        System("turbo", "large-v3-turbo, language detected"),
        System("turbo-lang", "large-v3-turbo, language forced", force_language=True),
        System("turbo-vad", "large-v3-turbo, language detected, only stretches with sound (VAD), "
               "skipped sound regions decoded again on their own clip (gap repair)", vad=True),
        System("turbo-vad-norepair", "large-v3-turbo, language detected, VAD without gap repair "
               "(comparison)", vad=True, gap_repair=False),
        System("turbo-glossary", "large-v3-turbo, personal glossary carried on every window",
               glossary=True),
        System("turbo-glossary-initial", "large-v3-turbo, personal glossary as initial prompt only",
               glossary=True, glossary_carried=False),
        System("turbo-prompt", "large-v3-turbo, language detected, disfluency prompt",
               prompt=True),
        System("turbo-lang-prompt", "large-v3-turbo, language forced, disfluency prompt",
               force_language=True, prompt=True),
        System("large-v3-lang", "large-v3, language forced", model=LARGE_V3,
               force_language=True),
        System("parakeet-v3", "Parakeet TDT 0.6B v3 (MLX), language detected",
               model="mlx-community/parakeet-tdt-0.6b-v3", kind="parakeet"),
        System("crisper-turbo-lang", "CrisperWhisper 2.0 turbo, verbatim mode, language forced "
               "(non-commercial weights)", model="nyralabs/CrisperWhisper2.0_turbo",
               force_language=True, kind="crisper"),
    ]
}  # fmt: skip
