"""/watch — show the watchlist grouped by sector with Sharia status badges."""
from __future__ import annotations

from collections import defaultdict

from core.logger import get_logger
from db.repos import runtime_config
from db.repos.stocks import latest_scores_all, list_all
from telegram_bot.alerts import ARABIC

log = get_logger("telegram.watch")


async def watch(update, context):
    stocks = list_all(enabled_only=False)
    scores = latest_scores_all([s.symbol for s in stocks])

    by_sector: dict[str, list] = defaultdict(list)
    for s in stocks:
        by_sector[s.sector].append(s)

    lines: list[str] = ["*Watchlist*\n"]
    for sector in sorted(by_sector.keys()):
        lines.append(f"\n_*{sector}*_")
        for s in sorted(by_sector[sector], key=lambda x: x.symbol):
            badge = ARABIC.get(s.sharia_status, s.sharia_status)
            score = scores.get(s.symbol, {}).get("score")
            score_s = f"  score {score:.0f}" if isinstance(score, (int, float)) else ""
            enabled_mark = "" if s.enabled else "  [disabled]"
            lines.append(f"  {badge}  *{s.symbol}*{score_s}{enabled_mark}")

    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n…"
    await update.message.reply_text(text, parse_mode="Markdown")

    runtime_config.log_command(
        chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        command="/watch", args=None, success=True,
    )
