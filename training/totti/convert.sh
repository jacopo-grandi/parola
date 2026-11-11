#!/usr/bin/env bash
# ToTTI T4: convert the fine-tuned checkpoint to CTranslate2 int8 (decision 0038, plan T4).
# The .spm files ride along so the folder drops straight into data/models/ and the app's
# Translator (src/parola/totti/translator.py) can load it unchanged.
#
# Usage: bash convert.sh <checkpoint-dir> <output-dir>
set -euo pipefail

src="${1:?usage: convert.sh <checkpoint-dir> <output-dir>}"
out="${2:?usage: convert.sh <checkpoint-dir> <output-dir>}"

ct2-transformers-converter \
  --model "$src" \
  --output_dir "$out" \
  --quantization int8 \
  --copy_files source.spm target.spm \
  --force
