#!/bin/sh
# Virtual display + window manager + VNC + noVNC, then the Playwright control API.
set -eu
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
sleep 1
fluxbox >/dev/null 2>&1 &

if [ -n "${VNC_PASSWORD:-}" ]; then
  mkdir -p "$HOME/.vnc" && x11vnc -storepasswd "$VNC_PASSWORD" "$HOME/.vnc/passwd" >/dev/null
  VNC_AUTH="-rfbauth $HOME/.vnc/passwd"
else
  VNC_AUTH="-nopw"
fi
# VNC listens on localhost only; noVNC (websockify) is the single entry point.
x11vnc -display :99 -forever -shared -localhost -rfbport 5900 $VNC_AUTH -quiet &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &

exec python -m uvicorn browser_svc.app:app --app-dir /app --host 0.0.0.0 --port 8002
