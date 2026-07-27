# config.py — IDX Analyst Bot v4 (No ML)
from __future__ import annotations
import os
import json
from typing import Final
from dotenv import load_dotenv

load_dotenv()


def _split_csv_env(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _split_int_csv_env(name: str) -> tuple[int, ...]:
    values = []
    for item in _split_csv_env(name):
        try:
            values.append(int(item))
        except ValueError:
            continue
    return tuple(values)


def _load_ai_providers() -> tuple[dict, ...]:
    raw = os.getenv("AI_PROVIDERS", "").strip()
    if not raw:
        return ()
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            return ()
        result = []
        for item in items:
            if not isinstance(item, dict) or item.get("enabled", True) is False:
                continue
            provider = dict(item)
            key_env = provider.pop("api_key_env", "")
            if key_env:
                provider["api_key"] = os.getenv(key_env, "")
            result.append(provider)
        return tuple(result)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()

DISCORD_TOKEN: Final[str]    = os.getenv("DISCORD_TOKEN", "")
DAILY_CHANNEL_ID: Final[int] = int(os.getenv("DAILY_CHANNEL_ID", "0"))
ALERT_CHANNEL_ID: Final[int] = int(os.getenv("ALERT_CHANNEL_ID", os.getenv("DAILY_CHANNEL_ID", "0")))

GROQ_API_KEY: Final[str]       = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: Final[str]         = "llama-3.3-70b-versatile"
GROQ_MODEL_FAST: Final[str]    = "llama-3.1-8b-instant"
GROQ_MAX_TOKENS: Final[int]    = 3000
GROQ_TEMPERATURE: Final[float] = 0.20
AI_PROVIDERS: Final[tuple[dict, ...]] = _load_ai_providers()

SECTORS_API_KEY: Final[str] = os.getenv("SECTORS_API_KEY", "")
SECTORS_BASE: Final[str]    = "https://api.sectors.app/v1"

REDIS_URL: Final[str]             = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_TTL_PRICE: Final[int]       = 60
REDIS_TTL_FUNDAMENTAL: Final[int] = 3600
REDIS_TTL_SENTIMENT: Final[int]   = 900
REDIS_TTL_SCAN: Final[int]        = 1800

API_HOST: Final[str]    = os.getenv("API_HOST", "0.0.0.0")
API_PORT: Final[int]    = int(os.getenv("PORT", "8080"))
API_SECRET: Final[str]  = os.getenv("API_SECRET", "").strip()
API_CORS_ORIGINS: Final[tuple[str, ...]] = _split_csv_env(
    "API_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"
    if os.getenv("ENV", "production") != "production" else "",
)
ENABLE_METRICS: Final[bool] = os.getenv("ENABLE_METRICS", "true").lower() == "true"

DAILY_HOUR_UTC: Final[int]         = int(os.getenv("DAILY_HOUR_UTC", "1"))
DAILY_MINUTE: Final[int]           = int(os.getenv("DAILY_MINUTE", "0"))
MONTHLY_HOUR_UTC: Final[int]       = 10
MONTHLY_MINUTE: Final[int]         = 0
ALERT_CHECK_SECONDS: Final[int]    = int(os.getenv("ALERT_CHECK_SECONDS", "60"))
SENTIMENT_REFRESH_MINUTES: Final[int] = 15
CHAT_CHANNEL_ALLOWLIST: Final[tuple[int, ...]] = _split_int_csv_env("CHAT_CHANNEL_ALLOWLIST")

VAR_CONFIDENCE: Final[float]             = 0.95
MAX_PORTFOLIO_VAR_PCT: Final[float]      = 0.03
MAX_SECTOR_CONCENTRATION: Final[float]   = 0.40
CORRELATION_ALERT_THRESHOLD: Final[float]= 0.85

STRESS_TEST_SCENARIOS: Final[list[str]] = [
    "covid_crash_2020", "fed_rate_hike_2022", "china_evergrande_2021",
    "idx_circuit_breaker_2020", "custom_minus30pct",
]

MAX_PORTFOLIO_POSITIONS: Final[int]   = 10
MIN_POSITION_SIZE_PCT: Final[float]   = 0.05
MAX_POSITION_SIZE_PCT: Final[float]   = 0.25
BL_TAU: Final[float]                  = 0.05
CVAR_ALPHA: Final[float]              = 0.05
KELLY_FRACTION: Final[float]          = 0.25
REBALANCE_THRESHOLD: Final[float]     = 0.10

AGGRESSIVE_MODE: Final[bool] = os.getenv("AGGRESSIVE_MODE", "true").lower() == "true"
TARGET_TRADES_PER_MONTH: Final[int] = int(os.getenv("TARGET_TRADES_PER_MONTH", "4"))
RISK_PER_TRADE_PCT: Final[float] = float(os.getenv("RISK_PER_TRADE_PCT", "0.02"))
MAX_RISK_PER_TRADE_PCT: Final[float] = 0.03
MAX_PORTFOLIO_DD_PCT: Final[float] = float(os.getenv("MAX_DD_PCT", "0.15"))
DD_PAUSE_TRADING: Final[bool] = True
MIN_ENTRY_CONDITIONS: Final[int] = int(os.getenv("MIN_ENTRY_COND", "3"))
MIN_ADX_ENTRY: Final[float] = float(os.getenv("MIN_ADX", "15.0"))
MIN_VOL_RATIO_ENTRY: Final[float] = float(os.getenv("MIN_VOL", "1.1"))

NEWS_RSS_FEEDS: Final[list[str]] = [
    "https://www.cnbcindonesia.com/rss",
    "https://market.bisnis.com/rss",
    "https://investasi.kontan.co.id/rss/news",
]

WF_IN_SAMPLE_MONTHS: Final[int]  = 12
WF_OUT_SAMPLE_MONTHS: Final[int] = 3
WF_STEP_MONTHS: Final[int]       = 3
MC_SIMULATIONS: Final[int]       = 50000
MC_BOOTSTRAP_BLOCK: Final[int]   = 10

DEFAULT_WATCHLIST: Final[list[str]] = [
    "BBCA","BBRI","BMRI","BNGA","BBNI","TLKM","ISAT","EXCL","ASII",
    "UNVR","INDF","MYOR","ICBP","CPIN","SIDO",
    "ADRO","PTBA","BYAN","ITMG","HRUM","ANTM","MDKA","INCO","TINS",
    "GOTO","EMTK","BSDE","CTRA","SMRA","PWON","KLBF","MIKA","HEAL",
]

SCORE_WEIGHTS: Final[dict[str, float]] = {
    "fundamental": 0.35, "technical": 0.30, "flow": 0.20, "sentiment": 0.15,
}

MIN_MARKET_CAP_T: Final[float]  = 1.0
MIN_DAILY_VALUE_B: Final[float] = 10.0

FUNDAMENTAL_GOOD: Final[dict[str, float]] = {
    "roe_min": 15.0, "per_max": 15.0, "pbv_max": 2.0,
    "der_max": 1.0, "revenue_growth_min": 10.0, "eps_growth_min": 10.0,
}

LOG_LEVEL: Final[str]  = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT: Final[str] = os.getenv("LOG_FORMAT", "json")
ENV: Final[str]        = os.getenv("ENV", "production")
IS_PROD: Final[bool]   = ENV == "production"

FEATURE_VECTOR_MEMORY: Final[bool]       = os.getenv("FEAT_RAG",  "false").lower() == "true"
FEATURE_PORTFOLIO_OPTIMIZER: Final[bool] = os.getenv("FEAT_OPT",  "true").lower() == "true"
FEATURE_RISK_ENGINE: Final[bool]         = os.getenv("FEAT_RISK", "true").lower() == "true"
FEATURE_SENTIMENT_PIPELINE: Final[bool]  = os.getenv("FEAT_SENT", "true").lower() == "true"
