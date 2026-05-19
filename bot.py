import os
import asyncio
import logging
import requests
import time
import schedule
from io import StringIO
from datetime import datetime
import pandas as pd
import numpy as np
from telegram import Bot
from telegram.constants import ParseMode

# ── CONFIGURAZIONE ──────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "8910207412:AAH1X3-K1DbjoukXh3keTgY4y3KQWmrlVx4")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "2025845923")

RSI_OVERSOLD   = 35
RSI_OVERBOUGHT = 65

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── DOWNLOAD DATI ───────────────────────────

def fetch_stock(ticker: str):
    try:
        url = f"https://stooq.com/q/d/l/?s={ticker.lower()}&i=d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200 or len(r.text) < 100:
            return None
        df = pd.read_csv(StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns={"Date":"date","Close":"close","Volume":"volume"})
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").tail(90)
    except Exception as e:
        log.warning(f"Stock error {ticker}: {e}")
        return None

def fetch_crypto(coin_id: str):
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        r = requests.get(url, params={"vs_currency":"usd","days":"90","interval":"daily"},
                         headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        prices  = data.get("prices", [])
        volumes = data.get("total_volumes", [])
        df = pd.DataFrame(prices, columns=["ts","close"])
        df["volume"] = [v[1] for v in volumes[:len(df)]]
        df["date"]   = pd.to_datetime(df["ts"], unit="ms")
        return df.tail(90)
    except Exception as e:
        log.warning(f"Crypto error {coin_id}: {e}")
        return None

# ── INDICATORI ──────────────────────────────

def rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))

def macd_hist(s):
    e12 = s.ewm(span=12, adjust=False).mean()
    e26 = s.ewm(span=26, adjust=False).mean()
    mac = e12 - e26
    sig = mac.ewm(span=9, adjust=False).mean()
    return mac - sig

# ── ANALISI ─────────────────────────────────

def analyze(ticker, is_crypto=False):
    try:
        df = fetch_crypto(ticker) if is_crypto else fetch_stock(ticker)
        if df is None or len(df) < 30:
            return None

        close  = df["close"].astype(float)
        volume = df["volume"].astype(float)

        cur_rsi  = float(rsi(close).iloc[-1])
        mh       = macd_hist(close)
        cur_mh   = float(mh.iloc[-1])
        prev_mh  = float(mh.iloc[-2])
        momentum = float(close.pct_change(10).iloc[-1] * 100)
        cur_p    = float(close.iloc[-1])
        chg      = (cur_p - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        avg_vol  = float(volume.iloc[-20:].mean())
        vol_r    = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0

        score, reasons = 0, []

        if cur_rsi < RSI_OVERSOLD:
            score += 2; reasons.append(f"RSI oversold {cur_rsi:.0f}")
        elif cur_rsi > RSI_OVERBOUGHT:
            score -= 2; reasons.append(f"RSI overbought {cur_rsi:.0f}")

        if cur_mh > 0 and prev_mh <= 0:
            score += 2; reasons.append("MACD cross rialzista ✅")
        elif cur_mh < 0 and prev_mh >= 0:
            score -= 2; reasons.append("MACD cross ribassista ❌")
        elif cur_mh > 0:
            score += 1
        else:
            score -= 1

        if momentum > 5:
            score += 1; reasons.append(f"Momentum +{momentum:.1f}%")
        elif momentum < -5:
            score -= 1; reasons.append(f"Momentum {momentum:.1f}%")

        if vol_r >= 1.3:
            if score > 0: score += 1; reasons.append(f"Vol alto x{vol_r:.1f}")
            else:         score -= 1; reasons.append(f"Vol alto x{vol_r:.1f}")

        if score >= 3:   signal, emoji = "🟢 COMPRA",      "🚀"
        elif score <= -3: signal, emoji = "🔴 VENDI/EVITA", "⚠️"
        else:             signal, emoji = "🟡 ATTENDI",     "⏳"

        label    = ticker.upper() if not is_crypto else ticker.capitalize()
        price_fmt = f"{cur_p:,.2f}" if cur_p > 10 else f"{cur_p:,.5f}"

        return dict(ticker=label, price=price_fmt, chg=chg,
                    rsi=cur_rsi, score=score, signal=signal,
                    emoji=emoji, reasons=reasons)
    except Exception as e:
        log.warning(f"Analyze error {ticker}: {e}")
        return None

# ── MESSAGGIO ───────────────────────────────

def build_msg(results):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    lines = ["📊 *SEGNALI DI TRADING*", f"🕐 {now}", "━━━━━━━━━━━━━━━━━━━━━━", ""]
    for cat, items in results.items():
        if not items: continue
        lines.append(f"*{cat}*")
        for r in sorted(items, key=lambda x: abs(x["score"]), reverse=True):
            sign = "+" if r["chg"] >= 0 else ""
            rs   = " · ".join(r["reasons"][:2]) if r["reasons"] else "neutro"
            lines.append(
                f"{r['emoji']} *{r['ticker']}*  `{r['signal']}`\n"
                f"   💵 ${r['price']}  ({sign}{r['chg']:.1f}%)  RSI {r['rsi']:.0f}\n"
                f"   📋 {rs}"
            )
        lines.append("")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              "⚠️ _Solo informativo\\. Non è consulenza finanziaria\\._"]
    return "\n".join(lines)

# ── JOB ─────────────────────────────────────

async def run():
    log.info("Avvio analisi...")
    bot = Bot(token=TELEGRAM_TOKEN)

    results = {
        "🇺🇸 Azioni USA":  [r for t in ["NVDA","TSLA","AMD","PLTR","COIN"] if (r:=analyze(t))],
        "🇪🇺 Azioni EU/IT": [r for t in ["STM.MI","ENI.MI","UCG.MI"]       if (r:=analyze(t))],
        "₿ Crypto":        [r for t in ["bitcoin","ethereum","solana","dogecoin"] if (r:=analyze(t,True))],
    }

    msg = build_msg(results)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN_V2)
    log.info("Messaggio inviato ✅")

def job():
    asyncio.run(run())

# ── MAIN ────────────────────────────────────

if __name__ == "__main__":
    log.info("Bot avviato. Segnali alle 09:00 e 15:30.")
    job()
    schedule.every().day.at("09:00").do(job)
    schedule.every().day.at("15:30").do(job)
    while True:
        schedule.run_pending()
        time.sleep(60)
