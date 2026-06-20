#!/usr/bin/env python3
"""Run a local UCI match: FusedFish vs a plain engine."""

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import chess


ROOT = Path(__file__).resolve().parent
FUSEDFISH = ROOT / "fusedfish.py"
RECKLESS = ROOT / "reckless" / "reckless"
STOCKFISH = ROOT / "stockfish" / "src" / "stockfish"


@dataclass
class EngineConfig:
    name: str
    path: Path
    options: dict[str, str]


OPPONENTS = {
    "reckless": EngineConfig("Reckless", RECKLESS, {}),
    "stockfish": EngineConfig("Stockfish", STOCKFISH, {}),
}


@dataclass
class MatchResult:
    result: str
    final_fen: str
    white: str
    black: str
    movetime: int
    movetext: str


class UciEngine:
    def __init__(self, config: EngineConfig):
        self.config = config
        self.proc: subprocess.Popen[str] | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.reader: threading.Thread | None = None

    def start(self) -> None:
        if not self.config.path.exists():
            raise FileNotFoundError(f"{self.config.name} not found: {self.config.path}")
        self.proc = subprocess.Popen(
            [str(self.config.path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self.send("uci")
        self.wait_for("uciok", 10)
        for name, value in self.config.options.items():
            self.send(f"setoption name {name} value {value}")
        self.ready()
        self.send("ucinewgame")
        self.ready()

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))

    def send(self, command: str) -> None:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError(f"{self.config.name} is not running")
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def wait_for(self, prefix: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line == prefix or line.startswith(prefix + " "):
                return line
        raise TimeoutError(f"{self.config.name} timed out waiting for {prefix}")

    def ready(self) -> None:
        self.send("isready")
        self.wait_for("readyok", 10)

    def move(self, board: chess.Board, movetime: int) -> chess.Move:
        position = "position startpos"
        if board.move_stack:
            position += " moves " + " ".join(move.uci() for move in board.move_stack)
        self.send(position)
        self.send(f"go movetime {movetime}")
        line = self.wait_for("bestmove", max(10, movetime / 1000 + 5))
        parts = line.split()
        if len(parts) < 2:
            raise RuntimeError(f"{self.config.name} returned malformed bestmove: {line}")
        try:
            move = chess.Move.from_uci(parts[1])
        except ValueError as exc:
            raise RuntimeError(f"{self.config.name} returned invalid move: {parts[1]}") from exc
        if move not in board.legal_moves:
            raise RuntimeError(f"{self.config.name} returned illegal move: {move.uci()}")
        return move

    def quit(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.send("quit")
            except Exception:
                pass
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def result_for_position(board: chess.Board) -> str:
    if board.is_checkmate():
        return "0-1" if board.turn == chess.WHITE else "1-0"
    if board.is_game_over(claim_draw=False):
        return board.result(claim_draw=False)
    return "*"


def run_game(args: argparse.Namespace) -> MatchResult:
    fused = EngineConfig(
        "FusedFish",
        FUSEDFISH,
        {
            "FusionMode": args.fusion_mode,
            "StockfishMultiPV": str(args.stockfish_multipv),
            "VetoMargin": str(args.veto_margin),
            "FusedMoveTimeScale": str(args.fused_power),
        },
    )
    opponent = OPPONENTS[args.opponent]
    white_config, black_config = (opponent, fused) if args.fused_black else (fused, opponent)
    engines = {chess.WHITE: UciEngine(white_config), chess.BLACK: UciEngine(black_config)}
    board = chess.Board()
    tokens: list[str] = []

    try:
        engines[chess.WHITE].start()
        engines[chess.BLACK].start()
        while not board.is_game_over(claim_draw=False) and board.ply() < args.max_plies:
            engine = engines[board.turn]
            move = engine.move(board, args.movetime)
            san = board.san(move)
            if board.turn == chess.WHITE:
                tokens.extend([f"{board.fullmove_number}.", san])
            else:
                tokens.append(san)
            move_label = f"{board.fullmove_number}." if board.turn == chess.WHITE else f"{board.fullmove_number}..."
            print(f"{move_label} {engine.config.name}: {san}", flush=True)
            board.push(move)
        result = result_for_position(board)
        tokens.append(result)
        return MatchResult(
            result=result,
            final_fen=board.fen(),
            white=white_config.name,
            black=black_config.name,
            movetime=args.movetime,
            movetext=" ".join(tokens),
        )
    finally:
        engines[chess.WHITE].quit()
        engines[chess.BLACK].quit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play FusedFish against a plain UCI engine.")
    parser.add_argument("--opponent", choices=sorted(OPPONENTS), default="reckless")
    parser.add_argument("--movetime", type=int, default=300, help="milliseconds per move")
    parser.add_argument("--max-plies", type=int, default=300, help="stop and mark unfinished after this many plies")
    parser.add_argument("--fused-black", action="store_true", help="play FusedFish as black")
    parser.add_argument("--fusion-mode", default="stockfish-veto")
    parser.add_argument("--stockfish-multipv", type=int, default=3)
    parser.add_argument("--veto-margin", type=int, default=35)
    parser.add_argument("--fused-power", type=float, default=3.0, help="FusedFish internal movetime multiplier")
    parser.add_argument("--pgn", default="", help="PGN output path")
    args = parser.parse_args()
    if not args.pgn:
        args.pgn = str(ROOT / f"fusedfish-vs-{args.opponent}.pgn")
    return args


def main() -> int:
    args = parse_args()
    match = run_game(args)
    pgn_path = Path(args.pgn)
    pgn = "\n".join(
        [
            f'[Event "FusedFish vs {match.black if match.white == "FusedFish" else match.white}"]',
            f'[White "{match.white}"]',
            f'[Black "{match.black}"]',
            f'[Result "{match.result}"]',
            f'[TimeControl "{match.movetime}ms/move"]',
            f'[FEN "{match.final_fen}"]',
            "",
            match.movetext,
            "",
        ]
    )
    pgn_path.write_text(pgn, encoding="utf-8")
    print()
    print(f"Result: {match.result}")
    print(f"Final FEN: {match.final_fen}")
    print(f"PGN: {pgn_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
