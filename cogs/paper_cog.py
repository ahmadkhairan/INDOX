"""Discord cog untuk paper trading tracker.

Commands:
- !paper-enter TICKER HARGA SL TP1 TP2 [LOT]
- !paper-size TRADE_ID LOT
- !paper-close TRADE_ID HARGA
- !paper-cancel TRADE_ID
- !paper-list          (open trades)
- !paper-stats         (full report)
- !paper-check         (manual price check)
"""
import asyncio
import discord
from discord.ext import commands
from discord import app_commands

from execution.paper_trader import get_paper_trader
from utils.helpers import send_long, split_msg
from utils.ticker_utils import normalize_ticker


class PaperCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Prefix command ──────────────────────────────────────────
    @commands.group(name="paper", aliases=["pp", "paper_trade"], invoke_without_command=True)
    async def cmd_paper(self, ctx):
        await ctx.reply(
            "📊 **Paper Trading Commands**\n"
            "• `!paper enter TICKER HARGA SL TP1 TP2 [LOT]` — catat entry\n"
            "• `!paper size ID LOT` — set ukuran lot\n"
            "• `!paper close ID HARGA` — tutup manual\n"
            "• `!paper cancel ID` — hapus entry\n"
            "• `!paper list` — lihat OPEN trades\n"
            "• `!paper stats` — lihat performa\n"
            "• `!paper check` — auto-check harga sekarang"
        )

    @cmd_paper.command(name="enter", aliases=["e", "buy"])
    async def cmd_paper_enter(self, ctx, ticker: str, entry: float,
                              sl: float, tp1: float, tp2: float, lots: int = 0):
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return

        try:
            pt = get_paper_trader()
            trade_id = pt.enter(
                ticker=ticker, entry_price=entry,
                sl=sl, tp1=tp1, tp2=tp2,
                qty_lots=lots, source="discord",
            )
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return

        risk_pct = round((entry - sl) / entry * 100, 2)
        reward_pct = round((tp2 - entry) / entry * 100, 2)
        rr = round((tp1 - entry) / max(entry - sl, 1), 2)

        msg = (
            f"✅ **Paper Trade #{trade_id}**\n"
            f"📈 {ticker} @ Rp{entry:,.0f}\n"
            f"🛑 SL: Rp{sl:,.0f} ({risk_pct}%)\n"
            f"🎯 TP1: Rp{tp1:,.0f} | TP2: Rp{tp2:,.0f} (+{reward_pct}%)\n"
            f"⚖️ R/R: 1:{rr}\n"
            f"📦 Size: {lots} lot"
        )
        await ctx.reply(msg)

    @cmd_paper.command(name="size", aliases=["sz"])
    async def cmd_paper_size(self, ctx, trade_id: str, lots: int):
        pt = get_paper_trader()
        if pt.set_size(trade_id, lots):
            await ctx.reply(f"✅ Trade {trade_id} size updated: {lots} lot")
        else:
            await ctx.reply(f"❌ Trade {trade_id} tidak ditemukan")

    @cmd_paper.command(name="close", aliases=["c"])
    async def cmd_paper_close(self, ctx, trade_id: str, price: float):
        pt = get_paper_trader()
        trade = pt.get_trade(trade_id)
        if not trade:
            await ctx.reply(f"❌ Trade {trade_id} tidak ditemukan")
            return
        if trade.status == "CLOSED":
            await ctx.reply(f"⚠️ Trade {trade_id} sudah closed")
            return

        if pt.close_trade(trade_id, price, reason="MANUAL"):
            pnl_pct = (price - trade.entry_price) / trade.entry_price * 100
            emoji = "🟢" if pnl_pct > 0 else "🔴"
            await ctx.reply(
                f"{emoji} **Trade {trade_id} CLOSED**\n"
                f"Exit @ Rp{price:,.0f} | PnL: {pnl_pct:+.2f}%"
            )
        else:
            await ctx.reply(f"❌ Gagal close trade {trade_id}")

    @cmd_paper.command(name="cancel", aliases=["x", "del"])
    async def cmd_paper_cancel(self, ctx, trade_id: str):
        pt = get_paper_trader()
        if pt.cancel_trade(trade_id):
            await ctx.reply(f"🗑️ Trade {trade_id} dihapus")
        else:
            await ctx.reply(f"❌ Trade {trade_id} tidak ditemukan / sudah closed")

    @cmd_paper.command(name="list", aliases=["l", "open"])
    async def cmd_paper_list(self, ctx):
        pt = get_paper_trader()
        open_trades = pt.get_open_trades()
        if not open_trades:
            await ctx.reply("📭 Tidak ada paper trade yang OPEN")
            return

        lines = [f"📋 **OPEN Paper Trades ({len(open_trades)})**"]
        for t in open_trades[:15]:
            status_icon = "🟢" if t.status == "TP1_HIT" else "🔵"
            lines.append(
                f"\n{status_icon} **{t.id}** — {t.ticker}\n"
                f"   Entry: Rp{t.entry_price:,.0f} | "
                f"SL: Rp{t.sl:,.0f} | "
                f"TP1: Rp{t.tp1:,.0f} | TP2: Rp{t.tp2:,.0f}\n"
                f"   Status: {t.status} | {t.qty_lots} lot | {t.source}"
            )

        if len(open_trades) > 15:
            lines.append(f"\n... dan {len(open_trades) - 15} lainnya")

        await send_long(ctx.channel, "\n".join(lines), reply_to=ctx.message)

    @cmd_paper.command(name="stats", aliases=["st", "report"])
    async def cmd_paper_stats(self, ctx):
        pt = get_paper_trader()
        report = pt.format_report()
        await send_long(ctx.channel, report, reply_to=ctx.message)

    @cmd_paper.command(name="check")
    async def cmd_paper_check(self, ctx):
        """Cek semua trade OPEN terhadap harga IDX sekarang."""
        pt = get_paper_trader()
        open_trades = pt.get_open_trades()
        if not open_trades:
            await ctx.reply("📭 Tidak ada trade OPEN untuk dicek")
            return

        msg = await ctx.reply(f"⏳ Mengecek {len(open_trades)} trades...")

        try:
            from utils.idx_api import get_idx_source
            idx = get_idx_source()
            tickers = list({t.ticker for t in open_trades})

            price_lookup = {}
            async def fetch_one(t):
                try:
                    summary = await idx.get_daily_summary(t)
                    if summary and summary.close > 0:
                        price_lookup[t] = summary.close
                except Exception:
                    pass

            await asyncio.gather(*[fetch_one(t) for t in tickers])

            triggered = pt.check_all(price_lookup)

            if triggered:
                lines = [f"🔔 **{len(triggered)} trades triggered!**"]
                for t in triggered:
                    emoji = "🟢" if t["pnl_pct"] > 0 else "🔴"
                    lines.append(
                        f"{emoji} {t['ticker']} — {t['reason']} @ Rp{t['exit_price']:,.0f} "
                        f"(PnL: {t['pnl_pct']:+.2f}%)"
                    )
                await msg.edit(content="\n".join(lines))
            else:
                no_data = [t.ticker for t in open_trades if t.ticker not in price_lookup]
                status_msg = f"✅ Semua {len(open_trades)} trades masih dalam batas SL/TP"
                if no_data:
                    status_msg += f"\n⚠️ Tidak ada data harga: {', '.join(no_data)}"
                await msg.edit(content=status_msg)

        except Exception as exc:
            await msg.edit(content=f"❌ Error saat check: {exc}")

    @commands.command(name="catat", aliases=["logtrade", "realtrade"])
    async def cmd_catat(self, ctx, ticker: str, action: str, price: float, lots: int, *, notes: str = ""):
        """!catat TICKER BUY/SELL HARGA LOT [CATATAN] — Catat trade real broker"""
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return

        act = action.upper()
        if act not in ("BUY", "SELL"):
            await ctx.reply("❌ Action harus BUY atau SELL")
            return

        from execution.journal import get_trade_journal
        j = get_trade_journal()
        trade = j.enter_trade(ticker, act, price, lots, notes)

        icon = "🟢 BUY" if act == "BUY" else "🔴 SELL"
        await ctx.reply(
            f"📝 **Real Trade Logged (#{trade.id})**\n"
            f"{icon} **{ticker}** @ Rp{price:,.0f} | {lots} lot\n"
            f"🕒 {trade.timestamp[:16].replace('T', ' ')}"
            + (f"\n💡 {notes}" if notes else "")
        )

    # ── Slash commands ──────────────────────────────────────────
    @app_commands.command(name="catat", description="Catat trade real yang dieksekusi di broker")
    @app_commands.describe(ticker="Ticker saham", action="BUY atau SELL", price="Harga eksekusi", lots="Jumlah lot", notes="Catatan opsional")
    @app_commands.choices(action=[
        app_commands.Choice(name="BUY", value="BUY"),
        app_commands.Choice(name="SELL", value="SELL"),
    ])
    async def slash_catat(self, interaction: discord.Interaction, ticker: str, action: str, price: float, lots: int, notes: str = ""):
        try:
            tk = normalize_ticker(ticker)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}")
            return

        from execution.journal import get_trade_journal
        j = get_trade_journal()
        trade = j.enter_trade(tk, action, price, lots, notes)

        icon = "🟢 BUY" if action == "BUY" else "🔴 SELL"
        await interaction.response.send_message(
            f"📝 **Real Trade Logged (`{trade.id}`)**\n"
            f"{icon} **{tk}** @ Rp{price:,.0f} | {lots} lot\n"
            f"🕒 {trade.timestamp[:16].replace('T', ' ')}"
            + (f"\n💡 {notes}" if notes else "")
        )

    @app_commands.command(name="paper", description="Paper trading tracker")
    @app_commands.describe(action="Action", ticker="Ticker", entry="Entry price", sl="Stop loss",
                          tp1="Take profit 1", tp2="Take profit 2", lots="Lot size",
                          trade_id="Trade ID", price="Current price")
    @app_commands.choices(action=[
        app_commands.Choice(name="enter", value="enter"),
        app_commands.Choice(name="list", value="list"),
        app_commands.Choice(name="stats", value="stats"),
        app_commands.Choice(name="check", value="check"),
        app_commands.Choice(name="close", value="close"),
        app_commands.Choice(name="cancel", value="cancel"),
    ])
    async def slash_paper(
        self, interaction: discord.Interaction, action: str,
        ticker: str = "", entry: float = 0.0,
        sl: float = 0.0, tp1: float = 0.0, tp2: float = 0.0, lots: int = 0,
        trade_id: str = "", price: float = 0.0,
    ):
        await interaction.response.defer(thinking=True)


        if action == "enter":
            if not all([ticker, entry, sl, tp1, tp2]):
                await interaction.followup.send("❌ Butuh: ticker, entry, sl, tp1, tp2")
                return
            try:
                tk = normalize_ticker(ticker)
            except ValueError as exc:
                await interaction.followup.send(f"❌ {exc}")
                return
            try:
                pt = get_paper_trader()
                tid = pt.enter(tk, entry, sl, tp1, tp2, qty_lots=lots)
            except ValueError as exc:
                await interaction.followup.send(f"❌ {exc}")
                return
            await interaction.followup.send(
                f"✅ Paper trade `{tid}`\n"
                f"📈 {tk} @ Rp{entry:,.0f} | SL {sl:,.0f} | TP1 {tp1:,.0f} | TP2 {tp2:,.0f}\n"
                f"📦 {lots} lot"
            )

        elif action == "list":
            pt = get_paper_trader()
            open_trades = pt.get_open_trades()
            if not open_trades:
                await interaction.followup.send("📭 Tidak ada trade OPEN")
                return
            lines = [f"📋 **OPEN ({len(open_trades)})**"]
            for t in open_trades[:10]:
                lines.append(f"`{t.id}` {t.ticker} @ Rp{t.entry_price:,.0f} [{t.status}]")
            await interaction.followup.send("\n".join(lines))

        elif action == "stats":
            pt = get_paper_trader()
            await interaction.followup.send(pt.format_report())

        elif action == "check":
            pt = get_paper_trader()
            open_trades = pt.get_open_trades()
            if not open_trades:
                await interaction.followup.send("📭 Tidak ada trade OPEN")
                return
            try:
                from utils.idx_api import get_idx_source
                idx = get_idx_source()
                tickers = list({t.ticker for t in open_trades})
                price_lookup = {}
                async def fetch_one(t):
                    try:
                        s = await idx.get_daily_summary(t)
                        if s and s.close > 0:
                            price_lookup[t] = s.close
                    except Exception:
                        pass
                await asyncio.gather(*[fetch_one(t) for t in tickers])
                triggered = pt.check_all(price_lookup)
                if triggered:
                    lines = [f"🔔 **{len(triggered)} triggered**"]
                    for t in triggered:
                        lines.append(f"`{t['ticker']}` {t['reason']} PnL {t['pnl_pct']:+.2f}%")
                    await interaction.followup.send("\n".join(lines))
                else:
                    await interaction.followup.send(f"✅ Semua {len(open_trades)} trades aman")
            except Exception as exc:
                await interaction.followup.send(f"❌ {exc}")

        elif action == "close":
            if not trade_id or price <= 0:
                await interaction.followup.send("❌ Butuh: trade_id, price")
                return
            pt = get_paper_trader()
            if pt.close_trade(trade_id, price, reason="SLASH"):
                trade = pt.get_trade(trade_id)
                pnl = trade.pnl_pct if trade else 0
                emoji = "🟢" if pnl > 0 else "🔴"
                await interaction.followup.send(
                    f"{emoji} Trade `{trade_id}` closed | PnL {pnl:+.2f}%"
                )
            else:
                await interaction.followup.send(f"❌ Trade `{trade_id}` tidak ditemukan / sudah closed")

        elif action == "cancel":
            if not trade_id:
                await interaction.followup.send("❌ Butuh: trade_id")
                return
            pt = get_paper_trader()
            if pt.cancel_trade(trade_id):
                await interaction.followup.send(f"🗑️ Trade `{trade_id}` dihapus")
            else:
                await interaction.followup.send(f"❌ Gagal cancel `{trade_id}`")


async def setup(bot):
    await bot.add_cog(PaperCog(bot))
