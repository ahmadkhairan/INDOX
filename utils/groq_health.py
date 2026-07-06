from __future__ import annotations

"""
utils/groq_health.py — Background startup health check for the Groq API.

Performs a lightweight 5-token test call after the bot is ready.
Does NOT block the event loop or the bot startup sequence.

Usage (in bot.py, inside `on_ready` or `main`):
    from utils.groq_health import schedule_groq_health_check
    asyncio.create_task(schedule_groq_health_check())
"""

import asyncio
from typing import Callable

from utils.logger import get_logger

log = get_logger("groq.health")

_last_status: dict = {"ok": None, "message": "", "checked_at": ""}


async def _probe_groq() -> tuple[bool, str]:
    """
    Make a minimal 5-token Groq call.
    Returns (ok, message).
    """
    from config import GROQ_API_KEY, GROQ_MODEL
    from utils.groq_utils import validate_groq_config

    ok, msg = validate_groq_config()
    if not ok:
        return False, msg

    try:
        from groq import Groq

        client = Groq(api_key=GROQ_API_KEY)
        loop = asyncio.get_event_loop()

        def _sync_call():
            return client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                temperature=0,
            )

        resp = await loop.run_in_executor(None, _sync_call)
        _ = resp.choices[0].message.content  # ensure we can read the response
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


async def schedule_groq_health_check(
    delay_seconds: float = 10.0,
    on_failure: Callable[[str], None] | None = None,
) -> None:
    """
    Run after `delay_seconds` so the bot finishes connecting first.
    Logs result and optionally calls `on_failure(message)`.
    """
    from datetime import datetime, timezone

    await asyncio.sleep(delay_seconds)
    ok, message = await _probe_groq()
    _last_status.update(
        {
            "ok": ok,
            "message": message,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if ok:
        log.info("Groq health check PASSED")
    else:
        log.warning(f"Groq health check FAILED: {message}")
        if on_failure:
            on_failure(message)


def get_groq_health() -> dict:
    """Return the result of the last health check (or None if not run yet)."""
    return dict(_last_status)
