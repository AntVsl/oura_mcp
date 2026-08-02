# python:3.12-slim с Docker Hub, а не образ uv с ghcr.io — последний
# недоступен из части сетей, и сборка на них молча падает.
FROM python:3.12.12-slim

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# Слой зависимостей отдельно от кода: правки в src не пересобирают окружение.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

# Токены и кэш живут в томе, а не в образе.
# Без -r: это не системная учётка, а обычная. С -r useradd ругается, что uid
# 1000 выше SYS_UID_MAX, и берёт другой — а нам нужен предсказуемый владелец тома.
RUN mkdir -p /data && useradd -u 1000 -M -s /usr/sbin/nologin oura \
    && chown -R oura /data /app
USER oura

# docker build предупреждает про SecretsUsedInArgOrEnv из-за слова TOKEN в имени.
# Это ложное срабатывание: здесь путь к файлу, а не секрет. Сами токены лежат
# в томе /data и в образ не попадают.
ENV OURA_TOKEN_STORE=/data/tokens.json \
    OURA_CACHE_DB=/data/cache.db \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"

CMD ["my-oura-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
