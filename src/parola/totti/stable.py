"""The stable prefix of the live window (LocalAgreement-2, as in whisper-streaming).

Phase 2 of the live-latency work (results/2026-10-21-totti-live-latency.md): the measured
pathology of the v0 policy is churn — every tick re-decodes the sliding window and rewrites
nearly every line on screen. Here a word is *committed* once two consecutive decodes agree
on it, even mid-sentence, and committed words are never taken back: the shown lines only
grow, then close on sentence punctuation. The cost by construction is one extra tick of
delay; the pre-registered gate in the report bounds it.

Decodes are matched on a shared clock: the caller anchors each decode with the absolute
time of the clip's first sample (its wall clock at the tail minus the seconds served), so
words keep their identity while the window slides under them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from parola.schema import Segment

# The same word may drift this much between two decodes of the sliding window and still
# count as agreed: segment timestamps move as Whisper re-cuts the window.
AGREE_TOLERANCE = 2.5
# Words ending at or before the frontier plus this guard were already committed: a re-decode
# that shifts them slightly must not commit them twice.
FRONTIER_GUARD = 0.2
# No word waits longer than this for a second opinion: past it, the current decode's word
# commits alone (by then it has been decoded two or three times; the latest reading wins).
# Without a cap, one unstable word at the head of the pending region blocks every word
# behind it until they scroll off the window and are lost (the stall the offline simulation
# showed). The caller turns it into an absolute deadline: ``total - MAX_WAIT``.
MAX_WAIT = 4.0
# A line longer than this closes even without sentence punctuation, so one run-on sentence
# cannot hold the open line (and its re-translation) forever.
MAX_LINE_CHARS = 120
_SENTENCE_END = (".", "?", "!", "…")


@dataclass
class _Word:
    end: float  # absolute seconds on the caller's clock
    text: str


def _words(segments: list[Segment], offset: float) -> list[_Word]:
    """Every word of ``segments`` with an absolute end time.

    The backend's word timestamps are used when a segment carries them (mlx-whisper decodes
    with ``word_timestamps=True``): drift-free times are what lets two decodes agree.
    Without them, a word's time is interpolated within its segment.
    """
    out: list[_Word] = []
    for s in segments:
        if s.words and all(w.end is not None for w in s.words):
            out.extend(_Word(end=offset + w.end, text=w.text) for w in s.words)
            continue
        parts = s.text.split()
        if not parts:
            continue
        step = max(s.end - s.start, 0.0) / len(parts)
        out.extend(_Word(end=offset + s.start + step * (i + 1), text=w)
                   for i, w in enumerate(parts))  # fmt: skip
    return out


def _norm(text: str) -> str:
    return text.strip(".,;:!?…\"'()[]«»").lower()


@dataclass
class StablePrefix:
    """Commits the words two consecutive decodes agree on; committed lines never vanish."""

    frontier: float = 0.0  # absolute end of the last committed word
    # Where the next decode should start: the last closed line's end. Decoding from a
    # committed sentence boundary keeps the left context identical between decodes, which is
    # what lets consecutive decodes agree (a freely sliding window re-cuts every tick).
    anchor: float = 0.0
    closed: list[str] = field(default_factory=list)
    _open: list[str] = field(default_factory=list)
    _prev: list[_Word] = field(default_factory=list)

    def update(self, segments: list[Segment], offset: float,
               deadline: float = float("-inf")) -> None:  # fmt: skip
        """One decode of the window, whose clip starts at ``offset`` on the shared clock.

        ``deadline``: words ending at or before it have waited long enough and commit
        without a second opinion (the caller passes ``total - MAX_WAIT``; the default
        never force-commits).
        """
        pending = [w for w in _words(segments, offset) if w.end > self.frontier + FRONTIER_GUARD]
        done = self._agreed_prefix(pending, self._prev, deadline)
        if done == 0:
            done = self._skip_garbled_head(pending, deadline)
        rest = pending[done:]
        while rest and rest[0].end <= deadline:
            self._commit(rest.pop(0))  # no second opinion came in time: take this one
        self._prev = rest

    def _agreed_prefix(self, pending: list[_Word], prev: list[_Word], deadline: float,
                       start: int = 0, prev_start: int = 0) -> int:  # fmt: skip
        """Commits the agreed run of ``pending[start:]`` vs ``prev[prev_start:]``; count done."""
        done = start
        for now, before in zip(pending[start:], prev[prev_start:], strict=False):
            agreed = (_norm(now.text) == _norm(before.text) and _norm(now.text)
                      and abs(now.end - before.end) <= AGREE_TOLERANCE)  # fmt: skip
            if not agreed and now.end > deadline:
                break
            self._commit(now)  # agreed, or about to scroll out: this decode is the last word
            done += 1
        return done

    def _skip_garbled_head(self, pending: list[_Word], deadline: float) -> int:
        """A garbled word at the frontier decodes differently every tick and would block the
        stable words behind it forever. When the first one or two words disagree but the
        next three agree (with up to two words of misalignment), the head is committed as
        this decode heard it and the agreed run follows."""
        for i in (1, 2):
            for j in (1, 2):
                anchored = (len(pending) >= i + 3 and len(self._prev) >= j + 3 and all(
                    _norm(pending[i + k].text) == _norm(self._prev[j + k].text)
                    and _norm(pending[i + k].text)
                    and abs(pending[i + k].end - self._prev[j + k].end) <= AGREE_TOLERANCE
                    for k in range(3)))  # fmt: skip
                if anchored:
                    for w in pending[:i]:
                        self._commit(w)
                    return self._agreed_prefix(pending, self._prev, deadline, i, j)
        return 0

    def _commit(self, word: _Word) -> None:
        self.frontier = word.end
        self._open.append(word.text)
        line = " ".join(self._open)
        if word.text.endswith(_SENTENCE_END) or len(line) >= MAX_LINE_CHARS:
            self.closed.append(line)
            self._open = []
            self.anchor = self.frontier  # the next decode can start at this clean boundary
            # Only the recent lines matter to the display: a call does not grow this list.
            del self.closed[:-50]

    def lines(self) -> list[str]:
        """The committed lines, oldest first; the last one may still be growing."""
        return [*self.closed, " ".join(self._open)] if self._open else list(self.closed)

    def pending(self) -> str:
        """The uncommitted words of the last decode: the provisional tail, still changeable.

        An English view may show it (dimmed) for latency, YouTube-caption style; the
        translated view never does — Italian lines are committed-only.
        """
        return " ".join(w.text for w in self._prev)

    def rest(self, at: float) -> None:
        """A fully silent window: what was shown has been read, the display starts afresh.

        The clock moves to ``at`` (the newest audio), so nothing heard before the silence
        can come back, and the next decode starts there.
        """
        self.frontier = at
        self.anchor = at
        self.closed = []
        self._open = []
        self._prev = []
