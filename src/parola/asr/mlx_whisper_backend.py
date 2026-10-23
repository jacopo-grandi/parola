"""mlx-whisper backend (Apple Silicon)."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from parola.asr.base import ASRResult
from parola.schema import Segment, Word
from parola.schema.vocab import LANGS

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
SEED = 0
# With VAD, a segment is dropped only when none of its words falls on sound: text written over
# silence is invented ("Grazie a tutti."). Judging by the share of the segment with sound was
# measured and rejected: long segments with little speech were dropped with real words in them
# (results/2026-10-15-vad.md). Tolerance around the regions, for imprecise word timestamps:
WORD_TOLERANCE_SECONDS = 0.3
# Gap repair (vad=True only): Whisper's long-form decode sometimes jumps a whole window of
# clean speech (seen on a real call, results/2026-10-23-gap-repair.md). After the full decode,
# sound regions at least this long with no transcribed word on them are decoded again, each on
# its own clip; the full decode is untouched, so the other windows cannot move (moving them is
# what sank the per-region decode, results/2026-10-15-vad.md).
GAP_MIN_SECONDS = 3.0
# The clip starts and ends this much outside the region, so edge words are heard whole.
GAP_MARGIN_SECONDS = 1.5
# Ceiling on clips per file: a pathological file must not multiply the decoding time.
MAX_GAP_REPAIRS = 8


def convert_result(result: dict[str, Any]) -> ASRResult:
    """Convert the dict returned by ``mlx_whisper.transcribe`` into an ``ASRResult``."""
    language = result.get("language")
    # The schema only knows it/en; any other detected language stays in the metadata.
    segment_lang = language if language in LANGS else None
    segments: list[Segment] = []
    for seg in result.get("segments", []):
        words = _merge_words(seg.get("words", []))
        # Segments whose words could not be aligned sometimes keep timestamps that go back
        # before the previous segment. Keep the text order and move the start forward.
        start = max(seg["start"], segments[-1].start) if segments else seg["start"]
        segments.append(
            Segment(
                text=seg["text"].strip(),
                start=start,
                end=max(seg["end"], start),
                lang=segment_lang,
                words=words,
            )
        )
    return ASRResult(segments=segments, language=language)


def _merge_words(raw_words: list[dict[str, Any]]) -> list[Word]:
    """Build words, joining tokens without a leading space to the previous word.

    Whisper splits elisions into separate tokens (" d", "'accordo"); in the transcript they
    are one whitespace-delimited word ("d'accordo"), as in the segment text.
    """
    words: list[Word] = []
    for w in raw_words:
        text = w["word"].strip()
        if not text:
            continue
        start = w["start"]
        # Whisper occasionally emits end < start by a few ms; clamp it.
        end = max(w["end"], start)
        probability = w.get("probability")
        if words and not w["word"][:1].isspace():
            prev = words[-1]
            probs = [p for p in (prev.probability, probability) if p is not None]
            words[-1] = Word(
                text=prev.text + text,
                start=prev.start,
                end=max(end, prev.end or end),
                probability=min(probs) if probs else None,
            )
        else:
            words.append(Word(text=text, start=start, end=end, probability=probability))
    return words


def restore_times(segments: list[Segment], cuts) -> list[Segment]:
    """Segments decoded on audio with shortened silences, on the original timeline."""

    def back(t: float | None) -> float | None:
        return None if t is None else cuts.original(t)

    out = []
    for seg in segments:
        words = [w.model_copy(update={"start": back(w.start), "end": back(w.end)})
                 for w in seg.words]  # fmt: skip
        timed = [w for w in words if w.start is not None and w.end is not None]
        # A segment can begin at the edge of a cut: its words carry the real times.
        start = timed[0].start if timed else back(seg.start)
        end = timed[-1].end if timed else back(seg.end)
        if out:
            # Word times jitter a few ms across segment boundaries, so a start taken from the
            # first word can fall before the previous segment's. Clamped, as in convert_result:
            # the decode order is the spoken order, and the words keep their real times.
            start = max(start, out[-1].start)
        update = {"start": start, "end": max(end, start), "words": words}
        out.append(seg.model_copy(update=update))
    return out


def has_sound(segment: Segment, regions: list[tuple[float, float]]) -> bool:
    """True if any word midpoint (or, without word times, the segment) falls on sound."""
    t = WORD_TOLERANCE_SECONDS
    mids = [
        (w.start + w.end) / 2 for w in segment.words if w.start is not None and w.end is not None
    ]
    if not mids:
        return sound_share(segment.start, segment.end, regions) > 0
    return any(a - t <= m <= b + t for m in mids for a, b in regions)


def sound_share(start: float, end: float, regions: list[tuple[float, float]]) -> float:
    """Share of [start, end] covered by the sound regions."""
    if end <= start:
        return 1.0 if any(a <= start <= b for a, b in regions) else 0.0
    covered = sum(max(0.0, min(end, b) - max(start, a)) for a, b in regions)
    return covered / (end - start)


def uncovered_regions(
    segments: list[Segment],
    regions: list[tuple[float, float]],
    min_seconds: float = GAP_MIN_SECONDS,
) -> list[tuple[float, float]]:
    """Sound regions at least ``min_seconds`` long on which no transcribed word falls.

    The mirror of ``has_sound``: word midpoints with the same tolerance; a segment without
    word times covers the regions its span overlaps.
    """
    t = WORD_TOLERANCE_SECONDS
    mids: list[float] = []
    spans: list[tuple[float, float]] = []
    for seg in segments:
        seg_mids = [(w.start + w.end) / 2 for w in seg.words
                    if w.start is not None and w.end is not None]  # fmt: skip
        if seg_mids:
            mids.extend(seg_mids)
        else:
            spans.append((seg.start, seg.end))
    out: list[tuple[float, float]] = []
    for a, b in regions:
        if b - a < min_seconds:
            continue
        if any(a - t <= m <= b + t for m in mids):
            continue
        if any(s <= b + t and e >= a - t for s, e in spans):
            continue
        out.append((a, b))
    return out


def shifted(segments: list[Segment], offset: float) -> list[Segment]:
    """Segments decoded on a clip, moved onto the original timeline."""

    def move(t: float | None) -> float | None:
        return None if t is None else t + offset

    out = []
    for seg in segments:
        words = [w.model_copy(update={"start": move(w.start), "end": move(w.end)})
                 for w in seg.words]  # fmt: skip
        out.append(seg.model_copy(update={"start": seg.start + offset,
                                          "end": seg.end + offset, "words": words}))  # fmt: skip
    return out


def write_clip(wav_path: Path, start: float, end: float, out_path: Path) -> None:
    """Write the ``[start, end]`` stretch of a wav file as a wav of its own."""
    import wave

    with wave.open(str(wav_path), "rb") as f:
        rate = f.getframerate()
        a = min(max(0, int(start * rate)), f.getnframes())
        b = min(max(a, int(end * rate)), f.getnframes())
        f.setpos(a)
        frames = f.readframes(b - a)
        params = f.getparams()
    with wave.open(str(out_path), "wb") as f:
        f.setparams(params)
        f.writeframes(frames)


@contextmanager
def whisper_progress(callback, segments=None) -> Iterator[None]:
    """Report how much of the audio Whisper has decoded: mlx_whisper only shows it as a tqdm
    bar, so the bar is replaced by one that calls ``callback`` with the share done.

    ``segments`` receives the segments decoded so far (mlx_whisper's own dicts, a copy of the
    list) each time a window is done, so the app can show the text as it grows. They are read
    from the transcribe loop, which updates the bar right after adding a window's segments;
    nothing in the decoding changes (decision 0033)."""
    if callback is None and segments is None:
        yield
        return
    import sys
    from types import SimpleNamespace

    import mlx_whisper.transcribe  # noqa: F401  (the module, not the function)

    module = sys.modules["mlx_whisper.transcribe"]

    class Bar:
        def __init__(self, total: int = 0, **_: Any) -> None:
            self.total, self.n = total or 0, 0

        def __enter__(self) -> "Bar":
            return self

        def __exit__(self, *_: Any) -> bool:
            return False

        def update(self, k: int) -> None:
            self.n += k
            if callback is not None and self.total:
                callback(min(1.0, self.n / self.total))
            if segments is not None:
                so_far = sys._getframe(1).f_locals.get("all_segments")
                if isinstance(so_far, list):  # another mlx_whisper version: no partial text
                    segments(list(so_far))

    original = module.tqdm
    module.tqdm = SimpleNamespace(tqdm=Bar)
    try:
        yield
    finally:
        module.tqdm = original


@contextmanager
def carried_prompt(text: str | None) -> Iterator[None]:
    """Put ``text`` at the start of Whisper's prompt on every window, not only the first one.

    mlx-whisper prepends the initial prompt to the transcribed text and keeps only the last
    n_ctx // 2 - 1 tokens of it, so after a minute or two of speech the initial prompt is gone.
    This keeps ``text`` and fills the rest of the budget with the most recent text, like
    openai-whisper's ``carry_initial_prompt``. The patch is active only inside the ``with``.
    """
    if not text:
        yield
        return
    from mlx_whisper import decoding

    original = decoding.DecodingTask._get_initial_tokens

    def initial_tokens(self) -> tuple[int, ...]:
        tokens = list(self.sot_sequence)
        if prefix := self.options.prefix:
            prefix_tokens = (self.tokenizer.encode(" " + prefix.strip())
                             if isinstance(prefix, str) else list(prefix))  # fmt: skip
            if self.sample_len is not None:
                prefix_tokens = prefix_tokens[-(self.n_ctx // 2 - self.sample_len) :]
            tokens = tokens + prefix_tokens
        carried = self.tokenizer.encode(" " + text.strip())
        prompt = self.options.prompt or []
        if isinstance(prompt, str):
            prompt = self.tokenizer.encode(" " + prompt.strip())
        room = max(0, self.n_ctx // 2 - 1 - len(carried))
        recent = list(prompt)[-room:] if room else []
        return tuple([self.tokenizer.sot_prev, *carried, *recent, *tokens])

    decoding.DecodingTask._get_initial_tokens = initial_tokens
    try:
        yield
    finally:
        decoding.DecodingTask._get_initial_tokens = original


class MLXWhisperBackend:
    name = "mlx-whisper"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        initial_prompt: str | None = None,
        seed: int = SEED,
        vad: bool = False,
        glossary: str | None = None,
        gap_repair: bool = True,
    ) -> None:
        self.model = model
        self.initial_prompt = initial_prompt
        self.seed = seed
        # Transcribe only the stretches with sound (parola.audio.vad).
        self.vad = vad
        # With VAD: decode again, each on its own clip, the sound regions the full decode
        # left without words (results/2026-10-23-gap-repair.md).
        self.gap_repair = gap_repair
        # Terms carried in the prompt of every window (parola.glossary).
        self.glossary = glossary
        self.on_progress = None
        # Called with the segments decoded so far, as Segment objects, after every window.
        self.on_segments = None

    def _partial(self, language: str | None, regions=None, cuts=None):
        """What whisper_progress hands over, made into the segments the transcript will have."""
        if self.on_segments is None:
            return None

        def hand_over(raw: list[dict]) -> None:
            segments = convert_result({"segments": raw, "language": language}).segments
            if cuts is not None:
                segments = restore_times(segments, cuts)
            if regions is not None:
                segments = [s for s in segments if has_sound(s, regions)]
            self.on_segments(segments)

        return hand_over

    def transcribe(self, wav_path: Path, language: str | None = None) -> ASRResult:
        with carried_prompt(self.glossary):
            return self._transcribe(wav_path, language)

    def _transcribe(self, wav_path: Path, language: str | None = None) -> ASRResult:
        import mlx.core as mx
        import mlx_whisper

        # Whisper falls back to sampling at higher temperatures when decoding fails
        # (loops, low confidence): a fixed seed makes those runs reproducible.
        mx.random.seed(self.seed)

        if not self.vad:
            # Library defaults are kept on purpose: decoding parameters only change after
            # they are measured on the test set (Phase 2).
            with whisper_progress(self.on_progress, self._partial(language)):
                result = mlx_whisper.transcribe(
                    str(wav_path),
                    path_or_hf_repo=self.model,
                    language=language,
                    word_timestamps=True,
                    initial_prompt=self.initial_prompt,
                )
            return convert_result(result)

        from parola.asr.language import detect
        from parola.audio.vad import speech_regions

        regions = speech_regions(wav_path)
        if not regions:
            return ASRResult(segments=[], language=None)
        if language is None:
            # Not Whisper's own guess on the first 30 s: windows spread over the whole call
            # (parola.asr.language).
            language = detect(wav_path, regions, self.model)
        # Decoding is the same as without VAD: shifting where it starts (or decoding each sound
        # stretch on its own, or Whisper's hallucination_silence_threshold) moved Whisper's
        # windows and made it skip real speech on the test set (results/2026-10-15-vad.md).
        # Only long runs of exact zeros, which a microphone never gives but a call's system
        # side gives whenever nobody plays anything, are shortened: Whisper skipped the speech
        # after them (parola.audio.silence).
        import tempfile

        from parola.audio.silence import condensed_wav

        with tempfile.TemporaryDirectory(prefix="parola-silence-") as tmp:
            short = Path(tmp) / "short.wav"
            cuts = condensed_wav(wav_path, short)
            source = short if cuts is not None else wav_path
            with whisper_progress(self.on_progress, self._partial(language, regions, cuts)):
                merged = mlx_whisper.transcribe(
                    str(source),
                    path_or_hf_repo=self.model,
                    language=language,
                    word_timestamps=True,
                    initial_prompt=self.initial_prompt,
                )
        converted = convert_result(merged)
        if cuts is not None:
            converted.segments = restore_times(converted.segments, cuts)
        converted.segments = [s for s in converted.segments if has_sound(s, regions)]
        if self.gap_repair:
            converted.segments = self._repair_gaps(wav_path, converted.segments, regions,
                                                   language)  # fmt: skip
        return converted

    def _repair_gaps(
        self,
        wav_path: Path,
        segments: list[Segment],
        regions: list[tuple[float, float]],
        language: str | None,
    ) -> list[Segment]:
        """Transcribe on its own clip every sound region the full decode left without words."""
        gaps = uncovered_regions(segments, regions)
        if not gaps:
            return segments
        import tempfile

        import mlx_whisper

        repaired = list(segments)
        with tempfile.TemporaryDirectory(prefix="parola-gaps-") as tmp:
            for i, (a, b) in enumerate(gaps[:MAX_GAP_REPAIRS]):
                start = max(0.0, a - GAP_MARGIN_SECONDS)
                clip = Path(tmp) / f"gap-{i}.wav"
                write_clip(wav_path, start, b + GAP_MARGIN_SECONDS, clip)
                result = mlx_whisper.transcribe(
                    str(clip),
                    path_or_hf_repo=self.model,
                    language=language,
                    word_timestamps=True,
                    initial_prompt=self.initial_prompt,
                )
                clip_segments = shifted(convert_result(result).segments, start)
                # Filtered against the gap region alone: the margins must not re-add words
                # the full decode already has, and the clip can hallucinate on its edges.
                repaired += [s for s in clip_segments if has_sound(s, [(a, b)])]
        repaired.sort(key=lambda s: (s.start, s.end))
        return repaired
