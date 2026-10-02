"""Deprecated compatibility entry point. Use `forge init` for deployment secrets."""

from __future__ import annotations

import argparse
import secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Deprecated JSON API Forge secret helper")
    parser.add_argument("--print", action="store_true", dest="print_secret", help="print one random secret for manual use")
    args = parser.parse_args()
    if not args.print_secret:
        parser.error("This helper no longer initializes deployments. Run `forge init` instead (or use --print only for a manual secret).")
    print(secrets.token_urlsafe(48))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
