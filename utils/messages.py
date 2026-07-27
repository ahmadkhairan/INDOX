from __future__ import annotations

"""
utils/messages.py — Centralized Discord message templates.

All user-facing strings live here.  Cogs import from this module instead
of inlining ad-hoc text.

Usage
-----
    from utils.messages import Msg

    await ctx.reply(Msg.loading("BBCA"))
    await ctx.reply(Msg.error("Ticker tidak ditemukan"))
    await msg.edit(content=Msg.progress_scan(30))
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class _Msg:
    # ── Generic states ──────────────────────────────────

    def loading(self, ticker: str = "") -> str:
        suffix = f" **{ticker}**" if ticker else ""
        return f"Sedang memproses{suffix}..."

    def done(self, ticker: str = "") -> str:
        suffix = f" **{ticker}**" if ticker else ""
        return f"Selesai{suffix}."

    def error(self, detail: str = "") -> str:
        body = f": {detail}" if detail else ""
        return f"Terjadi kesalahan{body}"

    def not_found(self, ticker: str) -> str:
        return (
            f"**{ticker}**: data tidak ditemukan. "
            "Pastikan kode saham benar (contoh: BBCA, TLKM, ADRO)."
        )

    def invalid_ticker(self, ticker: str = "") -> str:
        example = f" ({ticker!r})" if ticker else ""
        return f"Ticker{example} tidak valid. Gunakan 4 huruf, contoh: `BBCA`."

    def cooldown(self, retry_after: str) -> str:
        return f"Command sedang cooldown. Coba lagi dalam `{retry_after}`."

    def missing_arg(self) -> str:
        return "Parameter kurang. Gunakan `!bantuan` untuk melihat format."

    # ── Analysis ────────────────────────────────────────

    def analysis_start(self, ticker: str) -> str:
        return (
            f"Menganalisis **{ticker}**... "
            "(data fetcher + AI, estimasi 15–30 detik)"
        )

    # ── Picks ────────────────────────────────────────────

    def picks_scanning(self) -> str:
        return (
            "Scanning **seluruh IDX** "
            "(market cap + volume + foreign flow filter)… "
            "(estimasi 30–60 detik)"
        )

    def picks_regime(self) -> str:
        return "Menilai regime pasar..."

    def picks_commodities(self) -> str:
        return "Regime aman. Mengambil konteks komoditas..."

    def picks_idx_scan(self) -> str:
        return "Scanning seluruh IDX liquid... (tahap paling lama)"

    def picks_news(self) -> str:
        return "Kandidat terkumpul. Mengambil berita untuk shortlist..."

    def picks_ai(self) -> str:
        return "Shortlist siap. Menyusun daily picks dengan AI..."

    def picks_bear_regime(self, last: float, ma200: float, ma_label: str = "MA200") -> str:
        return (
            "**BEAR REGIME AKTIF — TIDAK ADA PICKS HARI INI**\n\n"
            f"IHSG berada di bawah {ma_label} ({ma200:,.0f}) selama ≥3 hari berturut-turut.\n\n"
            "**Semua entry baru DILARANG.** Strategi saat ini:\n"
            "• Jangan buka posisi baru\n"
            "• Ketatkan trailing stop posisi aktif\n"
            f"• Tunggu IHSG tutup DI ATAS {ma_label} minimal 2 hari berturut-turut sebelum re-entry\n\n"
            f"_IHSG terakhir: {last:,.2f} | {ma_label}: {ma200:,.0f}_\n"
            "Catatan: bukan saran investasi; ini adalah analisis edukasi."
        )

    # ── Backtest ─────────────────────────────────────────

    def backtest_start(self, ticker: str, months: int) -> str:
        return f"Backtesting **{ticker}** ({months} bulan) + Monte Carlo..."

    def backtest_fetching(self, ticker: str) -> str:
        return f"Mengambil data historis dan menjalankan backtest **{ticker}**..."

    def backtest_ai(self, ticker: str) -> str:
        return f"Backtest selesai. Menyusun evaluasi AI untuk **{ticker}**..."

    def backtest_no_data(self, ticker: str) -> str:
        return f"Data tidak cukup untuk backtest **{ticker}**."

    def walkforward_start(self, ticker: str, years: int) -> str:
        return f"Walk-forward **{ticker}** ({years} tahun)... (sekitar 40 detik)"

    def walkforward_running(self, ticker: str) -> str:
        return f"Menjalankan walk-forward window demi window untuk **{ticker}**..."

    def montecarlo_start(self, ticker: str, months: int) -> str:
        return f"Monte Carlo **{ticker}** ({months} bulan data)..."

    def montecarlo_fetching(self, ticker: str) -> str:
        return f"Mengambil data historis **{ticker}** untuk simulasi Monte Carlo..."

    def montecarlo_running(self, ticker: str, n_sim: int) -> str:
        return f"Menjalankan {n_sim:,} simulasi Monte Carlo untuk **{ticker}**..."

    def montecarlo_no_trades(self, ticker: str, months: int) -> str:
        return (
            f"Tidak ada trade dalam {months} bulan untuk **{ticker}**.\n"
            "Coba perpanjang periode."
        )

    # ── Risk ─────────────────────────────────────────────

    def risk_var_fetching(self, ticker: str) -> str:
        return f"Mengambil data historis **{ticker}** (1 tahun)..."

    def risk_var_calculating(self, ticker: str, method: str) -> str:
        return f"Menghitung VaR **{ticker}** dengan metode `{method}`..."

    def risk_stress_running(self, tickers_str: str) -> str:
        return f"Menjalankan 5 skenario stress test untuk {tickers_str}..."

    def risk_corr_fetching(self, n: int) -> str:
        return f"Mengambil return historis untuk {n} ticker..."

    def risk_corr_calculating(self) -> str:
        return "Menghitung matriks korelasi dan skor diversifikasi..."

    def risk_optimize_fetching(self, n: int) -> str:
        return f"Mengambil data historis 2 tahun untuk {n} ticker..."

    def risk_optimize_running(self, method: str) -> str:
        return f"Menjalankan optimizer `{method}`..."

    def risk_no_data(self, ticker: str) -> str:
        return f"Data tidak tersedia untuk **{ticker}**."

    # ── Alert ────────────────────────────────────────────

    def alert_set(self, ticker: str, condition: str, price: float) -> str:
        return (
            f"Alert dipasang: **{ticker}** {condition} Rp{price:,.0f}\n"
            "Kamu akan mendapat DM saat kondisi ini terpenuhi.\n"
            "_(Pastikan DM dari server ini tidak diblokir)_"
        )

    def alert_duplicate(self, ticker: str, condition: str, price: float) -> str:
        return f"Alert **{ticker}** {condition} Rp{price:,.0f} sudah ada."

    def alert_removed(self, ticker: str, n: int) -> str:
        return f"{n} alert untuk **{ticker}** dihapus."

    def alert_not_found(self, ticker: str) -> str:
        return f"Tidak ada alert aktif untuk **{ticker}**."

    def alert_empty(self) -> str:
        return "Kamu tidak punya alert aktif."

    # ── Portfolio ─────────────────────────────────────────

    def portfolio_reading(self) -> str:
        return "Membaca CSV dan menganalisis portfolio... (20–40 detik)"

    # ── Market ───────────────────────────────────────────

    def market_loading(self) -> str:
        return "Mengambil data pasar dan melakukan regime check..."

    # ── Groq / AI ────────────────────────────────────────

    def ai_unavailable(self) -> str:
        return (
            "Layanan AI sedang tidak tersedia. "
            "Coba lagi beberapa saat."
        )

    # ── Generic fallback ─────────────────────────────────

    def internal_error(self) -> str:
        return "Terjadi kesalahan internal. Coba lagi sebentar lagi."

    def provider_unavailable(self) -> str:
        return "Provider data pasar sementara tidak tersedia. Coba lagi sebentar."


# Singleton — import and use directly: `from utils.messages import Msg`
Msg = _Msg()
