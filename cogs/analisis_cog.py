import asyncio
from datetime import datetime
import re
from typing import Optional
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


def parse_analysis_query(raw_query: str) -> tuple[str, str]:
    """Parse question and language choice from raw user query text.
    Returns (cleaned_question, language_code) where language_code is 'id' or 'en'.
    """
    if not raw_query:
        return "", "id"

    text = raw_query.strip()
    words = text.split()
    if not words:
        return "", "id"

    first_word = words[0].lower().strip("-,:;[]()")
    en_tokens = {"en", "eng", "english", "inggris", "lang=en", "language=en"}
    id_tokens = {"id", "ina", "indo", "indonesia", "lang=id", "language=id"}

    if first_word in en_tokens:
        clean_q = " ".join(words[1:]).strip()
        return clean_q, "en"
    elif first_word in id_tokens:
        clean_q = " ".join(words[1:]).strip()
        return clean_q, "id"

    lower_text = text.lower()
    if any(p in lower_text for p in ("in english", "bahasa inggris", "dalam bahasa inggris", "use english", "english please")):
        return text, "en"
    if any(p in lower_text for p in ("bahasa indonesia", "dalam bahasa indonesia", "pake bahasa indo", "bahasa indo")):
        return text, "id"

    en_starters = ("what", "how", "why", "when", "is it", "should i", "can we", "target price", "buy or sell", "tell me")
    if any(lower_text.startswith(starter) for starter in en_starters):
        return text, "en"

    return text, "id"


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Splits AI output into (header, content) tuples based on numbered sections."""
    pattern = r'(?:^|\n)(?=(?:[1-7]️⃣|\d+\.|\#\#)\s*\*{0,2})'
    raw_parts = [p.strip() for p in re.split(pattern, text) if p.strip()]

    sections = []
    for part in raw_parts:
        lines = part.split("\n", 1)
        header = lines[0].strip().replace("#", "").strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        # Filter out trailing boilerplate disclaimers from field body
        if "disclaimer" in body.lower() or "bukan saran investasi" in body.lower() or "not financial investment advice" in body.lower():
            body_lines = [
                l for l in body.split("\n")
                if not any(kw in l.lower() for kw in ("disclaimer:", "bukan saran investasi", "not financial investment advice", "analisis edukasi"))
            ]
            body = "\n".join(body_lines).strip()

        if not body and not header:
            continue
        if not body:
            body = header
            header = "Detail"

        sections.append((header, body))
    return sections


def build_analysis_embed(ticker: str, data: dict, response_text: str, language: str = "id") -> discord.Embed:
    """Constructs a high-density, informative, single-message Discord Embed."""
    lang = "en" if str(language).lower().startswith("en") else "id"
    mkt = data.get("market", {})
    score = data.get("score", {})
    tech = data.get("technical", {})
    sc = data.get("sector_context", {})

    total_score = score.get("total", 0.0)
    grade = score.get("grade", "N/A")
    confidence = score.get("confidence", "N/A")
    preferred_mode = score.get("preferred_entry_mode", tech.get("preferred_entry_mode", "WAIT"))
    company_name = data.get("company_name", "")
    sector_name = sc.get("label", data.get("sector", "N/A"))
    price = mkt.get("price", 0)
    change_pct = mkt.get("change_pct", 0.0)
    vol_ratio = mkt.get("vol_ratio", 1.0)

    # Dynamic color based on score
    if total_score >= 65:
        color = discord.Color.green()
        signal_badge = "🟢 BULLISH / BUY"
    elif total_score >= 45:
        color = discord.Color.gold()
        signal_badge = "🟡 NEUTRAL / HOLD"
    else:
        color = discord.Color.red()
        signal_badge = "🔴 CAUTION / AVOID"

    title = f"📊 {ticker} — {company_name}" if company_name else f"📊 Analisis Saham {ticker}"

    if lang == "en":
        desc_lines = [
            f"**Sector**: {sector_name} | **Price**: `Rp {price:,.0f}` ({change_pct:+.2f}%) | **Vol Ratio**: `{vol_ratio:.1f}x`",
            f"**AI Score**: `{total_score:.1f}/100` (Grade {grade}) | **Signal**: **{signal_badge}** | **Mode**: `{preferred_mode}`",
        ]
    else:
        desc_lines = [
            f"**Sektor**: {sector_name} | **Harga**: `Rp {price:,.0f}` ({change_pct:+.2f}%) | **Vol Ratio**: `{vol_ratio:.1f}x`",
            f"**Skor AI**: `{total_score:.1f}/100` (Grade {grade}) | **Sinyal**: **{signal_badge}** | **Mode**: `{preferred_mode}`",
        ]

    embed = discord.Embed(
        title=title,
        description="\n".join(desc_lines),
        color=color,
        timestamp=datetime.now(),
    )

    sections = _split_into_sections(response_text)

    if sections and len(sections) >= 2:
        for header, body in sections:
            if not body:
                continue
            field_name = header[:256]
            if len(body) <= 1024:
                embed.add_field(name=field_name, value=body, inline=False)
            else:
                cut = body.rfind("\n", 0, 1020)
                if cut == -1:
                    cut = 1020
                embed.add_field(name=field_name, value=body[:cut].strip(), inline=False)
                remaining = body[cut:].strip()
                if remaining:
                    embed.add_field(name=f"{field_name} (cont.)", value=remaining[:1024], inline=False)
    else:
        clean_text = response_text.strip()
        if len(clean_text) <= 3900:
            embed.description = f"{embed.description}\n\n{clean_text}"
        else:
            chunks = split_msg(clean_text, limit=1000)
            for i, chunk in enumerate(chunks[:5]):
                embed.add_field(name=f"Detail #{i+1}", value=chunk, inline=False)

    footer_text = (
        "Bukan saran investasi | IDX Analyst Bot v4"
        if lang == "id"
        else "Educational analysis only, not investment advice | IDX Analyst Bot v4"
    )
    embed.set_footer(text=footer_text)
    return embed


class AnalisisCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Prefix command ────────────────────────────────────
    @commands.command(name="analisis", aliases=["a", "analis"])
    @commands.cooldown(*ANALYSIS_COOLDOWN, commands.BucketType.user)
    async def cmd_analisis(self, ctx, ticker: str = None, *, pertanyaan: str = ""):
        if not ticker:
            await ctx.reply("❌ Sertakan kode saham. Contoh: `!analisis BBCA` atau `!analisis BBCA en`")
            return
        await self._run_analisis(ctx, ticker, pertanyaan)

    # ── Slash command ─────────────────────────────────────
    @app_commands.command(name="analisis", description="Analisis lengkap saham IDX (Bilingual ID/EN)")
    @app_commands.describe(
        ticker="Kode saham (contoh: BBCA)",
        pertanyaan="Pertanyaan spesifik (opsional)",
        bahasa="Pilihan bahasa analisis (Indonesia / English)",
    )
    @app_commands.choices(
        bahasa=[
            app_commands.Choice(name="🇮🇩 Bahasa Indonesia", value="id"),
            app_commands.Choice(name="🇬🇧 English", value="en"),
        ]
    )
    @app_commands.checks.cooldown(*ANALYSIS_COOLDOWN)
    async def slash_analisis(
        self,
        interaction: discord.Interaction,
        ticker: str,
        pertanyaan: str = "",
        bahasa: Optional[app_commands.Choice[str]] = None,
    ):
        await interaction.response.defer(thinking=True)
        chosen_lang = bahasa.value if bahasa else None
        await self._run_analisis(interaction, ticker, pertanyaan, slash=True, language=chosen_lang)

    # ── Core logic ────────────────────────────────────────
    async def _run_analisis(
        self,
        ctx_or_inter,
        ticker: str,
        pertanyaan: str,
        slash: bool = False,
        language: Optional[str] = None,
    ):
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            msg = f"❌ {exc}"
            if slash:
                await ctx_or_inter.followup.send(msg)
            else:
                await ctx_or_inter.reply(msg)
            return

        if language:
            clean_q = (pertanyaan or "").strip()
            lang = "en" if str(language).lower().startswith("en") else "id"
        else:
            clean_q, lang = parse_analysis_query(pertanyaan)

        mem = get_memory()

        wait_msg = None
        if not slash:
            if lang == "en":
                wait_text = f"⏳ Analyzing **{ticker}**... (fetching market data + AI, est. 15-30s)"
            else:
                wait_text = f"⏳ Menganalisis **{ticker}**... (data fetcher + AI, estimasi 15-30 detik)"
            wait_msg = await ctx_or_inter.reply(wait_text)

        loop = asyncio.get_event_loop()

        # Paralel: data saham + berita + regime + coal
        data, news, regime, coal = await asyncio.gather(
            loop.run_in_executor(None, get_stock_data, ticker),
            loop.run_in_executor(None, get_berita, ticker, 5),
            loop.run_in_executor(None, get_ihsg_regime),
            loop.run_in_executor(None, get_coal_price),
        )

        if "error" in data:
            if lang == "en":
                err = (
                    f"❌ **{ticker}**: {data['error']}\n"
                    f"Please verify the ticker symbol (e.g., BBCA, TLKM, ADRO)"
                )
            else:
                err = (
                    f"❌ **{ticker}**: {data['error']}\n"
                    f"Pastikan kode saham benar (contoh: BBCA, TLKM, ADRO)"
                )
            if wait_msg:
                await wait_msg.delete()
            if slash:
                await ctx_or_inter.followup.send(err)
            else:
                await ctx_or_inter.channel.send(err, reference=ctx_or_inter.message)
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
                rag_ctx = await vm.get_rag_context(ticker, clean_q or "analisis")
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

        full_context = "\n".join(filter(None, [mem_ctx, rag_ctx, regime_warning, special_ctx, clean_q]))

        # Panggil AI dengan bahasa yang dipilih
        response = await loop.run_in_executor(None, analyze_ticker, data, news, full_context, lang)

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
                "language": lang,
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
                        "language": lang,
                    }
                )
            except Exception:
                pass

        embed = build_analysis_embed(ticker, data, response, lang)

        if slash:
            await ctx_or_inter.followup.send(embed=embed)
        else:
            if wait_msg:
                try:
                    await wait_msg.edit(content=None, embed=embed)
                except Exception:
                    await wait_msg.delete()
                    await ctx_or_inter.reply(embed=embed)
            else:
                await ctx_or_inter.reply(embed=embed)


async def setup(bot):
    await bot.add_cog(AnalisisCog(bot))


