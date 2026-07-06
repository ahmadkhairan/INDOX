from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd
from config import (
    MIN_POSITION_SIZE_PCT, MAX_POSITION_SIZE_PCT,
    BL_TAU, CVAR_ALPHA, KELLY_FRACTION, REBALANCE_THRESHOLD,
    FEATURE_PORTFOLIO_OPTIMIZER,
)
from utils.logger import get_logger

log = get_logger("risk.optimizer")

try:
    from scipy.optimize import minimize
    _SCIPY = True
except ImportError:
    _SCIPY = False

try:
    import cvxpy as cp
    _CVXPY = True
except ImportError:
    _CVXPY = False


@dataclass
class OptResult:
    method: str; weights: dict[str,float]
    expected_return: float; expected_vol: float
    sharpe: float; cvar_95: float
    kelly_sizes: dict[str,float]
    rebalance_trades: list[dict]; notes: list[str]


class PortfolioOptimizer:

    async def optimize(self, tickers, returns_df, analyst_views=None,
                       current_weights=None, method="black_litterman") -> OptResult:
        if not FEATURE_PORTFOLIO_OPTIMIZER or not _SCIPY:
            return self._equal_weight(tickers, returns_df, current_weights)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync, tickers, returns_df,
                                          analyst_views, current_weights, method)

    def _sync(self, tickers, returns_df, analyst_views, current_weights, method) -> OptResult:
        try:
            if method == "black_litterman" and analyst_views:
                return self._bl(tickers, returns_df, analyst_views, current_weights)
            elif method == "cvar":
                return self._cvar(tickers, returns_df, current_weights)
            else:
                return self._mv(tickers, returns_df, current_weights)
        except Exception as exc:
            log.warning(f"Optimizer error: {exc}")
            return self._equal_weight(tickers, returns_df, current_weights)

    def _bl(self, tickers, returns_df, views, current_weights) -> OptResult:
        n = len(tickers)
        mu = returns_df[tickers].mean() * 252
        cov = returns_df[tickers].cov() * 252
        w_mkt = np.array([current_weights.get(t, 1/n) for t in tickers] if current_weights else [1/n]*n)
        w_mkt /= w_mkt.sum()
        lam = float((mu.values @ w_mkt) / (w_mkt @ cov.values @ w_mkt + 1e-10))
        pi = lam * cov.values @ w_mkt
        vtickers = [t for t in views if t in tickers]
        if not vtickers:
            return self._mv(tickers, returns_df, current_weights)
        k = len(vtickers)
        P = np.zeros((k, n)); q = np.zeros(k)
        for i, t in enumerate(vtickers):
            P[i, tickers.index(t)] = 1.0; q[i] = views[t]
        tau = BL_TAU
        diag = np.diag(cov.values)
        omega = np.diag([tau * diag[tickers.index(t)] for t in vtickers])
        Sigma = cov.values
        inv_term = np.linalg.inv(
            np.linalg.inv(tau*Sigma) + P.T @ np.linalg.inv(omega) @ P
        )
        mu_bl = inv_term @ (np.linalg.inv(tau*Sigma) @ pi + P.T @ np.linalg.inv(omega) @ q)
        w = self._max_sharpe(mu_bl, Sigma + inv_term, n)
        return self._build(tickers, dict(zip(tickers, w)), returns_df, current_weights, "black_litterman")

    def _cvar(self, tickers, returns_df, current_weights) -> OptResult:
        n = len(tickers)
        R = returns_df[tickers].fillna(0).values; T = len(R)
        if _CVXPY and T > 20:
            w = cp.Variable(n); zeta = cp.Variable(); u = cp.Variable(T)
            obj = zeta + (1/(CVAR_ALPHA*T)) * cp.sum(u)
            cons = [u >= -R@w - zeta, u >= 0, cp.sum(w)==1,
                    w >= MIN_POSITION_SIZE_PCT, w <= MAX_POSITION_SIZE_PCT]
            prob = cp.Problem(cp.Minimize(obj), cons)
            prob.solve(solver=cp.ECOS, warm_start=True)
            if prob.status in ("optimal","optimal_inaccurate") and w.value is not None:
                weights = np.clip(w.value, 0, 1); weights /= weights.sum()
                return self._build(tickers, dict(zip(tickers, weights)), returns_df, current_weights, "cvar")
        cov = np.cov(R.T)*252 if R.shape[0]>1 else np.eye(n)
        weights = self._minvar(n, cov)
        return self._build(tickers, dict(zip(tickers, weights)), returns_df, current_weights, "cvar_fallback")

    def _mv(self, tickers, returns_df, current_weights) -> OptResult:
        n = len(tickers)
        mu = returns_df[tickers].mean().values * 252
        cov = returns_df[tickers].cov().values * 252
        w = self._max_sharpe(mu, cov, n)
        return self._build(tickers, dict(zip(tickers, w)), returns_df, current_weights, "mean_variance")

    def _max_sharpe(self, mu, cov, n) -> np.ndarray:
        if not _SCIPY: return np.array([1/n]*n)
        def neg_sharpe(w):
            r = w@mu; v = np.sqrt(w@cov@w + 1e-10)
            return -(r-0.06)/v
        res = minimize(neg_sharpe, [1/n]*n, method="SLSQP",
                       bounds=[(MIN_POSITION_SIZE_PCT, MAX_POSITION_SIZE_PCT)]*n,
                       constraints={"type":"eq","fun":lambda w: np.sum(w)-1},
                       options={"maxiter":1000})
        if res.success:
            w = np.clip(res.x, 0, 1); return w/w.sum()
        return np.array([1/n]*n)

    def _minvar(self, n, cov) -> np.ndarray:
        if not _SCIPY: return np.array([1/n]*n)
        res = minimize(lambda w: w@cov@w, [1/n]*n, method="SLSQP",
                       bounds=[(0.02, MAX_POSITION_SIZE_PCT)]*n,
                       constraints={"type":"eq","fun":lambda w: np.sum(w)-1})
        if res.success:
            w = np.clip(res.x, 0, 1); return w/w.sum()
        return np.array([1/n]*n)

    def _equal_weight(self, tickers, returns_df, current_weights) -> OptResult:
        n = len(tickers)
        return self._build(tickers, {t:1/n for t in tickers}, returns_df, current_weights, "equal_weight")

    def _build(self, tickers, weights_dict, returns_df, current_weights, method) -> OptResult:
        valid = [t for t in tickers if t in returns_df.columns]
        if valid:
            R = returns_df[valid].fillna(0)
            w_v = np.array([weights_dict.get(t,0) for t in valid])
            mu_p = float(R.mean().values @ w_v) * 252 * 100
            cov_p = R.cov().values * 252
            vol_p = float(np.sqrt(w_v @ cov_p @ w_v + 1e-10)) * 100
            sharpe = (mu_p - 6.0) / (vol_p + 1e-8)
            pr = R @ w_v
            cvar = round(float(pr[pr <= pr.quantile(0.05)].mean()) * -100, 2)
        else:
            mu_p, vol_p, sharpe, cvar = 0.0, 0.0, 0.0, 0.0

        kelly: dict[str,float] = {}
        for t in tickers:
            if t in returns_df.columns:
                r = returns_df[t].dropna()
                wins = r[r>0]; losses = r[r<=0]
                if len(wins)>0 and len(losses)>0:
                    p = len(wins)/len(r); b = wins.mean()/(abs(losses.mean())+1e-8)
                    k = max(0,(p*b-(1-p))/(b+1e-8)) * KELLY_FRACTION
                    kelly[t] = round(min(k*100, MAX_POSITION_SIZE_PCT*100), 2)
                else:
                    kelly[t] = round(MIN_POSITION_SIZE_PCT*100, 2)

        trades = []
        for t, tgt in weights_dict.items():
            cur = (current_weights or {}).get(t, 0.0)
            if abs(tgt-cur) > REBALANCE_THRESHOLD:
                trades.append({"ticker":t, "action":"BUY" if tgt>cur else "SELL",
                               "current_pct":round(cur*100,1), "target_pct":round(tgt*100,1),
                               "delta_pct":round((tgt-cur)*100,1)})

        notes = []
        if method in ("equal_weight","cvar_fallback"):
            notes.append("⚠️ Optimizer library tidak lengkap — install scipy/cvxpy")
        if not trades:
            notes.append("✅ Portfolio optimal, tidak perlu rebalancing")

        return OptResult(method=method, weights=weights_dict,
                         expected_return=round(mu_p,2), expected_vol=round(vol_p,2),
                         sharpe=round(sharpe,3), cvar_95=cvar,
                         kelly_sizes=kelly, rebalance_trades=trades, notes=notes)

    def kelly_size(self, win_rate: float, avg_win: float, avg_loss: float) -> dict:
        p = win_rate/100; b = avg_win/(avg_loss+1e-8)
        full = max(0,(p*b-(1-p))/(b+1e-8)); frac = full*KELLY_FRACTION
        size = min(frac*100, MAX_POSITION_SIZE_PCT*100)
        return {"kelly_full_pct":round(full*100,2),"kelly_frac_pct":round(frac*100,2),"position_pct":round(size,2)}


_opt: Optional[PortfolioOptimizer] = None
def get_optimizer() -> PortfolioOptimizer:
    global _opt
    if _opt is None: _opt = PortfolioOptimizer()
    return _opt
