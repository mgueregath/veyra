# Veyra (API + interfaz). La GPU no se usa aquí: los trabajos pesados van a los workers (repo veyra-worker).
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# editable: la API busca la interfaz compilada en <raíz>/frontend/dist relativa a su propio código
COPY backend ./backend
RUN pip install -e ./backend
COPY plugins ./plugins
COPY --from=web /web/dist ./frontend/dist
COPY deploy/entrypoint.sh /entrypoint.sh
# el mismo uid/gid que es dueño de la carpeta compartida (NFS), para poder escribir en ella
ARG UID=1000
ARG GID=3000
RUN groupadd -g ${GID} veyra && useradd -m -u ${UID} -g ${GID} veyra && chmod +x /entrypoint.sh
USER veyra
EXPOSE 8899
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8899/api/health',timeout=4)"
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
