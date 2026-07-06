from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import numpy as np
import pandas as pd
from config import STRESS_TEST_SCENARIOS, CORRELATION_ALERT_THRESHOLD
from utils.logger import get_logger

log = get_logger("risk.engine")

try:
    from scipy import stats as _stats
    _SCIPY = True
except ImportError:
    _SCIPY = False

SCENARIOS: dict[str, dict] = {
    "covid_crash_2020": {
        "label": "COVID-19 Crash (Feb-Mar 2020)", "market_shock": -0.37,
        "sector_shocks": {"Banking":-0.42,"Consumer":-0.30,"Coal Mining":-0.45,
                          "Metal Mining":-0.40,"Properti":-0.50,"Telco":-0.25,
                          "Technology":-0.35,"Healthcare":-0.20,"General":-0.35},
        "recovery_days": 180,
    },
    "fed_rate_hike_2022": {
        "label": "Fed Rate Hike Cycle 2022", "market_shock": -0.12,
        "sector_shocks": {"Banking":-0.08,"Consumer":-0.15,"Coal Mining":+0.30,
                          "Metal Mining":-0.05,"Properti":-0.20,"Telco":-0.10,
                          "Technology":-0.25,"Healthcare":-0.08,"General":-0.12},
        "recovery_days": 90,
    },
    "china_evergrande_2021": {
        "label": "China Evergrande Contagion 2021", "market_shock": -0.08,
        "sector_shocks": {"Banking":-0.10,"Consumer":-0.05,"Coal Mining":-0.12,
                          "Metal Mining":-0.15,"Properti":-0.18,"Telco":-0.05,
                          "Technology":-0.10,"Healthcare":-0.04,"General":-0.08},
        "recovery_days": 45,
    },
    "idx_circuit_breaker_2020": {
        "label": "IDX Circuit Breaker March 2020", "market_shock": -0.05,
        "sector_shocks": {"Banking":-0.07,"Consumer":-0.04,"Coal Mining":-0.06,
                          "Metal Mining":-0.05,"Properti":-0.08,"Telco":-0.03,
                          "Technology":-0.05,"Healthcare":-0.03,"General":-0.05},
        "recovery_days": 5,
    },
    "custom_minus30pct": {
        "label": "Custom Scenario: -30% Market Crash", "market_shock": -0.30,
        "sector_shocks": {"Banking":-0.30,"Consumer":-0.25,"Coal Mining":-0.35,
                          "Metal Mining":-0.40,"Properti":-0.40,"Telco":-0.20,
                          "Technology":-0.35,"Healthcare":-0.20,"General":-0.30},
        "recovery_days": 120,
    },
}

SECTOR_ALIASES = {
    "banking": "Banking",
    "coal mining": "Coal Mining",
    "metals mining": "Metal Mining",
    "metal mining": "Metal Mining",
    "property": "Properti",
    "properti": "Properti",
    "tech": "Technology",
    "technology": "Technology",
    "telco": "Telco",
    "healthcare": "Healthcare",
    "consumer": "Consumer",
    "consumer staples": "Consumer",
    "general": "General",
}


def _normalize_sector_label(sector: str) -> str:
    key = (sector or "General").strip().lower()
    return SECTOR_ALIASES.get(key, sector or "General")

@dataclass
class VaRResult:
    ticker: str; var_1d_95: float; var_1d_99: float
    cvar_95: float; cvar_99: float; ann_vol: float; method: str

@dataclass
class StressResult:
    scenario: str; label: str; impact_pct: float
    worst_ticker: str; worst_impact: float; recovery_days: int

@dataclass
class CorrResult:
    tickers: list[str]; matrix: list[list[float]]
    high_corr_pairs: list[tuple[str, str, float]]
    concentration: bool; div_score: float

@dataclass
class RiskReport:
    timestamp: str; var_95: float; cvar_95: float; ann_vol: float
    sharpe: float; max_drawdown: float; var_details: list[VaRResult]
    stress: list[StressResult]; correlation: CorrResult
    alerts: list[str]; risk_score: float


class RiskEngine:

    async def var(self, ticker: str, returns: pd.Series, method: str = "historical") -> VaRResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._var_sync, ticker, returns, method)

    def _var_sync(self, ticker: str, ret: pd.Series, method: str) -> VaRResult:
        ret = ret.dropna()
        if len(ret) < 20:
            return VaRResult(ticker, 0, 0, 0, 0, 0, method)
        if method == "parametric" and _SCIPY:
            mu, s = float(ret.mean()), float(ret.std())
            z95, z99 = _stats.norm.ppf(0.05), _stats.norm.ppf(0.01)
            v95 = -(mu + z95 * s) * 100; v99 = -(mu + z99 * s) * 100
            c95 = -(mu - s * _stats.norm.pdf(z95) / 0.05) * 100
            c99 = -(mu - s * _stats.norm.pdf(z99) / 0.01) * 100
        else:
            s95 = ret.sort_values()
            i95 = max(1, int(0.05 * len(s95))); i99 = max(1, int(0.01 * len(s95)))
            v95 = float(-s95.iloc[i95]) * 100; v99 = float(-s95.iloc[i99]) * 100
            c95 = float(-s95.iloc[:i95].mean()) * 100; c99 = float(-s95.iloc[:i99].mean()) * 100
        return VaRResult(
            ticker=ticker, var_1d_95=round(v95,3), var_1d_99=round(v99,3),
            cvar_95=round(c95,3), cvar_99=round(c99,3),
            ann_vol=round(float(ret.std())*np.sqrt(252)*100, 2), method=method,
        )

    async def stress_test(self, holdings: list[dict], total_val: float = 1.0) -> list[StressResult]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._stress_sync, holdings)

    def _stress_sync(self, holdings: list[dict]) -> list[StressResult]:
        out = []
        for key in STRESS_TEST_SCENARIOS:
            sc = SCENARIOS.get(key)
            if not sc: continue
            mkt = sc["market_shock"]; secs = sc.get("sector_shocks", {})
            total_loss, worst_t, worst_i = 0.0, "", 0.0
            for h in holdings:
                w = h.get("weight", 0.0); sector = _normalize_sector_label(h.get("sector", "General"))
                shock = secs.get(sector, mkt); impact = w * shock * 100
                total_loss += impact
                if impact < worst_i: worst_i = impact; worst_t = h.get("ticker", "")
            out.append(StressResult(
                scenario=key, label=sc["label"], impact_pct=round(total_loss,2),
                worst_ticker=worst_t, worst_impact=round(worst_i,2),
                recovery_days=sc.get("recovery_days",90),
            ))
        return sorted(out, key=lambda x: x.impact_pct)

    async def correlation(self, returns_dict: dict[str, pd.Series]) -> CorrResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._corr_sync, returns_dict)

    def _corr_sync(self, returns_dict: dict[str, pd.Series]) -> CorrResult:
        tickers = list(returns_dict.keys())
        df = pd.DataFrame(returns_dict).dropna()
        if df.empty or df.shape[1] < 2:
            return CorrResult(tickers, [], [], False, 1.0)
        corr = df.corr(); mat = corr.values.tolist()
        high: list[tuple[str,str,float]] = []
        n = len(tickers)
        for i in range(n):
            for j in range(i+1, n):
                val = float(corr.iloc[i,j])
                if abs(val) >= CORRELATION_ALERT_THRESHOLD:
                    high.append((tickers[i], tickers[j], round(val,3)))
        off = [abs(corr.iloc[i,j]) for i in range(n) for j in range(n) if i!=j]
        div = round(1 - float(np.mean(off)), 3) if off else 1.0
        return CorrResult(tickers=tickers, matrix=mat, high_corr_pairs=high,
                          concentration=len(high)>=max(1,n//3), div_score=div)


_engine: Optional[RiskEngine] = None
def get_risk_engine() -> RiskEngine:
    global _engine
    if _engine is None: _engine = RiskEngine()
    return _engine
