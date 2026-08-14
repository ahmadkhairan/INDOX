"""Real Trade Journaler — Catat trade yang dieksekusi manual di broker real.

Provides:
- enter_trade(ticker, action, price, qty_lots, notes)
- list_trades()
- stats()
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger("execution.journal")


@dataclass
class RealTrade:
    id: str
    ticker: str
    action: str  # BUY / SELL
    price: float
    qty_lots: int
    timestamp: str
    notes: str = ""


class TradeJournal:
    def __init__(self, data_dir: Path = Path("execution/paper")):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = data_dir / "real_journal.json"
        self._lock = threading.RLock()
        self.trades: list[RealTrade] = self._load()

    def _load(self) -> list[RealTrade]:
        if not self.file_path.exists():
            return []
        try:
            data = json.loads(self.file_path.read_text())
            return [RealTrade(**t) for t in data]
        except Exception as exc:
            log.warning(f"Journal load error: {exc}")
            return []

    def _save(self) -> None:
        try:
            with self._lock:
                data = [asdict(t) for t in self.trades]
                self.file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as exc:
            log.warning(f"Journal save error: {exc}")

    def enter_trade(
        self, ticker: str, action: str, price: float, qty_lots: int, notes: str = ""
    ) -> RealTrade:
        trade = RealTrade(
            id=f"REAL_{ticker.upper()}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            ticker=ticker.upper(),
            action=action.upper(),
            price=price,
            qty_lots=qty_lots,
            timestamp=datetime.now().isoformat(),
            notes=notes,
        )
        with self._lock:
            self.trades.append(trade)
            self._save()
        log.info(f"REAL TRADE LOGGED: {trade.id} {trade.action} {trade.ticker} @ {price} ({qty_lots} lots)")
        return trade

    def list_trades(self, limit: int = 15) -> list[RealTrade]:
        return sorted(self.trades, key=lambda t: t.timestamp, reverse=True)[:limit]


_journal: Optional[TradeJournal] = None


def get_trade_journal() -> TradeJournal:
    global _journal
    if _journal is None:
        _journal = TradeJournal()
    return _journal
