"""Authenticated wrapper around huashu-flash bench.py (skill script unchanged).

The SPA keeps its JWT in localStorage (vsplit_token/...), so each cold
browser context gets a storage_state carrying a fresh admin login for every
origin under test. Everything else (throttling, ready polling, A/B
alternation, output JSON) is bench.py verbatim.

Usage: python run.py <bench.py args...>   (same flags as bench.py)
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path.home() / ".claude/skills/huashu-flash/scripts"))
import bench  # noqa: E402

_PASSWORD = "bench-only-pass"  # matches perf/bench/env.sh; throwaway DB only


def _login(origin: str) -> dict:
    req = urllib.request.Request(
        f"{origin}/api/auth/login",
        data=json.dumps({"username": "admin", "password": _PASSWORD}).encode(),
        headers={"Content-Type": "application/json"},
    )
    body = json.load(urllib.request.urlopen(req))
    items = {
        "vsplit_token": body["access_token"],
        "vsplit_username": "admin",
        "vsplit_role": "admin",
        "vsplit_lang": "zh",
    }
    return {"origin": origin, "localStorage": [{"name": k, "value": v} for k, v in items.items()]}


def _origins(argv: list[str]) -> list[str]:
    out = []
    for flag in ("--url", "--url-b"):
        if flag in argv:
            u = urlsplit(argv[argv.index(flag) + 1])
            out.append(f"{u.scheme}://{u.netloc}")
    return out


class _AuthBrowser:
    def __init__(self, browser, state):
        self._b, self._state = browser, state

    async def new_context(self, **kw):
        return await self._b.new_context(storage_state=self._state, **kw)

    def __getattr__(self, name):
        return getattr(self._b, name)


def main() -> None:
    anon = "--anon" in sys.argv
    if anon:
        sys.argv.remove("--anon")
    state = {"cookies": [], "origins": [] if anon else [_login(o) for o in _origins(sys.argv)]}
    orig = bench.measure_once

    async def measure_once(browser, url, args, ready_expr):
        return await orig(_AuthBrowser(browser, state), url, args, ready_expr)

    bench.measure_once = measure_once
    asyncio.run(bench.main())


if __name__ == "__main__":
    main()
