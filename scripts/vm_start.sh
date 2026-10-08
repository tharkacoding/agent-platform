#!/usr/bin/env bash
# Start (or refresh) AgentOS inside the Linux test VM from the Windows source tree, then keep this WSL session open so
# the distro isn't shut down while idle. Called hidden by scripts/autostart.ps1:
#   wsl -d agentos-vm -u root -- bash /mnt/c/Users/raman/agent-platform/scripts/vm_start.sh
# Memory cap: %USERPROFILE%\.wslconfig memory=6GB. Nothing here ever shows on the owner's desktop.
set -u
SRC=/mnt/c/Users/raman/agent-platform
rsync -a --delete --exclude __pycache__ --exclude .ruff_cache --exclude node_modules --exclude '/data/' \
      --exclude '/build' --exclude '/dist' --exclude .git --exclude .pytest_cache "$SRC/" /opt/agentos/
sed -i 's/\r$//' /opt/agentos/scripts/*.sh
# one-time VM provisioning (no-ops once installed): agents expect `python` + pytest; the VM screen needs Xvfb/noVNC
command -v python >/dev/null && python3 -m pytest --version >/dev/null 2>&1 && command -v x11vnc >/dev/null || \
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python-is-python3 python3-pytest xvfb x11vnc novnc websockify \
    fluxbox xdotool >/dev/null 2>&1
bash /opt/agentos/scripts/vm_up.sh </dev/null
[ "${1:-}" = "--no-keep" ] || exec sleep infinity
