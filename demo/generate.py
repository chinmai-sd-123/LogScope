#!/usr/bin/env python
"""Generate a realistic log stream for demoing LogScope.

Writes mixed-format lines (JSON, logfmt, plain text) simulating a web service:
steady baseline traffic punctuated by periodic "incidents" -- bursts of database
connection errors that make the cluster panel and error-rate sparkline light up.

Usage:
    python demo/generate.py demo.log              # stream forever (ctrl+c to stop)
    python demo/generate.py demo.log --once 800   # write 800 lines then exit
    python demo/generate.py demo.log --rate 40    # ~40 lines/second
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone

ENDPOINTS = ["/api/users", "/api/orders", "/api/cart", "/api/search", "/healthz"]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normal_line() -> str:
    r = random.random()
    if r < 0.45:  # JSON access log
        return json.dumps({
            "ts": _iso(),
            "level": "info",
            "msg": f"{random.choice(['GET', 'POST'])} {random.choice(ENDPOINTS)}/{random.randint(1, 9999)}",
            "status": random.choice([200, 200, 200, 201, 304]),
            "ms": random.randint(3, 80),
        })
    if r < 0.75:  # logfmt
        return f'level=debug msg="cache hit" key=user:{random.randint(1, 9999)} ms={random.randint(1, 5)}'
    if r < 0.92:  # plain
        return f"INFO {_iso()} request completed for session {random.randint(1000, 9999)}"
    return f"WARN slow query took {random.randint(200, 900)}ms on table orders"


def incident_line() -> str:
    db = random.randint(1, 9)
    rid = "".join(random.choices("abcdef0123456789", k=6))
    if random.random() < 0.5:
        return json.dumps({
            "ts": _iso(),
            "level": "error",
            "msg": f"Failed to connect to db-{db} after 3 retries",
            "request_id": rid,
        })
    return f"ERROR {_iso()} Failed to connect to db-{db} after 3 retries (request_id={rid})"


def stream(path: str, rate: float, once: int | None) -> None:
    interval = 1.0 / rate if rate > 0 else 0.0
    with open(path, "a", encoding="utf-8") as fh:
        if once is not None:
            # One-shot: ~15% of lines are an incident cluster, rest baseline.
            for i in range(once):
                line = incident_line() if (i % 7 == 0 and i > 30) else normal_line()
                fh.write(line + "\n")
            print(f"wrote {once} lines to {path}")
            return

        print(f"streaming to {path} at ~{rate}/s (ctrl+c to stop)")
        elapsed = 0.0
        while True:
            # Every ~30s, a ~5s incident window where errors dominate.
            in_incident = elapsed > 10 and (elapsed % 30) < 5
            line = incident_line() if (in_incident and random.random() < 0.9) else normal_line()
            fh.write(line + "\n")
            fh.flush()
            time.sleep(interval)
            elapsed += interval


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate demo logs for LogScope.")
    ap.add_argument("path", help="file to append log lines to")
    ap.add_argument("--rate", type=float, default=30.0, help="lines per second")
    ap.add_argument("--once", type=int, default=None, help="write N lines then exit")
    args = ap.parse_args()
    try:
        stream(args.path, args.rate, args.once)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
