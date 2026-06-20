# FusedFish

FusedFish combines these upstream engines into one UCI bot:

- `stockfish/`: https://github.com/official-stockfish/Stockfish
- `reckless/`: https://github.com/codedeliveryservice/Reckless

The fusion layer is `fusedfish.py`. It starts both engines as UCI subprocesses,
forwards normal UCI setup commands to both, asks both to search each position,
then returns one `bestmove` to the GUI.

## Build

```bash
./build.sh
```

This builds:

- `stockfish/src/stockfish`
- `reckless/reckless`

## Run

Use this executable as the engine path in a UCI-compatible GUI:

```bash
/Users/folder1/Desktop/checkfish/fusedfish.py
```

Manual smoke test:

```bash
printf 'uci\nisready\nposition startpos\ngo depth 1\nquit\n' | ./fusedfish.py
```

## Desktop GUI

Install the GUI dependency:

```bash
python3 -m pip install --user -r requirements.txt
```

Launch the playable board:

```bash
./gui.py
```

The GUI lets you play either side, switch the fusion mode, boost FusedFish with
`Fused power`, adjust engine think time, flip the board, and start a new game.

## Web Board

Run the stripped human-vs-FusedFish web board:

```bash
python3 web_server.py
```

Open `http://127.0.0.1:5055`. The web version has no settings: just the board,
Lichess alpha piece assets, and an eval bar. FusedFish is hardcoded to boosted
`stockfish-veto` settings.

For always-on hosting, use the included [DEPLOY.md](DEPLOY.md), `Dockerfile`,
`fly.toml`, or `render.yaml`.

## FusedFish vs Plain Engines

Run a local UCI game between the fused bot and a plain engine:

```bash
./play_reckless_match.py --opponent reckless --movetime 300 --max-plies 120
./play_reckless_match.py --opponent stockfish --movetime 300 --max-plies 120
```

Use `--fused-black` to reverse colors. Use `--fused-power` to let FusedFish
spend more internal search time than the plain opponent:

```bash
./play_reckless_match.py --opponent reckless --fused-power 5
./play_reckless_match.py --opponent stockfish --fused-power 5
```

The GUI also exposes this under `Bot match` with opponent choices for plain
Reckless and plain Stockfish; moves are played live on the visible board.
Hitting the ply cap marks the game as unfinished (`*`) instead of pretending it
was a draw. The script writes PGN to `fusedfish-vs-reckless.pgn` or
`fusedfish-vs-stockfish.pgn` by default.

## UCI options

- `StockfishPath`: path to the Stockfish binary.
- `RecklessPath`: path to the Reckless binary.
- `FusionMode`:
  - `stockfish-veto`: default. Stockfish searches several candidate moves and
    acts as a tactical guard; Reckless can override only if Stockfish rates the
    Reckless move within the configured veto margin.
  - `best-score`: choose the child result with the best latest UCI score.
  - `agreement`: prefer a move if both engines agree, otherwise use best score.
  - `stockfish`: always prefer Stockfish if it returns a legal move.
  - `reckless`: always prefer Reckless if it returns a legal move.
- `StockfishMultiPV`: number of Stockfish candidate moves used by
  `stockfish-veto`.
- `VetoMargin`: maximum Stockfish centipawn loss allowed before Reckless is
  rejected in `stockfish-veto`.
- `FusedMoveTimeScale`: multiplier for `go movetime` searches inside FusedFish.
  Higher values make FusedFish stronger but slower.
- `ChildTimeout`: seconds to wait for each child engine.
- `EchoChildInfo`: emit child `info` output as `info string ...`.

## Notes

This is a process-level fusion, not a source-level merge of C++ and Rust search
internals. It keeps both upstream repositories maintainable and makes the fused
engine usable anywhere a UCI engine is accepted.
