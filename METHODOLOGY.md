# Methodology

How P.A.R.O.L.A. decides what a transcript should be, how it is measured and why each model and
setting was chosen. Each number links to the report it comes from in [`results/`](results/).

## Summary

P.A.R.O.L.A. transcribes Italian and English speech, calls and imported files, entirely on an
Apple Silicon Mac. It keeps what was said, as it was said: repetitions, truncations and false
starts stay, and grammar is not fixed. Whisper large-v3-turbo writes the transcript (fidelity
WER 4.8% in Italian and 5.3% in English on adjudicated references), a second model, Parakeet v3,
marks the words it does not confirm (66% of turbo's errors flagged by marking 12% of the words),
and the person reviews only those. Every correction is logged next to what the model had
written, and these logs become a personal glossary that suggests the next corrections. Titles
come from TF-IDF key phrases scored against the language, without a language model. English
calls can be read in Italian: an 87 MB local model translates each line, a student distilled from
a larger teacher on meeting speech (chrF 52.2 to 54.3 on 406 hand-translated pairs). Calls are recorded on two channels, so "me" and "the others"
are never guessed; within a channel, voices are told apart by sherpa-onnx with our own spectral
clustering (diarization error 30% on two AMI meetings). An hour of audio takes about 8 minutes on
an M4 Pro, with a memory peak of about 6 GB. The test set is small (0.76 hours of adjudicated
references), and the limitations are listed at the end.

## Pipeline

Each stage, with what it costs on an hour of audio (en-001, M4 Pro):

| Stage | What it does | Time per hour |
|---|---|---|
| Capture or import | Two channels from Core Audio taps, or any audio or video file, to 16 kHz mono | real time / seconds |
| Language | Italian or English, from 5 windows spread over the audio, unless the person chose it | 2 s |
| Voice activity | Finds the stretches with sound; text over silence is dropped | seconds |
| Transcript | Whisper large-v3-turbo, word timestamps; the text is shown as it grows | 3:00 |
| Doubtful words | Parakeet TDT 0.6B v3 as a second opinion, aligned word by word | 1:14 |
| Echo, segments | Drops the others' voice leaking into the microphone; joins short segments | seconds |
| Who speaks | sherpa-onnx pieces, regrouped by spectral clustering | 3:23 |
| Title, storage | Key-phrase title; lossless FLAC | seconds |

## Principles

- **Everything runs on the Mac.** The network is used once, to download the models. No cloud API,
  no telemetry.
- **The verbatim transcript is the source of truth.** Anything derived from it (a clean version,
  key phrases, a title) can only remove or normalise, never add, and every word traces back to a
  timestamp in the audio.
- **Fidelity over pure verbatim.** Losing an "ehm" is acceptable; rewriting what was said is not.
  Grammar mistakes stay as spoken.
- **The machine flags, the person decides.** Words the model may have got wrong are marked, and
  only the person corrects them. Nothing is corrected automatically.
- **Measure before changing.** No model or parameter changes without numbers on the test set.

## Test set and references

- Four recordings with the speakers' consent: it-001 (85 min), it-002 (17 min), en-001 (60 min)
  and en-002 (29 min). They are never committed. Only the manifest is in the repository, with
  each source and its licence.
- The transcription conventions (fillers, truncated words, repetitions, overlaps) are in
  [`docs/conventions.md`](docs/conventions.md). The annotation workflow is in
  [`docs/annotation.md`](docs/annotation.md).
- **References are "silver"**: a model draft checked word by word by a person.
  The drafts came from Whisper turbo, which
  biases the comparison in turbo's favour. To reduce that bias, it-002 and en-002 were
  **adjudicated**: wherever turbo and CrisperWhisper disagreed, a person listened and chose.
  Clear cases (repetitions, truncated words, extra words actually spoken) were accepted by a
  rule, which was itself checked on samples. In a 30-clip listening audit, 93% of the words
  CrisperWhisper added were really spoken
  ([audit](results/2026-10-15-audit-crisper.md)).
- Current numbers use the two adjudicated files: 0.76 hours. That is small, so the confidence
  intervals are wide and the report shows them.

## Metrics

Three word error rates, from strictest to most lenient:

| Metric | What counts as an error |
|---|---|
| **Fidelity WER** (primary) | Words that are not the ones spoken. Fillers, repetitions, truncated words and false starts are ignored. |
| Literal WER | Like fidelity, but repetitions and truncated words count. |
| Verbatim WER | Everything counts, fillers included, mapped to one spelling per filler. |

Plus filler recall and precision, the share of substitutions (the direct sign of rewording) and
the real-time factor. Numbers are normalised the same way on both sides (for example digits to
words). Confidence intervals come from bootstrapping over segments. Every metric has
hand-built unit tests.

## Models

On the adjudicated references ([report](results/2026-10-15-adjudicated.md); fidelity WER, 95% CI):

| System | Italian | English | Speed (RTF, M4 Pro) | Licence |
|---|---|---|---|---|
| **Whisper large-v3-turbo** (default) | **4.8** [3.6–6.3] | 5.3 [4.3–6.5] | 0.047 | MIT |
| CrisperWhisper 2.0 turbo | 8.5 [6.4–10.7] | **2.7** [2.0–3.6] | 0.159 | non-commercial |
| Parakeet TDT 0.6B v3 | 16.7 | 10.1 | 0.013 | CC BY 4.0 |
| Whisper large-v3 | 27.2 | 21.9 | 0.138 | MIT |

- **Turbo is the default**: the best in
  Italian, close in English, fast and freely licensed. It drops almost all fillers, which the
  fidelity metric accepts.
- **CrisperWhisper is optional**: the best in English and the most verbatim (96% filler recall),
  but its weights are non-commercial and it is worse in Italian.
- **What did not help:** a "disfluency" prompt (fidelity WER from 5.1 to 7.8 overall) and a
  personal glossary given to Whisper as a prompt (5.1 to 8.6–9.5,
  [report](results/2026-10-16-glossary.md)). The glossary fixed about half of the domain terms
  but made everything else worse. Model runs use a fixed random seed, because Whisper's
  temperature fallback otherwise samples at random.
- **Fine-tuning is parked.** The plan is an English-first LoRA on freely licensed data.

## Language

Whisper picks one language and transcribes the whole file in it. If it picks wrong, it does not
transcribe: an hour of English came out as an Italian translation. Its own guess looks at the
first 30 seconds only, and our earlier probe (the first 30 seconds of speech glued together)
said Italian for an Italian speaking English. Now the guess is the average of Whisper's language
probabilities on 5 raw 30-second windows that start on speech, spread over the whole recording,
and only Italian and English can win. On the four test-set files: 4 of 4 right, against 3 of 4,
in 1.9 seconds; the language of it-002 and en-002 does not change, so their error rates do not
either ([report](results/2026-10-16-language-detection.md)). When a file is imported the app asks its
language first (Italiano, English or detect), since the person usually knows it.

## Doubtful words and the correction loop

The core idea: two models that fail in different places point at the words to check, the person
checks only those, and every correction is kept, so the corrections become a corpus that suggests
the next ones.

### Two transcripts, their disagreements flagged

Each call is transcribed by Whisper large-v3-turbo (the transcript the person reads) and by
Parakeet TDT 0.6B v3, a second opinion with a different architecture (a transducer) and different
training data.

1. **Same tokens as the metric.** Both transcripts are cut into fidelity tokens: fillers and
   repetitions are left out and numbers and spellings are normalised as in the fidelity WER.
   A disagreement on an "ehm" is not a doubt.
2. **Alignment.** The two token sequences are aligned by edit distance, with the same alignment
   the WER uses.
3. **Flags.** A turbo word is doubtful when Parakeet replaces it or does not have it, and both
   turbo words around a place where Parakeet has extra words are doubtful (something may be
   missing there). No window around the flags by default.

Which second opinion was measured against the adjudicated references of it-002 and en-002, where
6.5% of turbo's words are errors (374 of 5761), so random flags would be right 6.5% of the time
([report](results/2026-10-16-doubt-analisi.md)):

| Second opinion | Licence | Turbo errors caught | Flags that are errors | Words to check |
|---|---|---|---|---|
| turbo, another seed | MIT | 2–29% | 48–53% | 0.3–4% |
| turbo with a prompt | MIT | 40% | 38% | 6.9% |
| **Parakeet v3** (chosen) | CC BY 4.0 | **66%** | **35%** | **12.4%** |
| Whisper large-v3 | MIT | 57% | 25% | 14.7% |
| Parakeet and turbo with a prompt | free | 79% | 31% | 16.3% |

Parakeet catches two errors out of three by marking one word in eight, its flags are five times
better than chance, and it adds about a minute per hour of audio. About a third of the errors
stay invisible: the places where both models make the same mistake. On a real 30-minute call,
recorded with consent, every error noticed in review had been flagged.

### Review

In the app the person jumps from one doubtful passage to the next (N and P), hears just that
excerpt (until one second after its last word), and confirms it (Enter on the unchanged text) or
types the correction. Any other word can be edited with a double click, and everything can be
undone.

### Corrections become a corpus

Every edit is logged in the transcript with what the model had written (`before`), what the
person wrote (`text`), where, and its kind (correction, confirmation, voice change); the model's
untouched output is kept next to it as `<name>.asr.json`. So each reviewed call adds pairs of
"what the model heard" and "what was said", and labels its flags as right or wrong.

Today this corpus feeds a personal glossary:

| Step | Rule | Parameter |
|---|---|---|
| Which corrections | Only terms: a name or acronym (a capital inside the word, or all capitals with two letters or more), a word with digits or a hyphen, or a word rare in the language of its sentence; case and punctuation changes are ignored | rare: Zipf < 3.5 (wordfreq); up to 4 words |
| When it is learned | After the same term was corrected that many times: once may be a slip | 2 corrections |
| When it is suggested | When the person opens a doubtful word: first the terms whose misheard form it is exactly ("orto lap" was corrected to "Orto-Lab" before), then terms with a close spelling | letter similarity ≥ 0.75 (difflib) |
| What is shown | The whole span with the term in place, best first; the person accepts one or not | up to 3 |

The glossary is read from the edit logs each time, so an undone correction is forgotten with it.
It starts empty on every install, lives only next to the calls (never in git), and never changes
the text by itself. Giving the same terms to Whisper as a prompt was measured and rejected: it
fixed about half of the domain terms but raised the fidelity WER from 5.1 to 8.6–9.5
([report](results/2026-10-16-glossary.md)).

Not measured yet, and the reason to keep the corpus: the confirmations measure the flags'
precision on real calls instead of on the test set; reviewed calls can become references; with
the participants' consent, the pairs are training data for the fine-tuning that is parked.

## Recording calls

- **Capture:** Core Audio process taps record the microphone ("Io") and the system audio
  ("Altri") into two files, so speakers are told apart by channel rather than guessed. No
  virtual audio driver is needed.
- **Echo:** a microphone segment whose words are mostly the same as the other channel's at the
  same time (60% or more) is your speakers leaking into the microphone, and is dropped.
- **Device changes:** headphones plugged in or out, or AirPods dropping from 48 to 24 kHz when
  their microphone starts, restart that side on the device in use. The files keep the format
  they started with (the new audio is converted) and the restart's moments are filled with
  silence, so the two files stay aligned with each other and with the clock.
- **Long digital silence:** the system side is exact zeros whenever no app plays anything,
  and Whisper skipped the speech that came after 23 s of it. Runs of exact zeros over 3 s are
  shortened to 1 s before decoding and the times mapped back. Microphone audio never holds
  them (the test set's longest run is 0.04 s), so the measured error rates do not change
  ([report](results/2026-10-18-digital-silence.md)).
- **Voice activity detection:** only stretches with sound are transcribed, to keep Whisper from
  inventing text over silence. Fidelity is unchanged: 5.1 with and without VAD
  ([report](results/2026-10-15-vad.md)). Earlier versions that shifted decoding windows lost
  speech (up to 27.9%) and were dropped ([analysis](results/2026-10-15-vad-analisi.md)).

## Who speaks

The others' channel and imported files can hold several voices. After transcription, sherpa-onnx
(pyannote segmentation 3.0, MIT; WeSpeaker VoxCeleb ResNet34-LM, CC BY 4.0; about 70 MB in all,
CPU only) finds where someone speaks; its pieces are regrouped by our own spectral clustering
(NME-SC) over 3-second voice prints. On two AMI test meetings this gets the diarization error
rate from 47% (sherpa-onnx alone) to 30%; pyannote reports 17-19% on the whole AMI test set.
Voices show as "Altri 1", "Altri 2"..., can be named, and every turn can be moved to another
voice and undone ([results](results/2026-10-16-diarization-sherpa.md)).

## Time and memory

An hour of audio takes about 7 minutes 50 seconds on an M4 Pro (stages in "Pipeline"). Only one
speech model is in memory at a time, and none between calls: for a 6-minute file the peak goes
from 11.5 GB to 6.1 GB and the waiting server from 10 GB to 0.27 GB, with the same time and the
same transcript.

The person does not wait for the end to read. Each 30-second window Whisper decodes is shown as
soon as it is done, then the doubtful words, then the voices; the text can be corrected once it is
final. The windows are read from Whisper's own decoding loop without changing it: on en-002 the
final transcript is identical, word for word with the same times, doubtful words and voices,
to the one made before.

## Storage

After transcription each channel is stored as FLAC at 16 kHz mono 16-bit: the very signal the
models get, losslessly, at about 100 MB per hour of call instead of about 2 GB. A lossy codec
was measured and rejected: re-encoding the test set as AAC at 32-48 kbps raised turbo's fidelity
WER from about 5% to 9-12%.

Clicking a word plays the audio from there, so the player must land on the right second. The
native app's window is WebKit, and WebKit cannot seek in FLAC: on an hour-long test file with a
beep on every minute, after a jump the beep came tens of seconds late or not at all, with or
without a FLAC seek table, while in WAV it came where expected; Chrome was exact (±0.02 s) on
both. Calls stay FLAC on disk, and the player gets a WAV copy decoded when the call is opened
(0.6 s per hour), of which only the last three are kept
([report](results/2026-10-16-flac-seeking.md)).

## Imported files

An audio or video file dropped into the app goes through the same pipeline as a recording:
conversion to 16 kHz mono, VAD, turbo, Parakeet for the doubtful words, segment merging and the
title, after the language question above. Every voice is on one track (channel `mixed`), so
there is no "Io / Altri" split and no echo removal; the voices are told apart as in "Who speaks".

## Derived layers

- **Clean version:** only removes material (fillers, repetitions, false starts) by explicit rules,
  never rewrites. A trained classifier is paused.
- **Title:** see the next section.
- **Italian translation:** see "Translation into Italian (ToTTI)".

## Titles from key phrases

A call gets a suggested title from its own words, without a language model: a RAKE-style
extraction (Rose et al., 2010) with TF-IDF against the language. Every rule is general: lists of
closed classes and frequency thresholds, nothing tuned on a particular call. The parameters were
chosen by hand and are declared here; none was tuned on the six transcripts used to check them,
except the spoken-frequency bar, revised later with the measurement procedure of §5 (see there).

### 1. Candidate phrases

The text is cut into runs at every word that cannot belong to a topic:

| Cut at | Detail |
|---|---|
| Closed classes, Italian and English | Articles, prepositions (articulated ones too), pronouns, conjunctions, the forms of *essere, avere, stare, potere, dovere, volere* and of *be, have, do* and the modals, listed in full |
| Fillers and discourse markers | The fillers of [`docs/conventions.md`](docs/conventions.md); markers such as *allora, quindi, cioè, insomma, praticamente, basically, anyway*; greetings (*ciao, salve, hello, goodbye*): they carry the flow of a call, never its subject |
| Number words | *uno, due, … mille, primo*; *one, two, … first* |
| Adverbs | Words ending in *-mente* (over 7 letters) or *-ly* (over 5 letters, so "Italy" stays) |
| Very frequent words | Zipf ≥ 6 in Italian **or** English (more than one in a thousand words: *cosa, think, because*), the frequency-based stop list RAKE itself suggests; either language, because calls mix them |
| Spoken hedges | Zipf ≥ 5.4 in the language of the segment itself. wordfreq counts written text, and the hedges of meeting speech (*kind, things, look*; *lavoro, momento*) sit at Zipf 5.4–6 while said constantly, so their term frequency beats any real topic. Topic words sit below the bar (*data* 5.34, *sicurezza* 5.30) |
| Other | Punctuation, words with digits, words under 3 letters unless an acronym, and doubtful words |

A doubtful word is kept only if it was transcribed the same way at least 3 times: once it may be
a mistake, ten identical "baking soda" are the subject of the call.

Consecutive repeats of one word count as one saying: a decode loop ("Ciao Ciao Ciao…", seen on a
real call's goodbyes) must not inflate a word's frequency, nor lend it the mid-sentence capital
that would make it pass for a name.

A run keeps 1 to 3 content words. "di"/"of" (and the articulated forms of "di") may join two
content words inside it ("raccolta delle olive", "number of seeds"); elided forms are split
("dell'impatto" gives "impatto"). A run longer than 3 content words counts word by word, and the
stretches of two or more content words inside a run also count ("vaso rialzato" inside "vaso
rialzato di ottobre").

### 2. Only noun phrases

A phrase counts as a topic if at least 10% of the times it is said it comes right after a
determiner (article, articulated preposition, demonstrative, possessive, quantifier: "il basilico",
"the ripest tomato"), or if it is a name or an acronym, which go without one ("Portogallo",
"DOP"). This keeps out discourse words such as "esatto" or "ciao" without listing them.

### 2-bis. Grammar, where frequency cannot tell

Frequency cannot separate the furniture of meetings from topics — measured on real calls, the
bands overlap fully (*attimo* 4.62 vs *crescita* 4.89; *prossima* 4.92 vs *product* 4.95). Two
small part-of-speech taggers close the gap, run locally once per call: spaCy `en_core_web_sm`
(MIT, installed with the app) and `it_core_news_sm` (CC BY-NC-SA 3.0, **non-commercial**, so
offered at install and never a default).

- A phrase is dropped when a tagged word in it is not a noun, proper noun, adjective or
  foreign word: the verbs of running a meeting (*mando*, *scrivo*) never title.
- A phrase of adjectives alone (*prossima*) stays ranked — a longer phrase may replace it
  (*legal* carries *legal reasoning* up) — but on its own never reaches the title.
- A phrase that is nothing but a person's name (*Maria*) is dropped; a person may still
  specify a topic (*progetto di Maria*).
- Sentences mix the two languages, so a tagger only judges the words that are more its
  language's than the other's (by Zipf): *kick off* in an Italian sentence keeps the benefit
  of the doubt rather than taking a junk tag from the Italian model.

Without the taggers the titles fall back to the frequency filters alone.

### 3. Score

Singular and plural are counted together (a final vowel after a consonant, or a final *s*, is
dropped for counting: *pomodoro/pomodori*, *seed/seeds*). Then:

score(phrase) = n × Σ over its content words w of f(w) × (9 − Zipf(w))

where n is how many times the phrase is said, f(w) how many times the word is said in the call
(the TF), and 9 − Zipf(w) is −log₁₀ of the word's frequency in the language it was mostly said in
(Zipf is log₁₀ per billion words), the IDF against the language. So "basil" or "DOP" beat
"realtà" or "lavoro" with no list of generic words. Ties go to the phrase said more often.

The phrases are taken best first, skipping one contained in a phrase already taken; a longer
phrase said at least twice replaces a better one it contains ("pizza margherita" over "pizza").

### 4. The title

| Rule | Parameter |
|---|---|
| Too short to tell: no title | under 60 content words |
| Phrases that can enter | said at least twice; the best 6 |
| Built like a title, not a tag list | the 2 best phrases joined by "e" or "and" (the call's main language) |
| Same head, different modifier, coordinated | "peperoni rossi" + "peperoni gialli" → "Peperoni rossi e gialli" |
| One coordination at most, and short | at most 60 characters, otherwise the first phrase alone |
| Spelling | an acronym in capitals if written so at least once (2–6 letters); a capital initial for a word always written with one; lowercase otherwise |

The rules were checked on six transcripts: the test set and the author's own trial
recordings. Their titles are not reproduced, since the conversations are private; with
invented calls, the rules give titles such as "Mulching naturale e semi antichi", "Ripest tomato and
variety of seeds" or "Peperoni rossi e gialli" (the examples in `tests/test_keywords.py`).

### 5. Measuring it on new calls

Each call keeps the suggested title (`title_suggested`) apart from the one the person chose
(`title`), and `parola titles` lists how many suggestions were kept and, for the others, the
suggestion next to the chosen title. The rules stay frozen until there are enough new calls to
judge them. This procedure already forced one revision: on the first imported real meeting the
top phrases were the hedges of meeting speech ("Kind and things") and the spoken-frequency bar
above was added, chosen by comparing candidates on the whole title set
([`results/2026-10-20-title-hedges.md`](results/2026-10-20-title-hedges.md)): it fixes the
measured case, reproduces word for word the one title chosen by hand, and leaves the kept
titles equal or better. Word frequencies come from
[wordfreq](https://github.com/rspeer/wordfreq) by Robyn Speer (code Apache-2.0, data
CC BY-SA 4.0).

## Translation into Italian (ToTTI)

English calls can be followed in Italian. An "IT" toggle in the call view shows an Italian
translation under each English line, and during a recording the same engine writes live
subtitles. The translation is a derived layer like the clean version and the title: it is
computed from the verbatim transcript, cached apart from it, and re-derived when the transcript
changes, so a corrected word corrects its translation too. It never touches the verbatim — each
Italian line sits under the English segment it comes from, with its timestamps — and the live
subtitles are ephemeral: the full-quality transcription still runs at the end of the call as
usual.

The engine is Opus-MT en-it (Helsinki-NLP) converted to CTranslate2 int8: 87 MB, about 40 ms
per sentence on the CPU, so Whisper keeps the GPU during a call. Larger models were rejected
for this seat: MADLAD-400 3B translates better but weighs over 6 GB, a local LLM can invent
text — the one thing subtitles must not do — and NLLB's licence is non-commercial.

### Measured like the transcript

- The test set started as **224 EN→IT pairs from two of the author's English recordings** (a
  lecture and a conversation, with the speakers' consent) and grew to **406** with two public
  meetings from YouTube (a GitLab team meeting, and a council meeting in formal procedural
  English): the sources are verbatim ASR segments, the references translated by hand. Like the
  audio, the pairs are never committed.
- A design choice worth stating plainly: the source sentences are the transcripts P.A.R.O.L.A.
  actually produces, residual ASR errors included, and the gold translates the transcript, not
  the idealized speech (stretches with unresolved doubts are excluded from pairing). The meter
  therefore measures translation of the production input — harder and more representative than
  clean text — and by construction it does not reward fixing transcription errors from context;
  judging that robustness would need a dedicated slice with gold translated from the audio.
- The metrics are corpus **chrF** (primary: character level, so Italian morphology costs a
  little, not a whole word) and **BLEU**, via sacrebleu, with two declared counters the scores
  cannot see — subtitles must not invent: **copies** (sources left untranslated) and
  **inflated** (translations over 1.6 times their source's length, which suggests added
  content). Metrics and counters have hand-built unit tests, as the WER does.
- **FLORES-200 devtest** (1,012 professionally translated EN→IT sentences, CC BY-SA 4.0) is
  the general-domain regression guard: a model that gets better on calls by getting worse at
  ordinary English is refused.

The base engine scores chrF 56.1 on the domain set (224 pairs at the time) and 57.6 on FLORES, with no copies and no
inflated output ([report](results/2026-10-19-totti-baseline-en-it.md)).

### A teacher chosen by the numbers

To improve the engine without making it heavier, the path is sequence-level distillation: a
large, cleanly licensed teacher translates in-domain English, and those pairs fine-tune the
small student. The bar was fixed before measuring: the teacher must beat the student by about
+3 chrF on the domain set, otherwise distillation is not worth the trouble.

Two candidates, both Apache-2.0 (licences checked by hand on the model cards): MADLAD-400-3B-MT
and Qwen3-4B-Instruct. On the lecture alone neither cleared the bar (the best gap was +2.0) and
the work would have stopped there. With the conversation in the set, the per-call breakdown
flipped the verdict:

| chrF per call | lecture | conversation |
|---|---|---|
| Opus-MT (the student-to-be) | 60.3 | 51.6 |
| MADLAD-400-3B-MT | 62.3 (+2.0) | 55.1 (+3.5) |

On a clean monologue the small model is already close; conversational meeting speech — the
domain the product lives in — is where it suffers and where the teacher clears the margin.
MADLAD was chosen; neither candidate copied a source or inflated an output
([report](results/2026-10-19-totti-baseline-en-it.md)).

### Distillation data

The teacher translated English meeting speech from the **AMI and ICSI** transcripts (text only,
CC BY 4.0, verified by hand on the archives themselves): 141,155 raw segments, 77,088 sentences
after the filters (4–60 words, exact duplicates dropped), with the verbatim feel — fillers,
truncations, restarts — kept on purpose, because that is what the student will see. The split
is by meeting series, so no speaker is in both train and dev, and overlap with the test set is
impossible by construction: its pairs come from the author's own recordings. Declared filters
on the teacher's output (length ratio outside [0.4, 1.6], source left untranslated) dropped
1,362 pairs, keeping 75,726.

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

### Why the full fine-tunes failed, and what shipped

The guardrail was fixed before training: the student must beat the base on the domain set
**and** stay above a floor on FLORES (56.6). Two full fine-tunes (on a rented GPU, about thirty
cents in all; training never runs on the Mac) both failed it:

| run | learning rate | training | FLORES |
|---|---|---|---|
| 1 | 5e-5 | 3 epochs | 57.7 → 54.9 |
| 2 | 2e-5 | ~8 epochs (early stop) | 57.7 → 51.3 |

The lesson: **forgetting tracks the total amount of training, not the learning rate** — the
gentler second run trained longer and drifted further. The fully trained student also scored
worse than the base on the hand-made set (55.1 against 56.1): past a point, the gain on the dev
set is imitation of the teacher's phrasing, not quality.

What shipped first (v0) is a **weight interpolation** between the base and the trained student,
θ(α) = α·student + (1−α)·base, each point converted to int8 and scored with the same harness:

| model | FLORES | domain |
|---|---|---|
| base | 57.6 | 56.1 |
| **α = 0.3** (v0) | **57.2** | **57.0** |
| α = 0.5 | 56.0 | 56.6 |
| α = 0.7 | 54.3 | 55.9 |
| α = 1.0 (the full student) | 51.3 | 55.1 |

α = 0.3 passes the gate: both calls improve (lecture 60.3 → 61.1, conversation 51.6 → 52.6) and
FLORES stays above the floor. The gain is small but consistent: a paired bootstrap over
segments has it ahead of the base in **1000 of 1000 resamples**
([report](results/2026-10-20-totti-t4-student.md)). v0 was replaced by v1, below.

### ToTTI v1: a human anchor, the model the app uses

On the widened 406-pair set v0 still beat the base (+1.6 chrF, +6.3 on the GitLab meeting) but
lost its edge on the council meeting (−0.3, within noise): formal register that the distilled
data never covered. v1 keeps the same 78M architecture — the live engine's speed is the
requirement — and changes the data: training restarts from the base on **97,522 pairs**, the
72,522 distilled ones plus **15,000 Tatoeba and 10,000 Europarl** human translations as an
anchor (26% human). The anchor did what it was added for: at α = 1 the FLORES drift fell from
−6.3 to −0.4. Interpolated with the base and scored with the same harness:

| model | all (406) | lecture | conversation | GitLab | council | FLORES |
|---|---|---|---|---|---|---|
| base | 52.2 | 60.3 | 51.6 | 40.4 | 52.0 | 57.6 |
| v0 (α = 0.3) | 53.8 | 61.1 | 52.6 | 46.7 | 51.7 | 57.2 |
| **v1, α = 0.5** (shipped) | **54.3** | 61.1 | 53.2 | 47.7 | **52.3** | **57.5** |
| v1, α = 1.0 (the full student) | 53.6 | 60.1 | 52.9 | 48.1 | 50.3 | 56.1 |

v1 at α = 0.5 is the model the app uses: **+2.1 chrF over the base** (CI [+1.1, +3.1], ahead
in all 1,000 paired-bootstrap resamples; +7.3 on the GitLab meeting) and +0.5 over v0 (ahead
in 98%), with the council slice recovered and FLORES within 0.1 of the base. It did **not**
pass the gate fixed before training — 54.7, what Opus-MT tc-big (a larger 0.2B model of the
same family) scores with no training at all — and the failure stays on record: v1 ships as a
strict improvement on v0 at the same speed, not through the gate. The app loads v1, then v0,
then the base, whichever is on disk ([report](results/2026-10-21-totti-v1-alpha-sweep.md)).

Honestly sized: +2.1 chrF is a bit over half of the teacher's +3.6 on the same 406 pairs, every
reference comes from one translator, and two of the four recordings are the author's own. More
hand-translated calls sharpen both the test set and the next run of this table; Italian to
English is the same loop in the other direction, not started.

## Limitations

- **A small test set.** The error rates come from 0.76 hours on two files, one per language;
  the confidence intervals are wide. it-001 and en-001 have no
  reference yet.
- **Silver references.** The references started as turbo drafts. Adjudication against
  CrisperWhisper reduces the bias towards turbo but does not remove it.
- **Same mistakes are invisible.** About a third of turbo's errors are not flagged, because
  Parakeet makes the same mistake. On accented English (en-001, an Italian speaker) 24% of the
  words are flagged, twice the test-set rate; this has not been analysed yet.
- **Voices measured on English meetings only.** The diarization error comes from two AMI
  meetings recorded in a room in 2005: no Meet audio, no Italian, and no reference on real calls.
  On en-001, a lecture, the voices alternate as if there were two speakers; not checked yet.
- **Language on four files.** The 5-window detection is checked on four recordings, none of them
  mixed Italian and English.
- **Translation measured on four recordings.** The 406 reference pairs come from a lecture, a
  conversation and two public meetings, all translated by one person, and the shipped gain over
  the base engine is +2.1 chrF.
  The references fix recognition errors that every system translates alike, so part of the
  measured gap is source noise. Italian to English does not exist yet.
- **Device changes untried on real hardware.** The conversion and the silence filling are
  checked in a test (24 kHz mono audio into a 48 kHz stereo file: right pitch, level and
  length); the restarts themselves still have to be tried with AirPods during a real call.
- **One machine.** Times and memory are measured on an M4 Pro with 24 GB. Macs with 8 GB and
  Windows are not covered.
- **Playback precision.** Seeking in WebKit was measured with Web Audio inside the page, which
  adds a variable delay at start; the WAV lands "where expected" (within about a second), not
  "to the hundredth".

## Third-party models and data

| What | Used for | Licence | Size |
|---|---|---|---|
| Whisper large-v3-turbo (MLX conversion) | transcript | MIT | 1.6 GB |
| Parakeet TDT 0.6B v3 (MLX conversion) | doubtful words | CC BY 4.0 | 2.5 GB |
| pyannote segmentation 3.0 (sherpa-onnx export) | who speaks | MIT | 6 MB |
| WeSpeaker VoxCeleb ResNet34-LM | who speaks | CC BY 4.0 | 27 MB |
| wordfreq | titles, glossary | Apache-2.0 (data CC BY-SA 4.0) | in the package |
| Opus-MT en-it (CTranslate2 conversion) | translation: the base, and the starting point of the ToTTI student (v1, α = 0.5 interpolation; weights not published) | Apache-2.0 (model card; CC BY 4.0 on the OPUS-MT repository) | 87 MB |
| MADLAD-400-3B-MT (training only) | distillation teacher | Apache-2.0 | not bundled |
| Qwen3-4B-Instruct (evaluation only) | teacher comparison | Apache-2.0 | not bundled |
| AMI and ICSI transcripts (training only) | distillation sources | CC BY 4.0 | not bundled |
| Tatoeba and Europarl (training only) | human anchor of the v1 student | Tatoeba CC BY 2.0 FR; Europarl: no known copyright restrictions (statmt.org) | not bundled |
| FLORES-200 devtest (evaluation only) | translation regression set | CC BY-SA 4.0 | not bundled |
| CrisperWhisper 2.0 (optional, evaluation only) | comparison | non-commercial | not bundled |

The models are downloaded once, when the person agrees in the app; the two voice models are
checked by SHA-256.

## Reproducing the numbers

```bash
uv run parola eval                      # all systems on the references in the manifest
uv run parola eval --systems turbo,turbo-vad
uv run --group baselines parola eval --systems crisper-turbo-lang
```

Reports are written to `results/` with the commit they were produced from.
