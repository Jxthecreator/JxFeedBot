# ============================================================
#  JxFeedBot - Multi-Coin Telegram Price Poster (CoinGecko REST)
#  Coins: BTC, ETH, BNB, SOL, XRP, XPR
#  Render WebService keep-alive via Flask
# ============================================================

import os, time, signal, sys, requests, threading
from dotenv import load_dotenv

# --- keep-alive server for Render ---
from flask import Flask
app = Flask(__name__)

@app.get("/")
def home():
    return "JxFeedBot running ✅"

def keep_alive():
    port = int(os.getenv("PORT", "10000"))
    # do NOT use reloader or debug in production
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# start the web server in a background thread
threading.Thread(target=keep_alive, daemon=True).start()

# ------------------- Load Environment -------------------
load_dotenv()

# ------------------- Telegram Config -------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("❌ Set TELEGRAM_BOT_TOKEN in env")

TG_SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

CHANNELS = {
    "BTC": os.getenv("CHAT_BTC", "@BTCLiveFeed").strip(),
    "ETH": os.getenv("CHAT_ETH", "@ETHLiveFeed").strip(),
    "BNB": os.getenv("CHAT_BNB", "@BNBLiveFeed").strip(),
    "SOL": os.getenv("CHAT_SOL", "@SOLLiveFeed").strip(),
    "XRP": os.getenv("CHAT_XRP", "@XRPLiveFeed").strip(),
    "XPR": os.getenv("CHAT_XPR", "@XPRLiveFeed").strip(),
}

POLL_SECONDS   = int(os.getenv("POLL_SECONDS", "60"))
PRICE_DECIMALS = int(os.getenv("PRICE_DECIMALS", "2"))
MIN_ABS_MOVE   = float(os.getenv("MIN_ABS_MOVE", "0"))
LOG_ERRORS     = os.getenv("LOG_ERRORS", "true").lower() == "true"

# ------------------- CoinGecko -------------------
CG_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "XPR": "proton",
}
CG_URL = "https://api.coingecko.com/api/v3/simple/price"
CG_PARAMS = {"ids": ",".join(CG_IDS.values()), "vs_currencies": "usd"}
CG_TIMEOUT = 15

http = requests.Session()
last_price = {s: None for s in CHANNELS}
_stop = False

def fmt_usd(v: float) -> str:
    return f"$ {v:,.{PRICE_DECIMALS}f}"

def should_post(sym: str, new_price: float) -> bool:
    prev = last_price.get(sym)
    if prev is None:
        return True
    if round(new_price, PRICE_DECIMALS) != round(prev, PRICE_DECIMALS):
        if MIN_ABS_MOVE > 0 and abs(new_price - prev) < MIN_ABS_MOVE:
            return False
        return True
    return False

def post_price(sym: str, price: float):
    payload = {"chat_id": CHANNELS[sym], "text": fmt_usd(price), "disable_web_page_preview": True}
    try:
        r = http.post(TG_SEND_URL, json=payload, timeout=12)
        r.raise_for_status()
        last_price[sym] = price
        print(f"[OK] {sym}: {fmt_usd(price)}")
    except Exception as e:
        if LOG_ERRORS:
            print(f"[ERROR] Telegram post failed for {sym}: {e}")

def fetch_prices() -> dict:
    r = http.get(CG_URL, params=CG_PARAMS, timeout=CG_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = {}
    for sym, cg_id in CG_IDS.items():
        usd = data.get(cg_id, {}).get("usd")
        if usd is not None:
            out[sym] = float(usd)
    return out

def loop():
    backoff = POLL_SECONDS
    print(f"✅ JxFeedBot running (every {POLL_SECONDS}s)…")
    while not _stop:
        try:
            prices = fetch_prices()
            for sym, price in prices.items():
                if sym not in CHANNELS or not CHANNELS[sym]:
                    continue
                if should_post(sym, price):
                    post_price(sym, price)
                elif last_price[sym] is None:
                    last_price[sym] = price
            time.sleep(POLL_SECONDS)
            backoff = POLL_SECONDS
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (429, 500, 502, 503, 504):
                backoff = min(max(int(backoff * 1.5), 30), 180)
                print(f"[WARN] CoinGecko HTTP {status}. Backing off {backoff}s…")
                time.sleep(backoff)
            else:
                print("[HTTP ERROR]", e)
                time.sleep(backoff)
        except Exception as e:
            print("[ERROR]", e)
            time.sleep(backoff)

def shutdown(*_):
    global _stop
    _stop = True
    try: http.close()
    finally:
        print("Shutting down cleanly…")
        sys.exit(0)

if __name__ == "__main__":
    for sym, chat in CHANNELS.items():
        if not chat:
            raise SystemExit(f"❌ Missing channel for {sym} (set CHAT_{sym})")
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    loop()
