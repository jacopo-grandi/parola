# ToTTI baseline: Opus-MT en-it on the domain test set (T1)

Date: 2026-10-19 · decision 0038, plan section T1.

## Setup

- **System**: Helsinki-NLP/opus-mt-en-it converted to CTranslate2 int8, beam 2, CPU
  (the engine the IT view uses).
- **Test set**: 157 EN→IT pairs (`data/private/totti/test-en-it.jsonl`, private). Sources are
  verbatim ASR segments from the recorded calls; references translated by hand (Jacopo,
  2026-10-19). 156 pairs come from one call (an English lecture),
  1 from a second recording: the domain is narrow until more English calls are recorded and
  the draft is regenerated. 11 drafted pairs are still unfilled and excluded.
- **Metrics**: corpus chrF (main) and BLEU via sacrebleu; `copies` counts sources left
  untranslated, `inflated` counts hypotheses long enough (ratio > 1.6) to suggest added
  content (`eval/translation.py`, unit-tested).

## Results

| system | set | pairs | chrF | BLEU | copies | inflated | time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| opus-mt-en-it (CT2 int8, beam 2) | domain | 157 | **60.3** | 25.2 | 0 | 0 | 3.4 s |
| opus-mt-en-it (CT2 int8, beam 2) | FLORES-200 devtest | 1012 | **57.6** | 27.5 | 0 | 1 | 26.2 s |

FLORES-200 devtest (added 2026-10-19) is the general-domain regression guard: professional
EN→IT references, CC BY-SA 4.0 (checked by hand on the Hugging Face dataset cards; fetched
from Meta's official archive). The student of T4 must stay close to 57.6 here while beating
60.3 on the domain set.

## Reading the numbers

- No untranslated sources and no length-inflated outputs: the engine does not invent, which
  is the one failure subtitles cannot afford.
- The references are clean translations: they fix ASR errors in the source and normalise the
  lecturer's phrasing, so part of the gap is source noise, not translation quality. The worst
  pairs are exactly those where the English contains a recognition error (the model dutifully
  translates the wrong word) — the doubtful-word filter at draft time keeps most but not all
  of these out.
- Recurring error classes seen in the lowest-scoring pairs: ASR errors propagated into
  Italian; informal second-person singular where the lecture register is plural; literal
  renderings of deictic classroom speech ("this guy here").

## Still missing for T1

- More calls in the test set: today it is one lecture, no conversation. The draft is
  regenerated (`parola totti draft --force`) as English calls accumulate.

## T2 — teacher candidates (2026-10-19, same day)

Both candidates measured on the same 157 pairs with the same harness. Licences checked by
hand on the model cards: MADLAD-400-3B-MT Apache-2.0 (T5, converted to CTranslate2 int8,
2.8 GB, `<2it>` target token); Qwen3-4B-Instruct-2507 Apache-2.0 (MLX 4-bit, 2.26 GB,
prompted to output only the translation).

| system | set | pairs | chrF | BLEU | copies | inflated | time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| opus-mt-en-it (CT2 int8, beam 2) | domain | 157 | 60.3 | 25.2 | 0 | 0 | 3.4 s |
| madlad400-3b-mt (CT2 int8, beam 2) | domain | 157 | **62.3** | 27.8 | 0 | 0 | 73.2 s |
| qwen3-4b-instruct-2507 (MLX 4-bit) | domain | 157 | 62.0 | 27.4 | 0 | 0 | 75.9 s |

**First verdict (157 pairs, one lecture).** Neither teacher cleared the ~+3 chrF margin
(best: +2.0): distillation looked not worth it. Caveats: references fix recognition errors
that every system translates alike, and the set was one lecture.

### Re-run with a conversation (same day, 224 pairs)

Jacopo translated a second recording (a conversation, 67 usable pairs; references with
unresolved words excluded). Whole-set and per-call numbers:

| system | set | pairs | chrF | BLEU | copies | inflated | time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| opus-mt-en-it | domain (all) | 224 | 56.1 | 20.7 | 0 | 0 | 6.0 s |
| madlad400-3b-mt | domain (all) | 224 | **58.8** | 22.8 | 0 | 0 | 136 s |
| qwen3-4b-instruct | domain (all) | 224 | 58.0 | 22.4 | 0 | 0 | 309 s |

Per call, chrF (opus → madlad): lecture 60.3 → 62.3 (**+2.0**); conversation 51.6 → 55.1
(**+3.5**).

**Verdict.** On the lecture (clean monologue) the gap stays small, but on the conversation —
the domain the product lives in — MADLAD clears the +3 margin. Conversational speech is
where Opus-MT suffers and where a distilled student has room to gain. **Decision: proceed to
T3 with MADLAD-400-3B-MT as the teacher**, with the margin re-checked as more conversations
join the set (n=67 is still thin). Qwen stays the runner-up; no candidate copied sources or
inflated output at any point.
