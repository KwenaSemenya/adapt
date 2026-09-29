# ADAPT: one image, one process. SvelteKit is built to static files; FastAPI serves them and the API.

# ---- web build ----
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- runtime ----
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ADAPT_ROOT=/app \
    ADAPT_ENV=production \
    ADAPT_DB_PATH=/data/adapt.db
WORKDIR /app/api
COPY api/pyproject.toml api/uv.lock api/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY api/src ./src
RUN uv sync --frozen --no-dev
WORKDIR /app
COPY config ./config
COPY fixtures ./fixtures
COPY --from=web /web/build ./web/build
EXPOSE 8080
CMD ["/app/api/.venv/bin/uvicorn", "adapt.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
