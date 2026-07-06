from __future__ import annotations

import re

from config import GROQ_API_KEY, GROQ_MODEL


GROQ_KEY_PATTERN = re.compile(r"^(gsk|grq)_[A-Za-z0-9_-]{20,}$")


def validate_groq_config(api_key: str | None = None, model: str | None = None) -> tuple[bool, str]:
    key = (api_key if api_key is not None else GROQ_API_KEY).strip()
    selected_model = (model if model is not None else GROQ_MODEL).strip()
    if not key:
        return False, "GROQ_API_KEY belum diisi"
    if not GROQ_KEY_PATTERN.match(key):
        return False, "Format GROQ_API_KEY tampak tidak valid"
    if not selected_model:
        return False, "GROQ_MODEL belum diisi"
    return True, "ok"


def groq_status(api_key: str | None = None, model: str | None = None) -> dict[str, str | bool]:
    ok, message = validate_groq_config(api_key=api_key, model=model)
    return {
        "configured": ok,
        "status": "configured" if ok else "invalid",
        "message": message,
    }
