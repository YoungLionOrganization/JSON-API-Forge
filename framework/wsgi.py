"""Lazy Passenger HTTP bridge with an explicit ASGI service lifespan."""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading

from a2wsgi import ASGIMiddleware

from .reload import create_live_app

log = logging.getLogger("json_api_forge.wsgi")


class PassengerApplication:
    """Start services on the bridge's event loop, after the worker's first call."""

    def __init__(self, factory=create_live_app):
        self._factory = factory
        self._lock = threading.Lock()
        self._loop = None
        self._thread = None
        self._app = None
        self._bridge = None

    def _start(self):
        with self._lock:
            if self._bridge is not None:
                return
            app = self._factory()
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="forge-passenger-asgi", daemon=True)
            thread.start()
            startup = asyncio.run_coroutine_threadsafe(app.start(), loop)
            try:
                startup.result(timeout=60)
            except BaseException:
                startup.cancel()
                try:
                    asyncio.run_coroutine_threadsafe(app.close(), loop).result(timeout=10)
                finally:
                    loop.call_soon_threadsafe(loop.stop)
                    thread.join(timeout=10)
                    if not thread.is_alive():
                        loop.close()
                raise
            self._app, self._loop, self._thread = app, loop, thread
            self._bridge = ASGIMiddleware(app, loop=loop)
            atexit.register(self.close)

    def __call__(self, environ, start_response):
        self._start()
        return self._bridge(environ, start_response)

    def close(self):
        with self._lock:
            if self._bridge is None:
                return
            self._bridge = None
            try:
                asyncio.run_coroutine_threadsafe(self._app.close(), self._loop).result(timeout=30)
            except Exception:
                log.exception("Passenger service shutdown failed")
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=10)
                if not self._thread.is_alive():
                    self._loop.close()
                atexit.unregister(self.close)
