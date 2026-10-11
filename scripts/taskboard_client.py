#!/usr/bin/env python3
"""Use the v0.5.2 async SDK to browse or seed a TaskBoard installation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from json_api_forge import AsyncForgeClient, ForgeError


async def fetch_tasks(
    client: AsyncForgeClient, *, status: str | None = None, max_items: int = 100
) -> list[dict[str, Any]]:
    return [
        item
        async for item in client.iter_items(
            "task-board",
            "tasks-feed",
            pagination="cursor",
            params={"status": status} if status else None,
            page_size=min(50, max_items),
            max_items=max_items,
        )
    ]


async def seed_tasks(client: AsyncForgeClient) -> None:
    # The server commits this heterogeneous batch atomically. Do not retry it.
    await client.batch_create_items(
        "task-board",
        "tasks",
        [
            {"title": "Plan v0.5.2"},
            {"title": "Review v0.5.2", "priority": 3, "due_at": "2026-10-15T12:00:00Z"},
        ],
    )


async def run(args: argparse.Namespace, key: str) -> list[dict[str, Any]]:
    async with AsyncForgeClient(
        args.url, api_key=key, allow_insecure_http=args.local_http
    ) as client:
        if args.seed:
            await seed_tasks(client)
        return await fetch_tasks(client, status=args.status, max_items=args.limit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--local-http",
        action="store_true",
        help="Allow HTTP on loopback development servers",
    )
    parser.add_argument("--status", choices=["todo", "doing", "blocked", "done"])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Create two example tasks once (not idempotent)",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 10_000:
        parser.error("--limit must be between 1 and 10000")
    key = os.environ.get("FORGE_API_KEY", "")
    if not key:
        parser.error("Set FORGE_API_KEY to a provisioned task_user API key")
    try:
        tasks = asyncio.run(asyncio.wait_for(run(args, key), timeout=60))
    except (ForgeError, ValueError, asyncio.TimeoutError) as exc:
        print(f"TaskBoard request failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(tasks, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
