import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone

st.set_page_config(
    page_title="AI TRADER PRO",
    page_icon="🚀",
    layout="wide"
)

# -------------------------------
# AYARLAR
# -------------------------------

START_BALANCE = 10000.0

COINS = {
    "Bitcoin (BTC)": "XBTUSD",
    "Ethereum (ETH)": "ETHUSD",
    "Solana (SOL)": "SOLUSD",
    "Ripple (XRP)": "XRPUSD",
}

# -------------------------------
# OTURUM VERİLERİ
# -------------------------------

if "balance" not in st.session_state:
    st.session_state.balance = START_BALANCE

if "trades" not in st.session_state:
    st.session_state.trades = []

if "positions" not in st.session_state:
    st.session_state.positions = {}

if "market_data" not in st.session_state:
    st.session_state.market_data = {}

if "last_update" not in st.session_state:
    st.session_state.last_update = None


# -------------------------------
# PİYASA VERİSİ
# -------------------------------

@st.cache_data(ttl=30)
def get_data(pair):
    url = "https://api.kraken.com/0/public/OHLC"

    response = requests.get(
        url,
        params={"pair": pair, "interval": 15},
        timeout=20,
        headers={"User-Agent": "AI-TRADER-PRO"}
    )

    response.raise_for_status()
    result = response.json()

    if result.get("error"):
        raise ValueError(", ".join(result["error"]))

    candles = result.get("result", {})
    pair_key = next(
        (key for key in candles if key != "last"),
        None
    )

    if not pair_key:
        raise ValueError("Piyasa verisi bulunamadı.")

    rows = candles[pair_key]

    df = pd.DataFrame(
        rows,
        columns=[
            "time", "open", "high", "low", "close",
            "vwap", "volume", "count"
        ]
    )

    df["time"] = pd.to_datetime(
        df["time"].astype(float),
        unit="s",
        utc=True
    )

    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = df[column].astype(float)

    # Son mum henüz tamamlanmamış olabilir.
    # Analizde son tamamlanmış mumu kullanacağız.
    if len(df) < 55:
        raise ValueError("Analiz için yeterli mum verisi yok.")

    df["EMA20"] = df["close"].ewm(
        span=20, adjust=False
    ).mean()

    df["EMA50"] = df["close"].ewm(
        span=50, adjust=False
    ).mean()

    # RSI 14
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14, min_periods=14, adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14, min_periods=14, adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df["close"].ewm(
        span=12, adjust=False
    ).mean()

    ema26 = df["close"].ewm(
        span=26, adjust=False
    ).mean()

    df["MACD"] = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(
        span=9, adjust=False
    ).mean()

    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

    return df


# -------------------------------
# SİNYAL HESAPLAMA
# -------------------------------

def get_signal(row):
    if pd.isna(row["RSI"]):
        return "VERİ YETERSİZ"

    bullish = (
        row["EMA20"] > row["EMA50"]
        and row["MACD"] > row["MACD_Signal"]
        and row["RSI"] < 70
    )

    bearish = (
        row["EMA20"] < row["EMA50"]
        and row["MACD"] < row["MACD_Signal"]
        and row["RSI"] > 30
    )

    if bullish:
        return "ALIM ADAYI"

    if bearish:
        return "SATIŞ ADAYI"

    return "BEKLE"


# -------------------------------
# SANAL POZİSYONLARI KONTROL ET
# -------------------------------

def check_risk_limits(prices):
    for coin_name, position in list(
        st.session_state.positions.items()
    ):
        if coin_name not in prices:
            continue

        price = prices[coin_name]

        if price <= position["stop_loss"]:
            close_position(
                coin_name, price, "Stop-loss"
            )

        elif price >= position["take_profit"]:
            close_position(
                coin_name, price, "Take-profit"
            )


def close_position(coin_name, price, reason):
    position = st.session_state.positions.get(coin_name)

    if not position:
        return

    quantity = position["quantity"]
    entry = position["entry_price"]
    proceeds = quantity * price
    cost = quantity * entry
    pnl = proceeds - cost

    st.session_state.balance += proceeds

    st.session_state.trades.append({
        "Zaman": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "Coin": coin_name,
        "İşlem": "SANAL SATIŞ",
        "Fiyat": round(price, 8),
        "Miktar": round(quantity, 8),
        "Kâr/Zarar (USDT)": round(pnl, 2),
        "Açıklama": reason
    })

    del st.session_state.positions[coin_name]


# -------------------------------
# BAŞLIK
# -------------------------------

st.title("🚀 AI TRADER PRO")
st.subheader("Yapay Zekâ Destekli Kripto Analiz Sistemi")

st.info(
    "Demo modudur. Gerçek emir gönderilmez. "
    "Sinyaller kesin tahmin değildir."
)

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "💰 Kullanılabilir Bakiye",
        f"{st.session_state.balance:,.2f} USDT"
    )

with col2:
    st.metric(
        "📂 Açık Pozisyon",
        str(len(st.session_state.positions))
    )

with col3:
    total_realized = sum(
        trade["Kâr/Zarar (USDT)"]
        for trade in st.session_state.trades
        if trade["İşlem"] == "SANAL SATIŞ"
    )

    st.metric(
        "📊 Gerçekleşmiş Kâr/Zarar",
        f"{total_realized:,.2f} USDT"
    )

st.divider()


# -------------------------------
# ANALİZ EKRANI
# -------------------------------

st.header("📈 Piyasa Analizi")

coin_name = st.selectbox(
    "Coin seç",
    list(COINS.keys())
)

stop_percent = st.number_input(
    "Stop-loss (%)",
    min_value=0.5,
    max_value=50.0,
    value=3.0,
    step=0.5
)

take_percent = st.number_input(
    "Take-profit (%)",
    min_value=0.5,
    max_value=100.0,
    value=6.0,
    step=0.5
)

if st.button("🔄 Piyasayı Analiz Et", use_container_width=True):
    try:
        pair = COINS[coin_name]
        df = get_data.clear.__self__ if False else get_data(pair)

        # Kraken son satırda açık mum döndürebilir.
        # Son tamamlanmış mumu analiz et.
        row = df.iloc[-2]

        st.session_state.market_data[coin_name] = {
            "price": float(row["close"]),
            "signal": get_signal(row),
            "time": str(row["time"])
        }

        st.session_state.last_update = datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Açık pozisyon için risk kontrolü
        prices = {
            name: data["price"]
            for name, data in st.session_state.market_data.items()
        }
        check_risk_limits(prices)

        st.session_state["df_" + coin_name] = df

    except Exception as e:
        st.error(
            "Piyasa verisi alınamadı. Biraz sonra tekrar dene. "
            f"Teknik detay: {e}"
        )

market = st.session_state.market_data.get(coin_name)

if market:
    price = market["price"]

    st.subheader(f"{coin_name} Analiz Sonucu")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.metric("Son Tamamlanmış Mum Fiyatı", f"{price:,.4f} USD")

    with c2:
        st.metric("Sinyal", market["signal"])

    with c3:
        st.caption(f"Mum zamanı: {market['time']}")

    if market["signal"] == "ALIM ADAYI":
        st.success("🟢 Teknik göstergeler alım yönünü destekliyor.")
    elif market["signal"] == "SATIŞ ADAYI":
        st.error("🔴 Teknik göstergeler satış yönünü destekliyor.")
    else:
        st.warning("🟡 Net bir yön sinyali yok: BEKLE.")

    df = st.session_state.get("df_" + coin_name)

    if df is not None:
        st.subheader("📉 Fiyat Grafiği")
        chart_df = df.set_index("time")[
            ["close", "EMA20", "EMA50"]
        ].rename(columns={
            "close": "Fiyat",
            "EMA20": "EMA20",
            "EMA50": "EMA50"
        })

        st.line_chart(chart_df)

        st.subheader("Teknik Göstergeler")
        last = df.iloc[-2]

        a, b, c = st.columns(3)

        a.metric(
            "RSI (14)",
            f"{last['RSI']:.2f}"
            if pd.notna(last["RSI"]) else "N/A"
        )

        b.metric("MACD", f"{last['MACD']:.4f}")
        c.metric(
            "MACD Sinyal",
            f"{last['MACD_Signal']:.4f}"
        )

    st.divider()

    # ---------------------------
    # SANAL ALIM
    # ---------------------------

    st.subheader("🛒 Sanal İşlem")

    amount = st.number_input(
        "Alım tutarı (USDT)",
        min_value=10.0,
        max_value=10000.0,
        value=100.0,
        step=10.0
    )

    if st.button("🟢 Sanal AL", use_container_width=True):
        if coin_name in st.session_state.positions:
            st.warning("Bu coin için zaten açık pozisyonun var.")
        elif amount > st.session_state.balance:
            st.error("Yetersiz sanal bakiye.")
        elif price <= 0:
            st.error("Fiyat geçersiz.")
        else:
            quantity = amount / price

            st.session_state.balance -= amount

            st.session_state.positions[coin_name] = {
                "entry_price": price,
                "quantity": quantity,
                "cost": amount,
                "stop_loss": price * (1 - stop_percent / 100),
                "take_profit": price * (1 + take_percent / 100)
            }

            st.session_state.trades.append({
                "Zaman": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                ),
                "Coin": coin_name,
                "İşlem": "SANAL ALIM",
                "Fiyat": round(price, 8),
                "Miktar": round(quantity, 8),
                "Kâr/Zarar (USDT)": 0.0,
                "Açıklama": "Sanal alım"
            })

            st.success(
                f"Sanal alım yapıldı: {quantity:.8f} adet."
            )
            st.rerun()

    # ---------------------------
    # SANAL SATIŞ
    # ---------------------------

    position = st.session_state.positions.get(coin_name)

    if position:
        st.subheader("🛡️ Açık Pozisyon")

        unrealized = (
            price - position["entry_price"]
        ) * position["quantity"]

        p1, p2, p3 = st.columns(3)

        p1.metric(
            "Giriş Fiyatı",
            f"{position['entry_price']:,.4f} USD"
        )

        p2.metric(
            "Stop-loss",
            f"{position['stop_loss']:,.4f} USD"
        )

        p3.metric(
            "Take-profit",
            f"{position['take_profit']:,.4f} USD"
        )

        st.metric(
            "Gerçekleşmemiş Kâr/Zarar",
            f"{unrealized:+,.2f} USDT"
        )

        if st.button("🔴 Pozisyonu Sanal Kapat"):
            close_position(coin_name, price, "Manuel satış")
            st.success("Sanal pozisyon kapatıldı.")
            st.rerun()

else:
    st.warning(
        "Henüz analiz yapılmadı. "
        "Piyasa verilerini yüklemek için analiz butonuna bas."
    )

st.caption(
    f"Son güncelleme: {st.session_state.last_update or 'Henüz yok'}"
)

st.divider()


# -------------------------------
# AÇIK POZİSYONLAR
# -------------------------------

st.header("📂 Açık Pozisyonlar")

if st.session_state.positions:
    position_rows = []

    for name, pos in st.session_state.positions.items():
        current = st.session_state.market_data.get(name, {}).get(
            "price", pos["entry_price"]
        )

        position_rows.append({
            "Coin": name,
            "Giriş Fiyatı": round(pos["entry_price"], 6),
            "Güncel Fiyat": round(current, 6),
            "Miktar": round(pos["quantity"], 8),
            "Stop-loss": round(pos["stop_loss"], 6),
            "Take-profit": round(pos["take_profit"], 6),
            "Kâr/Zarar": round(
                (current - pos["entry_price"]) * pos["quantity"], 2
            )
        })

    st.dataframe(
        pd.DataFrame(position_rows),
        use_container_width=True,
        hide_index=True
    )
else:
    st.write("Açık sanal pozisyon bulunmuyor.")


# -------------------------------
# İŞLEM GEÇMİŞİ
# -------------------------------

st.header("📜 İşlem Geçmişi")

if st.session_state.trades:
    st.dataframe(
        pd.DataFrame(st.session_state.trades[::-1]),
        use_container_width=True,
        hide_index=True
    )

    if st.button("🗑️ İşlem Geçmişini Temizle"):
        st.session_state.trades = []
        st.rerun()
else:
    st.write("Henüz sanal işlem yapılmadı.")

st.caption(
    "AI TRADER PRO | Eğitim ve simülasyon amaçlıdır. "
    "Kripto varlıklar yüksek risk içerir. "
    "Stop-loss ve take-profit kontrolleri yalnızca uygulama "
    "çalışırken ve fiyat güncellendiğinde yapılır."
)
