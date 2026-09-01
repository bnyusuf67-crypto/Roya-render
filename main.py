import os
import time
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlparse, parse_qs
from fastapi import FastAPI, Response, HTTPException
import requests

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept-Encoding": "identity",
})

CHANNELS = {
    "roya1": "https://ticket.roya-tv.com/api/v5/fastchannel/1",
    "roya2": "https://ticket.roya-tv.com/api/v5/fastchannel/21",
    "roya3": "https://ticket.roya-tv.com/api/v5/fastchannel/48",
}

cache = {}

# --- YENİ EKLENEN ÖN-YÜKLEME FONKSİYONLARI ---

def fetch_and_cache_variants(channel_key: str, api_url: str):
    """API'ye istek atar ve linkleri cache'ler."""
    print(f"[{channel_key}] Linkler güncelleniyor...")
    r = session.get(api_url, timeout=10)
    r.raise_for_status()
    secured_url = r.json()["data"]["secured_url"]

    # Expire süresini bul
    parsed_url = urlparse(secured_url)
    query_params = parse_qs(parsed_url.query)
    expire_timestamp = time.time() + 7200 # Varsayılan 2 saat
    for param in ["exp", "expires", "token_time"]:
        if param in query_params:
            try:
                expire_timestamp = float(query_params[param][0])
                break
            except (ValueError, IndexError):
                continue

    # Master m3u8 çek ve parçala
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

    if not variants:
        raise Exception(f"Yayın URL'leri bulunamadı: {api_url}")

    # 5 dakika emniyet payı düşerek kaydet
    cache[channel_key] = {
        "variants": variants,
        "expires_at": expire_timestamp - 300
    }
    print(f"[{channel_key}] Başarıyla cache'lendi. Kalan süre: {int(cache[channel_key]['expires_at'] - time.time())} sn.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Render/Docker sunucusu İLK AÇILDIĞI AN burası otomatik tetiklenir.
    Kullanıcıdan istek gelmesini beklemeden tüm kanalları bir kez doldurur.
    """
    print("Sunucu başlatılıyor... İlk linkler otomatik çekiliyor.")
    for channel_name, api_url in CHANNELS.items():
        try:
            fetch_and_cache_variants(channel_name, api_url)
        except Exception as e:
            print(f"Açılışta link çekme hatası [{channel_name}]: {e}")
    yield
    print("Sunucu kapatılıyor...")


# FastAPI'ye lifespan'i tanımlıyoruz
app = FastAPI(title="Roya TV Link Synchronizer", lifespan=lifespan)

# --- ENDPOINTLER ---

@app.get("/links/{channel_name}.m3u8")
def get_channel_m3u8(channel_name: str):
    if channel_name not in CHANNELS:
        raise HTTPException(status_code=404, detail="Kanal bulunamadı.")

    channel_cache = cache.get(channel_name)

    # LAZY LOAD KONTROLÜ: Süre bittiyse veya ilk yüklemede hata alındıysa otomatik günceller
    if not channel_cache or time.time() >= channel_cache["expires_at"]:
        try:
            fetch_and_cache_variants(channel_name, CHANNELS[channel_name])
            channel_cache = cache[channel_name]
        except Exception as e:
            if channel_cache:
                print(f"Güncelleme hatası, eski cache kullanılıyor: {e}")
            else:
                raise HTTPException(status_code=502, detail=f"Yayın sağlayıcı hatası: {e}")

    # m3u8 formatında metin üretip dönüyoruz
    content_lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for stream_info, variant_url in channel_cache["variants"]:
        content_lines.append(stream_info)
        content_lines.append(variant_url)
    
    return Response(content="\n".join(content_lines) + "\n", media_type="application/x-mpegURL")

@app.get("/")
def health_check():
    return {"status": "healthy", "cached_channels": list(cache.keys())}
