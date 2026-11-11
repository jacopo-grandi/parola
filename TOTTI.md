# ToTTI — English calls in Italian

ToTTI (Tool To Translate Italian) is the translation layer of P.A.R.O.L.A.: English calls can be
followed in Italian. The **IT** toggle in a call shows the Italian translation under each
English line, and the **CC** button on the floating recorder writes live Italian subtitles,
during a recording or without recording anything. It is experimental, and everything here runs
on the Mac, like the rest of the app.

This page collects how it works, how it was built and how it compares with the alternatives:
quality, speed and latency, each measured with the same harness. The method behind the
transcript itself is in [`METHODOLOGY.md`](METHODOLOGY.md).

## At a glance

| | ToTTI v1 |
|---|---|
| Model | Opus-MT en-it (78M parameters) distilled from MADLAD-400-3B-MT, interpolated with its base at α = 0.5 |
| Size | 87 MB (CTranslate2 int8) |
| Speed | ~40 sentences per second on the CPU, so Whisper keeps the GPU during a call |
| Quality on calls | chrF 54.3 on 406 hand-translated pairs, +2.1 over the base model |
| General register | chrF 57.5 on FLORES-200, within 0.1 of the base |
| Live subtitles | final Italian text ~3.4 s after the words are spoken; the dimmed English tail ~1.75 s |
| Weights | not published; without them the app falls back to plain Opus-MT, converted by hand ([README](README.md#reading-the-transcripts)) |

## How it works

**A derived layer, like the clean version and the title.** The translation is computed from
the verbatim transcript, cached apart from it, and re-derived when the transcript changes, so a
corrected word corrects its translation too. It never touches the verbatim: each Italian line
sits under the English segment it comes from, with its timestamps.

**The IT view** translates the English segments of a finished call, one sentence at a time,
with the same engine as the live subtitles: one engine to maintain and measure, the same
Italian in both places, no minutes-long batch runs.

**The live subtitles** read the last 15 seconds of the system channel (the others) every 1.5
seconds and transcribe them with Whisper. A word is shown in Italian only once two consecutive
decodes agree on it (a stable prefix): committed lines never change except by growing at the
end, so they can be read to the end. The English view also shows the still-provisional tail,
dimmed, like broadcast captions. Without a recording, the audio stays in a 15-second buffer in
memory and nothing is written to disk. The subtitles are ephemeral: the full-quality
transcription still runs at the end of the call as usual.

## How it is measured

- **The test set: 406 EN→IT pairs.** The sources are verbatim ASR segments, as the app produces
  them; the references were translated by hand by one translator. Stretches with unresolved
  doubts are excluded from pairing. Like the audio, the pairs are never committed.

  | slice | pairs | what it is |
  |---|---|---|
  | lecture | 157 | one of the author's English recordings, Italian-accented English, with consent |
  | conversation | 67 | one of the author's English recordings, with the speakers' consent |
  | GitLab | 86 | a public GitLab team meeting on YouTube, fast native speech |
  | council | 96 | a public council meeting on YouTube, formal procedural English |

- **Translating the production input.** The gold translates the transcript, not the idealised
  speech, so the meter measures translation of what the app actually produces — harder and more
  representative than clean text — and by construction it does not reward fixing transcription
  errors from context.
- **The metrics** are corpus **chrF** (primary: character level, so Italian morphology costs a
  little, not a whole word) and **BLEU**, via sacrebleu, with two counters the scores cannot see
  — subtitles must not invent: **copies** (sources left untranslated) and **inflated**
  (translations over 1.6 times their source's length, which suggests added content). Metrics
  and counters have hand-built unit tests, as the WER does.
- **FLORES-200 devtest** (1,012 professionally translated sentences, CC BY-SA 4.0) is the
  general-register guard: a model that gets better on calls by getting worse at ordinary
  English is refused. The floor is 56.6.
- **Uncertainty** comes from a paired bootstrap over segments, 1,000 resamples: "ahead in 98%"
  means the difference keeps its sign in 98% of the resamples.
- **Gates are fixed before measuring.** A gate that fails stays on record as failed.

## Every engine on the same meter

chrF on the 406 pairs per slice, FLORES-200, and speed on the CPU (CTranslate2 int8, beam 2;
Apple Translation through the macOS Translation framework, on-device):

| engine | all | lecture | conversation | GitLab | council | FLORES | sentences/s | size |
|---|---|---|---|---|---|---|---|---|
| Opus-MT en-it (base) | 52.2 | 60.3 | 51.6 | 40.4 | 52.0 | 57.6 | 42 | 87 MB |
| ToTTI v0 (α = 0.3) | 53.8 | 61.1 | 52.6 | 46.7 | 51.7 | 57.2 | 41 | 87 MB |
| **ToTTI v1** (shipped) | **54.3** | 61.1 | 53.2 | 47.7 | 52.3 | 57.5 | **40** | **87 MB** |
| quickmt-en-it | 53.9 | 60.9 | 53.8 | 45.4 | 52.0 | 59.2 | 38 | ~190 MB |
| Opus-MT tc-big en-it | 54.7 | 61.5 | 53.8 | 46.9 | 53.2 | 58.9 | 19.6 | 227 MB |
| MADLAD-400-3B-MT (the teacher) | 55.8 | 62.4 | 55.1 | 48.8 | 53.8 | 60.4 | 2.1 | 2.8 GB |
| Apple Translation (macOS) | 57.1 | 62.6 | 56.1 | 51.2 | 56.1 | 59.2 | 1.6 | n/d |

How to read it:

- **In its speed class ToTTI v1 is the best open model measured on call speech.** quickmt-en-it
  runs at the same speed and is the better general-register model (FLORES 59.2), but loses on
  calls: −0.4 overall (v1 ahead in 84% of resamples) and −2.3 on the GitLab meeting.
- **The more accurate engines are 20–25× slower.** The teacher and Apple Translation would not
  keep up with live subtitles on the CPU next to Whisper.
- **Opus-MT tc-big is 0.4 ahead with no training at all**, at half the speed and 2.6× the size.
  It was the pre-registered bar for v1, and v1 did not clear it (see below).
- **Apple Translation is the best number measured** (+2.8 over v1, ahead in every resample and
  on every slice) and stays a baseline, not the engine: it is a sealed engine that can change
  with an OS update, with no weights to version or fix, and it runs headless only from macOS 26.
  Its FLORES score is indicative only, since a public set may have been seen in training.

## On public test sets

Measured at home under the same conditions (chrF2 / spBLEU, sacrebleu 2.6.0):

| set | ToTTI v1 | base | tc-big |
|---|---|---|---|
| FLORES-200 devtest (1,012) | 57.5 / 33.6 | 57.6 / 33.9 | **58.9 / 35.4** |
| NTREX-128 (1,997, news) | 60.3 / 37.8 | 60.1 / 37.6 | **62.1 / 40.8** |
| Tatoeba test, decontaminated (27,324) | **75.9 / 61.6** | 73.2 / 57.3 | 74.4 / 59.5 |

On news ToTTI sits where its base sits, mid-table: the interpolation exists to protect the
base's general register, not to beat it. On short conversational sentences it leads even
tc-big. 1,888 Tatoeba test sources (6.5%) appeared verbatim in v1's training anchor and were
dropped from the row above; with them v1 scores 76.0. Published FLORES-200 figures, for
context: NLLB-200-distilled-1.3B 59.9 and 600M 58.3 (excluded by licence: non-commercial),
quickmt-en-it 59.7 on its model card (59.2 in our int8 re-measure), m2m100-418M 54.3
([report](results/2026-10-22-totti-public-sets.md)).

## How the model was built

### A teacher chosen by the numbers

To improve the engine without making it heavier, the path is sequence-level distillation: a
large, cleanly licensed teacher translates in-domain English, and those pairs fine-tune the
small student. The bar was fixed before measuring: the teacher must beat the student by about
+3 chrF on the domain set, otherwise distillation is not worth the trouble. On the 224 pairs of
the time:

| candidate (Apache-2.0) | chrF | BLEU | copies | inflated | time for 224 pairs |
|---|---|---|---|---|---|
| Opus-MT en-it (the student-to-be) | 56.1 | 20.7 | 0 | 0 | 6.0 s |
| **MADLAD-400-3B-MT** | **58.8** | 22.8 | 0 | 0 | 136 s |
| Qwen3-4B-Instruct | 58.0 | 22.4 | 0 | 0 | 309 s |

On the lecture alone neither cleared the bar (the best gap was +2.0). The conversation flipped
the verdict: there MADLAD was +3.5 (51.6 → 55.1). On a clean monologue the small model is
already close; conversational speech is where it suffers and where the teacher clears the
margin. A local LLM was also the riskier choice for subtitles, which must never invent. Later
the 7B MADLAD was measured as a stronger teacher: at twice the size and half the speed it never
cleared zero against the 3B, so the 3B stayed
([report](results/2026-10-19-totti-baseline-en-it.md),
[7B](results/2026-10-20-totti-test-set-v2.md)).

### Distillation data

The teacher translated English meeting speech from the **AMI and ICSI** transcripts (text only,
CC BY 4.0, verified by hand on the archives themselves): 141,155 raw segments, 77,088 sentences
after the filters (4–60 words, exact duplicates dropped), with the verbatim feel — fillers,
truncations, restarts — kept on purpose, because that is what the student will see. The split
is by meeting series, so no speaker is in both train and dev, and overlap with the test set is
impossible by construction. Declared filters on the teacher's output (length ratio outside
[0.4, 1.6], source left untranslated) dropped 1,362 pairs, keeping 75,726.

A **50-pair random sample was reviewed by hand** before any training: 39 approved, 6 corrected,
5 rejected. The review paid for itself twice. It caught an encoding artifact — the teacher
writes `&apos;` for every Italian apostrophe — which was unescaped rather than dropped, saving
5,242 good pairs. And it put a number on the failure no automatic filter catches without a
reference: a garbled ASR source that the teacher "repairs" into fluent, invented Italian — the
90% sample pass is the measure that the rate is tolerable for training. One inherited trait is
declared rather than fixed: the teacher drops fillers, so the student learns subtitle-style
output, not verbatim-preserving translation — an invented "so, uh, we we move the pots inside
before the frost" becomes "quindi spostiamo i vasi dentro prima del gelo", filler and
repetition gone. Acceptable for a layer whose output is subtitles, never the transcript. Final
set: 72,522 train / 3,199 dev pairs
([report](results/2026-10-19-totti-distillation-data.md)).

### v0: the full fine-tunes failed, an interpolation shipped

The guardrail was fixed before training: the student must beat the base on the domain set
**and** stay above the FLORES floor (56.6). Two full fine-tunes (on a rented GPU, about thirty
cents in all; training never runs on the Mac) both failed it:

| run | learning rate | training | FLORES |
|---|---|---|---|
| 1 | 5e-5 | 3 epochs | 57.7 → 54.9 |
| 2 | 2e-5 | ~8 epochs (early stop) | 57.7 → 51.3 |

The lesson: **forgetting tracks the total amount of training, not the learning rate** — the
gentler second run trained longer and drifted further. The fully trained student also scored
worse than the base on the hand-made set (55.1 against 56.1): past a point, the gain on the dev
set is imitation of the teacher's phrasing, not quality.

What shipped first is a **weight interpolation** between the base and the trained student,
θ(α) = α·student + (1−α)·base, each point converted to int8 and scored with the same harness
(224 pairs):

| model | FLORES | domain |
|---|---|---|
| base | 57.6 | 56.1 |
| **α = 0.3** (v0) | **57.2** | **57.0** |
| α = 0.5 | 56.0 | 56.6 |
| α = 0.7 | 54.3 | 55.9 |
| α = 1.0 (the full student) | 51.3 | 55.1 |

α = 0.3 passed the gate, ahead of the base in 1,000 of 1,000 resamples
([report](results/2026-10-20-totti-t4-student.md)). On the widened 406-pair set v0 kept a real
gain (+1.6, +6.3 on the GitLab meeting) but lost its edge on the council meeting (−0.3, within
noise): formal register that the distilled data never covered.

### v1: a human anchor, the model the app uses

v1 keeps the same 78M architecture — the live engine's speed is the requirement — and changes
the data: training restarts from the base on **97,522 pairs**, the 72,522 distilled ones plus
**15,000 Tatoeba and 10,000 Europarl** human translations as an anchor (26% human). The anchor
did what it was added for: at α = 1 the FLORES drift fell from −6.3 to −0.4. Interpolated with
the base and scored on the 406 pairs:

| model | all | lecture | conversation | GitLab | council | FLORES |
|---|---|---|---|---|---|---|
| base | 52.2 | 60.3 | 51.6 | 40.4 | 52.0 | 57.6 |
| v0 (α = 0.3) | 53.8 | 61.1 | 52.6 | 46.7 | 51.7 | 57.2 |
| v1, α = 0.3 | 54.1 | 61.2 | 52.9 | 47.4 | 52.1 | 57.7 |
| **v1, α = 0.5** (shipped) | **54.3** | 61.1 | 53.2 | 47.7 | **52.3** | **57.5** |
| v1, α = 0.8 | 54.3 | 61.2 | 53.4 | 48.3 | 51.6 | 57.0 |
| v1, α = 1.0 (the full student) | 53.6 | 60.1 | 52.9 | 48.1 | 50.3 | 56.1 |

v1 at α = 0.5 is **+2.1 chrF over the base** (CI [+1.1, +3.1], ahead in every resample; +7.3 on
the GitLab meeting) and **+0.5 over v0** (ahead in 98%), with the council slice recovered and
FLORES within 0.1 of the base. It did **not** pass the gate fixed before training — 54.7, what
Opus-MT tc-big scores with no training at all — and the failure stays on record: v1 ships as a
strict improvement on v0 at the same speed, not through the gate. Capacity, not data, is now the
wall. The app loads v1, then v0, then the base, whichever is on disk
([report](results/2026-10-21-totti-v1-alpha-sweep.md)).

## Live subtitles: latency and stability

Two recordings replayed in real time through the live loop, 12 minutes each: continuous speech
(the conversation, 96% speech) and a control with pauses (the lecture, 31 gaps over 2 s). The
first version re-decoded the window every tick and republished it: the lag was flat, but the
lines kept changing under the reader's eyes. The stable prefix fixed that, against gates
written before the fix:

| continuous speech | before (every decode shown) | after (stable prefix) | gate |
|---|---|---|---|
| lines rewritten per tick | 2.5 | **0.00** | ≤ 0.5 |
| lag, spoken → shown, mean | 1.54 s | 3.38 s | ≤ 4 s |
| lag, p95 | 2.78 s | 5.20 s | ≤ 6 s |
| trend over 12 minutes | flat | flat (3.4 → 3.2 s) | no growth |
| chrF of the batch path | 54.3 | 54.3, byte-identical | unchanged |

Agreement between two decodes costs about one tick by construction; the tick went from 2 s to
1.5 s to pay part of it back. What a word's appearance means in each view (continuous speech;
the screen adds about 0.25 s of polling):

| view | the word is | lag mean | p95 | max |
|---|---|---|---|---|
| English, dimmed tail | provisional, may still change | **1.75 s** | 2.93 s | 4.90 s |
| English and Italian, committed | final, never changes | 3.46 s | 5.21 s | 6.41 s |

Cold start: warming Whisper while the first seconds of audio are captured brought the first
committed line from ~6.2 s to **4.6 s** after the start. With pauses, the committed text can age
up to ~12 s, but only during silence: nothing new is being said
([report](results/2026-10-21-totti-live-latency.md)).

### Streaming speech recognition, tried

A natively streaming recogniser has no window cadence, so it could show words sooner. Two
sherpa-onnx Zipformers (Apache-2.0) were measured for the English view against a gate fixed in
advance (WER at most 1.5× Whisper's overall and 2× on every slice), on the same 406 clips:

| engine | all | lecture | conversation | GitLab | council |
|---|---|---|---|---|---|
| Whisper large-v3-turbo (the app's) | **6.7%** | 11.7% | 4.3% | 6.9% | 1.9% |
| Zipformer, LibriSpeech | 58.3% | 99.4% | 51.9% | 23.9% | 37.5% |
| Zipformer, + GigaSpeech | 29.0% | 63.9% | 19.3% | **6.6%** | 10.4% |

Both fail, decisively: they do not survive Italian-accented English. On fast native speech the
GigaSpeech one beats Whisper, so the search, if it reopens, is for an accent-robust model. The
references are Whisper-seeded, which favours Whisper and is why the gate is relative
([report](results/2026-10-21-totti-streaming-asr-scouting.md)).

## What was tried and did not ship

- **The teacher behind the IT view.** For a while the IT view translated finished calls with
  MADLAD-400-3B (+1.5 chrF, about 3 minutes of background work per hour of call). It left the
  app: one engine, no multi-gigabyte model to load, the same Italian in the view and in the subtitles.
- **Opus-MT tc-big as the live engine**: 0.4 better, at half the speed and 2.6× the size; the
  small student's speed was the requirement.
- **quickmt-en-it**: same speed, better on news, worse on calls (above).
- **Apple Translation**: the best quality measured, a baseline only (above).
- **Context at inference.** Translating each sentence with its neighbours, then cutting the
  neighbours' translation back out:

  | arm | chrF | cut failed | FLORES | verdict |
  |---|---|---|---|---|
  | sentence alone (control) | 54.3 | — | 57.5 | reproduces v1 |
  | previous + current | 53.8 | 15.3% | 56.3 | worse |
  | previous + current + next | 54.7 | 29.8% | 57.2 | fails: 3 in 10 fall back |

  The +0.4 of the window is carried by a cut that fails three times in ten, so it is not
  counted as a win. Marian merges and reorders concatenated sentences instead of translating
  them one by one; context goes into the next training recipe instead
  ([report](results/2026-10-22-totti-context-inference.md)).

## Limits

- **A small, single-translator meter.** 406 pairs, four recordings, two of them the author's
  own, every reference by one person; whether a reference started from a machine draft is not
  known.
- **Source noise counts as translation error.** The references fix recognition errors that
  every engine translates alike, so part of every gap is ASR, not translation.
- **English to Italian only.** Italian to English is the same loop in the other direction, not
  started.
- **No public weights.** The distilled model is not published; a fresh install gets plain
  Opus-MT, converted by hand.
- **One machine.** Speeds are measured on an M4 Pro, on the CPU, one engine at a time.

## Models and data

| What | Used for | Licence |
|---|---|---|
| Opus-MT en-it (Helsinki-NLP) | the base and the student's starting point | Apache-2.0 on the model card; CC BY 4.0 on the OPUS-MT repository |
| MADLAD-400-3B-MT | distillation teacher (training only) | Apache-2.0 |
| AMI and ICSI transcripts | distillation sources (training only) | CC BY 4.0 |
| Tatoeba | human anchor of v1 (training), public test set | CC BY 2.0 FR |
| Europarl | human anchor of v1 (training only) | no known copyright restrictions (statmt.org) |
| FLORES-200 devtest | general-register guard | CC BY-SA 4.0 |
| NTREX-128 | public test set | CC BY-SA 4.0 |
| Qwen3-4B-Instruct | teacher comparison only | Apache-2.0 |
| quickmt-en-it | comparison only | CC BY 4.0 |
| sherpa-onnx streaming Zipformers | comparison only | Apache-2.0 |
| Opus-MT tc-big en-it | comparison only | not recorded yet in the manifest |
| Apple Translation | comparison only | part of macOS, nothing downloaded or redistributed |

None of these is bundled with the app. The training package (Colab notebooks, configuration,
the scoring that runs at home) is in [`training/totti/`](training/totti/README.md); every
measurement above has its report in [`results/`](results/).
