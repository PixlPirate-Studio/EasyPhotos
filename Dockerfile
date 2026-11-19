# ---------------------------------------------------------------------------
# EasyPhotos: one lightweight container. No separate database or AI containers.
# ---------------------------------------------------------------------------

# Offline place names (GeoNames, CC BY 4.0)
FROM python:3.13-slim AS geo
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir numpy==2.5.3
WORKDIR /build
COPY tools/build_geo.py .
RUN curl -fsSL --retry 5 -o cities1000.zip https://download.geonames.org/export/dump/cities1000.zip \
    && unzip -q cities1000.zip \
    && curl -fsSL --retry 5 -o admin1.txt https://download.geonames.org/export/dump/admin1CodesASCII.txt \
    && curl -fsSL --retry 5 -o countryInfo.txt https://download.geonames.org/export/dump/countryInfo.txt \
    && mkdir -p /out && python build_geo.py cities1000.txt admin1.txt countryInfo.txt /out/geo.npz

# Face AI models (InsightFace buffalo_l, the same ones Immich uses)
FROM debian:bookworm-slim AS models
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /models/detection /models/recognition \
    && ( curl -fL --retry 5 -o /models/detection/model.onnx https://huggingface.co/immich-app/buffalo_l/resolve/main/detection/model.onnx \
      && curl -fL --retry 5 -o /models/recognition/model.onnx https://huggingface.co/immich-app/buffalo_l/resolve/main/recognition/model.onnx ) \
    || ( curl -fL --retry 5 -o /tmp/buffalo_l.zip https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip \
      && unzip -j /tmp/buffalo_l.zip det_10g.onnx w600k_r50.onnx -d /tmp/b \
      && mv /tmp/b/det_10g.onnx /models/detection/model.onnx \
      && mv /tmp/b/w600k_r50.onnx /models/recognition/model.onnx ) \
    && ls -la /models/*/

# The app
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EP_CONFIG_DIR=/config \
    EP_THUMBS_DIR=/thumbs \
    EP_LIBRARIES_ROOT=/libraries \
    EP_MODELS_DIR=/opt/easyphotos/models \
    EP_GEO_FILE=/opt/easyphotos/geo/geo.npz \
    EP_PORT=8080 \
    PUID=99 \
    PGID=100 \
    UMASK=002
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata util-linux \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=models /models /opt/easyphotos/models
COPY --from=geo /out/geo.npz /opt/easyphotos/geo/geo.npz
COPY easyphotos /app/easyphotos
COPY web /app/web
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /config /thumbs /libraries
ARG VERSION=dev
LABEL org.opencontainers.image.title="EasyPhotos" \
      org.opencontainers.image.description="Lightweight self-hosted photo gallery with face recognition" \
      org.opencontainers.image.source="https://github.com/PixlPirate-Studio/EasyPhotos" \
      org.opencontainers.image.version="${VERSION}"
EXPOSE 8080
VOLUME ["/config"]
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=5)" || exit 1
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "easyphotos.main"]
