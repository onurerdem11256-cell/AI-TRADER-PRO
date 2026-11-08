import json, time
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
import websocket

st.set_page_config(page_title="AI TRADER PRO", page_icon="📈", layout="wide")
st.title("📈 AI TRADER PRO — WebSocket Demo")
st.warning("GÜVENLİ DEMO: Gerçek emir, pozisyon, kaldıraç, transfer veya para çekme yoktur. Kâr garantisi yoktur.")

COINS = ["btcusdt","ethusdt","bnbusdt","solusdt","xrpusdt","adausdt","dogeusdt","linkusdt","avaxusdt","ltcusdt"]
if "quotes" not in st.session_state: st.session_state.quotes = {}
if "trades" not in st.session_state: st.session_state.trades = []

with st.sidebar:
    selected = st.multiselect("İzlenecek coinler", COINS, default=COINS[:5], format_func=lambda x:x.upper())
    seconds = st.slider("WebSocket dinleme süresi", 5, 20, 10)

def scan(symbols, duration):
    if not symbols: raise ValueError("En az bir coin seç.")
    streams = "/".join(f"{s}@ticker" for s in symbols)
    ws = websocket.create_connection("wss://fstream.binance.com/stream?streams="+streams, timeout=5)
    ws.settimeout(2)
    found = {}
    end = time.time()+duration
    try:
        while time.time()<end:
            try: packet = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException: continue
            d = packet.get("data", {})
            if d.get("s"):
                found[d["s"].lower()] = {
                    "price":float(d["c"]), "change":float(d["P"]),
                    "volume":float(d["q"]), "time":int(d["E"])
                }
    finally: ws.close()
    return found

st.subheader("Canlı piyasa taraması")
st.caption("Binance Futures WebSocket akışı kullanılır; REST piyasa isteği gönderilmez.")
if st.button("Canlı taramayı başlat", type="primary", use_container_width=True):
    try:
        with st.spinner(f"{seconds} saniye canlı veri alınıyor..."):
            st.session_state.quotes = scan(selected, seconds)
        st.session_state.scan_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        if not st.session_state.quotes: st.warning("Veri gelmedi; daha sonra tekrar dene.")
    except Exception as e:
        st.error(f"WebSocket bağlantı hatası: {e}")

if st.session_state.quotes:
    rows=[]
    for s,q in st.session_state.quotes.items():
        if q["change"] >= 2: sig="Pozitif momentum"
        elif q["change"] <= -2: sig="Negatif momentum"
        else: sig="Nötr / bekle"
        rows.append({"Coin":s.upper(),"Fiyat (USDT)":q["price"],"24s değişim %":q["change"],"24s hacim (USDT)":q["volume"],"Ön sinyal":sig})
    df=pd.DataFrame(rows).sort_values("24s değişim %",ascending=False)
    st.caption("Son tarama: "+st.session_state.get("scan_time",""))
    st.dataframe(df,use_container_width=True,hide_index=True)
    st.info("Bu ön sinyal yalnızca 24 saatlik değişimi gösterir; tek başına işlem kararı değildir.")

    st.subheader("Sanal işlem laboratuvarı")
    symbols=sorted(st.session_state.quotes)
    coin=st.selectbox("Coin",symbols)
    side=st.radio("Yön",["LONG","SHORT"],horizontal=True)
    amount=st.number_input("Sanal pozisyon büyüklüğü (USDT)",1.0,1000.0,10.0,1.0)
    if st.button("Sanal pozisyon aç"):
        st.session_state.trades.append({"coin":coin,"side":side,"entry":st.session_state.quotes[coin]["price"],"amount":amount,"status":"AÇIK"})
        st.success(f"Sanal {side} açıldı: {coin.upper()}")

    st.subheader("Sanal pozisyonlar")
    for i,t in enumerate(st.session_state.trades):
        if t["status"]!="AÇIK": continue
        q=st.session_state.quotes.get(t["coin"])
        if not q: continue
        move=(q["price"]/t["entry"]-1)*100
        if t["side"]=="SHORT": move=-move
        pnl=t["amount"]*move/100
        a,b=st.columns(2)
        a.metric(f'{t["coin"].upper()} {t["side"]}',f'{q["price"]:.6g} USDT')
        b.metric("Sanal anlık PnL",f"{pnl:+.4f} USDT",f"{move:+.2f}%")
        if st.button(f'Bu sanal pozisyonu kapat #{i}',key=f"close{i}"):
            t["status"]="KAPALI"; t["exit"]=q["price"]; t["pnl"]=pnl; st.rerun()
    closed=[t for t in st.session_state.trades if t["status"]=="KAPALI"]
    if closed:
        st.subheader("Kapanan işlemler")
        st.dataframe(pd.DataFrame([{"Coin":t["coin"].upper(),"Yön":t["side"],"Giriş":t["entry"],"Çıkış":t["exit"],"PnL USDT":t["pnl"]} for t in closed]),use_container_width=True,hide_index=True)
        st.metric("Toplam sanal PnL",f'{sum(t["pnl"] for t in closed):+.4f} USDT')
else:
    st.info("Coinleri seçip canlı taramayı başlat. Ardından sanal işlem alanı açılır.")

st.divider()
st.write("**Önemli sınırlar**")
st.write("- Tarama yalnızca düğmeye basınca çalışır; 7/24 arka plan botu değildir.")
st.write("- Sanal işlemler oturum belleğindedir; uygulama yeniden başlarsa kaybolabilir.")
st.write("- Bu sürüm otomatik işlem yapmaz ve gerçek emir göndermez.")
