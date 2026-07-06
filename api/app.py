from __future__ import annotations
import time
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from config import API_CORS_ORIGINS, API_SECRET, ENABLE_METRICS, ENV
from utils.error_utils import user_error_message
from utils.logger import get_logger
from utils.ticker_utils import normalize_ticker
log = get_logger("api")

try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
    REQ = Counter("idx_requests_total","Total requests",["method","endpoint","status"])
    LAT = Histogram("idx_request_seconds","Latency",["endpoint"])
    _PROM = True
except ImportError:
    _PROM = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"IDX Bot API v4 ({ENV})")
    try:
        from core.cache import get_cache; await get_cache(); log.info("Cache ready")
    except Exception as exc: log.warning(f"Cache: {exc}")
    try:
        from memory.vector_memory import get_vector_memory; await get_vector_memory(); log.info("VecMem ready")
    except Exception as exc: log.warning(f"VecMem: {exc}")
    yield

app = FastAPI(title="IDX Analyst Bot API v4", version="4.0.0", lifespan=lifespan,
              docs_url="/docs" if ENV != "production" else None)
API_VERSION = "v4"
API_PREFIX = f"/api/{API_VERSION}"
api_v4 = APIRouter(prefix=API_PREFIX, tags=[API_VERSION])
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(API_CORS_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)


def _ensure_api_secret() -> str:
    secret = (API_SECRET or "").strip()
    if not secret or secret.lower() == "changeme":
        raise HTTPException(503, "API_SECRET belum dikonfigurasi dengan aman")
    return secret

def auth(x_api_key: Optional[str] = Header(None)) -> bool:
    secret = _ensure_api_secret()
    if x_api_key != secret: raise HTTPException(401, "Invalid API key")
    return True

@app.middleware("http")
async def metrics_mw(request, call_next):
    start = time.time(); resp = await call_next(request); dur = time.time()-start
    resp.headers["X-API-Version"] = API_VERSION
    if _PROM and ENABLE_METRICS:
        REQ.labels(request.method, request.url.path, resp.status_code).inc()
        LAT.labels(request.url.path).observe(dur)
    return resp

@app.get("/")
@app.get("/health")
async def health(): return {"status":"ok","version":"4.0.0","env":ENV}

@app.get("/metrics")
async def metrics():
    if not _PROM or not ENABLE_METRICS: return PlainTextResponse("# disabled\n")
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/ready")
async def ready():
    checks = {}
    try:
        from core.cache import get_cache
        c = await get_cache()
        await c.set("_hc","ok",ttl=5); checks["redis"] = "ok"
    except Exception as e: 
        checks["redis"] = f"error:{e}"
    try:
        from core.ai_engine import get_groq_status
        groq = get_groq_status()
        checks["groq"] = groq["status"]
        checks["groq_message"] = groq["message"]
    except Exception as e:
        checks["groq"] = f"error:{e}"
    # Groq Health Check baru (lebih akurat)
    try:
        from utils.groq_health import get_groq_health
        health = get_groq_health()
        checks["groq_health"] = health.get("ok")
        checks["groq_health_message"] = health.get("message", "")
    except Exception as e:
        checks["groq_health"] = False
        checks["groq_health_message"] = str(e)
        
    ok = checks.get("redis") == "ok" and checks.get("groq") == "configured"
    from fastapi.responses import JSONResponse
    return JSONResponse({"status":"ready" if ok else "degraded","checks":checks}, status_code=200 if ok else 503)

class AnalyzeReq(BaseModel):
    ticker: str; user_question: str = ""
    include_sentiment: bool = True; include_var: bool = True

@api_v4.post("/analyze")
async def analyze(req: AnalyzeReq, _: bool = Depends(auth)):
    try:
        ticker = normalize_ticker(req.ticker)
        from data.fetcher import fetch_ticker_data, fetch_news
        from core.ai_engine import analyze_ticker_v4
        from memory.vector_memory import get_vector_memory
        data = await fetch_ticker_data(ticker); news = await fetch_news(ticker)
        if not data: raise HTTPException(404, f"Data tidak ditemukan: {ticker}")
        if isinstance(data, dict) and data.get("error"):
            raise HTTPException(502, data["error"])
        rag_ctx = ""
        try: vm = await get_vector_memory(); rag_ctx = await vm.get_rag_context(ticker, req.user_question or "analisis")
        except Exception: pass
        sent_dict = None
        if req.include_sentiment:
            try:
                from sentiment.pipeline import get_sentiment_pipeline
                ts = get_sentiment_pipeline().get(ticker)
                if ts: sent_dict = {"score":ts.score,"label":ts.label,"has_buyback":ts.has_buyback,"has_dividend":ts.has_dividend}
            except Exception: pass
        var_dict = None
        if req.include_var:
            try:
                from risk.engine import get_risk_engine
                from utils.yf_guard import get_history
                df = get_history(f"{ticker}.JK", period="1y", auto_adjust=True)
                if not df.empty:
                    vr = await get_risk_engine().var(ticker, df["Close"].pct_change().dropna())
                    var_dict = {"var_1d_95":vr.var_1d_95,"cvar_95":vr.cvar_95,"ann_vol":vr.ann_vol}
            except Exception: pass
        analysis = await analyze_ticker_v4(data=data,news=news,user_question=req.user_question,
                                            rag_context=rag_ctx,sentiment=sent_dict,var_result=var_dict)
        return {"ticker":ticker,"analysis":analysis,"sentiment":sent_dict,"var":var_dict,
                "timestamp":time.strftime("%Y-%m-%d %H:%M:%S")}
    except ValueError as exc: raise HTTPException(400, str(exc))
    except HTTPException: raise
    except Exception as exc: raise HTTPException(500, user_error_message(exc))

class OptReq(BaseModel):
    tickers: list[str]; analyst_views: dict[str,float] = {}; method: str = "black_litterman"

@api_v4.post("/optimize")
async def optimize(req: OptReq, _: bool = Depends(auth)):
    try:
        import pandas as pd
        from risk.optimizer import get_optimizer
        from utils.yf_guard import get_history
        tickers = [normalize_ticker(t) for t in req.tickers]
        if len(tickers) < 2:
            raise HTTPException(400, "Minimal 2 ticker untuk optimisasi portfolio")
        data = {}
        for ticker in tickers:
            hist = get_history(f"{ticker}.JK", period="2y", auto_adjust=True)
            if not hist.empty:
                data[ticker] = hist["Close"].pct_change().dropna()
        if len(data) < 2: raise HTTPException(400, "Minimal 2 ticker valid untuk optimisasi portfolio")
        res = await get_optimizer().optimize(list(data.keys()), pd.DataFrame(data),
                                              analyst_views=req.analyst_views or None, method=req.method)
        return {"method":res.method,"weights":res.weights,"expected_return":res.expected_return,
                "expected_vol":res.expected_vol,"sharpe":res.sharpe,"cvar_95":res.cvar_95,
                "kelly_sizes":res.kelly_sizes,"rebalance_trades":res.rebalance_trades,"notes":res.notes}
    except ValueError as exc: raise HTTPException(400, str(exc))
    except HTTPException: raise
    except Exception as exc: raise HTTPException(500, user_error_message(exc))

@api_v4.get("/backtest/{ticker}")
async def backtest(ticker: str, months: int = 3, walk_forward: bool = False, _: bool = Depends(auth)):
    try:
        ticker = normalize_ticker(ticker)
        if walk_forward:
            from backtest.engine_backtest import get_wf_engine
            res = await get_wf_engine().run(ticker)
            return {"ticker":res.ticker,"is_robust":res.is_robust,"robustness":res.robustness,
                    "oos":{"trades":res.oos_agg.total_trades,"win_rate":res.oos_agg.win_rate,
                           "pf":res.oos_agg.pf,"return":res.oos_agg.total_return,"sharpe":res.oos_agg.sharpe},
                    "recommendations":res.recommendations}
        else:
            from backtest.engine_backtest import run_backtest_v4
            return await run_backtest_v4(ticker, months=months)
    except ValueError as exc: raise HTTPException(400, str(exc))
    except Exception as exc: raise HTTPException(500, user_error_message(exc))

app.include_router(api_v4)
