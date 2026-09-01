from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from cogs.analisis_cog import parse_analysis_query
from core.ai_engine import SYSTEM_PROMPT_ID, SYSTEM_PROMPT_EN, analyze_ticker_v4
from api.app import AnalyzeReq


class TestAnalisisLanguageAndFormat(unittest.TestCase):
    def test_parse_analysis_query_default_indonesian(self):
        q, lang = parse_analysis_query("")
        self.assertEqual(lang, "id")
        self.assertEqual(q, "")

        q, lang = parse_analysis_query("bagaimana prospek laporan keuangan?")
        self.assertEqual(lang, "id")
        self.assertEqual(q, "bagaimana prospek laporan keuangan?")

    def test_parse_analysis_query_prefix_tokens(self):
        # English tokens
        for token in ["en", "eng", "english", "inggris", "lang=en"]:
            q, lang = parse_analysis_query(f"{token} what is target price?")
            self.assertEqual(lang, "en")
            self.assertEqual(q, "what is target price?")

            # Just the token alone
            q, lang = parse_analysis_query(token)
            self.assertEqual(lang, "en")
            self.assertEqual(q, "")

        # Indonesian tokens
        for token in ["id", "indo", "indonesia", "lang=id"]:
            q, lang = parse_analysis_query(f"{token} target berapa?")
            self.assertEqual(lang, "id")
            self.assertEqual(q, "target berapa?")

            q, lang = parse_analysis_query(token)
            self.assertEqual(lang, "id")
            self.assertEqual(q, "")

    def test_parse_analysis_query_heuristics(self):
        # Explicit phrasing
        q, lang = parse_analysis_query("analisis ini tolong in english ya")
        self.assertEqual(lang, "en")

        q, lang = parse_analysis_query("tolong dalam bahasa inggris")
        self.assertEqual(lang, "en")

        # Common English starters
        q, lang = parse_analysis_query("what is the expected return?")
        self.assertEqual(lang, "en")

        q, lang = parse_analysis_query("how strong is the momentum?")
        self.assertEqual(lang, "en")

        q, lang = parse_analysis_query("should I enter now?")
        self.assertEqual(lang, "en")

    def test_system_prompts_forbid_markdown_pipe_tables(self):
        self.assertIn("DILARANG KERAS menggunakan Markdown pipe table", SYSTEM_PROMPT_ID)
        self.assertIn("STRICTLY PROHIBITED: Markdown pipe tables", SYSTEM_PROMPT_EN)

    def test_analyze_ticker_v4_constructs_english_prompt(self):
        sample_data = {
            "ticker": "BBCA",
            "company_name": "Bank Central Asia Tbk",
            "sector": "Financials",
            "market": {"price": 10000, "change_pct": 1.5, "vol_ratio": 1.4, "market_cap": "1200T"},
            "fundamental": {"per": 22.0, "pbv": 4.5, "roe": 21.0, "der": 4.5, "dividend_yield": 2.5},
            "technical": {"trend": "BULLISH", "rsi": 60, "adx": 25, "atr_sl": 9700, "atr_tp1": 10500, "atr_tp2": 11000, "atr_rr": 2.5},
            "score": {"total": 85.0, "grade": "A", "confidence": "High", "win_probability": 72.0, "preferred_entry_mode": "MOMENTUM"},
        }
        news = [{"source": "CNBC", "title": "BBCA cetak laba rekor"}]

        captured_system = []
        captured_messages = []

        async def _mock_call_async(messages, system=None, max_tokens=None):
            captured_system.append(system)
            captured_messages.append(messages)
            return "ANALYSIS RESULT"

        with patch("core.ai_engine._call_async", side_effect=_mock_call_async):
            # Test English
            asyncio.run(analyze_ticker_v4(sample_data, news, user_question="target?", language="en"))
            self.assertEqual(captured_system[0], SYSTEM_PROMPT_EN)
            prompt_en = captured_messages[0][0]["content"]
            self.assertIn("Please provide an in-depth stock analysis in fluent English", prompt_en)
            self.assertIn("DO NOT use Markdown pipe tables", prompt_en)
            self.assertIn("Market & Regime Context", prompt_en)
            self.assertIn("Detailed Technical Analysis", prompt_en)
            self.assertIn("Fundamental Valuation & Health", prompt_en)

            # Test Indonesian
            captured_system.clear()
            captured_messages.clear()
            asyncio.run(analyze_ticker_v4(sample_data, news, user_question="target?", language="id"))
            self.assertEqual(captured_system[0], SYSTEM_PROMPT_ID)
            prompt_id = captured_messages[0][0]["content"]
            self.assertIn("Berikan analisis saham mendalam dalam Bahasa Indonesia", prompt_id)
            self.assertIn("DILARANG KERAS menggunakan Markdown pipe table", prompt_id)
            self.assertIn("Ringkasan Kondisi", prompt_id)
            self.assertIn("Analisis Teknikal Detail", prompt_id)
            self.assertIn("Analisis Fundamental & Valuasi", prompt_id)

    def test_api_analyze_req_supports_language(self):
        req_default = AnalyzeReq(ticker="BBCA")
        self.assertEqual(req_default.language, "id")

        req_en = AnalyzeReq(ticker="BBCA", language="en")
        self.assertEqual(req_en.language, "en")

    def test_build_analysis_embed_creates_clean_fields(self):
        from cogs.analisis_cog import build_analysis_embed
        sample_data = {
            "ticker": "BBCA",
            "company_name": "Bank Central Asia Tbk",
            "sector": "Financials",
            "market": {"price": 10000, "change_pct": 1.5, "vol_ratio": 1.4},
            "score": {"total": 85.0, "grade": "A", "preferred_entry_mode": "MOMENTUM"},
            "sector_context": {"label": "Banking"},
        }
        ai_response = """1️⃣ **Ringkasan Kondisi (Macro + Sektor + Flow + Sentimen)**
• **Macro & Regime**: IHSG di atas MA50.
• **Rotasi Sektor**: Sektor perbankan defensif.

2️⃣ **Analisis Teknikal Detail**
• **Trend & Struktur**: Bullish di atas MA20.
• **Momentum**: RSI 60 sehat.

5️⃣ **Trade Setup & Manajemen Risiko**
• **Entry Zone**: `Rp 9.900 – Rp 10.050`
• **Stop Loss (SL)**: `Rp 9.650`
• **Take Profit 1 (TP1)**: `Rp 10.500`

⚠️ Disclaimer: analisis edukasi."""

        embed = build_analysis_embed("BBCA", sample_data, ai_response, language="id")
        self.assertEqual(embed.title, "📊 BBCA — Bank Central Asia Tbk")
        self.assertIn("Skor AI", embed.description)
        self.assertGreaterEqual(len(embed.fields), 3)
        self.assertTrue(any("Ringkasan Kondisi" in f.name for f in embed.fields))
        self.assertTrue(any("Trade Setup" in f.name for f in embed.fields))

    def test_build_analysis_embed_color_indicator(self):
        from cogs.analisis_cog import build_analysis_embed
        import discord
        # Bullish
        embed_green = build_analysis_embed("BBCA", {"score": {"total": 80.0}}, "1️⃣ **A**\nB\n\n2️⃣ **C**\nD", "id")
        self.assertEqual(embed_green.color, discord.Color.green())

        # Neutral
        embed_gold = build_analysis_embed("BBCA", {"score": {"total": 55.0}}, "1️⃣ **A**\nB\n\n2️⃣ **C**\nD", "id")
        self.assertEqual(embed_gold.color, discord.Color.gold())

        # Bearish
        embed_red = build_analysis_embed("BBCA", {"score": {"total": 30.0}}, "1️⃣ **A**\nB\n\n2️⃣ **C**\nD", "id")
        self.assertEqual(embed_red.color, discord.Color.red())


if __name__ == "__main__":
    unittest.main()
