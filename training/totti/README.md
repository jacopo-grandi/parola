# ToTTI T4 — training package (decision 0038, plan T4)

Fine-tunes Helsinki-NLP/opus-mt-en-it on the 72,522 distilled pairs of T3
(`data/totti-train/clean-train.jsonl`, teacher MADLAD-400-3B-MT) and converts the result to
CTranslate2 int8 for the app. Training runs on pay-as-you-go Colab (T4 GPU is enough: 78M
parameters, ~4M tokens per epoch, a few minutes per epoch); expected cost **~1–2 compute
units per run**. The accepting scores run at home.

## Data policy

Nothing leaves Jacopo's control except the upload bundle sent to his own Colab session:

- `clean-train.jsonl` / `clean-dev.jsonl` — AMI/ICSI-derived distilled pairs. CC BY 4.0
  public-corpus derivatives (decision 0004; teacher outputs Apache 2.0): fine to upload.
- `eng_Latn.devtest` / `ita_Latn.devtest` — FLORES-200 devtest, CC BY-SA 4.0 (the notebook
  can also re-download them from Meta's official archive if left out of the bundle).

The private test set (`data/private/totti/test-en-it.jsonl`) **never** goes to Colab: the
final scoring runs at home with `score_at_home.py`.

## Guardrail (fixed before training)

The run is accepted only if, at home with the same CT2 int8 harness as the T1 baseline
(`results/2026-10-19-totti-baseline-en-it.md`):

1. domain chrF **beats the base Opus-MT scored on the same set, the same day** (the private
   set grows as calls are translated: the recorded 60.3 was the 157-pair lecture-only set,
   today's 224 pairs score 56.1 — `score_at_home.py` re-scores the base model so the gate
   never compares across different sets), and
2. FLORES-200 devtest chrF **stays within 1.0 of 57.6** (and of the base model's live score,
   whichever is lower).

The notebook prints a provisional FLORES verdict with its own fp16 measurements (student vs
the untouched base model under identical conditions); the home numbers are the ones that
count. Thresholds live in `config.yaml` under `guardrail`.

## Run steps

1. **Build the upload bundle** (repo root):

   ```bash
   cd training/totti && rm -rf bundle && mkdir -p bundle/data \
     && cp config.yaml train.py convert.sh bundle/ \
     && cp ../../data/totti-train/clean-train.jsonl ../../data/totti-train/clean-dev.jsonl \
          ../../data/flores200/flores200_dataset/devtest/eng_Latn.devtest \
          ../../data/flores200/flores200_dataset/devtest/ita_Latn.devtest bundle/data/ \
     && (cd bundle && zip -r -q ../totti-t4-bundle.zip .) && rm -rf bundle && cd ../..
   ```

2. **Open `totti_t4_train.ipynb` in Colab** (upload it at colab.research.google.com), set the
   runtime to **T4 GPU**, and **Run all**. Upload `totti-t4-bundle.zip` when the upload cell
   asks. Training early-stops on dev chrF; the last training cell prints the provisional
   guardrail verdict (exit code 1 = FLORES check failed, do not ship that checkpoint).

3. **Download `opus-mt-en-it-totti-ct2.zip`** from the final cell and unzip it at home into
   `data/models/opus-mt-en-it-totti-ct2/`.

4. **Score at home** (the accepting numbers):

   ```bash
   uv run python training/totti/score_at_home.py data/models/opus-mt-en-it-totti-ct2
   ```

   It prints the baseline-style table plus PASS/FAIL per guardrail and the final verdict.
   If accepted, record the numbers in `results/` and point the app at the new weights.

## Files

- `config.yaml` — every knob of the run: model, seeds, hyperparameters (fp16, batch 64,
  early stopping on dev chrF), guardrail thresholds.
- `train.py` — the training script the notebook calls; prints the provisional verdict.
- `totti_t4_train.ipynb` — the Colab notebook: install, upload, train, convert, download.
- `convert.sh` — checkpoint → CTranslate2 int8 with `--copy_files source.spm target.spm`,
  ready to drop into `data/models/`.
- `score_at_home.py` — the accepting scores on the private set + FLORES, at home only.

## Licence of the resulting weights

Follows the data (CLAUDE.md): base weights Apache 2.0 / CC BY 4.0, training pairs CC BY 4.0
derivatives with Apache 2.0 teacher outputs — distributable, when publishing is decided.
