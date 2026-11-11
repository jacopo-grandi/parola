"""The live subtitle loop (ToTTI v0, decision 0038).

Every ``TICK`` seconds the last ``WINDOW`` seconds of the growing system wav are transcribed
with Whisper (language forced to English) and translated into Italian on the CPU. The lines
are ephemeral: nothing is written to disk and nothing touches the verbatim transcript, which
is still produced in full when the call ends. Without a recording the audio comes from memory
instead of a wav (``listen.Listener``, decision 0042).
"""

from __future__ import annotations

import tempfile
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from parola.totti.stable import MAX_WAIT, StablePrefix
from parola.totti.tail import WavFormatError, tail
from parola.totti.translator import Translator

# Seconds between updates. Measured on 2026-10-19 (growing synthetic speech, 18 ticks): a
# tick takes ~1.1-1.3 s (ASR ~1.0 s, MT ~0.1 s) whether or not the distillation saturates the
# CPU. Re-measured on 2026-10-21 on two 12-minute real-time replays (ASR 0.87 s mean): the
# stable prefix needs two decodes to agree, so the cadence sets the floor of the committed
# lag — 1.5 s keeps the loop inside the tick and takes the mean per-word lag under 4 s
# (results/2026-10-21-totti-live-latency.md).
TICK = 1.5
WINDOW = 15.0  # seconds of audio transcribed each time
# Below this peak the window is digital silence (recorder.SILENCE_PEAK on a 0..1 scale):
# transcribing it would only make Whisper invent text.
SILENCE_PEAK = 8 / 32768


# Writes the last ``seconds`` heard to a wav and returns ``(available, peak, total)``, like
# ``tail``: the clip's seconds, its peak, and the seconds heard so far (the audio clock).
Source = Callable[[float, Path], tuple[float, float, float]]


def _trim(clip: Path, cut: float) -> float:
    """Drop the first ``cut`` seconds of ``clip`` in place; returns the new peak (0..1)."""
    with wave.open(str(clip), "rb") as f:
        rate = f.getframerate()
        samples = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    samples = samples[int(cut * rate) :]
    with wave.open(str(clip), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples.tobytes())
    return float(np.abs(samples.astype(np.int32)).max()) / 32768 if samples.size else 0.0


class SubtitleLoop:
    """Reads the tail of ``source`` while it grows and keeps the translated lines.

    ``source`` is the growing wav of a recording, or a callable giving the last seconds heard
    (listening without recording). ``running`` says whether the recording (or the listening) is
    still on: the loop stops by itself when it returns False, so the models are free again when
    the end-of-call transcription starts.
    """

    def __init__(self, source: Path | Source, translator: Translator, backend,
                 running: Callable[[], bool], lang: str = "it") -> None:  # fmt: skip
        if isinstance(source, Path):
            wav_path = source
            source = lambda seconds, out: tail(wav_path, seconds, out)  # noqa: E731
        self.source = source
        self.translator = translator
        self.backend = backend
        self._running = running
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # "it": Italian lines, committed-only (the translation needs words that will not
        # change). "en": the original, which affords showing the provisional tail too —
        # text at decode latency, refined afterwards — and skips the translation.
        self.lang = lang
        self.lines: list[dict] = []  # [{"en": ..., "it": ...}], oldest first
        self.tail = ""  # the last decode's uncommitted words (shown dimmed in the EN view)
        self.revision = 0
        self.error = ""
        # Seconds spent in the last tick's stages, and "lag": how far the newest *committed*
        # word sits behind the clock when the tick publishes ("raw_lag": the newest decoded
        # word behind the newest audio, the part the commit policy cannot help).
        self.timing: dict[str, float] = {}
        # Only words two consecutive decodes agree on are shown (the measured fix for the
        # churn of re-decoding the window: results/2026-10-21-totti-live-latency.md).
        self.stable = StablePrefix()
        # The window overlaps itself between ticks: translate each English line once.
        self._cache: dict[str, str] = {}

    # ---------------------------------------------------------------- control

    def start(self) -> None:
        if self.active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        return {"active": self.active, "lines": self.lines, "tail": self.tail,
                "lang": self.lang, "revision": self.revision,
                "error": self.error, "timing": self.timing}  # fmt: skip

    # ---------------------------------------------------------------- the loop

    def _run(self) -> None:
        # The model loads while the first seconds of audio are still being captured, so the
        # first decode does not pay for it (the cold start's biggest share, ~1.3 s).
        if (warm := getattr(self.backend, "warm", None)) and not self._stop.is_set():
            try:
                warm()
            except Exception as e:  # the first tick will load (and report) instead
                self.error = str(e)
        while not self._stop.is_set() and self._running():
            started = time.monotonic()
            try:
                self.tick()
                self.error = ""
            except Exception as e:  # shown in the UI; the next tick tries again
                self.error = str(e)
            self._stop.wait(max(TICK - (time.monotonic() - started), 0.1))

    def tick(self) -> None:
        """One update: tail → Whisper (en) → translation → ``lines``."""
        started = time.monotonic()
        timing: dict[str, float] = {}
        with tempfile.TemporaryDirectory(prefix="parola-totti-") as tmp:
            clip = Path(tmp) / "tail.wav"
            try:
                available, peak, total = self.source(WINDOW, clip)
            except (FileNotFoundError, WavFormatError):
                return  # the helper has not written the header yet
            # The clip's first sample on the audio clock: the words of every decode get
            # absolute times from it, so the prefix matching survives the window sliding
            # under them — and a source that stopped growing (a paused recording) stands
            # still instead of drifting, as a wall clock would.
            offset = total - available
            # Pin the decode's left edge at the anchor (the last closed line): an identical
            # left context is what lets consecutive decodes agree on the words after it. The
            # WINDOW cap stands, and at least one second of audio is always decoded.
            cut = min(max(self.stable.anchor - offset, 0.0), max(available - 1.0, 0.0))
            if cut > 0.05:
                peak = _trim(clip, cut)
                available -= cut
                offset += cut
            timing["tail"] = time.monotonic() - started
            if available < 1.0 or peak < SILENCE_PEAK:
                # A whole window of silence: what was shown has been read.
                self.stable.rest(at=total)
                self._publish([], "", timing)
                return
            result = self.backend.transcribe(clip, language="en")
        if self._stop.is_set():
            return  # stopped during the decode: the strip closed, publish nothing more
        timing["asr"] = time.monotonic() - started - timing["tail"]
        # Words that waited MAX_WAIT for a second opinion commit without one.
        self.stable.update(result.segments, offset, deadline=total - MAX_WAIT)
        texts = self.stable.lines()
        if self.lang == "it" and (missing := [t for t in texts if t not in self._cache]):
            for en, it in zip(missing, self.translator.translate(missing), strict=True):
                self._cache[en] = it
            # The open line grows word by word, each step a new entry: keep the cache small.
            while len(self._cache) > 500:
                del self._cache[next(iter(self._cache))]
        timing["mt"] = time.monotonic() - started - timing["tail"] - timing["asr"]
        timing["tick"] = time.monotonic() - started
        # How far what the viewer reads sits behind the clock when the tick publishes (the
        # UI poll and the audio buffers are on top); "raw_lag" is the newest decoded word
        # behind the newest audio — the share the commit policy cannot help.
        if self.stable.frontier:
            # Audio behind the frontier when the tail was cut, plus the tick spent since.
            timing["lag"] = max(total - self.stable.frontier, 0.0) + (time.monotonic() - started)
        if result.segments:
            timing["raw_lag"] = max(available - result.segments[-1].end, 0.0)
        lines = [{"en": t, "it": self._cache.get(t, "")} for t in texts]
        self._publish(lines, self.stable.pending(), timing)

    def _publish(self, lines: list[dict], tail: str = "",
                 timing: dict[str, float] | None = None) -> None:  # fmt: skip
        if self._stop.is_set():
            return  # the strip closed on stop: no line arrives after it
        if timing is not None:
            self.timing = {k: round(v, 2) for k, v in timing.items()}
        if lines != self.lines or tail != self.tail:
            self.lines = lines
            self.tail = tail
            self.revision += 1
