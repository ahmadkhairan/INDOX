from __future__ import annotations

from datetime import datetime

import discord
from discord.ext import commands

from config import DEFAULT_WATCHLIST


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="watchlist", aliases=["wl"])
    async def cmd_watchlist(self, ctx):
        tickers = " | ".join(DEFAULT_WATCHLIST)
        await ctx.reply(
            f"**Watchlist Default ({len(DEFAULT_WATCHLIST)} saham):**\n"
            f"`{tickers}`\n\n"
            f"Untuk scan **seluruh IDX**: gunakan `!picks` (otomatis scan semua saham liquid IDX)\n"
            f"Gunakan `!analisis TICKER` untuk analisis mendalam."
        )

    @commands.command(name="reset")
    async def cmd_reset(self, ctx):
        chat_cog = self.bot.get_cog("ChatCog")
        if chat_cog is not None:
            chat_cog.reset_history(ctx.author.id)
        await ctx.reply("History chat kamu sudah direset.")

    @commands.command(name="bantuan", aliases=["h", "help_saham"])
    async def cmd_bantuan(self, ctx):
        embed = discord.Embed(
            title="IDX Analyst Bot v4 — Panduan",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Commands Utama",
            value=(
                "`!analisis TICKER` — Analisis lengkap 1 saham (AI Groq)\n"
                "`!picks` — Daily top picks dari **scan seluruh IDX liquid**\n"
                "`!market` — Overview IHSG + regime check + gainer/loser\n"
                "`!portfolio` — Analisis portfolio (attach CSV)\n"
                "`!alert TICKER > HARGA` — Notif harga via DM\n"
                "`!backtest TICKER [BULAN]` — Backtest v4 + Monte Carlo\n"
                "`!walkforward TICKER [TAHUN]` — Uji robustness walk-forward\n"
                "`!montecarlo TICKER [BULAN]` — Simulasi distribusi return\n"
                "`!backtest_legacy` — Deprecated, arahkan ke engine v4\n"
                "`!watchlist` — Lihat daftar saham default\n"
                "`!reset` — Reset history chat\n"
                "`@bot [pertanyaan]` — Chat bebas tentang saham"
            ),
            inline=False,
        )
        embed.add_field(
            name="Fitur Utama",
            value=(
                "1. **Scan seluruh IDX** di `!picks` (bukan hanya watchlist)\n"
                "2. **IHSG Regime Filter** untuk menghentikan pick saat bear market\n"
                "3. Deteksi buyback dan dividen dari berita terkini\n"
                "4. Backtest v4 dengan multi-entry, dynamic exit, adaptive risk, dan Monte Carlo\n"
                "5. Walk-forward test untuk memeriksa robustness strategi\n"
                "6. Risk engine dan optimizer untuk analisis portfolio\n"
                "7. FastAPI untuk akses fitur melalui endpoint"
            ),
            inline=False,
        )
        embed.add_field(
            name="Scoring Model",
            value=(
                "Setiap saham mendapat **score 0-100** berdasarkan:\n"
                "1. Fundamental: 40% _(threshold menyesuaikan sektor)_\n"
                "2. Teknikal: 30% _(momentum, pullback, dan breakout)_\n"
                "3. Flow: 20% _(mining mendapat bobot ekstra)_\n"
                "4. Bonus: coal rally, signal stack, dan quality setup"
            ),
            inline=False,
        )
        embed.add_field(
            name="Label Picks",
            value=(
                "HOT VOLUME — volume ratio ≥ 2x\n"
                "FOREIGN RUSH — net buy asing besar\n"
                "COAL RALLY — batubara > $90/ton\n"
                "HIGH DIV — dividend yield ≥ 5%"
            ),
            inline=False,
        )
        embed.set_footer(text="Bukan saran investasi | IDX Analyst Bot v4")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
