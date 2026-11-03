import os
import time
import sqlite3
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="AI TRADER PRO", page_icon="📈", layout="wide")

BASE_URL = "https://api.kraken.com/0/public"
DB_PATH = os.getenv("DB_PATH", "/var/data/trader.db")
SCAN_SECONDS = 60
LEVERAGE = 10.0
MARGIN_FRACTION = 0.05
STOP_LOSS_PCT = 2.0
TAKE_PROFIT_PCT = 4.0

COINS = {
    "Bitcoin": "XBTUSD", "Ethereum": "ETHUSD", "Solana": "SOLUSD",
    "XRP": "XRPUSD", "Cardano": "ADAUSD", "Dogecoin": "XDGUSD",
    "Avalanche": "AVAXUSD", "Chainlink": "LINKUSD", "Polkadot": "DOTUSD",
    "Litecoin": "LTCUSD", "Bitcoin Cash": "BCHUSD", "Stellar": "XLMUSD",
    "Cosmos": "ATOMUSD", "Uniswap": "UNIUSD", "Ethereum Classic": "ETCUSD",
    "Aave": "AAVEUSD", "NEAR": "NEARUSD", "Sui": "SUIUSD", "TRON": "TRXUSD",
}

def db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK(id=1), cash REAL NOT NULL,
            initial_capital REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
            last_scan TEXT)""")
        c.execute("""INSERT OR IGNORE INTO account
            (id,cash,initial_capital,enabled,last_scan) VALUES (1,10000,10000,0,'-')""")
        c.execute("""CREATE TABLE IF NOT EXISTS positions (
            coin TEXT PRIMARY KEY, pair TEXT NOT NULL, side TEXT NOT NULL,
            entry REAL NOT NULL, qty REAL NOT NULL, margin REAL NOT NULL,
            opened TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, action TEXT,
            coin TEXT, side TEXT, price REAL, qty REAL, margin REAL,
            pnl REAL, reason TEXT)""")

@st.cache_data(ttl=45, show_spinner=False)
def get_ohlc(pair, interval=15):
    r = requests.get(BASE_URL + "/OHLC",
        params={"pair": pair, "interval": interval}, timeout=15,
        headers={"User-Agent": "AI-Trader-Pro/3.0"})
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise ValueError(", ".join(payload["error"]))
    result = payload.get("result", {})
    key = next((k for k in result if k != "last"), None)
    if not key:
        raise ValueError("Kraken veri döndürmedi")
    d = pd.DataFrame(result[key], columns=[
        "time","open","high","low","close","vwap","volume","count"])
    for col in ["open","high","low","close","volume"]:
        d[col] = pd.to_numeric(d[col], errors="coerce")
    return d.dropna().reset_index(drop=True)

def signal_for(pair):
    d = get_ohlc(pair, 15).copy()
    if len(d) < 55:
        raise ValueError("Yeterli mum verisi yok")
    d["ema20"] = d.close.ewm(span=20, adjust=False).mean()
    d["ema50"] = d.close.ewm(span=50, adjust=False).mean()
    delta = d.close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-12)
    d["rsi"] = 100 - 100/(1+rs)
    macd = d.close.ewm(span=12, adjust=False).mean() - d.close.ewm(span=26, adjust=False).mean()
    d["macd"] = macd
    d["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
    x = d.iloc[-1]
    score = int(x.close > x.ema20) + int(x.ema20 > x.ema50) + int(x.macd > x.macd_signal)
    score -= int(x.close <= x.ema20) + int(x.ema20 <= x.ema50) + int(x.macd <= x.macd_signal)
    if x.rsi < 35:
        score += 1
    elif x.rsi > 70:
        score -= 1
    sig = "LONG" if score >= 2 else "SHORT" if score <= -2 else "WAIT"
    return float(x.close), sig, float(x.rsi), score

def close_position(c, coin, pos, price, reason):
    direction = 1 if pos["side"] == "LONG" else -1
    pnl = (price / pos["entry"] - 1) * direction * pos["qty"]
    c.execute("UPDATE account SET cash=cash+? WHERE id=1", (pos["margin"] + pnl,))
    c.execute("INSERT INTO trades(at,action,coin,side,price,qty,margin,pnl,reason) VALUES(?,?,?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), "CLOSE", coin, pos["side"],
         price, pos["qty"], pos["margin"], pnl, reason))
    c.execute("DELETE FROM positions WHERE coin=?", (coin,))

def run_scan():
    init_db()
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(signal_for, pair): (coin, pair) for coin, pair in COINS.items()}
        for f in as_completed(futures):
            coin, pair = futures[f]
            try:
                price, signal, rsi, score = f.result()
                results[coin] = (pair, price, signal, rsi, score)
            except Exception as e:
                errors[coin] = str(e)

    with db() as c:
        account = c.execute("SELECT * FROM account WHERE id=1").fetchone()
        if account["enabled"]:
            # Önce açık pozisyonları stop, hedef veya ters sinyalle kapat.
            for row in c.execute("SELECT * FROM positions").fetchall():
                if row["coin"] not in results:
                    continue
                pair, price, signal, _, _ = results[row["coin"]]
                direction = 1 if row["side"] == "LONG" else -1
                move_pct = (price / row["entry"] - 1) * direction * 100
                reason = ("STOP LOSS" if move_pct <= -STOP_LOSS_PCT else
                          "TAKE PROFIT" if move_pct >= TAKE_PROFIT_PCT else
                          "Ters sinyal" if signal in ("LONG","SHORT") and signal != row["side"] else None)
                if reason:
                    close_position(c, row["coin"], dict(row), price, reason)

            # Her yeni pozisyon için başlangıç sermayesinin %5'i teminat.
            account = c.execute("SELECT * FROM account WHERE id=1").fetchone()
            margin_per_trade = account["initial_capital"] * MARGIN_FRACTION
            for coin, (pair, price, signal, _, _) in results.items():
                exists = c.execute("SELECT 1 FROM positions WHERE coin=?", (coin,)).fetchone()
                if exists or signal not in ("LONG","SHORT"):
                    continue
                margin = min(margin_per_trade, account["cash"])
                if margin < 10:
                    break
                notional = margin * LEVERAGE
                qty = notional / price
                c.execute("UPDATE account SET cash=cash-? WHERE id=1", (margin,))
                c.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?)",
                    (coin,pair,signal,price,qty,margin,datetime.now(timezone.utc).isoformat()))
                c.execute("INSERT INTO trades(at,action,coin,side,price,qty,margin,pnl,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(),"OPEN",coin,signal,price,qty,margin,0.0,
                     "10x kaldıraç; sermayenin %5'i teminat"))
        c.execute("UPDATE account SET last_scan=? WHERE id=1",
                  (datetime.now(timezone.utc).isoformat(),))
    return results, errors

_worker_started = False
_worker_lock = threading.Lock()

def background_worker():
    init_db()
    while True:
        try:
            run_scan()
        except Exception:
            pass
        time.sleep(SCAN_SECONDS)

def start_worker():
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=background_worker, daemon=True, name="trader-worker").start()
            _worker_started = True

init_db()
start_worker()

st.title("📈 AI TRADER PRO — SANAL VADELİ")
st.warning("Bu sürüm yalnızca SANAL vadeli işlem simülasyonudur. Gerçek borsaya emir göndermez. 10x kaldıraç yüksek risklidir; sonuçlar komisyon/funding içermeyen yaklaşık simülasyondur.")
with db() as c:
    account = c.execute("SELECT * FROM account WHERE id=1").fetchone()
    positions = [dict(x) for x in c.execute("SELECT * FROM positions ORDER BY coin").fetchall()]
    trades = [dict(x) for x in c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 100").fetchall()]

st.subheader("Bot kontrolü")
enabled = st.toggle("Arka plan otomatik sanal vadeli işlemi etkinleştir", value=bool(account["enabled"]))
if bool(enabled) != bool(account["enabled"]):
    with db() as c:
        c.execute("UPDATE account SET enabled=? WHERE id=1", (int(enabled),))
    st.rerun()

m1,m2,m3,m4 = st.columns(4)
m1.metric("Sanal bakiye", f"${account['cash']:,.2f}")
m2.metric("Kaldıraç", "10x")
m3.metric("İşlem teminatı", "%5")
m4.metric("Son arka plan taraması", account["last_scan"][:19].replace("T"," ") if account["last_scan"] != "-" else "Henüz yok")

st.caption("Bot, Render web servisi çalıştığı sürece tarama yapar; tarayıcıyı kapatmanız gerekmez. Render servisi uykuya alınır/yeniden başlatılırsa tarama durur. Kalıcı disk önerilir.")
st.subheader("Açık sanal pozisyonlar")
if positions:
    rows = []
    for p in positions:
        try:
            price, _, _, _ = signal_for(p["pair"])
            direction = 1 if p["side"] == "LONG" else -1
            pnl = (price/p["entry"]-1)*direction*p["qty"]
            rows.append({"Coin":p["coin"],"Yön":p["side"],"Giriş":p["entry"],
                         "Anlık":price,"Kaldıraç":"10x","Teminat":p["margin"],
                         "Tahmini K/Z":pnl,"K/Z % (teminat)":pnl/p["margin"]*100})
        except Exception as e:
            rows.append({"Coin":p["coin"],"Yön":p["side"],"Hata":str(e)})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.info("Açık pozisyon yok. Bot etkinleştirilirse sinyale göre sanal pozisyon açabilir.")

st.subheader("Piyasa tarayıcı")
if st.button("Piyasayı şimdi tara"):
    st.cache_data.clear()
    with st.spinner("Coinler taranıyor..."):
        scan_results, scan_errors = run_scan()
    st.success(f"{len(scan_results)} coin verisi alındı.")
    if scan_errors:
        st.warning("Bazı pariteler Kraken tarafından desteklenmiyor veya veri alınamadı: " + ", ".join(scan_errors))
    st.rerun()

market = []
for coin, pair in COINS.items():
    try:
        price, sig, rsi, score = signal_for(pair)
        market.append({"Coin":coin,"Parite":pair,"Fiyat USD":price,"RSI":round(rsi,1),
                       "Skor":score,"Sinyal":sig})
    except Exception as e:
        market.append({"Coin":coin,"Parite":pair,"Hata":str(e)})
st.dataframe(pd.DataFrame(market), use_container_width=True, hide_index=True)

st.subheader("Son sanal işlemler")
if trades:
    st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
else:
    st.caption("Henüz işlem kaydı yok.")
st.caption("Pozisyon boyutu: teminat × 10. Her işlemde başlangıç sermayesinin %5'i teminat olarak ayrılır. Stop-loss %2, take-profit %4; tasfiye/funding/komisyon modeli basitleştirilmiştir.")
