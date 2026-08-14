"""Paper trading tracker — validate picks tanpa pakai uang real.

Fitur:
- Catat entry manual atau auto dari daily picks
- Auto-check SL/TP1/TP2 terhadap harga terkini
- Hitung win rate, profit factor, expectancy, max DD
- Persistent ke JSON file
- Background task untuk auto-check saat jam bursa

Cara pakai:
    from execution.paper_trader import get_paper_trader
    pt = get_paper_trader()
    trade_id = pt.enter("BBCA", 9500, sl=9300, tp1=9800, tp2=10100)
    pt.set_size(trade_id, qty_lots=10)
    pt.check_all({"BBCA": 9750})  # manual check
    print(pt.stats())
"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger("execution.paper")


@dataclass
class PaperTrade:
    id: str
    ticker: str
    entry_date: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    qty_lots: int = 0
    status: str = "OPEN"  # OPEN, TP1_HIT, CLOSED
    signal: dict = field(default_factory=dict)
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    pnl_pct: float = 0.0
    pnl_idr: float = 0.0
    exit_reason: Optional[str] = None
    tp1_hit_date: Optional[str] = None
    notes: str = ""
    source: str = "manual"  # manual, daily_pick, signal


class PaperTrader:
    """Paper trading journal dengan SL/TP tracking + statistik."""

    def __init__(self, data_dir: Path = Path("execution/paper")):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = data_dir / "trades.json"
        self._lock = threading.RLock()
        self.trades: list[PaperTrade] = self._load()

    def _load(self) -> list[PaperTrade]:
        if not self.trades_file.exists():
            return []
        try:
            data = json.loads(self.trades_file.read_text())
            return [PaperTrade(**t) for t in data]
        except Exception as exc:
            log.warning(f"Paper trade load: {exc}")
            return []

    def _save(self) -> None:
        try:
            with self._lock:
                data = [asdict(t) for t in self.trades]
                self.trades_file.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False, default=str)
                )
        except Exception as exc:
            log.warning(f"Paper trade save: {exc}")

    # ── Entry / management ────────────────────────────────────────
    def enter(
        self,
        ticker: str,
        entry_price: float,
        sl: float,
        tp1: float,
        tp2: float,
        signal: Optional[dict] = None,
        source: str = "manual",
        qty_lots: int = 0,
        notes: str = "",
    ) -> str:
        """Catat paper trade entry. Return trade ID."""
        if entry_price <= 0:
            raise ValueError("entry_price harus > 0")
        if not (sl < entry_price < tp1 < tp2):
            raise ValueError(
                f"SL/TP tidak valid: harus sl({sl}) < entry({entry_price}) < tp1({tp1}) < tp2({tp2})"
            )

        trade = PaperTrade(
            id=f"{ticker.upper()}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            ticker=ticker.upper(),
            entry_date=datetime.now().isoformat(),
            entry_price=entry_price,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            qty_lots=qty_lots,
            signal=signal or {},
            source=source,
            notes=notes,
        )
        with self._lock:
            self.trades.append(trade)
            self._save()
        log.info(f"Paper trade ENTER: {trade.id} @ {entry_price} "
                 f"[SL {sl} | TP1 {tp1} | TP2 {tp2}]")
        return trade.id

    def set_size(self, trade_id: str, qty_lots: int) -> bool:
        """Set ukuran posisi (dalam lot, 1 lot = 100 lembar)."""
        for t in self.trades:
            if t.id == trade_id:
                t.qty_lots = qty_lots
                self._save()
                return True
        return False

    def close_trade(
        self,
        trade_id: str,
        current_price: float,
        reason: str = "MANUAL",
    ) -> bool:
        """Tutup trade secara manual."""
        for t in self.trades:
            if t.id == trade_id and t.status != "CLOSED":
                self._update_pnl(t, current_price)
                t.status = "CLOSED"
                t.exit_price = current_price
                t.exit_reason = reason
                t.exit_date = datetime.now().isoformat()
                self._save()
                log.info(f"Paper trade CLOSE: {t.id} reason={reason} pnl={t.pnl_pct:+.2f}%")
                return True
        return False

    def cancel_trade(self, trade_id: str) -> bool:
        """Hapus trade OPEN dari journal (kesalahan input, dll)."""
        with self._lock:
            for i, t in enumerate(self.trades):
                if t.id == trade_id and t.status != "CLOSED":
                    self.trades.pop(i)
                    self._save()
                    return True
        return False

    # ── Auto-check SL/TP ──────────────────────────────────────────
    def _update_pnl(self, trade: PaperTrade, current_price: float) -> None:
        pnl = (current_price - trade.entry_price) / trade.entry_price * 100
        trade.pnl_pct = round(pnl, 2)
        if trade.qty_lots > 0:
            shares = trade.qty_lots * 100
            trade.pnl_idr = round((current_price - trade.entry_price) * shares, 0)

    def _check_single(self, trade: PaperTrade, current_price: float) -> Optional[str]:
        """Cek 1 trade. Return exit_reason jika triggered, else None."""
        if trade.status == "CLOSED":
            return None

        self._update_pnl(trade, current_price)

        # SL hit
        if current_price <= trade.sl:
            trade.status = "CLOSED"
            trade.exit_price = trade.sl
            trade.exit_reason = "SL"
            trade.exit_date = datetime.now().isoformat()
            return "SL"

        # TP2 hit (full close)
        if current_price >= trade.tp2:
            trade.status = "CLOSED"
            trade.exit_price = trade.tp2
            trade.exit_reason = "TP2"
            trade.exit_date = datetime.now().isoformat()
            return "TP2"

        # TP1 hit (partial, naikkan SL ke breakeven + 0.2%)
        if current_price >= trade.tp1 and trade.tp1_hit_date is None:
            trade.tp1_hit_date = datetime.now().isoformat()
            trade.status = "TP1_HIT"
            new_sl = trade.entry_price * 1.002
            if new_sl > trade.sl:
                trade.sl = round(new_sl, 0)
            return "TP1"

        return None

    def check_all(self, price_lookup: dict[str, float]) -> list[dict]:
        """Cek semua trade OPEN terhadap price_lookup. Return daftar yang ke-trigger."""
        triggered = []
        for t in self.trades:
            if t.status == "CLOSED":
                continue
            price = price_lookup.get(t.ticker)
            if price is None or price <= 0:
                continue
            reason = self._check_single(t, price)
            if reason:
                triggered.append({
                    "id": t.id,
                    "ticker": t.ticker,
                    "reason": reason,
                    "pnl_pct": t.pnl_pct,
                    "pnl_idr": t.pnl_idr,
                    "exit_price": t.exit_price,
                })
        if triggered:
            self._save()
        return triggered

    # ── Query ─────────────────────────────────────────────────────
    def get_open_trades(self) -> list[PaperTrade]:
        return [t for t in self.trades if t.status != "CLOSED"]

    def has_open_trade(
        self,
        ticker: str,
        source: Optional[str] = None,
        on_date: Optional[str] = None,
    ) -> bool:
        """Check if an open trade already exists (for dedup on daily picks)."""
        ticker = ticker.upper()
        for t in self.get_open_trades():
            if t.ticker != ticker:
                continue
            if source and t.source != source:
                continue
            if on_date and not t.entry_date.startswith(on_date):
                continue
            return True
        return False

    def get_trade(self, trade_id: str) -> Optional[PaperTrade]:
        for t in self.trades:
            if t.id == trade_id:
                return t
        return None

    def get_recent(self, n: int = 10) -> list[PaperTrade]:
        return sorted(self.trades, key=lambda t: t.entry_date, reverse=True)[:n]

    def get_by_ticker(self, ticker: str) -> list[PaperTrade]:
        return [t for t in self.trades if t.ticker == ticker.upper()]

    # ── Stats ─────────────────────────────────────────────────────
    def stats(self, last_n: Optional[int] = None) -> dict:
        """Hitung statistik performa."""
        closed = [t for t in self.trades if t.status == "CLOSED"]
        if last_n:
            closed = closed[-last_n:]

        open_trades = self.get_open_trades()

        if not closed:
            return {
                "total_trades": 0,
                "open_trades": len(open_trades),
                "message": "Belum ada trade yang tertutup",
            }

        wins = [t for t in closed if t.pnl_pct > 0]
        losses = [t for t in closed if t.pnl_pct <= 0]

        win_rate = len(wins) / len(closed) * 100
        avg_win = sum(t.pnl_pct for t in wins) / max(len(wins), 1)
        avg_loss = sum(t.pnl_pct for t in losses) / max(len(losses), 1)
        gross_profit = sum(t.pnl_pct for t in wins)
        gross_loss = abs(sum(t.pnl_pct for t in losses))
        profit_factor = gross_profit / max(gross_loss, 0.01)
        expectancy = (win_rate / 100 * avg_win) + ((100 - win_rate) / 100 * avg_loss)

        # Max drawdown
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted(closed, key=lambda x: x.exit_date or ""):
            cumulative += t.pnl_pct
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        # By exit reason
        by_reason: dict[str, dict] = {}
        for t in closed:
            r = t.exit_reason or "UNKNOWN"
            if r not in by_reason:
                by_reason[r] = {"count": 0, "wins": 0, "pnl_sum": 0.0}
            by_reason[r]["count"] += 1
            by_reason[r]["pnl_sum"] += t.pnl_pct
            if t.pnl_pct > 0:
                by_reason[r]["wins"] += 1

        # By ticker
        by_ticker: dict[str, dict] = {}
        for t in closed:
            tk = t.ticker
            if tk not in by_ticker:
                by_ticker[tk] = {"count": 0, "wins": 0, "pnl_sum": 0.0}
            by_ticker[tk]["count"] += 1
            by_ticker[tk]["pnl_sum"] += t.pnl_pct
            if t.pnl_pct > 0:
                by_ticker[tk]["wins"] += 1

        return {
            "total_trades": len(closed),
            "open_trades": len(open_trades),
            "win_rate": round(win_rate, 1),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy_pct": round(expectancy, 2),
            "total_pnl_pct": round(sum(t.pnl_pct for t in closed), 2),
            "max_drawdown_pct": round(max_dd, 2),
            "best_trade_pct": round(max(t.pnl_pct for t in closed), 2),
            "worst_trade_pct": round(min(t.pnl_pct for t in closed), 2),
            "total_pnl_idr": sum(t.pnl_idr for t in closed if t.qty_lots > 0),
            "by_exit_reason": by_reason,
            "by_ticker": dict(sorted(by_ticker.items(),
                                      key=lambda x: x[1]["pnl_sum"], reverse=True)[:10]),
        }

    def format_report(self) -> str:
        """Format statistik jadi string siap Discord."""
        s = self.stats()
        if s.get("total_trades", 0) == 0:
            return f"📊 **Paper Trading**\n{s.get('message', 'Belum ada data')}"

        lines = [
            "📊 **PAPER TRADING REPORT**",
            f"Total Trades: {s['total_trades']} | Open: {s['open_trades']}",
            f"Win Rate: {s['win_rate']}% | Profit Factor: {s['profit_factor']}",
            f"Avg Win: +{s['avg_win_pct']}% | Avg Loss: {s['avg_loss_pct']}%",
            f"Expectancy: {s['expectancy_pct']:+.2f}% per trade",
            f"Total PnL: {s['total_pnl_pct']:+.2f}% | Max DD: {s['max_drawdown_pct']:.2f}%",
        ]

        if s.get("total_pnl_idr", 0) != 0:
            lines.append(f"Total PnL (IDR): Rp{s['total_pnl_idr']:,.0f}")

        # By exit reason
        by_reason = s.get("by_exit_reason", {})
        if by_reason:
            lines.append("\n**By Exit Reason:**")
            for reason, data in sorted(by_reason.items()):
                wr = data["wins"] / max(data["count"], 1) * 100
                lines.append(
                    f"  • {reason}: {data['count']} trades, "
                    f"WR {wr:.0f}%, PnL {data['pnl_sum']:+.2f}%"
                )

        # Top tickers
        by_ticker = s.get("by_ticker", {})
        if by_ticker:
            lines.append("\n**Top Tickers:**")
            for tk, data in list(by_ticker.items())[:5]:
                wr = data["wins"] / max(data["count"], 1) * 100
                lines.append(
                    f"  • {tk}: {data['count']} trades, "
                    f"WR {wr:.0f}%, PnL {data['pnl_sum']:+.2f}%"
                )

        return "\n".join(lines)


# ── Singleton ──────────────────────────────────────────────────────
_paper: Optional[PaperTrader] = None


def get_paper_trader() -> PaperTrader:
    global _paper
    if _paper is None:
        _paper = PaperTrader()
    return _paper
