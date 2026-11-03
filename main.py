import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="AI TRADER PRO", page_icon="🚀", layout="wide")

START_BALANCE = 10000.0
COINS = {
    "Bitcoin (BTC)": "XBTUSD",
    "Ethereum (ETH)": "ETHUSD",
    "Solana (SOL)": "SOLUSD",
    "Ripple (XRP)": "XRPUSD",
}
for key, default in {
    "balance": START_BALANCE, "trades": [], "positions": {},
    "market_data": {}, "last_update": None, "last_refresh_count": None
}.items():
    if key not in st.session_state:
        st.session_state[key] = default.copy() if isinstance(default, (dict, list)) else default


@st.cache_data(ttl=20, show_spinner=False)
def get_ohlc(pair):
    r = requests.get(
        "https://api.kraken.com/0/public/OHLC",
        params={"pair": pair, "interval": 15},
        headers={"User-Agent": "AI-TRADER-PRO"}, timeout=20
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(", ".join(data["error"]))
    result = data.get("result", {})
    pair_key = next((k for k in result if k != "last"), None)
    if not pair_key:
        raise ValueError("Mum verisi bulunamadı.")
    df = pd.DataFrame(result[pair_key], columns=[
        "time", "open", "high", "low", "close", "vwap", "volume", "count"
    ])
    df["time"] = pd.to_datetime(df["time"].astype(float), unit="s", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    if len(df) < 55:
        raise ValueError("Teknik analiz için yeterli mum verisi yok.")

    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    delta = df["close"].diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI"] = 100 - (100 / (1 + rs))
    df.loc[(avg_loss == 0) & (avg_gain > 0), "RSI"] = 100
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    return df


@st.cache_data(ttl=10, show_spinner=False)
def get_live_price(pair):
    r = requests.get(
        "https://api.kraken.com/0/public/Ticker",
        params={"pair": pair},
        headers={"User-Agent": "AI-TRADER-PRO"}, timeout=15
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(", ".join(data["error"]))
    result = data.get("result", {})
    if not result:
        raise ValueError("Güncel fiyat bulunamadı.")
    return float(next(iter(result.values()))["c"][0])


def get_signal(row):
    if pd.isna(row["RSI"]) or pd.isna(row["MACD_Signal"]):
        return "VERİ YETERSİZ"
    if row["EMA20"] > row["EMA50"] and row["MACD"] > row["MACD_Signal"] and row["RSI"] < 70:
        return "ALIM ADAYI"
    if row["EMA20"] < row["EMA50"] and row["MACD"] < row["MACD_Signal"] and row["RSI"] > 30:
        return "SATIŞ ADAYI"
    return "BEKLE"


def close_position(coin, price, reason):
    pos = st.session_state.positions.get(coin)
    if not pos:
        return
    proceeds = pos["quantity"] * price
    pnl = proceeds - pos["cost"]
    st.session_state.balance += proceeds
    st.session_state.trades.append({
        "Zaman": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "Coin": coin, "İşlem": "SANAL SATIŞ",
        "Fiyat (USD)": round(price, 8), "Miktar": round(pos["quantity"], 8),
        "Tutar (USDT)": round(proceeds, 2), "Kâr/Zarar (USDT)": round(pnl, 2),
        "Açıklama": reason
    })
    del st.session_state.positions[coin]


def refresh_markets():
    errors = []
    for name, pair in COINS.items():
        try:
            df = get_ohlc(pair)
            completed = df.iloc[-2]  # tamamlanmış son 15 dakikalık mum
            price = get_live_price(pair)  # güncel işlem fiyatı
            st.session_state.market_data[name] = {
                "price": price, "signal": get_signal(completed),
                "rsi": float(completed["RSI"]) if pd.notna(completed["RSI"]) else None,
                "macd": float(completed["MACD"]),
                "macd_signal": float(completed["MACD_Signal"]),
                "candle_time": str(completed["time"])
            }
            st.session_state["df_" + name] = df
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    # Risk kontrolleri yalnızca uygulama çalışırken, veri yenilenince yapılır.
    for name, pos in list(st.session_state.positions.items()):
        market = st.session_state.market_data.get(name)
        if not market:
            continue
        price = market["price"]
        if price <= pos["stop_loss"]:
            close_position(name, price, "Otomatik stop-loss")
        elif price >= pos["take_profit"]:
            close_position(name, price, "Otomatik take-profit")
    st.session_state.last_update = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return errors


st.title("🚀 AI TRADER PRO")
st.subheader("Çoklu Coin Takibi • Teknik Analiz • Sanal İşlem")
st.info("Demo modudur. Gerçek emir gönderilmez. Sinyaller kesin tahmin değildir.")

with st.sidebar:
    st.header("⚙️ Takip Ayarları")
    auto = st.toggle("Otomatik piyasa takibi", value=True)
    seconds = st.selectbox("Yenileme aralığı", [30, 60, 120, 300], index=1,
                           format_func=lambda x: f"{x} saniye")

count = st_autorefresh(interval=seconds * 1000, key="multi_coin_refresh") if auto else None
manual = st.button("🔄 Tüm Coinleri Şimdi Güncelle", use_container_width=True)
auto_due = auto and count != st.session_state.last_refresh_count
if manual or auto_due or not st.session_state.market_data:
    if auto_due:
        st.session_state.last_refresh_count = count
    with st.spinner("BTC, ETH, SOL ve XRP verileri alınıyor..."):
        errors = refresh_markets()
    if errors:
        st.warning("Bazı coinlerin verisi alınamadı:\n\n" + "\n".join(errors))

# Portföy özeti
market_value = 0.0
unrealized = 0.0
for name, pos in st.session_state.positions.items():
    current = st.session_state.market_data.get(name, {}).get("price", pos["entry_price"])
    market_value += pos["quantity"] * current
    unrealized += (current - pos["entry_price"]) * pos["quantity"]
realized = sum(t["Kâr/Zarar (USDT)"] for t in st.session_state.trades if t["İşlem"] == "SANAL SATIŞ")
equity = st.session_state.balance + market_value

a, b, c, d = st.columns(4)
a.metric("Kullanılabilir Bakiye", f'{st.session_state.balance:,.2f} USDT')
b.metric("Toplam Portföy", f"{equity:,.2f} USDT")
c.metric("Açık Pozisyon", str(len(st.session_state.positions)))
d.metric("Açık Pozisyon K/Z", f"{unrealized:+,.2f} USDT")
st.caption(f"Gerçekleşmiş K/Z: {realized:+,.2f} USDT | Son güncelleme: {st.session_state.last_update or 'Henüz yok'}")

st.divider()
st.header("📡 Çoklu Coin Piyasası")
table = []
for name in COINS:
    item = st.session_state.market_data.get(name)
    if item:
        table.append({
            "Coin": name, "Güncel fiyat (USD)": round(item["price"], 4),
            "Sinyal": item["signal"],
            "RSI": round(item["rsi"], 2) if item["rsi"] is not None else "N/A",
            "MACD": round(item["macd"], 4),
            "Son tamamlanmış mum": item["candle_time"]
        })
if table:
    st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)
else:
    st.warning("Piyasa verisi henüz yüklenmedi.")

st.divider()
st.header("🛒 Sanal İşlem")
coin = st.selectbox("İşlem yapılacak coin", list(COINS.keys()))
market = st.session_state.market_data.get(coin)
stop_pct = st.number_input("Stop-loss (%)", min_value=0.5, max_value=50.0, value=3.0, step=0.5)
take_pct = st.number_input("Take-profit (%)", min_value=0.5, max_value=100.0, value=6.0, step=0.5)
amount = st.number_input("Sanal alım tutarı (USDT)", min_value=10.0, max_value=10000.0, value=100.0, step=10.0)

if market:
    price = market["price"]
    st.metric(f"{coin} güncel fiyatı", f"{price:,.4f} USD")
    pos = st.session_state.positions.get(coin)
    if pos:
        value = pos["quantity"] * price
        pnl = value - pos["cost"]
        x, y, z = st.columns(3)
        x.metric("Giriş fiyatı", f'{pos["entry_price"]:,.4f} USD')
        y.metric("Pozisyon değeri", f"{value:,.2f} USDT")
        z.metric("Pozisyon K/Z", f"{pnl:+,.2f} USDT")
        st.caption(f'Stop-loss: {pos["stop_loss"]:,.4f} USD | Take-profit: {pos["take_profit"]:,.4f} USD')
        if st.button("🔴 Pozisyonu Sanal Kapat", use_container_width=True):
            close_position(coin, price, "Manuel sanal satış")
            st.success("Pozisyon kapatıldı.")
            st.rerun()
    elif st.button("🟢 Sanal AL", use_container_width=True):
        if amount > st.session_state.balance:
            st.error("Yetersiz sanal bakiye.")
        elif price <= 0:
            st.error("Geçersiz fiyat.")
        else:
            qty = amount / price
            st.session_state.balance -= amount
            st.session_state.positions[coin] = {
                "entry_price": price, "quantity": qty, "cost": amount,
                "stop_loss": price * (1 - stop_pct / 100),
                "take_profit": price * (1 + take_pct / 100)
            }
            st.session_state.trades.append({
                "Zaman": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "Coin": coin, "İşlem": "SANAL ALIM", "Fiyat (USD)": round(price, 8),
                "Miktar": round(qty, 8), "Tutar (USDT)": round(amount, 2),
                "Kâr/Zarar (USDT)": 0.0, "Açıklama": "Sanal alım"
            })
            st.success(f"Sanal alım tamamlandı: {qty:.8f} adet.")
            st.rerun()
else:
    st.info("İşlem için önce piyasa verilerini güncelle.")

st.divider()
st.header("📂 Açık Pozisyonlar")
if st.session_state.positions:
    positions_table = []
    for name, pos in st.session_state.positions.items():
        current = st.session_state.market_data.get(name, {}).get("price", pos["entry_price"])
        value = pos["quantity"] * current
        positions_table.append({
            "Coin": name, "Giriş": round(pos["entry_price"], 6),
            "Güncel": round(current, 6), "Miktar": round(pos["quantity"], 8),
            "Pozisyon değeri": round(value, 2),
            "Stop-loss": round(pos["stop_loss"], 6),
            "Take-profit": round(pos["take_profit"], 6),
            "K/Z (USDT)": round(value - pos["cost"], 2)
        })
    st.dataframe(pd.DataFrame(positions_table), use_container_width=True, hide_index=True)
else:
    st.write("Açık sanal pozisyon bulunmuyor.")

st.divider()
st.header("📜 İşlem Geçmişi")
if st.session_state.trades:
    st.dataframe(pd.DataFrame(st.session_state.trades[::-1]), use_container_width=True, hide_index=True)
    if st.button("🗑️ İşlem geçmişini temizle"):
        st.session_state.trades = []
        st.rerun()
else:
    st.write("Henüz sanal işlem yapılmadı.")

st.caption(
    "AI TRADER PRO | Eğitim ve simülasyon amaçlıdır; yatırım tavsiyesi değildir. "
    "Stop-loss/take-profit uygulama çalışırken ve veri yenilendiğinde kontrol edilir. "
    "Render oturumu yeniden başlatılırsa sanal bakiye ve işlem geçmişi sıfırlanabilir."
)
