import streamlit as st
import pandas as pd
import requests
import time

st.set_page_config(
    page_title="AI TRADER PRO",
    page_icon="🚀",
    layout="wide"
)

st.title("🚀 AI TRADER PRO")
st.subheader("Yapay Zekâ Destekli Kripto Analiz Sistemi")

if "balance" not in st.session_state:
    st.session_state.balance = 10000.0

if "trades" not in st.session_state:
    st.session_state.trades = []

st.metric("Sanal Bakiye", f"{st.session_state.balance:.2f} USDT")

coin = st.selectbox(
    "Coin Seç",
    ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
)

def get_data(symbol):
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": "15m",
        "limit": 100
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()

    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close",
        "volume", "close_time", "quote_volume",
        "trades", "taker_buy", "taker_quote", "ignore"
    ])

    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)

    return df

if st.button("Piyasayı Analiz Et"):
    try:
        df = get_data(coin)

        df["EMA20"] = df["close"].ewm(span=20).mean()
        df["EMA50"] = df["close"].ewm(span=50).mean()

        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()

        rs = avg_gain / avg_loss.replace(0, float("nan"))
        df["RSI"] = 100 - (100 / (1 + rs))

        last = df.iloc[-2]

        st.metric("Son Fiyat", f"{last['close']:.4f} USDT")
        st.metric("RSI", f"{last['RSI']:.2f}")

        if last["EMA20"] > last["EMA50"] and last["RSI"] < 70:
            st.success("ALIM ADAYI")
        elif last["EMA20"] < last["EMA50"] and last["RSI"] > 30:
            st.error("SATIŞ ADAYI")
        else:
            st.warning("BEKLE")

        st.line_chart(df.set_index("time")["close"])

    except Exception as e:
        st.error(f"Veri alınamadı: {e}")

st.subheader("📊 İşlem Geçmişi")
st.write(st.session_state.trades)

st.caption("İlk sürüm: Analiz ve sinyal üretimi. Gerçek emir gönderilmez.")
