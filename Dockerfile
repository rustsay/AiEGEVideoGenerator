FROM python:3.12-slim

WORKDIR /srv

# ffmpeg — сборка MP4, шрифты — рендер текста на «доске»
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY app ./app
COPY static ./static
RUN mkdir -p data/media

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
