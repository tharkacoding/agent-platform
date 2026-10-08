#!/bin/sh
# Builds the tinyproxy host filter from the allowlist, starts tinyproxy in the foreground (logs -> stdout),
# and reloads the filter (SIGUSR1) whenever /config/allowlist.extra changes.
set -eu

DEFAULT_ALLOW="pypi.org,files.pythonhosted.org,registry.npmjs.org,registry.yarnpkg.com,github.com,api.github.com,codeload.github.com,objects.githubusercontent.com,raw.githubusercontent.com,duckduckgo.com,html.duckduckgo.com,lite.duckduckgo.com,wikipedia.org,deb.debian.org"
EXTRA_FILE=/config/allowlist.extra
FILTER=/etc/tinyproxy/filter

build_filter() {
  tmp="$(mktemp)"
  { echo "${DEFAULT_ALLOW},${EGRESS_ALLOWLIST:-}" | tr ',' '\n'; [ -f "$EXTRA_FILE" ] && cat "$EXTRA_FILE"; } \
    | tr -d ' \r' | grep -v '^#' | grep -v '^$' | sort -u | while read -r d; do
      if [ "$d" = "*" ]; then echo '.*'; continue; fi
      # domain and all its subdomains, anchored: (^|\.)example\.com$
      esc="$(printf '%s' "$d" | sed 's/^\*\.//; s/[.]/\\./g')"
      echo "(^|\\.)${esc}\$"
    done > "$tmp"
  mv "$tmp" "$FILTER"
  chmod 644 "$FILTER"
  echo "[egress] allowlist: $(wc -l < "$FILTER") pattern(s)"
}

mkdir -p /config
build_filter
cp /etc/tinyproxy/tinyproxy.conf.tmpl /etc/tinyproxy/tinyproxy.conf

tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf &
PID=$!

last="$(cat "$EXTRA_FILE" 2>/dev/null | md5sum)"
while kill -0 "$PID" 2>/dev/null; do
  sleep 10
  now="$(cat "$EXTRA_FILE" 2>/dev/null | md5sum)"
  if [ "$now" != "$last" ]; then
    last="$now"
    build_filter
    kill -USR1 "$PID" 2>/dev/null || true
    echo "[egress] filter reloaded"
  fi
done
wait "$PID"
