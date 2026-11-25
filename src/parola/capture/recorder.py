"""Record a call (microphone + system audio) and transcribe it per channel.

Capture is done by the Swift helper ``parola-capture`` (Core Audio process taps, decision 0013),
built on first use with ``swiftc``. Each channel is transcribed separately and the two are
merged into one transcript whose segments carry ``channel`` = ``mic`` (you) or ``system``
(the others): turns are separated by channel, not guessed. An imported audio or video file is a
single ``mixed`` channel, everyone on one track (decision 0021).
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from parola import __version__
from parola.asr import ASRBackend, release_models
from parola.audio import extract_audio
from parola.keywords import suggest_title
from parola.pipeline import transcribe_file
from parola.schema import Metadata, Segment, Transcript, to_text
from parola.schema.vocab import split_punctuation
from parola.speakers import label as speaker_label

REPO_ROOT = Path(__file__).resolve().parents[3]
SWIFT_DIR = Path(__file__).resolve().parent / "swift"
BINARY = REPO_ROOT / "bin" / "parola-capture"
CHANNELS = ("mic", "system", "mixed")
# Peak below this (16-bit PCM) means digital silence: macOS returns zeros instead of an error
# when the permission to record system audio is missing.
SILENCE_PEAK = 8
# A call channel with speech spends whole seconds at speaking level; one that recorded the wrong
# device only rustles. On 2026-10-23 a dead mic (Meet used the headset mic, the default input
# was another device) passed the peak check on a few pops and Whisper hallucinated on it: real
# mics spent 16-89% of their seconds at or above -40 dBFS, dead ones at most 0.9%.
SPEECH_FLOOR_DBFS = -40.0
SPEECH_SHARE_MIN = 0.05
PERMISSION_HINT = {
    "mic": "System Settings > Privacy & Security > Microphone",
    "system": "System Settings > Privacy & Security > Screen & System Audio Recording "
    "(System Audio Recording Only)",
}


class CaptureError(RuntimeError):
    pass


def build_helper(force: bool = False) -> Path:
    """Compile the Swift helper if it is missing or older than its sources."""
    sources = [SWIFT_DIR / "main.swift", SWIFT_DIR / "Info.plist"]
    newest = max(s.stat().st_mtime for s in sources)
    if BINARY.exists() and not force and BINARY.stat().st_mtime >= newest:
        return BINARY
    if shutil.which("swiftc") is None:
        raise CaptureError("swiftc not found: install the Xcode Command Line Tools")
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "swiftc", "-O", "-swift-version", "5", str(sources[0]), "-o", str(BINARY),
        "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
        "-Xlinker", str(sources[1]),
    ]  # fmt: skip
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CaptureError(f"building parola-capture failed:\n{result.stderr}")
    return BINARY


@dataclass
class Recording:
    name: str
    out_dir: Path
    capture: dict

    def path(self, channel: str) -> Path:
        """The channel's audio: the recorded wav, or its FLAC once compressed."""
        flac = self.out_dir / f"{self.name}.{channel}.flac"
        return flac if flac.exists() else self.out_dir / f"{self.name}.{channel}.wav"

    def channels(self) -> list[str]:
        return [c for c in CHANNELS if c in self.capture and self.path(c).exists()]


def record(out_dir: Path, name: str, duration: float | None = None, mic: bool = True,
           system: bool = True) -> Recording:  # fmt: skip
    """Run the helper until ``duration`` elapses or the user presses Ctrl-C."""
    binary = build_helper()
    cmd = [str(binary), "--out-dir", str(out_dir), "--name", name]
    if duration:
        cmd += ["--duration", str(duration)]
    if not mic:
        cmd.append("--no-mic")
    if not system:
        cmd.append("--no-system")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    try:
        out, _ = proc.communicate()
    except KeyboardInterrupt:
        # Ctrl-C reaches the helper too (same process group); wait for it to close the files.
        proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate()
    if proc.returncode != 0:
        raise CaptureError(f"parola-capture exited with {proc.returncode}")
    return Recording(name, out_dir, json.loads(out))


def peak(path: Path) -> int:
    """Peak absolute sample (16-bit scale) of an audio file."""
    with tempfile.TemporaryDirectory(prefix="parola-peak-") as tmp:
        wav = extract_audio(path, Path(tmp) / "a.wav")
        with wave.open(str(wav), "rb") as f:
            samples = array("h", f.readframes(f.getnframes()))
    return max((abs(x) for x in samples), default=0)


def speech_share(path: Path, floor_dbfs: float = SPEECH_FLOOR_DBFS) -> float:
    """Fraction of one-second windows whose RMS reaches ``floor_dbfs``."""
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="parola-share-") as tmp:
        wav = extract_audio(path, Path(tmp) / "a.wav")
        with wave.open(str(wav), "rb") as f:
            rate = f.getframerate()
            x = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float64)
    seconds = len(x) // rate
    if seconds == 0:
        return 0.0
    rms = np.sqrt((x[: seconds * rate].reshape(seconds, rate) ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1.0) / 32768.0)
    return float((db >= floor_dbfs).mean())


def merge_channels(parts: dict[str, Transcript], audio_file: str, duration: float) -> Transcript:
    """One transcript with the segments of every channel, sorted by time."""
    segments: list[Segment] = []
    for channel, t in parts.items():
        for s in t.segments:
            segments.append(s.model_copy(update={"channel": channel}))
    segments.sort(key=lambda s: (s.start, CHANNELS.index(s.channel or "mic")))
    first = next(iter(parts.values()))
    return Transcript(
        metadata=Metadata(
            kind="asr",
            audio_file=audio_file,
            duration=duration,
            backend=first.metadata.backend,
            model=first.metadata.model,
            language_requested=first.metadata.language_requested,
            language_detected=first.metadata.language_detected,
            created_at=datetime.now(UTC),
            parola_version=__version__,
        ),
        segments=segments,
    )


def save_warnings(rec: Recording, warnings: list[dict]) -> None:
    """Into ``<name>.capture.json``, where the app reads them: a dead channel must be said on
    the call itself, not only in the engine log."""
    path = rec.out_dir / f"{rec.name}.capture.json"
    try:
        capture = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        capture = {}
    capture["warnings"] = warnings
    path.write_text(json.dumps(capture, indent=2, sort_keys=True), encoding="utf-8")
    rec.capture["warnings"] = warnings


# "mixed" (an imported file) has no side to name: every voice is on the one track.
LABELS = {"mic": "Io", "system": "Altri", "mixed": ""}


def marked_text(seg: Segment) -> str:
    """Segment text with runs of doubtful words as ``(word word?)``, like uncertain words in the
    annotation format (docs/annotation.md)."""
    if not seg.words:
        return seg.text
    out: list[str] = []
    run: list[str] = []
    for w in [*seg.words, None]:
        if w is not None and w.doubtful:
            run.append(w.text)
            continue
        if run:
            body, punct = split_punctuation(" ".join(run))
            out.append(f"({body}?){punct}")
            run = []
        if w is not None:
            out.append(w.text)
    return " ".join(out)


def to_dialogue(transcript: Transcript) -> str:
    """Readable text with the speaker side of each segment and the doubtful words marked."""
    lines = []
    for line, seg in zip(to_text(transcript).splitlines(), transcript.segments, strict=True):
        label = speaker_label(transcript, seg, LABELS)
        stamp, _, _ = line.partition("] ")
        who = f"{label}: " if label else ""
        lines.append(f"{stamp}] {who}{marked_text(seg)}")
    return "\n".join(lines) + ("\n" if lines else "")


# A mic segment is echo of the other side (headphones leaking into the microphone) when at
# least this share of its words is said by the other side within ECHO_MARGIN_SECONDS.
ECHO_SHARE = 0.6
ECHO_MARGIN_SECONDS = 2.0


def drop_echo(mic: Transcript, system: Transcript) -> int:
    """Remove from ``mic`` the segments that repeat what ``system`` says at the same time."""
    from collections import Counter

    from parola.eval.normalize import tokenize

    timed: list[tuple[str, float]] = []
    for seg in system.segments:
        for tok in tokenize(seg.words, seg.lang or "it", "fidelity", "hyp"):
            w = seg.words[tok.word_index]
            t = (w.start + w.end) / 2 if w.start is not None and w.end is not None else seg.start
            timed.append((tok.text, t))
    kept, dropped = [], 0
    for seg in mic.segments:
        tokens = [t.text for t in tokenize(seg.words, seg.lang or "it", "fidelity", "hyp")]
        if not tokens:
            kept.append(seg)
            continue
        lo, hi = seg.start - ECHO_MARGIN_SECONDS, seg.end + ECHO_MARGIN_SECONDS
        near = Counter(text for text, t in timed if lo <= t <= hi)
        mine = Counter(tokens)
        shared = sum(min(n, near[text]) for text, n in mine.items())
        if shared / len(tokens) >= ECHO_SHARE:
            dropped += 1
        else:
            kept.append(seg)
    mic.segments = kept
    return dropped


def mark_doubts(primary: Transcript, second: Transcript) -> int:
    """Flag the words of ``primary`` that ``second`` does not confirm; returns how many."""
    from parola.doubt import doubtful_words

    flagged = doubtful_words(primary, second)
    k = 0
    for seg in primary.segments:
        for w in seg.words:
            w.doubtful = k in flagged
            k += 1
    return len(flagged)


def provisional(parts: dict[str, Transcript], audio_file: str, duration: float) -> Transcript:
    """What the final transcript will look like with the steps done so far (decision 0031).

    A deep copy, so the steps still to come (doubtful words, voices) never change a transcript
    the app may be reading.
    """
    from parola.annotate import merge_segments

    copies = {c: t.model_copy(deep=True) for c, t in parts.items()}
    if "mic" in copies and "system" in copies:
        drop_echo(copies["mic"], copies["system"])
    copies = {c: merge_segments(t) for c, t in copies.items()}
    return merge_channels(copies, audio_file, duration)


# The growing text while Whisper works is handed to the app at most this often, in seconds.
PARTIAL_EVERY = 1.0

# Share of the work of each step, in minutes per hour of audio, measured on en-001 (1 h, imported)
# on 2026-10-16: Whisper 3:00, Parakeet 1:14, voices 3:23 (decision 0031).
WEIGHTS = {"primary": 3.0, "second": 1.2, "speakers": 3.4}


def transcribe_recording(rec: Recording, backend: ASRBackend, language: str | None = None,
                         second: ASRBackend | None = None, progress=None,
                         speakers: bool = True, preview=None,
                         two_people: bool = False) -> tuple[Path, Path]:  # fmt: skip
    """Transcribe each channel and write <name>.json and <name>.txt next to the audio.

    With ``second``, each channel is also transcribed by a second system and the words of the
    primary transcript it does not confirm are marked as doubtful. On the test set Parakeet v3
    catches about two thirds of turbo's errors while flagging 12% of the words
    (results/2026-10-16-doubt-analisi.md).

    With ``two_people`` the call had only the person recording and one other: the voices are not
    told apart, the other side is one voice (decision 0058).

    ``preview(stage, transcript)`` receives the provisional transcript as soon as Whisper is done
    on every channel when the voices are still to come (stage ``"transcript"``), and again with
    the voices when only the doubtful words are missing (stage ``"voices"``), so the app can
    show and export the text while it waits (decisions 0031, 0058). Nothing is written to disk
    before the final transcript.
    """
    parts: dict[str, Transcript] = {}
    with_speech = []
    warnings: list[dict] = []
    for c in rec.channels():
        if rec.capture[c].get("seconds", 0) <= 0:
            continue
        device = rec.capture[c].get("input_device") or rec.capture[c].get("output_device")
        if peak(rec.path(c)) < SILENCE_PEAK:
            # Transcribing silence makes Whisper invent text ("Thank you."): skip it.
            hint = PERMISSION_HINT.get(c, "the file's audio track")
            print(f"warning: the {c} channel is silent; if that is unexpected, check {hint}",
                  file=sys.stderr)  # fmt: skip
            warnings.append({"channel": c, "kind": "silent", "device": device})
            continue
        if c in ("mic", "system") and (share := speech_share(rec.path(c))) < SPEECH_SHARE_MIN:
            # A few pops pass the peak check; a channel with no stretch at speaking level
            # recorded the wrong device, and Whisper would only hallucinate on it.
            print(f"warning: the {c} channel has almost no speech "
                  f"({share:.1%} of its seconds reach {SPEECH_FLOOR_DBFS:.0f} dBFS)"
                  + (f"; it recorded {device!r}" if device else ""), file=sys.stderr)  # fmt: skip
            warnings.append({"channel": c, "kind": "no_speech", "share": round(share, 3),
                             "device": device})  # fmt: skip
            continue
        with_speech.append(c)
    if warnings:
        save_warnings(rec, warnings)
    if not with_speech:
        raise CaptureError("every channel is silent: nothing to transcribe")
    # Progress in units of work (WEIGHTS). Telling voices apart (decision 0026) runs on the
    # channels with several people on them.
    voiced = [c for c in with_speech if c in VOICED_CHANNELS] if speakers and not two_people else []
    if voiced:
        voiced = _speaker_models() and voiced
    weights = WEIGHTS
    total_steps = len(with_speech) * (weights["primary"] + (weights["second"] if second else 0))
    total_steps += len(voiced or []) * weights["speakers"]
    done = 0.0

    def run(engine: ASRBackend, kind: str, path: Path, out_dir: Path, message: str,
            channel: str | None = None) -> Transcript:  # fmt: skip
        def report(share: float) -> None:
            if progress is not None:
                progress(message, done + weights[kind] * share, total_steps)

        last = 0.0

        def grow(segments: list[Segment]) -> None:
            # The text so far, while Whisper is still on this channel (decision 0033): at most
            # once a second, the app does not need more.
            nonlocal last
            if preview is None or not segments or time.monotonic() - last < PARTIAL_EVERY:
                return
            last = time.monotonic()
            try:
                so_far = Transcript(metadata=Metadata(audio_file=path.name,
                                                      duration=rec.capture[channel]["seconds"],
                                                      created_at=datetime.now(UTC),
                                                      parola_version=__version__),
                                    segments=segments)  # fmt: skip
                preview("partial", provisional({**parts, channel: so_far}, audio_file, duration))
            except Exception as e:
                # A preview must never cost the transcript: a 25-minute call once aborted (and
                # lost its text) because one partial list failed validation mid-decode.
                print(f"warning: partial preview skipped ({e})", file=sys.stderr)

        report(0.0)
        if hasattr(engine, "on_progress"):
            engine.on_progress = report
        if channel is not None and hasattr(engine, "on_segments"):
            engine.on_segments = grow
        try:
            return transcribe_file(path, engine, out_dir=out_dir, language=language).transcript
        finally:
            if hasattr(engine, "on_progress"):
                engine.on_progress = None
            if hasattr(engine, "on_segments"):
                engine.on_segments = None
            # One engine in memory at a time: peak 4.7 GB instead of 8.8 for a call, same
            # time and the same transcript (decision 0027).
            release_models()

    duration = max(rec.capture[c]["seconds"] for c in with_speech)
    audio_file = f"{rec.name}.*.wav"

    def show(stage: str, t: Transcript) -> None:
        if preview is not None:
            preview(stage, t.model_copy(deep=True))  # a copy: the flags still to come stay out

    # Whisper on every channel first, so the text can be shown before the other steps; each
    # run is independent, so the order does not change the result.
    work = {c: rec.out_dir / f".{rec.name}.{c}" for c in with_speech}
    side = {c: f" {LABELS[c]}" if LABELS[c] else "" for c in with_speech}
    for channel in with_speech:
        parts[channel] = run(backend, "primary", rec.path(channel), work[channel],
                             f"trascrizione{side[channel]}", channel=channel)  # fmt: skip
        done += weights["primary"]
    # Each channel as Whisper wrote it, before the echo is dropped and the segments are joined:
    # the second opinion is compared with exactly this, as when it ran right after Whisper
    # (decision 0058). The segments share their words with the final transcript, so the flags
    # set on them land there.
    whisper = {c: t.model_copy(update={"segments": list(t.segments)}) for c, t in parts.items()}
    if "mic" in parts and "system" in parts:
        n = drop_echo(parts["mic"], parts["system"])
        if n:
            print(f"mic: dropped {n} segments that echo the other side", file=sys.stderr)
    # Whisper cuts speech into very short segments: join those of the same side separated by
    # at most 1 s (up to 30 s), as for annotation drafts. Words and their flags do not change.
    from parola.annotate import merge_segments

    merged = merge_channels({c: merge_segments(t) for c, t in parts.items()}, audio_file, duration)
    if two_people:
        # Only the person recording and one other (decision 0058): the other side is one
        # voice, which can be named at once; nothing to tell apart.
        from parola.speakers import single_voice

        for channel in with_speech:
            if channel in VOICED_CHANNELS:
                merged = single_voice(merged, channel)
    elif voiced:
        show("transcript", merged)
    for channel in voiced or []:
        from parola import diarize
        from parola.speakers import assign

        def report(share: float, base: float = done) -> None:
            if progress is not None:
                progress("chi parla", base + weights["speakers"] * share, total_steps)

        report(0.0)
        try:
            turns = diarize.diarize(rec.path(channel), progress=report)
        except Exception as e:  # the transcript is still good without the voices
            print(f"warning: voices not told apart on {channel}: {e}", file=sys.stderr)
            turns = []
        merged = assign(merged, channel, turns)
        done += weights["speakers"]
    if voiced:
        merged = merge_segments(merged)
    # The second opinion last (decision 0058): it only marks words, never changes them, so the
    # text and the voices are final from here and can be exported while it runs.
    if second is not None:
        show("voices", merged)
        for channel in with_speech:
            other = run(second, "second", rec.path(channel), work[channel] / "second",
                        f"parole dubbie{side[channel]}")  # fmt: skip
            done += weights["second"]
            n = mark_doubts(whisper[channel], other)
            total = sum(len(seg.words) for seg in whisper[channel].segments)
            print(f"{channel}: {n} doubtful words out of {total}", file=sys.stderr)
    for channel in with_speech:
        shutil.rmtree(work[channel], ignore_errors=True)
    json_path = rec.out_dir / f"{rec.name}.json"
    txt_path = rec.out_dir / f"{rec.name}.txt"
    # A title from what was said (decision 0020): only a label, the person can change it.
    merged.metadata.title_suggested = suggest_title(merged)
    merged.metadata.title = merged.metadata.title or merged.metadata.title_suggested
    merged.save(json_path)
    txt_path.write_text(to_dialogue(merged), encoding="utf-8")
    compress_channels(rec)
    return json_path, txt_path


# Channels that can hold several voices: the others in a call, everyone in an imported file.
VOICED_CHANNELS = ("system", "mixed")


def _speaker_models() -> bool:
    """sherpa-onnx is there and its models are, or can be fetched now (first use only)."""
    from parola import diarize

    if not diarize.installed():
        return False
    if diarize.available():
        return True
    try:
        diarize.ensure_models()
    except Exception as e:  # offline on first use: try again next time
        print(f"warning: speaker models not downloaded ({e}); voices not told apart",
              file=sys.stderr)  # fmt: skip
        return False
    return True


def compress_channels(rec: Recording) -> None:
    """Replace each channel's wav with a lossless FLAC of what the models hear (decision 0024).
    A failure keeps the wav: the call stays playable, only larger."""
    from parola.audio import compress

    for c in CHANNELS:
        wav = rec.out_dir / f"{rec.name}.{c}.wav"
        if wav.exists():
            try:
                compress(wav)
            except Exception as e:
                print(f"warning: {wav.name} not compressed: {e}", file=sys.stderr)


def main_record(out_dir: Path, name: str | None, backend: ASRBackend, language: str | None,
                duration: float | None, mic: bool, system: bool,
                second: ASRBackend | None = None) -> int:  # fmt: skip
    name = name or datetime.now().strftime("call-%Y%m%d-%H%M%S")
    try:
        print(f"recording {out_dir / name}.*.wav — stop with Ctrl-C", file=sys.stderr)
        rec = record(out_dir, name, duration=duration, mic=mic, system=system)
        for c in rec.channels():
            print(f"{c}: {rec.capture[c]['seconds']:.1f} s", file=sys.stderr)
        json_path, txt_path = transcribe_recording(rec, backend, language, second=second)
    except CaptureError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    return 0
