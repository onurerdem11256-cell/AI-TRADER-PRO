import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="AI TRADER PRO", page_icon="📈", layout="wide")

BASE_URL = "https://api.kraken.com/0/public"
COINS = {
    "Bitcoin": "XBTUSD",
    "Ethereum": "ETHUSD",
    "Solana": "SOLUSD",
    "XRP": "XRPUSD",
    "BNB": "BNBUSD",
    "Cardano": "ADAUSD",
    "Dogecoin": "XDGUSD",
    "Avalanche": "AVAXUSD",
    "Chainlink": "LINKUSD",
    "Polkadot": "DOTUSD",
    "Litecoin": "LTCUSD",
    "Bitcoin Cash": "BCHUSD",
    "Stellar": "XLMUSD",
    "Cosmos": "ATOMUSD",
    "Uniswap": "UNIUSD",
    "Ethereum Classic": "ETCUSD",
    "Aave": "AAVEUSD",
    "NEAR": "NEARUSD",
    "Sui": "SUIUSD",
    "TRON": "TRXUSD",
}

@st.cache_data(ttl=60, show_spinner=False)
def get_ohlc(pair: str, interval: int = 15):
    response = requests.get(
        f"{BASE_URL}/OHLC",
        params={"pair": pair, "interval": interval},
        timeout=15,
        headers={"User-Agent": "AI-Trader-Pro/1.0"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise ValueError(", ".join(payload["error"]))
    result = payload.get("result", {})
    data_key = next((key for key in result if key != "last"), None)
    if not data_key:
        raise ValueError("Kraken veri döndürmedi")
    df = pd.DataFrame(
        result[data_key],
        columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna().reset_index(drop=True)

def indicators(df):
    d = df.copy()
    d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    delta = d["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-12)
    d["rsi"] = 100 - (100 / (1 + rs))
    ema12 = d["close"].ewm(span=12, adjust=False).mean()
    ema26 = d["close"].ewm(span=26, adjust=False).mean()
    d["macd"] = ema12 - ema26
    d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean()
    return d

def analyze_coin(name, pair, interval):
    try:
        df = indicators(get_ohlc(pair, interval))
        if len(df) < 55:
            raise ValueError("Yeterli mum verisi yok")
        last, prev = df.iloc[-1], df.iloc[-2]
        score, reasons = 0, []

        if last["close"] > last["ema20"]:
            score += 1
            reasons.append("Fiyat EMA20 üzerinde")
        else:
            score -= 1
            reasons.append("Fiyat EMA20 altında")

        if last["ema20"] > last["ema50"]:
            score += 1
            reasons.append("EMA20 > EMA50")
        else:
            score -= 1
            reasons.append("EMA20 < EMA50")

        if last["macd"] > last["macd_signal"]:
            score += 1
            reasons.append("MACD pozitif")
        else:
            score -= 1
            reasons.append("MACD negatif")

        if last["rsi"] < 35:
            score += 1
            reasons.append("RSI aşırı satım bölgesinde")
        elif last["rsi"] > 70:
            score -= 1
            reasons.append("RSI aşırı alım bölgesinde")

        signal = "AL" if score >= 2 else "SAT" if score <= -2 else "BEKLE"
        change = ((last["close"] / prev["close"]) - 1) * 100

        return {
            "Coin": name,
            "Parite": pair,
            "Fiyat (USD)": float(last["close"]),
            # Bu ad, aşağıdaki tablo seçimindeki sütun adıyla birebir aynı olmalı.
            "Son mum %": float(change),
            "RSI": float(last["rsi"]),
            "Skor": score,
            "Sinyal": signal,
            "Açıklama": " • ".join(reasons),
        }
    except Exception as exc:
        return {"Coin": name, "Parite": pair, "Hata": str(exc)}

st.title("📈 AI TRADER PRO")
st.caption("Çoklu kripto tarayıcı • Kraken spot verileri • Teknik analiz sinyalleri")
st.warning(
    "Sinyaller teknik göstergelere dayalı otomatik analizdir; yatırım tavsiyesi değildir. "
    "Bu uygulama gerçek alım-satım emri göndermez."
)

with st.sidebar:
    st.header("Tarama ayarları")
    interval_label = st.selectbox(
        "Zaman aralığı",
        ["5 dakika", "15 dakika", "1 saat", "4 saat", "1 gün"],
        index=1,
    )
    interval_map = {
        "5 dakika": 5,
        "15 dakika": 15,
        "1 saat": 60,
        "4 saat": 240,
        "1 gün": 1440,
    }
    interval = interval_map[interval_label]
    selected_names = st.multiselect(
        "Taranacak coinler", list(COINS.keys()), default=list(COINS.keys())
    )
    if st.button("🔄 Verileri yenile", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Veriler 60 saniyede bir önbellekten yenilenir.")

if not selected_names:
    st.info("Tarama için en az bir coin seç.")
    st.stop()

rows = []
progress = st.progress(0, text="Coinler taranıyor...")
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {
        executor.submit(analyze_coin, name, COINS[name], interval): name
        for name in selected_names
    }
    for completed, future in enumerate(as_completed(futures), start=1):
        rows.append(future.result())
        progress.progress(completed / len(futures), text=f"Tarama: {completed}/{len(futures)}")
progress.empty()

valid = [r for r in rows if "Hata" not in r]
errors = [r for r in rows if "Hata" in r]

if not valid:
    st.error("Coin verileri alınamadı. Kraken bağlantısını veya seçilen pariteleri kontrol et.")
    for item in errors:
        st.write(f"**{item['Coin']}**: {item['Hata']}")
    st.stop()

df = pd.DataFrame(valid).sort_values(
    ["Skor", "Coin"], ascending=[False, True]
).reset_index(drop=True)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Taranan coin", len(valid))
c2.metric("AL sinyali", int((df["Sinyal"] == "AL").sum()))
c3.metric("SAT sinyali", int((df["Sinyal"] == "SAT").sum()))
c4.metric("BEKLE", int((df["Sinyal"] == "BEKLE").sum()))

st.subheader("🔎 Piyasa tarama sonuçları")
signal_filter = st.multiselect(
    "Sinyal filtresi", ["AL", "BEKLE", "SAT"], default=["AL", "BEKLE", "SAT"]
)
view = df[df["Sinyal"].isin(signal_filter)].copy()

# Hata veren eski sürümdeki KeyError burada oluşuyordu.
display = view[
    ["Coin", "Parite", "Fiyat (USD)", "Son mum %", "RSI", "Skor", "Sinyal"]
].copy()
display["Fiyat (USD)"] = display["Fiyat (USD)"].map(
    lambda x: f"${x:,.6f}".rstrip("0").rstrip(".")
)
display["Son mum %"] = display["Son mum %"].map(lambda x: f"{x:+.2f}%")
display["RSI"] = display["RSI"].map(lambda x: f"{x:.1f}")
st.dataframe(display, use_container_width=True, hide_index=True)

st.subheader("🎯 Coin detayları")
coin_choice = st.selectbox("Detayını görmek istediğin coin", [r["Coin"] for r in valid])
detail = next(r for r in valid if r["Coin"] == coin_choice)
st.write(
    f"**Sinyal: {detail['Sinyal']}** | **Skor: {detail['Skor']}/4** "
    f"| **RSI: {detail['RSI']:.1f}**"
)
st.write(detail["Açıklama"])
st.metric("Son fiyat", f"${detail['Fiyat (USD)']:,.6f}".rstrip("0").rstrip("."))

st.subheader("🧪 Sanal portföy")
if "cash" not in st.session_state:
    st.session_state.cash = 10000.0
if "positions" not in st.session_state:
    st.session_state.positions = {}
if "history" not in st.session_state:
    st.session_state.history = []

prices = {r["Coin"]: r["Fiyat (USD)"] for r in valid}
trade_coin = st.selectbox("İşlem yapılacak coin", list(prices.keys()), key="trade_coin")
amount = st.number_input(
    "İşlem tutarı (USD)", min_value=10.0, max_value=100000.0, value=100.0, step=10.0
)
buy_col, sell_col = st.columns(2)

with buy_col:
    if st.button("🟢 Sanal AL", use_container_width=True):
        price = prices[trade_coin]
        if amount <= st.session_state.cash:
            units = amount / price
            st.session_state.positions[trade_coin] = (
                st.session_state.positions.get(trade_coin, 0.0) + units
            )
            st.session_state.cash -= amount
            st.session_state.history.append(
                {"İşlem": "AL", "Coin": trade_coin, "Miktar": units,
                 "Fiyat": price, "Tutar": amount}
            )
            st.success("Sanal alım yapıldı.")
        else:
            st.error("Sanal nakit bakiyesi yetersiz.")

with sell_col:
    if st.button("🔴 Sanal SAT", use_container_width=True):
        price = prices[trade_coin]
        units_to_sell = amount / price
        owned = st.session_state.positions.get(trade_coin, 0.0)
        if units_to_sell <= owned:
            st.session_state.positions[trade_coin] = owned - units_to_sell
            st.session_state.cash += amount
            st.session_state.history.append(
                {"İşlem": "SAT", "Coin": trade_coin, "Miktar": units_to_sell,
                 "Fiyat": price, "Tutar": amount}
            )
            st.success("Sanal satış yapıldı.")
        else:
            st.error("Bu tutarda satmak için yeterli coin yok.")

portfolio_value = sum(
    st.session_state.positions.get(name, 0.0) * price
    for name, price in prices.items()
)
p1, p2, p3 = st.columns(3)
p1.metric("Sanal nakit", f"${st.session_state.cash:,.2f}")
p2.metric("Açık pozisyon değeri", f"${portfolio_value:,.2f}")
p3.metric("Toplam portföy", f"${st.session_state.cash + portfolio_value:,.2f}")

positions_rows = [
    {"Coin": name, "Miktar": units, "Anlık değer (USD)": units * prices[name]}
    for name, units in st.session_state.positions.items()
    if units > 0 and name in prices
]
if positions_rows:
    st.dataframe(pd.DataFrame(positions_rows), use_container_width=True, hide_index=True)
else:
    st.caption("Henüz açık sanal pozisyon yok.")

if st.session_state.history:
    with st.expander("Sanal işlem geçmişi"):
        st.dataframe(
            pd.DataFrame(st.session_state.history),
            use_container_width=True,
            hide_index=True,
        )

if errors:
    with st.expander(f"Verisi alınamayan coinler ({len(errors)})"):
        for item in errors:
            st.write(f"**{item['Coin']}** — {item['Hata']}")

st.caption(
    "Not: Son mum değişimi gösterilir; 24 saatlik değişim değildir. "
    "Sanal portföy uygulama oturumu sıfırlanınca silinebilir."
)
