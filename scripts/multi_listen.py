"""Process-mode entrypoint: one event loop, two listeners, one app state.

Replaces the two-container (backend + nginx frontend) deployment:
- binds 0.0.0.0:5180 (SPA + API, the public entry) and 0.0.0.0:8080 (API
  compat listener — both ports were published in the container era);
- BOTH servers share one app instance, so the in-process TaskRunner, SSE
  subscribers and confirm/cancel events never split across processes;
- chdir to the service root first: storage/logging paths in app.yaml are
  CWD-relative, and a launchd plist typo must not silently create a fresh
  database somewhere else;
- refuse to start when the configured DB file is missing (FIRST_RUN=1
  opts in for a deliberate initial install);
- pre-flight both ports so a taken port fails fast and loudly.

Shutdown: uvicorn's ``capture_signals`` installs its own ``handle_exit``
as the SIGINT/SIGTERM handler, so instead of registering signal handlers
we override ``handle_exit`` on BOTH server instances — whichever handler
fires, both servers get ``should_exit = True`` and ``gather`` returns.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "backend" / "src"))

PORTS = tuple(
    int(p) for p in os.environ.get("VSLP_PORTS", "5180,8080").split(",") if p.strip()
)


def _preflight_ports() -> None:
    for port in PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Match uvicorn's bind: without SO_REUSEADDR, TIME_WAIT sockets left
        # by the previous process fail a restart; a live listener still fails.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError as e:
            raise SystemExit(f"port {port} is already in use — refusing to start: {e}")
        finally:
            sock.close()


def _check_db() -> None:
    from video_split.config import get_settings

    db = Path(get_settings().storage.db_path).resolve()
    if not db.exists():
        if os.environ.get("FIRST_RUN") == "1":
            print(f"[multi_listen] FIRST_RUN=1: creating fresh database at {db}", flush=True)
            return
        raise SystemExit(
            f"database not found at {db} — refusing to start. "
            "Wrong working directory? Set FIRST_RUN=1 explicitly for a fresh install."
        )
    print(f"[multi_listen] db ok: {db}", flush=True)


async def main() -> None:
    import uvicorn

    from video_split.main import app

    _preflight_ports()
    _check_db()

    # Run the app lifespan exactly ONCE ourselves: two Servers each running
    # their own lifespan races init_db's create_all on a fresh database
    # ("table users already exists"). lifespan="off" + manual async-with
    # gives both listeners one shared, orderly startup/shutdown.
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                app,
                host="0.0.0.0",
                port=port,
                log_level="info",
                lifespan="off",
                # Default 5s closes idle keep-alive sockets; the public entry
                # rides a cloudflared tunnel whose origin pool wants a longer
                # reuse window — fewer reconnects on a lossy cross-border link.
                timeout_keep_alive=75,
            )
        )
        for port in PORTS
    ]

    def _exit_both(sig, frame):  # type: ignore[no-untyped-def]
        for s in servers:
            s.should_exit = True

    for s in servers:
        s.handle_exit = _exit_both

    async with app.router.lifespan_context(app):
        await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit as e:
        print(f"[multi_listen] {e}", flush=True)
        raise
