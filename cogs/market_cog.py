from __future__ import annotations

import asyncio
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DAILY_CHANNEL_ID, MONTHLY_HOUR_UTC, MONTHLY_MINUTE
from utils.command_limits import MARKET_COOLDOWN
from utils.helpers import send_long
from utils.market_regime import get_ihsg, get_ihsg_regime, get_top_movers


class MarketCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.monthly_task.start()

    def cog_unload(self):
        self.monthly_task.cancel()

    @commands.command(name="market", aliases=["m", "pasar"])
    @commands.cooldown(*MARKET_COOLDOWN, commands.BucketType.user)
    async def cmd_market(self, ctx):
        msg = await ctx.reply("⏳ Mengambil data pasar + regime check...")
        ihsg, movers, regime = await self._fetch_market_data(top_n=5)
        await msg.delete()
        await ctx.reply(_build_market_overview(ihsg, movers, regime))

    @app_commands.command(name="market", description="Overview pasar IDX hari ini")
    @app_commands.checks.cooldown(*MARKET_COOLDOWN)
    async def slash_market(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        ihsg, movers, regime = await self._fetch_market_data(top_n=5)
        await interaction.followup.send(_build_market_overview(ihsg, movers, regime))

    async def _fetch_market_data(self, top_n: int) -> tuple[dict, dict, dict]:
        loop = asyncio.get_event_loop()
        return await asyncio.gather(
            loop.run_in_executor(None, get_ihsg),
            loop.run_in_executor(None, get_top_movers, top_n),
            loop.run_in_executor(None, get_ihsg_regime),
        )

    @tasks.loop(minutes=1)
    async def monthly_task(self):
        now = datetime.utcnow()
        from calendar import monthrange

        last_day = monthrange(now.year, now.month)[1]
        is_last = now.day == last_day
        is_time = now.hour == MONTHLY_HOUR_UTC and now.minute == MONTHLY_MINUTE

        if not (is_last and is_time):
            return

        channel = self.bot.get_channel(DAILY_CHANNEL_ID)
        if not channel:
            return

        ihsg, movers, regime = await self._fetch_market_data(top_n=10)
        report = _build_monthly_report(now, ihsg, movers, regime)
        await channel.send("📅 **MONTHLY REPORT IDX**")
        await send_long(channel, report)

    @monthly_task.before_loop
    async def before_monthly(self):
        await self.bot.wait_until_ready()


def _build_market_overview(ihsg: dict, movers: dict, regime: dict | None = None) -> str:
    chg = ihsg.get("change_pct", 0)
    sign = "+" if chg >= 0 else ""
    emoji = "📈" if chg >= 0 else "📉"

    regime_line = ""
    if regime:
        r = regime.get("regime", "")
        ma_label = regime.get("ma_label", "MA50")
        if r == "BEAR":
            regime_line = f"\n⚠️ **BEAR MARKET**: IHSG di bawah {ma_label} ({regime.get('ihsg_ma50', regime.get('ihsg_ma200', 0)):,.0f})"
        elif r == "CAUTION":
            regime_line = f"\n🟡 **CAUTION**: IHSG mendekati {ma_label} ({regime.get('ihsg_ma50', regime.get('ihsg_ma200', 0)):,.0f})"
        else:
            regime_line = f"\n✅ **BULL REGIME**: IHSG di atas {ma_label} ({regime.get('ihsg_ma50', regime.get('ihsg_ma200', 0)):,.0f})"

    lines = [
        f"🏦 **IHSG OVERVIEW** — {datetime.now().strftime('%d %b %Y %H:%M WIB')}",
        f"{emoji} **IHSG: {ihsg.get('price', 0):,.2f}** ({sign}{chg:.2f}%){regime_line}",
        "",
        "🟢 **TOP GAINERS:**",
    ]
    for gainer in movers.get("gainers", []):
        lines.append(f"  • **{gainer['ticker']}** Rp{gainer['price']:,.0f} (+{gainer['change_pct']:.2f}%)")

    lines.append("\n🔴 **TOP LOSERS:**")
    for loser in movers.get("losers", []):
        lines.append(f"  • **{loser['ticker']}** Rp{loser['price']:,.0f} ({loser['change_pct']:.2f}%)")

    lines.append("\n_Gunakan `!analisis TICKER` untuk detail._")
    return "\n".join(lines)


def _build_monthly_report(now: datetime, ihsg: dict, movers: dict, regime: dict | None = None) -> str:
    chg = ihsg.get("change_pct", 0)
    month = now.strftime("%B %Y")
    regime_note = ""
    if regime:
        ma_label = regime.get("ma_label", "MA50")
        regime_name = regime.get("regime")
        flag = "⚠️ BEAR" if regime_name == "BEAR" else "🟡 CAUTION" if regime_name == "CAUTION" else "✅ BULL"
        regime_note = f"\nRegime: {flag} | IHSG {ma_label}: {regime.get('ihsg_ma50', regime.get('ihsg_ma200', 0)):,.0f}"

    lines = [
        f"📅 **MONTHLY REPORT IDX — {month}**",
        f"IHSG: {ihsg.get('price', 0):,.2f} ({'+' if chg >= 0 else ''}{chg:.2f}%){regime_note}",
        "",
        "🏆 **Top 10 Gainers bulan ini:**",
    ]
    for gainer in movers.get("gainers", []):
        lines.append(f"  • **{gainer['ticker']}** +{gainer['change_pct']:.2f}%")
    lines.append("\n📉 **Top 10 Losers bulan ini:**")
    for loser in movers.get("losers", []):
        lines.append(f"  • **{loser['ticker']}** {loser['change_pct']:.2f}%")
    lines.append("\n_Gunakan `!picks` untuk picks terbaru dari seluruh IDX._")
    return "\n".join(lines)


async def setup(bot):
    await bot.add_cog(MarketCog(bot))
