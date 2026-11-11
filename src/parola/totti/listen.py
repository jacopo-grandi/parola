"""Listening without recording: live subtitles with the audio kept in memory (decision 0042).

The capture helper runs with ``--stream``: the system audio alone, as 16 kHz mono 16-bit PCM
on its stdout, and nothing written to disk. Only the last ``WINDOW`` seconds stay here, in a
ring buffer the subtitle loop reads like the tail of a growing wav; they are dropped when
listening stops.
"""

from __future__ import annotations

import contextlib
import json
import signal
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np

from parola.audio import ASR_SAMPLE_RATE
from parola.totti.live import WINDOW


class AudioRing:
    """The last ``seconds`` of a stream of 16-bit mono PCM bytes, oldest first."""

    def __init__(self, seconds: float, rate: int = ASR_SAMPLE_RATE) -> None:
        self.rate = rate
        self._buf = np.zeros(int(seconds * rate), dtype=np.int16)
        self._filled = 0
        self._heard = 0  # samples ever appended: the audio clock of the stable prefix
        self._odd = b""  # a sample split across two reads
        self._lock = threading.Lock()

    def append(self, data: bytes) -> None:
        data = self._odd + data
        cut = len(data) - len(data) % 2
        self._odd = data[cut:]
        samples = np.frombuffer(data[:cut], dtype="<i2")
        size = len(self._buf)
        with self._lock:
            self._heard += len(samples)
            if len(samples) >= size:
                self._buf[:] = samples[-size:]
                self._filled = size
            else:
                n = len(samples)
                self._buf[: -n or None] = self._buf[n:]
                self._buf[size - n :] = samples
                self._filled = min(self._filled + n, size)

    def tail(self, seconds: float) -> np.ndarray:
        n = min(int(seconds * self.rate), self._filled)
        with self._lock:
            return self._buf[len(self._buf) - n :].copy()

    @property
    def heard(self) -> float:
        """Seconds ever appended, including what already left the ring."""
        return self._heard / self.rate

    def clear(self) -> None:
        with self._lock:
            self._buf[:] = 0
            self._filled = 0
            self._heard = 0
            self._odd = b""


def write_clip(samples: np.ndarray, out: Path, rate: int = ASR_SAMPLE_RATE) -> tuple[float, float]:
    """``samples`` as a 16-bit mono wav; ``(seconds, peak)`` of the clip."""
    with wave.open(str(out), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples.astype("<i2").tobytes())
    peak = float(np.abs(samples.astype(np.int32)).max()) / 32768 if samples.size else 0.0
    return len(samples) / rate, peak


class Listener:
    """The capture helper in ``--stream`` mode, feeding an :class:`AudioRing`."""

    def __init__(self, binary: Path | None = None) -> None:
        self._binary = binary
        self._proc: subprocess.Popen | None = None
        self.ring = AudioRing(WINDOW)
        self.levels: dict[str, float] = {}
        self.error = ""

    def start(self) -> None:
        if self.active:
            return
        from parola.capture.recorder import build_helper

        binary = self._binary or build_helper()
        self.ring.clear()
        self.levels, self.error = {}, ""
        proc = subprocess.Popen([str(binary), "--stream", "--levels"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)  # fmt: skip
        self._proc = proc
        threading.Thread(target=self._read_audio, args=(proc,), daemon=True).start()
        threading.Thread(target=self._read_levels, args=(proc,), daemon=True).start()

    def _read_audio(self, proc: subprocess.Popen) -> None:
        while chunk := proc.stdout.read1(8192):
            if proc is self._proc:
                self.ring.append(chunk)

    def _read_levels(self, proc: subprocess.Popen) -> None:
        tail: list[str] = []
        for raw in proc.stderr:
            line = raw.decode(errors="replace")
            if line.startswith("LEVELS "):
                with contextlib.suppress(json.JSONDecodeError):
                    self.levels = json.loads(line[7:])
            else:
                tail = [*tail, line.strip()][-5:]
        # The helper ended on its own (not through stop): permission or device problem.
        if proc.wait() != 0 and proc is self._proc:
            self.error = " ".join(tail) or "the capture stopped"

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.ring.clear()  # what was heard does not outlive the listening
        self.levels = {}

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def tail(self, seconds: float, out: Path) -> tuple[float, float, float]:
        """The subtitle loop's source: the last ``seconds`` heard, written to ``out``."""
        return (*write_clip(self.ring.tail(seconds), out, self.ring.rate), self.ring.heard)
