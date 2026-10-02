from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

from .config import ForgeConfig, load_config
from .doctor import ensure_no_errors, forge_diagnostics
from .domain import expand_feature_packs
from .settings import load_settings, settings

_ENV_REF = re.compile(r"\$env:([A-Z0-9_]+)(?::-[^\"']*)?")
_SECRET_NAME = re.compile(r"(SECRET|TOKEN|KEY|PASSWORD)", re.I)
_PROJECT_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ._-]{0,62}[A-Za-z0-9])?$")
_PROJECT_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def _root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "root", None) or os.getcwd()).resolve()


def _apply_root_settings(root: Path) -> None:
    """Refresh the shared Settings object without breaking imported references."""
    fresh = load_settings(root=root)
    for field in fresh.__class__.model_fields:
        setattr(settings, field, getattr(fresh, field))


def _load(root: Path) -> ForgeConfig:
    cfg = load_config(root / "app")
    for project in cfg.projects:
        expand_feature_packs(project)
    return cfg


def _print_diagnostics(items) -> None:
    if not items:
        print("OK: no diagnostics")
        return
    for item in items:
        print(f"{item.level.upper():7} {item.code:28} {item.project or 'global':12} {item.message}")


def cmd_validate(args: argparse.Namespace) -> None:
    cfg = _load(_root(args))
    ensure_no_errors(cfg, production=False)
    print(f"OK: {len(cfg.projects)} project(s)")
    for project in cfg.projects:
        print(
            f"  {project.slug}: resources={len(project.resources)} mongo={len(project.mongo_resources)} "
            f"operations={len(project.operations)} data_sources={len(project.data_sources)} events={len(project.event_channels)}"
        )


def cmd_doctor(args: argparse.Namespace) -> None:
    cfg = _load(_root(args))
    items = forge_diagnostics(cfg, production=args.production)
    if args.json:
        print(json.dumps([d.to_dict() for d in items], indent=2))
    else:
        _print_diagnostics(items)
    if any(d.level == "error" for d in items):
        raise SystemExit(2)


def cmd_routes(args: argparse.Namespace) -> None:
    root = _root(args)
    sys.path.insert(0, str(root))
    old = os.getcwd()
    try:
        os.chdir(root)
        from .factory import create_app

        app = create_app()
        for route in app.routes:
            path = getattr(route, "path", None)
            if not path:
                continue
            methods = ",".join(sorted(getattr(route, "methods", []) or ["WS"]))
            print(f"{methods:18} {path}")
    finally:
        os.chdir(old)


def _fragment_schema_rel() -> str:
    return "../../../schemas/fragment.schema.json"


def _starter_fragments(name: str, slug: str, preset: str) -> dict[str, dict[str, Any]]:
    env_prefix = re.sub(r"[^A-Z0-9]+", "_", slug.upper())
    fragments: dict[str, dict[str, Any]] = {
        "10-databases.json": {
            "$schema": _fragment_schema_rel(),
            "databases": {"primary": {"url": f"$env:{env_prefix}_DATABASE_URL:-sqlite+aiosqlite:///./data/{slug}.db"}},
        },
        "20-security.json": {
            "$schema": _fragment_schema_rel(),
            "security": {
                "bootstrap_enabled": True,
                "bootstrap_admin_key": f"$env:{env_prefix}_BOOTSTRAP_ADMIN_KEY",
                "bootstrap_one_time": True,
            },
            "roles": {"admin": {"permissions": ["*"]}},
        },
        "30-performance.json": {
            "$schema": _fragment_schema_rel(),
            "cache": {"enabled": True, "backend": "memory", "default_ttl_seconds": 30},
            "rate_limit": {
                "enabled": True,
                "backend": "memory",
                "requests": 300,
                "window_seconds": 60,
                "route_requests": 120,
                "route_window_seconds": 60,
            },
        },
    }
    if preset == "minimal":
        fragments["40-resources.json"] = {"$schema": _fragment_schema_rel(), "resources": []}
    else:
        permission = f"{slug}.items"
        fragments["40-resources.json"] = {
            "$schema": _fragment_schema_rel(),
            "resources": [
                {
                    "database": "primary",
                    "table": "items",
                    "path": "items",
                    "auto_create": True,
                    "columns": {
                        "id": {"type": "integer", "primary_key": True, "nullable": False},
                        "name": {"type": "string", "nullable": False, "max_length": 120},
                    },
                    "writable_fields": ["name"],
                    "allowed_sort": ["id", "name"],
                    "permissions": {
                        "list": f"{permission}.list",
                        "read": f"{permission}.read",
                        "create": f"{permission}.create",
                        "update": f"{permission}.update",
                        "delete": f"{permission}.delete",
                    },
                }
            ],
        }
    if preset == "discord-bot":
        fragments["50-bot.json"] = {
            "$schema": _fragment_schema_rel(),
            "roles": {
                "bot": {
                    "permissions": [
                        f"{slug}.items.*",
                        "system.meta.read",
                    ]
                }
            },
        }
    elif preset == "game-backend":
        fragments["50-game.json"] = {
            "$schema": _fragment_schema_rel(),
            "features": {"gaming": {"enabled": True, "database": "primary", "table_prefix": "game_"}},
        }
    return fragments


def cmd_new(args: argparse.Namespace) -> None:
    root = _root(args)
    name = args.name
    slug = args.slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not _PROJECT_NAME.fullmatch(name) or name in {".", ".."}:
        raise SystemExit("Project name must be 1-64 safe filename characters and cannot contain a path")
    if not _PROJECT_SLUG.fullmatch(slug):
        raise SystemExit("Project slug must be 1-64 lowercase letters, digits or internal hyphens")
    target = root / "app" / name
    apps_root = (root / "app").resolve()
    if target.resolve().parent != apps_root:
        raise SystemExit("Project target must be an immediate child of app/")
    if target.exists():
        raise SystemExit(f"Already exists: {target}")
    (target / "config").mkdir(parents=True)
    (target / "hooks").mkdir()
    (target / "data").mkdir()
    manifest = {
        "$schema": "../../schemas/manifest.schema.json",
        "slug": slug,
        "name": name,
        "version": "1.0.0",
        "api_prefix": f"/api/{slug}/v1",
        "docs_enabled": True,
        "audit_enabled": True,
    }
    (target / "app.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    fragments = _starter_fragments(name, slug, args.preset)
    for filename, value in fragments.items():
        (target / "config" / filename).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    (target / "hooks" / "__init__.py").write_text("", encoding="utf-8")
    schema_dir = root / "schemas"
    if not all((schema_dir / name).exists() for name in ("project.schema.json", "manifest.schema.json", "fragment.schema.json")):
        from .schema import write_schemas

        write_schemas(schema_dir)
    print(f"Created {target} preset={args.preset} fragments={len(fragments)}")
    print("Next: run `forge init` (once per checkout), then `forge validate` and `forge dev`.")


def _required_secret_envs(root: Path) -> set[str]:
    names: set[str] = set()
    for path in (root / "app").glob("**/*.json"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _ENV_REF.finditer(text):
            name = match.group(1)
            token = match.group(0)
            has_default = ":-" in token
            if _SECRET_NAME.search(name) and not has_default:
                names.add(name)
    return names


def _secret() -> str:
    return secrets.token_urlsafe(48)


def _replace_env_secrets(text: str, values: dict[str, str]) -> str:
    """Preserve user-owned .env lines verbatim except Forge-managed secret values."""
    seen: set[str] = set()
    out: list[str] = []
    assignment = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=).*$")
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        ending = line[len(raw) :] or "\n"
        match = assignment.match(raw)
        if match and match.group(2) in values:
            name = match.group(2)
            out.append(f"{match.group(1)}{name}{match.group(3)}{values[name]}{ending}")
            seen.add(name)
        else:
            out.append(line)
    if out and not out[-1].endswith(("\n", "\r")):
        out[-1] += "\n"
    missing = [name for name in sorted(values) if name not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("\n")
        out.append("# JSON API Forge generated/rotated secrets.\n")
        out.extend(f"{name}={values[name]}\n" for name in missing)
    return "".join(out)


def cmd_init(args: argparse.Namespace) -> None:
    root = _root(args)
    target = root / ".env"
    managed = {"OPERATOR_TOKEN"} if args.production else set()
    editor_enabled = bool(getattr(args, "editor", False))
    if editor_enabled:
        managed.add("EDITOR_TOKEN")
    names = sorted(_required_secret_envs(root) | managed)
    generated = {name: _secret() for name in names}
    existed = target.exists()
    if existed:
        if not args.force:
            raise SystemExit(f"Refusing to overwrite existing {target}. Use --force only if you intend to rotate Forge secrets.")
        content = _replace_env_secrets(target.read_text(encoding="utf-8"), generated)
    else:
        baseline = [
            "# Generated by JSON API Forge `forge init`.\n",
            "# Never commit this file.\n",
            f"APP_ENV={'production' if args.production else 'development'}\n",
            "INTERNAL_DATABASE_URL=sqlite+aiosqlite:///./data/internal-v4.db\n",
            "REDIS_URL=\n",
            "LOG_LEVEL=INFO\n",
            f"EDITOR_API_ENABLED={'true' if editor_enabled else 'false'}\n",
            "\n",
        ]
        content = "".join(baseline) + "".join(f"{name}={generated[name]}\n" for name in names)
    target.write_text(content, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print(f"{'Updated' if existed else 'Created'} {target} with {len(names)} generated/rotated secret(s).")
    print("Bootstrap credentials are one-time by default: create a persistent API key, then the bootstrap key is consumed.")


def cmd_secrets(args: argparse.Namespace) -> None:
    for _ in range(args.count):
        print(_secret())


def cmd_schema(args: argparse.Namespace) -> None:
    root = _root(args)
    from .schema import write_schemas

    paths = write_schemas(root / "schemas")
    for path in paths:
        print(path)


def cmd_openapi(args: argparse.Namespace) -> None:
    root = _root(args)
    sys.path.insert(0, str(root))
    old = os.getcwd()
    try:
        os.chdir(root)
        from .factory import create_app

        app = create_app()
        output = Path(args.output) if args.output else root / "openapi.json"
        if not output.is_absolute():
            output = root / output
        output.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
        print(output)
    finally:
        os.chdir(old)


def cmd_migrate(args: argparse.Namespace) -> None:
    """Create Forge-owned support tables and declarative auto-create SQL tables explicitly."""
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from .db import build_registry
    from .editor_identity import init_editor_identity
    from .security import init_security

    root = _root(args)
    _apply_root_settings(root)
    cfg = _load(root)

    async def run() -> None:
        internal_url = settings.internal_database_url
        internal_kwargs: dict[str, Any] = {}
        if not internal_url.startswith("sqlite+"):
            internal_kwargs = {
                "pool_pre_ping": settings.internal_pool_pre_ping,
                "pool_size": settings.internal_pool_size,
                "max_overflow": settings.internal_max_overflow,
                "pool_timeout": settings.internal_pool_timeout,
                "pool_recycle": settings.internal_pool_recycle,
            }
        internal = create_async_engine(internal_url, **internal_kwargs)
        try:
            await init_security(internal, mode="create")
            await init_editor_identity(internal, mode="create")
        finally:
            await internal.dispose()

        for project in cfg.projects:
            migrated = project.model_copy(deep=True)
            for database in migrated.databases.values():
                database.support_schema_mode = "create"
            registry = await build_registry(migrated)
            await registry.dispose()
            print(f"migrated project={project.slug} sql_databases={len(project.databases)}")

    asyncio.run(run())
    print("Migration complete. Production can now use support_schema_mode=validate and INTERNAL_SCHEMA_MODE=validate.")


def cmd_dev(args: argparse.Namespace) -> None:
    root = _root(args)
    os.chdir(root)
    import uvicorn

    uvicorn.run(
        "framework.reload:create_live_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(root / "app")] if args.reload else None,
        reload_includes=["*.py"] if args.reload else None,
        reload_excludes=["*/data/*", "*/media/*", "*/logs/*", "*/__pycache__/*", "*.db*", "*.sqlite*", "*.forge.lock"]
        if args.reload
        else None,
        log_level=args.log_level,
        proxy_headers=False,
        access_log=False,
        ws_max_size=1024 * 1024,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", description="JSON API Forge developer CLI")
    parser.add_argument("--root", help="Project root (defaults to current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="Parse configuration and run semantic validation")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("doctor", help="Run security, routing and deployment diagnostics")
    p.add_argument("--production", action="store_true", help="Enable production-only checks")
    p.add_argument("--json", action="store_true", help="Emit machine-readable diagnostics")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("routes", help="Print generated HTTP/WebSocket routes")
    p.set_defaults(func=cmd_routes)

    for command in ("new", "new-app"):
        p = sub.add_parser(command, help="Create an app/NAME declarative project")
        p.add_argument("name")
        p.add_argument("--slug")
        p.add_argument("--preset", choices=["minimal", "postgres-api", "discord-bot", "game-backend"], default="minimal")
        p.set_defaults(func=cmd_new)

    p = sub.add_parser("init", help="Create a local .env with cryptographically random secrets")
    p.add_argument("--production", action="store_true")
    p.add_argument("--editor", action="store_true", help="Enable the account-based Editor control plane and generate its setup token")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("secrets", help="Generate strong secrets without modifying files")
    p.add_argument("--count", type=int, default=1)
    p.set_defaults(func=cmd_secrets)

    p = sub.add_parser("schema", help="Regenerate JSON Schemas from typed config models")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("openapi", help="Export the generated OpenAPI document")
    p.add_argument("--output", "-o")
    p.set_defaults(func=cmd_openapi)

    p = sub.add_parser("migrate", help="Create Forge support schema and declarative auto-create SQL tables")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("dev", help="Run the development ASGI server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--log-level", default="info")
    p.set_defaults(func=cmd_dev)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_root_settings(_root(args))
    args.func(args)


if __name__ == "__main__":
    main()
