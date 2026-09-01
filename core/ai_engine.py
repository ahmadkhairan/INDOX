from __future__ import annotations
import asyncio, json
import re
from datetime import datetime
from typing import Optional
from config import GROQ_MAX_TOKENS, GROQ_TEMPERATURE
from utils.llm_provider import chat_completion, configured_provider_count
from utils.logger import get_logger
from utils.news_utils import detect_special_news
from utils.market_regime import get_coal_price

log = get_logger("core.ai")
_client_error = "Belum ada AI provider yang dikonfigurasi."

SYSTEM_PROMPT_ID = """Kamu adalah Chief Investment Officer + Head of Technical Analysis di top-tier fund ekuitas Indonesia, spesialisasi IDX.

FRAMEWORK ANALISIS 4-LAYER:
Layer 1 — MACRO/REGIME: IHSG vs trend anchor MA50, global risk appetite, sektor rotation
Layer 2 — FUNDAMENTAL: Quality screen sesuai threshold sektor
Layer 3 — TECHNICAL: Multi-entry confluence (momentum, pullback, breakout, volume, S/R)
Layer 4 — SENTIMENT/FLOW: News flow, IDX announcement, foreign flow, OBV

ATURAN SEKTOR KHUSUS:

BATUBARA (ADRO, PTBA, BYAN, ITMG, HRUM):
- ROE 8-15% & DER tinggi NORMAL. Jangan penalti.
- Fokus: cashflow, dividend yield >5%, ada/tidaknya buyback.
- Driver: Newcastle benchmark. Rally >$90/ton = positif.
- Risiko wajib: penurunan harga batubara + ESG pressure.

PERBANKAN (BBCA, BBRI, BMRI):
- DER tinggi NORMAL (leverage bisnis bank).
- KPI: NIM, NPL, CASA, loan growth. ROE >15% excellent.

LOGAM TAMBANG (ANTM, MDKA, INCO):
- ROE fluktuatif wajar. Fokus: net cash, produksi, harga komoditas.

PROPERTI (BSDE, CTRA, SMRA):
- DER moderate-tinggi wajar. Fokus: marketing sales, landbank.

AGGRESSIVE MULTI-ENTRY FRAMEWORK:
- MOMENTUM: ADX >= 18, +DI > -DI, RSI 38-68, volume >= 1.2x, harga > MA50/MA20, Stoch K > D, MACD bullish.
- PULLBACK: harga dekat MA20/VWAP, RSI 35-55, volume >= 0.9x, trend masih sehat, candle bounce.
- BREAKOUT: tembus high 20 hari, volume >= 1.8x, RSI < 75, ADX >= 15.
- Prioritas entry: BREAKOUT > PULLBACK > MOMENTUM bila beberapa sinyal aktif.
- Minimal 1 sinyal valid untuk actionable plan. Jika belum valid, berikan wait trigger yang spesifik.

KONFLIK INDIKATOR: sebutkan semua, tentukan mana lebih kuat, beri wait condition spesifik.

ATURAN FORMAT OUTPUT DISCORD (SANGAT PENTING):
1. DILARANG KERAS menggunakan Markdown pipe table (`| Kolom | Kolom |` atau `|---|---|`) karena Discord tidak merender tabel markdown dengan benar sehingga tampilan menjadi berantakan.
2. Gunakan format daftar berpoin / key-value yang bersih dan terstruktur (`• **Label**: Detail`).
3. Gunakan inline code (` `) untuk angka harga, level S/R, Stop Loss, dan Take Profit agar mudah dibaca.
4. Seluruh isi analisis harus dalam Bahasa Indonesia profesional dan konsisten (kecuali istilah finansial baku).
5. Akhiri dengan disclaimer edukasi singkat.
"""

SYSTEM_PROMPT_EN = """You are the Chief Investment Officer & Head of Technical Analysis at a top-tier Indonesian equity fund specializing in the Indonesia Stock Exchange (IDX).

4-LAYER ANALYSIS FRAMEWORK:
Layer 1 — MACRO/REGIME: IHSG vs MA50 trend anchor, global risk appetite, sector rotation
Layer 2 — FUNDAMENTAL: Quality screening based on sector thresholds
Layer 3 — TECHNICAL: Multi-entry confluence (momentum, pullback, breakout, volume, S/R)
Layer 4 — SENTIMENT/FLOW: News flow, IDX disclosures, foreign institutional flow, OBV

SECTOR-SPECIFIC RULES:

COAL MINING (ADRO, PTBA, BYAN, ITMG, HRUM):
- ROE 8-15% & high DER are NORMAL. Do not penalize.
- Focus: operating cashflow, dividend yield >5%, share buyback presence.
- Driver: Newcastle coal benchmark. Rally >$90/ton = positive.
- Mandatory risks: commodity downcycle + ESG headwinds.

BANKING (BBCA, BBRI, BMRI):
- High DER is NORMAL due to banking leverage.
- KPIs: NIM, NPL, CASA ratio, loan growth. ROE >15% is excellent.

METALS & MINING (ANTM, MDKA, INCO):
- Fluctuating ROE is standard. Focus: net cash position, production volume, underlying commodity prices.

PROPERTY (BSDE, CTRA, SMRA):
- Moderate-to-high DER is typical. Focus: marketing sales, land bank inventory.

AGGRESSIVE MULTI-ENTRY FRAMEWORK:
- MOMENTUM: ADX >= 18, +DI > -DI, RSI 38-68, volume >= 1.2x, price > MA50/MA20, Stoch K > D, MACD bullish.
- PULLBACK: price near MA20/VWAP, RSI 35-55, volume >= 0.9x, intact trend, candlestick bounce.
- BREAKOUT: breaking 20-day high, volume >= 1.8x, RSI < 75, ADX >= 15.
- Priority: BREAKOUT > PULLBACK > MOMENTUM when multiple triggers are present.
- At least 1 valid trigger for actionable trade plan. Otherwise, provide a specific wait trigger.

INDICATOR CONFLICTS: Identify conflicting signals, declare dominant thesis, and specify exact confirmation conditions.

DISCORD OUTPUT FORMATTING RULES (CRITICAL):
1. STRICTLY PROHIBITED: Markdown pipe tables (`| Col 1 | Col 2 |` or `|---|---|`). Discord chat cannot render markdown tables properly, resulting in broken wrapped text.
2. Use clean, structured bullet lists with bold key-value labels (`• **Label**: Detail`).
3. Highlight prices, support/resistance levels, Stop Loss, and Take Profit using inline code (` `) for maximum legibility.
4. The entire output must be written in 100% fluent, professional financial English.
5. End with a concise educational disclaimer.
"""

SYSTEM_PROMPT = SYSTEM_PROMPT_ID


async def _call_async(messages, system=None, max_tokens=None):
    loop = asyncio.get_event_loop()
    def _sync():
        return chat_completion(messages, system or SYSTEM_PROMPT, max_tokens or GROQ_MAX_TOKENS, GROQ_TEMPERATURE)[0]
    try:
        r = await loop.run_in_executor(None, _sync)
        return r.strip()
    except Exception as exc:
        log.error(f"AI provider error: {exc}"); return f"❌ Error AI provider: {exc}"

def _call_groq(messages, system=None, max_tokens=None):
    try:
        return chat_completion(messages, system or SYSTEM_PROMPT, max_tokens or GROQ_MAX_TOKENS, GROQ_TEMPERATURE)[0]
    except Exception as exc:
        return f"❌ Error AI provider: {exc}"

async def analyze_ticker_v4(data, news, user_question="", rag_context="",
                             sentiment=None, var_result=None, regime=None,
                             language="id"):
    lang = "en" if str(language).strip().lower().startswith("en") else "id"
    ticker = data.get("ticker","N/A"); mkt = data.get("market",{})
    fund = data.get("fundamental",{}); tech = data.get("technical",{})
    flow = data.get("flow",{}); score = data.get("score",{})
    sc = data.get("sector_context",{}); coal = data.get("coal_data",{})

    try:
        special = detect_special_news(news)
    except Exception:
        special = {"has_buyback":False,"has_dividend":False,"buyback_news":[],"dividend_news":[]}

    pivot = tech.get("pivot",{}); fib = tech.get("fibonacci",{}); swing = tech.get("swing_sr",{})
    obv = tech.get("obv",{})
    sups = " | ".join(f"Rp{x:,.0f}" for x in swing.get("supports",[])) or f"Rp{tech.get('support',0):,.0f}"
    ress = " | ".join(f"Rp{x:,.0f}" for x in swing.get("resistances",[])) or f"Rp{tech.get('resistance',0):,.0f}"

    sector_blk = f"\nSEKTOR: {sc.get('label','N/A')}\n{sc.get('note','')}\n" if sc.get("note") else ""
    coal_status = "🔥 COAL RALLY AKTIF" if coal.get("hot") else ""
    coal_blk = (f"\nCOAL BENCHMARK: {coal.get('label','N/A')} | Bonus: +{coal.get('score_bonus',0)}\n"
                f"{coal_status}\n"
               ) if coal and coal.get("price", 0) > 0 else ""
    corp_blk = ""
    if special.get("has_buyback"):
        corp_blk += f"\n🔄 BUYBACK: {'; '.join(special['buyback_news'][:2])}\n"
    if special.get("has_dividend"):
        corp_blk += f"\n💰 DIVIDEN: {'; '.join(special['dividend_news'][:2])}\n"
    buyback_flag = "✅" if sentiment and sentiment.get("has_buyback") else "❌"
    dividend_flag = "✅" if sentiment and sentiment.get("has_dividend") else "❌"
    sent_blk = (f"\nSENTIMENT: {sentiment.get('label','N/A')} (score:{sentiment.get('score',0):+.3f}) | "
                f"Buyback:{buyback_flag} "
                f"Dividen:{dividend_flag}\n") if sentiment else ""
    var_blk = (f"\nRISK: VaR 1d 95%={var_result.get('var_1d_95',0):.2f}% | CVaR={var_result.get('cvar_95',0):.2f}% | "
               f"Vol={var_result.get('ann_vol',0):.1f}%\n") if var_result else ""
    regime_blk = f"\n⚠️ REGIME: {regime.get('warning','')}\n" if regime and regime.get("regime") in ("BEAR","CAUTION") else ""
    rag_blk = f"\n{rag_context}\n" if rag_context else ""

    atr_sl=tech.get("aggressive_sl", tech.get("atr_sl",0)); atr_tp1=tech.get("aggressive_tp1", tech.get("atr_tp1",0)); atr_tp2=tech.get("aggressive_tp2", tech.get("atr_tp2",0)); atr_rr=tech.get("atr_rr",0)
    ma200_note = "" if tech.get("ma200_valid") else " (data<200hr)"
    preferred_mode = score.get("preferred_entry_mode", tech.get("preferred_entry_mode", "WAIT"))
    signal_count = score.get("aggressive_signal_count", tech.get("aggressive_signal_count", 0))
    momentum_cond = score.get("momentum_cond_count", tech.get("momentum_cond_count", score.get("five_cond_count", 0)))
    ready_modes = ", ".join(tech.get("entry_modes_ready", [])) or "WAIT"
    news_text = "\n".join(
        f"- [{n.get('source', '?')}] {n.get('title', '?')}" for n in news[:5]
    )

    data_summary = f"""DATA EMITEN {ticker} — {data.get("company_name","")} | {data.get("sector","N/A")} | {datetime.now().strftime("%d %B %Y %H:%M")} WIB
{sector_blk}{coal_blk}{corp_blk}{sent_blk}{var_blk}{regime_blk}{rag_blk}
MARKET: Harga Rp{mkt.get("price",0):,.0f} ({mkt.get("change_pct",0):+.2f}%) | Vol Ratio {mkt.get("vol_ratio",0):.2f}x | Cap {mkt.get("market_cap","N/A")} | Div {fund.get("dividend_yield",0):.1f}%

FUNDAMENTAL: PER={fund.get("per",0):.1f}x PBV={fund.get("pbv",0):.1f}x ROE={fund.get("roe",0):.1f}% DER={fund.get("der",0):.2f} RevG={fund.get("revenue_growth",0):.1f}% EPSG={fund.get("eps_growth",0):.1f}%
Threshold sektor: ROE>{sc.get("roe_ok_min",15):.0f}% PER<{sc.get("per_ok_max",15):.0f}x DER<{sc.get("der_ok_max",1):.1f}

TECHNICAL:
EMA9={tech.get("ema9",0):,.0f} MA20={tech.get("ma20",0):,.0f} MA50={tech.get("ma50",0):,.0f} MA200={tech.get("ma200",0):,.0f}{ma200_note}
VWAP={tech.get("vwap",0):,.0f} → {tech.get("vwap_signal","N/A")} | Trend: {tech.get("trend","N/A")} {tech.get("trend_strength","N/A")}
RSI={tech.get("rsi",50):.1f} → {tech.get("rsi_label","N/A")}
StochRSI K:{tech.get("stoch_rsi_k",50):.1f} D:{tech.get("stoch_rsi_d",50):.1f}
Williams%R={tech.get("williams_r",-50):.1f} → {tech.get("williams_r_label","N/A")}
MACD Hist={tech.get("macd_histogram",0):.4f} → {tech.get("macd_cross","N/A")}
ADX={tech.get("adx",0):.1f} (+DI:{tech.get("adx_plus_di",0):.1f} -DI:{tech.get("adx_minus_di",0):.1f})
ATR={tech.get("atr",0):,.0f} | BB%={tech.get("bb_pct",0):.1f}% | OBV: {obv.get("signal","N/A")}
Candlestick: {", ".join(tech.get("candlestick_patterns",[]) or ["N/A"])}

ATR LEVELS: SL Rp{atr_sl:,.0f} | TP1 Rp{atr_tp1:,.0f} | TP2 Rp{atr_tp2:,.0f} | R/R 1:{atr_rr:.1f}
SIGNALS: Preferred={preferred_mode} | Active={ready_modes} | Signal Count={signal_count}/3 | Momentum Cond={momentum_cond}/5
SETUP FLAGS: Pullback={tech.get("pullback_ready", False)} | Breakout={tech.get("breakout_ready", False)} | Near MA20={tech.get("near_ma20", False)} | Near VWAP={tech.get("near_vwap", False)} | Bounce={tech.get("bounce", False)}
S/R: Supports={sups} | Resistances={ress}
Pivot PP={pivot.get("pp",0):,.0f} R1={pivot.get("r1",0):,.0f} S1={pivot.get("s1",0):,.0f}
Fib 38.2%={fib.get("r_382",0):,.0f} 50%={fib.get("r_500",0):,.0f} 61.8%={fib.get("r_618",0):,.0f}

FLOW: {flow.get("signal","N/A")} | Net: {flow.get("net_foreign","N/A")}

SCORE: {score.get("total",0):.1f}/100 Grade {score.get("grade","N/A")} | Fund:{score.get("fundamental",0):.0f} Tech:{score.get("technical",0):.0f} Flow:{score.get("flow",0):.0f}
Entry Quality: {score.get("entry_quality","N/A")} | Conf: {score.get("confidence","N/A")} Win Prob: {score.get("win_probability",0):.1f}% | Preferred Mode: {preferred_mode}

NEWS:
{news_text}
{f"USER QUESTION: {user_question}" if user_question else ""}"""

    if lang == "en":
        system_to_use = SYSTEM_PROMPT_EN
        prompt = f"""{data_summary}

Please provide an in-depth stock analysis in fluent English.
STRICT FORMATTING RULE: DO NOT use Markdown pipe tables (| Col | Col |). Format structured items as clean bullet points (• **Label**: Detail) and use inline code (` `) for price levels.

STRUCTURE TO FOLLOW:
1️⃣ **Market & Regime Context (Macro + Sector + Flow + Sentiment)**
• **Macro & Regime**: [IHSG trend vs MA50 anchor, global risk appetite]
• **Sector Rotation**: [Sector momentum and rotation status]
• **Flow & Foreign**: [Foreign flow & institutional activity]
• **Sentiment Score**: [1-10/10] — [News summary & sentiment drivers]

2️⃣ **Detailed Technical Analysis**
• **Trend & Structure**: [Trend direction, MA20/MA50/MA200 alignment, VWAP status]
• **Momentum & Indicators**: [RSI, StochRSI, Williams%R, ADX (+DI/-DI), MACD]
• **Volume & OBV**: [Volume breakout, OBV trajectory]
• **Key Levels (S/R & Fib)**: [Supports, Resistances, Pivot, Fibonacci levels]
• **Candlestick & Signals**: [Candlestick pattern and indicator conflict resolution if any]

3️⃣ **Fundamental Valuation & Health**
• **Valuation**: [PER, PBV vs sector thresholds]
• **Profitability & Growth**: [ROE, Revenue Growth, EPS Growth]
• **Financial Health & Dividends**: [DER, Dividend Yield, Buyback if applicable]

4️⃣ **News Impact & Specific Catalysts**
• [Evaluation of relevant news items, commodity price movements, and corporate actions]

5️⃣ **Trade Setup & Money Management**
• **Preferred Mode**: [{preferred_mode}]
• **Entry Zone**: `Rp ... – Rp ...`
• **Stop Loss (SL)**: `Rp ...` (1.5x ATR | -...%)
• **Take Profit 1 (TP1)**: `Rp ...` (+...%)
• **Take Profit 2 (TP2)**: `Rp ...` (+...%)
• **Risk/Reward (R/R)**: `1 : ...` | **Risk per Trade**: `...%` | **Position Size**: `...% capital`
• **Confidence**: [Low / Medium / High] | **Win Probability**: `...%`

6️⃣ **Specific Risk Factors**
• [Sector-specific risks, commodity dependence, liquidity, macro]

7️⃣ **Confirmation & Exit Strategy**
• **Entry Confirmation**: [Specific mandatory conditions before taking the trade]
• **Exit Strategy**: [Trailing stop, partial profit-taking, RSI/MACD exits, max hold duration]

⚠️ *Educational analysis only, not financial investment advice.*"""
    else:
        system_to_use = SYSTEM_PROMPT_ID
        prompt = f"""{data_summary}

Berikan analisis saham mendalam dalam Bahasa Indonesia yang profesional.
ATURAN FORMAT WAJIB: DILARANG KERAS menggunakan Markdown pipe table (| Kolom | Kolom |). Gunakan format poin terstruktur yang rapi (• **Label**: Penjelasan) dan gunakan inline code (` `) untuk angka harga.

STRUKTUR WAJIB:
1️⃣ **Ringkasan Kondisi (Macro + Sektor + Flow + Sentimen)**
• **Macro & Regime**: [Kondisi IHSG vs MA50, sentimen pasar global]
• **Rotasi Sektor**: [Status momentum dan rotasi sektor]
• **Flow & Asing**: [Foreign flow, akumulasi/distribusi bandar/institusi]
• **Skor Sentimen**: [1-10/10] — [Ringkasan sentimen & driver berita utama]

2️⃣ **Analisis Teknikal Detail**
• **Trend & Struktur**: [Arah trend, MA20/MA50/MA200, posisi VWAP]
• **Momentum & Indikator**: [RSI, StochRSI, Williams%R, ADX (+DI/-DI), MACD]
• **Volume & OBV**: [Volume breakout, arah OBV]
• **Level Kunci (S/R & Fib)**: [Support, Resistance, Pivot PP/R1/S1, Fibonacci]
• **Candlestick & Sinyal**: [Pola candlestick, resolusi konflik indikator jika ada]

3️⃣ **Analisis Fundamental & Valuasi**
• **Valuasi**: [PER, PBV vs threshold sektor]
• **Profitabilitas & Kinerja**: [ROE, Pertumbuhan Pendapatan & EPS]
• **Kesehatan & Dividen**: [DER, Dividend Yield, Buyback jika ada]

4️⃣ **Sentimen Berita & Katalis**
• [Evaluasi dampak berita spesifik, aksi korporasi, pergerakan komoditas terkait]

5️⃣ **Trade Setup & Manajemen Risiko**
• **Setup Pilihan**: [{preferred_mode}]
• **Entry Zone**: `Rp ... – Rp ...`
• **Stop Loss (SL)**: `Rp ...` (1.5x ATR | -...%)
• **Take Profit 1 (TP1)**: `Rp ...` (+...%)
• **Take Profit 2 (TP2)**: `Rp ...` (+...%)
• **Risk/Reward (R/R)**: `1 : ...` | **Risk per Trade**: `...%` | **Ukuran Posisi**: `...% modal`
• **Confidence**: [Low / Medium / High] | **Win Probability**: `...%`

6️⃣ **Faktor Risiko Spesifik**
• [Risiko spesifik sektor/emiten, ketergantungan komoditas, likuiditas, makro]

7️⃣ **Konfirmasi & Strategi Exit**
• **Konfirmasi Entry**: [Kondisi mutlak yang HARUS terpenuhi sebelum entry]
• **Strategi Exit**: [Trailing stop, partial profit-taking, sinyal exit RSI/MACD, batas waktu hold]

⚠️ *Disclaimer: Analisis edukasi pasar modal, bukan rekomendasi atau ajakan investasi.*"""

    return await _call_async([{"role":"user","content":prompt}], system=system_to_use, max_tokens=max(GROQ_MAX_TOKENS, 3500))


async def generate_daily_picks_v4(candidates, news_context, regime=None, coal_data=None, market_sentiment=None):
    regime_blk = f"\nIHSG REGIME: {regime.get('regime','N/A')} | {regime.get('warning','')}\n" if regime else ""
    coal_blk = f"\nCOAL RALLY: {coal_data.get('label','N/A')}\n" if coal_data and coal_data.get("rally") else ""
    sent_blk = (f"\nMARKET SENTIMENT: {market_sentiment.get('label','NEUTRAL')} "
                f"(score:{market_sentiment.get('score',0):+.3f} coverage:{market_sentiment.get('coverage',0)})\n"
               ) if market_sentiment else ""

    summary = []
    for d in candidates[:12]:
        t=d.get("technical",{}); f=d.get("fundamental",{}); fl=d.get("flow",{})
        s=d.get("score",{}); sc=d.get("sector_context",{}); mkt=d.get("market",{})
        q=d.get("quant",{}); pos=d.get("position_sizing",{})
        labels=[]
        if mkt.get("vol_ratio",0)>=2.0: labels.append("HOT VOL")
        if fl.get("net_raw",0)>50000: labels.append("FOREIGN RUSH")
        if sc.get("label")=="Coal Mining" and coal_data and coal_data.get("rally"): labels.append("COAL RALLY")
        if f.get("dividend_yield",0)>=5.0: labels.append("HIGH DIV")
        summary.append({
            "ticker":d.get("ticker"),"company":d.get("company_name","")[:25],
            "sector":sc.get("label",d.get("sector","N/A")),"price":mkt.get("price",0),
            "quant_rank":q.get("rank"),"quant_score":q.get("score"),
            "expected_return_pct":d.get("return_profile",{}).get("expected_return_pct",0),
            "reward_pct":d.get("return_profile",{}).get("reward_pct",0),
            "risk_pct":d.get("return_profile",{}).get("risk_pct",0),
            "score":s.get("total",0),"grade":s.get("grade","N/A"),
            "confidence":s.get("confidence","N/A"),"win_prob":s.get("win_probability",0),
            "entry_quality":s.get("entry_quality","N/A"),"five_cond":s.get("five_cond_count",0),
            "signal_count":s.get("aggressive_signal_count", t.get("aggressive_signal_count", 0)),
            "preferred_mode":s.get("preferred_entry_mode", t.get("preferred_entry_mode", "WAIT")),
            "rsi":t.get("rsi",50),"adx":t.get("adx",0),"trend":t.get("trend","N/A"),
            "vb":t.get("volume_breakout",False),"atr_rr":t.get("atr_rr",0),
            "atr_sl":t.get("aggressive_sl", t.get("atr_sl",0)),"atr_tp1":t.get("aggressive_tp1", t.get("atr_tp1",0)),
            "atr_tp2":t.get("aggressive_tp2", t.get("atr_tp2",0)),
            "pullback_ready":t.get("pullback_ready", False),"breakout_ready":t.get("breakout_ready", False),
            "per":f.get("per",0),"roe":f.get("roe",0),"div_yield":f.get("dividend_yield",0),
            "flow":fl.get("signal","N/A"),"liquid":d.get("liquid",False),
            "risk_pct":pos.get("risk_pct",0),
            "retail_size_pct":pos.get("retail_position_pct",0),
            "retail_band":pos.get("retail_band",""),
            "kelly_frac_pct":pos.get("kelly_fractional_pct",0),
            "labels":labels,
            "scan_mode":"fallback" if d.get("scan_fallback") else "strict",
        })

    is_bear = regime and regime.get("regime")=="BEAR"
    bear_note = "\nBEAR MODE: Hanya score>75, size 50%, SL sangat ketat.\n" if is_bear else ""

    fallback_note = "\nPERINGATAN: Shortlist menggunakan fallback karena tidak ada kandidat yang memenuhi seluruh filter strict. Pilih setup terbaik, tetapi tulis dengan jelas kondisi yang masih harus dikonfirmasi sebelum entry.\n" if any(d.get("scan_fallback") for d in candidates) else ""
    prompt = f"""Tanggal: {datetime.now().strftime("%d %B %Y")}
{regime_blk}{coal_blk}{sent_blk}
{fallback_note}
DATA KANDIDAT (scan seluruh IDX liquid):
{json.dumps(summary, indent=2, default=str)}

BERITA: {news_context}
{bear_note}
Kamu hanya boleh memilih TEPAT 3 saham dari shortlist quant di atas.
Prioritas: expected_return_pct positif dan tertinggi, R/R sehat, quant_rank terbaik, quant_score tinggi, entry_quality CLEAN/GOOD, signal_count >= 1, preferred_mode jelas, liquid, trend bullish, ADX >= 18, flow tidak negatif. Jangan memilih kandidat jika expected_return_pct <= 0.
Jika regime CAUTION, prioritaskan quant_rank 1-6, setup paling clean, dan kecilkan size. Jangan pilih ticker di luar data input. Gunakan bahasa profesional dan jangan gunakan emoji, ikon, simbol dekoratif, atau karakter grafis apa pun.

FORMAT WAJIB:

**DAILY PICKS IDX — {datetime.now().strftime("%d %b %Y")} [v4]**
{regime_blk}

**PICK #1: [TICKER] — [Nama]**
Sektor: [sektor] [labels]
Score: __/100 Grade __ | RSI: __ | ADX: __ | Trend: __
Quant: Rank #__ | Quant Score __/100 | Mode: __ | Signals: __/3
Entry: Rp____ – Rp____ | SL: Rp____ | TP1: Rp____ (+__%) TP2: Rp____ (+__%)
R/R: 1:__ | Risk/Trade: __% | Retail Size: __% (__)
Confidence: __ | Win Prob: __%
Flow: __ | Alasan: [2 kalimat spesifik — trigger + alasan pilih hari ini]
Konfirmasi: [kondisi HARUS terpenuhi sebelum entry]

**PICK #2** [format sama]
**PICK #3** [format sama]

**MARKET PULSE**: [kondisi pasar + bias sektor]
*Analisis edukasi, bukan saran investasi.*"""
    return await _call_async([{"role":"user","content":prompt}], max_tokens=max(GROQ_MAX_TOKENS, 3500))


# Backward-compat sync wrappers
def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(coro)
        except Exception as exc:
            log.error(f"async wrapper error: {exc}")
            return f"❌ Error: {exc}"
    err = "Sync AI wrapper dipanggil di thread dengan event loop aktif. Gunakan API async atau run_in_executor."
    try:
        coro.close()
    except Exception:
        pass
    log.error(err)
    return f"❌ Error: {err}"

def analyze_ticker(data, news, user_question="", language="id"):
    return _run_async(analyze_ticker_v4(data, news, user_question, language=language))

def generate_daily_picks(candidates, news_context, regime=None, coal_data=None):
    return _run_async(generate_daily_picks_v4(candidates, news_context, regime, coal_data))

CHAT_SYSTEM_PROMPT = """Kamu adalah asisten informasi pasar IDX. Jawab hanya berdasarkan informasi yang tersedia dalam percakapan.
Jika data tidak cukup, pertanyaan ambigu, atau pengguna meminta rekomendasi saham tanpa ticker dan data analisis, jangan mengarang dan jangan memberi rekomendasi. Minta pengguna memakai !picks atau !analisis TICKER.
Gunakan bahasa Indonesia yang profesional dan ringkas. Jangan gunakan emoji atau simbol dekoratif."""


def _chat_route_guard(user_message: str) -> str | None:
    """Handle factual market routes before the LLM can hallucinate."""
    text = (user_message or "").strip()
    lower = text.lower()
    if len(text.split()) < 2:
        return "Pertanyaan terlalu singkat. Jelaskan informasi yang ingin Anda ketahui, misalnya: harga coal, kondisi IHSG, atau analisis BBCA."

    if any(term in lower for term in ("coal", "batubara", "batu bara")):
        data = get_coal_price()
        if not data.get("available") or data.get("price", 0) <= 0:
            return "Data harga coal sedang tidak tersedia. Silakan coba lagi beberapa saat lagi."
        stale = " (menggunakan cache terakhir)" if data.get("stale") else ""
        return (
            f"Harga coal saat ini: USD {data['price']:,.2f} per ton{stale}. "
            f"Sumber: {data.get('source', 'provider pasar')}."
        )

    asks_recommendation = any(term in lower for term in (
        "rekomendasi", "saham apa", "saham bagus", "beli saham", "buy", "target harga",
        "entry", "take profit", "tp1", "tp2",
    ))
    ticker_match = re.search(r"\b[A-Z]{4}\b", text.upper())
    if asks_recommendation and not ticker_match:
        return (
            "Informasi belum cukup untuk memberikan rekomendasi. "
            "Gunakan `!picks` untuk shortlist harian atau `!analisis TICKER` "
            "untuk analisis saham tertentu."
        )

    if ticker_match and any(term in lower for term in ("analisis", "analysis", "gimana", "bagaimana", "prospek")):
        return (
            f"Untuk analisis {ticker_match.group(0)}, gunakan `!analisis {ticker_match.group(0)}` "
            "agar sistem mengambil data harga, teknikal, fundamental, dan risiko terbaru."
        )
    return None


def chat(user_message, history=None):
    guarded = _chat_route_guard(user_message)
    if guarded:
        return guarded
    msgs = list(history or []) + [{"role":"user","content":user_message}]
    return _call_groq(msgs, system=CHAT_SYSTEM_PROMPT)

def analyze_portfolio(holdings):
    total_inv = sum(h["qty"]*h["avg_price"] for h in holdings)
    total_val = sum(h["qty"]*h["current_price"] for h in holdings)
    pnl_pct = (total_val-total_inv)/total_inv*100 if total_inv>0 else 0
    rows = [f"- {h['ticker']}: {h['qty']:,}lot @{h['avg_price']:,.0f}→{h['current_price']:,.0f} "
            f"P&L:{(h['current_price']-h['avg_price'])*h['qty']:+,.0f} ({(h['current_price']/h['avg_price']-1)*100:+.2f}%)"
            for h in holdings]
    prompt = f"""Analisis portfolio:
{chr(10).join(rows)}
Total: Rp{total_inv:,.0f} → Rp{total_val:,.0f} | P&L: {pnl_pct:+.2f}%
Berikan: 1.Konsentrasi & diversifikasi 2.Best/worst performer 3.Top 3 risiko 4.Rebalancing (Kelly-aware) 5.Exit strategy posisi merugi"""
    return _call_groq([{"role":"user","content":prompt}], max_tokens=1200)

def analyze_backtest(ticker, results):
    mc = results.get("monte_carlo") or {}
    mc_t = ""
    if mc.get("median_return") is not None:
        mc_t = f"MC ({mc.get('n_simulations',0):,} sim): Median {mc['median_return']:+.2f}% P5 {mc.get('p5_return',0):+.2f}% P95 {mc.get('p95_return',0):+.2f}% ProbPos {mc.get('probability_positive',0):.1f}% Conf:{mc.get('confidence','N/A')}\n"
    entry_breakdown = results.get("entry_breakdown") or {}
    prompt = f"""Evaluasi singkat backtest {ticker} ({results.get("period","?")}):
Trades:{results.get("total_trades",0)} WinRate:{results.get("win_rate",0):.1f}% PF:{results.get("profit_factor",0):.2f} Return:{results.get("total_return",0):.2f}% MaxDD:{results.get("max_drawdown",0):.2f}% Sharpe:{results.get("sharpe",0):.3f}
Mode:{results.get("entry_mode","all")} | Regime:{results.get("regime_state","N/A")} | Risk/Trade:{results.get("risk_per_trade_pct",0):.2f}% | Kelly:{results.get("kelly_fraction_pct",0):.2f}%
Entries: Momentum {entry_breakdown.get("MOMENTUM",0)} Pullback {entry_breakdown.get("PULLBACK",0)} Breakout {entry_breakdown.get("BREAKOUT",0)}
{mc_t}
    Aturan:
    - Jawab dalam bahasa Indonesia profesional, maksimal 12 baris.
    - Jangan gunakan emoji atau simbol dekoratif.
    - Jika total trades < 30, sebutkan bahwa sampel historis terbatas dan confidence rendah.
    - Jangan menyimpulkan dynamic exit bekerja atau tidak jika tidak ada metrik exit yang diberikan.
    - Jangan menyebut multi-entry rusak hanya karena satu mode entry dominan; jelaskan bahwa mode lain tidak aktif pada periode tersebut.
    - Jangan memberi rekomendasi live trading jika return negatif, profit factor < 1, atau Sharpe negatif.

    Format wajib:
    Status: Layak / Belum layak live trading — satu alasan utama.
    Sampel: jumlah trade dan dampaknya pada confidence.
    Entry dan exit: observasi yang benar-benar didukung data; tulis "data tidak tersedia" jika perlu.
    Risiko Monte Carlo: median, probabilitas positif, dan P5 secara ringkas.
    Tindakan: maksimal tiga perbaikan yang paling relevan."""
    return _call_groq([{"role":"user","content":prompt}], max_tokens=900)

def get_groq_status() -> dict[str, str | bool]:
    return {
        "configured": configured_provider_count() > 0,
        "status": "configured" if configured_provider_count() > 0 else "invalid",
        "message": f"{configured_provider_count()} provider(s) configured" if configured_provider_count() > 0 else _client_error,
    }
