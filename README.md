# XYZStocksUS

Premium multi-agent stock signals bot for US markets with full Sharia compliance integration.

Phase 1: signals only. No paper trading. Telegram alerts + mobile-friendly dashboard.

## Quickstart (local)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
copy .env.example .env             # Windows
# cp .env.example .env             # macOS/Linux
# fill in ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN

# web (dashboard + bot)
gunicorn -b 0.0.0.0:8080 workers.web:app

# worker (scheduler)
python -m workers.scanner
```

## Deploy (Railway)

1. Create a new Railway project named `xyzstocksus`.
2. Attach a persistent volume mounted at `/data`.
3. Set environment variables from `.env.example`.
4. Push this repo. Railway will use `Procfile` + `supervisord.conf` to run both `web` and `worker` in one container.
5. Health check: `GET /health`.

## Architecture

8-agent debate (Technical, BTC Macro, Fundamentals, Risk, Devil's Advocate, Macro Voice, Sharia Officer, Synthesizer). Two-tier LLM (Haiku pre-screen + Sonnet deep). 4 NYSE-aware scans per day.

Full architecture, Sharia compliance system (AAOIFI Standard 21), and operational guide live in `CLAUDE.md` (written at the end of Phase 1).
