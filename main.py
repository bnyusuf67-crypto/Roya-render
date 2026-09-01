import os
import time
import subprocess
import threading
from urllib.parse import urljoin, urlparse, parse_qs
from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import requests

app = FastAPI(title="Roya TV Smart FFmpeg Relay")

# --- CORS AYARLARI ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HLS_DIR = "/app/links"
os.makedirs(HLS_DIR, exist_ok=True)
app.mount("/live", StaticFiles(directory=HLS_DIR), name="live")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Encoding": "identity",
})

CHANNELS = {
    "roya1": "https://ticket.roya-tv.com/api/v5/fastchannel/1",
    "roya2": "https://ticket.roya-tv.com/api/v5/fastchannel/21",
    "roya3": "https://ticket.roya-tv.com/api/v5/fastchannel/48",
}

ffmpeg_process = None
active_proxy = None
token_expires_at = 0


# ==========================================
# AE VE EG ODAKLI AKTİF PROXY MOTORU
# ==========================================
def get_jordan_friendly_proxy():
    """
    Roya TV'nin açık olduğu BAE (AE) ve Mısır (EG) sayfalarını sırayla kontrol eder.
    """
    # Sizin belirttiğiniz aktif proxy havuzuna sahip ülkeleri sırayla tarar
    target_countries = ["AE", "EG"]
    
    for country in target_countries:
        try:
            # Belirttiğiniz v2 API yapısı ve timeout=3000 filtresi uygulandı
            api_url = f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&country={country}"
            response = requests.get(api_url, timeout=10)
            
            if response.status_code == 200 and response.text.strip():
                lines = response.text.strip().splitlines()
                # Eğer listeden en az bir adet IP döndüyse
                if lines and len(lines) > 0:
                    proxy = lines[0].strip()  # İlk sıradaki en taze proxy'yi cımbızla
                    if proxy:
                        proxy_address = f"http://{proxy}" if not proxy.startswith("http") else proxy
                        print(f"[PROXY BULUNDU] Ülke: {country} -> Seçilen IP: {proxy_address}")
                        return proxy_address
        except Exception as e:
            print(f"[PROXY DENEME] {country} sayfası taranamadı: {e}")
            continue

    print("[UYARI] BAE ve Mısır sayfaları o an boş döndü! Proxy olmadan denenecek.")
    return None


def get_expire_time_from_url(url: str) -> float:
    """URL içindeki exp veya expires parametresini okur ve unix timestamp döner."""
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    for param in ["exp", "expires", "token_time"]:
        if param in query_params:
            try:
                return float(query_params[param])
            except (ValueError, IndexError):
                continue
    return time.time() + 7200


def get_single_channel_variants(api_url):
    """Roya TV API'sinden master listeyi alır ve alt kalitelerin linklerini çözer."""
    global token_expires_at
    try:
        r = session.get(api_url, timeout=10)
        r.raise_for_status()
        secured_url = r.json()["data"]["secured_url"]

        # 🧠 EXPIRE SÜRESİNİ OKUMA VE KAYDETME MECHANİSMASI
        url_expire = get_expire_time_from_url(secured_url)
        if token_expires_at == 0 or url_expire < token_expires_at:
            # Emniyet payı: Linkler patlamadan tam 5 dakika (300 sn) önce kapatıp yenilemeyi hedefler
            token_expires_at = url_expire - 300

        r = session.get(secured_url, timeout=10)
        r.raise_for_status()
        playlist = r.text

        variants = []
        lines = playlist.splitlines()
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                stream_info = line
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()
                    if next_line and not next_line.startswith("#"):
                        full_url = urljoin(secured_url, next_line)
                        variants.append((stream_info, full_url))
                        break
        return variants
    except Exception as e:
        print(f"[VARYANT HATASI] Link çözümleme hatası: {e}")
        return []


def run_ffmpeg_loop():
    """FFmpeg sürecini, zaman sayacını ve proxy döngüsünü yöneten ana motor."""
    global ffmpeg_process, active_proxy, token_expires_at
    
    while True:
        print("\n--- [YENİ PERİYOT BAŞLADI] ---")
        token_expires_at = 0  # Önceki süreyi sıfırla
        
        # BAE ve Mısır sayfalarından dinamik olarak proxy çekiliyor
        active_proxy = get_jordan_friendly_proxy()
        if active_proxy:
            session.proxies = {"http": active_proxy, "https": active_proxy}
        else:
            session.proxies = {}

        all_active_inputs = []
        track_mappings = []

        # Tüm kanalları proxy arkasından güvenle tarayıp doldur
        for channel_key, api_url in CHANNELS.items():
            variants = get_single_channel_variants(api_url)
            for idx, (stream_info, variant_url) in enumerate(variants):
                track_name = f"{channel_key}_{idx}.m3u8"
                all_active_inputs.append(variant_url)
                track_mappings.append(track_name)

        # Eğer listeler doldurulamadıysa proxy banlı veya sorunlu olabilir, 10 sn bekle ve döngüyü başa sar
        if not all_active_inputs:
            print("[SİSTEM] Varyant toplanamadı. 10 saniye sonra yeni proxy ile döngü başa saracak...")
            time.sleep(10)
            continue

        # FFmpeg komut dizisi inşası
        ffmpeg_cmd = ["ffmpeg", "-y"]
        
        for url in all_active_inputs:
            if active_proxy:
                ffmpeg_cmd.extend(["-http_proxy", active_proxy])
            ffmpeg_cmd.extend([
                "-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\nReferer: https://roya.tv\r\n",
                "-i", url
            ])
            
        # Sizin enumerate haritalama döngünüz
        for idx, track_name in enumerate(track_mappings):
            ffmpeg_cmd.extend([
                "-map", f"{idx}:v?", "-map", f"{idx}:a?", "-c", "copy",
                "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
                "-hls_flags", "delete_segments+append_list",
                os.path.join(HLS_DIR, track_name)
            ])

        try:
            ffmpeg_process = subprocess.Popen(ffmpeg_cmd)
            print(f"[FFMPEG] Canlı kopyalama başlatıldı. PID: {ffmpeg_process.pid}")
            
            # 🧠 EXPIRE SÜRESİNİ HESAPLAYAN SANİYELİK MOTOR
            while True:
                if ffmpeg_process.poll() is not None:
                    print("[FFMPEG] Süreç dış bir sebepten kapandı. Yeniden başlatılıyor...")
                    break
                
                # Kalan süreyi saniye cinsinden hesapla
                kalan_saniye = int(token_expires_at - time.time())
                
                # Süre dolduysa (5 dakikalık emniyet payı geldiyse) döngüyü kır ve en başa dön
                if kalan_saniye <= 0:
                    print("[SÜRE DOLDU] Token expire zamanı geldi! Linkler yenileniyor...")
                    ffmpeg_process.terminate()  # Eski FFmpeg'i kapat
                    ffmpeg_process.wait()
                    break
                
                time.sleep(1)  # Hassas saniyelik takip mekanizması
                
        except Exception as e:
            print(f"[FFMPEG HATA] Çalıştırma hatası: {e}")
            time.sleep(5)


@app.on_event("startup")
def startup_event():
    """Konteyner açıldığı an otonom motoru arka planda ateşler."""
    thread = threading.Thread(target=run_ffmpeg_loop, daemon=True)
    thread.start()


@app.get("/")
def health_check():
    ffmpeg_status = "Running" if (ffmpeg_process and ffmpeg_process.poll() is None) else "Stopped"
    kalan_sure = int(token_expires_at - time.time()) if token_expires_at > 0 else 0
    return {
        "status": "healthy",
        "ffmpeg": ffmpeg_status,
        "active_proxy": active_proxy if active_proxy else "Direct/None",
        "token_expires_in": f"{kalan_sure} seconds"
    }
