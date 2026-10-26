"""Looking at a port of this machine: whether something answers on it, and whether it is free."""

from __future__ import annotations

import http.client
import socket
import sys

__all__ = ["is_ready", "port_free"]

LOOPBACK = "127.0.0.1"


def is_ready(port: int, path: str) -> bool:
    """Whether the service on loopback answers its health path with 200.

    The request goes straight to loopback, so a proxy of the enterprise cannot answer it.
    """
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=1)
    try:
        connection.request("GET", path)
        with connection.getresponse() as response:
            # Finish the exchange. Closing with unread bytes can reset the peer on Windows.
            response.read()
            return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def port_free(port: int) -> bool:
    """Whether nothing listens on loopback at this port, so a service can take it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if sys.platform == "win32":
            # Without this, Windows lets a second program share a port that is in use.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((LOOPBACK, port))
        except OSError:
            return False
    return True
