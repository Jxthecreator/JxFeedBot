# ============================================================
#  JxFeedBot - Multi-Coin Telegram Price Poster (CoinGecko REST)
#  Coins: BTC, ETH, BNB, SOL, XRP, XPR
#  Render WebService keep-alive via Flask + robust TG logging
# ============================================================

import os, time, signal, sys, requests, threading
from dotenv import load_dotenv

# ---------- keep-alive server for Render ----------
from flask import Flask
app = Flask(__name__)

@app.get("/")
def home():
    return "JxFeedBot running ✅"

def keep_alive():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# Start the web server in a background thread
threading.Thread(target=keep_alive, daemon=True).start()

# ------------------- Load Environment -------------------
load_dotenv()

# ------------------- Telegram Config -------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("❌ Set TELEGRAM_BOT_TOKEN in env")

TG_SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

def env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return v.strip() if v else v

CHANNELS = {
    "BTC": env("CHAT_BTC", "@BTCLiveFeed"),
    "ETH": env("CHAT_ETH", "@ETHLiveFeed"),
    "BNB": env("CHAT_BNB", "@BNBLiveFeed"),
    "SOL": env("CHAT_SOL", "@SOLLiveFeed"),
    "XRP": env("CHAT_XRP", "@XRPLiveFeed"),
    "XPR": env("CHAT_XPR", "@XPRLiveFeed"),
}

POLL_SECONDS   = int(env("POLL_SECONDS", "60"))
PRICE_DECIMALS = int(env("PRICE_DECIMALS", "2"))
MIN_ABS_MOVE   = float(env("MIN_ABS_MOVE", "0"))
LOG_ERRORS     = env("LOG_ERRORS", "true").lower() == "true"

# Debug: show what env keys we actually see
seen_keys = [k for k in os.environ.keys() if k.startswith("CHAT_") or "TELEGRAM" in k]
print("[ENV DEBUG] Seen keys:", seen_keys)
print("[ENV DEBUG] Channel map:", {k: CHANNELS[k] for k in CHANNELS})

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
http.headers.update({"User-Agent": "JxFeedBot/1.0"})

# ------------------- State -------------------
last_price = {s: None for s in CHANNELS}
_stop = False

# ------------------- Helpers -------------------
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

def tg_post(chat_id: str, text: str) -> requests.Response:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    r = http.post(TG_SEND_URL, json=payload, timeout=12)
    if r.status_code != 200:
        # Print exact error body so we know 403 vs 400 etc.
        print(f"[TG ERROR] status={r.status_code} chat={chat_id} body={r.text}")
    r.raise_for_status()
    return r

def post_price(sym: str, price: float):
    chat = CHANNELS[sym]
    try:
        tg_post(chat, fmt_usd(price))
        last_price[sym] = price
        print(f"[OK] {sym} → {chat}: {fmt_usd(price)}")
    except Exception as e:
        if LOG_ERRORS:
            print(f"[ERROR] Telegram post failed for {sym} → {chat}: {e}")

def startup_ping():
    """Try a 'live' message to each configured channel to surface TG errors immediately."""
    for sym, chat in CHANNELS.items():
        if not chat:
            continue
        try:
            tg_post(chat, f"🟢 JxFeedBot live for {sym}")
            print(f"[LIVE] Pinged {sym} → {chat}")
        except Exception as e:
            print(f"[LIVE ERROR] {sym} → {chat}: {e}")

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
                chat = CHANNELS.get(sym)
                if not chat:
                    continue
                # Force an initial post so wiring is obvious
                if last_price[sym] is None:
                    print(f"[INIT] First post for {sym} at {fmt_usd(price)}")
                    post_price(sym, price)
                    continue
                if should_post(sym, price):
                    post_price(sym, price)
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
    try:
        http.close()
    finally:
        print("Shutting down cleanly…")
        sys.exit(0)

# ------------------- Main -------------------
if __name__ == "__main__":
    # Basic env validation
    for sym, chat in CHANNELS.items():
        if not chat:
            raise SystemExit(f"❌ Missing channel for {sym} (set CHAT_{sym})")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Send startup 'live' pings so you immediately see TG permission/ID errors
    startup_ping()

    # Start price loop
    loop()
