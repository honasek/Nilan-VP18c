"""Transparent TCP byte stream, with a deadline for the entire CTS frame.

There is deliberately no Modbus TCP conversion or MBAP header here.
An incomplete or failed transaction closes the socket before reconnecting.
"""
from __future__ import annotations
import socket
import time
from contextlib import contextmanager


class TransparentTCP:
    def __init__(self, host: str, port: int, timeout: float = 3.0):
        self.host, self.port, self.timeout = host, int(port), timeout
        self.socket = None
        self.deadline = None
        self.operation_deadline = None

    def connect(self):
        if self.socket is None:
            self.socket = socket.create_connection((self.host, self.port), self.timeout)
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return True

    def close(self):
        if self.socket is not None:
            self.socket.close()
        self.socket = None
        self.deadline = None

    @contextmanager
    def operation(self, seconds=25):
        """Bound menu traversal, without cancelling an executor mid-write."""
        self.operation_deadline = time.monotonic() + seconds
        try:
            yield
        finally:
            self.operation_deadline = None

    def _timeout(self):
        deadlines = [d for d in (self.deadline, self.operation_deadline) if d is not None]
        remaining = min(deadlines) - time.monotonic() if deadlines else self.timeout
        if remaining <= 0:
            raise TimeoutError("CTS600 operation deadline exceeded")
        self.socket.settimeout(min(self.timeout, remaining))

    def send(self, data):
        self.deadline = time.monotonic() + self.timeout
        try:
            self.connect()
            self._timeout()
            self.socket.sendall(data)
            return len(data)
        except (OSError, TimeoutError):
            self.close()
            raise

    def recv(self, size):
        """Read exactly N bytes even when TCP splits a response."""
        if size == 0:
            return b""
        if size < 0 or size > 2048 or self.socket is None:
            raise OSError("Invalid CTS600 receive request or disconnected socket")
        data = bytearray()
        try:
            while len(data) < size:
                self._timeout()
                chunk = self.socket.recv(size - len(data))
                if not chunk:
                    raise ConnectionError("CTS600 TCP peer disconnected")
                data.extend(chunk)
            return bytes(data)
        except (OSError, TimeoutError):
            self.close()
            raise
