from __future__ import annotations
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from utils.command_limits import RISK_COOLDOWN
from utils.error_utils import user_error_message
from utils.logger import get_logger
from utils.runtime_cache import TTLCache
from utils.ticker_utils import normalize_ticker, normalize_tickers
log = get_logger("cog.risk")

RETURNS_CACHE_TTL_SECONDS = 120.0
RISK_RESULT_CACHE_TTL_SECONDS = 180.0
_returns_cache = TTLCache[object](max_entries=96)
_risk_result_cache = TTLCache[object](max_entries=96)

class RiskCog(commands.Cog, name="Risk Engine v4"):
    def __init__(self, bot): self.bot = bot

    def _parse_ticker_text(self, raw: str, max_items: int, min_items: int = 1) -> list[str]:
        tokens = [token for token in raw.replace(",", " ").split() if token.strip()]
        if len(tokens) < min_items:
            raise ValueError(f"Minimal {min_items} ticker.")
        return normalize_tickers(tokens[:max_items])

    async def _get_returns_series(self, ticker: str, period: str):
        cache_key = ("returns_series", ticker, period)
        cached = _returns_cache.get(cache_key)
        if cached is not None:
            return cached
        from utils.yf_guard import get_history
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(None, lambda: get_history(f"{ticker}.JK", period=period, auto_adjust=True))
        if df.empty or len(df) <= 20:
            return None
        series = df["Close"].pct_change().dropna()
        if series.empty:
            return None
        return _returns_cache.set(cache_key, series, RETURNS_CACHE_TTL_SECONDS)

    async def _get_returns_map(self, tickers: list[str], period: str) -> dict[str, object]:
        series_list = await asyncio.gather(*[
            self._get_returns_series(ticker, period) for ticker in tickers
        ])
        return {
            ticker: series
            for ticker, series in zip(tickers, series_list)
            if series is not None
        }

    async def _get_var_result(self, ticker: str, method: str):
        cache_key = ("var", ticker, method)
        cached = _risk_result_cache.get(cache_key)
        if cached is not None:
            return cached
        from risk.engine import get_risk_engine
        returns = await self._get_returns_series(ticker, "1y")
        if returns is None:
            return None
        result = await get_risk_engine().var(ticker, returns, method=method)
        return _risk_result_cache.set(cache_key, result, RISK_RESULT_CACHE_TTL_SECONDS)

    async def _get_corr_result(self, tickers: list[str]):
        cache_key = ("corr", tuple(tickers))
        cached = _risk_result_cache.get(cache_key)
        if cached is not None:
            return cached
        from risk.engine import get_risk_engine
        returns_map = await self._get_returns_map(tickers, "1y")
        if len(returns_map) < 2:
            return None
        result = await get_risk_engine().correlation(returns_map)
        return _risk_result_cache.set(cache_key, result, RISK_RESULT_CACHE_TTL_SECONDS)

    async def _get_stress_result(self, tickers: list[str]):
        cache_key = ("stress", tuple(sorted(tickers)))
        cached = _risk_result_cache.get(cache_key)
        if cached is not None:
            return cached
        from data.fetcher import get_sector_for_ticker
        from risk.engine import get_risk_engine
        holdings = [{"ticker": ticker, "weight": 1 / len(tickers), "sector": get_sector_for_ticker(ticker)} for ticker in tickers]
        result = await get_risk_engine().stress_test(holdings)
        return _risk_result_cache.set(cache_key, result, RISK_RESULT_CACHE_TTL_SECONDS)

    async def _get_optimize_result(self, method: str, tickers: list[str]):
        if len(tickers) < 2:
            return None
        cache_key = ("optimize", method, tuple(tickers))
        cached = _risk_result_cache.get(cache_key)
        if cached is not None:
            return cached
        import pandas as pd
        from risk.optimizer import get_optimizer
        returns_map = await self._get_returns_map(tickers, "2y")
        if len(returns_map) < 2:
            return None
        result = await get_optimizer().optimize(list(returns_map.keys()), pd.DataFrame(returns_map), method=method)
        return _risk_result_cache.set(cache_key, result, RISK_RESULT_CACHE_TTL_SECONDS)

    @commands.command(name="var")
    @commands.cooldown(*RISK_COOLDOWN, commands.BucketType.user)
    async def var_cmd(self, ctx, ticker: str, method: str = "historical"):
        """!var BBCA [historical|parametric] — Value-at-Risk"""
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return
        msg = await ctx.reply(f"⚙️ Menghitung VaR **{ticker}** ({method})...")
        try:
            await msg.edit(content=f"⚙️ Mengambil data historis **{ticker}** (1 tahun)...")
            await msg.edit(content=f"⚙️ Menghitung VaR **{ticker}** dengan metode `{method}`...")
            vr = await self._get_var_result(ticker, method)
            if vr is None: await msg.edit(content=f"❌ Data tidak tersedia untuk {ticker}"); return
            emb = discord.Embed(title=f"📊 VaR — {ticker}", color=discord.Color.orange())
            emb.add_field(name="🔴 VaR 1-Day", value=f"95% → **{vr.var_1d_95:.3f}%**\n99% → **{vr.var_1d_99:.3f}%**", inline=True)
            emb.add_field(name="🔥 CVaR (ES)", value=f"95% → **{vr.cvar_95:.3f}%**\n99% → **{vr.cvar_99:.3f}%**", inline=True)
            emb.add_field(name="📈 Vol Tahunan", value=f"**{vr.ann_vol:.2f}%**", inline=True)
            emb.add_field(name="💡 Interpretasi", value=f"Dengan confidence 95%, max kerugian 1 hari = **{vr.var_1d_95:.2f}%** dari posisi.", inline=False)
            emb.set_footer(text=f"Method: {method} | IDX Bot v4")
            await msg.edit(content=None, embed=emb)
        except Exception as exc: await msg.edit(content=f"❌ {user_error_message(exc)}")

    @app_commands.command(name="var", description="Value-at-Risk satu saham")
    @app_commands.describe(ticker="Kode saham IDX", method="historical atau parametric")
    @app_commands.choices(method=[
        app_commands.Choice(name="Historical", value="historical"),
        app_commands.Choice(name="Parametric", value="parametric"),
    ])
    @app_commands.checks.cooldown(*RISK_COOLDOWN)
    async def slash_var(self, interaction: discord.Interaction, ticker: str, method: str = "historical"):
        await interaction.response.defer(thinking=True)
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        try:
            await interaction.edit_original_response(content=f"⚙️ Mengambil data historis **{ticker}** (1 tahun)...")
            await interaction.edit_original_response(content=f"⚙️ Menghitung VaR **{ticker}** dengan metode `{method}`...")
            vr = await self._get_var_result(ticker, method)
            if vr is None:
                await interaction.edit_original_response(content=f"❌ Data tidak tersedia untuk {ticker}")
                return
            emb = discord.Embed(title=f"📊 VaR — {ticker}", color=discord.Color.orange())
            emb.add_field(name="🔴 VaR 1-Day", value=f"95% → **{vr.var_1d_95:.3f}%**\n99% → **{vr.var_1d_99:.3f}%**", inline=True)
            emb.add_field(name="🔥 CVaR (ES)", value=f"95% → **{vr.cvar_95:.3f}%**\n99% → **{vr.cvar_99:.3f}%**", inline=True)
            emb.add_field(name="📈 Vol Tahunan", value=f"**{vr.ann_vol:.2f}%**", inline=True)
            emb.add_field(name="💡 Interpretasi", value=f"Dengan confidence 95%, max kerugian 1 hari = **{vr.var_1d_95:.2f}%** dari posisi.", inline=False)
            emb.set_footer(text=f"Method: {method} | IDX Bot v4")
            await interaction.edit_original_response(content=None, embed=emb)
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ {user_error_message(exc)}")

    @commands.command(name="stress")
    @commands.cooldown(*RISK_COOLDOWN, commands.BucketType.user)
    async def stress_cmd(self, ctx, *tickers: str):
        """!stress BBCA BBRI TLKM — Stress test portfolio equal-weight (5 scenarios)"""
        if not tickers: await ctx.reply("❌ Contoh: `!stress BBCA BBRI TLKM ADRO`"); return
        try:
            t_list = normalize_tickers(list(tickers[:8]))
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return
        msg = await ctx.reply(f"⚙️ Stress test: {', '.join(t_list)}...")
        try:
            await msg.edit(content=f"⚙️ Menjalankan 5 skenario stress test untuk {', '.join(t_list)}...")
            results = await self._get_stress_result(t_list)
            emb = discord.Embed(title=f"🧨 Stress Test — {', '.join(t_list[:4])}{'...' if len(t_list)>4 else ''}",
                                description="Equal-weight | 5 Historical Scenarios", color=discord.Color.red())
            for r in results:
                emoji = "🔴" if r.impact_pct<-15 else ("🟡" if r.impact_pct<-8 else "🟢")
                emb.add_field(name=f"{emoji} {r.label}",
                    value=f"Impact: **{r.impact_pct:+.1f}%** | Worst: {r.worst_ticker} ({r.worst_impact:+.1f}%) | Recovery: ~{r.recovery_days}hr",
                    inline=False)
            emb.set_footer(text="Estimasi berdasarkan historical shock | IDX Bot v4")
            await msg.edit(content=None, embed=emb)
        except Exception as exc: await msg.edit(content=f"❌ {user_error_message(exc)}")

    @app_commands.command(name="stress", description="Stress test portfolio equal-weight")
    @app_commands.describe(tickers="Pisahkan dengan spasi atau koma. Contoh: BBCA BBRI TLKM ADRO")
    @app_commands.checks.cooldown(*RISK_COOLDOWN)
    async def slash_stress(self, interaction: discord.Interaction, tickers: str):
        await interaction.response.defer(thinking=True)
        try:
            t_list = self._parse_ticker_text(tickers, max_items=8, min_items=1)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        try:
            await interaction.edit_original_response(content=f"⚙️ Menjalankan 5 skenario stress test untuk {', '.join(t_list)}...")
            results = await self._get_stress_result(t_list)
            emb = discord.Embed(
                title=f"🧨 Stress Test — {', '.join(t_list[:4])}{'...' if len(t_list) > 4 else ''}",
                description="Equal-weight | 5 Historical Scenarios",
                color=discord.Color.red(),
            )
            for item in results:
                emoji = "🔴" if item.impact_pct < -15 else ("🟡" if item.impact_pct < -8 else "🟢")
                emb.add_field(
                    name=f"{emoji} {item.label}",
                    value=f"Impact: **{item.impact_pct:+.1f}%** | Worst: {item.worst_ticker} ({item.worst_impact:+.1f}%) | Recovery: ~{item.recovery_days}hr",
                    inline=False,
                )
            emb.set_footer(text="Estimasi berdasarkan historical shock | IDX Bot v4")
            await interaction.edit_original_response(content=None, embed=emb)
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ {user_error_message(exc)}")

    @commands.command(name="korrelasi", aliases=["corr"])
    @commands.cooldown(*RISK_COOLDOWN, commands.BucketType.user)
    async def corr_cmd(self, ctx, *tickers: str):
        """!korrelasi BBCA BBRI TLKM — Correlation matrix + diversification score"""
        if len(tickers)<2: await ctx.reply("❌ Minimal 2 ticker. Contoh: `!korrelasi BBCA BBRI TLKM`"); return
        try:
            t_list = normalize_tickers(list(tickers[:8]))
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return
        msg = await ctx.reply(f"⚙️ Correlation matrix: {', '.join(t_list)}...")
        try:
            await msg.edit(content=f"⚙️ Mengambil return historis untuk {len(t_list)} ticker...")
            result = await self._get_corr_result(t_list)
            if result is None: await msg.edit(content="❌ Data tidak cukup."); return
            await msg.edit(content="⚙️ Menghitung matriks korelasi & skor diversifikasi...")
            tks = result.tickers
            lines = ["```", "       " + "  ".join(f"{t[:5]:>5}" for t in tks)]
            for i, t1 in enumerate(tks):
                row = f"{t1[:5]:<5}  " + "  ".join(f"{result.matrix[i][j]:+.2f}" for j in range(len(tks)))
                lines.append(row)
            lines.append("```")
            emb = discord.Embed(title="📊 Correlation Matrix", description="\n".join(lines)[:1020], color=discord.Color.blue())
            if result.high_corr_pairs:
                emb.add_field(name="⚠️ Korelasi Tinggi (>0.85)",
                    value="\n".join(f"• **{p[0]}** ↔ **{p[1]}**: {p[2]:+.3f}" for p in result.high_corr_pairs[:5]), inline=False)
            emb.add_field(name="📊 Diversification Score",
                value=f"**{result.div_score:.3f}** / 1.00 | {'⚠️ Risiko konsentrasi' if result.concentration else '✅ Diversifikasi OK'}",
                inline=False)
            emb.set_footer(text="1 tahun data harian | IDX Bot v4")
            await msg.edit(content=None, embed=emb)
        except Exception as exc: await msg.edit(content=f"❌ {user_error_message(exc)}")

    @app_commands.command(name="corr", description="Correlation matrix dan diversification score")
    @app_commands.describe(tickers="Pisahkan dengan spasi atau koma. Minimal 2 ticker")
    @app_commands.checks.cooldown(*RISK_COOLDOWN)
    async def slash_corr(self, interaction: discord.Interaction, tickers: str):
        await interaction.response.defer(thinking=True)
        try:
            t_list = self._parse_ticker_text(tickers, max_items=8, min_items=2)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        try:
            await interaction.edit_original_response(content=f"⚙️ Mengambil return historis untuk {len(t_list)} ticker...")
            result = await self._get_corr_result(t_list)
            if result is None:
                await interaction.edit_original_response(content="❌ Data tidak cukup.")
                return
            await interaction.edit_original_response(content="⚙️ Menghitung matriks korelasi & skor diversifikasi...")
            tks = result.tickers
            lines = ["```", "       " + "  ".join(f"{t[:5]:>5}" for t in tks)]
            for i, t1 in enumerate(tks):
                row = f"{t1[:5]:<5}  " + "  ".join(f"{result.matrix[i][j]:+.2f}" for j in range(len(tks)))
                lines.append(row)
            lines.append("```")
            emb = discord.Embed(title="📊 Correlation Matrix", description="\n".join(lines)[:1020], color=discord.Color.blue())
            if result.high_corr_pairs:
                emb.add_field(
                    name="⚠️ Korelasi Tinggi (>0.85)",
                    value="\n".join(f"• **{p[0]}** ↔ **{p[1]}**: {p[2]:+.3f}" for p in result.high_corr_pairs[:5]),
                    inline=False,
                )
            emb.add_field(
                name="📊 Diversification Score",
                value=f"**{result.div_score:.3f}** / 1.00 | {'⚠️ Risiko konsentrasi' if result.concentration else '✅ Diversifikasi OK'}",
                inline=False,
            )
            emb.set_footer(text="1 tahun data harian | IDX Bot v4")
            await interaction.edit_original_response(content=None, embed=emb)
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ {user_error_message(exc)}")

    @commands.command(name="optimize")
    @commands.cooldown(*RISK_COOLDOWN, commands.BucketType.user)
    async def optimize_cmd(self, ctx, method: str, *tickers: str):
        """!optimize [black_litterman|cvar|mean_variance] BBCA BBRI TLKM"""
        if len(tickers) < 2:
            await ctx.reply("❌ Minimal 2 ticker. Format: `!optimize [method] BBCA BBRI`\nMethods: `black_litterman` | `cvar` | `mean_variance`"); return
        try:
            t_list = normalize_tickers(list(tickers[:10]))
        except ValueError as exc:
            await ctx.reply(f"❌ {exc}")
            return
        msg = await ctx.reply(f"⚙️ Optimizing ({method}): {', '.join(t_list)}...")
        try:
            await msg.edit(content=f"⚙️ Mengambil data historis 2 tahun untuk {len(t_list)} ticker...")
            res = await self._get_optimize_result(method, t_list)
            if res is None: await msg.edit(content="❌ Data tidak cukup."); return
            await msg.edit(content=f"⚙️ Menjalankan optimizer `{method}`...")
            wt = "\n".join(f"• **{t}**: {w*100:.1f}% (Kelly: {res.kelly_sizes.get(t,0):.1f}%)"
                            for t,w in sorted(res.weights.items(), key=lambda x:-x[1]))
            emb = discord.Embed(title=f"🎯 Optimizer — {method.replace('_',' ').title()}", color=discord.Color.green())
            emb.add_field(name="📊 Optimal Weights", value=wt[:1020], inline=False)
            emb.add_field(name="📈 Expected", value=f"Return: **{res.expected_return:.2f}%** pa\nVol: **{res.expected_vol:.2f}%** pa\nSharpe: **{res.sharpe:.3f}**\nCVaR 95%: **{res.cvar_95:.2f}%**", inline=True)
            if res.rebalance_trades:
                emb.add_field(name="🔄 Rebalancing",
                    value="\n".join(f"• {t['action']} **{t['ticker']}** {t['current_pct']:.1f}%→{t['target_pct']:.1f}% ({t['delta_pct']:+.1f}%)"
                                    for t in res.rebalance_trades[:6]), inline=False)
            if res.notes: emb.add_field(name="📌 Notes", value="\n".join(res.notes), inline=False)
            emb.set_footer(text="⚠️ Historical data. Bukan saran investasi. | IDX Bot v4")
            await msg.edit(content=None, embed=emb)
        except Exception as exc: await msg.edit(content=f"❌ {user_error_message(exc)}")

    @app_commands.command(name="optimize", description="Optimizer portfolio multi-saham")
    @app_commands.describe(
        method="Metode optimisasi",
        tickers="Pisahkan dengan spasi atau koma. Contoh: BBCA BBRI TLKM",
    )
    @app_commands.choices(method=[
        app_commands.Choice(name="Black-Litterman", value="black_litterman"),
        app_commands.Choice(name="CVaR", value="cvar"),
        app_commands.Choice(name="Mean Variance", value="mean_variance"),
    ])
    @app_commands.checks.cooldown(*RISK_COOLDOWN)
    async def slash_optimize(self, interaction: discord.Interaction, method: str, tickers: str):
        await interaction.response.defer(thinking=True)
        try:
            t_list = self._parse_ticker_text(tickers, max_items=10, min_items=2)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        try:
            await interaction.edit_original_response(content=f"⚙️ Mengambil data historis 2 tahun untuk {len(t_list)} ticker...")
            res = await self._get_optimize_result(method, t_list)
            if res is None:
                await interaction.edit_original_response(content="❌ Data tidak cukup.")
                return
            await interaction.edit_original_response(content=f"⚙️ Menjalankan optimizer `{method}`...")
            wt = "\n".join(
                f"• **{ticker}**: {weight*100:.1f}% (Kelly: {res.kelly_sizes.get(ticker,0):.1f}%)"
                for ticker, weight in sorted(res.weights.items(), key=lambda item: -item[1])
            )
            emb = discord.Embed(title=f"🎯 Optimizer — {method.replace('_', ' ').title()}", color=discord.Color.green())
            emb.add_field(name="📊 Optimal Weights", value=wt[:1020], inline=False)
            emb.add_field(
                name="📈 Expected",
                value=f"Return: **{res.expected_return:.2f}%** pa\nVol: **{res.expected_vol:.2f}%** pa\nSharpe: **{res.sharpe:.3f}**\nCVaR 95%: **{res.cvar_95:.2f}%**",
                inline=True,
            )
            if res.rebalance_trades:
                emb.add_field(
                    name="🔄 Rebalancing",
                    value="\n".join(
                        f"• {item['action']} **{item['ticker']}** {item['current_pct']:.1f}%→{item['target_pct']:.1f}% ({item['delta_pct']:+.1f}%)"
                        for item in res.rebalance_trades[:6]
                    ),
                    inline=False,
                )
            if res.notes:
                emb.add_field(name="📌 Notes", value="\n".join(res.notes), inline=False)
            emb.set_footer(text="⚠️ Historical data. Bukan saran investasi. | IDX Bot v4")
            await interaction.edit_original_response(content=None, embed=emb)
        except Exception as exc:
            await interaction.edit_original_response(content=f"❌ {user_error_message(exc)}")

    @commands.command(name="kelly")
    async def kelly_cmd(self, ctx, win_rate: float, avg_win: float, avg_loss: float):
        """!kelly 55 3.5 2.0 — Kelly position sizing"""
        from risk.optimizer import get_optimizer
        res = get_optimizer().kelly_size(win_rate, avg_win, avg_loss)
        emb = discord.Embed(title="🎲 Kelly Criterion", color=discord.Color.gold())
        emb.add_field(name="📥 Input", value=f"Win Rate: **{win_rate:.1f}%**\nAvg Win: **{avg_win:.2f}%**\nAvg Loss: **{avg_loss:.2f}%**", inline=True)
        emb.add_field(name="📊 Output", value=f"Full Kelly: **{res['kelly_full_pct']:.2f}%**\nFractional (25%): **{res['kelly_frac_pct']:.2f}%**\nRekomendasi: **{res['position_pct']:.2f}%** kapital", inline=True)
        interp = ("⚠️ Kecil — cek R/R & win rate" if res["position_pct"]<5
                  else "⚠️ Besar — pastikan strategi proven" if res["position_pct"]>20
                  else "✅ Range reasonable untuk swing trading")
        emb.add_field(name="💡", value=interp, inline=False)
        emb.set_footer(text="Fractional Kelly 25% | IDX Bot v4")
        await ctx.reply(embed=emb)

    @app_commands.command(name="kelly", description="Hitung ukuran posisi dengan Kelly Criterion")
    @app_commands.describe(win_rate="Win rate dalam persen", avg_win="Average win dalam persen", avg_loss="Average loss dalam persen")
    async def slash_kelly(self, interaction: discord.Interaction, win_rate: float, avg_win: float, avg_loss: float):
        from risk.optimizer import get_optimizer

        res = get_optimizer().kelly_size(win_rate, avg_win, avg_loss)
        emb = discord.Embed(title="🎲 Kelly Criterion", color=discord.Color.gold())
        emb.add_field(name="📥 Input", value=f"Win Rate: **{win_rate:.1f}%**\nAvg Win: **{avg_win:.2f}%**\nAvg Loss: **{avg_loss:.2f}%**", inline=True)
        emb.add_field(name="📊 Output", value=f"Full Kelly: **{res['kelly_full_pct']:.2f}%**\nFractional (25%): **{res['kelly_frac_pct']:.2f}%**\nRekomendasi: **{res['position_pct']:.2f}%** kapital", inline=True)
        interp = ("⚠️ Kecil — cek R/R & win rate" if res["position_pct"] < 5
                  else "⚠️ Besar — pastikan strategi proven" if res["position_pct"] > 20
                  else "✅ Range reasonable untuk swing trading")
        emb.add_field(name="💡", value=interp, inline=False)
        emb.set_footer(text="Fractional Kelly 25% | IDX Bot v4")
        await interaction.response.send_message(embed=emb, ephemeral=True)

async def setup(bot): await bot.add_cog(RiskCog(bot))
