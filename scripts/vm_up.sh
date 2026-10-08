#!/usr/bin/env bash
# AgentOS inside the Linux test VM (WSL2 distro "agentos-vm", 6 GB RAM; owner request 2026-10-06).
# Separate ports + data from the Windows copy; its own headless Chromium; NO desktop bridge (it never touches the
# owner's screen); phone listener OFF (the Windows copy owns the phone topic).
#   gateway http://localhost:7870   runner :8011   browser :8012
set -u
APP=/opt/agentos
DATA=/opt/agentos-data
VENV=/opt/agentos-venv
LOGS=$DATA/logs
mkdir -p "$LOGS" "$DATA/workspace" "$DATA/shared" "$DATA/tmp" "$DATA/browser_profiles"
cd "$APP"
export PYTHONIOENCODING=utf-8 AAP_DATA_DIR=$DATA
PY=$VENV/bin/python

pkill -f "uvicorn (aap.api.app|runner.app|browser_svc.app)" 2>/dev/null; sleep 1

# The VM's own screen: a virtual display :99 that never appears on the owner's desktop (WSLg's DISPLAY/WAYLAND are
# dropped). Watch it only when wanted: http://localhost:6081/vnc.html (noVNC -> x11vnc, both localhost-only).
unset WAYLAND_DISPLAY PULSE_SERVER
export DISPLAY=:99
if ! pgrep -f "Xvfb :99" >/dev/null; then
  nohup Xvfb :99 -screen 0 1366x850x24 -nolisten tcp >"$LOGS/xvfb.log" 2>&1 &
  sleep 1
  nohup fluxbox >"$LOGS/fluxbox.log" 2>&1 &
fi
pgrep -x x11vnc >/dev/null || nohup x11vnc -display :99 -localhost -rfbport 5901 -forever -shared -nopw -quiet \
  >"$LOGS/x11vnc.log" 2>&1 &
pgrep -f "websockify.*6081" >/dev/null || nohup websockify --web /usr/share/novnc 127.0.0.1:6081 127.0.0.1:5901 \
  >"$LOGS/novnc.log" 2>&1 &

$PY - <<'EOF'
from aap import settings
settings.update({"browser_default_mode": "sandbox", "ntfy_inbound": False, "notify_enabled": False,
                 "laptop_access": "off",
                 "vm_browser_url": "http://127.0.0.1:8012"})   # its own browser IS the VM browser (Browser page login card)
print("vm settings ok")
EOF

RUNNER_ROOTS="$DATA/workspace;$DATA/shared;$DATA/tmp" \
RUNNER_PATH_MAP="/workspace=$DATA/workspace;/shared=$DATA/shared;/tmp=$DATA/tmp" \
  nohup $PY -m uvicorn runner.app:app --host 127.0.0.1 --port 8011 >"$LOGS/runner.log" 2>&1 &
PROFILES_DIR="$DATA/browser_profiles" BROWSER_HEADLESS=false \
  nohup $PY -m uvicorn browser_svc.app:app --host 127.0.0.1 --port 8012 >"$LOGS/browser.log" 2>&1 &
# AAP_SELF_URL / AOS_*: the built-in capability suite (aap/selftest.py) tests THIS gateway; no Windows desktop here
AAP_SELF_URL=http://127.0.0.1:7870 AOS_RUNNER=http://127.0.0.1:8011 AOS_DESKTOP=0 \
AAP_VNC_PUBLIC_URL="http://localhost:6081/vnc.html?autoconnect=1&resize=scale" \
AAP_BROWSER_URL=http://127.0.0.1:8012 AAP_RUNNER_URL=http://127.0.0.1:8011 AAP_BRIDGE_URL=http://127.0.0.1:1 \
  nohup $PY -m uvicorn aap.api.app:app --host 127.0.0.1 --port 7870 >"$LOGS/gateway.log" 2>&1 &

for i in $(seq 1 90); do
  curl -s -o /dev/null http://127.0.0.1:7870/api/health && {
    echo "AgentOS VM up: http://localhost:7870   VM screen: http://localhost:6081/vnc.html"; exit 0; }
  sleep 1
done
echo "gateway did not start — see $LOGS/gateway.log"; exit 1
