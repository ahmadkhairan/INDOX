from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    AGGRESSIVE_MODE,
    DD_PAUSE_TRADING,
    MAX_PORTFOLIO_DD_PCT,
    MAX_RISK_PER_TRADE_PCT,
    MC_BOOTSTRAP_BLOCK,
    MC_SIMULATIONS,
    MIN_ADX_ENTRY,
    MIN_ENTRY_CONDITIONS,
    MIN_VOL_RATIO_ENTRY,
    RISK_PER_TRADE_PCT,
    TARGET_TRADES_PER_MONTH,
    WF_IN_SAMPLE_MONTHS,
    WF_OUT_SAMPLE_MONTHS,
    WF_STEP_MONTHS,
)
from utils.logger import get_logger
from utils.microstructure import round_to_tick
from utils.position_sizer import adaptive_risk_pct, calc_position_size, kelly_aggressive
from utils.ticker_utils import normalize_ticker
from utils.yf_guard import get_history

log = get_logger("backtest")


@dataclass
class Trade:
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_days: int
    exit_reason: str
    cond_met: int
    entry_mode: str = ""
    risk_pct: float = 0.0


@dataclass
class PeriodResult:
    label: str
    start: str
    end: str
    oos: bool
    total_trades: int
    win_rate: float
    pf: float
    total_return: float
    max_dd: float
    sharpe: float
    avg_win: float
    avg_loss: float
    params: dict[str, float | int | str] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)


@dataclass
class WFResult:
    ticker: str
    is_periods: list[PeriodResult]
    oos_periods: list[PeriodResult]
    oos_agg: PeriodResult
    is_agg: PeriodResult
    robustness: float
    is_robust: bool
    recommendations: list[str]


@dataclass
class MCResult:
    ticker: str
    n_sim: int
    median: float
    p5: float
    p25: float
    p75: float
    p95: float
    prob_positive: float
    prob_above_10: float
    mdd_median: float
    mdd_p95: float
    sharpe_median: float
    confidence: str
    block_size: int = MC_BOOTSTRAP_BLOCK
    mdd_p5: float = 0.0


class EntryMode(str, Enum):
    MOMENTUM = "momentum"
    PULLBACK = "pullback"
    BREAKOUT = "breakout"
    ALL = "all"


@dataclass(frozen=True)
class StrategyParams:
    sl_mult: float = 1.5
    tp1_mult: float = 2.0
    tp2_mult: float = 3.0
    hold_days: int = 12
    min_cond: int = MIN_ENTRY_CONDITIONS
    entry_mode: str = EntryMode.ALL.value

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "sl_mult": self.sl_mult,
            "tp1_mult": self.tp1_mult,
            "tp2_mult": self.tp2_mult,
            "hold_days": self.hold_days,
            "min_cond": self.min_cond,
            "entry_mode": self.entry_mode,
        }


def _rsi(c, p=14):
    d = c.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - (100 / (1 + g / (l + 1e-8)))


def _atr(h, l, c, p=14):
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def _adx(h, l, c, p=14):
    pdm = h.diff().clip(lower=0)
    mdm = (-l.diff()).clip(lower=0)
    atr_ = _atr(h, l, c, p)
    pdi = 100 * pdm.rolling(p).mean() / (atr_ + 1e-8)
    mdi = 100 * mdm.rolling(p).mean() / (atr_ + 1e-8)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-8)
    return dx.rolling(p).mean(), pdi, mdi


def _stoch(c, p=14):
    r = _rsi(c, p)
    lo = r.rolling(p).min()
    hi = r.rolling(p).max()
    k = (r - lo) / (hi - lo + 1e-8) * 100
    return k, k.rolling(3).mean()


def _rolling_vwap(h, l, c, v, window=20):
    typical = (h + l + c) / 3.0
    pv = typical * v
    return pv.rolling(window).sum() / (v.rolling(window).sum() + 1e-8)


class SingleBT:
    def __init__(self, params: Optional[StrategyParams] = None, use_regime: bool = True):
        self.params = params or StrategyParams()
        self.use_regime = use_regime
        self.SL = self.params.sl_mult
        self.TP1 = self.params.tp1_mult
        self.TP2 = self.params.tp2_mult
        self.HOLD = self.params.hold_days

    def _gv(self, s, i, d=0.0) -> float:
        try:
            x = s.iloc[i]
            return float(x) if not pd.isna(x) else d
        except Exception:
            return d

    def _infer_regime(self, price: float, ma50_val: float, ma200_val: float) -> str:
        if not self.use_regime:
            return "BULL"
        if ma200_val > 0 and price < ma200_val * 0.985:
            return "BEAR"
        if (ma200_val > 0 and price < ma200_val) or (ma50_val > 0 and price < ma50_val):
            return "CAUTION"
        return "BULL"

    def _check_entries(
        self,
        i,
        c,
        h,
        v,
        rsi,
        sk,
        sd,
        atr,
        adx,
        pdi,
        mdi,
        ma20,
        ma50,
        ma200,
        avg_vol,
        macd,
        msig,
        vwap,
    ) -> list[tuple[str, float, int]]:
        price = float(c.iloc[i])
        entries: list[tuple[str, float, int]] = []
        av = self._gv(atr, i)
        if av <= 0:
            return entries

        mode = self.params.entry_mode

        if mode in (EntryMode.MOMENTUM.value, EntryMode.ALL.value):
            c1 = self._gv(adx, i) > max(MIN_ADX_ENTRY, 18.0) and self._gv(pdi, i) > self._gv(mdi, i)
            c2 = 38 <= self._gv(rsi, i) <= 68
            c3 = self._gv(v, i) > self._gv(avg_vol, i) * max(MIN_VOL_RATIO_ENTRY, 1.2)
            c4 = price > self._gv(ma50, i) if not pd.isna(ma50.iloc[i]) else price > self._gv(ma20, i)
            c5 = self._gv(sk, i) > self._gv(sd, i)
            mc = self._gv(macd, i) > self._gv(msig, i)
            cond_count = sum([c1, c2, c3, c4, c5])
            if cond_count >= self.params.min_cond and mc:
                entries.append((EntryMode.MOMENTUM.name, price, cond_count))

        if mode in (EntryMode.PULLBACK.value, EntryMode.ALL.value):
            prev_price = float(c.iloc[i - 1]) if i > 0 else price
            ma20_val = self._gv(ma20, i)
            vwap_val = self._gv(vwap, i)
            near_ma20 = ma20_val > 0 and abs(price - ma20_val) / ma20_val < 0.015
            near_vwap = vwap_val > 0 and abs(price - vwap_val) / vwap_val < 0.012
            rsi_mid = 35 <= self._gv(rsi, i) <= 55
            vol_pickup = self._gv(v, i) > self._gv(avg_vol, i) * 0.9
            trend_ok = price > self._gv(ma50, i) if not pd.isna(ma50.iloc[i]) else True
            bounce = price > prev_price
            if (near_ma20 or near_vwap) and rsi_mid and vol_pickup and trend_ok and bounce:
                entries.append((EntryMode.PULLBACK.name, price, 3))

        if mode in (EntryMode.BREAKOUT.value, EntryMode.ALL.value):
            high_20 = float(h.iloc[max(0, i - 20):i].max()) if i >= 20 else 0.0
            vol_surge = self._gv(v, i) > self._gv(avg_vol, i) * 1.8
            price_break = high_20 > 0 and price > high_20 * 0.995
            rsi_ok = self._gv(rsi, i) < 75
            adx_ok = self._gv(adx, i) > MIN_ADX_ENTRY
            trend_ok = pd.isna(ma200.iloc[i]) or price > self._gv(ma200, i) * 0.97
            if vol_surge and price_break and rsi_ok and adx_ok and trend_ok:
                entries.append((EntryMode.BREAKOUT.name, price, 4))

        return entries

    def _pick_entry(self, entries: list[tuple[str, float, int]]) -> tuple[str, float, int] | None:
        if not entries:
            return None
        priority = {
            EntryMode.BREAKOUT.name: 3,
            EntryMode.PULLBACK.name: 2,
            EntryMode.MOMENTUM.name: 1,
        }
        return max(entries, key=lambda item: (item[2], priority.get(item[0], 0)))

    def _calc_exit(
        self,
        i,
        price,
        hd,
        ld,
        ep,
        ei,
        tp1,
        tp2,
        trail,
        av,
        rsi,
        macd,
        msig,
        n,
    ) -> tuple[Optional[str], Optional[float], float]:
        hold = i - ei
        new_trail = round_to_tick(price - self.params.sl_mult * av, "floor")
        if new_trail > trail:
            trail = new_trail

        if ld <= trail:
            return "SL/TRAIL", max(trail, ld), trail
        if hd >= tp2:
            return "TP2", tp2, trail
        if hd >= tp1:
            trail = max(trail, round_to_tick(ep * 1.002, "floor"))

        rsi_val = self._gv(rsi, i)
        if rsi_val > 75 and hold >= 3:
            return "RSI_OB", price, trail

        pnl_now = (price - ep) / (ep + 1e-8) * 100
        if pnl_now > 2.0 and self._gv(macd, i) < self._gv(msig, i):
            if i > 0 and self._gv(macd, i - 1) >= self._gv(msig, i - 1):
                return "MACD_BEAR", price, trail

        if hold >= self.params.hold_days:
            return ("MAX_HOLD_WIN" if pnl_now > 0 else "MAX_HOLD_CUT"), price, trail

        if i == n - 1:
            return "END", price, trail

        return None, None, trail

    def _recent_win_rate(self, trades: list[Trade], lookback: int = 6) -> float:
        if not trades:
            return 0.0
        sample = trades[-lookback:]
        wins = sum(1 for trade in sample if trade.pnl_pct > 0)
        return wins / len(sample) * 100

    def _kelly_risk_pct(self, trades: list[Trade], fallback: float, lookback: int = 8) -> float:
        if not trades:
            return round(fallback, 4)
        sample = trades[-lookback:]
        wins = [trade.pnl_pct for trade in sample if trade.pnl_pct > 0]
        losses = [abs(trade.pnl_pct) for trade in sample if trade.pnl_pct <= 0]
        if not wins or not losses:
            return round(fallback, 4)
        kelly_pct = kelly_aggressive(
            win_rate=len(wins) / len(sample) * 100,
            avg_win=float(np.mean(wins)),
            avg_loss=float(np.mean(losses)),
        )
        # Kelly acts as a throttle on top of regime-adaptive risk, not a replacement for it.
        return round(min(max(kelly_pct, 0.005), fallback, MAX_RISK_PER_TRADE_PCT), 4)

    def _consecutive_losses(self, trades: list[Trade]) -> int:
        streak = 0
        for trade in reversed(trades):
            if trade.pnl_pct <= 0:
                streak += 1
                continue
            break
        return streak

    def infer_current_regime(self, df: pd.DataFrame) -> str:
        close = df["Close"].astype(float)
        ma50 = close.rolling(50).mean()
        ma200 = close.rolling(200).mean()
        return self._infer_regime(
            float(close.iloc[-1]),
            self._gv(ma50, len(df) - 1),
            self._gv(ma200, len(df) - 1),
        )

    def run(self, df, ticker="", evaluation_start: int = 60) -> PeriodResult:
        if len(df) < 60:
            return self._empty(ticker)

        c = df["Close"].astype(float)
        h = df["High"].astype(float)
        l = df["Low"].astype(float)
        v = df["Volume"].astype(float)
        n = len(df)

        rsi = _rsi(c)
        sk, sd = _stoch(c)
        atr = _atr(h, l, c)
        adx, pdi, mdi = _adx(h, l, c)
        ma20 = c.rolling(20).mean()
        ma50 = c.rolling(50).mean()
        ma200 = c.rolling(200).mean()
        avg_vol = v.rolling(20).mean()
        macd = c.ewm(12).mean() - c.ewm(26).mean()
        msig = macd.ewm(9).mean()
        vwap = _rolling_vwap(h, l, c, v)

        trades: list[Trade] = []
        in_trade = False
        entry_price = 0.0
        entry_idx = 0
        sl = 0.0
        tp1 = 0.0
        tp2 = 0.0
        trail = 0.0
        entry_mode = ""
        cond_met = 0
        trade_risk_pct = RISK_PER_TRADE_PCT

        equity = 1.0
        peak_equity = 1.0
        pause_until = -1
        eq = [1.0]

        # Indicators need a warm-up window. Walk-forward passes historical
        # IS rows before OOS and sets evaluation_start to the OOS boundary;
        # entries before that boundary are deliberately ignored.
        evaluation_start = max(60, min(int(evaluation_start), n))
        for i in range(60, n):
            price = self._gv(c, i)
            hd = self._gv(h, i)
            ld = self._gv(l, i)
            av = self._gv(atr, i)
            today = df.index[i]
            regime = self._infer_regime(price, self._gv(ma50, i), self._gv(ma200, i))

            if in_trade:
                exit_reason, exit_price, trail = self._calc_exit(
                    i=i,
                    price=price,
                    hd=hd,
                    ld=ld,
                    ep=entry_price,
                    ei=entry_idx,
                    tp1=tp1,
                    tp2=tp2,
                    trail=trail,
                    av=av,
                    rsi=rsi,
                    macd=macd,
                    msig=msig,
                    n=n,
                )
                if exit_reason and exit_price is not None:
                    pnl = (exit_price - entry_price) / (entry_price + 1e-8)
                    risk_distance = max((entry_price - sl) / (entry_price + 1e-8), 0.001)
                    equity *= 1 + pnl * trade_risk_pct / risk_distance
                    equity = max(equity, 0.01)
                    drawdown_now = (equity - peak_equity) / (peak_equity + 1e-8)
                    if DD_PAUSE_TRADING and drawdown_now <= -MAX_PORTFOLIO_DD_PCT:
                        pause_until = max(pause_until, i + 10)
                    peak_equity = max(peak_equity, equity)
                    trades.append(
                        Trade(
                            ticker=ticker,
                            entry_date=df.index[entry_idx].strftime("%Y-%m-%d"),
                            exit_date=today.strftime("%Y-%m-%d"),
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl_pct=round(pnl * 100, 2),
                            hold_days=i - entry_idx,
                            exit_reason=exit_reason,
                            cond_met=cond_met,
                            entry_mode=entry_mode,
                            risk_pct=round(trade_risk_pct * 100, 2),
                        )
                    )
                    in_trade = False
                eq.append(equity)
                continue

            if i < evaluation_start:
                eq.append(equity)
                continue

            if av <= 0 or i <= pause_until:
                eq.append(equity)
                continue

            if self.use_regime and regime == "BEAR":
                eq.append(equity)
                continue

            entries = self._check_entries(
                i=i,
                c=c,
                h=h,
                v=v,
                rsi=rsi,
                sk=sk,
                sd=sd,
                atr=atr,
                adx=adx,
                pdi=pdi,
                mdi=mdi,
                ma20=ma20,
                ma50=ma50,
                ma200=ma200,
                avg_vol=avg_vol,
                macd=macd,
                msig=msig,
                vwap=vwap,
            )
            selected = self._pick_entry(entries)
            if not selected:
                eq.append(equity)
                continue

            recent_wr = self._recent_win_rate(trades)
            consecutive_losses = self._consecutive_losses(trades)
            adaptive_risk = min(
                adaptive_risk_pct(regime, consecutive_losses, recent_wr),
                MAX_RISK_PER_TRADE_PCT,
            )
            trade_risk_pct = self._kelly_risk_pct(
                trades=trades,
                fallback=adaptive_risk,
            )

            entry_mode, entry_price, cond_met = selected
            entry_price = round_to_tick(entry_price, "nearest")
            entry_idx = i
            sl = round_to_tick(price - self.SL * av, "floor")
            tp1 = round_to_tick(price + self.TP1 * av, "ceil")
            tp2 = round_to_tick(price + self.TP2 * av, "ceil")
            trail = sl
            in_trade = True
            eq.append(equity)

        return self._stats(
            trades,
            eq,
            ticker,
            df.index[0].strftime("%Y-%m-%d"),
            df.index[-1].strftime("%Y-%m-%d"),
        )

    def _stats(self, trades, eq_curve, ticker, start, end, label="") -> PeriodResult:
        if not trades:
            return self._empty(ticker, start, end)

        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        eq = np.array(eq_curve)
        rolling_max = np.maximum.accumulate(eq)
        dd = float(((eq - rolling_max) / (rolling_max + 1e-8)).min()) * 100
        rets = np.diff(eq) / (eq[:-1] + 1e-8)
        sharpe = float(np.mean(rets) / (np.std(rets) + 1e-8)) * np.sqrt(252) if len(rets) > 1 else 0.0
        return PeriodResult(
            label=label,
            start=start,
            end=end,
            oos=False,
            total_trades=len(trades),
            win_rate=round(len(wins) / len(trades) * 100, 1),
            pf=round(sum(wins) / (abs(sum(losses)) + 1e-8), 2),
            total_return=round((eq[-1] - 1) * 100, 2),
            max_dd=round(dd, 2),
            sharpe=round(sharpe, 3),
            avg_win=round(float(np.mean(wins)), 2) if wins else 0.0,
            avg_loss=round(float(np.mean([abs(loss_) for loss_ in losses])), 2) if losses else 0.0,
            params=self.params.as_dict(),
            trades=trades,
        )

    def _empty(self, ticker, start="", end="") -> PeriodResult:
        return PeriodResult(
            "",
            start,
            end,
            False,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            params=self.params.as_dict(),
        )


class WalkForwardEngine:
    PARAM_GRID = tuple(
        StrategyParams(
            sl_mult=sl,
            tp1_mult=tp1,
            tp2_mult=tp1 + 1.0,
            hold_days=hold,
            min_cond=min_cond,
            entry_mode=mode,
        )
        for sl in (1.2, 1.5, 1.8, 2.0, 2.5)
        for tp1 in (1.5, 2.0, 2.5, 3.0)
        for hold in (8, 12, 15)
        for min_cond in (3, 4)
        for mode in (
            EntryMode.MOMENTUM.value,
            EntryMode.PULLBACK.value,
            EntryMode.BREAKOUT.value,
            EntryMode.ALL.value,
        )
    )

    def __init__(self):
        self._bt = SingleBT()

    async def run(self, ticker, years=2) -> WFResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync, ticker, years)

    def _sync(self, ticker, years) -> WFResult:
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            return self._empty(str(ticker), str(exc))
        try:
            end = datetime.now()
            start = end - timedelta(days=years * 365 + 60)
            df = get_history(f"{ticker}.JK", start=start, end=end, auto_adjust=True)
        except Exception as exc:
            return self._empty(ticker, str(exc))
        if df.empty or len(df) < 120:
            return self._empty(ticker, "Data tidak cukup")

        df.columns = [c.strip() for c in df.columns]
        n = len(df)
        isd = WF_IN_SAMPLE_MONTHS * 21
        oosd = WF_OUT_SAMPLE_MONTHS * 21
        step = WF_STEP_MONTHS * 21
        is_res, oos_res = [], []
        idx, win = 0, 0

        while idx + isd + oosd <= n:
            win += 1
            is_df = df.iloc[idx:idx + isd]
            oos_df = df.iloc[idx + isd:idx + isd + oosd]
            best_params, r_is = self._optimize_params(is_df, ticker)
            r_is.label = f"IS-{win}"
            r_is.oos = False
            # Preserve IS history as indicator warm-up. Running on the bare
            # two-month OOS slice would always return zero trades because
            # SingleBT requires 60 rows before it evaluates entries.
            warmup = is_df.tail(60)
            oos_input = pd.concat([warmup, oos_df])
            r_oos = SingleBT(best_params).run(
                oos_input,
                ticker,
                evaluation_start=len(warmup),
            )
            r_oos.label = f"OOS-{win}"
            r_oos.oos = True
            is_res.append(r_is)
            oos_res.append(r_oos)
            idx += step

        if not oos_res:
            return self._empty(ticker, "Tidak cukup window")

        is_agg = self._agg(is_res, False, ticker)
        oos_agg = self._agg(oos_res, True, ticker)
        valid_oos = [period for period in oos_res if period.total_trades > 0]
        positive_oos_rate = (
            sum(1 for period in valid_oos if period.total_return > 0) / len(valid_oos)
            if valid_oos else 0.0
        )
        sample_score = min(oos_agg.total_trades / 30.0, 1.0)
        robustness_score = (
            min(max(oos_agg.pf / 1.4, 0.0), 1.0) * 0.30
            + min(max(oos_agg.win_rate / 55.0, 0.0), 1.0) * 0.20
            + positive_oos_rate * 0.25
            + sample_score * 0.25
        )
        rob = round(robustness_score, 3)
        is_robust = (
            oos_agg.total_trades >= 30
            and oos_agg.total_return > 0
            and oos_agg.win_rate >= 45
            and oos_agg.pf >= 1.2
            and positive_oos_rate >= 0.5
        )
        recs = []
        if not is_robust:
            recs.append("Tidak robust di OOS; tahan live trading dulu.")
        if oos_agg.max_dd < -18:
            recs.append("Max DD OOS terlalu dalam; kecilkan risk per trade.")
        if oos_agg.win_rate >= 55 and oos_agg.pf >= 1.8:
            recs.append("Robust untuk mode agresif; sizing tetap harus disiplin.")
        if oos_agg.total_trades < 8:
            recs.append("Sample OOS masih tipis; tambah periode validasi.")
        if positive_oos_rate < 0.5:
            recs.append("Kurang dari separuh window OOS positif; parameter belum stabil lintas periode.")
        return WFResult(
            ticker=ticker,
            is_periods=is_res,
            oos_periods=oos_res,
            oos_agg=oos_agg,
            is_agg=is_agg,
            robustness=rob,
            is_robust=is_robust,
            recommendations=recs,
        )

    def _optimize_params(self, df, ticker) -> tuple[StrategyParams, PeriodResult]:
        best_params = StrategyParams()
        best_result = SingleBT(best_params).run(df, ticker)
        best_score = self._score_period(best_result)
        best_distance = self._distance_from_default(best_params)
        for params in self.PARAM_GRID:
            result = SingleBT(params).run(df, ticker)
            score = self._score_period(result)
            distance = self._distance_from_default(params)
            if score > best_score or (score == best_score and distance < best_distance):
                best_params = params
                best_result = result
                best_score = score
                best_distance = distance
        return best_params, best_result

    def _score_period(self, result: PeriodResult) -> float:
        if result.total_trades <= 0:
            return -1e9

        score = 0.0
        score += max(result.pf, 0.0) * 3.0
        score += max(result.sharpe, -2.0) * 2.0
        score += min(result.total_trades / 10, 1.0) * 15.0
        if result.win_rate >= 60:
            score += (result.win_rate - 60) * 0.5
        elif result.win_rate < 45:
            score -= (45 - result.win_rate) * 0.3
        score += max(result.total_return, -50.0) * 0.1
        if result.max_dd < -20:
            score += result.max_dd * 0.6
        elif result.max_dd < -15:
            score += result.max_dd * 0.3
        if result.total_trades < 5:
            score -= 5.0
        return round(score, 6)

    def _distance_from_default(self, params: StrategyParams) -> float:
        default = StrategyParams()
        mode_penalty = 0.0 if params.entry_mode == default.entry_mode else 0.5
        return round(
            abs(params.sl_mult - default.sl_mult)
            + abs(params.tp1_mult - default.tp1_mult)
            + abs(params.tp2_mult - default.tp2_mult) * 0.5
            + abs(params.hold_days - default.hold_days) * 0.25
            + abs(params.min_cond - default.min_cond) * 0.5
            + mode_penalty,
            6,
        )

    def _agg(self, periods, is_oos, ticker) -> PeriodResult:
        all_trades = [trade for period in periods for trade in period.trades]
        if not all_trades:
            return SingleBT()._empty(ticker)
        pnls = [trade.pnl_pct for trade in all_trades]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [abs(pnl) for pnl in pnls if pnl <= 0]
        params = periods[-1].params if periods else {}
        compounded_return = (np.prod([1.0 + pnl / 100.0 for pnl in pnls]) - 1.0) * 100.0
        equity = np.cumprod([1.0] + [1.0 + pnl / 100.0 for pnl in pnls])
        rolling_max = np.maximum.accumulate(equity)
        aggregate_dd = float(((equity - rolling_max) / (rolling_max + 1e-8)).min()) * 100.0
        active_sharpes = [period.sharpe for period in periods if period.total_trades > 0]
        return PeriodResult(
            label="OOS_AGG" if is_oos else "IS_AGG",
            start=periods[0].start,
            end=periods[-1].end,
            oos=is_oos,
            total_trades=len(all_trades),
            win_rate=round(len(wins) / len(pnls) * 100, 1),
            pf=round(sum(wins) / (sum(losses) + 1e-8), 2),
            total_return=round(compounded_return, 2),
            max_dd=round(aggregate_dd, 2),
            sharpe=round(float(np.mean(active_sharpes)) if active_sharpes else 0.0, 3),
            avg_win=round(float(np.mean(wins)) if wins else 0, 2),
            avg_loss=round(float(np.mean(losses)) if losses else 0, 2),
            params=params,
            trades=all_trades,
        )

    def _empty(self, ticker, note="") -> WFResult:
        empty_result = SingleBT()._empty(ticker)
        return WFResult(
            ticker=ticker,
            is_periods=[],
            oos_periods=[],
            oos_agg=empty_result,
            is_agg=empty_result,
            robustness=0.0,
            is_robust=False,
            recommendations=[f"Error: {note}" if note else "Data tidak cukup"],
        )


class MonteCarloEngine:
    async def run(self, trades, n_sim=MC_SIMULATIONS) -> MCResult:
        if len(trades) < 5:
            return MCResult("", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "LOW", block_size=1)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync, trades, n_sim)

    def _sync(self, trades, n_sim) -> MCResult:
        pnls = np.array([t.pnl_pct / 100 for t in trades])
        n = len(pnls)
        block = self._choose_block_size(pnls)
        rng = np.random.default_rng(42)
        sim_ret, sim_mdd, sim_sharpe = [], [], []

        for _ in range(n_sim):
            sim = []
            while len(sim) < n:
                start = rng.integers(0, max(1, n - block + 1))
                sim.extend(pnls[start:start + block].tolist())
            sim = np.array(sim[:n])
            # Trade.pnl_pct is already converted to decimal above.
            # Multiplying by 0.01 again understated every simulated return
            # by 100x and made the MC output misleadingly flat.
            eq = np.cumprod(1 + sim)
            sim_ret.append(float(eq[-1] - 1) * 100)
            rolling_max = np.maximum.accumulate(eq)
            sim_mdd.append(float(((eq - rolling_max) / (rolling_max + 1e-8)).min()) * 100)
            sim_sharpe.append(float(np.mean(sim) / (np.std(sim) + 1e-8)) * np.sqrt(len(sim)))

        arr = np.array(sim_ret)
        marr = np.array(sim_mdd)
        ret_range = float(np.percentile(arr, 95)) - float(np.percentile(arr, 5))
        confidence = "HIGH" if ret_range < 20 else ("MEDIUM" if ret_range < 50 else "LOW")
        if n < 30:
            confidence = "LOW"
        return MCResult(
            ticker=trades[0].ticker,
            n_sim=n_sim,
            median=round(float(np.median(arr)), 2),
            p5=round(float(np.percentile(arr, 5)), 2),
            p25=round(float(np.percentile(arr, 25)), 2),
            p75=round(float(np.percentile(arr, 75)), 2),
            p95=round(float(np.percentile(arr, 95)), 2),
            prob_positive=round(float((arr > 0).mean() * 100), 1),
            prob_above_10=round(float((arr > 10).mean() * 100), 1),
            mdd_median=round(float(np.median(marr)), 2),
            mdd_p95=round(float(np.percentile(marr, 95)), 2),
            sharpe_median=round(float(np.median(sim_sharpe)), 3),
            confidence=confidence,
            block_size=block,
            mdd_p5=round(float(np.percentile(marr, 5)), 2),
        )

    def _choose_block_size(self, pnls: np.ndarray) -> int:
        n = len(pnls)
        if n <= 2:
            return 1
        max_lag = min(20, max(2, n // 3))
        significant_lags = [lag for lag in range(1, max_lag + 1) if abs(self._autocorr(pnls, lag)) >= 0.1]
        if significant_lags:
            block = max(3, significant_lags[-1] * 2)
        else:
            block = max(3, min(MC_BOOTSTRAP_BLOCK, n // 4 if n >= 12 else 3))
        return int(max(1, min(block, max(3, n // 2))))

    def _autocorr(self, values: np.ndarray, lag: int) -> float:
        if lag <= 0 or lag >= len(values):
            return 0.0
        left = values[:-lag]
        right = values[lag:]
        if np.std(left) < 1e-12 or np.std(right) < 1e-12:
            return 0.0
        return float(np.corrcoef(left, right)[0, 1])


async def run_backtest_v4(
    ticker,
    months=12,
    sl_mult=1.5,
    tp_mult=2.0,
    use_regime=True,
    entry_mode: str = EntryMode.ALL.value,
):
    try:
        ticker = normalize_ticker(ticker)
    except ValueError as exc:
        return {"error": str(exc)}

    end = datetime.now()
    start = end - timedelta(days=months * 31 + 90)
    try:
        df = get_history(f"{ticker}.JK", start=start, end=end, auto_adjust=True)
    except Exception as exc:
        return {"error": str(exc)}
    if df.empty or len(df) < 50:
        return {"error": f"Data tidak cukup ({len(df)} hari)"}

    df.columns = [c.strip() for c in df.columns]
    params = StrategyParams(
        sl_mult=sl_mult,
        tp1_mult=tp_mult,
        tp2_mult=tp_mult + 1.0,
        hold_days=StrategyParams().hold_days,
        min_cond=StrategyParams().min_cond,
        entry_mode=entry_mode,
    )
    bt = SingleBT(params, use_regime=use_regime)
    res = bt.run(df, ticker=ticker)
    mc_engine = MonteCarloEngine()
    mc = await mc_engine.run(res.trades) if res.trades else None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    atr = _atr(high, low, close)
    current_price = float(close.iloc[-1])
    current_atr = float(atr.dropna().iloc[-1]) if not atr.dropna().empty else 0.0
    current_sl = current_price - sl_mult * current_atr if current_atr > 0 else 0.0
    regime = bt.infer_current_regime(df)
    consecutive_losses = bt._consecutive_losses(res.trades)
    recent_win_rate = bt._recent_win_rate(res.trades)
    adaptive_risk = min(adaptive_risk_pct(regime, consecutive_losses, recent_win_rate), MAX_RISK_PER_TRADE_PCT)
    risk_pct = bt._kelly_risk_pct(res.trades, adaptive_risk)
    kelly_pct = kelly_aggressive(res.win_rate, res.avg_win, res.avg_loss)
    sizing_example = (
        calc_position_size(10_000_000.0, current_price, current_sl, risk_pct)
        if current_price > 0 and current_sl > 0
        else {"shares": 0, "lots": 0, "risk_amount": 0}
    )
    avg_cond_met = round(float(np.mean([trade.cond_met for trade in res.trades])), 2) if res.trades else 0.0
    entry_breakdown = {
        mode: sum(1 for trade in res.trades if trade.entry_mode == mode)
        for mode in (EntryMode.MOMENTUM.name, EntryMode.PULLBACK.name, EntryMode.BREAKOUT.name)
    }
    validation_target_hit = res.pf > 1.8 and res.win_rate > 55 and res.max_dd > -18

    return {
        "ticker": ticker,
        "period": f"{months} bulan",
        "total_trades": res.total_trades,
        "win_rate": res.win_rate,
        "profit_factor": res.pf,
        "total_return": res.total_return,
        "max_drawdown": res.max_dd,
        "avg_win": res.avg_win,
        "avg_loss": res.avg_loss,
        "sharpe": res.sharpe,
        "sl_mult": sl_mult,
        "tp_mult": tp_mult,
        "entry_mode": entry_mode,
        "min_cond": params.min_cond,
        "regime_filter": use_regime,
        "regime_state": regime,
        "avg_cond_met": avg_cond_met,
        "entry_breakdown": entry_breakdown,
        "equity_final": round(1.0 + res.total_return / 100, 4),
        "aggressive_mode": AGGRESSIVE_MODE,
        "target_trades_per_month": TARGET_TRADES_PER_MONTH,
        "risk_per_trade_pct": round(risk_pct * 100, 2),
        "max_risk_per_trade_pct": round(MAX_RISK_PER_TRADE_PCT * 100, 2),
        "kelly_fraction_pct": round(kelly_pct * 100, 2),
        "consecutive_losses": consecutive_losses,
        "recent_win_rate": round(recent_win_rate, 1),
        "dd_circuit_breaker_pct": round(MAX_PORTFOLIO_DD_PCT * 100, 2),
        "validation_target_hit": validation_target_hit,
        "position_sizing_example": {
            "capital": 10_000_000,
            "price": round(current_price, 2),
            "stop_price": round(current_sl, 2) if current_sl > 0 else 0.0,
            **sizing_example,
        },
        "monte_carlo": {
            "median_return": mc.median,
            "p5_return": mc.p5,
            "p95_return": mc.p95,
            "probability_positive": mc.prob_positive,
            "confidence": mc.confidence,
            "n_simulations": mc.n_sim,
            "block_size": mc.block_size,
        } if mc else None,
    }


_wf: Optional[WalkForwardEngine] = None
_mc: Optional[MonteCarloEngine] = None


def get_wf_engine():
    global _wf
    if _wf is None:
        _wf = WalkForwardEngine()
    return _wf


def get_mc_engine():
    global _mc
    if _mc is None:
        _mc = MonteCarloEngine()
    return _mc
