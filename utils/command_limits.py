from __future__ import annotations


ANALYSIS_COOLDOWN = (2, 30.0)
MARKET_COOLDOWN = (2, 15.0)
PICKS_COOLDOWN = (1, 45.0)
PORTFOLIO_COOLDOWN = (1, 45.0)
BACKTEST_COOLDOWN = (1, 60.0)
RISK_COOLDOWN = (1, 30.0)
CHAT_MENTION_COOLDOWN_SECONDS = 15.0


def format_retry_after(seconds: float) -> str:
    retry = max(1, int(round(seconds)))
    minutes, secs = divmod(retry, 60)
    if minutes:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    return f"{secs}s"
