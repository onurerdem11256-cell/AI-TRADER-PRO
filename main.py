import json
import logging
import time
from datetime import datetime

import pandas as pd
import streamlit as st
import websocket

# Render Logs'a teşhis kaydı yaz
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
)
log = logging.getLogger("ai_trader_pro")

st.set_page_config(page_title="AI TRADER PRO | WebSocket teşhis", page_icon="📈", layout="wide")
st.title("📈 AI TRADER PRO — WebSocket Teşhis")
st.warning(
    "GÜVENLİ DEMO: Bu sürüm yalnızca herkese açık piyasa verisini okur. "
    "Gerçek emir, pozisyon, kaldıraç, transfer veya para çekme yapmaz."
)

DEFAULT_COINS = ["btcusdt", "ethusdt", "bnbusdt", "solusdt", "xrpusdt"]
ALL_COINS = DEFAULT_COINS + ["adausdt", "dogeusdt", "linkusdt", "avaxusdt", "ltcusdt"]

with st.sidebar:
    st.subheader("Tarama ayarları")
    selected = st.multiselect(
        "İzlenecek coinler",
        ALL_COINS,
        default=DEFAULT_COINS[:2],
        format_func=lambda x: x.upper(),
    )
    duration = st.slider("Dinleme süresi (saniye)", 5, 30, 15)

def scan_websocket(symbols, seconds):
    if not symbols:
        raise ValueError("En az bir coin seçmelisin.")

    streams = "/".join(f"{symbol}@ticker" for symbol in symbols)
    url = "wss://fstream.binance.com/stream?streams=" + streams
    log.info("WS_DIAG_START symbols=%s duration=%s url=%s", ",".join(symbols), seconds, url)

    ws = None
    received = 0
    latest = {}
    started = time.time()

    try:
        log.info("WS_DIAG_CONNECTING")
        ws = websocket.create_connection(
            url,
            timeout=10,
            enable_multithread=True,
            http_proxy_host=None,
        )
        log.info("WS_DIAG_CONNECTED")
        ws.settimeout(3)

        while time.time() - started < seconds:
            try:
                raw = ws.recv()
                if not raw:
                    log.warning("WS_DIAG_EMPTY_FRAME")
                    continue

                received += 1
                packet = json.loads(raw)
                data = packet.get("data", packet)

                symbol = data.get("s", "").lower()
                if symbol and "c" in data:
                    latest[symbol] = {
                        "price": float(data["c"]),
                        "change": float(data.get("P", 0)),
                        "volume": float(data.get("q", 0)),
                        "event_time": int(data.get("E", 0)),
                    }
            except websocket.WebSocketTimeoutException:
                log.info("WS_DIAG_WAITING frames=%s elapsed=%.1fs", received, time.time() - started)
            except json.JSONDecodeError:
                log.exception("WS_DIAG_JSON_ERROR")
            except websocket.WebSocketConnectionClosedException:
                log.exception("WS_DIAG_CONNECTION_CLOSED")
                raise

        log.info(
            "WS_DIAG_FINISHED frames=%s symbols_with_data=%s elapsed=%.1fs",
            received, ",".join(sorted(latest.keys())), time.time() - started
        )
        return latest, received

    except Exception:
        log.exception("WS_DIAG_FAILED")
        raise
    finally:
        if ws is not None:
            try:
                ws.close()
                log.info("WS_DIAG_SOCKET_CLOSED")
            except Exception:
                log.exception("WS_DIAG_CLOSE_ERROR")

st.subheader("Canlı piyasa verisi")
st.caption("Binance USDⓈ-M Futures WebSocket bağlantısı kullanılır. REST piyasa isteği gönderilmez.")

if st.button("WebSocket bağlantısını test et", type="primary", use_container_width=True):
    st.session_state["ws_diag_ran"] = True
    st.session_state["ws_diag_error"] = None
    st.session_state["ws_diag_data"] = {}
    st.session_state["ws_diag_frames"] = 0

    try:
        with st.spinner(f"Binance WebSocket'e bağlanılıyor; {duration} saniye dinleniyor..."):
            data, frames = scan_websocket(selected, duration)
        st.session_state["ws_diag_data"] = data
        st.session_state["ws_diag_frames"] = frames
        st.session_state["ws_diag_time"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    except Exception as exc:
        st.session_state["ws_diag_error"] = f"{type(exc).__name__}: {exc}"
        st.error(f"Bağlantı başarısız: {type(exc).__name__}: {exc}")
        st.caption("Ayrıntılı hata Render → Logs bölümüne WS_DIAG_FAILED satırıyla yazıldı.")

if st.session_state.get("ws_diag_ran"):
    if st.session_state.get("ws_diag_error"):
        st.error("Teşhis sonucu: WebSocket bağlantısı veya veri okuma başarısız.")
        st.code(st.session_state["ws_diag_error"])
    else:
        data = st.session_state.get("ws_diag_data", {})
        frames = st.session_state.get("ws_diag_frames", 0)
        st.caption("Son test: " + st.session_state.get("ws_diag_time", ""))
        st.metric("Alınan WebSocket mesajı", frames)
        if data:
            rows = []
            for symbol, item in data.items():
                rows.append({
                    "Coin": symbol.upper(),
                    "Fiyat (USDT)": item["price"],
                    "24s değişim (%)": item["change"],
                    "24s hacim (USDT)": item["volume"],
                })
            st.success(f"Bağlantı başarılı. {len(data)} coin için veri alındı.")
            st.dataframe(
                pd.DataFrame(rows).sort_values("24s değişim (%)", ascending=False),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.warning(
                "WebSocket bağlantısı kurulmuş olabilir ancak seçilen sürede ticker verisi alınmadı. "
                "Render Logs içindeki WS_DIAG satırlarını kontrol et."
            )

st.divider()
st.subheader("Güvenlik")
st.write("- Gerçek emir gönderme: **YOK**")
st.write("- API Key / Secret kullanımı: **YOK**")
st.write("- Hesap veya bakiye erişimi: **YOK**")
st.write("- Otomatik arka plan taraması: **YOK**")
st.write("- Para çekme / transfer: **YOK**")
st.caption("Test yalnızca sen düğmeye bastığında başlar. 418/429 REST API limitlerini tetikleyecek REST çağrısı yapmaz.")
