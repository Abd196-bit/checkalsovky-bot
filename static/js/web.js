const boardEl = document.getElementById("board");
const statusEl = document.getElementById("status");
const evalFill = document.getElementById("evalFill");
const evalText = document.getElementById("evalText");

let gameId = null;
let squares = {};
let legal = [];
let selected = null;
let lastMove = null;
let busy = false;

const files = ["a", "b", "c", "d", "e", "f", "g", "h"];
const ranks = ["8", "7", "6", "5", "4", "3", "2", "1"];
const pieceMap = {
  P: "wP", N: "wN", B: "wB", R: "wR", Q: "wQ", K: "wK",
  p: "bP", n: "bN", b: "bB", r: "bR", q: "bQ", k: "bK",
};

function squareName(index) {
  const file = files[index % 8];
  const rank = ranks[Math.floor(index / 8)];
  return `${file}${rank}`;
}

function render() {
  boardEl.innerHTML = "";
  for (let i = 0; i < 64; i += 1) {
    const sq = squareName(i);
    const fileIndex = i % 8;
    const rankIndex = Math.floor(i / 8);
    const square = document.createElement("button");
    square.type = "button";
    square.className = `square ${(fileIndex + rankIndex) % 2 === 0 ? "light" : "dark"}`;
    square.dataset.square = sq;
    if (selected === sq) square.classList.add("selected");
    if (lastMove && (lastMove.slice(0, 2) === sq || lastMove.slice(2, 4) === sq)) square.classList.add("last");
    if (selected && legal.some((move) => move.startsWith(selected + sq))) square.classList.add("legal");
    square.addEventListener("click", () => clickSquare(sq));

    const piece = squares[sq];
    if (piece) {
      const img = document.createElement("img");
      img.className = "piece";
      img.alt = piece;
      img.src = `/static/pieces/alpha/${pieceMap[piece]}.svg`;
      square.appendChild(img);
    }
    boardEl.appendChild(square);
  }
}

function applyState(state) {
  gameId = state.gameId;
  squares = state.squares;
  legal = state.legal || [];
  lastMove = state.lastMove || lastMove;
  updateEval(state.eval || 0);
  render();
  if (state.status === "checkmate") statusEl.textContent = "Checkmate.";
  else if (state.status === "check") statusEl.textContent = "Check.";
  else if (state.status === "stalemate") statusEl.textContent = "Stalemate.";
  else if (state.status === "draw") statusEl.textContent = "Draw.";
  else statusEl.textContent = busy ? "checkalsovky bot is calculating..." : "Your move.";
}

function updateEval(cp) {
  const clamped = Math.max(-1000, Math.min(1000, cp));
  const whitePercent = 50 + clamped / 20;
  evalFill.style.height = `${Math.max(3, Math.min(97, whitePercent))}%`;
  evalText.textContent = `${(cp / 100).toFixed(1)}`;
}

function clickSquare(sq) {
  if (busy) return;
  if (!selected) {
    if (squares[sq] && squares[sq] === squares[sq].toUpperCase()) {
      selected = sq;
      render();
    }
    return;
  }
  const move = legal.find((candidate) => candidate.startsWith(selected + sq));
  if (!move) {
    selected = squares[sq] && squares[sq] === squares[sq].toUpperCase() ? sq : null;
    render();
    return;
  }
  selected = null;
  sendMove(move);
}

function applyLocalHumanMove(move) {
  const from = move.slice(0, 2);
  const to = move.slice(2, 4);
  const promotion = move[4];
  const piece = squares[from];
  if (!piece) return;

  const next = { ...squares };
  delete next[from];

  if (piece === "P" && from[0] !== to[0] && !squares[to]) {
    const captured = `${to[0]}${Number(to[1]) - 1}`;
    delete next[captured];
  }

  if (piece === "K" && Math.abs(files.indexOf(from[0]) - files.indexOf(to[0])) === 2) {
    if (to === "g1") {
      next.f1 = next.h1;
      delete next.h1;
    } else if (to === "c1") {
      next.d1 = next.a1;
      delete next.a1;
    }
  }

  next[to] = promotion ? promotion.toUpperCase() : piece;
  squares = next;
  lastMove = move;
  legal = [];
  render();
}

async function sendMove(move) {
  applyLocalHumanMove(move);
  busy = true;
  statusEl.textContent = "checkalsovky bot is calculating...";
  try {
    const response = await fetch("/api/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gameId, move }),
    });
    const state = await response.json();
    if (!response.ok) throw new Error(state.error || "Move failed");
    busy = false;
    applyState(state);
  } catch (error) {
    busy = false;
    statusEl.textContent = error.message;
  }
}

async function newGame() {
  busy = true;
  const response = await fetch("/api/new", { method: "POST" });
  const state = await response.json();
  busy = false;
  selected = null;
  lastMove = null;
  applyState(state);
}

newGame();
