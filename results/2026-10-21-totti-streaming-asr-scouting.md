# Streaming ASR engines for the live subtitles: a paper comparison, before any download

Date: 2026-10-21 · follow-up of [2026-10-21-totti-live-latency.md](2026-10-21-totti-live-latency.md).
Nothing here was downloaded or run: this note compares candidates on licence, size, declared
latency and integration cost, so the next step (measuring) can be decided on paper first.
Per CLAUDE.md, any download happens only after an explicit go-ahead, licence checked by hand.

## Why

The live loop decodes a window with Whisper every 1.5 s: even with the stable prefix, the
committed text trails the speaker by ~3.4 s (mean), and the English provisional tail by
~1.5 s — the window cadence is the floor. Natively *streaming* ASR (frame-in, token-out)
has no such floor: it emits words hundreds of milliseconds after they are spoken, refining
a short right-context. For the EN view it would be a direct engine swap; for the IT view it
could feed the translator committed words earlier than the windowed loop does.

## Candidates

### a) sherpa-onnx streaming Zipformer (EN)

- **Already a dependency**: `sherpa-onnx` is in `pyproject.toml` (used for other models),
  so no new runtime enters the project — only model weights would.
- **Licence, checked by hand on the HF model card (2026-10-21)**: `apache-2.0` for
  `csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26` (converted from the icefall
  LibriSpeech checkpoint). The twin `...-en-2023-06-21` adds GigaSpeech training data.
- **Size (int8)**: encoder 68 MB (+ ~1.5 MB decoder/joiner) for the 2023-06-26; 179 MB for
  the LibriSpeech+GigaSpeech 2023-06-21.
- **Declared latency**: chunked streaming at 320–640 ms per chunk (community measurements
  on sibling models; no official figure) — an order of magnitude under the windowed loop.
- **The real question is register, not latency**: the small one is trained on LibriSpeech
  only — read audiobook speech, the opposite of our noisy-meeting domain. The GigaSpeech
  variant (podcast/YouTube audio) is the plausible candidate. Both must be measured on our
  own references (WER against the frozen test calls' verbatim transcripts) before any talk
  of adoption; a fast engine that mishears meetings is a regression, not a win.
- **Integration**: Python-side, same process as the current loop; the stable-prefix layer
  would shrink to bookkeeping (streaming models emit their own stable/endpointed tokens).

### b) Apple SpeechAnalyzer / SpeechTranscriber (macOS 26)

- **Licence**: an OS framework — nothing to download or redistribute, nothing in the
  manifest; fits "everything local" by construction.
- **Declared latency**: Apple's claim is qualitative ("live transcription, low latency");
  third-party reports put the first volatile result at ~0.3–0.5 s and partials under a
  second. No controlled measurement exists in what was found; ours would be the number.
- **Accuracy**: a third-party LibriSpeech-clean test reports ~2.1% WER (better than
  Whisper small, worse than our large-v3-turbo baseline on hard audio, presumably — to be
  measured). English support is first-class; Italian exists but quality is unknown.
- **Integration**: Swift-side — it would live in the capture helper (which already streams
  system audio), emitting words over the same stdout protocol. More plumbing than (a):
  a new helper mode and a word-stream wire format, plus macOS 26 as a hard floor.

### c) Status quo: windowed Whisper + stable prefix (measured)

- Committed (IT): mean 3.4 s, p95 5.2 s. Provisional tail (EN): ~1.5 s. Zero rewrites of
  committed text. Register already proven on our domain; no new licence, no new code.

## Gate, pre-registered before any candidate number (2026-10-21, trigger of 0043 fired)

Jacopo authorized the download after the Soniox comparison; both Zipformers are on disk
and recorded in `data/manifests/models.jsonl`. Declared **before measuring either one**:

1. **Latency** (real-time replay, per-word first emission on the continuous conversation run):
   mean **< 1.0 s** and p95 **< 1.5 s** — clearly under the Whisper path's 1.75 s mean.
2. **Accuracy** (WER on the frozen test set's 406 EN references, clip by clip, same
   normalization): the candidate's WER must be **≤ 1.5× Whisper's** overall **and ≤ 2×**
   on every slice. Relative bounds, fixed before the Whisper baseline itself lands, since
   the references are declaredly Whisper-seeded (reviewed by hand during translation) and
   verbatim — both of which favor Whisper.
3. If one model passes, the EN captions mode moves to it (word-by-word, no windowed tick);
   the IT mode stays on Whisper + stable prefix + ToTTI v1. If none passes, everything
   stays as it is and this report says so.

## Verdict: both candidates fail the gate — the EN captions stay on Whisper

WER on the frozen 406 EN references, clip by clip (same clips, same normalization;
`jiwer`, lowercased, punctuation stripped). Whisper here is the app's own backend
(large-v3-turbo), and the references are declaredly Whisper-seeded — its number is a
favored baseline, which is exactly why the gate was set at 1.5×/2× rather than parity:

| engine | all (406) | lecture | conversation | GitLab | council |
| --- | --- | --- | --- | --- | --- |
| Whisper large-v3-turbo (baseline) | **6.7%** | 11.7% | 4.3% | 6.9% | 1.9% |
| gate ceiling (1.5× / 2× per slice) | 10.0% | 23.4% | 8.6% | 13.8% | 3.8% |
| Zipformer 2023-06-26 (LibriSpeech) | 58.3% | 99.4% | 51.9% | 23.9% | 37.5% |
| Zipformer 2023-06-21 (+GigaSpeech) | 29.0% | 63.9% | 19.3% | **6.6%** | 10.4% |

**Both fail, decisively** — per the pre-registered clause, everything stays as it is: the
EN captions keep the Whisper + stable-prefix path, the IT mode keeps ToTTI v1. The latency
leg was not run: the gate is conjunctive and the accuracy leg had already failed (the same
economy as the 7B teacher rejection).

Two findings worth the record:
- **The failure mode is the non-native speaker, not streaming per se.** A spot check on the
  lecture (Italian-accented English) shows real but garbled hypotheses (a plain sentence came out as a run of unrelated capitalised words). LibriSpeech/GigaSpeech training has no accent robustness; Whisper's breadth
  is what the product actually relies on, and no small streaming model replaces it for free.
- **On native fast meeting speech the GigaSpeech variant beats the baseline** (GitLab 6.6%
  vs 6.9%): streaming transducers are register-sensitive, not uniformly weak. If a
  streaming EN mode is ever revisited, the hunt is for an accent-robust model, and the
  lecture slice is the gatekeeper.

## Reading, and the proposed next step (superseded by the verdict above)

For the EN view, (a) is the cheap experiment with the biggest promised gain: the runtime
is already installed, the licence is verified, and the GigaSpeech variant matches the
register. (b) is attractive as a zero-download native option but costs Swift plumbing and
pins the app to macOS 26+, so it is worth measuring after (a) sets a bar. Proposed, pending
Jacopo's explicit OK (nothing downloaded until then): fetch the two Zipformer variants
(68 MB / 179 MB, Apache-2.0), measure WER on the frozen test calls' English references and
per-word display latency with the harness of the live-latency report, and only then decide
whether the EN view (or more) changes engine.
