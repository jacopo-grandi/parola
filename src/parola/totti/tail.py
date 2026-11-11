"""Read the tail of a wav file that is still being written.

The capture helper fixes the RIFF chunk sizes only when the recording stops, so the header
of a growing file declares stale sizes. The chunks are parsed by hand and the data chunk is
taken to run to the end of the file: the live subtitle loop (ToTTI, decision 0038) reads the
last seconds of the system channel this way.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from parola.audio import ASR_SAMPLE_RATE, FFmpegError

# wFormatTag values; EXTENSIBLE repeats the real tag in the first two bytes of its GUID.
_PCM, _FLOAT, _EXTENSIBLE = 1, 3, 0xFFFE
# ffmpeg's name for each (format tag, bits per sample) raw stream.
_RAW = {
    (_PCM, 16): "s16le",
    (_PCM, 24): "s24le",
    (_PCM, 32): "s32le",
    (_FLOAT, 32): "f32le",
    (_FLOAT, 64): "f64le",
}


class WavFormatError(RuntimeError):
    pass


@dataclass
class WavStream:
    rate: int
    channels: int
    raw_format: str  # ffmpeg's -f name for the samples
    bytes_per_frame: int
    data_offset: int  # where the samples start


def wav_stream(path: Path) -> WavStream:
    """The sample format and data offset of ``path``, with the declared sizes distrusted."""
    with path.open("rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise WavFormatError(f"{path.name}: not a wav file")
        fmt = None
        while True:
            header = f.read(8)
            if len(header) < 8:
                raise WavFormatError(f"{path.name}: no data chunk yet")
            cid, size = header[:4], int.from_bytes(header[4:8], "little")
            if cid == b"fmt ":
                fmt = f.read(min(size, 40))
                f.seek(size - len(fmt) + size % 2, 1)
            elif cid == b"data":
                if fmt is None or len(fmt) < 16:
                    raise WavFormatError(f"{path.name}: data before a usable fmt chunk")
                return _stream(path.name, fmt, f.tell())
            else:
                f.seek(size + size % 2, 1)


def _stream(name: str, fmt: bytes, data_offset: int) -> WavStream:
    tag, channels, rate = struct.unpack_from("<HHI", fmt, 0)
    bits = struct.unpack_from("<H", fmt, 14)[0]
    if tag == _EXTENSIBLE:
        if len(fmt) < 26:
            raise WavFormatError(f"{name}: truncated extensible fmt chunk")
        tag = struct.unpack_from("<H", fmt, 24)[0]
    raw = _RAW.get((tag, bits))
    if raw is None or not channels or not rate:
        raise WavFormatError(f"{name}: unsupported sample format (tag {tag}, {bits} bit)")
    return WavStream(rate, channels, raw, channels * bits // 8, data_offset)


def tail(path: Path, seconds: float, out: Path,
         sample_rate: int = ASR_SAMPLE_RATE) -> tuple[float, float, float]:  # fmt: skip
    """Write the last ``seconds`` of ``path`` to ``out`` as 16-bit PCM mono at ``sample_rate``.

    Returns ``(available, peak, total)``: how many seconds the clip holds, the largest
    absolute sample of the tail on a 0..1 scale (1.0 when the format has no cheap peak) so
    the caller can skip digital silence, and the seconds the whole file holds so far — the
    audio clock the stable prefix anchors its words to.
    """
    s = wav_stream(path)
    total = path.stat().st_size - s.data_offset
    total -= total % s.bytes_per_frame
    want = int(seconds * s.rate) * s.bytes_per_frame
    take = min(want, max(total, 0))
    if take <= 0:
        return 0.0, 0.0, max(total, 0) / (s.rate * s.bytes_per_frame)
    with path.open("rb") as f:
        f.seek(s.data_offset + total - take)
        raw = f.read(take)
    if shutil.which("ffmpeg") is None:
        raise FFmpegError("ffmpeg not found on PATH (install it with `brew install ffmpeg`)")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", s.raw_format, "-ar", str(s.rate), "-ac", str(s.channels), "-i", "pipe:0",
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(out),
    ]  # fmt: skip
    result = subprocess.run(cmd, input=raw, capture_output=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(f"ffmpeg failed on the tail of {path.name}: "
                          f"{result.stderr.decode(errors='replace').strip()}")  # fmt: skip
    return (len(raw) / (s.rate * s.bytes_per_frame), _peak(raw, s.raw_format),
            total / (s.rate * s.bytes_per_frame))  # fmt: skip


def _peak(raw: bytes, raw_format: str) -> float:
    if raw_format == "f32le":
        samples = np.frombuffer(raw, dtype="<f4")
    elif raw_format == "s16le":
        samples = np.frombuffer(raw, dtype="<i2") / 32768.0
    else:  # 24/32-bit int, float64: rare enough that "there is sound" is the safe answer
        return 1.0
    return float(np.abs(samples).max()) if samples.size else 0.0
