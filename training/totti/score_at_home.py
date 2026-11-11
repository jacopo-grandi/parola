"""ToTTI T4: the accepting scores, run at home after training (decision 0038, plan T4).

Scores a CTranslate2 model folder with the same harness as the T1 baseline
(results/2026-10-19-totti-baseline-en-it.md): parola.eval.translation on the private domain
test set (which never leaves this Mac) and on FLORES-200 devtest. The base Opus-MT model is
scored alongside on the same sets, so the domain gate never goes stale as the test set grows
(the recorded 60.3 was the 157-pair lecture-only set; the 224-pair set scores 56.1). Verdict:
accepted only if the student beats the base model on the domain set AND FLORES stays within
the margin fixed in config.yaml before training.

Usage (repo root): uv run python training/totti/score_at_home.py data/models/<model-dir>
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import yaml

from parola.eval.translation import score
from parola.totti.translator import MODEL_DIR as BASE_MODEL_DIR
from parola.totti.translator import Translator

REPO_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SET = REPO_ROOT / "data" / "private" / "totti" / "test-en-it.jsonl"
FLORES_DIR = REPO_ROOT / "data" / "flores200" / "flores200_dataset" / "devtest"
CONFIG = Path(__file__).parent / "config.yaml"


def load_domain(path: Path) -> tuple[list[str], list[str]]:
    en, it = [], []
    with path.open() as f:
        for line in f:
            row = json.loads(line)
            en.append(row["en"])
            it.append(row["it"])
    return en, it


def run(translator: Translator, name: str, sources: list[str], references: list[str]) -> float:
    start = time.perf_counter()
    hypotheses = translator.translate(sources)
    elapsed = time.perf_counter() - start
    s = score(sources, hypotheses, references)
    print(f"| {name} | {s.pairs} | {s.chrf:.1f} | {s.bleu:.1f} | {s.copies} | {s.inflated} |"
          f" {elapsed:.1f} s |")  # fmt: skip
    return s.chrf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", type=Path, help="CTranslate2 model folder to score")
    parser.add_argument("--base", type=Path, default=BASE_MODEL_DIR,
                        help="baseline CTranslate2 model folder to beat")  # fmt: skip
    args = parser.parse_args()

    student = Translator(model_dir=args.model_dir)
    base = Translator(model_dir=args.base)
    for t in (student, base):
        if not t.available():
            print(f"no model.bin under {t.model_dir}")
            return 2

    guard = yaml.safe_load(CONFIG.read_text())["guardrail"]
    domain_en, domain_it = load_domain(DOMAIN_SET)
    flores_en = (FLORES_DIR / "eng_Latn.devtest").read_text().splitlines()
    flores_it = (FLORES_DIR / "ita_Latn.devtest").read_text().splitlines()

    print("| system | set | pairs | chrF | BLEU | copies | inflated | time |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    base_domain = run(base, f"base ({args.base.name}) | domain", domain_en, domain_it)
    base_flores = run(base, f"base ({args.base.name}) | FLORES-200 devtest", flores_en, flores_it)
    domain_chrf = run(student, f"student ({args.model_dir.name}) | domain", domain_en, domain_it)
    flores_chrf = run(student, f"student ({args.model_dir.name}) | FLORES-200 devtest",
                      flores_en, flores_it)  # fmt: skip

    # The domain gate is against the base model on today's set, not a stored constant: the
    # test set grows as calls are translated, and the margin must compare like with like.
    floor = min(guard["flores_baseline_chrf"], base_flores) - guard["flores_margin"]
    domain_ok = domain_chrf > base_domain
    flores_ok = flores_chrf >= floor
    print()
    print(f"domain: student {domain_chrf:.1f} vs base {base_domain:.1f} → "
          f"{'PASS' if domain_ok else 'FAIL'}")  # fmt: skip
    print(f"FLORES: student {flores_chrf:.1f} vs floor {floor:.1f} "
          f"(min of recorded {guard['flores_baseline_chrf']:.1f} and base {base_flores:.1f},"
          f" margin {guard['flores_margin']:.1f}) → "
          f"{'PASS' if flores_ok else 'FAIL'}")  # fmt: skip
    verdict = domain_ok and flores_ok
    print(f"verdict: {'ACCEPTED — the app can move to these weights' if verdict else 'REJECTED'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
