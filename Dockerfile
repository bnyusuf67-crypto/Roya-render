# 1. Aşama: Resmi ve hafif Python imajını kullanıyoruz
FROM python:3.11-slim

# 2. Aşama: Python'un çıktıları terminale anlık yazdırmasını sağlıyoruz (Log takibi için önemli)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 3. Aşama: Çalışma dizinini oluştur ve ayarla
WORKDIR /app

# 4. Aşama: Sistem bağımlılıklarını güncelle ve temiz tut
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 5. Aşama: Önce sadece requirements.txt dosyasını kopyala (Docker cache mekanizmasından yararlanmak için)
COPY requirements.txt .

# 6. Aşama: Gerekli Python kütüphanelerini yükle
RUN pip install --no-cache-dir -r requirements.txt

# 7. Aşama: Proje kodlarını kopyala
COPY . .

# 8. Aşama: Render'ın dinamik port atamasını ($PORT) destekleyecek şekilde uygulamayı başlat
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
