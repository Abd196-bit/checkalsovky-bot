#!/usr/bin/env python3
"""Desktop GUI for playing against FusedFish."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import chess


ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / "fusedfish.py"
RECKLESS = ROOT / "reckless" / "reckless"
STOCKFISH = ROOT / "stockfish" / "src" / "stockfish"
BOARD_SIZE = 560
SQUARE = BOARD_SIZE // 8

LIGHT = "#e8d9bd"
DARK = "#7a8f67"
SELECTED = "#f5c542"
LEGAL = "#d95f38"
LAST_MOVE = "#9fc5e8"
PANEL = "#f4f1eb"
TEXT = "#202124"

PIECES = {
    chess.PAWN: {chess.WHITE: "♙", chess.BLACK: "♟"},
    chess.KNIGHT: {chess.WHITE: "♘", chess.BLACK: "♞"},
    chess.BISHOP: {chess.WHITE: "♗", chess.BLACK: "♝"},
    chess.ROOK: {chess.WHITE: "♖", chess.BLACK: "♜"},
    chess.QUEEN: {chess.WHITE: "♕", chess.BLACK: "♛"},
    chess.KING: {chess.WHITE: "♔", chess.BLACK: "♚"},
}


class UciEngine:
    def __init__(self, path: Path, name: str = "Engine", options: dict[str, str] | None = None):
        self.path = path
        self.name = name
        self.options = options or {}
        self.proc: subprocess.Popen[str] | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.reader: threading.Thread | None = None
        self.lock = threading.Lock()

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"Engine not found: {self.path}")
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
        self.wait_for("uciok", 5)
        for name, value in self.options.items():
            self.send(f"setoption name {name} value {value}")
        self.send("isready")
        self.wait_for("readyok", 10)
        self.send("ucinewgame")
        self.send("isready")
        self.wait_for("readyok", 10)

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))

    def send(self, command: str) -> None:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError("Engine is not running")
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def wait_for(self, token: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line == token or line.startswith(token + " "):
                return line
        raise TimeoutError(f"Timed out waiting for {token}")

    def configure(self, mode: str, movetime_ms: int, scale: float = 1.0) -> None:
        timeout = max(2, int(movetime_ms / 1000) + 3)
        self.send(f"setoption name FusionMode value {mode}")
        self.send(f"setoption name FusedMoveTimeScale value {scale}")
        self.send(f"setoption name ChildTimeout value {timeout}")
        self.send("isready")
        self.wait_for("readyok", 10)

    def bestmove(self, moves: list[str], mode: str, movetime_ms: int, scale: float = 1.0) -> str:
        with self.lock:
            if mode:
                self.configure(mode, movetime_ms, scale)
            position = "position startpos"
            if moves:
                position += " moves " + " ".join(moves)
            self.send(position)
            self.send(f"go movetime {movetime_ms}")
            line = self.wait_for("bestmove", max(5, movetime_ms / 1000 + 5))
        parts = line.split()
        if len(parts) < 2:
            raise RuntimeError(f"Bad bestmove response: {line}")
        return parts[1]

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


class FusedFishGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FusedFish")
        self.resizable(False, False)
        self.configure(bg=PANEL)

        self.board = chess.Board()
        self.engine = UciEngine(ENGINE)
        self.selected: chess.Square | None = None
        self.legal_targets: set[chess.Square] = set()
        self.human_color = chess.WHITE
        self.flipped = False
        self.thinking = False
        self.last_move: chess.Move | None = None

        self.mode_var = tk.StringVar(value="stockfish-veto")
        self.side_var = tk.StringVar(value="White")
        self.time_var = tk.IntVar(value=1000)
        self.match_opponent_var = tk.StringVar(value="Reckless")
        self.match_color_var = tk.StringVar(value="White")
        self.match_plies_var = tk.IntVar(value=300)
        self.power_var = tk.IntVar(value=3)
        self.status_var = tk.StringVar(value="Starting engine...")
        self.matching = False

        self._build_ui()
        self.after(50, self._start_engine)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")

        self.canvas = tk.Canvas(
            outer,
            width=BOARD_SIZE,
            height=BOARD_SIZE,
            highlightthickness=1,
            highlightbackground="#918a80",
        )
        self.canvas.grid(row=0, column=0, rowspan=2)
        self.canvas.bind("<Button-1>", self._click)

        controls = ttk.Frame(outer, padding=(12, 0, 0, 0))
        controls.grid(row=0, column=1, sticky="new")

        ttk.Button(controls, text="New Game", command=self.new_game).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(controls, text="Flip Board", command=self.flip_board).grid(row=1, column=0, sticky="ew", pady=(0, 12))

        ttk.Label(controls, text="Play as").grid(row=2, column=0, sticky="w")
        side_box = ttk.Combobox(controls, textvariable=self.side_var, values=["White", "Black"], state="readonly", width=18)
        side_box.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        side_box.bind("<<ComboboxSelected>>", lambda _event: self.new_game())

        ttk.Label(controls, text="Fusion mode").grid(row=4, column=0, sticky="w")
        mode_box = ttk.Combobox(
            controls,
            textvariable=self.mode_var,
            values=["stockfish-veto", "best-score", "agreement", "stockfish", "reckless"],
            state="readonly",
            width=18,
        )
        mode_box.grid(row=5, column=0, sticky="ew", pady=(0, 12))

        ttk.Label(controls, text="Think time").grid(row=6, column=0, sticky="w")
        ttk.Scale(controls, from_=250, to=5000, variable=self.time_var, orient="horizontal").grid(
            row=7, column=0, sticky="ew"
        )
        self.time_label = ttk.Label(controls, text="")
        self.time_label.grid(row=8, column=0, sticky="w", pady=(0, 12))
        self.time_var.trace_add("write", lambda *_args: self._update_time_label())
        self._update_time_label()

        ttk.Label(controls, text="Fused power").grid(row=9, column=0, sticky="w")
        ttk.Spinbox(controls, from_=1, to=10, increment=1, textvariable=self.power_var, width=18).grid(
            row=10, column=0, sticky="ew", pady=(0, 8)
        )

        ttk.Button(controls, text="Engine Move", command=self.request_engine_move).grid(
            row=11, column=0, sticky="ew", pady=(0, 8)
        )

        ttk.Separator(controls).grid(row=12, column=0, sticky="ew", pady=8)
        ttk.Label(controls, text="Bot match").grid(row=13, column=0, sticky="w")

        ttk.Label(controls, text="Opponent").grid(row=14, column=0, sticky="w")
        opponent_box = ttk.Combobox(
            controls,
            textvariable=self.match_opponent_var,
            values=["Reckless", "Stockfish"],
            state="readonly",
            width=18,
        )
        opponent_box.grid(row=15, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(controls, text="FusedFish color").grid(row=16, column=0, sticky="w")
        color_box = ttk.Combobox(
            controls,
            textvariable=self.match_color_var,
            values=["White", "Black"],
            state="readonly",
            width=18,
        )
        color_box.grid(row=17, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(controls, text="Ply cap").grid(row=18, column=0, sticky="w")
        ttk.Spinbox(controls, from_=20, to=600, increment=10, textvariable=self.match_plies_var, width=18).grid(
            row=19, column=0, sticky="ew", pady=(0, 8)
        )

        ttk.Button(controls, text="Start Bot Match", command=self.start_bot_match).grid(
            row=20, column=0, sticky="ew", pady=(0, 8)
        )

        status = ttk.Label(outer, textvariable=self.status_var, wraplength=BOARD_SIZE, anchor="w")
        status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self.draw()

    def _start_engine(self) -> None:
        try:
            self.engine.start()
        except Exception as exc:
            self.status_var.set(str(exc))
            messagebox.showerror("FusedFish", str(exc))
            return
        self.status_var.set("Your move.")
        if self.human_color == chess.BLACK:
            self.request_engine_move()

    def _update_time_label(self) -> None:
        self.time_label.configure(text=f"{self.time_var.get()} ms")

    def start_bot_match(self) -> None:
        if self.matching:
            return
        opponent = self.match_opponent_var.get().lower()
        fused_black = self.match_color_var.get() == "Black"
        movetime = self.time_var.get()
        max_plies = self.match_plies_var.get()
        mode = self.mode_var.get()
        power = self.power_var.get()
        pgn = ROOT / f"fusedfish-vs-{opponent}.pgn"
        self.board.reset()
        self.selected = None
        self.legal_targets = set()
        self.last_move = None
        self.draw()
        self.matching = True
        self.thinking = False
        self.status_var.set(f"Bot match running: FusedFish vs {self.match_opponent_var.get()}...")
        thread = threading.Thread(
            target=self._bot_match_worker,
            args=(opponent, fused_black, movetime, max_plies, mode, power, pgn),
            daemon=True,
        )
        thread.start()

    def _bot_match_worker(
        self,
        opponent: str,
        fused_black: bool,
        movetime: int,
        max_plies: int,
        mode: str,
        power: int,
        pgn: Path,
    ) -> None:
        opponent_name = "Stockfish" if opponent == "stockfish" else "Reckless"
        opponent_path = STOCKFISH if opponent == "stockfish" else RECKLESS
        fused = UciEngine(
            ENGINE,
            "FusedFish",
            {
                "FusionMode": mode,
                "StockfishMultiPV": "3",
                "VetoMargin": "35",
                "FusedMoveTimeScale": str(power),
                "ChildTimeout": str(max(2, int(movetime / 1000) + 3)),
            },
        )
        plain = UciEngine(opponent_path, opponent_name)
        white_engine, black_engine = (plain, fused) if fused_black else (fused, plain)
        engines = {chess.WHITE: white_engine, chess.BLACK: black_engine}
        board = chess.Board()
        tokens: list[str] = []
        try:
            white_engine.start()
            black_engine.start()
            self.after(0, self._bot_match_status, f"{white_engine.name} vs {black_engine.name}")
            while not board.is_game_over(claim_draw=False) and board.ply() < max_plies:
                engine = engines[board.turn]
                move_mode = mode if engine.name == "FusedFish" else ""
                move_scale = power if engine.name == "FusedFish" else 1
                uci = engine.bestmove([move.uci() for move in board.move_stack], move_mode, movetime, move_scale)
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise RuntimeError(f"{engine.name} returned illegal move: {uci}")
                san = board.san(move)
                if board.turn == chess.WHITE:
                    tokens.extend([f"{board.fullmove_number}.", san])
                    move_label = f"{board.fullmove_number}."
                else:
                    tokens.append(san)
                    move_label = f"{board.fullmove_number}..."
                board.push(move)
                self.after(0, self._show_bot_move, board.copy(stack=True), move, f"{move_label} {engine.name}: {san}")
                time.sleep(0.12)
            result = self._match_result(board)
            tokens.append(result)
            self._write_match_pgn(pgn, white_engine.name, black_engine.name, result, movetime, board.fen(), " ".join(tokens))
            if result == "*":
                message = f"Bot match reached ply cap; game unfinished. PGN: {pgn}"
            else:
                message = f"Bot match {result}. PGN: {pgn}"
            self.after(0, self._bot_match_done, message)
        except Exception as exc:
            self.after(0, self._bot_match_done, str(exc))
        finally:
            white_engine.quit()
            black_engine.quit()

    def _show_bot_move(self, board: chess.Board, move: chess.Move, message: str) -> None:
        self.board = board
        self.last_move = move
        self.selected = None
        self.legal_targets = set()
        self.draw()
        self.status_var.set(message)

    def _bot_match_status(self, message: str) -> None:
        self.status_var.set(message)

    def _bot_match_done(self, message: str) -> None:
        self.matching = False
        self.status_var.set(message)

    def _match_result(self, board: chess.Board) -> str:
        if board.is_checkmate():
            return "0-1" if board.turn == chess.WHITE else "1-0"
        if board.is_game_over(claim_draw=False):
            return board.result(claim_draw=False)
        return "*"

    def _write_match_pgn(
        self,
        path: Path,
        white: str,
        black: str,
        result: str,
        movetime: int,
        fen: str,
        movetext: str,
    ) -> None:
        pgn = "\n".join(
            [
                f'[Event "{white} vs {black}"]',
                f'[White "{white}"]',
                f'[Black "{black}"]',
                f'[Result "{result}"]',
                f'[TimeControl "{movetime}ms/move"]',
                f'[FEN "{fen}"]',
                "",
                movetext,
                "",
            ]
        )
        path.write_text(pgn, encoding="utf-8")

    def new_game(self) -> None:
        if self.thinking or self.matching:
            return
        self.board.reset()
        self.selected = None
        self.legal_targets = set()
        self.last_move = None
        self.human_color = chess.WHITE if self.side_var.get() == "White" else chess.BLACK
        self.status_var.set("Your move." if self.board.turn == self.human_color else "Engine thinking...")
        self.draw()
        if self.board.turn != self.human_color:
            self.request_engine_move()

    def flip_board(self) -> None:
        self.flipped = not self.flipped
        self.draw()

    def square_at(self, x: int, y: int) -> chess.Square:
        file_index = x // SQUARE
        rank_index = 7 - (y // SQUARE)
        if self.flipped:
            file_index = 7 - file_index
            rank_index = 7 - rank_index
        return chess.square(file_index, rank_index)

    def coords_for(self, square: chess.Square) -> tuple[int, int]:
        file_index = chess.square_file(square)
        rank_index = chess.square_rank(square)
        if self.flipped:
            file_index = 7 - file_index
            rank_index = 7 - rank_index
        return file_index * SQUARE, (7 - rank_index) * SQUARE

    def _click(self, event: tk.Event) -> None:
        if self.matching or self.thinking or self.board.is_game_over() or self.board.turn != self.human_color:
            return
        square = self.square_at(event.x, event.y)
        piece = self.board.piece_at(square)
        if self.selected is None:
            if piece and piece.color == self.human_color:
                self.select(square)
            return
        move = self.build_move(self.selected, square)
        if move and move in self.board.legal_moves:
            self.board.push(move)
            self.last_move = move
            self.selected = None
            self.legal_targets = set()
            self.draw()
            self.after(100, self.request_engine_move)
            return
        if piece and piece.color == self.human_color:
            self.select(square)
        else:
            self.selected = None
            self.legal_targets = set()
            self.draw()

    def build_move(self, source: chess.Square, target: chess.Square) -> chess.Move | None:
        move = chess.Move(source, target)
        if move in self.board.legal_moves:
            return move
        piece = self.board.piece_at(source)
        if piece and piece.piece_type == chess.PAWN and chess.square_rank(target) in {0, 7}:
            promoted = chess.Move(source, target, promotion=chess.QUEEN)
            if promoted in self.board.legal_moves:
                return promoted
        return None

    def select(self, square: chess.Square) -> None:
        self.selected = square
        self.legal_targets = {move.to_square for move in self.board.legal_moves if move.from_square == square}
        self.draw()

    def request_engine_move(self) -> None:
        if self.matching or self.thinking or self.board.is_game_over():
            self._update_status()
            return
        if self.board.turn == self.human_color:
            return
        self.thinking = True
        self.status_var.set("Engine thinking...")
        moves = [move.uci() for move in self.board.move_stack]
        mode = self.mode_var.get()
        movetime = self.time_var.get()
        power = self.power_var.get()
        thread = threading.Thread(target=self._engine_worker, args=(moves, mode, movetime, power), daemon=True)
        thread.start()

    def _engine_worker(self, moves: list[str], mode: str, movetime: int, power: int) -> None:
        try:
            best = self.engine.bestmove(moves, mode, movetime, power)
            self.after(0, self._apply_engine_move, best)
        except Exception as exc:
            self.after(0, self._engine_failed, str(exc))

    def _apply_engine_move(self, uci: str) -> None:
        self.thinking = False
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            self._engine_failed(f"Engine returned invalid move: {uci}")
            return
        if move not in self.board.legal_moves:
            self._engine_failed(f"Engine returned illegal move: {uci}")
            return
        self.board.push(move)
        self.last_move = move
        self.draw()
        self._update_status()

    def _engine_failed(self, message: str) -> None:
        self.thinking = False
        self.status_var.set(message)

    def _update_status(self) -> None:
        if self.board.is_checkmate():
            winner = "Black" if self.board.turn == chess.WHITE else "White"
            self.status_var.set(f"Checkmate. {winner} wins.")
        elif self.board.is_stalemate():
            self.status_var.set("Stalemate.")
        elif self.board.is_insufficient_material():
            self.status_var.set("Draw by insufficient material.")
        elif self.board.can_claim_draw():
            self.status_var.set("Draw can be claimed.")
        elif self.board.is_check():
            self.status_var.set("Check. Your move." if self.board.turn == self.human_color else "Check. Engine thinking...")
        else:
            self.status_var.set("Your move." if self.board.turn == self.human_color else "Engine thinking...")

    def draw(self) -> None:
        self.canvas.delete("all")
        for square in chess.SQUARES:
            x, y = self.coords_for(square)
            color = LIGHT if (chess.square_file(square) + chess.square_rank(square)) % 2 else DARK
            if self.last_move and square in {self.last_move.from_square, self.last_move.to_square}:
                color = LAST_MOVE
            if self.selected == square:
                color = SELECTED
            self.canvas.create_rectangle(x, y, x + SQUARE, y + SQUARE, fill=color, outline=color)
            if square in self.legal_targets:
                cx = x + SQUARE // 2
                cy = y + SQUARE // 2
                self.canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, fill=LEGAL, outline="")
            piece = self.board.piece_at(square)
            if piece:
                label = PIECES[piece.piece_type][piece.color]
                fill = "#f7f3e8" if piece.color == chess.WHITE else "#171717"
                shadow = "#4d4038" if piece.color == chess.WHITE else "#d8cab7"
                self.canvas.create_text(
                    x + SQUARE // 2 + 2,
                    y + SQUARE // 2 + 2,
                    text=label,
                    font=("Arial Unicode MS", 42),
                    fill=shadow,
                )
                self.canvas.create_text(
                    x + SQUARE // 2,
                    y + SQUARE // 2,
                    text=label,
                    font=("Arial Unicode MS", 42),
                    fill=fill,
                )

        for file_index in range(8):
            file_name = chess.FILE_NAMES[7 - file_index if self.flipped else file_index]
            self.canvas.create_text(
                file_index * SQUARE + 8,
                BOARD_SIZE - 8,
                text=file_name,
                anchor="sw",
                font=("Helvetica", 10, "bold"),
                fill="#303030",
            )
        for rank_row in range(8):
            rank_name = str(rank_row + 1 if self.flipped else 8 - rank_row)
            self.canvas.create_text(
                6,
                rank_row * SQUARE + 6,
                text=rank_name,
                anchor="nw",
                font=("Helvetica", 10, "bold"),
                fill="#303030",
            )

    def _close(self) -> None:
        self.engine.quit()
        self.destroy()


def main() -> int:
    try:
        app = FusedFishGui()
        app.mainloop()
        return 0
    except ImportError as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with: python3 -m pip install --user -r requirements.txt", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
