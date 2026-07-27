# cogs/picks_cog.py
# Daily picks from the full IDX liquid universe.
# Includes: IHSG regime guard, coal context, quant shortlist, AI narration,
# picks tracker persistence, and progress callbacks.

import asyncio
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DAILY_CHANNEL_ID, DAILY_HOUR_UTC, DAILY_MINUTE, DEFAULT_WATCHLIST
from data.alert_store_sqlite import get_picks_cache_date, set_picks_cache_date
from data.picks_tracker import get_picks_tracker
from utils.ai_engine import generate_daily_picks
from utils.command_limits import PICKS_COOLDOWN
from utils.helpers import send_long, split_msg
from utils.market_regime import get_coal_price, get_ihsg_regime
from utils.messages import Msg
from utils.news_utils import get_berita
from utils.picks_engine import build_quant_shortlist, extract_selected_tickers
from utils.scan_utils import scan_all_liquid_idx, scan_watchlist


class PicksCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._picks_cache: str = ""
        self.daily_task.start()

    def cog_unload(self):
        self.daily_task.cancel()

    # ── Scheduler ─────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def daily_task(self):
        now = datetime.utcnow()
        if now.hour == DAILY_HOUR_UTC and now.minute == DAILY_MINUTE:
            today = now.strftime("%Y-%m-%d")
            if get_picks_cache_date() == today:
                return
            response = await self._fetch_picks(force=True)
            if DAILY_CHANNEL_ID:
                channel = self.bot.get_channel(DAILY_CHANNEL_ID)
                if channel:
                    await channel.send("@here **Daily Picks IDX tersedia**")
                    await send_long(channel, response)
                    set_picks_cache_date(today)

    @daily_task.before_loop
    async def before_daily(self):
        await self.bot.wait_until_ready()

    # ── Prefix command ────────────────────────────────────

    @commands.command(name="picks", aliases=["p", "daily"])
    @commands.cooldown(*PICKS_COOLDOWN, commands.BucketType.user)
    async def cmd_picks(self, ctx):
        """!picks — daily top 3 swing picks dari seluruh IDX liquid"""
        msg = await ctx.reply(Msg.picks_scanning())
        response = await self._fetch_picks(force=True, progress=msg.edit)
        await msg.delete()
        await send_long(ctx.channel, response, reply_to=ctx.message)

    # ── Slash command ─────────────────────────────────────

    @app_commands.command(name="picks", description="Daily top 3 swing picks dari seluruh IDX liquid")
    @app_commands.checks.cooldown(*PICKS_COOLDOWN)
    async def slash_picks(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        async def _progress(content: str):
            await interaction.edit_original_response(content=content)

        response = await self._fetch_picks(force=True, progress=_progress)
        chunks = split_msg(response)
        await interaction.edit_original_response(content=chunks[0])
        for c in chunks[1:]:
            await interaction.followup.send(c)
            await asyncio.sleep(0.3)

    @commands.command(name="pickstats", aliases=["picksperf", "pickrate"])
    @commands.cooldown(*PICKS_COOLDOWN, commands.BucketType.user)
    async def cmd_pickstats(self, ctx):
        """!pickstats — hit-rate real 30 hari picks"""
        msg = await ctx.reply("Menghitung performa picks 30 hari terakhir...")
        text = await self._build_pick_stats()
        await msg.delete()
        await ctx.reply(text)

    # ── Core ──────────────────────────────────────────────

    async def _fetch_picks(self, force: bool = False, progress=None) -> str:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if not force and get_picks_cache_date() == today and self._picks_cache:
            return self._picks_cache

        loop = asyncio.get_event_loop()
        await self._notify_progress(progress, Msg.picks_regime())

        regime = await loop.run_in_executor(None, _get_ihsg_regime_safe)
        r = regime.get("regime", "UNKNOWN")

        if r == "BEAR":
            msg = Msg.picks_bear_regime(
                last=regime.get("ihsg_last", 0),
                ma200=regime.get("ihsg_ma50", regime.get("ihsg_ma200", 0)),
                ma_label=regime.get("ma_label", "MA50"),
            )
            self._picks_cache = msg
            return msg

        caution_prefix = ""
        if r == "CAUTION":
            last = regime.get("ihsg_last", 0)
            ma200 = regime.get("ihsg_ma50", regime.get("ihsg_ma200", 0))
            ma_label = regime.get("ma_label", "MA50")
            caution_prefix = (
                f"**MODE WASPADA** — IHSG melemah di sekitar {ma_label}. "
                f"({last:,.2f} vs {ma_label} {ma200:,.0f})\n"
                "Kurangi ukuran posisi 50%, fokus ke setup paling clean, dan risk maksimal 1.5% per trade.\n\n"
            )

        await self._notify_progress(progress, Msg.picks_commodities())
        coal = await loop.run_in_executor(None, get_coal_price)

        await self._notify_progress(progress, Msg.picks_idx_scan())
        min_score = 72.0 if r == "CAUTION" else 60.0
        try:
            candidates = await loop.run_in_executor(
                None,
                lambda: scan_all_liquid_idx(
                    min_vol_ratio=1.0,
                    min_foreign_lot=0,
                    min_adx=15.0,
                    top_n=30,
                    min_score=min_score,
                ),
            )
            if len(candidates) < 3:
                candidates = await loop.run_in_executor(None, scan_watchlist, DEFAULT_WATCHLIST)
        except Exception as exc:
            print(f"[picks] ⚠️ scan_all_liquid_idx error: {exc} — fallback ke watchlist")
            candidates = await loop.run_in_executor(None, scan_watchlist, DEFAULT_WATCHLIST)

        await self._notify_progress(progress, Msg.picks_news())
        news_ctx = await self._build_news_context(candidates[:5])

        shortlist = build_quant_shortlist(candidates, regime=regime, max_candidates=12)
        eligible_shortlist = [item for item in shortlist if item.get("pick_eligible")]
        if len(eligible_shortlist) < 3:
            message = (
                "**DAILY PICKS IDX**\n\n"
                "Tidak ada minimal tiga setup yang memenuhi expected return positif, "
                "R/R minimum 1:1.2, dan kualitas entry yang memadai hari ini.\n\n"
                "Tidak ada saham yang dipaksakan masuk ke picks."
            )
            self._picks_cache = message
            return message
        shortlist = eligible_shortlist

        await self._notify_progress(progress, Msg.picks_ai())
        response = await loop.run_in_executor(
            None, generate_daily_picks, shortlist, news_ctx, regime, coal
        )

        tracker = get_picks_tracker()
        selected_tickers = extract_selected_tickers(
            response, [item.get("ticker", "") for item in shortlist]
        )
        if not selected_tickers:
            selected_tickers = [item.get("ticker", "") for item in shortlist[:3] if item.get("ticker")]
        selected_rows = []
        shortlist_map = {item.get("ticker"): item for item in shortlist}
        for ticker in selected_tickers[:3]:
            item = shortlist_map.get(ticker)
            if not item:
                continue
            selected_rows.append(
                {
                    "ticker": ticker,
                    "company_name": item.get("company_name", ""),
                    "entry_price": item.get("market", {}).get("price"),
                    "quant_rank": item.get("quant", {}).get("rank"),
                    "quant_score": item.get("quant", {}).get("score"),
                    "retail_position_pct": item.get("position_sizing", {}).get(
                        "retail_position_pct"
                    ),
                }
            )
        await loop.run_in_executor(
            None, lambda: tracker.record_run(today, selected_rows, shortlist)
        )
        asyncio.create_task(self._refresh_tracker_background())

        if caution_prefix:
            response = caution_prefix + response
        self._picks_cache = response
        return response

    async def _build_news_context(self, candidates: list[dict]) -> str:
        loop = asyncio.get_event_loop()
        news_batches = await asyncio.gather(
            *[
                loop.run_in_executor(None, get_berita, item.get("ticker", ""), 2)
                for item in candidates
                if item.get("ticker")
            ]
        )
        lines = []
        for item, berita_list in zip(
            [c for c in candidates if c.get("ticker")], news_batches
        ):
            ticker = item.get("ticker", "")
            for berita in berita_list:
                lines.append(f"[{ticker}] {berita.get('title', '')}")
        return "\n".join(lines)

    async def _refresh_tracker_background(self) -> None:
        loop = asyncio.get_event_loop()
        tracker = get_picks_tracker()
        try:
            await loop.run_in_executor(None, tracker.refresh_results)
        except Exception as exc:
            print(f"[picks] ⚠️ tracker.refresh_results background gagal: {exc}")

    async def _notify_progress(self, progress, content: str) -> None:
        if progress is None:
            return
        try:
            await progress(content=content)
        except TypeError:
            await progress(content)
        except Exception:
            pass

    async def _build_pick_stats(self) -> str:
        loop = asyncio.get_event_loop()
        tracker = get_picks_tracker()
        await loop.run_in_executor(None, tracker.refresh_results)
        stats = await loop.run_in_executor(None, tracker.summarize, 30)
        ret7 = stats.get("ret_7d", {})
        ret14 = stats.get("ret_14d", {})

        def _fmt(label: str, block: dict) -> str:
            if not block or not block.get("count"):
                return f"{label}: data belum cukup"
            return (
                f"{label}: hit-rate **{block.get('hit_rate', 0):.1f}%** | "
                f"avg return **{block.get('avg_return', 0):+.2f}%** | "
                f"sampel {block.get('count', 0)} picks"
            )

        recent = ", ".join(stats.get("last_dates", [])[-3:]) or "-"
        return (
            "**PERFORMA DAILY PICKS — 30 HARI**\n"
            f"{_fmt('7D', ret7)}\n"
            f"{_fmt('14D', ret14)}\n"
            f"Total picks tercatat: **{stats.get('total_picks', 0)}**\n"
            f"Update terakhir: {recent}\n"
            "Catatan: hit-rate dihitung dari close aktual 7 dan 14 hari setelah picks."
        )


def _get_ihsg_regime_safe() -> dict:
    try:
        return get_ihsg_regime()
    except Exception:
        return {"regime": "UNKNOWN", "warning": ""}


async def setup(bot):
    await bot.add_cog(PicksCog(bot))
