from __future__ import annotations

from datetime import datetime

import feedparser
from utils.yf_guard import YFinanceUnavailable, get_news


BUYBACK_KEYWORDS = [
    "buyback", "buy back", "pembelian kembali", "share repurchase",
    "treasury", "right issue", "akuisisi saham",
]
DIVIDEND_KEYWORDS = ["dividen", "dividend", "interim dividend", "final dividend", "cum date"]


def detect_special_news(news: list) -> dict:
    buyback_news = []
    dividend_news = []

    for item in news:
        title_lower = item.get("title", "").lower()
        if any(keyword in title_lower for keyword in BUYBACK_KEYWORDS):
            buyback_news.append(item.get("title", ""))
        if any(keyword in title_lower for keyword in DIVIDEND_KEYWORDS):
            dividend_news.append(item.get("title", ""))

    return {
        "has_buyback": bool(buyback_news),
        "has_dividend": bool(dividend_news),
        "buyback_news": buyback_news[:2],
        "dividend_news": dividend_news[:2],
        "score_bonus": (15 if buyback_news else 0) + (10 if dividend_news else 0),
    }


def get_berita(ticker: str, max_items: int = 5) -> list:
    berita = []
    try:
        for item in get_news(f"{ticker.upper()}.JK")[:max_items]:
            ts = item.get("providerPublishTime", 0)
            berita.append({
                "title": item.get("title", ""),
                "published": datetime.fromtimestamp(ts).strftime("%d %b %Y %H:%M") if ts else "N/A",
                "source": item.get("publisher", "Yahoo Finance"),
                "link": item.get("link", ""),
            })
    except YFinanceUnavailable:
        pass
    except Exception:
        pass

    if len(berita) < max_items:
        try:
            import requests
            url = f"https://news.google.com/rss/search?q={ticker}+saham+IDX&hl=id&gl=ID&ceid=ID:id"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[: max_items - len(berita)]:
                    berita.append({
                        "title": entry.get("title", ""),
                        "published": entry.get("published", "N/A"),
                        "source": "Google News",
                        "link": entry.get("link", ""),
                    })
        except Exception:
            pass

    if not berita:
        berita.append({
            "title": f"Tidak ada berita terkini untuk {ticker}",
            "source": "-",
            "published": "N/A",
            "link": "",
        })

    return berita[:max_items]
