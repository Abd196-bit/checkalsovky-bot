#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export CARGO_HOME="$ROOT/.cargo-home"
export RUSTUP_HOME="$ROOT/.rustup-home"
export CARGO_TARGET_DIR="$ROOT/reckless/target"

mkdir -p "$CARGO_HOME" "$RUSTUP_HOME" "$CARGO_TARGET_DIR"

pip install -r requirements.txt
./build.sh
