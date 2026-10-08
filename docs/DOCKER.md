# AgentOS containers

| Service | Image | Networks | Published | Purpose |
|---|---|---|---|---|
| gateway | docker/gateway.Dockerfile | internal + edge | 127.0.0.1:7860 | API, web UI, LangGraph runtime, secrets |
| runner | docker/runner.Dockerfile | internal only | — | shell/python/node/git for agents (`runner/app.py`) |
| browser | docker/browser.Dockerfile | internal only | — | Playwright Chromium + Xvfb + noVNC (`browser_svc/app.py`) |
| egress | docker/egress.Dockerfile | internal + edge | — | tinyproxy domain allowlist for runner/browser |
| novnc-edge | alpine/socat | internal + edge | 127.0.0.1:6080 | relays noVNC (internal networks can't publish ports) |

## Isolation
- runner and browser have no route to the internet or the host. Their only way out is `http://egress:3128`, which denies every domain not on the allowlist.
- The allowlist is built from the defaults in `egress/entrypoint.sh`, plus `EGRESS_ALLOWLIST` (comma-separated), plus `/config/allowlist.extra`. The gateway writes that last file to its `aos_egress` volume at `/data/egress/allowlist.extra`, one domain per line. egress re-reads it within 10 s, so no restart is needed.
- Every container drops all capabilities and sets `no-new-privileges`, with mem/cpu/pids limits. gateway and runner use a read-only root filesystem plus tmpfs. egress adds back SETUID/SETGID so tinyproxy can drop to its own user.
- Host paths: only `${AOS_SHARED}` (default `C:/Users/raman/AgentOS/shared`) goes to runner `/shared`, plus explicit user mounts from `docker-compose.mounts.yml` (see `docker-compose.mounts.example.yml`).
- Chromium runs with `--no-sandbox` (Playwright's default in containers). The container is the sandbox.
- noVNC has no password unless `VNC_PASSWORD` is set. It's reachable only from 127.0.0.1.

## Dev / host mode (no Docker) — NOT a sandbox
`scripts/dev_up.ps1` runs the same services directly on Windows (runner :8001, browser_svc :8002, bridge :8765 with
Chrome + desktop, gateway :7860). The runner maps `/workspace`, `/shared`, `/tmp` to `%USERPROFILE%\AgentOS\{workspace,
shared,tmp}` (`RUNNER_PATH_MAP`) and uses Git Bash (else PowerShell). File tools stay jailed to those folders, but a
shell command can reach the whole machine, so the gateway switches to a strict policy (`config.RUNNER_ON_HOST`):
only read-only commands run without asking; everything else (python, installs, commits, redirection) needs approval,
and so does `run_python` unless it passes the pure-logic AST policy. Use Docker for real isolation.

## Volumes
`aos_data` (gateway /data), `aos_workspace` (runner /workspace), `aos_profiles` (browser /profiles), `aos_egress` (egress /config and gateway /data/egress).

## Commands
```
docker compose up -d --build
docker compose -f docker-compose.yml -f docker-compose.mounts.yml up -d   # with user mounts
docker compose logs -f egress     # see allowed/denied requests
```
