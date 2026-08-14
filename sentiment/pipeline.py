from __future__ import annotations
import asyncio, hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import aiohttp
from config import NEWS_RSS_FEEDS, FEATURE_SENTIMENT_PIPELINE
from utils.logger import get_logger
log = get_logger("sentiment")

@dataclass
class SentItem:
    title: str; summary: str; url: str; source: str
    published: str; tickers: list[str]; score: float
    label: str; category: str; impact: str; _hash: str = ""

@dataclass
class TickerSentiment:
    ticker: str; score: float; label: str
    pos_count: int; neg_count: int; neu_count: int
    has_buyback: bool; has_dividend: bool
    has_earn_beat: bool; has_reg_risk: bool
    items: list[SentItem] = field(default_factory=list)
    last_updated: str = ""

POS_KW = {"laba bersih naik":0.7,"revenue tumbuh":0.6,"buyback":0.8,"dividen":0.6,
          "ekspansi":0.5,"kontrak baru":0.6,"profit meningkat":0.7,"akuisisi":0.4}
NEG_KW = {"rugi":-0.8,"kerugian":-0.7,"pailit":-1.0,"gagal bayar":-0.9,
          "delisting":-0.9,"turun":-0.4,"merosot":-0.5,"sanksi OJK":-0.7,
          "pembekuan":-0.8,"NPL meningkat":-0.6,"default":-0.8}
CAT_KW = {
    "earnings":["laba","rugi","EPS","pendapatan"],
    "buyback":["buyback","pembelian kembali saham"],
    "dividend":["dividen","dividend","RUPS"],
    "macro":["BI rate","inflasi","rupiah","IHSG","Fed"],
    "regulatory":["OJK","BEI","regulasi"],
}

# Indonesian sentiment scorer (IndoBERT + lexicon fallback)
_scorer = None
def _get_scorer():
    global _scorer
    if _scorer is None:
        from sentiment.indobert import get_sentiment_scorer
        _scorer = get_sentiment_scorer()
    return _scorer

def _score(title, summary):
    """Score sentiment using IndoBERT (with lexicon fallback).

    Priority:
    1. IndoBERT model (if available) — understands context
    2. Lexicon + keywords — fast fallback
    """
    text = f"{title}. {summary}".strip()
    if not text:
        return 0.0, "NEUTRAL"

    # Try IndoBERT first
    try:
        scorer = _get_scorer()
        score, label = scorer.score(text)
        if score != 0.0 or label != "NEUTRAL":
            return score, label
    except Exception:
        pass

    # Fallback: keyword-based
    text_lower = text.lower(); s = 0.0
    for kw, v in POS_KW.items():
        if kw.lower() in text_lower: s += v
    for kw, v in NEG_KW.items():
        if kw.lower() in text_lower: s += v
    s = max(-1.0, min(1.0, s))
    lbl = ("VERY_POS" if s>0.5 else "POS" if s>0.1 else "VERY_NEG" if s<-0.5 else "NEG" if s<-0.1 else "NEUTRAL")
    return s, lbl

def _category(title, summary):
    text = (title+" "+summary).lower()
    for cat, kws in CAT_KW.items():
        if any(k.lower() in text for k in kws): return cat
    return "other"

def _tickers(title, summary, wl):
    text = (title+" "+summary).upper()
    return [t for t in wl if t in text]

class SentimentPipeline:
    def __init__(self, watchlist):
        self._wl = watchlist; self._cache: dict[str,TickerSentiment] = {}; self._seen: set[str] = set()

    async def refresh(self):
        if not FEATURE_SENTIMENT_PIPELINE: return {}
        raw = await self._fetch_all()
        unique = self._dedup(raw)
        scored = await self._score_all(unique)
        self._cache = self._aggregate(scored)
        log.info(f"Sentiment: {len(self._cache)} tickers updated")
        return self._cache

    def get(self, ticker): return self._cache.get(ticker)
    def market_sentiment(self):
        if not self._cache: return {"label":"NEUTRAL","score":0.0,"coverage":0}
        scores = [s.score for s in self._cache.values()]
        avg = sum(scores)/len(scores)
        return {"label":"BULLISH" if avg>0.2 else "BEARISH" if avg<-0.2 else "NEUTRAL",
                "score":round(avg,3),"coverage":len(self._cache)}

    async def _fetch_all(self):
        out = []
        async with aiohttp.ClientSession() as sess:
            tasks = [self._fetch_rss(sess, u) for u in NEWS_RSS_FEEDS] + [self._fetch_idx(sess)]
            
            # Additional free sentiment sources
            try:
                from sentiment.sources import scrape_stockbit_forum, scrape_telegram_signals
                tasks.append(scrape_telegram_signals())
                # Scrape stockbit for first 5 watchlist tickers to avoid rate limiting
                for t in self._wl[:5]:
                    tasks.append(scrape_stockbit_forum(t))
            except Exception as exc:
                log.warning(f"Error initializing extra sentiment sources: {exc}")

            results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list): out.extend(r)
        return out


    async def _fetch_rss(self, sess, url):
        try:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200: return []
                text = await r.text(errors="replace")
            import feedparser
            feed = feedparser.parse(text)
            return [{"title":getattr(e,"title",""),"summary":getattr(e,"summary","")[:300],
                     "url":getattr(e,"link",url),"published":getattr(e,"published",""),"source":url}
                    for e in feed.entries[:15]]
        except Exception as exc:
            log.warning(f"RSS {url[:40]}: {exc}"); return []

    async def _fetch_idx(self, sess):
        params = {"IndexFrom": 1, "PageSize": 20}
        url = "https://www.idx.co.id/primary/NewsAnnouncement/GetAllAnnouncement"
        data = None
        try:
            async with sess.get(url, params=params, timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status == 200:
                    text = await r.text()
                    if text.strip() and not text.strip().startswith("<"):
                        import json
                        data = json.loads(text)
        except Exception as exc:
            log.debug(f"IDX news aiohttp: {exc}")

        if data is None:
            try:
                from utils.idx_api import get_idx_source
                data = await get_idx_source()._fetch_json(
                    "NewsAnnouncement/GetAllAnnouncement", params
                )
            except Exception as exc:
                log.warning(f"IDX fetch: {exc}")
                return []

        items = data.get("Items") or data.get("data") or data.get("Data") or []
        out = []
        for a in items:
            title = a.get("Title") or a.get("Headline") or ""
            if not title:
                continue
            code = (a.get("Code") or "").strip()
            pub = a.get("PublishDate") or a.get("Date") or ""
            if pub and "T" in pub:
                pub = pub[:16].replace("T", " ")
            attachments = a.get("Attachments") or []
            link = ""
            if attachments and isinstance(attachments[0], dict):
                link = attachments[0].get("FullSavePath") or attachments[0].get("PDFFilename") or ""
            out.append({
                "title": title,
                "summary": title[:300],
                "url": link,
                "published": pub,
                "source": "IDX Official",
                "_ticker": code,
            })
        return out

    def _dedup(self, items):
        unique = []
        for it in items:
            h = hashlib.md5((it.get("title","") + it.get("url","")).encode()).hexdigest()
            if h not in self._seen:
                self._seen.add(h); it["_hash"] = h; unique.append(it)
        return unique

    async def _score_all(self, items):
        ai_scores = await self._ai_score(items)
        scored = []
        for i, it in enumerate(items):
            sc, lbl = (ai_scores[i] if ai_scores and i < len(ai_scores) else _score(it.get("title",""), it.get("summary","")))
            tickers = _tickers(it.get("title",""), it.get("summary",""), self._wl)
            if it.get("_ticker") and it["_ticker"] not in tickers: tickers.insert(0, it["_ticker"])
            scored.append(SentItem(
                title=it.get("title",""), summary=it.get("summary",""),
                url=it.get("url",""), source=it.get("source",""),
                published=it.get("published",""), tickers=tickers, score=sc, label=lbl,
                category=_category(it.get("title",""), it.get("summary","")),
                impact="HIGH" if abs(sc)>0.6 else ("MEDIUM" if abs(sc)>0.3 else "LOW"),
                _hash=it.get("_hash",""),
            ))
        return scored

    async def _ai_score(self, items):
        if not items: return []
        batch = items[:12]
        titles = "\n".join(f"{i+1}. {it.get('title','')}" for i, it in enumerate(batch))
        prompt = f"Scoring sentimen berita saham IDX. Skala -1.0 sampai +1.0.\nJawab hanya angka per baris, tanpa teks lain.\n\nBerita:\n{titles}"
        try:
            from utils.llm_provider import chat_completion
            text, _provider = chat_completion(
                [{"role":"user","content":prompt}],
                "Kamu adalah mesin scoring sentimen. Jawab hanya angka per baris.",
                80, 0.1,
            )
            out = []
            for line in text.strip().split("\n"):
                try:
                    v = max(-1.0, min(1.0, float(line.strip())))
                    lbl = "VERY_POS" if v>0.5 else "POS" if v>0.1 else "VERY_NEG" if v<-0.5 else "NEG" if v<-0.1 else "NEUTRAL"
                    out.append((v, lbl))
                except ValueError:
                    out.append((0.0, "NEUTRAL"))
            return out
        except Exception as exc:
            log.warning(f"AI sentiment: {exc}"); return []

    def _aggregate(self, items):
        """Aggregate sentiment per ticker using smart aggregator
        (time-decay + source credibility weighting).
        """
        from sentiment.aggregator import aggregate_smart

        tm: dict[str, list[SentItem]] = {}
        for it in items:
            for t in it.tickers: tm.setdefault(t, []).append(it)
        result = {}
        for t, its in tm.items():
            result[t] = aggregate_smart(its)
        return result

_pipeline: Optional[SentimentPipeline] = None
def get_sentiment_pipeline(watchlist=None):
    global _pipeline
    from config import DEFAULT_WATCHLIST
    if _pipeline is None: _pipeline = SentimentPipeline(watchlist or DEFAULT_WATCHLIST)
    return _pipeline
