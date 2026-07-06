from __future__ import annotations

import asyncio
import time

from discord.ext import commands

from config import CHAT_CHANNEL_ALLOWLIST
from utils.ai_engine import chat
from utils.command_limits import CHAT_MENTION_COOLDOWN_SECONDS
from utils.helpers import send_long


class ChatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_hist: dict[str, list[dict]] = {}
        self._last_mention_at: dict[str, float] = {}

    def reset_history(self, user_id: int) -> None:
        uid = str(user_id)
        self.user_hist.pop(uid, None)
        self._last_mention_at.pop(uid, None)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not self.bot.user.mentioned_in(message) or message.content.startswith("!"):
            return
        if message.guild and CHAT_CHANNEL_ALLOWLIST and message.channel.id not in CHAT_CHANNEL_ALLOWLIST:
            return

        content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
        if not content:
            return

        uid = str(message.author.id)
        now = time.monotonic()
        last = self._last_mention_at.get(uid, 0.0)
        if now - last < CHAT_MENTION_COOLDOWN_SECONDS:
            return
        self._last_mention_at[uid] = now

        async with message.channel.typing():
            history = self.user_hist.get(uid, [])
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, chat, content, history)
            history.append({"role": "user", "content": content})
            history.append({"role": "assistant", "content": resp})
            self.user_hist[uid] = history[-20:]
            await send_long(message.channel, resp)


async def setup(bot):
    await bot.add_cog(ChatCog(bot))
