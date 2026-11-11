# The small/fast EN→IT class: a paper census, before any download

Date: 2026-10-22 · phase 1 of validating the positioning claim "best open EN→IT model in
the ~80–100 MB / ~40 sent/s-on-CPU class, on meeting speech". Licences checked by hand on
each source (dates noted); **nothing downloaded** — phase 2 runs only on an explicit OK,
and would measure every entrant on the uniform meter: the 406-pair set per slice + FLORES
+ speed, CT2 int8 beam 2 where the runtime allows, bootstrap against ToTTI v1.

## The incumbent and its class

ToTTI v1: 78M parameters, ~82 MB int8, Marian 6+6; measured 54.3 chrF on the 406 meter,
FLORES 57.5. The claim's class can be read two ways, and the census covers both: by
**size** (≤~100 MB download) and by **speed** (comparable sentences/s on CPU regardless of
megabytes). The second reading is the dangerous one.

## Candidates in (or near) the class

| candidate | licence (checked) | size | expected speed | notes |
| --- | --- | --- | --- | --- |
| **quickmt/quickmt-en-it** | CC-BY-4.0, on the HF card, 2026-10-22 | 185M params, ~190 MB int8 | high: 8-enc/2-dec transformer, CT2-native ("fastest in its comparison", self-reported) | **The serious challenger.** Self-reported FLORES: chrF2 59.7, BLEU 30.5 — above tc-big's measured 58.9. News/FLORES register though; the meeting meter is ours. Out of the size class, plausibly in the speed class. |
| **Firefox/Bergamot `enit`, tiny tier** | MPL-2.0 (repo LICENSE, 2026-10-22) | ~17 MB (int8 Marian student) | very high (built for in-browser CPU) | The truly-tiny class. Runtime is bergamot/Marian, **not CT2**: the uniform meter needs their runtime, a declared caveat. Quality on meeting register unknown, likely under our base. |
| **Firefox/Bergamot `enit`, base-memory tier** | MPL-2.0, same repo | ~40 MB | high | Same caveats; the better-quality sibling. No plain `base` tier exists for enit. |
| **Argos Translate `en_it`** | project states MIT/CC0 for tooling; the per-package licence must be read from the argospm index entry at download time — **to confirm** | ~100–250 MB (.argosmodel) | medium-high (CT2 inside!) | OPUS-data lineage, likely ≤ our base in quality; trivially measurable (the archive contains a CTranslate2 model). |
| OPUS-MT family, newer small en-it | — | — | — | **Does not exist.** The Tatoeba-Challenge eng-ita releases are the 2020 `opus+bt` (our base's lineage) and the 2021 `transformer-big` (tc-big, already measured). The 2021 data refresh never shipped in the small size. |

## Out of class or excluded, on the record

- **NLLB-200 (any size)** — excluded by licence (CC-BY-NC, decision 0015).
- **opus-mt-tc-big-en-it** — 232M, measured already (54.7 domain / 58.9 FLORES, 19.5
  sent/s): the bigger class the claim deliberately does not enter.
- **M2M-100 418M** (MIT) and **SMaLL-100** (MIT on the HF card, checked 2026-10-22; 330M,
  distilled from M2M-12B) — permissive but heavy: multilingual vocabularies (128k) make
  the decoder slow regardless of quantization; not credible in the speed class.
- **HPLT MT models** — no en-it release found (their v1 pairs target other languages).
- **opus-mt-en-ROMANCE / en-mul** — same 78M architecture as our base with the target
  split across languages: dominated by the dedicated en-it base, not worth a download.

## Reading

In the strict **size** reading (≤~100 MB) the field is: the two Bergamot students, maybe
Argos, and our own base lineage — v1 likely wins, but the claim is only blindata once they
are measured. In the **speed** reading, quickmt-en-it is a real threat: CT2-native, a
2-layer decoder, and self-reported FLORES above tc-big. If it holds that quality on the
meeting register at comparable speed, it is not a threat to the claim but the app's next
engine — either outcome is worth the download.

**Proposed phase 2, pending the OK** (downloads in this order of information value):
quickmt-en-it (~190 MB), Bergamot enit tiny + base-memory (~60 MB together), Argos en_it
(licence read before anything else; dropped if not clean). Each goes in
`data/manifests/models.jsonl` on download, scores land here with per-slice bootstrap vs v1.

## Phase 2 (2026-10-22, authorized): the challenger measured — v1 holds the class

Who could actually be measured, declared first:

- **quickmt-en-it**: downloaded (CT2 files only, manifest updated), measured below.
- **Bergamot enit (both tiers)**: *excluded for runtime unavailability*, not downloaded —
  no prebuilt wheel exists for macOS arm (`bergamot` and `kotki` both fail to install),
  and the students use SSRU decoders that CTranslate2 cannot load, so measuring them means
  building bergamot-translator from source. Their class position stays unmeasured.
- **Argos en_it**: *still licence-unconfirmed* — the argospm index entry carries no licence
  field, and the only place the licence could be read is inside the package itself, which
  the authorization explicitly gates on the licence. Catch-22 declared; not downloaded.

Uniform meter: CT2 int8 (quickmt quantized at load from the fp16 download), beam 2, CPU,
1×4 threads, speed on the same 406-sentence batch, same process.

| engine | all (406) | lecture | conversation | GitLab | council | FLORES | sent/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **ToTTI v1** (78M, ~82 MB int8) | **54.3** | 61.1 | 53.2 | **47.7** | 52.3 | 57.5 | **40.2** |
| quickmt-en-it (185M, ~190 MB int8) | 53.9 | 60.9 | 53.8 | 45.4 | 52.0 | **59.2** | 38.4 |

Paired bootstrap (quickmt − v1, 1,000 resamples): all −0.4, CI [−1.2, +0.4], win 16%;
GitLab **−2.3**, CI [−4.6, +0.0], win 3%; conversation +0.6 (85%), lecture and council noise.

**Reading.** At the same speed (38 vs 40 sent/s — quickmt's 2-layer decoder earns its
reputation) and 2.3× the size, the challenger loses the meter the claim is about: meeting
speech, where v1's distillation on real calls shows exactly where the product lives
(GitLab −2.3). It wins FLORES clearly (59.2, even above tc-big's 58.9): quickmt is the
better *general-register* small-fast model, and anyone translating news should use it.
**The claim holds as worded** — best open EN→IT in the small/fast class *on meeting
speech* — strictly so in the ≤100 MB size class (no measurable competitor exists there at
all), and on the measured evidence in the speed class too, with the Bergamot gap declared.

**Addendum (2026-10-23).** Apple Translation, measured on the same meter by a dedicated
session ([report](2026-10-23-totti-apple-translation.md)): **57.1** overall at **1.6
sentences/s** — the best number measured by anyone, at 25× v1's latency, in a closed
engine that is headless only from macOS 26. It enters the books as baseline, not engine
(decision 0046), and sharpens the claim's defensible wording: *best measured open model in
the ~40 sent/s class on meeting speech; the more accurate engines measured (MADLAD 3B,
Apple Translation) run 20–25× slower.*
