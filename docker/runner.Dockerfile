# Runner: executes untrusted commands / code for agents. Non-root, no secrets, network only via egress proxy.
FROM node:22-bookworm-slim AS nodesrc

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HOME=/home/agent \
    RUNNER_ROOTS=/workspace:/shared:/mounts:/tmp \
    npm_config_cache=/tmp/.npm

# Node 22 (copied from the official image) + toolchain
COPY --from=nodesrc /usr/local/bin/node /usr/local/bin/node
COPY --from=nodesrc /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update && apt-get install -y --no-install-recommends \
      git ripgrep build-essential curl ca-certificates jq unzip procps \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-runner.txt /tmp/requirements-runner.txt
RUN pip install -r /tmp/requirements-runner.txt && rm /tmp/requirements-runner.txt

# git identity for agent commits; trust mounted repos owned by other uids
RUN git config --system user.name "AgentOS" \
 && git config --system user.email "agentos@localhost" \
 && git config --system safe.directory '*' \
 && git config --system init.defaultBranch main

RUN useradd --uid 1000 --create-home agent \
 && mkdir -p /workspace /shared /mounts \
 && chown -R agent:agent /workspace /home/agent
WORKDIR /app
COPY runner/ runner/
USER agent
WORKDIR /workspace
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=4).status==200 else 1)"
CMD ["python", "-m", "uvicorn", "runner.app:app", "--app-dir", "/app", "--host", "0.0.0.0", "--port", "8001"]
