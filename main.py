import os
import time
import hmac
import hashlib
import sqlite3
import threading
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

# AI TRADER PRO — Binance USDⓈ-M Futures
# Gerçek emir gönderimi varsayılan olarak KAPALIDIR.
# Açmak için Render'da ALLOW_LIVE_TRADING=true ve API anahtarları gerekir.

st.set_page_config(page_title="AI TRADER PRO | Binance Futures", page_icon="📈", layout="wide")

BASE_URL = os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com").rstrip("/")
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
ALLOW_LIVE = os.getenv("ALLOW_LIVE_TRADING", "false").lower() == "true"
DB_PATH = os.getenv("DB_PATH", "/tmp/ai_trader_binance.db")

LEVERAGE = 5
MARGIN_FRACTION = 0.05
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04
SCAN_SECONDS = 60
MAX_OPEN_POSITIONS = 1
MAX_DAILY_LOSS_FRACTION = 0.05
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]

session = requests.Session()
session.headers.update({"User-Agent": "AI-TRADER-PRO/4.0"})

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK(id=1),
            live_enabled INTEGER NOT NULL DEFAULT 0,
            last_scan TEXT NOT NULL DEFAULT '-',
            last_message TEXT NOT NULL DEFAULT 'Henüz tarama yapılmadı'
        )""")
        c.execute("INSERT OR IGNORE INTO settings(id,live_enabled) VALUES(1,0)")
        c.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, level TEXT,
            symbol TEXT, message TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS daily (
            day TEXT PRIMARY KEY, start_equity REAL, realized_pnl REAL DEFAULT 0
        )""")

def log(level, message, symbol=""):
    with db() as c:
        c.execute("INSERT INTO events(at,level,symbol,message) VALUES(?,?,?,?)",
                  (now_iso(), level, symbol, str(message)[:1000]))

def public_get(path, params=None):
    r = session.get(BASE_URL + path, params=params or {}, timeout=12)
    r.raise_for_status()
    return r.json()

def signed_request(method, path, params=None):
    if not API_KEY or not API_SECRET:
        raise RuntimeError("Binance API anahtarları Render Environment Variables bölümünde tanımlı değil.")
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 5000
    query = urlencode(p, doseq=True)
    signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query}&signature={signature}"
    r = session.request(method, url, headers={"X-MBX-APIKEY": API_KEY}, timeout=15)
    try:
        data = r.json()
    except Exception:
        data = {"msg": r.text}
    if not r.ok:
        raise RuntimeError(f"Binance API {r.status_code}: {data}")
    return data

def get_klines(symbol, interval="15m", limit=120):
    data = public_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    d = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume","close_time",
        "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    for col in ("open","high","low","close","volume"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    return d.dropna().reset_index(drop=True)

def make_signal(symbol):
    d = get_klines(symbol)
    if len(d) < 60:
        return None
    d["ema20"] = d.close.ewm(span=20, adjust=False).mean()
    d["ema50"] = d.close.ewm(span=50, adjust=False).mean()
    macd = d.close.ewm(span=12, adjust=False).mean() - d.close.ewm(span=26, adjust=False).mean()
    d["macd"] = macd
    d["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
    # Kapanmış son mum kullanılır; oluşmakta olan mumdan sinyal üretmez.
    x = d.iloc[-2]
    if x.close > x.ema20 > x.ema50 and x.macd > x.macd_signal:
        side = "LONG"
    elif x.close < x.ema20 < x.ema50 and x.macd < x.macd_signal:
        side = "SHORT"
    else:
        side = "WAIT"
    return {"symbol": symbol, "price": float(d.iloc[-1].close), "signal": side,
            "signal_close": float(x.close), "ema20": float(x.ema20), "ema50": float(x.ema50)}

def get_exchange_rules(symbol):
    info = public_get("/fapi/v1/exchangeInfo")
    item = next((x for x in info["symbols"] if x["symbol"] == symbol), None)
    if not item or item["status"] != "TRADING":
        raise RuntimeError(f"{symbol} şu anda işlem göremiyor.")
    filters = {f["filterType"]: f for f in item["filters"]}
    lot = filters["LOT_SIZE"]
    price_filter = filters["PRICE_FILTER"]
    min_notional = filters.get("MIN_NOTIONAL", {}).get("notional", "5")
    return Decimal(lot["stepSize"]), Decimal(lot["minQty"]), Decimal(price_filter["tickSize"]), Decimal(str(min_notional))

def floor_step(value, step):
    value, step = Decimal(str(value)), Decimal(str(step))
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

def account_info():
    return signed_request("GET", "/fapi/v2/account")

def position_risk():
    return signed_request("GET", "/fapi/v2/positionRisk")

def get_mark_price(symbol):
    return float(public_get("/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])

def place_market(symbol, side, qty):
    return signed_request("POST", "/fapi/v1/order", {
        "symbol": symbol, "side": side, "type": "MARKET",
        "quantity": format(qty, "f"), "newOrderRespType": "RESULT"
    })

def place_protective_order(symbol, side, order_type, trigger_price):
    # Binance USD-M conditional/algo order endpoint.
    return signed_request("POST", "/fapi/v1/algoOrder", {
        "algoType": "CONDITIONAL", "symbol": symbol, "side": side,
        "type": order_type, "triggerPrice": format(trigger_price, "f"),
        "closePosition": "true", "workingType": "MARK_PRICE"
    })

def set_leverage(symbol):
    return signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})

def current_position_for(symbol):
    rows = position_risk()
    for p in rows:
        if p["symbol"] == symbol and abs(float(p["positionAmt"])) > 0:
            return p
    return None

def today_realized_pnl():
    rows = signed_request("GET", "/fapi/v1/income", {"incomeType": "REALIZED_PNL", "startTime": int(time.time()*1000)-86400000, "limit": 1000})
    return sum(float(x["income"]) for x in rows)

def daily_loss_guard(equity):
    pnl = today_realized_pnl()
    max_loss = max(0.0, equity * MAX_DAILY_LOSS_FRACTION)
    if pnl <= -max_loss:
        raise RuntimeError(f"Günlük zarar limiti doldu ({pnl:.4f} USDT). Yeni işlem durduruldu.")
    return pnl

def open_trade(symbol, direction, equity):
    # direction LONG/SHORT. Risk sizing is based on available USDT balance.
    acct = account_info()
    available = float(acct.get("availableBalance", 0))
    margin = min(available * MARGIN_FRACTION, equity * MARGIN_FRACTION)
    if margin <= 0:
        raise RuntimeError("Kullanılabilir USDT teminatı yok.")
    mark = get_mark_price(symbol)
    notional = margin * LEVERAGE
    step, min_qty, tick, min_notional = get_exchange_rules(symbol)
    raw_qty = Decimal(str(notional / mark))
    qty = floor_step(raw_qty, step)
    if qty < min_qty:
        raise RuntimeError(f"{symbol}: hesaplanan miktar minimum miktarın altında; emir atlanıyor.")
    actual_notional = float(qty) * mark
    if Decimal(str(actual_notional)) < min_notional:
        raise RuntimeError(f"{symbol}: pozisyon büyüklüğü {actual_notional:.4f} USDT; Binance minimum nominali {min_notional} USDT. Emir gönderilmedi.")
    set_leverage(symbol)
    order_side = "BUY" if direction == "LONG" else "SELL"
    order = place_market(symbol, order_side, qty)
    filled_qty = Decimal(str(order.get("executedQty") or qty))
    entry = float(order.get("avgPrice") or mark)
    if entry <= 0 or filled_qty <= 0:
        raise RuntimeError("Piyasa emri gerçekleşmiş miktar/fiyat döndürmedi; pozisyon doğrulanmalı.")
    close_side = "SELL" if direction == "LONG" else "BUY"
    if direction == "LONG":
        sl = floor_step(entry * (1 - STOP_LOSS_PCT), tick)
        tp = floor_step(entry * (1 + TAKE_PROFIT_PCT), tick)
    else:
        sl = floor_step(entry * (1 + STOP_LOSS_PCT), tick)
        tp = floor_step(entry * (1 - TAKE_PROFIT_PCT), tick)
    try:
        sl_order = place_protective_order(symbol, close_side, "STOP_MARKET", sl)
        tp_order = place_protective_order(symbol, close_side, "TAKE_PROFIT_MARKET", tp)
    except Exception as e:
        # Koruyucu emirlerden biri kurulamazsa açık pozisyonu piyasa emriyle kapatmayı dene.
        try:
            signed_request("POST", "/fapi/v1/order", {
                "symbol": symbol, "side": close_side, "type": "MARKET",
                "quantity": format(filled_qty, "f"), "reduceOnly": "true"
            })
        finally:
            raise RuntimeError(f"Koruyucu SL/TP kurulamadı; pozisyonu kapatma denendi. Ayrıntı: {e}")
    log("ORDER", f"{direction} açıldı. Qty={filled_qty}, giriş={entry}, SL={sl}, TP={tp}; emir={order.get('orderId')}", symbol)
    return order, sl_order, tp_order

def scan_once(live_enabled):
    results, errors = [], []
    for symbol in SYMBOLS:
        try:
            results.append(make_signal(symbol))
        except Exception as e:
            errors.append(f"{symbol}: {e}")
    with db() as c:
        c.execute("UPDATE settings SET last_scan=?,last_message=? WHERE id=1",
                  (now_iso(), f"{len(results)} sembol tarandı; {len(errors)} hata"))
    if live_enabled:
        if not ALLOW_LIVE:
            raise RuntimeError("Canlı emir izni kapalı. Render'da ALLOW_LIVE_TRADING=true olmadan emir gönderilmez.")
        if not API_KEY or not API_SECRET:
            raise RuntimeError("Binance API anahtarları eksik.")
        acct = account_info()
        equity = float(acct.get("totalWalletBalance", 0)) + float(acct.get("totalUnrealizedProfit", 0))
        daily_loss_guard(equity)
        # Tek seferde tek bot pozisyonu; mevcut hesap pozisyonlarına dokunmaz.
        open_positions = [p for p in position_risk() if abs(float(p["positionAmt"])) > 0]
        if open_positions:
            return results, errors, "Hesapta açık Futures pozisyonu var; yeni bot emri güvenlik için atlandı."
        for item in results:
            if item and item["signal"] in ("LONG", "SHORT"):
                try:
                    open_trade(item["symbol"], item["signal"], equity)
                    break
                except Exception as e:
                    errors.append(f"{item['symbol']} emir açılmadı: {e}")
                    log("WARN", e, item["symbol"])
    return results, errors, "Tarama tamamlandı."

_worker_started = False
_worker_lock = threading.Lock()

def worker():
    while True:
        try:
            with db() as c:
                enabled = bool(c.execute("SELECT live_enabled FROM settings WHERE id=1").fetchone()[0])
            scan_once(enabled)
        except Exception as e:
            log("ERROR", e)
        time.sleep(SCAN_SECONDS)

def start_worker():
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=worker, daemon=True, name="binance-futures-worker").start()
            _worker_started = True

init_db()
start_worker()

st.title("📈 AI TRADER PRO — BINANCE FUTURES")
st.warning("Gerçek para riski vardır. Gerçek emir modu varsayılan olarak kapalıdır. 5x kaldıraç, bakiye teminatının %5'i, %2 stop-loss ve %4 take-profit kullanılır. Sinyaller EMA20/EMA50 + MACD teknik kurallarıdır; kâr garantisi yoktur.")
if not ALLOW_LIVE:
    st.info("Canlı emir izni kapalı. Önce Render Environment Variables bölümünde ALLOW_LIVE_TRADING=true ayarlanmalıdır.")
if not API_KEY or not API_SECRET:
    st.info("Binance API anahtarları tanımlı değil. API Key ve Secret yalnızca Render Environment Variables içinde saklanmalıdır.")

with db() as c:
    settings = dict(c.execute("SELECT * FROM settings WHERE id=1").fetchone())
    events = [dict(x) for x in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30").fetchall()]

st.subheader("İşlem kontrolü")
confirm = st.checkbox("Gerçek para ile otomatik Futures emri gönderilmesini ve kayıp riskini kabul ediyorum.")
can_enable = ALLOW_LIVE and bool(API_KEY and API_SECRET) and confirm
requested = st.toggle("OTOMATİK GERÇEK İŞLEM", value=bool(settings["live_enabled"]), disabled=not can_enable)
if requested != bool(settings["live_enabled"]):
    with db() as c:
        c.execute("UPDATE settings SET live_enabled=? WHERE id=1", (int(requested),))
    log("CONTROL", "Canlı otomatik işlem " + ("ETKİN" if requested else "KAPALI"))
    st.rerun()
if not can_enable and not settings["live_enabled"]:
    st.caption("Gerçek işlem anahtarı için: ALLOW_LIVE_TRADING=true + API izinleri + yukarıdaki risk onayı gerekir.")

col1, col2, col3 = st.columns(3)
col1.metric("Kaldıraç", f"{LEVERAGE}x")
col2.metric("İşlem teminatı", "%5 kullanılabilir bakiye")
col3.metric("Tarama aralığı", "60 saniye")

if st.button("Şimdi piyasayı tara"):
    with st.spinner("Binance Futures verileri alınıyor..."):
        try:
            res, errs, msg = scan_once(False)
            st.success(msg)
            if errs:
                st.warning(" | ".join(errs))
        except Exception as e:
            st.error(str(e))
    st.rerun()

st.subheader("Canlı piyasa sinyalleri")
market = []
for symbol in SYMBOLS:
    try:
        x = make_signal(symbol)
        market.append(x)
    except Exception as e:
        market.append({"symbol": symbol, "signal": "HATA", "price": None, "ema20": None, "ema50": None})
st.dataframe(pd.DataFrame(market), use_container_width=True, hide_index=True)

if API_KEY and API_SECRET:
    st.subheader("Binance Futures hesap durumu")
    try:
        a = account_info()
        b1,b2,b3 = st.columns(3)
        b1.metric("Wallet balance", f"{float(a.get('totalWalletBalance',0)):.4f} USDT")
        b2.metric("Available balance", f"{float(a.get('availableBalance',0)):.4f} USDT")
        b3.metric("Unrealized PnL", f"{float(a.get('totalUnrealizedProfit',0)):.4f} USDT")
        positions = [p for p in position_risk() if abs(float(p["positionAmt"])) > 0]
        st.write("Açık Futures pozisyonları")
        if positions:
            st.dataframe(pd.DataFrame([{
                "Sembol": p["symbol"], "Miktar": p["positionAmt"],
                "Giriş": p["entryPrice"], "Mark": p["markPrice"],
                "Gerçekleşmemiş K/Z": p["unRealizedProfit"],
                "Likidasyon fiyatı": p["liquidationPrice"]
            } for p in positions]), use_container_width=True, hide_index=True)
        else:
            st.info("Açık Futures pozisyonu bulunamadı.")
    except Exception as e:
        st.error(f"Hesap bilgisi alınamadı: {e}")

st.subheader("Bot olayları ve emir kayıtları")
if events:
    st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
else:
    st.info("Henüz bot olayı yok.")

st.caption("Uyarı: Sunucu/bağlantı kesintisi, kayma, likidasyon, komisyon ve funding kayıpları artırabilir. Stop emri mutlak fiyat garantisi vermez. Bu yazılım yatırım tavsiyesi değildir.")
