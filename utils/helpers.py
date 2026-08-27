# utils/helpers.py
# Shared utility functions untuk Discord messaging

import asyncio
import discord


def split_msg(text: str, limit: int = 1900) -> list:
    """Potong pesan panjang jadi chunks Discord-safe."""
    text = (text or "").strip()
    if not text:
        return ["⚠️ Tidak ada hasil yang dapat ditampilkan."]
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunk = text[:cut].strip()
        if chunk:
            chunks.append(chunk)
        text = text[cut:].lstrip("\n")
    return chunks or ["⚠️ Tidak ada hasil yang dapat ditampilkan."]


async def send_long(channel, content: str, reply_to=None):
    """Kirim pesan panjang, dipotong per 1900 karakter."""
    chunks = split_msg(content)
    for i, chunk in enumerate(chunks):
        if not chunk or not chunk.strip():
            continue
        if i == 0 and reply_to:
            await reply_to.reply(chunk)
        else:
            await channel.send(chunk)
        if len(chunks) > 1:
            await asyncio.sleep(0.4)
