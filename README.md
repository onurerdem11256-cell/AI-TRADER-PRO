AI TRADER PRO – Binance USDⓈ-M Futures

1. Sistem özellikleri

* Binance USDⓈ-M Futures bağlantısı
* LONG / SHORT sinyal sistemi
* 5x kaldıraç
* Kullanılabilir bakiyenin %5’i teminat
* %2 Stop-Loss
* %4 Take-Profit
* 60 saniyede bir piyasa taraması
* Canlı bakiye ve kâr/zarar takibi
* Otomatik pozisyon kontrolü
* Günlük zarar limiti
* Binance minimum emir miktarı kontrolü

2. GitHub dosyaları

Depoda şu dosyalar bulunmalıdır:

* main.py
* requirements.txt
* render.yaml
* README_KURULUM.txt

3. Render kurulumu

Render hesabına gir ve mevcut web servisini aç.

Build Command:

pip install -r requirements.txt

Start Command:

streamlit run main.py –server.address 0.0.0.0 –server.port $PORT

4. Environment Variables

Render → Environment bölümüne şu değişkenleri ekle:

BINANCE_API_KEY

BINANCE_API_SECRET

ALLOW_LIVE_TRADING=false

BINANCE_FUTURES_BASE_URL=https://fapi.binance.com

API anahtarlarını GitHub’a veya sohbet mesajlarına yazma.

5. Binance API güvenliği

* Para çekme izni kesinlikle kapalı olmalıdır.
* Yalnızca gerekli Futures işlem izinleri verilmelidir.
* Mümkünse IP kısıtlaması kullanılmalıdır.
* API Secret güvenli tutulmalıdır.

6. İlk çalıştırma

1. GitHub değişikliklerini kaydet.
2. Render deploy işleminin tamamlanmasını bekle.
3. Uygulamayı aç.
4. Binance API bağlantısını kontrol et.
5. Canlı piyasa verilerinin geldiğini doğrula.
6. Minimum emir miktarı ve koruyucu emirleri test et.

7. İşlem stratejisi

15 dakikalık kapanmış mumlar kullanılır.

EMA20, EMA50 ve MACD birlikte değerlendirilir.

* Yükseliş koşulları: LONG
* Düşüş koşulları: SHORT
* Belirsiz piyasa: WAIT

Bu sistem kurallı teknik analiz kullanır; kendi kendine öğrenen bir yapay zekâ modeli değildir.

8. Risk uyarısı

Gerçek Futures işlemleri sermaye kaybına neden olabilir.

5x kaldıraç kullanılır. Küçük bakiyelerde Binance minimum emir tutarı nedeniyle işlem açılamayabilir.

Stop-loss, kaybı kesin bir tutarda sınırlamayı garanti etmez.

Gerçek emir modu başlangıçta kapalıdır:

ALLOW_LIVE_TRADING=false

Canlı işlem ancak API bağlantısı ve emir güvenliği doğrulandıktan sonra bilinçli olarak etkinleştirilmelidir.
