# Free Discord Bot on a server, 24/7. See HOSTING.md.
FROM python:3.13-slim

# ffmpeg: for /gif, /opacity and /file-format. tzdata: time zones for /time.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt
COPY botd ./botd
COPY ui ./ui

# settings and data live in /data (a Docker volume), keys come from the .env file
ENV BOTD_DATA_DIR=/data \
    BOTD_HOST=0.0.0.0 \
    BOTD_PORT=8765 \
    BOTD_AUTOSTART=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
CMD ["python", "-m", "botd"]
