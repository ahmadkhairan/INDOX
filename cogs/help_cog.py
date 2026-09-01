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
            title="IDX Analyst Bot v4 — Panduan Perintah",
            description="Gunakan prefix `!` atau Slash Command `/` untuk berinteraksi dengan bot.",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="📊 Analisis & Pasar",
            value=(
                "• `!analisis TICKER [en/id]` — Analisis teknikal, fundamental & AI (Bilingual ID/EN)\n"
                "• `!picks` — Daily top picks dari scan seluruh IDX\n"
                "• `!market` — Status IHSG, regime market & top movers\n"
                "• `!portfolio` — Analisis risiko & kesehatan portofolio (attach CSV)\n"
                "• `!alert TICKER > HARGA` — Pasang notifikasi harga via DM\n"
                "• `@bot [pertanyaan]` — Tanya jawab bebas seputar saham"
            ),
            inline=False,
        )
        embed.add_field(
            name="📝 Paper Trading & Jurnal",
            value=(
                "• `!paper list` — Lihat posisi paper trade yang OPEN\n"
                "• `!paper stats` — Laporan statistik PnL, Win Rate & Profit Factor\n"
                "• `!paper check` — Cek harga sekarang & evaluasi otomatis SL/TP\n"
                "• `!paper enter TICKER ENTRY SL TP1 TP2 [LOT]` — Catat trade baru\n"
                "• `!paper close ID HARGA` — Tutup posisi paper trade manual\n"
                "• `!catat TICKER BUY/SELL HARGA LOT [NOTE]` — Jurnal trade real broker"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔬 Backtest & Validasi",
            value=(
                "• `!backtest TICKER [BULAN]` — Backtest engine v4 + Monte Carlo\n"
                "• `!walkforward TICKER [TAHUN]` — Uji robustness walk-forward\n"
                "• `!montecarlo TICKER [BULAN]` — Simulasi probabilitas return & drawdown"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛡️ Risk Engine & Optimizer",
            value=(
                "• `!var TICKER` — Hitung Value at Risk (VaR & CVaR)\n"
                "• `!stress TICKER1 TICKER2...` — Stress test skenario krisis pasar\n"
                "• `!corr TICKER1 TICKER2...` — Matriks korelasi & diversifikasi\n"
                "• `!optimize TICKER1 TICKER2...` — Optimasi bobot portofolio (Sharpe)\n"
                "• `!kelly WIN_RATE WIN_LOSS_RATIO` — Kalkulator ukuran posisi Kelly"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ Lainnya",
            value=(
                "• `!watchlist` — Daftar saham default\n"
                "• `!reset` — Reset riwayat chat dengan bot"
            ),
            inline=False,
        )
        embed.set_footer(text="Bukan saran investasi | IDX Analyst Bot v4")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
