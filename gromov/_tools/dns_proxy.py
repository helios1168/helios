#!/usr/bin/env python3
"""Local HTTP CONNECT proxy that resolves names with nslookup.

This environment's getaddrinfo (mDNSResponder) is unreachable, so uv, pip and
huggingface_hub cannot resolve any hostname -- while raw egress and direct UDP
DNS both work fine. This proxy accepts CONNECT, resolves the host via nslookup,
opens a socket to the resulting IP, and blindly tunnels bytes. TLS remains
end-to-end between the client and the origin, so certificates validate normally
and no traffic is inspected.

    python3 dns_proxy.py 8899 &
    export HTTPS_PROXY=http://127.0.0.1:8899 HTTP_PROXY=http://127.0.0.1:8899
"""
import re
import select
import socket
import socketserver
import subprocess
import sys
import threading

_cache = {}
_lock = threading.Lock()


def resolve(host):
    if re.fullmatch(r"[0-9.]+", host):
        return host
    with _lock:
        if host in _cache:
            return _cache[host]
    out = subprocess.run(["nslookup", host], capture_output=True, text=True).stdout
    ips = re.findall(r"^Address:\s*([0-9.]+)$", out, re.M)
    ip = ips[0] if ips else None
    with _lock:
        _cache[host] = ip
    return ip


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(30)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                data += chunk
                if len(data) > 65536:
                    return
        except OSError:
            return

        line = data.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            sock.sendall(b"HTTP/1.1 501 Not Implemented\r\n\r\n")
            return

        hostport = parts[1]
        host, _, port = hostport.rpartition(":")
        try:
            port = int(port)
        except ValueError:
            host, port = hostport, 443
        ip = resolve(host)
        if not ip:
            sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return

        try:
            upstream = socket.create_connection((ip, port), timeout=30)
        except OSError:
            sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return

        sock.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        sock.settimeout(None)
        upstream.settimeout(None)
        try:
            while True:
                r, _, _ = select.select([sock, upstream], [], [], 300)
                if not r:
                    break
                for src in r:
                    dst = upstream if src is sock else sock
                    buf = src.recv(65536)
                    if not buf:
                        return
                    dst.sendall(buf)
        except OSError:
            pass
        finally:
            for s in (upstream, sock):
                try:
                    s.close()
                except OSError:
                    pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f"dns-proxy listening on 127.0.0.1:{port}", flush=True)
    Server(("127.0.0.1", port), Handler).serve_forever()
