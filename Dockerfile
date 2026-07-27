# python:3.12-slim с Docker Hub, а не образ uv с ghcr.io — последний
# недоступен из части сетей, и сборка на них молча падает.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Слой зависимостей отдельно от кода: правки в src не пересобирают окружение.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN uv pip install --system --no-cache .

# Токены и кэш живут в томе, а не в образе.
RUN mkdir -p /data && useradd -r -u 1000 oura && chown -R oura /data /app
USER oura

ENV OURA_TOKEN_STORE=/data/tokens.json \
    OURA_CACHE_DB=/data/cache.db \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"

CMD ["oura-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
