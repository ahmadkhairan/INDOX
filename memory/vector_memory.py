from __future__ import annotations
import asyncio, os, uuid
from datetime import datetime
from typing import Any, Optional
from utils.logger import get_logger
from utils.json_store import read_json, write_json
from config import FEATURE_VECTOR_MEMORY
log = get_logger("memory.vector")

_CHROMA = False; _EMBED = False; _embedder: Any = None; _SentenceTransformer: Any = None
try:
    import chromadb
    from chromadb.config import Settings
    _CHROMA = True
except ImportError: log.warning("chromadb tidak ada")
try:
    from sentence_transformers import SentenceTransformer
    _SentenceTransformer = SentenceTransformer
    _EMBED = True
except ImportError: log.warning("sentence-transformers tidak ada")

class VectorMemory:
    COL = "idx_analysis"; JSON_PATH = "memory/store.json"
    def __init__(self):
        self._client: Any = None; self._col: Any = None
        self._json: list[dict] = []
        self._use_vec = _CHROMA and _EMBED and FEATURE_VECTOR_MEMORY

    async def initialize(self):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._init)

    def _init(self):
        if not self._use_vec:
            self._load_json(); return
        try:
            self._ensure_embedder()
            self._client = chromadb.PersistentClient(
                path=os.getenv("CHROMA_DIR","./chroma_db"),
                settings=Settings(anonymized_telemetry=False))
            self._col = self._client.get_or_create_collection(
                self.COL, metadata={"hnsw:space":"cosine"})
            log.info("ChromaDB ready")
        except Exception as exc:
            log.warning(f"ChromaDB: {exc}"); self._use_vec = False; self._load_json()

    def _ensure_embedder(self):
        global _embedder
        if _embedder is not None:
            return
        if not _SentenceTransformer:
            raise RuntimeError("sentence-transformers tidak tersedia")
        model_name = os.getenv("ST_MODEL", "all-MiniLM-L6-v2")
        _embedder = _SentenceTransformer(model_name)
        log.info(f"SentenceTransformer ready: {model_name}")

    def _load_json(self):
        os.makedirs("memory", exist_ok=True)
        data = read_json(self.JSON_PATH, [])
        self._json = data if isinstance(data, list) else []

    def _save_json(self):
        try:
            write_json(self.JSON_PATH, self._json[-2000:], ensure_ascii=False, indent=2)
        except Exception as exc: log.warning(f"JSON save: {exc}")

    async def add_analysis(self, ticker, text, score, signal, extra=None):
        content = f"[{ticker}] Score:{score:.1f} Signal:{signal} | {text[:400]}"
        meta = {"ticker":ticker,"score":score,"signal":signal,"ts":datetime.now().isoformat(),**(extra or {})}
        await self._store(str(uuid.uuid4()), content, meta)

    async def get_rag_context(self, ticker, query, k=4):
        results = await self._search(f"{ticker} {query}", ticker, k)
        if not results: return ""
        lines = ["═══ RIWAYAT ANALISIS (RAG) ═══"]
        for r in results:
            m = r.get("metadata",{}); ts = m.get("ts","")[:10]
            lines.append(f"[{ts}] Score:{m.get('score',0):.1f} Signal:{m.get('signal','?')} | {r.get('content','')[:150]}")
        return "\n".join(lines)

    async def _search(self, query, ticker_filter, k):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query, ticker_filter, k)

    def _search_sync(self, query, ticker_filter, k):
        if self._use_vec and self._col:
            try:
                emb = _embedder.encode([query])[0].tolist()
                where = {"ticker":ticker_filter} if ticker_filter else None
                res = self._col.query(query_embeddings=[emb], n_results=k, where=where,
                                      include=["metadatas","documents","distances"])
                return [{"content":d,"metadata":m,"distance":dist}
                        for d,m,dist in zip(res["documents"][0],res["metadatas"][0],res["distances"][0])]
            except Exception as exc: log.warning(f"Vector search: {exc}")
        q = query.lower(); out = []
        for e in reversed(self._json):
            if ticker_filter and e.get("metadata",{}).get("ticker") != ticker_filter: continue
            if q[:20] in e.get("content","").lower(): out.append(e)
            if len(out) >= k: break
        return out

    async def _store(self, uid, content, meta):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._store_sync, uid, content, meta)

    def _store_sync(self, uid, content, meta):
        if self._use_vec and self._col:
            try:
                emb = _embedder.encode([content])[0].tolist()
                self._col.add(ids=[uid], embeddings=[emb], documents=[content], metadatas=[meta])
                return
            except Exception as exc: log.warning(f"Vector store: {exc}")
        self._json.append({"id":uid,"content":content,"metadata":meta})
        self._save_json()

_vm: Optional[VectorMemory] = None
async def get_vector_memory() -> VectorMemory:
    global _vm
    if _vm is None: _vm = VectorMemory(); await _vm.initialize()
    return _vm
