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

# --- CORS AYARLARI (hls.js Hatalarını Önler) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HLS_DIR = "/app/platforms/links"
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
# Geliştirilmiş ve Boş Kalmayan Proxy Motoru
# ==========================================
def get_working_proxy():
    """
    Önce Ürdün (JO) dener. Boş dönerse Orta Doğu ülkelerini, 
    o da boşsa genel hızlı elit proxyleri sırayla tarar.
    """
    # Denenecek ülke kombinasyonları kademeli olarak genişler
    country_filters = ["JO", "SA,AE,EG", "all"]
    
    for country in country_filters:
        try:
            # İstediğiniz gibi timeout=3000 parametresi entegre edildi
            api_url = f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&country={country}&ssl=yes&anonymity=elite"
            response = requests.get(api_url, timeout=10)
            
            if response.status_code == 200 and response.text.strip():
                lines = response.text.strip().splitlines()
                # Sayfada en az bir satır proxy adresi var mı?
                if lines and len(lines[0]) > 5:
                    proxy_address = lines[0].strip()
                    if not proxy_address.startswith("http"):
                        proxy_address = f"http://{proxy_address}"
                    print(f"[PROXY BAŞARILI] Filtre: {country} -> Seçilen IP: {proxy_address}")
                    return proxy_address
        except Exception as e:
            print(f"[PROXY DENEME] {country} filtresi başarısız oldu: {e}")
            continue
            
    print("[PROXY BULUNAMADI] Havuz tamamen boş. Proxy olmadan denenecek.")
    return None


def get_expire_time_from_url(url: str) -> float:
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
    global token_expires_at
    try:
        r = session.get(api_url, timeout=10)
        r.raise_for_status()
        secured_url = r.json()["data"]["secured_url"]

        url_expire = get_expire_time_from_url(secured_url)
        if token_expires_at == 0 or url_expire < token_expires_at:
            token_expires_at = url_expire - 300  # 5 dakika emniyet payı

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
    global ffmpeg_process, active_proxy, token_expires_at
    
    while True:
        print("\n--- [YENİ PERİYOT BAŞLADI] ---")
        token_expires_at = 0
        
        # Yedekli proxy seçici devreye giriyor (Sayfa asla boş kalmıyor)
        active_proxy = get_working_proxy()
        if active_proxy:
            session.proxies = {"http": active_proxy, "https": active_proxy}
        else:
            session.proxies = {}

        all_active_inputs = []
        track_mappings = []

        for channel_key, api_url in CHANNELS.items():
            variants = get_single_channel_variants(api_url)
            for idx, (stream_info, variant_url) in enumerate(variants):
                track_name = f"{channel_key}_{idx}.m3u8"
                all_active_inputs.append(variant_url)
                track_mappings.append(track_name)

        if not all_active_inputs:
            print("[SİSTEM] Link alınamadı. 15 saniye sonra yeni proxy ile tekrar denenecek...")
            time.sleep(15)
            continue

        ffmpeg_cmd = ["ffmpeg", "-y"]
        
        # FFmpeg girdilerine proxy ekleme aşaması
        for url in all_active_inputs:
            if active_proxy:
                ffmpeg_cmd.extend(["-http_proxy", active_proxy])
            ffmpeg_cmd.extend([
                "-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\nReferer: https://roya.tv\r\n",
                "-i", url
            ])
            
        # Sizin yazdığınız meşhur haritalama döngüsü
        for idx, track_name in enumerate(track_mappings):
            ffmpeg_cmd.extend([
                "-map", f"{idx}:v?", "-map", f"{idx}:a?", "-c", "copy",
                "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
                "-hls_flags", "delete_segments+append_list",
                os.path.join(HLS_DIR, track_name)
            ])

        try:
            ffmpeg_process = subprocess.Popen(ffmpeg_cmd)
            print(f"[FFMPEG] Yayına başlandı. PID: {ffmpeg_process.pid}")
            
            # Expire süresini saniye saniye izleyen motor
            while True:
                if ffmpeg_process.poll() is not None:
                    print("[FFMPEG] Beklenmedik kesinti oluştu.")
                    break
                
                kalan_saniye = int(token_expires_at - time.time())
                
                if kalan_saniye <= 0:
                    print("[SÜRE DOLDU] Tokenların süresi bitti. Yenileniyor...")
                    ffmpeg_process.terminate()
                    ffmpeg_process.wait()
                    break
                
                time.sleep(1)
                
        except Exception as e:
            print(f"[FFMPEG HATA] Çalıştırma hatası: {e}")
            time.sleep(5)


@app.on_event("startup")
def startup_event():
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
