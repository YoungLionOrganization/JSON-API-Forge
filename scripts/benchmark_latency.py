#!/usr/bin/env python3
"""Controlled local latency probes; no live HTTP, DNS or hosting dependency.

Compare a checkout with a previous source export using --source-root. Timings
are observations, not CI thresholds or production latency promises.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import socket
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace


def milliseconds(start):
    return round((time.perf_counter() - start) * 1000, 3)


def summary(values):
    ordered = sorted(values)
    return {
        "samples": len(values),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "p99_ms": ordered[math.ceil(0.99 * len(ordered)) - 1],
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("repeats must be between 1 and 20")
    sys.path.insert(0, str(args.source_root.resolve()))
    start = time.perf_counter()
    import httpx

    from framework.config import DataSourceConfig, ForgeConfig, ProjectConfig
    from framework.datasources import DataSourceManager
    from framework.factory import create_app
    from framework.reload import ReloadingForge
    from framework.services.http_client import _AddressPolicyBackend
    from framework.settings import settings
    from framework.wsgi import PassengerApplication

    imports_ms = milliseconds(start)
    settings.editor_api_enabled = False
    settings.apps_auto_reload = False
    settings.log_level = "ERROR"
    all_results = []
    warm_samples, cold_samples = [], []

    async def probes(temp):
        settings.internal_database_url = f"sqlite+aiosqlite:///{temp}/internal.db"
        project = ProjectConfig.model_validate(
            {
                "slug": "probe",
                "name": "Probe",
                "audit_enabled": False,
                "rate_limit": {"enabled": False},
                "databases": {"primary": {"url": f"sqlite+aiosqlite:///{temp}/app.db"}},
                "protection": {"request_timeout_seconds": 0.025, "max_concurrent_requests": 1},
            }
        )
        app = create_app(_configuration=(ForgeConfig(projects=[project]), {}))
        handler = {"completed": False, "cancelled": False}
        response_start_ms = None
        began = None

        @app.get("/api/probe/v1/slow")
        async def slow():
            try:
                await asyncio.sleep(0.18)
                handler["completed"] = True
                return {"ok": True}
            except asyncio.CancelledError:
                handler["cancelled"] = True
                raise

        async def observe(scope, receive, send):
            async def tracked(message):
                nonlocal response_start_ms
                if message["type"] == "http.response.start":
                    response_start_ms = milliseconds(began)
                await send(message)

            await app(scope, receive, tracked)

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe), base_url="http://testserver") as client:
                began = time.perf_counter()
                response = await client.get("/api/probe/v1/slow")
                result = {
                    "request_deadline": {
                        "configured_ms": 25,
                        "response_start_ms": response_start_ms,
                        "asgi_completion_ms": milliseconds(began),
                        "status": response.status_code,
                        **handler,
                    }
                }
        start = time.perf_counter()
        managers = [DataSourceManager(project) for _ in range(10)]
        result["ten_static_managers_ms"] = milliseconds(start)
        await asyncio.gather(*(manager.close() for manager in managers))
        manager = DataSourceManager(project)
        attempts = []

        async def fail(*args, **kwargs):
            attempts.append(True)
            await asyncio.sleep(0.02)
            raise httpx.ReadTimeout("controlled stall")

        manager.http._send_once = fail
        supports_total = "total_timeout_seconds" in DataSourceConfig.model_fields
        source = DataSourceConfig(
            name="slow",
            type="http",
            url="https://probe.invalid",
            public=True,
            timeout_seconds=0.02,
            retries=2,
            **({"total_timeout_seconds": 0.03} if supports_total else {}),
        )
        start = time.perf_counter()
        try:
            await manager.read(source, SimpleNamespace(query_params=httpx.QueryParams()))
        except httpx.TimeoutException:
            pass
        result["datasource_retry"] = {
            "phase_timeout_ms": 20,
            "requested_total_budget_ms": 30,
            "source_supports_total_budget": supports_total,
            "elapsed_ms": milliseconds(start),
            "attempts": len(attempts),
        }
        await manager.close()
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=2))
        original = socket.getaddrinfo

        def dns(*args, **kwargs):
            time.sleep(0.12)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

        socket.getaddrinfo = dns
        try:
            backend = _AddressPolicyBackend(block_private_networks=True)
            start = time.perf_counter()
            await asyncio.gather(*(backend.connect_tcp("probe.invalid", 443, timeout=0.005) for _ in range(2)), return_exceptions=True)
            result["dns_caller_timeout_ms"] = milliseconds(start)
            start = time.perf_counter()
            await asyncio.to_thread(lambda: True)
            result["file_executor_wait_after_dns_ms"] = milliseconds(start)
        finally:
            socket.getaddrinfo = original
        return result

    for _ in range(args.repeats):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            all_results.append(asyncio.run(probes(temp)))
            root = temp / "apps"
            root.mkdir()
            settings.internal_database_url = f"sqlite+aiosqlite:///{temp}/passenger-internal.db"
            for number in range(10):
                appdir = root / f"P{number}"
                appdir.mkdir()
                (appdir / "app.json").write_text(
                    json.dumps(
                        {
                            "slug": f"p{number}",
                            "name": f"Project {number}",
                            "audit_enabled": False,
                            "rate_limit": {"enabled": False},
                            "databases": {"primary": {"url": f"sqlite+aiosqlite:///{appdir}/app.db"}},
                            "data_sources": [{"name": "info", "type": "static", "public": True, "data": {"ok": True}}],
                        }
                    )
                )
            bridge = PassengerApplication(lambda root=root: ReloadingForge(root))
            try:
                with httpx.Client(transport=httpx.WSGITransport(app=bridge), base_url="http://testserver") as client:
                    start = time.perf_counter()
                    assert client.get("/api/p0/v1/data/info").json() == {"ok": True}
                    cold_samples.append(milliseconds(start))
                    for _ in range(30):
                        start = time.perf_counter()
                        assert client.get("/api/p0/v1/data/info").json() == {"ok": True}
                        warm_samples.append(milliseconds(start))
            finally:
                bridge.close()
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "source_root": str(args.source_root.resolve()),
                "framework_import_ms": imports_ms,
                "controlled_runs": all_results,
                "passenger_10_project_first_request": summary(cold_samples),
                "passenger_warm_request": summary(warm_samples),
                "limits": "Local SQLite, in-process WSGI; no hosting spawn, TLS, real DNS or remote database latency. Percentiles use nearest rank.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
