FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANQUANDAY_WEB_HOST=0.0.0.0 \
    ANQUANDAY_WEB_PORT=8766
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --no-create-home app

COPY web/ ./web/
COPY config/ ./config/
COPY frontend/index.html frontend/app.js frontend/styles.css ./frontend/
COPY run.py ./

USER 1000:1000
EXPOSE 8766
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/healthz', timeout=2).close()"]
CMD ["python", "run.py", "--config", "/app/config/web.json"]
