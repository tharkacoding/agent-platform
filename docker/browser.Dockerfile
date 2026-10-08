# Browser: Playwright Chromium on a virtual X display, viewable/controllable through noVNC.
# Base image tag MUST match the playwright version pinned in requirements-browser.txt.
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DISPLAY=:99 PROFILES_DIR=/profiles BROWSER_HEADLESS=false \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11vnc fluxbox novnc websockify fonts-noto-color-emoji fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-browser.txt /tmp/requirements-browser.txt
RUN pip install -r /tmp/requirements-browser.txt && rm /tmp/requirements-browser.txt

WORKDIR /app
COPY browser_svc/ browser_svc/
COPY docker/browser-start.sh /usr/local/bin/browser-start.sh
RUN sed -i 's/\r$//' /usr/local/bin/browser-start.sh && chmod +x /usr/local/bin/browser-start.sh \
 && mkdir -p /profiles && chown -R pwuser:pwuser /profiles /app

USER pwuser
EXPOSE 8002 6080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8002/health',timeout=4).status==200 else 1)"
CMD ["/usr/local/bin/browser-start.sh"]
