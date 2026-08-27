from __future__ import annotations
import asyncio, os, sys
import discord
from discord import app_commands
from discord.ext import commands
from config import (
    API_SECRET, DISCORD_TOKEN, GROQ_API_KEY, GROQ_MODEL, API_HOST, API_PORT, ENV,
    FEATURE_PORTFOLIO_OPTIMIZER, FEATURE_RISK_ENGINE,
    FEATURE_SENTIMENT_PIPELINE, FEATURE_VECTOR_MEMORY,
    SENTIMENT_REFRESH_MINUTES,
)
from utils.command_limits import format_retry_after
from utils.error_utils import user_error_message
from utils.groq_utils import validate_ai_config
from utils.logger import get_logger
log = get_logger("bot")

# ==================== BANNER CLEAN (Issue #9) ====================
ON_READY_BANNER = """
╔══════════════════════════════════════════════════╗
║   IDX Analyst Bot                                ║
║   AI      : {ai:<35} ║
║   Risk     : {risk}  Optimizer : {opt}           ║
║   RAG      : {rag}   Sentiment : {sent}          ║
║   Backtest : ✅  FastAPI : ✅                    ║
╚══════════════════════════════════════════════════╝
"""

def validate_config():
    errs = []
    if not DISCORD_TOKEN: errs.append("DISCORD_TOKEN belum diisi")
    ok_groq, groq_msg = validate_ai_config()
    if not ok_groq:
        errs.append(groq_msg)
    if ENV == "production" and (not API_SECRET or API_SECRET.lower() == "changeme"):
        errs.append("API_SECRET harus diisi dengan value aman saat ENV=production")
    if errs:
        for e in errs: print(f"❌ {e}")
        print("\nBuat file .env:\n  DISCORD_TOKEN=xxx\n  GROQ_API_KEY=xxx\n  API_SECRET=isi_secret_aman")
        sys.exit(1)

async def run_api_server():
    try:
        import uvicorn
        from api.app import app
        cfg = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="warning", access_log=False)
        server = uvicorn.Server(cfg)
        log.info(f"FastAPI starting on {API_HOST}:{API_PORT}")
        await server.serve()
    except ImportError:
        from aiohttp import web
        async def h(r): return web.Response(text='{"status":"ok","version":"4.0.0"}', content_type="application/json")
        a = web.Application()
        a.router.add_get("/", h); a.router.add_get("/health", h)
        runner = web.AppRunner(a); await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", API_PORT).start()
        log.info(f"Fallback health server on port {API_PORT}")

async def run_sentiment_bg():
    if not FEATURE_SENTIMENT_PIPELINE: return
    from sentiment.pipeline import get_sentiment_pipeline
    from config import DEFAULT_WATCHLIST
    pipeline = get_sentiment_pipeline(DEFAULT_WATCHLIST)
    while True:
        try:
            await pipeline.refresh()
        except Exception as exc:
            log.warning(f"Sentiment refresh: {exc}")
        await asyncio.sleep(SENTIMENT_REFRESH_MINUTES * 60)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None, description="IDX Analyst Bot v4")

COGS = [
    "cogs.analisis_cog",
    "cogs.picks_cog",
    "cogs.alert_cog",
    "cogs.portfolio_cog",
    "cogs.market_cog",
    "cogs.legacy_backtest_cog",
    "cogs.help_cog",
    "cogs.chat_cog",
    "cogs.risk_cog",
    "cogs.backtest_cog",
    "cogs.paper_cog",
]

@bot.event
async def on_ready():
    log.info(f"Bot login: {bot.user} | Servers: {len(bot.guilds)}")
    try:
        synced = await bot.tree.sync()
        log.info(f"Slash commands synced: {len(synced)}")
    except Exception as exc:
        log.warning(f"Slash sync: {exc}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="IDX v4 | !bantuan"))
    
    print(ON_READY_BANNER.format(
        ai=f"{GROQ_MODEL} (Groq)",
        risk="✅" if FEATURE_RISK_ENGINE else "❌",
        opt="✅" if FEATURE_PORTFOLIO_OPTIMIZER else "❌",
        rag="✅" if FEATURE_VECTOR_MEMORY else "❌",
        sent="✅" if FEATURE_SENTIMENT_PIPELINE else "❌",
    ))


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("❌ Parameter kurang. Gunakan `!bantuan`."); return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.reply(f"⏳ Command ini sedang cooldown. Coba lagi dalam `{format_retry_after(error.retry_after)}`.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.reply(f"❌ {user_error_message(error, fallback='Argumen tidak valid. Cek format command lalu coba lagi.')}")
        return
    log.error(f"Command error: {ctx.command} | {type(error).__name__}: {error}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        msg = f"⏳ Command ini sedang cooldown. Coba lagi dalam `{format_retry_after(error.retry_after)}`."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
    if isinstance(error, app_commands.CommandInvokeError):
        msg = f"❌ {user_error_message(error.original)}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
    log.error(f"App command error: {type(error).__name__}: {error}")

async def main():
    validate_config()
    try:
        from core.cache import get_cache
        await get_cache()
    except Exception as exc: log.warning(f"Cache init: {exc}")
    if FEATURE_VECTOR_MEMORY:
        try:
            from memory.vector_memory import get_vector_memory
            await get_vector_memory()
        except Exception as exc: log.warning(f"Vector memory: {exc}")
    async with bot:
        asyncio.create_task(run_api_server())
        # Groq Health Check (Issue #5) — sudah diaktifkan
        from utils.groq_health import schedule_groq_health_check
        asyncio.create_task(schedule_groq_health_check(delay_seconds=15))
        
        asyncio.create_task(run_sentiment_bg())
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                log.info(f"Loaded: {cog}")
            except Exception as exc:
                log.warning(f"Cog load failed ({cog}): {exc}")
        log.info("Connecting to Discord...")
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot dimatikan oleh pengguna (Ctrl+C). Keluar dengan aman.")

