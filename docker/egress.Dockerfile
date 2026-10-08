FROM alpine:3.20
RUN apk add --no-cache tinyproxy
COPY egress/tinyproxy.conf.tmpl /etc/tinyproxy/tinyproxy.conf.tmpl
COPY egress/entrypoint.sh /entrypoint.sh
# /etc/tinyproxy stays root-owned: the entrypoint (root, all caps dropped => no DAC_OVERRIDE) rewrites the filter.
# /config is the shared aos_egress volume. It is owned by uid 1000 so the gateway (uid 1000) can write
# allowlist.extra no matter which container creates the volume first.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh \
 && touch /etc/tinyproxy/filter && chmod 644 /etc/tinyproxy/filter \
 && mkdir -p /config && chown 1000:1000 /config
EXPOSE 3128
ENTRYPOINT ["/entrypoint.sh"]
