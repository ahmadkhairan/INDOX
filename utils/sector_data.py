from __future__ import annotations


SECTOR_MAP = {
    "ADRO": "Coal Mining", "PTBA": "Coal Mining", "BYAN": "Coal Mining",
    "ITMG": "Coal Mining", "HRUM": "Coal Mining", "DOID": "Coal Mining",
    "BOSS": "Coal Mining", "MBAP": "Coal Mining", "TOBA": "Coal Mining",
    "ANTM": "Metals Mining", "MDKA": "Metals Mining", "INCO": "Metals Mining",
    "TINS": "Metals Mining", "PSAB": "Metals Mining",
    "BBCA": "Banking", "BBRI": "Banking", "BMRI": "Banking", "BNGA": "Banking",
    "BBNI": "Banking", "BRIS": "Banking", "BTPS": "Banking", "NISP": "Banking",
    "BNII": "Banking", "PNBN": "Banking",
    "TLKM": "Telco", "EXCL": "Telco", "ISAT": "Telco",
    "UNVR": "Consumer Staples", "INDF": "Consumer Staples", "MYOR": "Consumer Staples",
    "ICBP": "Consumer Staples", "CPIN": "Consumer Staples",
    "BSDE": "Property", "CTRA": "Property", "SMRA": "Property",
    "KLBF": "Healthcare", "SIDO": "Healthcare", "MIKA": "Healthcare",
    "ASII": "Automotive", "UNTR": "Automotive",
    "GOTO": "Tech", "EMTK": "Tech",
}

SECTOR_NORMS = {
    "Coal Mining": {
        "note": "Sektor tambang batubara: ROE 8-15% & DER tinggi NORMAL. Fokus ke cashflow, dividend yield, dan buyback news. Perhatikan harga batubara global (Newcastle benchmark).",
        "roe_ok_min": 8.0,
        "per_ok_max": 10.0,
        "der_ok_max": 2.5,
        "flow_weight_bonus": 0.10,
    },
    "Metals Mining": {
        "note": "Sektor logam tambang: ROE fluktuatif mengikuti harga komoditas. DER moderate wajar. Fokus ke Net Cash position & kapasitas produksi.",
        "roe_ok_min": 10.0,
        "per_ok_max": 12.0,
        "der_ok_max": 1.5,
        "flow_weight_bonus": 0.05,
    },
    "Banking": {
        "note": "Sektor perbankan: DER tinggi NORMAL (leverage bisnis). ROE > 15% excellent. NIM & NPL lebih penting dari DER.",
        "roe_ok_min": 12.0,
        "per_ok_max": 18.0,
        "der_ok_max": 10.0,
        "flow_weight_bonus": 0.0,
    },
    "Property": {
        "note": "Sektor properti: DER moderate-tinggi wajar untuk leverage proyek. Fokus ke marketing sales & landbank.",
        "roe_ok_min": 8.0,
        "per_ok_max": 20.0,
        "der_ok_max": 2.0,
        "flow_weight_bonus": 0.0,
    },
}


def get_sector_context(ticker: str, yf_sector: str = "") -> dict:
    sector = SECTOR_MAP.get(ticker.upper())
    if not sector and yf_sector:
        yfmap = {
            "Basic Materials": "Metals Mining",
            "Energy": "Coal Mining",
            "Financial Services": "Banking",
            "Real Estate": "Property",
            "Consumer Defensive": "Consumer Staples",
            "Technology": "Tech",
            "Communication Services": "Telco",
            "Healthcare": "Healthcare",
            "Consumer Cyclical": "Automotive",
        }
        sector = yfmap.get(yf_sector, yf_sector)

    norms = SECTOR_NORMS.get(sector, {})
    return {
        "label": sector or "General",
        "note": norms.get("note", ""),
        "roe_ok_min": norms.get("roe_ok_min", 15.0),
        "per_ok_max": norms.get("per_ok_max", 15.0),
        "der_ok_max": norms.get("der_ok_max", 1.0),
        "flow_weight_bonus": norms.get("flow_weight_bonus", 0.0),
    }


def get_sector_label(ticker: str, yf_sector: str = "") -> str:
    return get_sector_context(ticker, yf_sector).get("label", "General")
