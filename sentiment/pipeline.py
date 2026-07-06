from __future__ import annotations
import asyncio, hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import aiohttp
from config import NEWS_RSS_FEEDS, GROQ_API_KEY, GROQ_MODEL_FAST, FEATURE_SENTIMENT_PIPELINE
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

def _score(title, summary):
    text = (title+" "+summary).lower(); s = 0.0
    for kw, v in POS_KW.items():
        if kw.lower() in text: s += v
    for kw, v in NEG_KW.items():
        if kw.lower() in text: s += v
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
        try:
            async with sess.get(
                "https://www.idx.co.id/primary/NewsAnnouncement/GetAllAnnouncement",
                params={"IndexFrom":1,"PageSize":20}, timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                if r.status != 200: return []
                data = await r.json(content_type=None)
            return [{"title":a.get("Headline",a.get("Title","")),"summary":a.get("Headline",""),
                     "url":a.get("AttachmentFile",""),"published":a.get("Date",""),
                     "source":"IDX Official","_ticker":a.get("Code","")}
                    for a in (data.get("data") or data.get("Data") or [])]
        except Exception as exc:
            log.warning(f"IDX fetch: {exc}"); return []

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
        if not GROQ_API_KEY or not items: return []
        batch = items[:12]
        titles = "\n".join(f"{i+1}. {it.get('title','')}" for i, it in enumerate(batch))
        prompt = f"Scoring sentimen berita saham IDX. Skala -1.0 sampai +1.0.\nJawab hanya angka per baris, tanpa teks lain.\n\nBerita:\n{titles}"
        try:
            from groq import Groq
            c = Groq(api_key=GROQ_API_KEY)
            resp = c.chat.completions.create(
                model=GROQ_MODEL_FAST,
                messages=[{"role":"user","content":prompt}],
                temperature=0.1, max_tokens=80,
            )
            out = []
            for line in resp.choices[0].message.content.strip().split("\n"):
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
        tm: dict[str, list[SentItem]] = {}
        for it in items:
            for t in it.tickers: tm.setdefault(t, []).append(it)
        result = {}
        wm = {"HIGH":3.0,"MEDIUM":2.0,"LOW":1.0}
        for t, its in tm.items():
            tw = sum(wm[i.impact] for i in its)
            comp = max(-1.0, min(1.0, sum(i.score*wm[i.impact] for i in its)/(tw+1e-8)))
            lbl = "VERY_POS" if comp>0.5 else "POS" if comp>0.1 else "VERY_NEG" if comp<-0.5 else "NEG" if comp<-0.1 else "NEUTRAL"
            result[t] = TickerSentiment(
                ticker=t, score=round(comp,3), label=lbl,
                pos_count=sum(1 for i in its if i.score>0.1),
                neg_count=sum(1 for i in its if i.score<-0.1),
                neu_count=sum(1 for i in its if -0.1<=i.score<=0.1),
                has_buyback=any(i.category=="buyback" for i in its),
                has_dividend=any(i.category=="dividend" for i in its),
                has_earn_beat=any(i.category=="earnings" and i.score>0.5 for i in its),
                has_reg_risk=any(i.category=="regulatory" and i.score<0 for i in its),
                items=sorted(its, key=lambda x:x.published, reverse=True)[:5],
                last_updated=datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
        return result

_pipeline: Optional[SentimentPipeline] = None
def get_sentiment_pipeline(watchlist=None):
    global _pipeline
    from config import DEFAULT_WATCHLIST
    if _pipeline is None: _pipeline = SentimentPipeline(watchlist or DEFAULT_WATCHLIST)
    return _pipeline
