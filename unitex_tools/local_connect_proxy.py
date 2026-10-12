"""Loopback-only HTTP CONNECT proxy for a temporary SSH reverse tunnel."""

from __future__ import annotations

import select
import socket
import socketserver


LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 18080


class ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        client.settimeout(10)
        header = b""
        while b"\r\n\r\n" not in header and len(header) < 8192:
            chunk = client.recv(4096)
            if not chunk:
                return
            header += chunk
        first_line = header.split(b"\r\n", 1)[0].decode("ascii", "replace")
        parts = first_line.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        try:
            host, port_text = parts[1].rsplit(":", 1)
            port = int(port_text)
            if not (1 <= port <= 65535):
                raise ValueError
            upstream = socket.create_connection((host, port), timeout=15)
        except Exception:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        with upstream:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            sockets = [client, upstream]
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 60)
                if exceptional or not readable:
                    if not readable:
                        continue
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = upstream if source is client else client
                    target.sendall(data)


class ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with ThreadingServer((LISTEN_HOST, LISTEN_PORT), ConnectHandler) as server:
        print(f"Listening on {LISTEN_HOST}:{LISTEN_PORT}", flush=True)
        server.serve_forever()
