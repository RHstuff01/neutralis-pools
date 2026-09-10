FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEUTRALIS_POOLS_DATA_DIR=/data \
    STATIC_DIR=/opt/neutralis-pools/dist \
    PORT=8788

WORKDIR /opt/neutralis-pools
COPY app/ ./app/
COPY dist/ ./dist/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 neutralis \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin neutralis \
    && mkdir -p /data \
    && chown -R neutralis:neutralis /data /opt/neutralis-pools \
    && chmod 755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8788
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=8s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/api/health', timeout=3).read()"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "app/server.py"]
