from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

_T = TypeVar("_T")


async def run_io(function: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run blocking IO without abandoning owned files when the caller cancels."""
    worker = asyncio.create_task(asyncio.to_thread(partial(function, *args, **kwargs)))
    return await wait_owned(worker)


async def wait_owned(worker: asyncio.Future[_T]) -> _T:
    """Finish owned cleanup before returning caller cancellation."""
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # An owned operation must finish before its context can return or close
        # its resources, even if cancellation repeats during cleanup.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise
