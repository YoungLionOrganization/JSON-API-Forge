from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

_T = TypeVar("_T")


async def run_io(function: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run blocking IO without abandoning owned files when the caller cancels."""
    worker = asyncio.create_task(asyncio.to_thread(partial(function, *args, **kwargs)))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Thread cancellation cannot stop an OS operation. Drain it before an
        # enclosing context closes its handle, even if cancellation repeats.
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
