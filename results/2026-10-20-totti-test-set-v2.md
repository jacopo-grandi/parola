# ToTTI test set v2: the first move-A gold widens the meter

Date: 2026-10-20 · quality plan move A (PLAN, "Piano qualità post-T4") · harness as in
[2026-10-19-totti-baseline-en-it.md](2026-10-19-totti-baseline-en-it.md).

## The new call and its pairing

Third call in the set: a **public GitLab team meeting from YouTube** (42.7 min, English,
conversational, noisy — the register the product lives in). Source and licence recorded in
`data/manifests/totti-test.jsonl` (CC BY per GitLab's public-by-default practice, to be
confirmed on the specific video). The Italian gold was translated by hand; pairing rules,
applied and counted:

- aligned to the transcript by HH:MM:SS, like the earlier golds;
- translator notes in [square brackets] stripped;
- pairs whose reference keeps a "(word?)" doubt mark **excluded** (104): those marks flag
  audio nobody could resolve, so neither side of the pair is trustworthy;
- source segments under 4 words or with > 20% doubtful words excluded (77), one block had no
  gold counterpart; no duplicates.

**86 pairs added → 310 in all** (lecture 157, conversation 67, GitLab meeting 86).

## Results (chrF, CT2 int8, beam 2 — the app's own engines)

| model | all (310) | lecture | conversation | GitLab meeting |
| --- | --- | --- | --- | --- |
| opus-mt-en-it (base) | 52.3 | 60.3 | 51.6 | 40.4 |
| **student α0.3 (in the app)** | **54.4** | 61.1 | 52.6 | **46.7** |
| teacher MADLAD-400-3B | 56.3 | 62.3 | 55.1 | 48.7 |

Paired bootstrap (1,000 resamples): the student beats the base in **100%**.

## Reading

- The harder and more meeting-like the call, the more the distillation pays: on the GitLab
  meeting the student gains **+6.3** over the base and keeps 76% of the teacher's margin
  there. The +0.9 of the first measurement was an artifact of a lecture-heavy meter, as
  suspected: on the widened set the student's true gain is **+2.1 overall**.
- **B+C gate: cleared.** The teacher's margin on the widened meter is **+4.0** (and +3.5 /
  +8.3 on the two conversational slices) — above the pre-registered +3 threshold. The tc-big
  student with a human-anchor KD recipe (move B+C) is now worth one Colab run, once the
  second gold lands.

## Caveats, declared

- Every gold in this set is the work of **one translator** (Jacopo): consistent, but a
  single person's choices set the reference style.
- The GitLab-meeting slice lost 104 candidate pairs to unresolved "(word?)" marks: the kept
  86 skew toward the cleaner stretches of a noisy recording.
- A second meeting gold is being translated and will extend this table.

## Second gold, same day: the council meeting (set → 406)

Fourth call: a **public council meeting from YouTube** (104 min, formal procedural English —
motions, apologies, minutes). Same pairing rules: 96 pairs added, 232 dropped for unresolved
"(word?)" marks, 138 for short or doubtful sources. Manifest record appended (licence to
confirm on the specific video).

| model | all (406) | lecture | conversation | GitLab | council |
| --- | --- | --- | --- | --- | --- |
| base | 52.2 | 60.3 | 51.6 | 40.4 | 52.0 |
| **student α0.3** | **53.8** | 61.1 | 52.6 | 46.7 | 51.7 |
| teacher | 55.8 | 62.4 | 55.1 | 48.8 | 53.8 |

Bootstrap (1,000 resamples): student over base in 100%.

### Per-slice uncertainty (paired bootstrap, 1,000 resamples within each slice)

| slice | student − base | 95% CI | win | teacher − base | 95% CI | win |
| --- | --- | --- | --- | --- | --- | --- |
| all (406) | +1.6 | [+0.8, +2.6] | 100% | +3.6 | [+2.5, +4.7] | 100% |
| lecture (157) | +0.8 | [+0.0, +1.6] | 98% | +2.1 | [+0.9, +3.3] | 100% |
| conversation (67) | +1.0 | [−0.0, +2.3] | 97% | +3.5 | [+2.1, +5.0] | 100% |
| GitLab (86) | **+6.3** | **[+2.7, +9.9]** | 100% | +8.4 | [+4.9, +12.3] | 100% |
| council (96) | −0.3 | [−1.7, +0.9] | 35% | +1.8 | [−0.5, +4.2] | 93% |

**Answers to the questions the table was built for.** The GitLab **+6.3 is a real gain**:
its whole interval sits above zero. The council **−0.3 is noise**, not a regression — the
interval straddles zero both ways; the true finding there is weaker than first written: the
student's *advantage vanishes* on the formal procedural register the distillation data never
covered (and even the teacher's +1.8 is not significant on 96 pairs). Still a direct input
for the B+C mix: register breadth (the human anchor, possibly a formal-speech slice such as
Europarl — free licence, to be proposed and approved before any download).

B+C gate on the widened meter: teacher margin **+3.6, CI [+2.5, +4.7]** — the point estimate
clears the pre-registered +3, the interval dips below it; the gate was defined on the point
estimate and stands, with the uncertainty now on record.

## Teacher candidate madlad400-7b-mt: rejected

Jacopo's question — would a stronger teacher raise the ceiling? — answered the cheap way:
converted on Colab (licence Apache 2.0, checked; ~3 compute units in all) and scored at home
on the 406-pair meter, against the 3B with per-slice paired bootstrap:

| slice | 3B | 7B | Δ (7B−3B) | 95% CI | win |
| --- | --- | --- | --- | --- | --- |
| all (406) | 55.8 | 55.5 | −0.3 | [−0.9, +0.4] | 19% |
| lecture | 62.4 | 61.9 | −0.5 | [−1.6, +0.6] | 18% |
| conversation | 55.1 | 54.5 | −0.5 | [−1.7, +0.6] | 17% |
| GitLab | 48.8 | 48.2 | −0.6 | [−2.0, +0.7] | 19% |
| council | 53.8 | 54.4 | +0.6 | [−1.2, +2.5] | 72% |

At twice the size and half the speed, the 7B never clears zero. **The 3B stays the teacher**
and, decisively for move B+C: the 77k distilled pairs stay valid — no regeneration. Teacher
quality is not the current bottleneck; student capacity and training-mix breadth are, which
is exactly what B+C addresses.

## Jacopo's objection pays: tc-big zero-shot beats the fine-tuned student

Before spending the B+C Colab units, the planned student was measured **as is** (CT2 int8,
beam 2, same harness):

| slice | base | a30 (shipped) | tc-big zero-shot | teacher 3B |
| --- | --- | --- | --- | --- |
| all (406) | 52.2 | 53.8 | **54.7** | 55.8 |
| lecture | 60.3 | 61.1 | 61.5 | 62.4 |
| conversation | 51.6 | 52.6 | 53.8 | 55.1 |
| GitLab | 40.4 | 46.7 | 46.9 | 48.8 |
| council | 52.0 | 51.7 | **53.2** | 53.8 |

FLORES-200: **58.9** — above the small base's 57.6: no forgetting trade-off, simply a better
model. Speed 19.5 sentences/s on CPU (≈0.2 s of MT per 2-second live tick: well inside the
budget). Bootstrap vs the shipped student: ahead in 99% of 1,000 resamples.

**Reading.** Zero-shot, tc-big matches the distillation's whole GitLab gain, covers the
council register the small student never could, and sits 1.1 chrF from the 3B teacher. The
B+C fine-tune's realistic headroom is therefore ≤1 point, bought with training-drift risk —
the T4 story taught what that costs. The economical move is shipping tc-big as is and
retiring the fine-tune until the meter grows; the decision is Jacopo's.
