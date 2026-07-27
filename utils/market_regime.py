from __future__ import annotations

import time

from utils.ticker_utils import IDX_UNIVERSE
from utils.yf_guard import YFinanceUnavailable, download, get_fast_info, get_history, get_info


_coal_cache = {"price": 0.0, "ts": 0}
_ihsg_regime_cache = {"ts": 0, "data": {}}


def _safe(val, default=0.0):
    if val is None:
        return default
    try:
        num = float(val)
        return default if num != num or num in (float("inf"), float("-inf")) else num
    except (TypeError, ValueError):
        return default


def _yf_info_with_retry(ticker_jk: str, max_attempts: int = 3) -> dict:
    for attempt in range(max_attempts):
        try:
            info = get_info(ticker_jk)
            if info and len(info) > 5:
                return info
        except YFinanceUnavailable:
            raise
        except Exception as exc:
            if attempt == max_attempts - 1:
                raise exc
        time.sleep(2 ** attempt)
    return {}


def get_coal_price() -> dict:
    now_ts = time.time()
    if _coal_cache.get("ts", 0) > 0 and (now_ts - _coal_cache["ts"]) < 3600 and _coal_cache.get("price", 0) > 0:
        return _coal_cache

    coal_price = 0.0
    source = ""

    for ticker in ["MTF=F", "COAL.L"]:
        try:
            fast = get_fast_info(ticker)
            price = float(fast.last_price or fast.previous_close or 0)
            if price > 5:
                coal_price = price
                source = ticker
                break
        except YFinanceUnavailable:
            break
        except Exception:
            pass

    if coal_price == 0.0:
        for ticker in ["MTF=F", "COAL.L"]:
            try:
                hist = get_history(ticker, period="5d", interval="1d")
                if not hist.empty:
                    price = float(hist["Close"].dropna().iloc[-1])
                    if price > 5:
                        coal_price = price
                        source = f"{ticker} (hist)"
                        break
            except YFinanceUnavailable:
                break
            except Exception:
                pass

    if coal_price == 0.0:
        stale_price = _safe(_coal_cache.get("price", 0.0))
        if stale_price > 0:
            _coal_cache.update({
                "price": stale_price,
                "ts": now_ts,
                "source": "stale_cache",
                "rally": False,
                "hot": False,
                "stale": True,
                "available": False,
                "label": f"Stale (${stale_price:.0f}/ton)",
                "score_bonus": 0,
            })
            return _coal_cache
        result = {
            "price": 0.0,
            "ts": now_ts,
            "source": "unavailable",
            "rally": False,
            "hot": False,
            "stale": False,
            "available": False,
            "label": "Unavailable",
            "score_bonus": 0,
        }
        _coal_cache.update(result)
        return _coal_cache

    rally = coal_price > 90.0
    hot = coal_price > 120.0
    _coal_cache.update({
        "price": coal_price,
        "ts": now_ts,
        "source": source,
        "rally": rally,
        "hot": hot,
        "stale": False,
        "available": True,
        "label": f"HOT 🔥 (${coal_price:.0f}/ton)" if hot else (f"RALLY (${coal_price:.0f}/ton)" if rally else f"Soft (${coal_price:.0f}/ton)"),
        "score_bonus": 15 if hot else (10 if rally else 0),
    })
    return _coal_cache


def get_ihsg() -> dict:
    try:
        info = _yf_info_with_retry("^JKSE")
        price = _safe(info.get("regularMarketPrice") or info.get("currentPrice"))
        prev = _safe(info.get("previousClose"))
        chg = ((price - prev) / prev * 100) if prev > 0 else 0.0
        return {"price": round(price, 2), "change_pct": round(chg, 2), "volume": _safe(info.get("regularMarketVolume"))}
    except Exception:
        return {"price": 0.0, "change_pct": 0.0, "volume": 0.0}


def get_ihsg_regime() -> dict:
    now_ts = time.time()
    if _ihsg_regime_cache["ts"] > 0 and (now_ts - _ihsg_regime_cache["ts"]) < 1800:
        return _ihsg_regime_cache["data"]

    try:
        hist = get_history("^JKSE", period="1y", interval="1d", auto_adjust=True)
        if hist.empty or len(hist) < 50:
            result = {"regime": "UNKNOWN", "warning": "", "ihsg_ma50": 0.0, "ihsg_ma200": 0.0, "ihsg_last": 0.0, "ma_label": "MA50"}
            _ihsg_regime_cache.update({"ts": now_ts, "data": result})
            return result

        close = hist["Close"].astype(float)
        # Regime guard untuk pick memakai MA50. ihsg_ma200 dipertahankan
        # sebagai alias agar consumer lama tetap kompatibel.
        ma_val = float(close.rolling(50).mean().iloc[-1])
        ma_label = "MA50"

        consecutive_bear = 0
        for price in close.values[::-1][:10]:
            if price < ma_val:
                consecutive_bear += 1
            else:
                break

        last_price = round(float(close.iloc[-1]), 2)
        if consecutive_bear >= 3:
            regime = "BEAR"
            warning = (
                f"**BEAR MARKET WARNING**: IHSG di bawah {ma_label} ({ma_val:,.0f}) "
                f"selama {consecutive_bear} hari berturut-turut. Semua entry baru DILARANG — "
                f"tunggu IHSG tutup di atas {ma_label} minimal 2 hari berturut-turut sebelum re-entry."
            )
        elif consecutive_bear >= 1:
            regime = "CAUTION"
            warning = (
                f"**CAUTION**: IHSG mendekati/menyentuh {ma_label} ({ma_val:,.0f}) "
                f"({consecutive_bear} hari bear). Kurangi ukuran posisi 50%, perkuat SL."
            )
        else:
            regime = "BULL"
            warning = ""

        result = {
            "regime": regime,
            "warning": warning,
            "ihsg_ma50": round(ma_val, 2),
            "ihsg_ma200": round(ma_val, 2),
            "ihsg_last": last_price,
            "bear_streak": consecutive_bear,
            "ma_label": ma_label,
        }
        _ihsg_regime_cache.update({"ts": now_ts, "data": result})
        return result
    except YFinanceUnavailable as exc:
        print(f"[regime] ⚠️ yfinance unavailable: {exc}")
        result = {"regime": "UNKNOWN", "warning": "Data pasar sementara tidak tersedia", "ihsg_ma50": 0.0, "ihsg_ma200": 0.0, "ihsg_last": 0.0, "ma_label": "MA50"}
        _ihsg_regime_cache.update({"ts": now_ts - 1500, "data": result})
        return result
    except Exception as exc:
        print(f"[regime] ⚠️ Error: {exc}")
        result = {"regime": "UNKNOWN", "warning": "", "ihsg_ma50": 0.0, "ihsg_ma200": 0.0, "ihsg_last": 0.0, "ma_label": "MA50"}
        _ihsg_regime_cache.update({"ts": now_ts - 1500, "data": result})
        return result


def get_top_movers(n: int = 5) -> dict:
    tickers_jk = [f"{ticker}.JK" for ticker in IDX_UNIVERSE]
    try:
        data = download(tickers_jk, period="2d", interval="1d", auto_adjust=True, progress=False, group_by="ticker")
    except YFinanceUnavailable:
        return {"gainers": [], "losers": []}
    except Exception:
        return {"gainers": [], "losers": []}

    changes = []
    for ticker in IDX_UNIVERSE:
        try:
            prices = data.get((f"{ticker}.JK", "Close")) or data.get(f"{ticker}.JK", {}).get("Close")
            if prices is None or len(prices) < 2:
                continue
            prev = _safe(prices.iloc[-2])
            curr = _safe(prices.iloc[-1])
            if prev > 0:
                chg = (curr - prev) / prev * 100
                changes.append({"ticker": ticker, "price": round(curr, 0), "change_pct": round(chg, 2)})
        except Exception:
            continue

    changes.sort(key=lambda item: item["change_pct"], reverse=True)
    return {"gainers": changes[:n], "losers": changes[-n:][::-1]}
