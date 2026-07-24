# cogs/alert_cog.py
# Cog: !alert [TICKER] [> atau <] [HARGA]
# Background task cek harga tiap 60 detik & kirim DM user

import asyncio
from datetime import datetime
import discord
from discord.ext import commands, tasks
from discord import app_commands

from data.alert_store_sqlite import get_alert_store
from config import ALERT_CHECK_SECONDS
from utils.helpers import send_long
from utils.ticker_utils import normalize_ticker
from utils.yf_guard import YFinanceUnavailable, get_info


class AlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot   = bot
        self.store = get_alert_store()
        self.check_alerts.start()

    def cog_unload(self):
        self.check_alerts.cancel()

    # ── Background check ──────────────────────────────────
    @tasks.loop(seconds=ALERT_CHECK_SECONDS)
    async def check_alerts(self):
        alerts = self.store.get_all_active()
        if not alerts:
            return

        # Kumpulkan ticker unik
        tickers = list(set(a["ticker"] for a in alerts))
        prices  = {}
        loop    = asyncio.get_event_loop()

        for ticker in tickers:
            try:
                price = await loop.run_in_executor(None, _get_price, ticker)
                if price and price > 0:
                    prices[ticker] = price
            except Exception:
                pass

        for alert in alerts:
            ticker = alert["ticker"]
            if ticker not in prices:
                continue
            price  = prices[ticker]
            cond   = alert["condition"]
            target = alert["price"]

            triggered = (cond == ">" and price > target) or (cond == "<" and price < target)
            if not triggered:
                continue

            # Kirim DM user
            user = self.bot.get_user(alert["user_id"])
            if not user:
                try:
                    user = await self.bot.fetch_user(alert["user_id"])
                except Exception:
                    continue

            try:
                emoji = "🚀" if cond == ">" else "📉"
                msg = (
                    f"{emoji} **ALERT TRIGGERED: {ticker}**\n"
                    f"Harga sekarang: **Rp{price:,.0f}**\n"
                    f"Kondisi: {ticker} {cond} Rp{target:,.0f} ✅\n"
                    f"_Waktu: {datetime.now().strftime('%d %b %Y %H:%M WIB')}_\n\n"
                    f"Gunakan `!analisis {ticker}` untuk analisis terkini."
                )
                await user.send(msg)
                self.store.deactivate(alert["user_id"], ticker, cond, target)
                print(f"[Alert] ✅ Terkirim ke {alert['user_id']} — {ticker} {cond} {target}")
            except discord.Forbidden:
                print(f"[Alert] ⚠️ DM ditolak user {alert['user_id']}")
            except Exception as e:
                print(f"[Alert] ❌ Error: {e}")

    @check_alerts.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # ── !alert ────────────────────────────────────────────
    @commands.command(name="alert")
    async def cmd_alert(self, ctx, ticker: str = None, condition: str = None, price: str = None):
        """
        !alert BBCA > 10500   — notif jika BBCA di atas 10500
        !alert TLKM < 3000    — notif jika TLKM di bawah 3000
        !alert list            — lihat alert aktif kamu
        !alert hapus BBCA      — hapus alert BBCA kamu
        """
        if not ticker:
            embed = discord.Embed(title="📢 Cara Pakai !alert", color=discord.Color.orange())
            embed.add_field(name="Pasang Alert", value="`!alert BBCA > 10500`\n`!alert TLKM < 3000`", inline=False)
            embed.add_field(name="Lihat Alert", value="`!alert list`", inline=False)
            embed.add_field(name="Hapus Alert", value="`!alert hapus BBCA`", inline=False)
            await ctx.reply(embed=embed)
            return

        uid = ctx.author.id

        # List
        if ticker.lower() == "list":
            alerts = self.store.get_user_alerts(uid)
            if not alerts:
                await ctx.reply("📭 Kamu tidak punya alert aktif.")
                return
            lines = [f"• **{a['ticker']}** {a['condition']} Rp{a['price']:,.0f}" for a in alerts]
            await ctx.reply("📋 **Alert aktifmu:**\n" + "\n".join(lines))
            return

        # Hapus
        if ticker.lower() == "hapus":
            if not condition:
                await ctx.reply("❌ Sertakan ticker yang ingin dihapus. Contoh: `!alert hapus BBCA`")
                return
            try:
                target_t = normalize_ticker(condition)
            except ValueError as exc:
                await ctx.reply(f"❌ {exc}")
                return
            n = self.store.remove(uid, target_t)
            await ctx.reply(f"✅ {n} alert untuk **{target_t}** dihapus." if n else f"❌ Tidak ada alert aktif untuk **{target_t}**.")
            return

        # Pasang alert
        if not condition or not price:
            await ctx.reply("❌ Format: `!alert TICKER > HARGA` atau `!alert TICKER < HARGA`")
            return

        if condition not in (">", "<"):
            await ctx.reply("❌ Kondisi harus `>` atau `<`. Contoh: `!alert BBCA > 10500`")
            return

        try:
            target_price = float(price.replace(",", "").replace(".", ""))
        except ValueError:
            await ctx.reply("❌ Harga tidak valid. Contoh: `!alert BBCA > 10500`")
            return

        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return
        added  = self.store.add(uid, ticker, condition, target_price)
        if added:
            await ctx.reply(
                f"✅ Alert dipasang: **{ticker}** {condition} Rp{target_price:,.0f}\n"
                f"Kamu akan mendapat DM saat kondisi ini terpenuhi.\n"
                f"_(Pastikan DM dari server ini tidak diblokir)_"
            )
        else:
            await ctx.reply(f"⚠️ Alert **{ticker}** {condition} Rp{target_price:,.0f} sudah ada.")

    # ── Slash ─────────────────────────────────────────────
    @app_commands.command(name="alert", description="Pasang notifikasi harga saham via DM")
    @app_commands.describe(
        ticker="Kode saham (contoh: BBCA)",
        condition="Kondisi: > (naik di atas) atau < (turun di bawah)",
        price="Target harga (contoh: 10500)"
    )
    @app_commands.choices(condition=[
        app_commands.Choice(name="Di atas (>)", value=">"),
        app_commands.Choice(name="Di bawah (<)", value="<"),
    ])
    async def slash_alert(self, interaction: discord.Interaction, ticker: str, condition: str, price: float):
        uid    = interaction.user.id
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        added  = self.store.add(uid, ticker, condition, price)
        if added:
            await interaction.response.send_message(
                f"✅ Alert dipasang: **{ticker}** {condition} Rp{price:,.0f}\n"
                f"Kamu akan mendapat DM saat kondisi ini terpenuhi.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ Alert ini sudah ada.", ephemeral=True
            )


def _get_price(ticker: str) -> float:
    try:
        info = get_info(f"{ticker}.JK")
        return float(
            info.get("currentPrice") or
            info.get("regularMarketPrice") or
            info.get("ask") or 0
        )
    except YFinanceUnavailable:
        return 0.0
    except Exception:
        return 0.0


async def setup(bot):
    await bot.add_cog(AlertCog(bot))
