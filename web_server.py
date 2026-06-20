#!/usr/bin/env python3
"""Minimal web chess board for human vs checkalsovky bot."""

from __future__ import annotations

import queue
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import chess
from flask import Flask, jsonify, render_template, request


ROOT = Path(__file__).resolve().parent
FUSEDFISH = ROOT / "fusedfish.py"
STOCKFISH = ROOT / "stockfish" / "src" / "stockfish"

BOT_MOVETIME = 900
BOT_POWER = 6
EVAL_MOVETIME = 180

app = Flask(__name__)


class UciEngine:
    def __init__(self, path: Path, options: dict[str, str] | None = None):
        self.path = path
        self.options = options or {}
        self.proc: subprocess.Popen[str] | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.reader: threading.Thread | None = None
        self.lock = threading.Lock()

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(
            [str(self.path)],
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
        for name, value in self.options.items():
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
            raise RuntimeError(f"Engine is not running: {self.path}")
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
        raise TimeoutError(f"Timed out waiting for {prefix}")

    def ready(self) -> None:
        self.send("isready")
        self.wait_for("readyok", 10)

    def bestmove(self, board: chess.Board, movetime: int, timeout_scale: float = 1.0) -> str:
        with self.lock:
            moves = " ".join(move.uci() for move in board.move_stack)
            position = "position startpos" + (f" moves {moves}" if moves else "")
            self.send(position)
            self.send(f"go movetime {movetime}")
            line = self.wait_for("bestmove", movetime * timeout_scale / 1000 + 10)
        parts = line.split()
        if len(parts) < 2:
            raise RuntimeError(f"Bad bestmove response: {line}")
        return parts[1]

    def evaluate(self, board: chess.Board, movetime: int) -> int:
        with self.lock:
            moves = " ".join(move.uci() for move in board.move_stack)
            position = "position startpos" + (f" moves {moves}" if moves else "")
            self.send(position)
            self.send(f"go movetime {movetime}")
            score = 0
            deadline = time.monotonic() + movetime / 1000 + 5
            while time.monotonic() < deadline:
                try:
                    line = self.lines.get(timeout=0.05)
                except queue.Empty:
                    continue
                if line.startswith("info "):
                    parsed = parse_score(line)
                    if parsed is not None:
                        score = parsed
                if line.startswith("bestmove "):
                    return score if board.turn == chess.WHITE else -score
        return score if board.turn == chess.WHITE else -score

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


def parse_score(line: str) -> int | None:
    parts = line.split()
    if "score" not in parts:
        return None
    index = parts.index("score")
    if index + 2 >= len(parts):
        return None
    kind = parts[index + 1]
    try:
        value = int(parts[index + 2])
    except ValueError:
        return None
    if kind == "cp":
        return max(-1200, min(1200, value))
    if kind == "mate":
        return 1200 if value > 0 else -1200
    return None


@dataclass
class GameState:
    board: chess.Board


games: dict[str, GameState] = {}
fused = UciEngine(
    FUSEDFISH,
    {
        "FusionMode": "stockfish-veto",
        "StockfishMultiPV": "5",
        "VetoMargin": "25",
        "FusedMoveTimeScale": str(BOT_POWER),
        "ChildTimeout": "20",
    },
)
eval_engine = UciEngine(STOCKFISH)


def square_payload(board: chess.Board) -> dict[str, str]:
    return {chess.square_name(square): piece.symbol() for square, piece in board.piece_map().items()}


def status_for(board: chess.Board) -> str:
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "draw"
    if board.is_check():
        return "check"
    return "playing"


def payload(game_id: str, board: chess.Board, last_move: str | None = None) -> dict:
    eval_cp = eval_engine.evaluate(board, EVAL_MOVETIME)
    return {
        "gameId": game_id,
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "squares": square_payload(board),
        "legal": [move.uci() for move in board.legal_moves],
        "lastMove": last_move,
        "status": status_for(board),
        "eval": eval_cp,
    }


@app.before_request
def boot_engines() -> None:
    fused.start()
    eval_engine.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/new")
def new_game():
    game_id = uuid.uuid4().hex
    games[game_id] = GameState(chess.Board())
    return jsonify(payload(game_id, games[game_id].board))


@app.post("/api/move")
def move():
    data = request.get_json(force=True)
    game_id = data.get("gameId")
    uci = data.get("move")
    state = games.get(game_id)
    if not state:
        return jsonify({"error": "missing game"}), 404
    board = state.board
    try:
        human_move = chess.Move.from_uci(uci)
    except (TypeError, ValueError):
        return jsonify({"error": "bad move"}), 400
    if human_move not in board.legal_moves or board.turn != chess.WHITE:
        return jsonify({"error": "illegal move"}), 400
    board.push(human_move)
    last = human_move.uci()
    if not board.is_game_over(claim_draw=False):
        bot_uci = fused.bestmove(board, BOT_MOVETIME, BOT_POWER)
        bot_move = chess.Move.from_uci(bot_uci)
        if bot_move not in board.legal_moves:
            return jsonify({"error": f"bot illegal move {bot_uci}"}), 500
        board.push(bot_move)
        last = bot_move.uci()
    return jsonify(payload(game_id, board, last))


if __name__ == "__main__":
    host = os.environ.get("CHECKALSOVKY_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("CHECKALSOVKY_PORT", "5055")))
    app.run(host=host, port=port, debug=False)
