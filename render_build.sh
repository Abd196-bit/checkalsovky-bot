#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export CARGO_HOME="$ROOT/.cargo-home"
export RUSTUP_HOME="$ROOT/.rustup-home"
export CARGO_TARGET_DIR="$ROOT/reckless/target"

mkdir -p "$CARGO_HOME" "$RUSTUP_HOME" "$CARGO_TARGET_DIR"

if command -v rustup >/dev/null 2>&1; then
  rustup toolchain install stable --profile minimal
fi

pip install -r requirements.txt
./build.sh
