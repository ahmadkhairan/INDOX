# cogs/analisis_cog.py — v4
# UPGRADES:
#   - Regime check ditambahkan ke context analisis
#   - Coal price pass ke data sebelum analisis
#   - Special news (buyback/dividen) di-detect sebelum AI call
#   - Memory context lebih kaya (score + confidence + entry quality + preferred mode)

import asyncio
import discord
from discord.ext import commands
from discord import app_commands

from utils.command_limits import ANALYSIS_COOLDOWN
from utils.ai_engine import analyze_ticker
from memory.simple_memory import get_memory
from config import FEATURE_VECTOR_MEMORY
from utils.helpers import send_long, split_msg
from utils.market_regime import get_coal_price, get_ihsg_regime
from utils.news_utils import detect_special_news, get_berita
from utils.stock_service import get_stock_data
from utils.ticker_utils import normalize_ticker


class AnalisisCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Prefix command ────────────────────────────────────
    @commands.command(name="analisis", aliases=["a", "analis"])
    @commands.cooldown(*ANALYSIS_COOLDOWN, commands.BucketType.user)
    async def cmd_analisis(self, ctx, ticker: str = None, *, pertanyaan: str = ""):
        if not ticker:
            await ctx.reply("❌ Sertakan kode saham. Contoh: `!analisis BBCA`")
            return
        await self._run_analisis(ctx, ticker, pertanyaan)

    # ── Slash command ─────────────────────────────────────
    @app_commands.command(name="analisis", description="Analisis lengkap saham IDX (Powered by Groq AI)")
    @app_commands.describe(ticker="Kode saham (contoh: BBCA)", pertanyaan="Pertanyaan spesifik (opsional)")
    @app_commands.checks.cooldown(*ANALYSIS_COOLDOWN)
    async def slash_analisis(self, interaction: discord.Interaction, ticker: str, pertanyaan: str = ""):
        await interaction.response.defer(thinking=True)
        await self._run_analisis(interaction, ticker, pertanyaan, slash=True)

    # ── Core logic ────────────────────────────────────────
    async def _run_analisis(self, ctx_or_inter, ticker: str, pertanyaan: str, slash: bool = False):
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            msg = f"❌ {exc}"
            if slash:
                await ctx_or_inter.followup.send(msg)
            else:
                await ctx_or_inter.reply(msg)
            return
        mem    = get_memory()

        wait_msg = None
        if not slash:
            wait_msg = await ctx_or_inter.reply(
                f"⏳ Menganalisis **{ticker}**... "
                f"(data fetcher + AI, estimasi 15-30 detik)"
            )

        loop = asyncio.get_event_loop()

        # Paralel: data saham + berita + regime + coal
        data, news, regime, coal = await asyncio.gather(
            loop.run_in_executor(None, get_stock_data, ticker),
            loop.run_in_executor(None, get_berita, ticker, 5),
            loop.run_in_executor(None, get_ihsg_regime),
            loop.run_in_executor(None, get_coal_price),
        )

        if "error" in data:
            err = (
                f"❌ **{ticker}**: {data['error']}\n"
                f"Pastikan kode saham benar (contoh: BBCA, TLKM, ADRO)"
            )
            if wait_msg: await wait_msg.delete()
            if slash:    await ctx_or_inter.followup.send(err)
            else:        await ctx_or_inter.channel.send(err, reference=ctx_or_inter.message)
            return

        # Inject coal data ke data dict (untuk prompt AI)
        sc = data.get("sector_context", {})
        if sc.get("label") == "Coal Mining":
            data["coal_data"] = coal

        # Special news detection
        special = detect_special_news(news)

        # Build pertanyaan dengan memory context
        mem_ctx = mem.build_context(ticker)

        # RAG context dari VectorMemory
        rag_ctx = ""
        if FEATURE_VECTOR_MEMORY:
            try:
                from memory.vector_memory import get_vector_memory
                vm = await get_vector_memory()
                rag_ctx = await vm.get_rag_context(ticker, pertanyaan or "analisis")
            except Exception:
                pass

        # Regime warning
        regime_warning = ""
        if regime.get("regime") in ("BEAR", "CAUTION"):
            regime_warning = f"\n{regime.get('warning', '')}\n"

        # Special news prefix
        special_ctx = ""
        if special["has_buyback"]:
            special_ctx += f"\n🔄 BUYBACK DETECTED: {'; '.join(special['buyback_news'])}"
        if special["has_dividend"]:
            special_ctx += f"\n💰 DIVIDEN DETECTED: {'; '.join(special['dividend_news'])}"

        full_context = "\n".join(filter(None, [mem_ctx, rag_ctx, regime_warning, special_ctx, pertanyaan]))

        # Panggil AI
        response = await loop.run_in_executor(None, analyze_ticker, data, news, full_context)

        # Simpan ke memory dengan info lebih kaya
        score_data = data.get("score", {})
        total_s    = score_data.get("total", 0.0)
        confidence = score_data.get("confidence", "Low")
        entry_q    = score_data.get("entry_quality", "N/A")
        preferred_mode = score_data.get("preferred_entry_mode", data.get("technical", {}).get("preferred_entry_mode", "WAIT"))
        action     = "BUY" if total_s >= 65 else ("HOLD" if total_s >= 45 else "AVOID")
        snippet    = response[:200] if response else ""

        mem.save_analysis(
            ticker, snippet, snippet,
            score=total_s, action=action,
            extra={
                "confidence": confidence,
                "entry_quality": entry_q,
                "preferred_mode": preferred_mode,
                "sector": sc.get("label", "N/A"),
            }
        )

        if FEATURE_VECTOR_MEMORY:
            try:
                from memory.vector_memory import get_vector_memory
                vm = await get_vector_memory()
                await vm.add_analysis(
                    ticker, response, total_s, action,
                    extra={
                        "confidence": confidence,
                        "entry_quality": entry_q,
                        "preferred_mode": preferred_mode,
                        "sector": sc.get("label", "N/A"),
                    }
                )
            except Exception:
                pass

        if wait_msg:
            await wait_msg.delete()

        channel = ctx_or_inter.channel if not slash else None
        ref     = ctx_or_inter.message if not slash else None

        if slash:
            chunks = split_msg(response)
            await ctx_or_inter.followup.send(chunks[0])
            for c in chunks[1:]:
                await ctx_or_inter.followup.send(c)
                await asyncio.sleep(0.3)
        else:
            await send_long(channel, response, reply_to=ref)


async def setup(bot):
    await bot.add_cog(AnalisisCog(bot))
