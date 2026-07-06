from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.command_limits import BACKTEST_COOLDOWN


class LegacyBacktestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="backtest_legacy", aliases=["bt_legacy"])
    @commands.cooldown(*BACKTEST_COOLDOWN, commands.BucketType.user)
    async def cmd_backtest_legacy(self, ctx, ticker: str = None, bulan: str = "3"):
        target = f"`!backtest {ticker} {bulan}`" if ticker else "`!backtest BBCA 3`"
        await ctx.reply(
            "⚠️ `!backtest_legacy` sudah deprecated dan dinonaktifkan untuk mencegah kebingungan dua engine.\n"
            f"Gunakan {target}, atau `!walkforward BBCA 2` / `!montecarlo BBCA 6` untuk evaluasi lanjutan."
        )

    @app_commands.command(name="backtestv3", description="Simulasi backtest strategi swing legacy (ATR dynamic)")
    @app_commands.describe(ticker="Kode saham", bulan="Periode (1-12 bulan, default 3)")
    @app_commands.checks.cooldown(*BACKTEST_COOLDOWN)
    async def slash_backtest_legacy(self, interaction: discord.Interaction, ticker: str, bulan: int = 3):
        await interaction.response.send_message(
            "⚠️ `/backtestv3` sudah deprecated dan dinonaktifkan.\n"
            f"Gunakan `/backtest ticker:{ticker} bulan:{max(1, min(bulan, 12))}` atau `/walkforward` / `/montecarlo`.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(LegacyBacktestCog(bot))
