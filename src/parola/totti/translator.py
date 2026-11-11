"""EN→IT translation for the live subtitles (ToTTI, decision 0038).

Helsinki-NLP/opus-mt-en-it converted to CTranslate2 (int8), run on the CPU so Whisper keeps
the GPU during a call. Licence of the weights, checked by hand on 2026-10-19: Apache-2.0 on
the Hugging Face model card, CC-BY 4.0 on the OPUS-MT repository; attribution kept either
way. The converted model lives under ``data/models`` (gitignored, never committed); the
conversion runs in a throwaway environment so torch and transformers stay out of this one.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_DIR = REPO_ROOT / "data" / "models" / "opus-mt-en-it-ct2"
# ToTTI v1, shipped on 2026-10-21 (results/2026-10-21-totti-v1-alpha-sweep.md): the v1
# checkpoint (distilled + Tatoeba + Europarl anchor) interpolated with the base at alpha
# 0.5. It missed the pre-registered 54.7 gate (tc-big zero-shot stays 0.4 ahead) and ships
# as a strict improvement on v0: +0.5 chrF (98% bootstrap), council recovered, FLORES 57.5
# within the guardrail, identical speed. V0 kept as fallback for a disk without v1.
STUDENT_DIR = REPO_ROOT / "data" / "models" / "opus-mt-en-it-totti-v1-ct2"
V0_DIR = REPO_ROOT / "data" / "models" / "opus-mt-en-it-totti-a30-ct2"


def default_model_dir() -> Path:
    """The newest accepted student on disk, otherwise the base Opus-MT."""
    for d in (STUDENT_DIR, V0_DIR):
        if (d / "model.bin").exists():
            return d
    return BASE_DIR


MODEL_DIR = default_model_dir()
# Small beam: ~40 ms per sentence on CPU. Decoding parameters only change after a translation
# test set exists (CLAUDE.md: measure first, then change).
BEAM_SIZE = 2


class Translator:
    """Opus-MT en-it on the CPU, loaded on first use."""

    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        self.model_dir = model_dir
        self._translator = None
        self._source = None
        self._target = None

    @property
    def name(self) -> str:
        """The engine's name, recorded with every translation it produces."""
        return self.model_dir.name

    def available(self) -> bool:
        return (self.model_dir / "model.bin").exists()

    def _load(self) -> None:
        import ctranslate2
        import sentencepiece

        self._translator = ctranslate2.Translator(
            str(self.model_dir), device="cpu", inter_threads=1, intra_threads=4
        )
        self._source = sentencepiece.SentencePieceProcessor(str(self.model_dir / "source.spm"))
        self._target = sentencepiece.SentencePieceProcessor(str(self.model_dir / "target.spm"))

    def translate(self, texts: list[str]) -> list[str]:
        """The Italian for each English text, in the same order."""
        if not texts:
            return []
        if self._translator is None:
            self._load()
        tokens = [[*self._source.encode(t, out_type=str), "</s>"] for t in texts]
        results = self._translator.translate_batch(tokens, beam_size=BEAM_SIZE)
        return [self._target.decode(r.hypotheses[0]) for r in results]


TEACHER_DIR = REPO_ROOT / "data" / "models" / "madlad400-3b-mt-ct2"


class TeacherTranslator:
    """google/madlad400-3b-mt (CT2 int8): the teacher, for whole-call batch translation only.

    Heavier and slower than the student (~3.5 GB of RAM while loaded, ~1.6 sentences/s), so
    it is loaded on first use and let go with ``unload()`` when the batch is done — the
    spirit of decision 0027: nothing stays in memory between jobs. Decision 0038 keeps it
    out of the live path; the batch view is outside that ban (extension of 2026-10-20).
    """

    name = "madlad400-3b-mt"

    def __init__(self, model_dir: Path = TEACHER_DIR) -> None:
        self.model_dir = model_dir
        self._translator = None
        self._source = None

    def available(self) -> bool:
        return (self.model_dir / "model.bin").exists()

    def _load(self) -> None:
        import ctranslate2
        import sentencepiece

        self._translator = ctranslate2.Translator(
            str(self.model_dir), device="cpu", inter_threads=1, intra_threads=8
        )
        self._source = sentencepiece.SentencePieceProcessor(str(self.model_dir / "spiece.model"))

    def translate(self, texts: list[str]) -> list[str]:
        """The Italian for each English text; MADLAD takes a ``<2it>`` target token."""
        if not texts:
            return []
        if self._translator is None:
            self._load()
        tokens = [[*self._source.encode(f"<2it> {t}", out_type=str), "</s>"] for t in texts]
        results = self._translator.translate_batch(tokens, beam_size=2, max_batch_size=32,
                                                   max_decoding_length=256)  # fmt: skip
        return [self._source.decode(r.hypotheses[0]) for r in results]

    def unload(self) -> None:
        """Free the ~3.5 GB: the next translate() loads again."""
        self._translator = None
        self._source = None
