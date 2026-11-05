import os, time, hmac, hashlib
from urllib.parse import urlencode
import requests
import streamlit as st

st.set_page_config(page_title="AI TRADER PRO | API Kontrolü", page_icon="📈", layout="wide")
BASE_URL = os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com").rstrip("/")
API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

st.title("📈 AI TRADER PRO — Binance API Kontrolü")
st.warning("Bu sürüm yalnızca bağlantıyı ve hesap erişimini kontrol eder. Emir göndermez, pozisyon açmaz ve otomatik işlem yapmaz.")

c1, c2 = st.columns(2)
c1.metric("API Key", "Tanımlı" if API_KEY else "Eksik")
c2.metric("API Secret", "Tanımlı" if API_SECRET else "Eksik")
st.caption(f"Futures API adresi: {BASE_URL}")

def check_public():
    r = requests.get(f"{BASE_URL}/fapi/v1/ping", timeout=15)
    r.raise_for_status()

def check_account():
    if not API_KEY or not API_SECRET:
        raise RuntimeError("BINANCE_API_KEY veya BINANCE_API_SECRET eksik.")
    params = {"timestamp": int(time.time() * 1000), "recvWindow": 10000}
    query = urlencode(params)
    signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    r = requests.get(
        f"{BASE_URL}/fapi/v2/account?{query}&signature={signature}",
        headers={"X-MBX-APIKEY": API_KEY}, timeout=20
    )
    try:
        data = r.json()
    except ValueError:
        data = {"msg": r.text[:300]}
    if not r.ok:
        code = data.get("code", "bilinmiyor") if isinstance(data, dict) else "bilinmiyor"
        msg = data.get("msg", "Bilinmeyen Binance hatası") if isinstance(data, dict) else "Bilinmeyen hata"
        raise RuntimeError(f"Binance HTTP {r.status_code} | kod: {code} | {msg}")
    return data

if st.button("Bağlantıyı kontrol et", type="primary", use_container_width=True):
    with st.spinner("Binance bağlantısı kontrol ediliyor..."):
        try:
            check_public()
            st.success("1/2 — Binance Futures sunucusuna genel bağlantı başarılı.")
        except Exception as e:
            st.error(f"1/2 — Binance sunucusuna ulaşılamadı: {e}")
            st.stop()
        try:
            account = check_account()
            a, b, c = st.columns(3)
            a.metric("Wallet balance", f'{float(account.get("totalWalletBalance", 0)):.4f} USDT')
            b.metric("Available balance", f'{float(account.get("availableBalance", 0)):.4f} USDT')
            c.metric("Unrealized PnL", f'{float(account.get("totalUnrealizedProfit", 0)):.4f} USDT')
            st.success("2/2 — Binance Futures API kimlik doğrulaması başarılı.")
            st.info("Bu, hesap okuma erişiminin çalıştığını gösterir; emir gönderme iznini doğrulamaz. Bu uygulama emir göndermez.")
        except Exception as e:
            st.error(f"2/2 — Futures hesap API kontrolü başarısız: {e}")
            st.markdown("""**Kontrol listesi**
- Render değişken adları tam olarak `BINANCE_API_KEY` ve `BINANCE_API_SECRET` olmalı.
- Değerlerin başında/sonunda boşluk olmamalı.
- API anahtarının Futures hesabı için hesap okuma erişimi olmalı.
- Binance IP kısıtlaması açıksa Render çıkış IP'leri izin listesinde olmalı.
- Anahtar iptal edilmiş/yenilenmiş olmamalı.""")

st.divider()
st.subheader("Güvenlik")
st.write("• Gerçek işlem: **KAPALI**")
st.write("• Emir gönderme kodu: **YOK**")
st.write("• Otomatik tarama ve arka plan iş parçacığı: **YOK**")
st.write("• Para çekme izni: **Gerekli değil; açmayın.**")
st.caption("API Secret ekrana yazdırılmaz. Anahtarları yalnızca Render Environment Variables içinde saklayın.")
