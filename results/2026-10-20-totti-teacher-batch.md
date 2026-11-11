# ToTTI move D: the teacher becomes the batch engine of the IT view

Date: 2026-10-20 · extension of decision 0038 · quality plan move D (PLAN, "Piano qualità
post-T4").

## What changed

The whole-call IT view now translates with **google/madlad400-3b-mt** (CT2 int8) when its
model is on disk; the **α-0.3 student stays the only live engine** (the 0038 ban on MADLAD
covers latency-bound paths only). The teacher is loaded per job and unloaded after; the
server refuses a teacher batch while a capture or a transcription runs (409) so it never
competes with Whisper for memory or CPU; the translation cache records its engine, and a
cache produced by another engine is re-derived rather than reused; the UI names the engine
next to the IT toggle. For public installs the teacher will be an optional, declared extra
download (asked, never assumed) — the code only checks "model on disk", like the student.

## Numbers (same harness as T1/T4)

| engine | domain chrF (224) | speed |
| --- | --- | --- |
| student α0.3 (live + fallback) | 57.0 | ~37 sentences/s |
| teacher MADLAD-400-3B (batch) | **58.8** | ~1.6 sentences/s |

The teacher's domain score is the T2 re-run measurement (identical model, quantization and
decoding parameters). Batch cost measured on a 29-minute recorded conversation: 96 English
segments translated in 88 s — **~3 minutes per hour of call**, once per call per edit
(per-segment cache reuse within the same engine still applies).

## Why this is the right split

Latency-bound (live CC): the student, 20× faster, within −1.8 chrF of the teacher.
Latency-free (reading a recorded call): the teacher, +1.8 chrF over the student for three
minutes of background work per hour of audio. Both engines are declared in the UI, so a
reader always knows which model produced the Italian.
