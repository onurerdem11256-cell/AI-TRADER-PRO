import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="AI TRADER PRO", page_icon="📈", layout="wide")
# Sekme açık/ön plandayken 60 saniyede bir yeniden çalıştır.
st_autorefresh(interval=60_000, key="refresh_60s")

BASE = "https://api.kraken.com/0/public"
COINS = {
    "Bitcoin":"XBTUSD", "Ethereum":"ETHUSD", "Solana":"SOLUSD", "XRP":"XRPUSD",
    "BNB":"BNBUSD", "Cardano":"ADAUSD", "Dogecoin":"XDGUSD",
    "Avalanche":"AVAXUSD", "Chainlink":"LINKUSD", "Polkadot":"DOTUSD",
    "Litecoin":"LTCUSD", "Bitcoin Cash":"BCHUSD", "Stellar":"XLMUSD",
    "Cosmos":"ATOMUSD", "Uniswap":"UNIUSD", "Ethereum Classic":"ETCUSD",
    "Aave":"AAVEUSD", "NEAR":"NEARUSD", "Sui":"SUIUSD", "TRON":"TRXUSD"
}

@st.cache_data(ttl=50, show_spinner=False)
def get_ohlc(pair, interval):
    r = requests.get(BASE + "/OHLC", params={"pair":pair, "interval":interval},
                     timeout=15, headers={"User-Agent":"AI-Trader-Pro/2.0"})
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
    for c in ["open","high","low","close","volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna().reset_index(drop=True)

def analyze(name, pair, interval):
    try:
        d = get_ohlc(pair, interval).copy()
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
        if len(d) < 55:
            raise ValueError("Yeterli mum verisi yok")
        last, prev = d.iloc[-1], d.iloc[-2]
        score, why = 0, []
        for condition, yes, no in [
            (last.close > last.ema20, "Fiyat EMA20 üzerinde", "Fiyat EMA20 altında"),
            (last.ema20 > last.ema50, "EMA20 > EMA50", "EMA20 < EMA50"),
            (last.macd > last.macd_signal, "MACD pozitif", "MACD negatif")
        ]:
            score += 1 if condition else -1
            why.append(yes if condition else no)
        if last.rsi < 35:
            score += 1
            why.append("RSI aşırı satım")
        elif last.rsi > 70:
            score -= 1
            why.append("RSI aşırı alım")
        signal = "AL" if score >= 2 else "SAT" if score <= -2 else "BEKLE"
        return {"Coin":name, "Parite":pair, "Fiyat (USD)":float(last.close),
                "Son mum %":float((last.close/prev.close-1)*100),
                "RSI":float(last.rsi), "Skor":score, "Sinyal":signal,
                "Açıklama":" • ".join(why)}
    except Exception as e:
        return {"Coin":name, "Parite":pair, "Hata":str(e)}

# Oturum portföyü
for k, v in {"cash":10000.0, "initial_capital":10000.0,
             "positions":{}, "history":[], "auto_trade":False}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# Önceki sürümdeki entry_price alanını koruyarak yeni formata dönüştür.
for old_pos in st.session_state.positions.values():
    if "entry" not in old_pos and "entry_price" in old_pos:
        old_pos["entry"] = old_pos["entry_price"]
    if "invested" not in old_pos and "units" in old_pos:
        old_pos["invested"] = old_pos["units"] * old_pos.get("entry", 0.0)

st.title("📈 AI TRADER PRO")
st.caption("Kraken piyasa tarayıcı • EMA / RSI / MACD • 60 saniyelik kontrol")
st.warning("Yalnızca sanal işlem yapar; gerçek borsaya emir göndermez. Yatırım tavsiyesi değildir.")

with st.sidebar:
    st.header("Tarama ayarları")
    label = st.selectbox("Zaman aralığı", ["5 dakika","15 dakika","1 saat","4 saat","1 gün"], index=1)
    interval = {"5 dakika":5, "15 dakika":15, "1 saat":60, "4 saat":240, "1 gün":1440}[label]
    selected = st.multiselect("Taranacak coinler", list(COINS), default=list(COINS))
    if st.button("🔄 Verileri şimdi yenile", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Sekme açıkken yaklaşık 60 saniyede bir yenilenir.")

if not selected:
    st.info("En az bir coin seçin.")
    st.stop()

rows = []
bar = st.progress(0, text="Coinler taranıyor...")
with ThreadPoolExecutor(max_workers=5) as pool:
    futures = [pool.submit(analyze, n, COINS[n], interval) for n in selected]
    for i, f in enumerate(as_completed(futures), 1):
        rows.append(f.result())
        bar.progress(i/len(futures), text=f"Tarama: {i}/{len(futures)}")
bar.empty()
valid = [x for x in rows if "Hata" not in x]
errors = [x for x in rows if "Hata" in x]
if not valid:
    st.error("Kraken'den veri alınamadı.")
    for x in errors:
        st.write(f"{x['Coin']}: {x['Hata']}")
    st.stop()

df = pd.DataFrame(valid).sort_values(["Skor","Coin"], ascending=[False,True])
a,b,c,d = st.columns(4)
a.metric("Taranan coin",len(valid))
b.metric("AL",int((df.Sinyal=="AL").sum()))
c.metric("SAT",int((df.Sinyal=="SAT").sum()))
d.metric("BEKLE",int((df.Sinyal=="BEKLE").sum()))
st.subheader("🔎 Piyasa tarama sonuçları")
filters = st.multiselect("Sinyal filtresi",["AL","BEKLE","SAT"],default=["AL","BEKLE","SAT"])
shown = df[df.Sinyal.isin(filters)][["Coin","Parite","Fiyat (USD)","Son mum %","RSI","Skor","Sinyal"]].copy()
if not shown.empty:
    shown["Fiyat (USD)"] = shown["Fiyat (USD)"].map(lambda x:f"${x:,.6f}".rstrip("0").rstrip("."))
    shown["Son mum %"] = shown["Son mum %"].map(lambda x:f"{x:+.2f}%")
    shown["RSI"] = shown["RSI"].map(lambda x:f"{x:.1f}")
st.dataframe(shown, use_container_width=True, hide_index=True)

st.subheader("🎯 Coin detayları")
choice = st.selectbox("Coin", [x["Coin"] for x in valid])
detail = next(x for x in valid if x["Coin"] == choice)
st.write(f"**{detail['Sinyal']} | Skor: {detail['Skor']}/4 | RSI: {detail['RSI']:.1f}**")
st.write(detail["Açıklama"])
st.metric("Son fiyat", f"${detail['Fiyat (USD)']:,.6f}".rstrip("0").rstrip("."))

st.subheader("🤖 Otomatik sanal AL-SAT")
left, right = st.columns([2,1])
with left:
    st.session_state.auto_trade = st.toggle("Otomatik sanal al-satı etkinleştir",
        value=st.session_state.auto_trade,
        help="Açıkken her yenilemede açık pozisyonlar ve yeni AL/SAT sinyalleri kontrol edilir.")
with right:
    st.metric("İşlem başına hedef", "%10 sermaye")
sl = st.number_input("Stop-Loss (%)", min_value=0.5, max_value=30.0, value=2.0, step=0.5)
tp = st.number_input("Take-Profit (%)", min_value=0.5, max_value=100.0, value=4.0, step=0.5)
prices = {x["Coin"]:x["Fiyat (USD)"] for x in valid}
signals = {x["Coin"]:x["Sinyal"] for x in valid}
events = []

if st.session_state.auto_trade:
    # İlk önce mevcut pozisyonları kapatma koşullarını denetle.
    for coin in list(st.session_state.positions):
        if coin not in prices:
            continue
        pos = st.session_state.positions[coin]
        price = prices[coin]
        pnl = (price/pos["entry"]-1)*100
        reason = ("STOP-LOSS" if pnl <= -sl else
                  "TAKE-PROFIT" if pnl >= tp else
                  "SAT sinyali" if signals.get(coin) == "SAT" else None)
        if reason:
            proceeds = pos["units"]*price
            st.session_state.cash += proceeds
            st.session_state.history.append({
                "Zaman":time.strftime("%Y-%m-%d %H:%M:%S"), "İşlem":"OTOMATİK SAT",
                "Coin":coin, "Fiyat":price, "Miktar":pos["units"],
                "Tutar":proceeds, "Sebep":reason, "Getiri %":pnl})
            del st.session_state.positions[coin]
            events.append(f"{coin}: {reason}; ${proceeds:,.2f} nakde döndü.")

    # Bir coinde tek açık pozisyon. Her AL işlemi başlangıç sermayesinin %10'u.
    budget = st.session_state.initial_capital * 0.10
    for coin, price in prices.items():
        if coin in st.session_state.positions or signals.get(coin) != "AL":
            continue
        investment = min(budget, st.session_state.cash)
        if investment < 10:
            continue
        units = investment/price
        st.session_state.positions[coin] = {"units":units, "entry":price, "invested":investment}
        st.session_state.cash -= investment
        st.session_state.history.append({
            "Zaman":time.strftime("%Y-%m-%d %H:%M:%S"), "İşlem":"OTOMATİK AL",
            "Coin":coin, "Fiyat":price, "Miktar":units, "Tutar":investment,
            "Sebep":"AL sinyali; sermayenin %10'u", "Getiri %":0.0})
        events.append(f"{coin}: ${investment:,.2f} tutarında sanal alındı.")
    for event in events:
        st.success(event)
    if not events:
        st.info("Bu taramada yeni işlem veya satış koşulu oluşmadı.")
    st.caption("Otomatik kontrol: " + time.strftime("%Y-%m-%d %H:%M:%S"))
else:
    st.info("Otomatik sanal işlem kapalı.")

st.subheader("🧪 Sanal portföy")
manual_coin = st.selectbox("Manuel işlem yapılacak coin", list(prices), key="manual_coin")
amount = st.number_input("Manuel işlem tutarı (USD)", min_value=10.0,
    max_value=100000.0, value=100.0, step=10.0, key="manual_amount")
buy, sell = st.columns(2)
with buy:
    if st.button("🟢 Manuel sanal AL", use_container_width=True):
        price = prices[manual_coin]
        if amount > st.session_state.cash:
            st.error("Sanal nakit bakiyesi yetersiz.")
        else:
            units = amount/price
            old = st.session_state.positions.get(manual_coin)
            if old:
                total_units = old["units"]+units
                total_cost = old["invested"]+amount
                old.update(units=total_units, invested=total_cost, entry=total_cost/total_units)
            else:
                st.session_state.positions[manual_coin] = {"units":units,"entry":price,"invested":amount}
            st.session_state.cash -= amount
            st.session_state.history.append({"Zaman":time.strftime("%Y-%m-%d %H:%M:%S"),
                "İşlem":"MANUEL AL","Coin":manual_coin,"Fiyat":price,"Miktar":units,
                "Tutar":amount,"Sebep":"Kullanıcı işlemi","Getiri %":0.0})
            st.success("Manuel sanal alım yapıldı.")
with sell:
    if st.button("🔴 Manuel sanal SAT", use_container_width=True):
        pos = st.session_state.positions.get(manual_coin)
        if not pos:
            st.error("Bu coinde açık pozisyon yok.")
        else:
            price = prices[manual_coin]
            proceeds = pos["units"]*price
            pnl = (price/pos["entry"]-1)*100
            st.session_state.cash += proceeds
            st.session_state.history.append({"Zaman":time.strftime("%Y-%m-%d %H:%M:%S"),
                "İşlem":"MANUEL SAT","Coin":manual_coin,"Fiyat":price,"Miktar":pos["units"],
                "Tutar":proceeds,"Sebep":"Kullanıcı işlemi","Getiri %":pnl})
            del st.session_state.positions[manual_coin]
            st.success("Manuel sanal satış yapıldı.")

position_value = sum(p["units"]*prices.get(n,p["entry"]) for n,p in st.session_state.positions.items())
equity = st.session_state.cash+position_value
x,y,z = st.columns(3)
x.metric("Sanal nakit",f"${st.session_state.cash:,.2f}")
y.metric("Açık pozisyon değeri",f"${position_value:,.2f}")
z.metric("Toplam portföy",f"${equity:,.2f}")
pos_rows = []
for name,p in st.session_state.positions.items():
    current = prices.get(name,p["entry"])
    pos_rows.append({"Coin":name,"Miktar":p["units"],"Giriş fiyatı":p["entry"],
        "Anlık fiyat":current,"Getiri %":(current/p["entry"]-1)*100,
        "Pozisyon değeri":p["units"]*current})
if pos_rows:
    st.dataframe(pd.DataFrame(pos_rows),use_container_width=True,hide_index=True)
else:
    st.caption("Açık sanal pozisyon yok.")
if st.session_state.history:
    with st.expander("Sanal işlem geçmişi"):
        st.dataframe(pd.DataFrame(st.session_state.history),use_container_width=True,hide_index=True)
if errors:
    with st.expander(f"Verisi alınamayan coinler ({len(errors)})"):
        for item in errors:
            st.write(f"**{item['Coin']}** — {item['Hata']}")
st.caption("Not: Son mum değişimi 24 saatlik değişim değildir. Oturum sıfırlanırsa sanal portföy silinebilir. "
           "iPhone ekranı kilitlenirse Safari yenilemeyi durdurabilir. Gerçek emir gönderilmez.")
