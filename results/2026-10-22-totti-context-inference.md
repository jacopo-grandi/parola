# Context at inference for the batch IT view: a three-arm experiment

Date: 2026-10-22 · approved by Jacopo ("lancia l'esperimento a tre bracci con i gate detti
e il tasso di ritagli falliti come metrica") · engine ToTTI v1, zero downloads, zero
training. The live path stays sentence-by-sentence whatever happens here: a divergent
batch configuration is explicitly fine.

## Design, registered before any harness code

Three arms on the same 406 pairs and gold:

- **(a) control** — each sentence alone. Must reproduce 54.3 chrF, or the harness is wrong.
- **(b) previous + current** — the preceding segment concatenated before the target;
  only the target's translation is kept.
- **(c) centered window-3** — [previous, current, next]; the middle translation is kept
  (the batch view knows the future).

Context comes from contiguous segments of the same call and the same channel, English
only; never across call boundaries; a missing neighbor leaves the slot empty (arm b then
degenerates to the control for that segment, declared in the counts). On FLORES the same
procedure uses adjacent devtest lines as context — the file carries no document boundaries,
so crossing them is a declared approximation that can only hurt the context arms.

**The clip strategy, declared.** Marian swallows explicit separator tokens and ASR
punctuation is unreliable, so no sentence-splitting of the output is trusted. Instead each
context sentence is also translated alone (deterministic: same engine, beam 2), and the
target's translation is clipped by anchored fuzzy trimming: the longest output prefix
matching translate(previous) (difflib ratio ≥ 0.5 on the candidate prefix) is removed, and
for arm c likewise the longest suffix matching translate(next). A clip **fails** when an
anchor cannot be found at ratio ≥ 0.5 or the remainder is empty; the segment then falls
back to its isolated translation and is counted.

## Gates, pre-registered

1. **Domain**: arm chrF on the 406 set ≥ **54.3** (never worse than isolated v1).
2. **FLORES**: within the usual guardrail (≥ **56.6**).
3. **Failed-clip rate**: a first-class metric, reported per arm and per slice; an arm that
   wins chrF by discarding its own mechanism (high fallback rate means the "win" is just
   the control with noise) is not a win: the fallback rate must stay **≤ 10%**.
4. Per-slice paired bootstrap for any arm that passes 1–3, against the control.

**Decision rule**: an arm that passes enters the **batch path of the IT view only**; if
none passes, context moves to the v2 training recipe and the inference thread closes.

## Results: no arm passes — the inference thread closes

| arm | domain chrF | clip failed | FLORES | gate verdict |
| --- | --- | --- | --- | --- |
| (a) isolated (control) | 54.3 | — | 57.5 | reproduces v1: harness sane |
| (b) previous + current | 53.8 | **15.3%** | 56.3 | fails 1 (< 54.3) and 3 (> 10%) |
| (c) centered window-3 | 54.7 | **29.8%** | 57.2 | passes 1 and 2, **fails 3** |

Bootstrap, for the record: arm c beats the control overall (+0.4, CI [−0.0, +0.9], win
97%, best on council +0.7) — but with **three segments in ten falling back to the isolated
translation** because the anchored clip could not find its context in the output. The
pre-registered reading applies verbatim: a win carried by the fallback mechanism is not a
win. Arm b is worse than useless: on the register slices the previous sentence *drags the
translation off* (GitLab −2.0), and its one bright spot (lecture +0.7) is where sentences
actually continue each other.

What the failures look like, for the v2 recipe's benefit: Marian does not translate
concatenations compositionally — it merges, reorders or drops sentences (which is exactly
why the clip anchors fail), so inference-time context on this architecture caps out before
its linguistic benefit can be banked. The signal that context *helps* (arm c's +0.4 with a
blunt instrument, council's +0.7 where pronouns and procedure span sentences) is real and
goes **into the v2 training recipe** (context-aware distillation pairs), where the model
can learn to use context without an extraction step. The batch IT view stays
sentence-by-sentence; nothing ships from this experiment.
