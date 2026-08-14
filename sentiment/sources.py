"""Free Sentiment Sources — Stockbit Forum & Telegram Channel scrapers.

Provides:
- scrape_stockbit_forum(ticker): Fetch user comments from Stockbit ticker stream.
- scrape_telegram_signals(channels): Scrape public Telegram web preview feeds.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from utils.logger import get_logger

log = get_logger("sentiment.sources")

DEFAULT_TELEGRAM_CHANNELS = [
    "sahamgain",
    "idxupdate",
    "sahamindonesia_id",
    "idxanalisa",
]

_STOCKBIT_PLACEHOLDER = re.compile(r"\[%\w+%\]\s*")
_TICKER_RE = re.compile(r"\b[A-Z]{4}\b")


def _clean_stockbit_text(text: str) -> str:
    """Strip Stockbit mention placeholders like [%QPhP%]."""
    return _STOCKBIT_PLACEHOLDER.sub("", text).strip()


def _parse_stockbit_next_data(html: str, ticker: str, url: str) -> list[dict]:
    """Parse Stockbit SPA data from __NEXT_DATA__ JSON blob."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []

    try:
        data = json.loads(script.string)
    except json.JSONDecodeError as exc:
        log.debug(f"Stockbit JSON parse {ticker}: {exc}")
        return []

    posts = data.get("props", {}).get("pageProps", {}).get("posts", [])
    items = []
    for post in posts[:15]:
        content = _clean_stockbit_text(post.get("content") or post.get("content_original") or "")
        if not content or len(content) < 15:
            continue

        pub = post.get("created") or post.get("created_display") or ""
        if pub and len(pub) >= 16:
            pub_date = pub[:16]
        else:
            pub_date = datetime.now().strftime("%Y-%m-%d %H:%M")

        items.append({
            "title": content[:80] + ("..." if len(content) > 80 else ""),
            "summary": content[:400],
            "url": url,
            "published": pub_date,
            "source": "Stockbit Forum",
            "_ticker": ticker,
        })
    return items


async def scrape_stockbit_forum(ticker: str) -> list[dict]:
    """Scrape recent discussion from Stockbit forum for a specific ticker.

    Stockbit is a Next.js SPA — content lives in __NEXT_DATA__ JSON, not HTML DOM.
    """
    ticker_clean = ticker.upper().strip()
    url = f"https://stockbit.com/symbol/{ticker_clean}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text(errors="ignore")
        return _parse_stockbit_next_data(html, ticker_clean, url)
    except Exception as exc:
        log.debug(f"Stockbit scrape {ticker_clean}: {exc}")
        return []


def _parse_telegram_messages(html: str, channel: str, url: str) -> list[dict]:
    """Parse Telegram web preview — pair each message with its timestamp."""
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for wrap in soup.find_all("div", class_="tgme_widget_message_wrap"):
        msg_div = wrap.find("div", class_="tgme_widget_message_text")
        if not msg_div:
            continue
        txt = msg_div.get_text(separator=" ", strip=True)
        if not txt or len(txt) < 15:
            continue

        time_el = wrap.find("time", class_="time")
        pub_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        if time_el and time_el.get("datetime"):
            pub_date = time_el["datetime"][:16].replace("T", " ")

        item: dict = {
            "title": txt[:80] + ("..." if len(txt) > 80 else ""),
            "summary": txt[:400],
            "url": url,
            "published": pub_date,
            "source": f"Telegram @{channel}",
        }
        tickers = _TICKER_RE.findall(txt.upper())
        if tickers:
            item["_ticker"] = tickers[0]
        items.append(item)

    return items[-10:]


async def scrape_telegram_signals(channels: Optional[list[str]] = None) -> list[dict]:
    """Scrape public Telegram channels via web preview interface (t.me/s/<channel>).

    No Telegram API key needed.
    """
    env_channels = os.getenv("TELEGRAM_SENTIMENT_CHANNELS", "").strip()
    if env_channels:
        ch_list = [c.strip().lstrip("@") for c in env_channels.split(",") if c.strip()]
    else:
        ch_list = channels or DEFAULT_TELEGRAM_CHANNELS

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    all_items: list[dict] = []

    async with aiohttp.ClientSession(headers=headers) as session:
        for channel in ch_list:
            url = f"https://t.me/s/{channel}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        log.debug(f"Telegram @{channel}: HTTP {resp.status}")
                        continue
                    html = await resp.text(errors="ignore")
                parsed = _parse_telegram_messages(html, channel, url)
                all_items.extend(parsed)
            except Exception as exc:
                log.debug(f"Telegram scrape @{channel}: {exc}")
                continue

    return all_items
