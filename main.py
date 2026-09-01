import os
import time
from urllib.parse import urljoin, urlparse, parse_qs
from fastapi import FastAPI, Response, HTTPException
import requests

app = FastAPI(title="Roya TV Link Synchronizer")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept-Encoding": "identity",
})

# Kanalların API adresleri tanımları
CHANNELS = {
    "roya1": "https://ticket.roya-tv.com/api/v5/fastchannel/1",
    "roya2": "https://ticket.roya-tv.com/api/v5/fastchannel/21",
    "roya3": "https://ticket.roya-tv.com/api/v5/fastchannel/48",
}

# Bellek içi cache (önbellek) yapısı
# Her kanal için: {"variants": [...], "expires_at": unix_timestamp}
cache = {}


def get_expire_time_from_url(url: str) -> float:
    """
    URL içerisindeki 'exp' veya 'expires' parametresini ayıklar.
    Bulamazsa güvenli tarafta kalmak için 2 saat (7200 sn) geçerlilik verir.
    """
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)

    # Genel IPTV/HLS token parametre adları (exp, expires vb.) kontrol edilir
    for param in ["exp", "expires", "token_time"]:
        if param in query_params:
            try:
                return float(query_params[param][0])
            except ValueError:
                continue

    # URL içinde parametre bulunamazsa anlık zamana + 2 saat eklenir
    return time.time() + 7200


def fetch_and_cache_variants(channel_key: str, api_url: str):
    """
    API'ye istek atar, m3u8 playlist'i ayrıştırır ve expire süresini hesaplayarak cache'ler.
    """
    print(f"[{channel_key}] Linklerin süresi dolmuş veya hiç alınmamış. Güncelleniyor...")

    # 1. API'den secured URL'yi al
    r = session.get(api_url, timeout=10)
    r.raise_for_status()
    secured_url = r.json()["data"]["secured_url"]

    # URL'den expire süresini hesapla
    expire_timestamp = get_expire_time_from_url(secured_url)

    # 2. Master m3u8 içeriğini çek
    r = session.get(secured_url, timeout=10)
    r.raise_for_status()
    playlist = r.text

    variants = []
    lines = playlist.splitlines()

    # 3. Stream etiketi ve ona karşılık gelen URL'leri eşleştir
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

    # Emniyet payı: Gerçek süreden 5 dakika (300 saniye) önce bayat kabul et
    safe_expires_at = expire_timestamp - 300

    # Cache'e yaz
    cache[channel_key] = {
        "variants": variants,
        "expires_at": safe_expires_at
    }
    print(f"[{channel_key}] Yeni expire süresi: {safe_expires_at} (Kalan: {int(safe_expires_at - time.time())} saniye)")


def generate_m3u8_content(variants) -> str:
    """Alınan varyantları m3u8 formatında metne dönüştürür."""
    content_lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3"
    ]
    for stream_info, variant_url in variants:
        content_lines.append(stream_info)
        content_lines.append(variant_url)

    return "\n".join(content_lines) + "\n"


@app.get("/links/{channel_name}.m3u8")
def get_channel_m3u8(channel_name: str):
    """
    İstek geldiğinde expire süresini kontrol eder.
    Süre dolmuşsa otomatik günceller, dolmamışsa cache'den çok hızlı yanıt döner.
    """
    if channel_name not in CHANNELS:
        raise HTTPException(status_code=404, detail="Kanal bulunamadı.")

    api_url = CHANNELS[channel_name]
    channel_cache = cache.get(channel_name)

    # Eğer hiç cache yoksa veya expire süresi şimdiki zamandan küçükse tetikle
    if not channel_cache or time.time() >= channel_cache["expires_at"]:
        try:
            fetch_and_cache_variants(channel_name, api_url)
            channel_cache = cache[channel_name]
        except Exception as e:
            # Güncelleme sırasında hata olursa ve eski veri varsa geçici olarak eskisini dön
            if channel_cache:
                print(f"Güncelleme hatası, eski cache kullanılıyor: {e}")
            else:
                raise HTTPException(status_code=502, detail=f"Yayın sağlayıcı hatası: {e}")

    # m3u8 formatında çıktı üretip HTTP Header'ları ayarla
    m3u8_text = generate_m3u8_content(channel_cache["variants"])
    return Response(content=m3u8_text, media_type="application/x-mpegURL")


@app.get("/")
def health_check():
    """Render'ın servisi canlı tutması ve takip etmesi için kök dizin kontrolü."""
    return {"status": "healthy", "monitored_channels": list(CHANNELS.keys())}


if __name__ == "__main__":
    import uvicorn
    # Yerelde test etmek için local port ataması
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
