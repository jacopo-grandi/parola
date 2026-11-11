# ToTTI v1 on the public sets: the honest absolute placement

Date: 2026-10-22 · approved round ("sempre meglio averli quei numeri") · v1, base and
tc-big measured at home under identical conditions (CT2 int8, beam 2, CPU); external
numbers quoted with their sources and their conditions flagged. The expectation, written
before the numbers and confirmed by them: **on general register v1 is mid-table — its
advantage is the domain.** The point of this round is the honest placement, not a win.

## Sets and licences

- **FLORES-200 devtest** (1,012 sentences): in-house already (CC BY-SA 4.0).
- **NTREX-128 eng→ita** (1,997 sentences, newstest2019 source): CC BY-SA 4.0, stated in
  the repository README, checked by hand 2026-10-22; manifest `data/manifests/public-sets.jsonl`.
- **Tatoeba-Challenge eng-ita test** (29,212 pairs, master of 2026-10-22): CC-BY 2.0 FR
  for test/dev data per the repository README (the repo LICENSE's CC-BY-NC-SA covers the
  training data), checked by hand 2026-10-22. Published scores use the tagged
  v2021-08-07 set (17,320 pairs), which could not be fetched: version mismatch declared
  wherever compared.

## Measured at home (signatures below)

| set | v1 chrF2 / spBLEU | base | tc-big |
| --- | --- | --- | --- |
| FLORES-200 devtest | 57.5 / 33.6 | 57.6 / 33.9 | **58.9 / 35.4** |
| NTREX-128 | 60.3 / 37.8 | 60.1 / 37.6 | **62.1 / 40.8** |
| Tatoeba test (full) | **76.0 / 61.7** † | 73.2 / 57.2 | 74.4 / 59.5 |
| Tatoeba test, decontaminated (27,324) | **75.9 / 61.6** | 73.2 / 57.3 | 74.4 / 59.5 |

† **Contamination, found and handled**: 1,888 of the test sources (6.5%) appear verbatim
in v1's training mix (its 15k-pair Tatoeba anchor). The decontaminated row drops every
overlapping pair — v1 keeps 75.9, so the advantage is register familiarity (short
conversational sentences, the same distribution as the anchor), not memorization. Both
numbers are on the record; the clean row is the honest one, and it is still
in-distribution for v1 in a way it is not for the base.

Signatures (sacrebleu 2.6.0):
`chrF2 nrefs:1|case:mixed|eff:yes|nc:6|nw:0|space:no|version:2.6.0` ·
`spBLEU nrefs:1|case:mixed|eff:no|tok:flores200|smooth:exp|version:2.6.0`

## Against the published numbers (each with source and conditions)

FLORES-200 devtest, chrF2 — external rows from the
[quickmt-en-it model card](https://huggingface.co/quickmt/quickmt-en-it) (their sacrebleu
run, beam undeclared, GPU; their base row matches our measured 57.6, which anchors the two
harnesses to each other):

| model | chrF2 | source |
| --- | --- | --- |
| NLLB-200-distilled-1.3B | 59.9 | quickmt card (excluded from our class by licence, 0015) |
| quickmt-en-it | 59.7 | quickmt card; our int8 re-measure: 59.2 |
| opus-mt-tc-big-en-it | 58.9 | **ours**; its own card reports chrF .590 on flores**101** — different set version, flagged |
| NLLB-200-distilled-600M | 58.3 | quickmt card |
| opus-mt-en-it (base) | 57.6 | ours; quickmt card says 57.66 |
| **ToTTI v1** | **57.5** | ours |
| m2m100-418M | 54.3 | quickmt card |

Tatoeba, for context only (version mismatch): the tc-big card reports chrF .72539 on
v2021-08-07 (17,320 pairs); our tc-big scores 74.4 on the 29,212-pair master set — the
sets differ, so the comparison is indicative, not a ranking. The OPUS-MT dashboard was
unreachable (502) at the time of writing; its NTREX rows could not be quoted.

## Reading, without detours

- On **general register** (FLORES, NTREX) v1 sits exactly where the base sits — mid-table,
  ~2 chrF2 under its own teacher-class models (tc-big, quickmt, NLLB-1.3B). That is by
  design: the α0.5 interpolation exists to protect the base's general register, not to
  beat it, and the guardrail held (57.5 vs the 56.6 floor).
- On **conversational register** (Tatoeba, decontaminated) v1 leads even tc-big by +1.5 —
  consistent with the domain meter, where its distillation on conversational speech is worth
  +2.1 over the base. The positioning sentence the numbers support, updated after the Apple
  Translation baseline landed ([2026-10-23 report](2026-10-23-totti-apple-translation.md)):
  *mid-table on news; on meeting speech, the best measured open model in the ~40
  sentences/s class — the more accurate engines measured (MADLAD 3B, Apple Translation)
  run 20–25× slower.*
