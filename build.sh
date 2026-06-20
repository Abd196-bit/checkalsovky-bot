#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Render sets CARGO_HOME to /usr/local/cargo, which is read-only during builds.
export CARGO_HOME="$ROOT/.cargo-home"
export RUSTUP_HOME="${RUSTUP_HOME:-$ROOT/.rustup-home}"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$ROOT/reckless/target}"

mkdir -p "$CARGO_HOME" "$RUSTUP_HOME" "$CARGO_TARGET_DIR"

echo "Building Stockfish..."
make -C "$ROOT/stockfish/src" -j"$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 2)" build

echo "Building Reckless..."
(
  cd "$ROOT/reckless"
  cargo rustc --release --no-default-features --bin reckless -- -C target-cpu=native --emit link=reckless
)

chmod +x "$ROOT/fusedfish.py"

echo
echo "FusedFish is ready:"
echo "  $ROOT/fusedfish.py"
