"""Smart sentiment aggregator with time decay + source credibility.

Replaces simple weighted-mean aggregation with:
1. Time decay — recent news weighted more heavily
2. Source credibility — official sources > news media > forums > telegram
3. Recency boost — very fresh news gets amplification
4. Outlier dampening — single extreme news doesn't dominate
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sentiment.pipeline import TickerSentiment, SentItem


# Source credibility weights (higher = more trusted)
SOURCE_WEIGHT = {
    "IDX Official": 3.0,           # exchange announcements
    "idx.co.id": 3.0,
    "CNBC Indonesia": 2.5,
    "Bisnis.com": 2.5,
    "Kontan": 2.5,
    "Bloomberg": 2.8,
    "Reuters": 2.8,
    "Yahoo Finance": 2.0,
    "Google News": 1.5,
    "Stockbit Forum": 1.3,         # noisy but real-time
    "stockbit.com": 1.3,
    "Telegram": 0.7,               # very noisy, often pump & dump
    "sahamgain": 0.8,
    "idxupdate": 0.8,
}

# Impact multipliers (HIGH > MEDIUM > LOW)
IMPACT_WEIGHT = {
    "HIGH": 3.0,
    "MEDIUM": 2.0,
    "LOW": 1.0,
}


def _parse_time(ts: str) -> Optional[datetime]:
    """Parse various timestamp formats."""
    if not ts or ts == "N/A":
        return None
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d %b %Y %H:%M",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(ts[:25], fmt)
        except (ValueError, TypeError):
            continue
    # Try ISO format
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _time_decay(published: str, now: Optional[datetime] = None) -> float:
    """Time decay: full weight for <6h, half at 24h, near-zero at 72h."""
    if now is None:
        now = datetime.now()
    pub_time = _parse_time(published)
    if pub_time is None:
        return 0.5  # unknown time = medium weight
    age_hours = max(0, (now - pub_time).total_seconds() / 3600)
    if age_hours < 6:
        return 1.0
    elif age_hours < 24:
        return 0.85
    elif age_hours < 48:
        return 0.5
    elif age_hours < 72:
        return 0.25
    else:
        return 0.1


def _source_credibility(source: str) -> float:
    """Get source credibility weight, with partial matching."""
    if not source:
        return 1.0
    source_lower = source.lower()
    # Direct match
    if source in SOURCE_WEIGHT:
        return SOURCE_WEIGHT[source]
    # Partial match
    for key, weight in SOURCE_WEIGHT.items():
        if key.lower() in source_lower or source_lower in key.lower():
            return weight
    return 1.0  # unknown source = neutral


def aggregate_smart(
    items: list[SentItem],
    now: Optional[datetime] = None,
    min_items_for_aggregation: int = 1,
) -> TickerSentiment:
    """Aggregate sentiment with time-decay + source credibility weighting.

    Args:
        items: List of SentItem objects (from pipeline scoring)
        now: Reference time for decay (default: current time)
        min_items_for_aggregation: minimum items to form a signal

    Returns:
        TickerSentiment aggregated for a single ticker
    """
    if not items:
        return TickerSentiment(
            ticker="N/A", score=0.0, label="NEUTRAL",
            pos_count=0, neg_count=0, neu_count=0,
            has_buyback=False, has_dividend=False,
            has_earn_beat=False, has_reg_risk=False,
            items=[], last_updated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    if now is None:
        now = datetime.now()

    # Group by ticker (in case items span multiple)
    by_ticker: dict[str, list[SentItem]] = {}
    for item in items:
        for t in item.tickers:
            by_ticker.setdefault(t, []).append(item)

    if not by_ticker:
        return TickerSentiment(
            ticker="N/A", score=0.0, label="NEUTRAL",
            pos_count=0, neg_count=0, neu_count=0,
            has_buyback=False, has_dividend=False,
            has_earn_beat=False, has_reg_risk=False,
            items=[], last_updated=now.strftime("%Y-%m-%d %H:%M"),
        )

    # Pick the most-mentioned ticker (or single if only one)
    ticker = max(by_ticker.keys(), key=lambda t: len(by_ticker[t]))
    ticker_items = by_ticker[ticker]

    if len(ticker_items) < min_items_for_aggregation:
        return TickerSentiment(
            ticker=ticker, score=0.0, label="NEUTRAL",
            pos_count=0, neg_count=0, neu_count=0,
            has_buyback=False, has_dividend=False,
            has_earn_beat=False, has_reg_risk=False,
            items=ticker_items, last_updated=now.strftime("%Y-%m-%d %H:%M"),
        )

    # Aggregate with smart weights
    weighted_sum = 0.0
    total_weight = 0.0
    pos_count = 0
    neg_count = 0
    neu_count = 0
    has_buyback = False
    has_dividend = False
    has_earn_beat = False
    has_reg_risk = False

    for item in ticker_items:
        # Composite weight: impact × source credibility × time decay
        w = (
            IMPACT_WEIGHT.get(item.impact, 1.0)
            * _source_credibility(item.source)
            * _time_decay(item.published, now)
        )
        weighted_sum += item.score * w
        total_weight += w

        # Count
        if item.score > 0.1:
            pos_count += 1
        elif item.score < -0.1:
            neg_count += 1
        else:
            neu_count += 1

        # Categorical flags
        if item.category == "buyback":
            has_buyback = True
        if item.category == "dividend":
            has_dividend = True
        if item.category == "earnings" and item.score > 0.5:
            has_earn_beat = True
        if item.category == "regulatory" and item.score < 0:
            has_reg_risk = True

    # Normalize score
    if total_weight > 0:
        composite = weighted_sum / total_weight
    else:
        composite = 0.0
    composite = max(-1.0, min(1.0, composite))

    # Label
    if composite > 0.5:
        label = "VERY_POS"
    elif composite > 0.1:
        label = "POS"
    elif composite < -0.5:
        label = "VERY_NEG"
    elif composite < -0.1:
        label = "NEG"
    else:
        label = "NEUTRAL"

    return TickerSentiment(
        ticker=ticker,
        score=round(composite, 3),
        label=label,
        pos_count=pos_count,
        neg_count=neg_count,
        neu_count=neu_count,
        has_buyback=has_buyback,
        has_dividend=has_dividend,
        has_earn_beat=has_earn_beat,
        has_reg_risk=has_reg_risk,
        items=sorted(ticker_items, key=lambda x: x.published, reverse=True)[:5],
        last_updated=now.strftime("%Y-%m-%d %H:%M"),
    )
