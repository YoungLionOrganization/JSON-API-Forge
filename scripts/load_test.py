"""Small HTTP load probe using runtime dependency httpx."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from collections import Counter

import httpx


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(int(round((len(values) - 1) * p)), len(values) - 1)]


async def run(args) -> None:
    sem = asyncio.Semaphore(args.concurrency)
    latencies = []
    statuses = Counter()
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    limits = httpx.Limits(max_connections=max(args.concurrency, 10), max_keepalive_connections=max(args.concurrency, 10))
    async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout), limits=limits, verify=not args.insecure) as client:

        async def one(_):
            async with sem:
                start = time.perf_counter()
                try:
                    statuses[(await client.request(args.method, args.url, headers=headers)).status_code] += 1
                except Exception as exc:
                    statuses[type(exc).__name__] += 1
                finally:
                    latencies.append((time.perf_counter() - start) * 1000)

        start = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(args.requests)))
        elapsed = time.perf_counter() - start
    print(f"requests={args.requests} concurrency={args.concurrency} elapsed={elapsed:.3f}s rps={args.requests / max(elapsed, 1e-9):.2f}")
    print("statuses=", dict(statuses))
    if latencies:
        print(
            f"latency_ms mean={statistics.fmean(latencies):.2f} p50={percentile(latencies, 0.50):.2f} p95={percentile(latencies, 0.95):.2f} p99={percentile(latencies, 0.99):.2f} max={max(latencies):.2f}"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--requests", type=int, default=1000)
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--method", default="GET")
    p.add_argument("--api-key")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--insecure", action="store_true")
    args = p.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        p.error("requests and concurrency must be >= 1")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
