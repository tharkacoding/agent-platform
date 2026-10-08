# ---- stage 1: build the React/Vite web UI -------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY web/ ./
RUN npm run build

# ---- stage 2: gateway (FastAPI + LangGraph). Holds secrets; never executes generated code. ------------
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    AAP_DATA_DIR=/data AAP_WEB_DIST=/app/web/dist
RUN useradd --uid 1000 --create-home aos
WORKDIR /app
COPY requirements-gateway.txt .
RUN pip install -r requirements-gateway.txt
COPY aap/ aap/
COPY docs/ docs/
COPY --from=web /web/dist /app/web/dist
RUN mkdir -p /data /data/egress && chown -R aos:aos /data
USER aos
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/api/health',timeout=4).status==200 else 1)"
CMD ["uvicorn", "aap.api.app:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers"]
