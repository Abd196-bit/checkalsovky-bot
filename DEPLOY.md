# Deploy checkalsovky bot

The app must run on an always-on server because the bot uses Python plus native
Stockfish/Reckless engine binaries. Static hosts such as GitHub Pages, Netlify,
and plain Vercel static deploys are not enough.

## Docker

Build and run anywhere Docker is available:

```bash
docker build -t checkalsovky-bot .
docker run -p 8080:8080 checkalsovky-bot
```

Open:

```text
http://localhost:8080
```

## Fly.io

Install and log in to `flyctl`, then:

```bash
fly launch --copy-config --name checkalsovky-bot
fly deploy
```

The included `fly.toml` keeps one machine running so the bot stays online.

## Render

Create a new Blueprint from this repo or use the included `render.yaml`.

Use at least a paid/starter web service. Free services that sleep will make the
bot unavailable or slow to wake.

## Hugging Face Spaces

This is the closest free host for this Docker app.

Log in first:

```bash
huggingface-cli login
```

Deploy:

```bash
./deploy_hf.sh checkalsovky-bot
```

Free Spaces can sleep or be slow under load, but they do not depend on your
laptop being on.

## VPS

On a Linux VPS with Docker:

```bash
git clone <your-repo-url> checkalsovky-bot
cd checkalsovky-bot
docker build -t checkalsovky-bot .
docker run -d --restart unless-stopped -p 80:8080 --name checkalsovky-bot checkalsovky-bot
```
