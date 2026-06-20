#!/usr/bin/env bash
set -euo pipefail

SPACE="${1:-checkalsovky-bot}"
USER_NAME="$(huggingface-cli whoami | head -1)"

if [[ -z "$USER_NAME" || "$USER_NAME" == "Not logged in" ]]; then
  echo "Not logged in to Hugging Face. Run: huggingface-cli login"
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

huggingface-cli repo create "$SPACE" --type space --space_sdk docker --yes || true
git clone "https://huggingface.co/spaces/${USER_NAME}/${SPACE}" "$TMP_DIR/space"

rsync -a \
  --exclude .git \
  --exclude reckless/.git \
  --exclude reckless/target \
  --exclude stockfish/.git \
  --exclude 'stockfish/src/*.o' \
  --exclude stockfish/src/stockfish \
  --exclude reckless/reckless \
  ./ "$TMP_DIR/space/"

cp Dockerfile.hf "$TMP_DIR/space/Dockerfile"
cp README-hf.md "$TMP_DIR/space/README.md"

cd "$TMP_DIR/space"
git add .
git commit -m "Deploy checkalsovky bot" || true
git push

echo "https://huggingface.co/spaces/${USER_NAME}/${SPACE}"
