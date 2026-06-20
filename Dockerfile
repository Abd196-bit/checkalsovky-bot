FROM rust:1.96-bookworm AS engine-build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential clang make ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY stockfish ./stockfish
RUN make -C stockfish/src -j"$(nproc)" build ARCH=x86-64 COMP=gcc

COPY reckless ./reckless
RUN cd reckless \
    && cargo rustc --release --no-default-features --bin reckless -- -C target-cpu=x86-64 --emit link=reckless

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    CHECKALSOVKY_HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fusedfish.py web_server.py ./
COPY templates ./templates
COPY static ./static
COPY --from=engine-build /build/stockfish/src/stockfish ./stockfish/src/stockfish
COPY --from=engine-build /build/reckless/reckless ./reckless/reckless

RUN chmod +x fusedfish.py stockfish/src/stockfish reckless/reckless

EXPOSE 8080

CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 --timeout 180 web_server:app"]
