from __future__ import annotations

from utils.yf_guard import YFinanceUnavailable


def user_error_message(exc: Exception, fallback: str = "Terjadi error internal. Coba lagi sebentar lagi.") -> str:
    if isinstance(exc, YFinanceUnavailable):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "Operasi timeout. Coba lagi saat koneksi/provider lebih stabil."

    text = str(exc).strip()
    if not text:
        return fallback

    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "Request timeout ke provider data. Coba lagi beberapa saat."
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return "Provider sedang rate limit. Tunggu sebentar lalu coba lagi."
    if "not enough data" in lowered or "data tidak cukup" in lowered:
        return text

    return text[:280]
