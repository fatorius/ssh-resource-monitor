FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor ./monitor

ENV MONITOR_DB=/data/monitor.db \
    MONITOR_HOST=0.0.0.0 \
    MONITOR_PORT=8787 \
    MONITOR_INTERVAL=10

VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD \
    python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('MONITOR_PORT','8787') + '/api/health', timeout=4)" || exit 1

CMD ["python", "-m", "monitor"]
