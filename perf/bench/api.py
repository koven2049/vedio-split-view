"""Server-side API latency: p50/p75/p95 per endpoint, A/B alternated.

Loopback, no throttling — isolates server time (DB read + serialization).
Usage: python api.py --a http://127.0.0.1:8301 [--b http://127.0.0.1:8302]
                     [--runs 40] [--out result.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request

ENDPOINTS = [
    "/api/videos?page_size=20",
    "/api/videos?page_size=100",
    "/api/videos/1",
    "/api/docs-data",
    # new in this round; 404 on baseline is recorded and skipped
    "/api/videos/ids",
    "/api/videos/1/meta",
]


def _login(origin: str) -> str:
    req = urllib.request.Request(
        f"{origin}/api/auth/login",
        data=json.dumps({"username": "admin", "password": "bench-only-pass"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))["access_token"]


def _hit(origin: str, path: str, token: str) -> tuple[float, int, int]:
    req = urllib.request.Request(f"{origin}{path}", headers={"Authorization": f"Bearer {token}"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        body, code = b"", e.code
    return (time.perf_counter() - t0) * 1000, len(body), code


def _pct(v: list[float], p: int) -> float:
    return round(statistics.quantiles(sorted(v), n=100, method="inclusive")[p - 1], 2)


def main() -> None:
    ap = argparse.ArgumentParser(description="API latency p50/p75/p95, A/B alternated")
    ap.add_argument("--a", required=True)
    ap.add_argument("--b")
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--out")
    args = ap.parse_args()
    origins = {"A": args.a, **({"B": args.b} if args.b else {})}
    tokens = {k: _login(o) for k, o in origins.items()}
    res: dict = {k: {p: {"ms": [], "bytes": 0, "code": 0} for p in ENDPOINTS} for k in origins}
    for k, o in origins.items():  # warm-up
        for p in ENDPOINTS:
            _hit(o, p, tokens[k])
    for i in range(args.runs):
        order = list(origins) if i % 2 == 0 else list(reversed(list(origins)))
        for p in ENDPOINTS:
            for k in order:
                ms, n, code = _hit(origins[k], p, tokens[k])
                cell = res[k][p]
                cell["ms"].append(ms)
                cell["bytes"], cell["code"] = n, code
    summary = {}
    for k in origins:
        summary[k] = {}
        for p in ENDPOINTS:
            c = res[k][p]
            if c["code"] != 200:
                summary[k][p] = {"code": c["code"]}
                continue
            summary[k][p] = {"p50": _pct(c["ms"], 50), "p75": _pct(c["ms"], 75),
                             "p95": _pct(c["ms"], 95), "bytes": c["bytes"]}
    for p in ENDPOINTS:
        row = "  ".join(
            f"{k}: " + (f"p50/p75/p95={s['p50']}/{s['p75']}/{s['p95']}ms {s['bytes']}B"
                        if "p75" in s else f"HTTP {s['code']}")
            for k, s in ((k, summary[k][p]) for k in origins)
        )
        print(f"{p:32s} {row}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"runs": args.runs, "origins": origins, "summary": summary}, fh, indent=1)


if __name__ == "__main__":
    main()
