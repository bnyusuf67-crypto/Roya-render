import os
import time
import subprocess
import threading
from urllib.parse import urljoin
from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import requests

app = FastAPI(title="Roya TV FFmpeg HLS Relay")

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

# Üretilen m3u8 ve ts dosyalarını dışarıya açar (Örn: /live/roya1_1080p.m3u8 vb.)
app.mount("/live", StaticFiles(directory=HLS_DIR), name="live")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept-Encoding": "identity",
})

CHANNELS = {
    "roya1": "https://ticket.roya-tv.com/api/v5/fastchannel/48",
    "roya2": "https://ticket.roya-tv.com/api/v5/fastchannel/21",
    "roya3": "https://ticket.roya-tv.com/api/v5/fastchannel/1",
}

ffmpeg_process = None


def get_single_channel_variants(api_url):
    """
    Sizin yazdığınız orijinal varyant bulma fonksiyonu.
    Master playlist'i okur ve alt kalitelerin doğrudan ulaşılabilecek tam URL'lerini döner.
    """
    try:
        # 1. API'den secured URL'yi al
        r = session.get(api_url, timeout=10)
        r.raise_for_status()
        secured_url = r.json()["data"]["secured_url"]

        # 2. Master m3u8 içeriğini çek
        r = session.get(secured_url, timeout=10)
        r.raise_for_status()
        playlist = r.text

        variants = []
        lines = playlist.splitlines()
        
        # 3. Stream etiketi ve URL'leri eşleştir
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                # Örn: #EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
                stream_info = line
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()
                    if next_line and not next_line.startswith("#"):
                        full_url = urljoin(secured_url, next_line)
                        variants.append((stream_info, full_url))
                        break
        return variants
    except Exception as e:
        print(f"Varyantlar bulunurken hata oluştu [{api_url}]: {e}")
        return []


def run_ffmpeg():
    """Tüm kanalların tüm alt varyantlarını bulur ve FFmpeg'e besler."""
    global ffmpeg_process
    
    print("FFmpeg için varyantlar toplanıyor...")
    
    all_active_inputs = []  # FFmpeg'e verilecek -i linkleri
    track_mappings = []     # FFmpeg'in çıktı haritası ve dosya adları

    # Tüm kanalları dön ve içlerindeki kaliteleri (varyantları) ayıkla
    for channel_key, api_url in CHANNELS.items():
        variants = get_single_channel_variants(api_url)
        
        # Her bir varyant (kalite) için FFmpeg'e ayrı bir girdi ve çıktı tanımlıyoruz
        for idx, (stream_info, variant_url) in enumerate(variants):
            # Dosya adını benzersiz yapmak için kanal adı + kalite sırası (örn: roya1_0.m3u8, roya1_1.m3u8)
            track_name = f"{channel_key}_{idx}.m3u8"
            
            all_active_inputs.append(variant_url)
            track_mappings.append(track_name)

    if not all_active_inputs:
        print("Kritik Hata: Hiçbir varyant URL'si toplanamadı. 30 sn sonra tekrar denenecek.")
        time.sleep(30)
        return run_ffmpeg()

    # FFmpeg komutunu inşa ediyoruz
    ffmpeg_cmd = ["ffmpeg", "-y"]
    
    # 1. Adım: Tüm varyant linklerini girdi (-i) olarak ekle
    for url in all_active_inputs:
        ffmpeg_cmd.extend(["-i", url])
        
    # 2. Adım: Sizin ilettiğiniz dinamik map ve segmentasyon döngüsü
    # Artık idx doğrudan her bir varyantın (alt kalitenin) indexine denk geliyor
    for idx, track_name in enumerate(track_mappings):
        ffmpeg_cmd.extend([
            "-map", f"{idx}:v?", 
            "-map", f"{idx}:a?", 
            "-c", "copy",
            "-f", "hls", 
            "-hls_time", "4", 
            "-hls_list_size", "10",
            "-hls_flags", "delete_segments+append_list",
            os.path.join(HLS_DIR, track_name)
        ])

    try:
        ffmpeg_process = subprocess.Popen(ffmpeg_cmd)
        print(f"FFmpeg toplam {len(all_active_inputs)} farklı varyantla başlatıldı. PID: {ffmpeg_process.pid}")
        
        # FFmpeg süreç koptuğunda veya token bittiğinde burası çözülür
        ffmpeg_process.wait()
        print("FFmpeg süreci sonlandı. Linkler yenilenip 10 saniye sonra tekrar başlatılacak...")
        
    except Exception as e:
        print(f"FFmpeg çalıştırılırken hata oluştu: {e}")
        
    time.sleep(10)
    return run_ffmpeg()


@app.on_event("startup")
def startup_event():
    """Sunucu açıldığında FFmpeg thread'ini arka planda tetikler."""
    thread = threading.Thread(target=run_ffmpeg, daemon=True)
    thread.start()


@app.get("/")
def health_check():
    ffmpeg_status = "Running" if (ffmpeg_process and ffmpeg_process.poll() is None) else "Stopped"
    return {
        "status": "healthy",
        "ffmpeg": ffmpeg_status,
        "hls_output_folder": "/live/"
    }
