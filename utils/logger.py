from __future__ import annotations
import json, logging, sys
from datetime import datetime, timezone
from typing import Any
from config import LOG_LEVEL, LOG_FORMAT

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname, "logger": record.name,
            "msg": record.getMessage(), "module": record.module, "line": record.lineno,
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers: return logger
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(
        JSONFormatter() if LOG_FORMAT == "json"
        else logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    logger.addHandler(h)
    logger.propagate = False
    return logger
