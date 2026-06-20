#!/usr/bin/env python3
"""FusedFish: a UCI wrapper that combines Stockfish and Reckless.

The wrapper keeps both engines as separate UCI subprocesses and arbitrates
between their best moves. This avoids mixing C++ and Rust internals while still
presenting one UCI-compatible bot to chess GUIs.
"""

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_STOCKFISH = ROOT / "stockfish" / "src" / "stockfish"
DEFAULT_RECKLESS = ROOT / "reckless" / "reckless"

MATE_SCORE = 100_000
DEFAULT_TIMEOUT = 2.0


def emit(line: str) -> None:
    print(line, flush=True)


def option_payload(command: str) -> tuple[str, str] | None:
    parts = command.split()
    if len(parts) < 4 or parts[0] != "setoption" or parts[1] != "name":
        return None
    try:
        value_index = parts.index("value")
    except ValueError:
        name = " ".join(parts[2:])
        value = ""
    else:
        name = " ".join(parts[2:value_index])
        value = " ".join(parts[value_index + 1 :])
    return name, value


def score_from_info(line: str) -> int | None:
    parts = line.split()
    if "score" not in parts:
        return None
    i = parts.index("score")
    if i + 2 >= len(parts):
        return None
    kind, raw = parts[i + 1], parts[i + 2]
    try:
        value = int(raw)
    except ValueError:
        return None
    if kind == "cp":
        return value
    if kind == "mate":
        if value > 0:
            return MATE_SCORE - value
        return -MATE_SCORE - value
    return None


def timeout_for_go(command: str, fallback: float) -> float:
    parts = command.split()
    if "movetime" in parts:
        try:
            milliseconds = int(parts[parts.index("movetime") + 1])
        except (ValueError, IndexError):
            return fallback
        return max(fallback, milliseconds / 1000.0 + 2.0)
    return fallback


def scale_go_movetime(command: str, scale: float) -> str:
    if scale <= 1.0:
        return command
    parts = command.split()
    if "movetime" not in parts:
        return command
    index = parts.index("movetime") + 1
    if index >= len(parts):
        return command
    try:
        milliseconds = int(parts[index])
    except ValueError:
        return command
    parts[index] = str(max(1, int(milliseconds * scale)))
    return " ".join(parts)


@dataclass
class SearchResult:
    name: str
    bestmove: str | None = None
    ponder: str | None = None
    score: int | None = None
    depth: int = 0
    candidates: dict[str, tuple[int, int]] | None = None
    failed: str | None = None

    def __post_init__(self) -> None:
        if self.candidates is None:
            self.candidates = {}


class ChildEngine:
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.proc: subprocess.Popen[str] | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.reader: threading.Thread | None = None
        self.alive = False

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        binary = Path(os.path.expanduser(self.path))
        if not binary.exists():
            raise FileNotFoundError(f"{self.name} binary not found: {binary}")
        self.proc = subprocess.Popen(
            [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.alive = True
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self.send("uci")
        self.wait_for("uciok", DEFAULT_TIMEOUT)

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))
        self.alive = False

    def send(self, command: str) -> None:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError(f"{self.name} is not running")
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def wait_for(self, token: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line == token:
                return True
        return False

    def ready(self, timeout: float) -> bool:
        self.send("isready")
        return self.wait_for("readyok", timeout)

    def search(self, command: str, timeout: float, echo: bool) -> SearchResult:
        result = SearchResult(self.name)
        try:
            self.send(command)
        except Exception as exc:
            result.failed = str(exc)
            return result

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.05)
            except queue.Empty:
                if self.proc and self.proc.poll() is not None:
                    result.failed = f"{self.name} exited"
                    return result
                continue
            if echo and line.startswith("info "):
                emit(f"info string {self.name}: {line[5:]}")
            parsed_score = score_from_info(line)
            if parsed_score is not None:
                result.score = parsed_score
            if line.startswith("info "):
                parts = line.split()
                depth = result.depth
                if "depth" in parts:
                    try:
                        depth = int(parts[parts.index("depth") + 1])
                        result.depth = max(result.depth, depth)
                    except (ValueError, IndexError):
                        pass
                if parsed_score is not None and "pv" in parts:
                    try:
                        move = parts[parts.index("pv") + 1]
                    except IndexError:
                        move = ""
                    if move:
                        previous = result.candidates.get(move)
                        if previous is None or depth >= previous[1]:
                            result.candidates[move] = (parsed_score, depth)
            if line.startswith("bestmove "):
                parts = line.split()
                result.bestmove = parts[1] if len(parts) > 1 else None
                if len(parts) > 3 and parts[2] == "ponder":
                    result.ponder = parts[3]
                return result
        try:
            self.send("stop")
        except Exception:
            pass
        result.failed = f"{self.name} timed out"
        return result

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


class FusedFish:
    def __init__(self) -> None:
        self.stockfish_path = str(DEFAULT_STOCKFISH)
        self.reckless_path = str(DEFAULT_RECKLESS)
        self.mode = "stockfish-veto"
        self.child_timeout = 2.0
        self.stockfish_multipv = 3
        self.veto_margin = 35
        self.move_time_scale = 1.0
        self.echo_child_info = False
        self.children: list[ChildEngine] = []
        self.pending_position = "position startpos"

    def ensure_children(self) -> bool:
        if not self.children:
            self.children = [
                ChildEngine("stockfish", self.stockfish_path),
                ChildEngine("reckless", self.reckless_path),
            ]
        ok = True
        for child in self.children:
            try:
                child.start()
            except Exception as exc:
                emit(f"info string {exc}")
                ok = False
        return ok

    def setoption(self, command: str) -> None:
        parsed = option_payload(command)
        if not parsed:
            return
        name, value = parsed
        lowered = name.lower()
        if lowered == "stockfishpath":
            self.stockfish_path = value
            self.children = []
        elif lowered == "recklesspath":
            self.reckless_path = value
            self.children = []
        elif lowered == "fusionmode":
            self.mode = value
        elif lowered == "stockfishmultipv":
            try:
                self.stockfish_multipv = min(10, max(1, int(value)))
            except ValueError:
                emit(f"info string invalid StockfishMultiPV: {value}")
        elif lowered == "vetomargin":
            try:
                self.veto_margin = max(0, int(value))
            except ValueError:
                emit(f"info string invalid VetoMargin: {value}")
        elif lowered == "fusedmovetimescale":
            try:
                self.move_time_scale = min(10.0, max(1.0, float(value)))
            except ValueError:
                emit(f"info string invalid FusedMoveTimeScale: {value}")
        elif lowered == "childtimeout":
            try:
                self.child_timeout = max(0.1, float(value))
            except ValueError:
                emit(f"info string invalid ChildTimeout: {value}")
        elif lowered == "echochildinfo":
            self.echo_child_info = value.lower() in {"true", "1", "yes", "on"}
        else:
            self.forward(command)

    def forward(self, command: str) -> None:
        if not self.ensure_children():
            return
        for child in self.children:
            try:
                child.send(command)
            except Exception as exc:
                emit(f"info string {child.name} forward failed: {exc}")

    def isready(self) -> None:
        if self.ensure_children():
            for child in self.children:
                try:
                    child.ready(DEFAULT_TIMEOUT)
                except Exception as exc:
                    emit(f"info string {child.name} isready failed: {exc}")
        emit("readyok")

    def choose(self, results: Iterable[SearchResult]) -> SearchResult | None:
        playable = [r for r in results if r.bestmove and r.bestmove != "(none)"]
        if not playable:
            return None
        if self.mode == "stockfish-veto":
            stockfish = next((r for r in playable if r.name == "stockfish"), None)
            reckless = next((r for r in playable if r.name == "reckless"), None)
            if not stockfish:
                return reckless or playable[0]
            if not reckless or not reckless.bestmove:
                return stockfish
            if reckless.bestmove == stockfish.bestmove:
                return stockfish
            stockfish_score = stockfish.candidates.get(stockfish.bestmove or "", (stockfish.score, stockfish.depth))[0]
            reckless_score = stockfish.candidates.get(reckless.bestmove, (None, 0))[0]
            if stockfish_score is not None and reckless_score is not None:
                if stockfish_score - reckless_score <= self.veto_margin:
                    emit(
                        "info string stockfish-veto accepted reckless "
                        f"{reckless.bestmove}; Stockfish delta {stockfish_score - reckless_score} cp"
                    )
                    return reckless
                emit(
                    "info string stockfish-veto rejected reckless "
                    f"{reckless.bestmove}; Stockfish delta {stockfish_score - reckless_score} cp"
                )
            return stockfish
        if self.mode == "stockfish":
            return next((r for r in playable if r.name == "stockfish"), playable[0])
        if self.mode == "reckless":
            return next((r for r in playable if r.name == "reckless"), playable[0])
        if self.mode == "agreement":
            moves = {}
            for result in playable:
                moves.setdefault(result.bestmove, []).append(result)
            agreed = [group for group in moves.values() if len(group) > 1]
            if agreed:
                return max(agreed[0], key=lambda r: (r.score is not None, r.score or -MATE_SCORE))
        return max(playable, key=lambda r: (r.score is not None, r.score or -MATE_SCORE, r.depth))

    def go(self, command: str) -> None:
        if not self.ensure_children():
            emit("bestmove 0000")
            return
        child_command = scale_go_movetime(command, self.move_time_scale)
        search_timeout = timeout_for_go(child_command, self.child_timeout)
        for child in self.children:
            try:
                if child.name == "stockfish":
                    multipv = self.stockfish_multipv if self.mode == "stockfish-veto" else 1
                    child.send(f"setoption name MultiPV value {multipv}")
                    child.ready(DEFAULT_TIMEOUT)
                child.send(self.pending_position)
            except Exception as exc:
                emit(f"info string {child.name} position failed: {exc}")

        threads: list[threading.Thread] = []
        results: list[SearchResult] = []
        lock = threading.Lock()

        def run(child: ChildEngine) -> None:
            result = child.search(child_command, search_timeout, self.echo_child_info)
            with lock:
                results.append(result)

        for child in self.children:
            thread = threading.Thread(target=run, args=(child,), daemon=True)
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()

        for result in results:
            if result.failed:
                emit(f"info string {result.failed}")
            elif result.bestmove:
                score = "none" if result.score is None else str(result.score)
                emit(f"info string {result.name} bestmove {result.bestmove} score {score} depth {result.depth}")
        choice = self.choose(results)
        if not choice:
            emit("bestmove 0000")
            return
        ponder = f" ponder {choice.ponder}" if choice.ponder else ""
        emit(f"bestmove {choice.bestmove}{ponder}")

    def loop(self) -> None:
        for raw in sys.stdin:
            command = raw.strip()
            if not command:
                continue
            if command == "uci":
                emit("id name FusedFish")
                emit("id author OpenAI Codex wrapper over Stockfish and Reckless")
                emit(f"option name StockfishPath type string default {self.stockfish_path}")
                emit(f"option name RecklessPath type string default {self.reckless_path}")
                emit(
                    "option name FusionMode type combo default stockfish-veto "
                    "var stockfish-veto var best-score var agreement var stockfish var reckless"
                )
                emit("option name StockfishMultiPV type spin default 3 min 1 max 10")
                emit("option name VetoMargin type spin default 35 min 0 max 1000")
                emit("option name FusedMoveTimeScale type spin default 1 min 1 max 10")
                emit("option name ChildTimeout type spin default 2 min 1 max 3600")
                emit("option name EchoChildInfo type check default false")
                emit("uciok")
            elif command == "isready":
                self.isready()
            elif command.startswith("setoption "):
                self.setoption(command)
            elif command == "ucinewgame":
                self.forward(command)
            elif command.startswith("position "):
                self.pending_position = command
                self.forward(command)
            elif command.startswith("go"):
                self.go(command)
            elif command == "stop":
                self.forward(command)
            elif command == "quit":
                for child in self.children:
                    child.quit()
                return
            else:
                self.forward(command)


def main() -> int:
    if len(sys.argv) > 1:
        emit("FusedFish is a UCI engine. Start it from a UCI GUI or pipe UCI commands to stdin.")
        emit(f"Default Stockfish: {shlex.quote(str(DEFAULT_STOCKFISH))}")
        emit(f"Default Reckless: {shlex.quote(str(DEFAULT_RECKLESS))}")
        return 0
    FusedFish().loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
