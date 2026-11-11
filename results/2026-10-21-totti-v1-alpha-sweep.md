# ToTTI v1 alpha sweep: better than v0, short of the pre-registered gate

Date: 2026-10-21 · recipe and gate in `training/totti/config-v1.yaml` · harness as in
[2026-10-20-totti-test-set-v2.md](2026-10-20-totti-test-set-v2.md) (406 pairs, chrF, CT2
int8, beam 2).

## The run

Same 78M student (speed is the point), training restarted from the factory base on the v1
mix — 97,522 pairs: 72,522 distilled (3B teacher), 15,000 Tatoeba and 10,000 Europarl as
human anchor (26% human), 6 corrections. Colab, 6 epochs, best checkpoint by dev chrF
(77.4). Provisional on-Colab guardrail: FLORES 56.2 vs floor 56.6 — a −0.4 drift at α=1,
against T4's −6.3: **the human anchor cut catastrophic forgetting by ~6 points**, which is
what it was added for.

At home the checkpoint was interpolated with the factory base (θ(α) = α·v1 + (1−α)·base,
clone-safe state dicts), each point converted to CT2 int8 and scored on the private 406 set
and FLORES:

| model | all (406) | lecture | conversation | GitLab | council | FLORES |
| --- | --- | --- | --- | --- | --- | --- |
| base | 52.2 | 60.3 | 51.6 | 40.4 | 52.0 | 57.6 |
| v0 (α0.3, shipped) | 53.8 | 61.1 | 52.6 | 46.7 | 51.7 | 57.2 |
| v1 α0.3 | 54.1 | 61.2 | 52.9 | 47.4 | 52.1 | 57.7 |
| **v1 α0.5** | **54.3** | 61.1 | 53.2 | 47.7 | **52.3** | **57.5** |
| v1 α0.6 | 54.2 | 60.9 | 53.6 | 47.6 | 51.8 | 57.4 |
| v1 α0.7 | 54.0 | 60.7 | 53.4 | 47.5 | 51.5 | 57.1 |
| v1 α0.8 | 54.3 | 61.2 | 53.4 | 48.3 | 51.6 | 57.0 |
| v1 α0.9 | 54.2 | 60.8 | 53.6 | 48.4 | 51.1 | 56.6 |
| v1 α1.0 | 53.6 | 60.1 | 52.9 | 48.1 | 50.3 | 56.1 |

Base and v0 reproduce their recorded numbers exactly — the harness is the same meter.

## Gate verdict: FAIL on the pre-registered bar

The gate fixed before training: domain ≥ **54.7** (tc-big zero-shot) with FLORES ≥ 56.6.
The best α reaches **54.3**. No α passes; the 78M student, even with the richer mix, stops
0.4 chrF short of what the 0.2B model does with no training at all.

## What the sweep did win (paired bootstrap, 1,000 resamples)

**v1 α0.5 vs v0 (the shipped student):** +0.5 overall, CI [+0.0, +0.9], ahead in **98%** of
resamples — a real, if small, improvement at identical speed. Per slice: GitLab +1.0 (96%),
conversation +0.6 (92%), council +0.5 (87%), lecture −0.0 (44%).

**v1 α0.5 vs base:** +2.1 overall, CI [+1.1, +3.1], 100%; GitLab +7.3, CI [+3.6, +11.5].

The Europarl anchor did its second job too: on the council slice v0 *lost* 0.3 to the base;
v1 α0.5 is +0.3 (and +0.5 over v0). The formal-register hole is patched, not just padded.
FLORES at α0.5 is 57.5 — above the floor, above v0's 57.2, within 0.1 of the base: no
forgetting trade-off at the chosen α.

## Reading

- The v1 recipe is strictly better than the v0 recipe: more domain, more register breadth,
  less forgetting, same architecture and speed. As an *improvement of the shipped model*,
  α0.5 is justified by the numbers.
- As a *challenge to tc-big zero-shot*, it fails: 54.3 vs 54.7 — and tc-big also holds
  53.2 on council and 58.9 on FLORES. Capacity, not data, is now clearly the wall;
  the pre-registered next lever (teacher n-best/sampled distillation) stays on record but
  its realistic headroom is the remaining 0.4.
- The decision — ship v1 α0.5 (keep the small-student speed), stay on v0, or revisit
  tc-big for the live engine — is Jacopo's; the gate was defined on 54.7 and this report
  does not reinterpret it.

## Decision: shipped as ToTTI v1 (Jacopo, 2026-10-21)

**The 54.7 gate failed and is recorded as failed** — tc-big zero-shot stays 0.4 chrF ahead
on the domain meter (and ahead on council and FLORES). v1 α0.5 ships *not* through the gate
but as a strict improvement on the incumbent v0: +0.5 chrF (98% bootstrap wins), council
recovered, FLORES 57.5 inside the guardrail, identical speed — the criterion Jacopo set when
choosing to keep the 78M student ("la velocità è veramente importante"). The engine is
`opus-mt-en-it-totti-v1-ct2`; `translator.py` prefers it, with v0 then the base as
fallbacks. tc-big remains the measured ceiling for a same-speed-class swap, on record.
