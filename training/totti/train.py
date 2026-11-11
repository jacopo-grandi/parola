"""ToTTI T4: fine-tune Opus-MT en-it on the distilled pairs (decision 0038, plan T4).

Runs on Colab (T4 GPU is enough: 78M parameters, ~4M tokens per epoch). The notebook calls
this script; everything it needs is in config.yaml next to it. Early stopping on dev chrF,
then a FLORES-200 regression check against the guardrail fixed before training. The verdict
printed here is provisional (fp16 beam search is not the CT2 int8 engine the app runs); the
accepting numbers come from score_at_home.py on the private test set, which never leaves
the Mac.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import sacrebleu
import torch
import yaml


def load_pairs(path: Path) -> tuple[list[str], list[str]]:
    """The (en, it) columns of a distilled JSONL file."""
    en, it = [], []
    with path.open() as f:
        for line in f:
            row = json.loads(line)
            en.append(row["en"])
            it.append(row["it"])
    return en, it


def load_lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def chrf(hypotheses: list[str], references: list[str]) -> float:
    return sacrebleu.corpus_chrf(hypotheses, [references]).score


def translate(model, tokenizer, sources: list[str], num_beams: int, batch_size: int = 64,
              max_length: int = 256) -> list[str]:  # fmt: skip
    """Batched beam-search translation with the given model, for the FLORES check."""
    model.eval()
    out: list[str] = []
    for i in range(0, len(sources), batch_size):
        batch = tokenizer(sources[i : i + batch_size], return_tensors="pt", padding=True,
                          truncation=True, max_length=max_length).to(model.device)  # fmt: skip
        with torch.no_grad():
            generated = model.generate(**batch, num_beams=num_beams, max_length=max_length)
        out.extend(tokenizer.batch_decode(generated, skip_special_tokens=True))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config.yaml")
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args()

    cfg = yaml.safe_load(args.config.read_text())
    base = args.config.parent
    tr, ev, guard = cfg["training"], cfg["eval"], cfg["guardrail"]
    # Echo the knobs that matter, so the log proves which config actually ran (run 2 of
    # 2026-10-19 silently reused run 1's bundle left in /content: never again).
    print(
        f"config: lr {tr['learning_rate']} · epochs ≤{tr['epochs']} · "
        f"batch {tr['batch_size']} · patience {tr['early_stopping_patience']}"
    )

    import transformers
    from transformers import (
        AutoModelForSeq2SeqLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        EarlyStoppingCallback,
        Seq2SeqTrainer,
        Seq2SeqTrainingArguments,
    )

    seed = cfg["seed"]
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    transformers.set_seed(seed)

    tokenizer = AutoTokenizer.from_pretrained(cfg["model"])
    model = AutoModelForSeq2SeqLM.from_pretrained(cfg["model"])

    max_src = cfg["tokenizer"]["max_source_length"]
    max_tgt = cfg["tokenizer"]["max_target_length"]

    def encode(en: list[str], it: list[str]) -> list[dict]:
        rows = []
        for src, tgt in zip(en, it, strict=True):
            enc = tokenizer(src, truncation=True, max_length=max_src)
            enc["labels"] = tokenizer(text_target=tgt, truncation=True, max_length=max_tgt)[
                "input_ids"
            ]
            rows.append(enc)
        return rows

    train_en, train_it = load_pairs(base / cfg["data"]["train"])
    dev_en, dev_it = load_pairs(base / cfg["data"]["dev"])
    print(f"train: {len(train_en)} pairs · dev: {len(dev_en)} pairs")
    train_rows = encode(train_en, train_it)
    dev_rows = encode(dev_en, dev_it)

    def compute_metrics(eval_pred) -> dict:
        predictions, labels = eval_pred
        predictions = np.where(predictions != -100, predictions, tokenizer.pad_token_id)
        labels = np.where(labels != -100, labels, tokenizer.pad_token_id)
        hyp = tokenizer.batch_decode(predictions, skip_special_tokens=True)
        ref = tokenizer.batch_decode(labels, skip_special_tokens=True)
        return {"chrf": chrf(hyp, ref)}

    training_args = Seq2SeqTrainingArguments(
        output_dir=str(args.out / "checkpoints"),
        seed=seed,
        num_train_epochs=tr["epochs"],
        per_device_train_batch_size=tr["batch_size"],
        per_device_eval_batch_size=tr["batch_size"],
        learning_rate=tr["learning_rate"],
        warmup_ratio=tr["warmup_ratio"],
        weight_decay=tr["weight_decay"],
        label_smoothing_factor=tr["label_smoothing"],
        fp16=tr["fp16"],
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="chrf",
        greater_is_better=True,
        predict_with_generate=True,
        generation_num_beams=ev["num_beams"],
        generation_max_length=max_tgt,
        logging_steps=100,
        report_to=[],
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=train_rows,
        eval_dataset=dev_rows,
        data_collator=DataCollatorForSeq2Seq(tokenizer, model=model),
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=tr["early_stopping_patience"])],
    )
    trainer.train()
    dev_chrf = trainer.evaluate()["eval_chrf"]

    best_dir = args.out / "best"
    trainer.save_model(str(best_dir))
    tokenizer.save_pretrained(str(best_dir))
    print(f"best checkpoint saved to {best_dir} (dev chrF {dev_chrf:.1f})")

    # FLORES-200 regression check: the student and the untouched base model, translated under
    # identical conditions here, plus the absolute guardrail from the home baseline.
    flores_src = load_lines(base / cfg["data"]["flores_src"])
    flores_ref = load_lines(base / cfg["data"]["flores_ref"])
    student_hyp = translate(model, tokenizer, flores_src, ev["num_beams"], max_length=max_tgt)
    student_flores = chrf(student_hyp, flores_ref)

    base_model = AutoModelForSeq2SeqLM.from_pretrained(cfg["model"]).to(model.device)
    base_hyp = translate(base_model, tokenizer, flores_src, ev["num_beams"], max_length=max_tgt)
    base_flores = chrf(base_hyp, flores_ref)

    floor = guard["flores_baseline_chrf"] - guard["flores_margin"]
    ok = student_flores >= floor and student_flores >= base_flores - guard["flores_margin"]
    print()
    print("=" * 72)
    print("GUARDRAIL (fixed before training — plan T4)")
    print(f"  dev chrF (distilled references):   {dev_chrf:.1f}")
    print(f"  FLORES-200 devtest chrF, student:  {student_flores:.1f}")
    print(f"  FLORES-200 devtest chrF, base:     {base_flores:.1f}  (same conditions, fp16)")
    print(f"  home baseline (CT2 int8):          {guard['flores_baseline_chrf']:.1f}"
          f"  → floor {floor:.1f}")  # fmt: skip
    print(f"  FLORES check: {'PASS' if ok else 'FAIL — do not ship this checkpoint'}")
    print("  Domain check runs AT HOME: score_at_home.py must beat the base Opus-MT")
    print("  re-scored live on the private set (never uploaded here).")
    print("  This verdict is provisional: the accepting numbers are the home ones (CT2 int8).")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
