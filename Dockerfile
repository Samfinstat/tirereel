FROM python:3.11-slim

# ffmpeg + кириллические шрифты
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        fonts-dejavu-extra \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads output

EXPOSE 10000

# 2 воркера, таймаут 5 минут (генерация может занять время)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", \
     "--workers", "2", "--timeout", "300", \
     "--worker-class", "sync", "app:app"]
