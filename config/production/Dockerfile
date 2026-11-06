FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home arcsync
COPY src ./src
COPY scripts ./scripts
COPY data/evaluations ./data/evaluations
COPY docs ./docs
COPY docs-archive ./docs-archive
RUN mkdir -p /state && chown -R arcsync:arcsync /state /app/data
ENV PYTHONPATH=/app/src ARCSYNC_ENV=production ARCSYNC_DB=/state/business.sqlite3 ARCSYNC_CHAT_DB=/state/chat.sqlite3
USER arcsync
CMD ["uvicorn", "arcsync.main:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "172.30.0.2,127.0.0.1", "--no-access-log"]
