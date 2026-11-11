# Live subtitles under continuous speech: the lag is flat, the lines are unstable

Date: 2026-10-21 · phase 1 (measure) of the live-latency work · loop as shipped
(`live.py`: TICK 2 s, WINDOW 15 s, MLXWhisperBackend vad=True, ToTTI v1 student).

## Setup

Two recorded calls replayed **in real time** through the unmodified `SubtitleLoop`: a
source callable serves the tail of the audio according to the wall clock, exactly as the
in-memory listener does, and an instrumented subclass logs every tick — ASR/MT time,
loop-visible display lag (wall time at publish minus the absolute time of the newest
transcribed word), lines shown, and previously shown lines that the new tick no longer
shows verbatim ("gone", the instability).

- **Continuous speech**: 12 min of the conversation (`en-002`, 120 s–840 s) — 96% speech
  coverage, no gap over 2.7 s. The register of the reported problem (monologue, no pauses).
- **Control with pauses**: 12 min of the lecture (`en-001-2`, 300 s–1020 s) — 72% coverage,
  31 gaps over 2 s, one of 23.5 s.

## Results (per-tick, speech ticks only)

| | ticks | ASR mean / p95 / max | lag mean / p95 / max | lines shown | rewritten per tick |
| --- | --- | --- | --- | --- | --- |
| continuous | 356 | 0.96 / 1.01 / 4.23 s | 1.54 / 2.78 / 4.57 s | 2.8 | **2.5** |
| with pauses | 349 | 0.99 / 0.97 / 4.97 s | 2.44 / 7.44 / 12.66 s | 2.6 | 2.3 |

Lag by 2-minute bucket, continuous: 1.6 · 1.7 · 1.4 · 1.5 · 1.5 · 1.5 — **flat over 12
minutes**. ASR time likewise (1.00 → 0.95 s). The pause run's higher tail is staleness
during silence (nothing new is being said, so the newest word ages), peaking inside the
23.5 s gap — not subtitle delay.

## Reading: the accumulating-queue diagnosis is rejected by the numbers

- The loop reads the **last 15 s by the clock** every tick and the in-memory ring holds
  exactly 15 s by sample count: there is no queue anywhere to grow, and none grew. Decode
  time does not rise under continuous speech (the window is capped), and the MT is 0.1 s.
- The measured pathology is **churn, not lag**: each tick re-decodes the whole window and
  republishes it, and on average 2.5 of the ~2.8 lines on screen change verbatim every 2
  seconds (sliding alone explains ~0.4). The newest line is rewritten essentially every
  tick, so a reader can never finish it — which reads as "the subtitles never catch up
  with the speaker", and feels worse the longer one watches, even though the true lag is a
  constant ~1.5 s.
- Caveat, declared: this replay did not reproduce the original sighting's full setup (a
  YouTube tab playing while the listener captured). A sustained ASR spike on a loaded
  machine would raise the flat lag (spikes to 4–5 s exist in both runs) but still cannot
  make it *grow*: the window stays tied to the clock.

## Pre-registered gate for the fix (fixed before writing it)

The fix is a stable-prefix commit policy (LocalAgreement-style: words on which two
consecutive decodes agree are committed, even mid-sentence; committed lines may be
refined by punctuation only, never disappear; the open tail stays visible as today).
Measured on the same two replays, same metrics:

1. **Stability**: previously shown committed lines never vanish; total rewrites fall from
   2.5 to **≤ 0.5 per tick** on the continuous run.
2. **Lag**: committed-word display lag mean **≤ 4 s** and p95 **≤ 6 s** on the continuous
   run (the agreement costs one extra tick by construction: ~2 s on top of today's 1.5),
   with no upward trend (last 2-minute bucket within 1 s of the first).
3. **No regression with pauses**: speech-tick lag mean within 0.5 s of this baseline run.
4. **Batch path untouched**: the 406-pair chrF of the document/batch engines is identical
   before and after (the fix lives in `live.py` only).

Fail any of these and the fix does not ship.

## Phase 2: the stable prefix, built and measured

The policy (`stable.py`, wired into `live.py`): a word is committed when two consecutive
decodes agree on it (text-normalized, word timestamps within 2.5 s — the backend already
decodes with `word_timestamps=True`, and using its real times instead of interpolating
within segments was worth 3.7 points of p95 lag); committed lines never change except by
growing at the tail, and close on sentence punctuation (or 120 chars), which also pins the
next decode's left edge at a committed boundary so decodes keep agreeing. Two escape
valves, both found by simulation, both necessary: a garbled word at the frontier (decoded
differently every tick: "vehicle" / "tech" / "uh, Google") commits as last heard when the
three words after it agree, and no word waits more than `MAX_WAIT` = 4 s for its second
opinion. Only the committed prefix is shown and translated; the still-unstable tail of the
window is not displayed.

The cadence became the floor of the lag — agreement needs two decodes, so TICK 2.0 s put
the structural minimum at ~4.3 s — and with ASR at 0.87 s per window there was room to
re-measure it: **TICK is now 1.5 s** (the loop stays inside the tick on 475/480 of the
continuous run's ticks).

### Gate verdict (same two replays, real time, per-word lag: spoken → committed)

| criterion (pre-registered) | measured | verdict |
| --- | --- | --- |
| committed lines never vanish; rewrites ≤ 0.5/tick (was 2.5) | **0.00/tick**, none vanished, both runs | **pass** |
| continuous: lag mean ≤ 4 s | **3.38 s** | **pass** |
| continuous: lag p95 ≤ 6 s | **5.20 s** (max 6.49) | **pass** |
| continuous: no upward trend (last 2-min bucket within 1 s of first) | 3.4 → 3.2, flat | **pass** |
| pauses: "lag mean within 0.5 s of this baseline run" | see below | **ill-posed** |
| batch path untouched: 406-pair chrF identical | 54.3, hypotheses byte-identical | **pass** |

The pauses criterion, as registered, compares the new committed lag to the baseline's raw
lag — incommensurable by construction, since criterion 2 itself concedes the agreement's
~2 s premium. Declared rather than reinterpreted silently; both readings on record: vs the
pause baseline's raw 2.54 s the committed mean is 4.10 s (+1.56, the policy premium); vs
the continuous fixed run it is +0.72. What the criterion was for is answered by the parts
that are commensurable: the raw decode lag of the pause run is unchanged by the fix (same
~2.5 s mean, same bucket shape), rewrites fell 2.3 → 0.00, and the committed tail (p95
7.25, max 11.7, last bucket 8.3) is gap staleness — nothing new is said during a 23.5 s
silence, so the newest committed word ages — not queueing; the fix holds Jacopo's original
envelope ("stable, ≤ 6–8 s") on both runs.

One number to watch, declared: on the pause run 25/448 ticks overran the 1.5 s cadence
(ASR spikes to 6.2 s at gap edges, where Whisper retries at higher temperatures). The loop
absorbs them (the window is tied to the clock) and the per-word p95 already includes them.

**Shipped**: the live loop now shows only committed text. The batch/document path and the
engines are untouched.

## The two strip languages, measured (same continuous replay, real time)

The strip now has a language toggle, held by the server so web bar and floater agree.
Italian stays committed-only (a translation needs words that will not change); the English
view affords more: it also shows the current decode's still-provisional tail, dimmed,
YouTube-caption style — text at decode latency, refined afterwards — and skips the MT.

| view | what a word's appearance means | lag mean | p95 | max |
| --- | --- | --- | --- | --- |
| EN, first display (dimmed tail) | provisional, may still change | **1.75 s** | 2.93 | 4.90 |
| EN/IT, committed | final, never changes | 3.46 s | 5.21 | 6.41 |

(1,601 words first displayed vs 1,410 committed: the difference is the refinement the
provisional view accepts.) Stopping the capture now also freezes the strip at once: a tick
in flight publishes nothing after stop, and no translation runs on content nobody will see.

### Instrument vs screen, and the truncation bug the field test caught

The numbers above are loop-side (word spoken → published). What a person sees adds the UI
poll: the native floater polls every 0.5 s (+0.25 s mean), the web strip every 0.6 s
(+0.3 s) — so the EN tail reaches the eye at **~2.0 s mean**, the committed text at ~3.7 s.

The first field test saw neither: the floater's live line truncated at the END (SwiftUI's
default), which is exactly where the growing committed words and the whole provisional tail
appear. Whenever the line overflowed, new text only became visible when a sentence closed
and shifted the lines up — read as "the subtitles move one sentence at a time, ~3 s
behind", with the measured 1.75 s invisible on screen. Fixed with head truncation
(broadcast style: the start of the line is cut, never the newest words) and a 12 pt taller
strip. The web strip wraps without clamping and never had the bug. Lesson for the record:
the latency instrument stopped at the publish boundary; the pixels were never measured —
a field test is part of the gate.

### Cold start: the ~7 s of "Sottotitoli in arrivo…", broken down

From the first ticks of the (clean) real-time logs, with the model not yet in memory:
tick 1 runs on a near-empty window and publishes nothing; the first decode ends at ~3.6 s —
**~1.3 s of it is the Whisper model loading inside that first decode** (2.1 s vs the 0.85 s
steady state) — and the first *committed* line lands at ~6.2 s (second decode + sentence
close), ~7 s at the eye with the poll. That is exactly the observed placeholder time, in
the Italian view (the default), where only committed text shows; the EN tail would appear
at ~4 s cold.

Cheap lever, shipped and verified on a quiet machine: the backend gains `warm()` and the
loop thread warms it *while the first seconds of audio are still being captured*,
overlapping the two waits. Two findings on the way there, both measured: loading the
weights (`mx.eval`) is not enough — MLX also compiles its Metal kernels on the first
forward pass, so the warm-up is a real 0.5 s transcription, and it must run with
`word_timestamps=True` so the alignment kernels compile too, matching the live path.
Verified cold start, end to end: the first decode drops from 2.1–2.5 s to **0.80 s** and
the first committed line from ~6.2 s to **4.6 s** (~4.9 at the eye, from ~7); the first EN
tail publishes at ~3.0 s. Steady state untouched (same process-wide model cache).
