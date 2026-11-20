"""Minimal RFC 6455 WebSocket client (stdlib only: socket + ssl).

Used by the live microstructure collector. No third-party dependency, so it runs the same on
the VPS, on CI and inside the Android app (Chaquopy). Client frames are masked; text, binary,
ping/pong and close are handled; fragmented messages are reassembled.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
from typing import Optional
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WSClosed(Exception):
    pass


def encode_frame(opcode: int, payload: bytes, mask_key: Optional[bytes] = None) -> bytes:
    mask_key = mask_key if mask_key is not None else os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", n)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return head + mask_key + masked


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self._buf = b""

    @classmethod
    def connect(cls, url: str, timeout: float = 15.0) -> "WebSocket":
        u = urlparse(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname) if u.scheme == "wss" else raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(req.encode())
        ws = cls(sock)
        head = ws._read_until(b"\r\n\r\n").decode("latin-1")
        status = head.split("\r\n", 1)[0]
        if " 101 " not in status:
            raise ConnectionError(f"handshake failed: {status}")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if accept.lower() not in head.lower():
            raise ConnectionError("handshake failed: bad Sec-WebSocket-Accept")
        return ws

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSClosed("connection closed during handshake")
            self._buf += chunk
        i = self._buf.index(marker) + len(marker)
        out, self._buf = self._buf[:i], self._buf[i:]
        return out

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise WSClosed("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text: str) -> None:
        self.sock.sendall(encode_frame(OP_TEXT, text.encode()))

    def _frame(self) -> tuple[bool, int, bytes]:
        b1, b2 = self._read(2)
        fin, op = bool(b1 & 0x80), b1 & 0x0F
        masked, n = bool(b2 & 0x80), b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read(8))[0]
        key = self._read(4) if masked else None
        data = self._read(n)
        if key:
            data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
        return fin, op, data

    def recv(self) -> str:
        """Next complete text/binary message (control frames are handled here)."""
        parts: list[bytes] = []
        while True:
            fin, op, data = self._frame()
            if op == OP_PING:
                self.sock.sendall(encode_frame(OP_PONG, data))
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                try:
                    self.sock.sendall(encode_frame(OP_CLOSE, data[:2]))
                finally:
                    raise WSClosed(f"server closed: {data[:2].hex()}")
            parts.append(data)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.sendall(encode_frame(OP_CLOSE, struct.pack("!H", 1000)))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
