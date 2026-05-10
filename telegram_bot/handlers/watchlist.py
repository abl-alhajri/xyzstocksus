"""/watch — show the watchlist grouped by sector with Sharia status badges."""
from __future__ import annotations

from collections import defaultdict
from html import escape as h

from core.logger import get_logger
from db.repos import runtime_config
from db.repos.stocks import latest_scores_all, list_all
from telegram_bot.alerts import ARABIC
from telegram_bot.safe_reply import safe_html_reply

log = get_logger("telegram.watch")

_MAX_LEN = 3500
_TRUNCATE_SUFFIX = "\n…(truncated)"


async def watch(update, context):
    stocks = list_all(enabled_only=False)
    scores = latest_scores_all([s.symbol for s in stocks])

    by_sector: dict[str, list] = defaultdict(list)
    for s in stocks:
        by_sector[s.sector].append(s)

    lines: list[str] = ["<b>Watchlist</b>", ""]
    for sector in sorted(by_sector.keys()):
        lines.append(f"\n<b><i>{h(sector)}</i></b>")
        for s in sorted(by_sector[sector], key=lambda x: x.symbol):
            badge = ARABIC.get(s.sharia_status, s.sharia_status)
            score = scores.get(s.symbol, {}).get("score")
            score_s = f"  score {score:.0f}" if isinstance(score, (int, float)) else ""
            enabled_mark = "" if s.enabled else "  [disabled]"
            lines.append(
                f"  {h(badge)}  <b>{h(s.symbol)}</b>{score_s}{enabled_mark}"
            )

    text = "\n".join(lines)
    if len(text) > _MAX_LEN:
        text = text[:_MAX_LEN] + _TRUNCATE_SUFFIX
    await safe_html_reply(update, text)

    runtime_config.log_command(
        chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        command="/watch", args=None, success=True,
    )
