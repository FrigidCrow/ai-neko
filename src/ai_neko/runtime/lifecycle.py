"""Wait for owned worker settlement without losing caller cancellation."""

import asyncio
import functools


async def _finish_task(task):
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


def _settled_mutation(method):
    @functools.wraps(method)
    async def wrapped(self, *args, **kwargs):
        return await _finish_task(asyncio.create_task(method(self, *args, **kwargs)))

    return wrapped
