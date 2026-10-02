#!/usr/bin/env python3
"""Fail CI when security, data-integrity, lifecycle, or assembly coverage regresses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MODULE_FLOORS: dict[str, float] = {
    "framework/audit.py": 80.0,
    "framework/cache.py": 80.0,
    "framework/crud.py": 80.0,
    "framework/datasources.py": 80.0,
    "framework/db.py": 80.0,
    "framework/events.py": 80.0,
    "framework/factory.py": 80.0,
    "framework/media.py": 80.0,
    "framework/mongo.py": 80.0,
    "framework/observability.py": 80.0,
    "framework/operations.py": 80.0,
    "framework/protection.py": 80.0,
    "framework/rate_limit.py": 80.0,
    "framework/routers/project.py": 75.0,
    "framework/runtime.py": 80.0,
    "framework/reload.py": 80.0,
    "framework/security.py": 80.0,
    "framework/services/http_client.py": 80.0,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", nargs="?", default="coverage.json")
    parser.add_argument("--minimum", type=float, default=None)
    args = parser.parse_args()
    payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
    files = payload.get("files", {})
    failures = []
    print("Critical module branch-aware coverage gates:")
    for module, configured_floor in MODULE_FLOORS.items():
        floor = args.minimum if args.minimum is not None else configured_floor
        info = files.get(module)
        if info is None:
            failures.append(f"{module}: missing from coverage report")
            continue
        percent = float(info.get("summary", {}).get("percent_covered", 0.0))
        print(f"  {module:<42} {percent:6.2f}%  required>={floor:5.1f}%")
        if percent + 1e-9 < floor:
            failures.append(f"{module}: {percent:.2f}% < {floor:.2f}%")
    if failures:
        print("\nCoverage gate failed:")
        [print(f"  - {x}") for x in failures]
        return 1
    print("Critical coverage gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
