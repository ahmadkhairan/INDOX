from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from config import MC_SIMULATIONS, WF_IN_SAMPLE_MONTHS, WF_OUT_SAMPLE_MONTHS, WF_STEP_MONTHS
from utils.command_limits import BACKTEST_COOLDOWN
from utils.error_utils import user_error_message
from utils.logger import get_logger
from utils.ticker_utils import normalize_ticker
log = get_logger("cog.backtest")

class BacktestCog(commands.Cog, name="Backtest v4"):
    def __init__(self, bot): self.bot = bot

    @commands.command(name="backtest", aliases=["bt"])
    @commands.cooldown(*BACKTEST_COOLDOWN, commands.BucketType.user)
    async def bt_cmd(self, ctx, ticker: str = None, bulan: str = "3"):
        """!backtest BBCA [bulan] — Backtest ATR dynamic + Monte Carlo"""
        if not ticker:
            await ctx.reply("❌ Format: `!backtest TICKER [BULAN]`\nContoh: `!backtest BBCA 3`"); return
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return
        try: months = max(1, min(int(bulan), 12))
        except: months = 3
        msg = await ctx.reply(f"⚙️ Backtesting **{ticker}** ({months} bulan) + Monte Carlo...")
        try:
            from backtest.engine_backtest import run_backtest_v4
            from core.ai_engine import analyze_backtest
            from utils.helpers import send_long
            await msg.edit(content=f"⚙️ Mengambil data historis & menjalankan backtest **{ticker}**...")
            res = await run_backtest_v4(ticker, months=months)
            if "error" in res: await msg.edit(content=f"❌ {res['error']}"); return
            wr_e = "🟢" if res["win_rate"]>=60 else ("🟡" if res["win_rate"]>=45 else "🔴")
            mc = res.get("monte_carlo") or {}
            mc_t = ""
            if mc.get("median_return") is not None:
                mc_t = (f"MC ({mc.get('n_simulations',0):,} sim):\n"
                        f"Median {mc['median_return']:+.2f}% | P5 {mc.get('p5_return',0):+.2f}% | P95 {mc.get('p95_return',0):+.2f}%\n"
                        f"Prob Positif {mc.get('probability_positive',0):.1f}% | Conf: {mc.get('confidence','N/A')} | Block {mc.get('block_size','N/A')}\n")
            entry_stats = res.get("entry_breakdown", {})
            sizing = res.get("position_sizing_example") or {}
            summary = (f"📊 **BACKTEST v4: {ticker}** — {res['period']}\n"
                       f"_ATR SL×{res.get('sl_mult',1.5)} / TP1×{res.get('tp_mult',2.0)} / TP2×{res.get('tp_mult',2.0)+1.0} | Multi-entry {res.get('entry_mode','all')} | Dynamic exit + Adaptive risk_\n"
                       f"```\n"
                       f"Total Trade   : {res['total_trades']}\n"
                       f"Win Rate      : {wr_e} {res['win_rate']:.1f}%\n"
                       f"Profit Factor : {res['profit_factor']:.2f}\n"
                       f"Total Return  : {res['total_return']:+.2f}%\n"
                       f"Max Drawdown  : {res['max_drawdown']:.2f}%\n"
                       f"Sharpe Ratio  : {res['sharpe']:.3f}\n"
                       f"Avg Win       : +{res['avg_win']:.2f}%\n"
                       f"Avg Loss      : -{res['avg_loss']:.2f}%\n"
                       f"Avg Cond      : {res.get('avg_cond_met',0):.2f}\n"
                       f"Regime        : {res.get('regime_state','N/A')}\n"
                       f"Risk/Trade    : {res.get('risk_per_trade_pct',0):.2f}% | Kelly {res.get('kelly_fraction_pct',0):.2f}%\n"
                       f"Entries       : M {entry_stats.get('MOMENTUM',0)} | P {entry_stats.get('PULLBACK',0)} | B {entry_stats.get('BREAKOUT',0)}\n"
                       f"Lot Example   : {sizing.get('lots',0)} lot | Pos {sizing.get('position_pct',0):.2f}% | Risk {sizing.get('risk_pct_actual',0):.2f}%\n"
                       f"{'─'*38}\n"
                       f"{mc_t}"
                       f"```")
            await msg.edit(content=f"⚙️ Backtest selesai. Menyusun evaluasi AI untuk **{ticker}**...")
            ai_eval = analyze_backtest(ticker, res)
            await msg.delete()
            await send_long(ctx.channel, summary + "\n\n" + ai_eval, reply_to=ctx.message)
        except Exception as exc: await msg.edit(content=f"❌ {user_error_message(exc)}")

    @app_commands.command(name="backtest", description="Backtest ATR dynamic + Monte Carlo")
    @app_commands.describe(ticker="Kode saham IDX", bulan="Periode backtest 1-12 bulan")
    @app_commands.checks.cooldown(*BACKTEST_COOLDOWN)
    async def slash_backtest(self, interaction: discord.Interaction, ticker: str, bulan: int = 3):
        await interaction.response.defer(thinking=True)
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        months = max(1, min(bulan, 12))
        await interaction.edit_original_response(content=f"⚙️ Mengambil data historis & menjalankan backtest **{ticker}**...")
        try:
            from backtest.engine_backtest import run_backtest_v4
            from core.ai_engine import analyze_backtest
            res = await run_backtest_v4(ticker, months=months)
            if "error" in res:
                await interaction.edit_original_response(content=f"❌ {res['error']}")
                return
            summary = self._build_backtest_summary(ticker, res)
            await interaction.edit_original_response(content=f"⚙️ Backtest selesai. Menyusun evaluasi AI untuk **{ticker}**...")
            ai_eval = analyze_backtest(ticker, res)
            await self._send_slash_long(interaction, summary + "\n\n" + ai_eval)
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ {user_error_message(exc)}")

    @commands.command(name="walkforward", aliases=["wf"])
    @commands.cooldown(*BACKTEST_COOLDOWN, commands.BucketType.user)
    async def wf_cmd(self, ctx, ticker: str = None, years: str = "2"):
        """!walkforward BBCA [years] — Walk-Forward robustness test"""
        if not ticker: await ctx.reply("❌ Contoh: `!walkforward BBCA 2`"); return
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return
        try: yr = max(1, min(int(years), 5))
        except: yr = 2
        msg = await ctx.reply(f"⚙️ Walk-forward **{ticker}** ({yr} tahun)... (~40 detik)")
        try:
            from backtest.engine_backtest import get_wf_engine
            await msg.edit(content=f"⚙️ Menjalankan walk-forward window demi window untuk **{ticker}**...")
            result = await get_wf_engine().run(ticker, years=yr)
            emb = self._build_walkforward_embed(ticker, result)
            await msg.edit(content=None, embed=emb)
        except Exception as exc: await msg.edit(content=f"❌ {user_error_message(exc)}")

    @app_commands.command(name="walkforward", description="Walk-forward robustness test")
    @app_commands.describe(ticker="Kode saham IDX", years="Rentang historis 1-5 tahun")
    @app_commands.checks.cooldown(*BACKTEST_COOLDOWN)
    async def slash_walkforward(self, interaction: discord.Interaction, ticker: str, years: int = 2):
        await interaction.response.defer(thinking=True)
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        yr = max(1, min(years, 5))
        await interaction.edit_original_response(content=f"⚙️ Menjalankan walk-forward window demi window untuk **{ticker}**...")
        try:
            from backtest.engine_backtest import get_wf_engine
            result = await get_wf_engine().run(ticker, years=yr)
            emb = self._build_walkforward_embed(ticker, result)
            await interaction.edit_original_response(content=None, embed=emb)
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ {user_error_message(exc)}")

    @commands.command(name="montecarlo", aliases=["mc"])
    @commands.cooldown(*BACKTEST_COOLDOWN, commands.BucketType.user)
    async def mc_cmd(self, ctx, ticker: str = None, months: str = "6"):
        """!montecarlo BBCA [months] — Monte Carlo return distribution"""
        if not ticker: await ctx.reply("❌ Contoh: `!montecarlo BBCA 6`"); return
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return
        try: mo = max(3, min(int(months), 24))
        except: mo = 6
        msg = await ctx.reply(f"🎲 Monte Carlo **{ticker}** ({mo} bulan data)...")
        try:
            from backtest.engine_backtest import SingleBT, MonteCarloEngine
            from datetime import datetime, timedelta
            from utils.yf_guard import get_history
            end = datetime.now(); start = end - timedelta(days=mo*31+90)
            await msg.edit(content=f"🎲 Mengambil data historis **{ticker}** untuk simulasi Monte Carlo...")
            df = get_history(f"{ticker}.JK", start=start, end=end, auto_adjust=True)
            if df.empty or len(df)<50: await msg.edit(content=f"❌ Data tidak cukup untuk {ticker}"); return
            df.columns = [c.strip() for c in df.columns]
            bt = SingleBT(); res = bt.run(df, ticker=ticker)
            if not res.trades:
                await msg.edit(content=f"⚠️ Tidak ada trade dalam {mo} bulan untuk {ticker}.\nCoba perpanjang periode."); return
            await msg.edit(content=f"🎲 Menjalankan {MC_SIMULATIONS:,} simulasi Monte Carlo untuk **{ticker}**...")
            mcr = await MonteCarloEngine().run(res.trades)
            color = (discord.Color.green() if mcr.prob_positive>65 else
                     discord.Color.gold() if mcr.prob_positive>45 else discord.Color.red())
            emb = discord.Embed(title=f"🎲 Monte Carlo — {ticker} ({mcr.n_sim:,} sim)",
                                description=f"Confidence: **{mcr.confidence}** | Dari {res.total_trades} trades historis", color=color)
            emb.add_field(name="📊 Return Distribution",
                value=f"P5   : {mcr.p5:+.2f}% (worst)\nP25  : {mcr.p25:+.2f}%\nMedian: **{mcr.median:+.2f}%**\nP75  : {mcr.p75:+.2f}%\nP95  : {mcr.p95:+.2f}% (best)", inline=True)
            emb.add_field(name="🎯 Probabilitas",
                value=f"Return>0%  : **{mcr.prob_positive:.1f}%**\nReturn>10% : **{mcr.prob_above_10:.1f}%**\nSharpe median: **{mcr.sharpe_median:.3f}**", inline=True)
            emb.add_field(name="📉 Drawdown Risk",
                value=f"Max DD median: {mcr.mdd_median:.2f}%\nMax DD P95   : **{mcr.mdd_p95:.2f}%**", inline=True)
            interp = ("✅ Distribusi favorabel" if mcr.prob_positive>65 and mcr.mdd_p95>-20
                      else "🟡 Borderline — perhatikan drawdown" if mcr.prob_positive>50
                      else "⚠️ Probability negatif tinggi — optimasi strategi dulu")
            emb.add_field(name="💡 Assessment", value=interp, inline=False)
            emb.set_footer(text=f"Block bootstrap adaptif ({mcr.block_size}) | adaptive risk sizing | IDX Bot v4")
            await msg.edit(content=None, embed=emb)
        except Exception as exc: await msg.edit(content=f"❌ {user_error_message(exc)}")

    @app_commands.command(name="montecarlo", description="Monte Carlo distribution dari trade historis")
    @app_commands.describe(ticker="Kode saham IDX", months="Lookback 3-24 bulan")
    @app_commands.checks.cooldown(*BACKTEST_COOLDOWN)
    async def slash_montecarlo(self, interaction: discord.Interaction, ticker: str, months: int = 6):
        await interaction.response.defer(thinking=True)
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        mo = max(3, min(months, 24))
        try:
            from backtest.engine_backtest import SingleBT, MonteCarloEngine
            from datetime import datetime, timedelta
            from utils.yf_guard import get_history
            end = datetime.now()
            start = end - timedelta(days=mo * 31 + 90)
            await interaction.edit_original_response(content=f"🎲 Mengambil data historis **{ticker}** untuk simulasi Monte Carlo...")
            df = get_history(f"{ticker}.JK", start=start, end=end, auto_adjust=True)
            if df.empty or len(df) < 50:
                await interaction.edit_original_response(content=f"❌ Data tidak cukup untuk {ticker}")
                return
            df.columns = [c.strip() for c in df.columns]
            bt = SingleBT()
            res = bt.run(df, ticker=ticker)
            if not res.trades:
                await interaction.edit_original_response(content=f"⚠️ Tidak ada trade dalam {mo} bulan untuk {ticker}. Coba perpanjang periode.")
                return
            await interaction.edit_original_response(content=f"🎲 Menjalankan {MC_SIMULATIONS:,} simulasi Monte Carlo untuk **{ticker}**...")
            mcr = await MonteCarloEngine().run(res.trades)
            emb = self._build_mc_embed(ticker, res.total_trades, mcr)
            await interaction.edit_original_response(content=None, embed=emb)
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ {user_error_message(exc)}")

    def _build_backtest_summary(self, ticker: str, res: dict) -> str:
        wr_e = "🟢" if res["win_rate"] >= 60 else ("🟡" if res["win_rate"] >= 45 else "🔴")
        mc = res.get("monte_carlo") or {}
        mc_t = ""
        if mc.get("median_return") is not None:
            mc_t = (
                f"MC ({mc.get('n_simulations',0):,} sim):\n"
                f"Median {mc['median_return']:+.2f}% | P5 {mc.get('p5_return',0):+.2f}% | P95 {mc.get('p95_return',0):+.2f}%\n"
                f"Prob Positif {mc.get('probability_positive',0):.1f}% | Conf: {mc.get('confidence','N/A')} | Block {mc.get('block_size','N/A')}\n"
            )
        return (
            f"📊 **BACKTEST v4: {ticker}** — {res['period']}\n"
            f"_ATR SL×{res.get('sl_mult',1.5)} / TP1×{res.get('tp_mult',2.0)} / TP2×{res.get('tp_mult',2.0)+1.0} | Multi-entry {res.get('entry_mode','all')} | Dynamic exit + Adaptive risk_\n"
            f"```\n"
            f"Total Trade   : {res['total_trades']}\n"
            f"Win Rate      : {wr_e} {res['win_rate']:.1f}%\n"
            f"Profit Factor : {res['profit_factor']:.2f}\n"
            f"Total Return  : {res['total_return']:+.2f}%\n"
            f"Max Drawdown  : {res['max_drawdown']:.2f}%\n"
            f"Sharpe Ratio  : {res['sharpe']:.3f}\n"
            f"Avg Win       : +{res['avg_win']:.2f}%\n"
            f"Avg Loss      : -{res['avg_loss']:.2f}%\n"
            f"Avg Cond      : {res.get('avg_cond_met',0):.2f}\n"
            f"Regime        : {res.get('regime_state','N/A')}\n"
            f"Risk/Trade    : {res.get('risk_per_trade_pct',0):.2f}% | Kelly {res.get('kelly_fraction_pct',0):.2f}%\n"
            f"Entries       : M {res.get('entry_breakdown',{}).get('MOMENTUM',0)} | P {res.get('entry_breakdown',{}).get('PULLBACK',0)} | B {res.get('entry_breakdown',{}).get('BREAKOUT',0)}\n"
            f"{'─'*38}\n"
            f"{mc_t}"
            f"```"
        )

    def _build_walkforward_embed(self, ticker: str, result) -> discord.Embed:
        color = (discord.Color.green() if result.is_robust else
                 discord.Color.orange() if result.robustness > 0.5 else discord.Color.red())
        label = ("✅ ROBUST" if result.is_robust else
                 "⚠️ SEMI-ROBUST" if result.robustness > 0.5 else "❌ TIDAK ROBUST")
        emb = discord.Embed(
            title=f"📊 Walk-Forward — {ticker}",
            description=f"**{label}** | Robustness: **{result.robustness:.3f}**",
            color=color,
        )
        oos = result.oos_agg
        iis = result.is_agg
        emb.add_field(
            name="✅ In-Sample",
            value=f"Trades:{iis.total_trades}\nWin Rate:**{iis.win_rate:.1f}%**\nPF:**{iis.pf:.2f}**\nReturn:{iis.total_return:.2f}%\nMaxDD:{iis.max_dd:.2f}%\nSharpe:{iis.sharpe:.3f}",
            inline=True,
        )
        emb.add_field(
            name="🧪 Out-of-Sample",
            value=f"Trades:{oos.total_trades}\nWin Rate:**{oos.win_rate:.1f}%**\nPF:**{oos.pf:.2f}**\nReturn:{oos.total_return:.2f}%\nMaxDD:{oos.max_dd:.2f}%\nSharpe:{oos.sharpe:.3f}",
            inline=True,
        )
        emb.add_field(
            name="📐 Config",
            value=(
                f"IS:{WF_IN_SAMPLE_MONTHS}m | OOS:{WF_OUT_SAMPLE_MONTHS}m | Step:{WF_STEP_MONTHS}m | Windows:{len(result.oos_periods)}\n"
                f"{self._format_wf_params(result)}"
            ),
            inline=False,
        )
        if result.recommendations:
            emb.add_field(name="💡 Rekomendasi", value="\n".join(result.recommendations[:4]), inline=False)
        emb.set_footer(text="Multi-entry + dynamic exit + adaptive risk | IDX Bot v4")
        return emb

    def _format_wf_params(self, result) -> str:
        params = [getattr(period, "params", {}) for period in result.oos_periods if getattr(period, "params", None)]
        if not params:
            return "Optimized Params: default"
        sl_values = sorted({float(item.get("sl_mult", 2.0)) for item in params})
        tp_values = sorted({float(item.get("tp1_mult", 3.0)) for item in params})
        hold_values = sorted({int(item.get("hold_days", 8)) for item in params})
        cond_values = sorted({int(item.get("min_cond", 3)) for item in params})
        entry_modes = sorted({str(item.get("entry_mode", "all")) for item in params})
        return (
            f"Optimized Params: SL {self._format_param_values(sl_values)} | "
            f"TP {self._format_param_values(tp_values)} | Hold {self._format_param_values(hold_values)}d | "
            f"MinCond {self._format_param_values(cond_values)} | Mode {'/'.join(entry_modes)}"
        )

    def _format_param_values(self, values) -> str:
        if not values:
            return "default"
        if len(values) == 1:
            value = values[0]
            return f"{value:g}" if isinstance(value, float) else str(value)
        first, last = values[0], values[-1]
        if isinstance(first, float) or isinstance(last, float):
            return f"{first:g}-{last:g}"
        return f"{first}-{last}"

    def _build_mc_embed(self, ticker: str, total_trades: int, mcr) -> discord.Embed:
        color = (discord.Color.green() if mcr.prob_positive > 65 else
                 discord.Color.gold() if mcr.prob_positive > 45 else discord.Color.red())
        emb = discord.Embed(
            title=f"🎲 Monte Carlo — {ticker} ({mcr.n_sim:,} sim)",
            description=f"Confidence: **{mcr.confidence}** | Dari {total_trades} trades historis",
            color=color,
        )
        emb.add_field(
            name="📊 Return Distribution",
            value=f"P5   : {mcr.p5:+.2f}% (worst)\nP25  : {mcr.p25:+.2f}%\nMedian: **{mcr.median:+.2f}%**\nP75  : {mcr.p75:+.2f}%\nP95  : {mcr.p95:+.2f}% (best)",
            inline=True,
        )
        emb.add_field(
            name="🎯 Probabilitas",
            value=f"Return>0%  : **{mcr.prob_positive:.1f}%**\nReturn>10% : **{mcr.prob_above_10:.1f}%**\nSharpe median: **{mcr.sharpe_median:.3f}**",
            inline=True,
        )
        emb.add_field(
            name="📉 Drawdown Risk",
            value=f"Max DD median: {mcr.mdd_median:.2f}%\nMax DD P95   : **{mcr.mdd_p95:.2f}%**",
            inline=True,
        )
        interp = ("✅ Distribusi favorabel" if mcr.prob_positive > 65 and mcr.mdd_p95 > -20
                  else "🟡 Borderline — perhatikan drawdown" if mcr.prob_positive > 50
                  else "⚠️ Probability negatif tinggi — optimasi strategi dulu")
        emb.add_field(name="💡 Assessment", value=interp, inline=False)
        emb.set_footer(text=f"Block bootstrap adaptif ({mcr.block_size}) | adaptive risk sizing | IDX Bot v4")
        return emb

    async def _send_slash_long(self, interaction: discord.Interaction, content: str) -> None:
        from utils.helpers import split_msg

        chunks = split_msg(content)
        await interaction.edit_original_response(content=chunks[0])
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk)

async def setup(bot): await bot.add_cog(BacktestCog(bot))
