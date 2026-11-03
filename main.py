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
# Kalıcı disk tanımlı değilse /var/data izin hatası verir.
# /tmp varsayılanı uygulamanın açılmasını sağlar; Render yeniden başlarsa
# bu klasördeki simülasyon verileri silinebilir.
# Kalıcı disk eklendiğinde Render Environment içine DB_PATH=/var/data/trader.db yazılabilir.
DB_PATH = os.getenv("DB_PATH", "/tmp/trader.db")
SCAN_SECONDS = 60
LEVERAGE = 5.0
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

    # İki yönlü ve simetrik trend filtresi:
    # LONG için üç yükseliş koşulunun, SHORT için üç düşüş koşulunun
    # aynı anda sağlanmasını ister. Sırf işlem açmak için yön uydurmaz.
    bullish = (
        x.close > x.ema20 and
        x.ema20 > x.ema50 and
        x.macd > x.macd_signal
    )
    bearish = (
        x.close < x.ema20 and
        x.ema20 < x.ema50 and
        x.macd < x.macd_signal
    )

    score = (
        int(x.close > x.ema20) - int(x.close < x.ema20) +
        int(x.ema20 > x.ema50) - int(x.ema20 < x.ema50) +
        int(x.macd > x.macd_signal) - int(x.macd < x.macd_signal)
    )
    if bullish:
        sig = "LONG"
    elif bearish:
        sig = "SHORT"
    else:
        sig = "WAIT"

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
                # Her yeni pozisyon öncesinde güncel bakiyeyi oku.
                # Böylece aynı taramada kullanılabilir teminattan fazlası ayrılmaz.
                current_cash = c.execute(
                    "SELECT cash FROM account WHERE id=1"
                ).fetchone()["cash"]
                margin = min(margin_per_trade, current_cash)
                if margin < 10:
                    break
                notional = margin * LEVERAGE
                qty = notional / price
                c.execute("UPDATE account SET cash=cash-? WHERE id=1", (margin,))
                c.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?)",
                    (coin,pair,signal,price,qty,margin,datetime.now(timezone.utc).isoformat()))
                c.execute("INSERT INTO trades(at,action,coin,side,price,qty,margin,pnl,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(),"OPEN",coin,signal,price,qty,margin,0.0,
                     "5x kaldıraç; sermayenin %5'i teminat"))
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
st.warning("Bu sürüm yalnızca SANAL vadeli işlem simülasyonudur. Gerçek borsaya emir göndermez. 5x kaldıraç kullanılır; LONG ve SHORT sinyalleri ayrı ayrı değerlendirilir. Sonuçlar komisyon/funding içermeyen yaklaşık simülasyondur.")
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

# Gerçekleşmiş kâr/zarar yalnızca kapanan işlemlerden; açık işlemler ayrıca anlık hesaplanır.
with db() as c:
    realized_pnl = c.execute(
        "SELECT COALESCE(SUM(pnl),0) AS total FROM trades WHERE action='CLOSE'"
    ).fetchone()["total"]

m1,m2,m3,m4 = st.columns(4)
m1.metric("Kullanılabilir sanal bakiye", f"${account['cash']:,.2f}")
m2.metric("Gerçekleşmiş K/Z", f"${realized_pnl:,.2f}")
m3.metric("Kaldıraç", "5x")
m4.metric("Son arka plan taraması", account["last_scan"][:19].replace("T"," ") if account["last_scan"] != "-" else "Henüz yok")

st.caption("Açık işlemlerin K/Z'si henüz gerçekleşmemiştir; pozisyon kapandığında gerçekleşmiş K/Z'ye eklenir. Değerler komisyon ve funding hariç tahminidir.")
st.caption("Bot, Render web servisi çalıştığı sürece tarama yapar; tarayıcıyı kapatmanız gerekmez. Render servisi yeniden başlatılırsa /tmp veritabanı sıfırlanabilir.")
st.subheader("Açık sanal pozisyonlar")
open_rows = []
unrealized_pnl = 0.0
used_margin = 0.0
if positions:
    for p in positions:
        try:
            price, _, _, _ = signal_for(p["pair"])
            direction = 1 if p["side"] == "LONG" else -1
            pnl = (price/p["entry"]-1)*direction*p["qty"]
            unrealized_pnl += pnl
            used_margin += p["margin"]
            open_rows.append({"Coin":p["coin"],"Yön":p["side"],"Giriş":round(p["entry"],8),
                         "Anlık fiyat":round(price,8),"Miktar":round(p["qty"],8),
                         "Teminat ($)":round(p["margin"],2),"Açık K/Z ($)":round(pnl,4),
                         "Teminat getirisi (%)":round(pnl/p["margin"]*100,4) if p["margin"] else 0})
        except Exception as e:
            open_rows.append({"Coin":p["coin"],"Yön":p["side"],"Fiyat/KZ hatası":str(e)})
    st.dataframe(pd.DataFrame(open_rows), use_container_width=True, hide_index=True)
else:
    st.info("Açık pozisyon yok. Bot etkinleştirilirse sinyale göre sanal pozisyon açabilir.")

k1,k2,k3 = st.columns(3)
k1.metric("Açık pozisyonların toplam K/Z'si", f"${unrealized_pnl:,.4f}")
k2.metric("Ayrılmış teminat", f"${used_margin:,.2f}")
k3.metric("Toplam özsermaye (tahmini)", f"${account['cash'] + used_margin + unrealized_pnl:,.2f}")

st.subheader("Piyasa tarayıcı")
if st.button("Piyasayı şimdi tara"):
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

st.subheader("İşlem geçmişi (OPEN = açılış, CLOSE = kapanış; gerçekleşmiş K/Z CLOSE satırındadır)")
if trades:
    st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
else:
    st.caption("Henüz işlem kaydı yok.")
st.caption("Pozisyon boyutu: teminat × 5. Her işlemde başlangıç sermayesinin %5'i teminat olarak ayrılır. Stop-loss %2, take-profit %4; tasfiye/funding/komisyon modeli basitleştirilmiştir.")
