# 1. Aşama: Resmi ve güncel Python imajını taban alıyoruz
FROM python:3.11-slim

# 2. Aşama: Python loglarının terminalde anlık görünmesini sağlıyoruz (Render takibi için kritik)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 3. Aşama: Konteyner içindeki çalışma dizinini belirliyoruz
WORKDIR /app

# 4. Aşama: FFmpeg ve derleme araçlarını linux sistemine kuruyoruz
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 5. Aşama: Python kütüphanelerini kopyalayıp yüklüyoruz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Aşama: Projenin tüm dosyalarını (main.py vb.) konteynere aktarıyoruz
COPY . .

# 7. Aşama: Render'ın atayacağı dinamik portu ($PORT) yakalayarak uygulamayı otomatik başlatıyoruz
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
