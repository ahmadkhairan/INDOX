"""Indonesian sentiment scoring using pre-trained IndoBERT model.

Replaces keyword-based scoring with deep learning model.
Falls back gracefully if transformers/torch not installed.

Models (all free, from HuggingFace):
- mdhugol/indonesia-bert-sentiment-classification (recommended, ~500MB)
- indolem/indobert-base-uncased (general purpose, fine-tune yourself)

Usage:
    scorer = IndonesianSentiment()
    score, label = scorer.score("Saham BBCA naik 5% setelah dividen besar")
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from utils.logger import get_logger

log = get_logger("sentiment.indobert")

_MODEL = None
_TOKENIZER = None
_PIPELINE = None
_AVAILABLE = False

try:
    from transformers import AutoTokenizer, AutoModelForSequenceClassification, pipeline
    _AVAILABLE = True
except ImportError:
    log.warning("transformers tidak tersedia — IndoBERT sentiment disabled, pakai keyword fallback")


# Indonesian sentiment lexicon (VADER-style, lightweight, always-available fallback)
_POS_LEXICON = {
    "naik": 0.3, "turun": -0.3, "laba": 0.5, "rugi": -0.5,
    "dividen": 0.4, "buyback": 0.6, "ekspansi": 0.4, "akuisisi": 0.3,
    "pertumbuhan": 0.4, "positif": 0.4, "negatif": -0.4, "kuat": 0.3,
    "lemah": -0.3, "bullish": 0.6, "bearish": -0.6, "rebound": 0.4,
    "krisis": -0.5, "default": -0.7, "pailit": -0.9, "delisting": -0.9,
    "sanksi": -0.6, "denda": -0.5, "pelanggaran": -0.5, "manipulasi": -0.7,
    "meroket": 0.7, "anjlok": -0.7, "cuan": 0.6, "untung": 0.5,
    "loss": -0.5, "profit": 0.5, "rekor": 0.4, "terendah": -0.4,
    "koreksi": -0.3, "rebound": 0.4, "all time high": 0.6,
    "all time low": -0.6, "ath": 0.5, "atl": -0.5,
    "alhi": 0.5, "gold": 0.3, "melesat": 0.5, "melemah": -0.3,
    "menguat": 0.3, "melemah": -0.3, "tertekan": -0.3,
    "overvalued": -0.3, "undervalued": 0.3, "konsolidasi": 0.1,
    "akumulasi": 0.4, "distribusi": -0.4, "rekomendasi beli": 0.7,
    "rekomendasi jual": -0.7, "rekomendasi tahan": 0.0,
    "upgrade": 0.4, "downgrade": -0.4, "target naik": 0.5,
    "target turun": -0.5, "prospek cerah": 0.6, "prospek suram": -0.6,
    "performa baik": 0.4, "performa buruk": -0.4,
    "laba bersih naik": 0.7, "laba bersih turun": -0.7,
    "revenue tumbuh": 0.5, "revenue turun": -0.5,
    "kontrak baru": 0.5, "kehilangan kontrak": -0.5,
    "ekspor naik": 0.4, "ekspor turun": -0.4,
    "penjualan naik": 0.4, "penjualan turun": -0.4,
}

# Negation words flip the polarity of next word
_NEGATION = {"tidak", "tak", "bukan", "belum", "jangan", "tanpa"}


def _lexicon_score(text: str) -> Tuple[float, str]:
    """Lightweight Indonesian sentiment using lexicon (no model needed)."""
    text_lower = text.lower()
    words = text_lower.split()
    score = 0.0
    matches = 0
    i = 0
    while i < len(words):
        word = words[i]
        # Check for multi-word phrases first
        matched = False
        for n in (3, 2, 1):
            if i + n <= len(words):
                phrase = " ".join(words[i:i + n])
                if phrase in _POS_LEXICON:
                    val = _POS_LEXICON[phrase]
                    # Check negation in previous 2 words
                    negated = any(w in _NEGATION for w in words[max(0, i - 2):i])
                    if negated:
                        val = -val
                    score += val
                    matches += 1
                    i += n
                    matched = True
                    break
        if not matched:
            i += 1
    # Normalize
    if matches == 0:
        return 0.0, "NEUTRAL"
    score = max(-1.0, min(1.0, score / max(matches * 0.6, 1.0)))
    if score > 0.4:
        label = "VERY_POS"
    elif score > 0.1:
        label = "POS"
    elif score < -0.4:
        label = "VERY_NEG"
    elif score < -0.1:
        label = "NEG"
    else:
        label = "NEUTRAL"
    return round(score, 3), label


def _load_indobert():
    """Lazy load IndoBERT model (downloads ~500MB on first run)."""
    global _MODEL, _TOKENIZER, _PIPELINE
    if _PIPELINE is not None:
        return _PIPELINE
    if not _AVAILABLE:
        return None

    model_name = os.getenv("INDOBERT_MODEL", "mdhugol/indonesia-bert-sentiment-classification")
    try:
        log.info(f"Loading IndoBERT: {model_name} (first run may take 30-60s)...")
        _PIPELINE = pipeline(
            "sentiment-analysis",
            model=model_name,
            tokenizer=model_name,
            device=-1,  # CPU only; set to 0 if GPU available
        )
        log.info("IndoBERT ready")
        return _PIPELINE
    except Exception as exc:
        log.warning(f"Failed to load IndoBERT: {exc}, falling back to lexicon")
        return None


class IndonesianSentiment:
    """Drop-in replacement for keyword-based sentiment.

    Priority:
    1. IndoBERT (if available) — most accurate
    2. Lexicon (always available) — fast, decent accuracy
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.use_model = os.getenv("USE_INDOBERT", "auto").lower()
        self._model_loaded = False
        self._initialized = True

    def _ensure_model(self):
        if self._model_loaded:
            return
        if self.use_model == "off":
            return
        if self.use_model in ("auto", "on"):
            pipe = _load_indobert()
            self._model_loaded = pipe is not None

    def score(self, text: str) -> Tuple[float, str]:
        """Score text sentiment. Returns (score -1..1, label)."""
        if not text or not text.strip():
            return 0.0, "NEUTRAL"

        # Truncate to 512 tokens (BERT limit)
        text_trunc = text[:1000]

        # Try IndoBERT
        if self.use_model in ("auto", "on"):
            self._ensure_model()
            if _PIPELINE is not None:
                try:
                    result = _PIPELINE(text_trunc[:512])[0]
                    label_raw = result["label"].upper()
                    conf = float(result["score"])
                    if "POS" in label_raw:
                        return round(conf, 3), "VERY_POS" if conf > 0.7 else "POS"
                    elif "NEG" in label_raw:
                        return round(-conf, 3), "VERY_NEG" if conf > 0.7 else "NEG"
                    else:
                        return 0.0, "NEUTRAL"
                except Exception as exc:
                    log.debug(f"IndoBERT inference failed: {exc}")

        # Fallback to lexicon
        return _lexicon_score(text_trunc)

    def score_batch(self, texts: list[str]) -> list[Tuple[float, str]]:
        """Batch scoring — faster for many items."""
        results = []
        if self.use_model in ("auto", "on"):
            self._ensure_model()
            if _PIPELINE is not None:
                try:
                    truncated = [t[:512] for t in texts]
                    outputs = _PIPELINE(truncated)
                    for out in outputs:
                        label_raw = out["label"].upper()
                        conf = float(out["score"])
                        if "POS" in label_raw:
                            results.append((round(conf, 3), "VERY_POS" if conf > 0.7 else "POS"))
                        elif "NEG" in label_raw:
                            results.append((round(-conf, 3), "VERY_NEG" if conf > 0.7 else "NEG"))
                        else:
                            results.append((0.0, "NEUTRAL"))
                    return results
                except Exception as exc:
                    log.debug(f"IndoBERT batch failed: {exc}")

        # Fallback
        return [_lexicon_score(t) for t in texts]


# Singleton
_scorer: Optional[IndonesianSentiment] = None


def get_sentiment_scorer() -> IndonesianSentiment:
    global _scorer
    if _scorer is None:
        _scorer = IndonesianSentiment()
    return _scorer
