# ============================================================
#  JxFeedBot - Multi-Coin Telegram Price Poster (CoinGecko REST)
#  Works worldwide (no VPN). Safe, stable, and auto-backoff ready.
#  Coins: BTC, ETH, BNB, SOL, XRP, XPR
#  Author: Jx (2025)
# ============================================================

import os, time, json, signal, sys, requests
from dotenv import load_dotenv

# ------------------- Load Environment -------------------
load_dotenv()

# Debug: show what environment keys are visible
print("[ENV DEBUG] Seen keys:", [k for k in os.environ if k.startswith("CHAT_") or "TELEGRAM" in k])

# ------------------- Telegram Config -------------------
raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Remove stray quotes or newlines just in case
BOT_TOKEN = raw_token.strip().strip('"').strip("'").replace("\n", "").replace("\r", "")
TG_SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

if not BOT_TOKEN or "PUT_YOUR_TOKEN_HERE" in BOT_TOKEN:
    raise SystemExit("❌ Set TELEGRAM_BOT_TOKEN in your .env / env vars")

CHANNELS = {
    "BTC": os.getenv("CHAT_BTC", "@BTCLiveFeed").strip(),
    "ETH": os.getenv("CHAT_ETH", "@ETHLiveFeed").strip(),
    "BNB": os.getenv("CHAT_BNB", "@BNBLiveFeed").strip(),
    "SOL": os.getenv("CHAT_SOL", "@SOLLiveFeed").strip(),
    "XRP": os.getenv("CHAT_XRP", "@XRPLiveFeed").strip(),
    "XPR": os.getenv("CHAT_XPR", "@XPRLiveFeed").strip(),
}

# ------------------- Bot Behavior -------------------
POLL_SECONDS    = int(os.getenv("POLL_SECONDS", "60"))     # how often to fetch
PRICE_DECIMALS  = int(os.getenv("PRICE_DECIMALS", "2"))    # rounding for changes
MIN_ABS_MOVE    = float(os.getenv("MIN_ABS_MOVE", "0"))    # skip if change smaller
LOG_ERRORS      = os.getenv("LOG_ERRORS", "true").lower() == "true"

# ------------------- CoinGecko API -------------------
CG_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "XPR": "proton",
}
CG_URL = "https://api.coingecko.com/api/v3/simple/price"
CG_PARAMS = {
    "ids": ",".join(CG_IDS.values()),
    "vs_currencies": "usd",
}
CG_TIMEOUT = 15

# ------------------- State -------------------
http = requests.Session()
last_price = {s: None for s in CHANNELS}
_stop = False


# ------------------- Helper Functions -------------------
def fmt_usd(v: float) -> str:
    return f"$ {v:,.{PRICE_DECIMALS}f}"


def should_post(sym: str, new_price: float) -> bool:
    """Return True if we should post an update for this coin."""
    prev = last_price.get(sym)
    if prev is None:
        return True
    if round(new_price, PRICE_DECIMALS) != round(prev, PRICE_DECIMALS):
        if MIN_ABS_MOVE > 0 and abs(new_price - prev) < MIN_ABS_MOVE:
            return False
        return True
    return False


def post_price(sym: str, price: float):
    """Send the message to Telegram."""
    payload = {
        "chat_id": CHANNELS[sym],
        "text": fmt_usd(price),
        "disable_web_page_preview": True,
    }
    try:
        r = http.post(TG_SEND_URL, json=payload, timeout=12)
        r.raise_for_status()
        last_price[sym] = price
        print(f"[OK] {sym}: {fmt_usd(price)}")
    except Exception as e:
        if LOG_ERRORS:
            print(f"[ERROR] Telegram post failed for {sym}: {e}")


def fetch_prices() -> dict:
    """Fetch all prices from CoinGecko."""
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
    """Main polling loop with automatic backoff."""
    backoff = POLL_SECONDS
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
            backoff = POLL_SECONDS  # reset after success

        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (429, 500, 502, 503, 504):
                backoff = min(max(backoff * 1.5, 30), 180)
                print(f"[WARN] CoinGecko HTTP {status}. Backing off {int(backoff)}s…")
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
    for sym, chat in CHANNELS.items():
        if not chat:
            raise SystemExit(f"❌ Missing channel for {sym}. Set CHAT_{sym} in env vars")
        if not (chat.startswith("@") or chat.lstrip("-").isdigit()):
            print(f"[WARN] {sym} chat '{chat}' should be @username or numeric id.")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"✅ JxFeedBot running (CoinGecko REST, every {POLL_SECONDS}s)…")

    loop()
