# cogs/portfolio_cog.py
# Cog: !portfolio — upload CSV atau input manual, analisis AI

import asyncio
import io
import csv
from types import SimpleNamespace

try:
    import discord
    from discord.ext import commands
    from discord import app_commands
except Exception:  # pragma: no cover - fallback only used in lightweight test envs
    discord = SimpleNamespace(Attachment=object, Interaction=object, Embed=object, Color=SimpleNamespace(blue=lambda: 0))

    def _identity_decorator(*args, **kwargs):
        def _wrap(func):
            return func
        return _wrap

    class _DummyCog:
        pass

    class _DummyBucketType:
        user = object()

    commands = SimpleNamespace(
        Cog=_DummyCog,
        BucketType=_DummyBucketType,
        command=_identity_decorator,
        cooldown=_identity_decorator,
    )
    app_commands = SimpleNamespace(
        command=_identity_decorator,
        describe=_identity_decorator,
        checks=SimpleNamespace(cooldown=_identity_decorator),
    )

from utils.ai_engine import analyze_portfolio
from utils.command_limits import PORTFOLIO_COOLDOWN
from utils.helpers import send_long, split_msg
from utils.ticker_utils import normalize_ticker
from utils.yf_guard import YFinanceUnavailable, get_info


class PortfolioCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="portfolio", aliases=["pf"])
    @commands.cooldown(*PORTFOLIO_COOLDOWN, commands.BucketType.user)
    async def cmd_portfolio(self, ctx):
        """
        !portfolio — Attach CSV atau tulis format:
        TICKER,QTY,AVG_PRICE
        BBCA,100,9500
        TLKM,200,3100
        """
        if not ctx.message.attachments:
            # Bantu user dengan format
            embed = discord.Embed(
                title="📂 Cara Pakai !portfolio",
                color=discord.Color.blue(),
                description=(
                    "**Cara 1 — Upload CSV:**\n"
                    "Buat file CSV dengan format:\n"
                    "```\nTICKER,QTY,AVG_PRICE\n"
                    "BBCA,100,9500\nTLKM,200,3100\nBBRI,150,5000\n```"
                    "Kemudian ketik `!portfolio` sambil attach file CSV tersebut.\n\n"
                    "**Format kolom:**\n"
                    "• `TICKER` — kode saham IDX (tanpa .JK)\n"
                    "• `QTY` — jumlah lot (1 lot = 100 lembar)\n"
                    "• `AVG_PRICE` — harga rata-rata beli (per lembar, dalam Rupiah)"
                )
            )
            await ctx.reply(embed=embed)
            return

        msg = await ctx.reply("⏳ Membaca CSV & menganalisis portfolio... (20-40 detik)")
        result = await _analyze_attachment(ctx.message.attachments[0])
        if "error" in result:
            await msg.delete()
            await ctx.reply(f"❌ {result['error']}")
            return

        await msg.delete()
        await send_long(ctx.channel, result["text"], reply_to=ctx.message)

    @app_commands.command(name="portfolio", description="Analisis portfolio saham kamu")
    @app_commands.checks.cooldown(*PORTFOLIO_COOLDOWN)
    @app_commands.describe(file="File CSV dengan format TICKER,QTY,AVG_PRICE")
    async def slash_portfolio(self, interaction: discord.Interaction, file: discord.Attachment):
        await interaction.response.defer(thinking=True)
        result = await _analyze_attachment(file)
        if "error" in result:
            await interaction.followup.send(f"❌ {result['error']}", ephemeral=True)
            return
        chunks = split_msg(result["text"])
        await interaction.followup.send(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────

async def _parse_csv(raw_text: str) -> list:
    holdings = []
    sanitized_text = raw_text.lstrip("\ufeff")
    reader   = csv.DictReader(io.StringIO(sanitized_text))
    for row in reader:
        # Normalisasi nama kolom (case-insensitive, strip)
        normalized = {}
        overflow = []
        for k, v in row.items():
            if k is None:
                if isinstance(v, list):
                    overflow.extend(item.strip() for item in v if isinstance(item, str))
                elif isinstance(v, str):
                    overflow.append(v.strip())
                continue
            normalized[k.strip().upper()] = v.strip() if isinstance(v, str) else ""
        if overflow and normalized.get("AVG_PRICE"):
            normalized["AVG_PRICE"] = "".join([normalized["AVG_PRICE"], *overflow])

        row = normalized
        ticker    = row.get("TICKER") or row.get("KODE") or row.get("SAHAM", "")
        qty       = row.get("QTY") or row.get("LOT") or row.get("JUMLAH", "0")
        avg_price = row.get("AVG_PRICE") or row.get("AVG") or row.get("HARGA_BELI", "0")

        if not ticker:
            continue
        try:
            holdings.append({
                "ticker":    normalize_ticker(ticker),
                "qty":       int(float(qty.replace(",", ""))),
                "avg_price": float(avg_price.replace(",", "")),
                "current_price": 0.0,
            })
        except (ValueError, AttributeError):
            continue
    return holdings


async def _analyze_attachment(att: discord.Attachment) -> dict:
    if not att.filename.endswith(".csv"):
        return {"error": "Lampirkan file CSV. Format: `TICKER,QTY,AVG_PRICE`"}

    try:
        raw_bytes = await att.read()
        raw_text = raw_bytes.decode("utf-8-sig")
        holdings = await _parse_csv(raw_text)
    except Exception as exc:
        return {"error": f"Gagal baca CSV: {exc}"}

    if not holdings:
        return {"error": "CSV kosong atau format tidak valid."}

    loop = asyncio.get_event_loop()
    holdings = await loop.run_in_executor(None, _enrich_holdings, holdings)
    response = await loop.run_in_executor(None, analyze_portfolio, holdings)
    summary = _build_summary_table(holdings)
    return {"text": summary + "\n\n" + response}


def _enrich_holdings(holdings: list) -> list:
    """Fetch harga terkini untuk semua holding."""
    for h in holdings:
        try:
            info  = get_info(f"{h['ticker']}.JK")
            price = float(
                info.get("currentPrice") or
                info.get("regularMarketPrice") or
                info.get("ask") or
                h["avg_price"]
            )
            h["current_price"] = price
        except YFinanceUnavailable:
            h["current_price"] = h["avg_price"]
        except Exception:
            h["current_price"] = h["avg_price"]
    return holdings


def _build_summary_table(holdings: list) -> str:
    """Buat tabel ringkasan portfolio."""
    lines  = ["📊 **PORTFOLIO SUMMARY**", "```"]
    lines += [f"{'TICKER':<8} {'LOT':>6} {'AVG':>8} {'NOW':>8} {'P&L%':>7} {'P&L IDR':>12}"]
    lines += ["─" * 55]

    total_inv = 0.0
    total_val = 0.0
    for h in holdings:
        inv     = h["qty"] * h["avg_price"] * 100    # lot * lembar
        val     = h["qty"] * h["current_price"] * 100
        pnl     = val - inv
        pnl_pct = (pnl / inv * 100) if inv > 0 else 0
        total_inv += inv
        total_val += val
        sign = "+" if pnl_pct >= 0 else ""
        lines.append(
            f"{h['ticker']:<8} {h['qty']:>6,} {h['avg_price']:>8,.0f} "
            f"{h['current_price']:>8,.0f} {sign}{pnl_pct:>6.2f}% {sign}{pnl:>11,.0f}"
        )

    total_pnl     = total_val - total_inv
    total_pnl_pct = (total_pnl / total_inv * 100) if total_inv > 0 else 0
    lines += ["─" * 55]
    sign = "+" if total_pnl >= 0 else ""
    lines.append(f"{'TOTAL':<8} {'':>6} {'':>8} {'':>8} {sign}{total_pnl_pct:>6.2f}% {sign}{total_pnl:>11,.0f}")
    lines.append("```")
    lines.append(f"💼 Modal: Rp{total_inv:,.0f} | Nilai: Rp{total_val:,.0f}")
    return "\n".join(lines)


async def setup(bot):
    await bot.add_cog(PortfolioCog(bot))
