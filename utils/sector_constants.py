from __future__ import annotations

from enum import Enum


class Sector(str, Enum):
    COAL_MINING = "Coal Mining"
    METALS_MINING = "Metals Mining"
    BANKING = "Banking"
    TELCO = "Telco"
    CONSUMER_STAPLES = "Consumer Staples"
    PROPERTY = "Property"
    HEALTHCARE = "Healthcare"
    AUTOMOTIVE = "Automotive"
    TECH = "Tech"
    GENERAL = "General"

    @classmethod
    def normalize(cls, raw: str) -> "Sector":
        """
        Canonicalize any sector string to a Sector member.
        Raises ValueError if the string is unrecognized.

        Usage:
            Sector.normalize("Real Estate")  -> Sector.PROPERTY
            Sector.normalize("banking")      -> Sector.BANKING
        """
        mapping: dict[str, Sector] = {
            "coal mining": cls.COAL_MINING,
            "coal": cls.COAL_MINING,
            "energy": cls.COAL_MINING,
            "metals mining": cls.METALS_MINING,
            "metal mining": cls.METALS_MINING,
            "basic materials": cls.METALS_MINING,
            "mining": cls.METALS_MINING,
            "banking": cls.BANKING,
            "financial services": cls.BANKING,
            "bank": cls.BANKING,
            "telco": cls.TELCO,
            "communication services": cls.TELCO,
            "telecommunications": cls.TELCO,
            "consumer staples": cls.CONSUMER_STAPLES,
            "consumer": cls.CONSUMER_STAPLES,
            "consumer defensive": cls.CONSUMER_STAPLES,
            "property": cls.PROPERTY,
            "real estate": cls.PROPERTY,
            "properti": cls.PROPERTY,
            "healthcare": cls.HEALTHCARE,
            "health care": cls.HEALTHCARE,
            "automotive": cls.AUTOMOTIVE,
            "consumer cyclical": cls.AUTOMOTIVE,
            "tech": cls.TECH,
            "technology": cls.TECH,
            "general": cls.GENERAL,
        }
        key = raw.strip().lower()
        result = mapping.get(key)
        if result is None:
            raise ValueError(
                f"Unknown sector label {raw!r}. "
                f"Valid values: {[s.value for s in cls]}"
            )
        return result

    @classmethod
    def normalize_safe(cls, raw: str, default: "Sector | None" = None) -> "Sector":
        """Like normalize() but returns default instead of raising."""
        try:
            return cls.normalize(raw)
        except ValueError:
            return default if default is not None else cls.GENERAL


# Backward-compatible string alias so old code that does
# `from utils.sector_constants import SECTOR_LABELS` keeps working.
SECTOR_LABELS: dict[str, str] = {s.value: s.value for s in Sector}
