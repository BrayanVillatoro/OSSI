"""Keeps one copy of OSSI running, and lets a new launch bring its window back.

The running copy listens on a local-only port, which it saves next to the database.
"""

import socket

from ossi import storage

REQUEST = b"ossi-show"
REPLY = b"ossi-ok"


def port_file():
    return storage.default_path().with_name("instance.port")


def signal_running(show=True):
    """Return True if OSSI is already running, asking it to show its window if wanted."""
    try:
        port = int(port_file().read_text())
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(REQUEST if show else b"ossi-ping")
            return connection.recv(16) == REPLY
    except (OSError, ValueError):
        return False


class Listener:
    def __init__(self):
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
                request = connection.recv(16)
                if request.startswith(b"ossi-"):
                    connection.sendall(REPLY)
                show = show or request == REQUEST
            except OSError:
                pass
            finally:
                connection.close()

    def close(self):
        self.server.close()
        port_file().unlink(missing_ok=True)
