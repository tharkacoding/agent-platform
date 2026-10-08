"""HTTP/WebSocket-aware TCP forwarder 0.0.0.0:9223 -> 127.0.0.1:9222 for Chrome DevTools.

Chrome binds its debugging port to 127.0.0.1 and rejects requests whose Host header is a hostname such as
`host.docker.internal`. This forwarder
  * only accepts clients from loopback and the WSL/Docker virtual-switch subnets,
  * rewrites the Host header to `localhost:9222`,
  * for plain HTTP (/json/version, /json/list) rewrites `ws://localhost:9222/...` in the body to the address the
    client used, fixing Content-Length, so Playwright's connect_over_cdp follows the forwarder,
  * for WebSocket upgrades rewrites Host and then pipes bytes both ways.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import threading

log = logging.getLogger("bridge.forwarder")

# Docker Desktop proxies container -> host.docker.internal traffic so it arrives from loopback; 192.168.65.0/24 is
# Docker Desktop's VM network. vEthernet subnets are discovered at runtime. AGENTOS_BRIDGE_ALLOW adds more.
_DEFAULT_NETS = ["127.0.0.0/8", "192.168.65.0/24"]


def virtual_switch_networks() -> list[ipaddress.IPv4Network]:
    """vEthernet (WSL / Default Switch) subnets from Get-NetIPAddress; falls back to the private ranges WSL uses."""
    nets = [ipaddress.ip_network(n) for n in _DEFAULT_NETS]
    extra = os.environ.get("AGENTOS_BRIDGE_ALLOW", "")
    nets += [ipaddress.ip_network(n.strip(), strict=False) for n in extra.split(",") if n.strip()]
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetIPAddress -AddressFamily IPv4 | Where-Object InterfaceAlias -like 'vEthernet*' | "
                 "Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15).stdout.strip()
            if out:
                data = json.loads(out)
                for row in data if isinstance(data, list) else [data]:
                    nets.append(ipaddress.ip_network(f"{row['IPAddress']}/{row['PrefixLength']}", strict=False))
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass
    return nets


def client_allowed(ip: str, nets: list[ipaddress.IPv4Network]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


_HOST_RE = re.compile(rb"^Host:[^\r\n]*\r\n", re.I | re.M)


def rewrite_request_head(head: bytes, upstream: str) -> tuple[bytes, str]:
    """Replace the Host header; return (new_head, original_host)."""
    m = re.search(rb"^Host:\s*([^\r\n]*)\r\n", head, re.I | re.M)
    original = m.group(1).decode("latin-1").strip() if m else ""
    new = _HOST_RE.sub(f"Host: {upstream}\r\n".encode(), head, count=1) if m else \
        head.replace(b"\r\n", f"\r\nHost: {upstream}\r\n".encode(), 1)
    return new, original


def rewrite_response(resp: bytes, upstream: str, public: str) -> bytes:
    """Point ws:// / devtools URLs at the public forwarder address and fix Content-Length."""
    sep = resp.find(b"\r\n\r\n")
    if sep < 0 or not public:
        return resp
    head, body = resp[:sep], resp[sep + 4:]
    for src in (upstream, upstream.replace("localhost", "127.0.0.1")):
        body = body.replace(src.encode(), public.encode())
    head = re.sub(rb"(?im)^Content-Length:\s*\d+", f"Content-Length: {len(body)}".encode(), head)
    return head + b"\r\n\r\n" + body


class CdpForwarder:
    def __init__(self, listen_port: int = 9223, target_port: int = 9222, listen_host: str = "0.0.0.0") -> None:
        self.listen = (listen_host, listen_port)
        self.target = ("127.0.0.1", target_port)
        self.upstream_host = f"localhost:{target_port}"
        self.nets = virtual_switch_networks()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> "CdpForwarder":
        if self._sock:
            return self
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(self.listen)
        s.listen(16)
        self._sock = s
        threading.Thread(target=self._accept_loop, daemon=True, name="cdp-forwarder").start()
        log.info("CDP forwarder %s:%s -> %s:%s", *self.listen, *self.target)
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
            self._sock = None

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self._sock:
            try:
                client, (ip, _) = self._sock.accept()
            except OSError:
                return
            if not client_allowed(ip, self.nets):
                log.warning("refused CDP client %s", ip)
                client.close()
                continue
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        try:
            head = b""
            client.settimeout(15)
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    client.close()
                    return
                head += chunk
            new_head, public_host = rewrite_request_head(head, self.upstream_host)
            upstream = socket.create_connection(self.target, timeout=15)
            if b"upgrade: websocket" in head.lower():
                upstream.sendall(new_head)
                client.settimeout(None)
                upstream.settimeout(None)
                self._pipe(client, upstream)
                return
            # plain HTTP: one request per connection, rewrite the response body
            if b"connection:" in new_head.lower():
                new_head = re.sub(rb"(?im)^Connection:[^\r\n]*", b"Connection: close", new_head)
            else:
                new_head = new_head.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n", 1)
            upstream.sendall(new_head)
            resp = b""
            while True:
                chunk = upstream.recv(65536)
                if not chunk:
                    break
                resp += chunk
            upstream.close()
            client.sendall(rewrite_response(resp, self.upstream_host, public_host))
            client.close()
        except OSError as e:
            log.debug("forwarder connection error: %s", e)
            try:
                client.close()
            except OSError:
                pass

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        def pump(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        t = threading.Thread(target=pump, args=(b, a), daemon=True)
        t.start()
        pump(a, b)
        t.join(timeout=1)
        a.close()
        b.close()
