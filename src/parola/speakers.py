"""Voices on a channel: who said each segment, and how a voice is shown (decision 0026).

A speaker id is ``<channel>:<n>``, numbered by first appearance: ``system:1`` is the first
voice heard among the others, ``mixed:2`` the second voice of an imported file. The microphone
channel is always the person recording ("Io") and gets no id. Names chosen by the person live
in ``Metadata.speakers``; without one a voice shows as "Altri 1" or "Voce 1", or as "Altri"
when it is the only voice of its channel.
"""

from __future__ import annotations

from parola.schema import Segment, Transcript

DEFAULT_PREFIX = {"system": "Altri", "mixed": "Voce"}
# A word with no turn over its middle takes the nearest turn within this distance.
NEAREST_SECONDS = 1.0


def speaker_id(channel: str, voice: int) -> str:
    return f"{channel}:{voice + 1}"


def name(t: Transcript, speaker: str) -> str:
    if speaker in t.metadata.speakers:
        return t.metadata.speakers[speaker]
    channel, _, n = speaker.partition(":")
    prefix = DEFAULT_PREFIX.get(channel, channel)
    # The only voice of its channel (a call with two people) is the side itself: "Altri".
    alone = not any(s.speaker and s.speaker != speaker and s.speaker.startswith(f"{channel}:")
                    for s in t.segments)  # fmt: skip
    return prefix if alone else f"{prefix} {n}"


def single_voice(t: Transcript, channel: str) -> Transcript:
    """Every segment of ``channel`` to one voice: a call with two people (decision 0058), where
    the other side needs no telling apart but must be nameable."""
    sid = speaker_id(channel, 0)
    segments = [s.model_copy(update={"speaker": sid}) if s.channel == channel else s
                for s in t.segments]  # fmt: skip
    return t.model_copy(update={"segments": segments})


def label(t: Transcript, seg: Segment, side_labels: dict[str, str]) -> str:
    """What to write before a segment: the voice's name, or the side ("Io", "Altri")."""
    if seg.speaker:
        return name(t, seg.speaker)
    return side_labels.get(seg.channel or "", "?")


def _voice_at(time: float, turns: list[tuple[float, float, int]]) -> int | None:
    best, distance = None, NEAREST_SECONDS
    for start, end, voice in turns:
        if start <= time <= end:
            return voice
        d = min(abs(time - start), abs(time - end))
        if d < distance:
            best, distance = voice, d
    return best


def assign(t: Transcript, channel: str, turns: list[tuple[float, float, int]]) -> Transcript:
    """Give each segment of ``channel`` its voice, splitting a segment where the voice changes
    between two words. Words, their times and flags are kept as they are."""
    out: list[Segment] = []
    for seg in t.segments:
        if seg.channel != channel or not turns:
            out.append(seg)
            continue
        words = seg.words
        if not words:
            voice = _voice_at((seg.start + seg.end) / 2, turns)
            sid = speaker_id(channel, voice) if voice is not None else None
            out.append(seg.model_copy(update={"speaker": sid}))
            continue
        voices: list[int | None] = []
        for w in words:
            a = w.start if w.start is not None else seg.start
            b = w.end if w.end is not None else a
            v = _voice_at((a + b) / 2, turns)
            voices.append(v if v is not None else (voices[-1] if voices else None))
        # A word with no voice takes the next one when it opens the segment.
        first = next((v for v in voices if v is not None), None)
        voices = [first if v is None else v for v in voices]
        start = 0
        for k in range(1, len(words) + 1):
            if k == len(words) or voices[k] != voices[start]:
                part = words[start:k]
                a = part[0].start if part[0].start is not None else seg.start
                b = part[-1].end if part[-1].end is not None else seg.end
                sid = speaker_id(channel, voices[start]) if voices[start] is not None else None
                out.append(seg.model_copy(update={
                    "start": a if start else seg.start, "end": b if k < len(words) else seg.end,
                    "text": " ".join(w.text for w in part), "words": part, "speaker": sid,
                }))  # fmt: skip
                start = k
    # Splitting in place can leave a segment of another channel that starts inside the split
    # one's span sitting after pieces that start later: Transcript wants start-sorted segments
    # (stable: equal starts keep the channel order the caller chose).
    out.sort(key=lambda s: s.start)
    return t.model_copy(update={"segments": out})
