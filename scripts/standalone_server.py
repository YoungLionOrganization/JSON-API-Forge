#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run a packaged JSON API Forge v0.5.3 server")
    value.add_argument("--root", default=".", help="Deployment root containing app/, .env and data/")
    value.add_argument("--host", default="127.0.0.1", help="Listen address; use 0.0.0.0 only behind a configured firewall/proxy")
    value.add_argument("--port", type=int, default=8000)
    value.add_argument("--log-level", choices=["critical", "error", "warning", "info", "debug", "trace"], default="info")
    value.add_argument("--proxy-headers", action="store_true", help="Accept proxy headers only from --forwarded-allow-ips")
    value.add_argument(
        "--forwarded-allow-ips", default="127.0.0.1", help="Trusted proxy peers; never use '*' on an internet-facing listener"
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"Deployment root does not exist: {root}")
    os.chdir(root)
    from framework.reload import create_live_app

    app = create_live_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        proxy_headers=args.proxy_headers,
        forwarded_allow_ips=args.forwarded_allow_ips if args.proxy_headers else "",
        access_log=False,
        ws_max_size=1024 * 1024,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
