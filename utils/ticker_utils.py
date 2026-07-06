from __future__ import annotations

import re


IDX_UNIVERSE: tuple[str, ...] = (
    "BBCA", "BBRI", "BMRI", "BBNI", "BNGA", "NISP", "PNBN", "BRIS",
    "TLKM", "EXCL", "ISAT", "ASII", "UNTR", "UNVR", "ICBP", "INDF", "MYOR", "CPIN", "SIDO", "KLBF", "MIKA",
    "ADRO", "PTBA", "BYAN", "ITMG", "HRUM", "ANTM", "MDKA", "INCO", "TINS",
    "BSDE", "CTRA", "SMRA", "LPKR", "PWON", "GOTO", "EMTK", "MTEL", "SMGR", "INTP",
    "INKP", "TKIM", "GGRM", "HMSP", "PGAS", "AKRA", "MEDC", "ELSA", "JPFA", "MAIN",
    "HEAL", "TSPC", "ACES", "MAPI", "LPPF", "AMRT", "BULL", "NCKL", "DNET", "WIFI",
    "BRPT", "TPIA", "MDIY", "TOWR", "TBIG", "ESSA", "RAJA", "MBMA", "DSSA",
    "DOID", "TOBA", "MBAP", "BOSS", "PSAB", "BTPS", "BJTM", "BJBR", "BEKS",
    "BBTN", "BDMN", "WIKA", "WSKT", "ADHI", "PTPP", "GIAA", "SMDR",
)

_TICKER_RE = re.compile(r"^[A-Z]{4}$")


def normalize_ticker(ticker: str) -> str:
    value = (ticker or "").strip().upper()
    if not _TICKER_RE.fullmatch(value):
        raise ValueError("Ticker harus 4 huruf A-Z, contoh: BBCA")
    return value


def is_valid_ticker(ticker: str) -> bool:
    try:
        normalize_ticker(ticker)
        return True
    except ValueError:
        return False


def normalize_tickers(tickers: list[str] | tuple[str, ...]) -> list[str]:
    return [normalize_ticker(ticker) for ticker in tickers]
