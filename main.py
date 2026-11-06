import os, time, hmac, hashlib
from urllib.parse import urlencode
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="AI TRADER PRO", page_icon="📈", layout="wide")
BASE_URL = os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com").rstrip("/")
API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

st.title("📈 AI TRADER PRO")
st.warning("GÜVENLİ DEMO MODU: Bu uygulama emir göndermez, pozisyon açmaz, transfer veya para çekme yapmaz.")

if "blocked_until" not in st.session_state:
    st.session_state.blocked_until = 0.0
if "last_private_call" not in st.session_state:
    st.session_state.last_private_call = 0.0

def api_get(path, params=None, signed=False):
    if time.time() < st.session_state.blocked_until:
        left = int(st.session_state.blocked_until - time.time())
        raise RuntimeError(f"Binance koruması etkin. Yaklaşık {left} saniye bekleyin; tekrar denemeyin.")
    p = dict(params or {})
    headers = {}
    if signed:
        if not API_KEY or not API_SECRET:
            raise RuntimeError("Render ortam değişkenlerinde API Key/Secret eksik.")
        if time.time() - st.session_state.last_private_call < 10:
            raise RuntimeError("Hesap API çağrıları arasında en az 10 saniye bekleyin.")
        p.update(timestamp=int(time.time() * 1000), recvWindow=10000)
        q = urlencode(p)
        sig = hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
        headers["X-MBX-APIKEY"] = API_KEY
        url = f"{BASE_URL}{path}?{q}&signature={sig}"
        st.session_state.last_private_call = time.time()
        r = requests.get(url, headers=headers, timeout=15)
    else:
        r = requests.get(f"{BASE_URL}{path}", params=p, timeout=15)
    if r.status_code in (418, 429):
        retry = r.headers.get("Retry-After", "60")
        try:
            wait = max(60, int(float(retry)))
        except ValueError:
            wait = 60
        st.session_state.blocked_until = time.time() + wait
        raise RuntimeError(f"Binance HTTP {r.status_code}: IP/istek limiti. Yeni istekler {wait} saniye durduruldu.")
    try:
        data = r.json()
    except ValueError:
        data = {"msg": r.text[:200]}
    if not r.ok:
        raise RuntimeError(f"Binance HTTP {r.status_code} | {data.get('code', '?')} | {data.get('msg', 'Hata')}")
    return data

@st.cache_data(ttl=60, show_spinner=False)
def load_klines(symbol, interval, limit):
    data = api_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    cols = ["open_time","open","high","low","close","volume","close_time","qv","trades","tb","tq","ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50)
    return df

with st.sidebar:
    st.header("Analiz ayarları")
    symbol = st.selectbox("Coin", ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT"])
    interval = st.selectbox("Zaman dilimi", ["5m","15m","1h","4h","1d"], index=2)
    limit = st.selectbox("Mum sayısı", [100,200,300], index=1)
    st.caption("Arka planda sürekli tarama yapılmaz. Veri yalnızca düğmeye basınca alınır.")

st.write("**API durumu:**", "Tanımlı" if API_KEY and API_SECRET else "API bilgileri eksik")
st.write("**Gerçek emirler:** KAPALI")

left, right = st.columns([2,1])
with left:
    st.subheader(f"{symbol} — {interval}")
    if st.button("Piyasa verisini getir / yenile", type="primary", use_container_width=True):
        try:
            with st.spinner("Piyasa verisi alınıyor..."):
                load_klines.clear()
                st.session_state.market_df = load_klines(symbol, interval, limit)
                st.session_state.market_key = (symbol, interval)
        except Exception as e:
            st.error(str(e))
    if st.session_state.get("market_key") == (symbol, interval):
        df = st.session_state.market_df
        row = df.iloc[-1]
        if row["ema9"] > row["ema21"] and row["rsi"] >= 52:
            st.success("Analiz sinyali: YUKARI EĞİLİM — pozitif momentum")
        elif row["ema9"] < row["ema21"] and row["rsi"] <= 48:
            st.error("Analiz sinyali: AŞAĞI EĞİLİM — negatif momentum")
        else:
            st.info("Analiz sinyali: NÖTR / BEKLE — net yön teyidi yok")
        a,b,c = st.columns(3)
        a.metric("Son fiyat", f"{row['close']:,.4f} USDT")
        b.metric("RSI 14", f"{row['rsi']:.1f}")
        c.metric("EMA 9", f"{row['ema9']:,.4f}")
        st.line_chart(df.set_index("time")[["close","ema9","ema21"]], height=350)
        st.caption("Basit teknik göstergeler eğitim amaçlıdır; yatırım tavsiyesi veya kazanç garantisi değildir.")
    else:
        st.info("Analiz için piyasa verisini getir düğmesine bas.")

with right:
    st.subheader("Futures hesap özeti")
    st.caption("Bu istek yalnızca sen düğmeye bastığında gönderilir.")
    if st.button("Hesap bakiyesini oku", use_container_width=True):
        try:
            with st.spinner("Hesap okunuyor..."):
                acc = api_get("/fapi/v2/account", signed=True)
            st.metric("Wallet balance", f"{float(acc.get('totalWalletBalance',0)):.4f} USDT")
            st.metric("Available balance", f"{float(acc.get('availableBalance',0)):.4f} USDT")
            st.metric("Unrealized PnL", f"{float(acc.get('totalUnrealizedProfit',0)):.4f} USDT")
        except Exception as e:
            st.error(str(e))
    if time.time() < st.session_state.blocked_until:
        st.error("418/429 koruması etkin; yeni istek gönderilmiyor.")

st.divider()
st.subheader("Güvenlik")
st.success("Bu dosyada emir, pozisyon, kaldıraç, transfer veya para çekme kodu bulunmaz.")
st.write("- Piyasa verileri 60 saniye önbelleğe alınır.\n- Hesap çağrıları arasında en az 10 saniye beklenir.\n- 418/429 yanıtında yeni istekler durdurulur.\n- API Secret ekrana yazdırılmaz.")
