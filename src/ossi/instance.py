"""Keeps one copy of OSSI running, and lets a new launch bring its window back.

The running copy listens on a local-only port, which it saves next to the database. A launch
says how new its code is, so that updated code replaces a copy that's been running since
before the update, instead of just reopening the old window.
"""

import socket
import time
from pathlib import Path

from ossi import storage

SHOW = b"ossi-show"
PING = b"ossi-ping"
REPLY = b"ossi-ok"
REPLACING = b"ossi-replacing"
WAIT_FOR_OLD_COPY_SECONDS = 5


def code_stamp():
    """When OSSI's code last changed, in whole seconds."""
    return max(int(path.stat().st_mtime) for path in Path(__file__).parent.rglob("*.py"))


# Taken once as OSSI starts, so a running copy can tell that newer code has arrived since.
THIS_CODE = code_stamp()


def port_file():
    return storage.default_path().with_name("instance.port")


def signal_running(show=True):
    """Return True if OSSI is already running, asking it to show its window if wanted.

    A copy running older code closes itself, which also returns False, so this launch can
    start in its place.
    """
    try:
        port = int(port_file().read_text())
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall((SHOW if show else PING) + b" %d" % THIS_CODE)
            reply = connection.recv(32)
    except (OSError, ValueError):
        return False
    if reply == REPLACING:
        return not wait_for_old_copy()
    return reply == REPLY


def wait_for_old_copy():
    """Wait for the old copy to finish closing, which removes its port file."""
    deadline = time.monotonic() + WAIT_FOR_OLD_COPY_SECONDS
    while time.monotonic() < deadline:
        if not port_file().exists():
            return True
        time.sleep(0.05)
    return False


class Listener:
    def __init__(self):
        self.replaced = False  # A launch with newer code wants this copy to close.
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.server.setblocking(False)
        path = port_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(self.server.getsockname()[1]))

    def poll(self):
        """Answer waiting launches. Return True if one asked to show the window."""
        show = False
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return show
            try:
                connection.settimeout(0.2)
                show = self.answer(connection) or show
            except OSError:
                pass
            finally:
                connection.close()

    def answer(self, connection):
        parts = connection.recv(64).split()
        if not parts or parts[0] not in (SHOW, PING):
            return False
        if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > THIS_CODE:
            self.replaced = True
            connection.sendall(REPLACING)
            return False
        connection.sendall(REPLY)
        return parts[0] == SHOW

    def close(self):
        self.server.close()
        port_file().unlink(missing_ok=True)
