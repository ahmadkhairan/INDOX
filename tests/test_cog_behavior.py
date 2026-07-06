from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from cogs.backtest_cog import BacktestCog
from cogs.picks_cog import PicksCog
from cogs.risk_cog import RiskCog


class _FakeFollowup:
    def __init__(self):
        self.messages: list[str] = []

    async def send(self, content, **kwargs):
        self.messages.append(content)


class _FakeInteraction:
    def __init__(self):
        self.edits: list[str] = []
        self.followup = _FakeFollowup()

    async def edit_original_response(self, *, content=None, embed=None):
        self.edits.append(content if content is not None else f"<embed:{getattr(embed, 'title', '')}>")


class CogBehaviorTests(unittest.TestCase):
    def test_send_slash_long_splits_into_followups(self):
        cog = BacktestCog(bot=None)
        interaction = _FakeInteraction()
        content = ("A" * 1800) + "\n" + ("B" * 200)

        asyncio.run(cog._send_slash_long(interaction, content))

        self.assertEqual(len(interaction.edits), 1)
        self.assertEqual(len(interaction.followup.messages), 1)
        self.assertTrue(interaction.edits[0].startswith("A"))
        self.assertTrue(interaction.followup.messages[0].startswith("B"))

    def test_risk_parse_ticker_text_handles_spaces_and_commas(self):
        cog = RiskCog(bot=None)
        tickers = cog._parse_ticker_text(" bbca, bbri   tlkm ", max_items=5, min_items=2)
        self.assertEqual(tickers, ["BBCA", "BBRI", "TLKM"])

    def test_risk_parse_ticker_text_enforces_minimum(self):
        cog = RiskCog(bot=None)
        with self.assertRaises(ValueError):
            cog._parse_ticker_text("BBCA", max_items=5, min_items=2)

    def test_picks_notify_progress_supports_keyword_callback(self):
        events: list[str] = []

        async def _progress(*, content=None):
            events.append(content)

        cog = PicksCog.__new__(PicksCog)
        asyncio.run(cog._notify_progress(_progress, "stage-1"))

        self.assertEqual(events, ["stage-1"])

    def test_picks_notify_progress_supports_positional_callback(self):
        events: list[str] = []

        async def _progress(content):
            events.append(content)

        cog = PicksCog.__new__(PicksCog)
        asyncio.run(cog._notify_progress(_progress, "stage-2"))

        self.assertEqual(events, ["stage-2"])

    def test_build_walkforward_embed_includes_optimized_param_range(self):
        cog = BacktestCog(bot=None)
        result = SimpleNamespace(
            is_robust=True,
            robustness=0.88,
            oos_agg=SimpleNamespace(total_trades=8, win_rate=62.5, pf=1.7, total_return=14.2, max_dd=-6.1, sharpe=1.3),
            is_agg=SimpleNamespace(total_trades=10, win_rate=65.0, pf=1.9, total_return=18.3, max_dd=-5.0, sharpe=1.5),
            oos_periods=[
                SimpleNamespace(params={"sl_mult": 1.5, "tp1_mult": 3.0, "hold_days": 6}),
                SimpleNamespace(params={"sl_mult": 2.0, "tp1_mult": 3.5, "hold_days": 8}),
            ],
            recommendations=["ok"],
        )

        embed = cog._build_walkforward_embed("BBCA", result)

        self.assertEqual(embed.title, "📊 Walk-Forward — BBCA")
        config_field = next(field for field in embed.fields if field.name == "📐 Config")
        self.assertIn("Optimized Params", config_field.value)
        self.assertIn("SL 1.5-2", config_field.value)


if __name__ == "__main__":
    unittest.main()
