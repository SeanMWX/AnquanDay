FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANQUANDAY_API_HOST=0.0.0.0 \
    ANQUANDAY_API_PORT=8765 \
    ANQUANDAY_DATA_DIR=/app/data
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --no-create-home app \
    && mkdir /app/data && chown app:app /app/data

COPY engine/ ./engine/
COPY service/ ./service/
COPY config/ ./config/
# Immutable bootstrap catalogue remains available with custom read-only config mounts.
COPY config/apt_catalog.json config/ATTACK-LICENSE.txt ./resources/
COPY run.py ./

USER 1000:1000
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=2).close()"]
CMD ["python", "run.py", "--config", "/app/config/service.json"]
