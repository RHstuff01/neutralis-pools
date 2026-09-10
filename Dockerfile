FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEUTRALIS_POOLS_DATA_DIR=/data \
    STATIC_DIR=/opt/neutralis-pools/dist \
    PORT=8788

WORKDIR /opt/neutralis-pools
COPY app/ ./app/
COPY dist/ ./dist/

RUN mkdir -p /data && chown -R 1000:1000 /data /opt/neutralis-pools

USER 1000:1000
EXPOSE 8788
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=8s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/api/health', timeout=3).read()"]

CMD ["python", "app/server.py"]
