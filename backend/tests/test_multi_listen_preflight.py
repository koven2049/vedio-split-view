"""multi_listen port preflight: restart must survive TIME_WAIT, but a live
listener on the port must still be refused."""

from __future__ import annotations

import importlib.util
import os
import socket
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "multi_listen.py"


@pytest.fixture
def multi_listen(monkeypatch):
    # Module import chdirs to the service root; restore cwd afterwards.
    monkeypatch.chdir(os.getcwd())
    spec = importlib.util.spec_from_file_location("multi_listen_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _time_wait_port() -> int:
    """Leave a server-side TIME_WAIT socket on a port, like a just-stopped
    service whose clients were connected."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 0))
    port = srv.getsockname()[1]
    srv.listen()
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    conn.close()  # server closes first -> TIME_WAIT on the server port
    client.close()
    srv.close()
    return port


def test_preflight_passes_with_time_wait(multi_listen, monkeypatch):
    """Regression: `manage.sh restart` failed with "port 5180 is already in
    use" right after the old process exited."""
    monkeypatch.setattr(multi_listen, "PORTS", (_time_wait_port(),))
    multi_listen._preflight_ports()


def test_preflight_refuses_live_listener(multi_listen, monkeypatch):
    live = socket.socket()
    live.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    live.bind(("0.0.0.0", 0))
    live.listen()
    try:
        monkeypatch.setattr(multi_listen, "PORTS", (live.getsockname()[1],))
        with pytest.raises(SystemExit, match="already in use"):
            multi_listen._preflight_ports()
    finally:
        live.close()
