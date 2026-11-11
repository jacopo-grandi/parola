# ToTTI T4: the student ships at alpha 0.3

Date: 2026-10-20 · decision 0038, plan T4 · data in
[2026-10-19-totti-distillation-data.md](2026-10-19-totti-distillation-data.md), baselines in
[2026-10-19-totti-baseline-en-it.md](2026-10-19-totti-baseline-en-it.md).

## Training runs (Colab, T4 GPU, ~3 compute units in all)

Full fine-tune of Opus-MT en-it on the 72,522 distilled pairs, fp16, batch 64, early
stopping on dev chrF (config echoed in the log after run 2 silently reused run 1's bundle —
the echo is now permanent).

| run | lr | epochs | dev chrF | FLORES (fp16) | guardrail |
| --- | --- | --- | --- | --- | --- |
| 1 | 5e-5 | 3 | 76.6 | 57.7 → 54.9 | FAIL |
| 2 | 2e-5 | ~8 | 77.4 | 57.7 → 51.3 | FAIL |

Lesson: forgetting tracks the **total amount of training**, not just the rate — run 2's
gentler rate but more epochs drifted further. The full student also scores *worse than the
base* on the human test set (55.1 vs 56.1): the dev gain past a point is imitation of the
teacher's style, not quality.

## Weight interpolation (at home, free)

θ(α) = α·student + (1−α)·base on the run-2 checkpoint, each point converted to CT2 int8 and
scored with the same harness (dev = distilled references; domain = the private 224-pair
hand-made set; FLORES-200 devtest). A first sweep was invalidated by an aliasing bug
(`state_dict()` returns references; `load_state_dict` overwrote the student tensors after
the first alpha) — caught because α=1.0 failed to reproduce Colab's 51.3; the corrected
sweep reproduces it exactly.

| model | dev | FLORES | domain |
| --- | --- | --- | --- |
| base | 66.8 | 57.6 | 56.1 |
| **α=0.3** | **72.0** | **57.2** | **57.0** |
| α=0.5 | 74.6 | 56.0 | 56.6 |
| α=0.7 | 76.2 | 54.3 | 55.9 |
| α=1.0 | 77.1 | 51.3 | 55.1 |

## Verdict

**α=0.3 is accepted** by the gate fixed before training: domain 57.0 beats the live base
56.1 (both calls improve: lecture 60.3→61.1, conversation 51.6→52.6) and FLORES 57.2 stays above
the 56.6 floor. The gain is small but consistent: a paired bootstrap over segments has α=0.3
ahead of the base in **1000 of 1000 resamples**. Installed as
`data/models/opus-mt-en-it-totti-a30-ct2`; `translator.py` prefers it when present and falls
back to the base.

Honest limits: +0.9 chrF is a third of the teacher's +2.7 potential on this set, and the
test set is still two calls. Next lever if more is wanted: LoRA plus a general-data replay
slice (Tatoeba) — or simply more translated calls, which sharpen both the test set and the
re-run of this whole table.
