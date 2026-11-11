# Apple Translation on the 406-pair meter: the best number measured — and not the engine

Date: 2026-10-23 · measured by a dedicated session with the project's own harness (local
engines re-run the same day with the app's Translator, CT2 int8 beam 2: every cell
reproduces the figures on record; the quickmt row is transcribed from the 2026-10-22
census, its model no longer on disk). Apple Translation ran on-device through the macOS
Translation framework; runs deterministic, sequential and batch outputs identical, each
segment translated in isolation like the app's own batch path.

## chrF (406 pairs, per slice) + FLORES-200 devtest + speed

| engine | all (406) | lecture | conversation | GitLab | council | FLORES | sent/s | size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base opus-mt | 52.2 | 60.3 | 51.6 | 40.4 | 52.0 | 57.6 | 42 | 87 MB |
| v0 (α0.3) | 53.8 | 61.1 | 52.6 | 46.7 | 51.7 | 57.2 | 41 | 87 MB |
| ToTTI v1 | 54.3 | 61.1 | 53.2 | 47.7 | 52.3 | 57.5 | 40 | 87 MB |
| tc-big zero-shot | 54.7 | 61.5 | 53.8 | 46.9 | 53.2 | 58.9 | 19.6 | 227 MB |
| quickmt-en-it | 53.9 | 60.9 | 53.8 | 45.4 | 52.0 | 59.2 | 38 | ~190 MB |
| teacher MADLAD 3B | 55.8 | 62.4 | 55.1 | 48.8 | 53.8 | 60.4 | 2.1 | 2.8 GB |
| **Apple Translation** | **57.1** | **62.6** | **56.1** | **51.2** | **56.1** | 59.2 | **1.6** | n/d |

Paired bootstrap (1,000 resamples), Apple minus X — the lead is real everywhere:

| vs | all | lecture | conversation | GitLab | council |
| --- | --- | --- | --- | --- | --- |
| v1 | +2.8 [+2.1,+3.5] 100% | +1.6 [+0.5,+2.7] 100% | +2.9 [+1.8,+3.9] 100% | +3.5 [+1.7,+5.4] 100% | +3.9 [+2.1,+6.3] 100% |
| teacher | +1.3 [+0.5,+2.1] 100% | +0.3 [−1.0,+1.5] 68% | +1.0 [+0.2,+2.0] 99% | +2.3 [+0.5,+4.1] 99% | +2.3 [−0.0,+4.8] 97% |
| tc-big | +2.4 [+1.7,+3.0] | +1.1 [+0.3,+2.0] | +2.3 [+1.3,+3.3] | +4.2 [+2.1,+6.6] | +2.9 [+1.3,+4.7] |
| base | +4.9 [+3.9,+5.9] | +2.3 [+1.1,+3.5] | +4.5 [+3.0,+6.0] | +10.8 [+7.3,+14.3] | +4.2 [+2.4,+6.1] |

Speed, measured: 406 sentences in 260 s (1.56/s), FLORES 1,012 in 639 s (1.58/s) —
**25× slower than v1**, the teacher's class. (Machine on AC, not idle; local engines were
measured sequentially without concurrency.)

## Caveats, declared

- The gold is one translator's hand translation; whether it started from a machine draft
  is unknown — a machine-flavoured reference could favour Apple. Not verifiable.
- Apple is a black box: FLORES contamination cannot be ruled out (public set), so its 59.2
  there is indicative only; the 406 pairs are private, so that concern does not apply to
  the domain number.
- Platform constraints: Translation framework needs macOS 15+, headless use macOS 26+
  (below that, only a SwiftUI route with a window); the EN↔IT model is a separate
  on-demand download; the engine can change silently with OS updates — no weights, no
  fine-tuning, no public parameters; Intel not verified.

## Decision (Jacopo, 2026-10-23): the number goes on record, the engine does not change

"Porta il numero a verbale (report e bench) ma il motore batch resta com'è." Apple
Translation enters the books as the **best measured baseline**, not as an engine: the
app's batch path stays on ToTTI v1 — a verifiable, versionable, fixable model consistent
with the project's philosophy, against a sealed engine that can change under an OS update
and cannot run headless before macOS 26. See decision 0046.

**What this does to the positioning claim**: the defensible wording is now *"the best
measured open model in the ~40 sentences/s class on meeting speech; the more accurate
engines measured (MADLAD 3B, Apple Translation) run 20–25× slower"*. The census and
public-sets reports carry the same rewording.
