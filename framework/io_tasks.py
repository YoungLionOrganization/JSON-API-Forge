"""Cancellation-safe ownership of blocking file work."""

import asyncio
import os


async def finish_io(awaitable):
    """Retain IO ownership until completion, including repeated request cancellation."""
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            task.exception()
        raise


def flush_file(out):
    out.flush()
    os.fsync(out.fileno())
