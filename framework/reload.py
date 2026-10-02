"""Debounced JSON application reload with complete ASGI generation snapshots.

The listener stays alive. Each request (including streaming/WebSocket traffic)
keeps its routes, security policy and services until it finishes. Failed edits
retain the previous valid app configuration. Python imports and process-wide
settings still require a process restart; this does not reload Python modules.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ForgeConfig, load_config_resilient
from .settings import settings

log = logging.getLogger("json_api_forge.reload")


def configuration_signature(root: Path) -> tuple:
    """Stat only manifests and numbered JSON fragments, never data/media trees."""
    paths = [root.parent / ".env"]
    directories = sorted(root.iterdir())
    if len(directories) > 1024:
        raise ValueError("Too many entries in the apps root")
    for directory in directories:
        if directory.name.startswith(("_", ".")) or directory.is_symlink() or not directory.is_dir():
            continue
        paths.extend((directory / "app.json", directory / "manifest.json"))
        config = directory / "config"
        if config.is_symlink():
            paths.append(config)
        elif config.is_dir():
            fragments = sorted(config.glob("*.json"))
            if len(fragments) > 256:
                raise ValueError("Too many configuration fragments")
            paths.extend(fragments)
    values = []
    for path in paths:
        try:
            stat = path.lstat()
            values.append((str(path), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_mode))
        except FileNotFoundError:
            values.append((str(path), None))
    return tuple(values)


@dataclass(eq=False)
class _Generation:
    app: Any
    config: ForgeConfig
    context: Any = None
    requests: int = 0
    idle: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self):
        self.idle.set()
        self.context = self.app.router.lifespan_context(self.app)
        await self.context.__aenter__()

    async def close(self):
        await self.idle.wait()
        if self.context is not None:
            context, self.context = self.context, None
            await context.__aexit__(None, None, None)


class ReloadingForge:
    def __init__(self, apps_dir: Path | str | None = None):
        self.apps_dir = Path(apps_dir if apps_dir is not None else settings.apps_dir).resolve()
        config, issues = load_config_resilient(self.apps_dir)
        self.current = self._build(config, issues)
        self._observed = configuration_signature(self.apps_dir)
        self._lock = asyncio.Lock()
        self._retired: list[tuple[_Generation, asyncio.Task]] = []
        self._watcher: asyncio.Task | None = None
        self.last_error: str | None = None
        self.generation = 0

    @property
    def state(self):
        return self.current.app.state

    def _build(self, config, issues):
        from .factory import create_app

        return _Generation(
            create_app(
                apps_dir=self.apps_dir,
                _configuration=(config.model_copy(deep=True), issues),
                _reuse=self.current.app.state.runtimes if hasattr(self, "current") else None,
            ),
            config,
        )

    async def start(self):
        await self.current.start()
        if settings.apps_auto_reload:
            self._watcher = asyncio.create_task(self._watch(), name="forge-config-watch")

    async def close(self):
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
            self._watcher = None
        await self.current.close()
        for _generation, task in self._retired:
            await task
        self._retired.clear()

    async def reload_now(self) -> bool:
        """Apply a stable change; useful for explicit maintenance and regression tests."""
        async with self._lock:
            # Never accumulate unbounded databases/tasks behind a long-lived stream.
            remaining = []
            for generation, task in self._retired:
                if task.done():
                    task.result()
                elif generation.idle.is_set():
                    await task
                else:
                    remaining.append((generation, task))
            self._retired = remaining
            if len(self._retired) >= 2:
                return False
            candidate = None
            signature = None
            try:
                signature = await asyncio.to_thread(configuration_signature, self.apps_dir)
                if signature == self._observed:
                    return False
                config, issues = await asyncio.to_thread(load_config_resilient, self.apps_dir)
                previous = {Path(project.project_dir).name: project for project in self.current.config.projects}
                # Parsing errors and route ownership conflicts cannot silently
                # remove or replace an already running application's identity.
                config.projects.extend(project.model_copy(deep=True) for name, project in previous.items() if name in issues)
                self._check_ownership(config)
                candidate = self._build(config, issues)
                await candidate.start()
                failed = self._failed_existing(candidate)
                if failed:
                    await candidate.close()
                    candidate = None
                    for index, project in enumerate(config.projects):
                        if project.slug in failed:
                            name = Path(project.project_dir).name
                            if name not in previous:
                                raise ValueError("Reload cannot replace another app's identity")
                            issues[name] = "RuntimeReloadError"
                            config.projects[index] = previous[name].model_copy(deep=True)
                    self._check_ownership(config)
                    candidate = self._build(config, issues)
                    await candidate.start()
                    if self._failed_existing(candidate):
                        raise RuntimeError("Previous runtime could not be reconstructed")
                if signature != await asyncio.to_thread(configuration_signature, self.apps_dir):
                    await candidate.close()
                    return False
                old, self.current = self.current, candidate
                candidate = None
                self.generation += 1
                self._observed = signature
                self.last_error = None
                self._retired.append((old, asyncio.create_task(old.close(), name="forge-retired-generation")))
                log.info(
                    "Applied app configuration generation=%d apps=%d retained_errors=%d", self.generation, len(config.projects), len(issues)
                )
                return True
            except asyncio.CancelledError:
                if candidate is not None:
                    await candidate.close()
                raise
            except Exception as exc:
                if candidate is not None:
                    await candidate.close()
                self.last_error = type(exc).__name__
                if signature is not None:
                    self._observed = signature  # retry only after another file change
                log.error("App reload rejected error_type=%s; previous generation remains active", self.last_error)
                return False

    def _failed_existing(self, candidate):
        healthy_directories = {
            Path(runtime.config.project_dir).name for runtime in self.current.app.state.runtimes.values() if runtime.available
        }
        return {
            slug
            for slug, runtime in candidate.app.state.runtimes.items()
            if not runtime.available and Path(runtime.config.project_dir).name in healthy_directories
        }

    @staticmethod
    def _check_ownership(config):
        for index, project in enumerate(config.projects):
            prefix = project.api_prefix.rstrip("/")
            for other in config.projects[index + 1 :]:
                target = other.api_prefix.rstrip("/")
                if project.slug == other.slug or prefix == target or prefix.startswith(target + "/") or target.startswith(prefix + "/"):
                    raise ValueError("Reload contains conflicting app identities or API prefixes")

    async def _watch(self):
        pending = self._observed
        changed_at = time.monotonic()
        while True:
            await asyncio.sleep(settings.apps_reload_interval_seconds)
            try:
                signature = await asyncio.to_thread(configuration_signature, self.apps_dir)
                if signature != pending:
                    pending, changed_at = signature, time.monotonic()
                elif signature != self._observed and time.monotonic() - changed_at >= settings.apps_reload_debounce_seconds:
                    await self.reload_now()
            except (OSError, ValueError) as exc:
                if self.last_error != type(exc).__name__:
                    log.error("App watcher unavailable error_type=%s", type(exc).__name__)
                self.last_error = type(exc).__name__

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            message = await receive()
            if message["type"] != "lifespan.startup":
                return
            try:
                await self.start()
            except Exception as exc:
                await send({"type": "lifespan.startup.failed", "message": type(exc).__name__})
                return
            await send({"type": "lifespan.startup.complete"})
            await receive()
            try:
                await self.close()
            except Exception as exc:
                await send({"type": "lifespan.shutdown.failed", "message": type(exc).__name__})
                return
            await send({"type": "lifespan.shutdown.complete"})
            return
        generation = self.current
        generation.requests += 1
        generation.idle.clear()
        try:
            await generation.app(scope, receive, send)
        finally:
            generation.requests -= 1
            if generation.requests == 0:
                generation.idle.set()


def create_live_app():
    return ReloadingForge()
