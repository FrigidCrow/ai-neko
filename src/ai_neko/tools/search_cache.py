"""Bounded process-only search snippets, with independent coalesced waiters."""

from __future__ import annotations

import asyncio
import copy
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable


class SearchCacheError(ValueError):
    """A disposed or superseded search must not be reused."""


@dataclass
class _Entry:
    value: dict
    created: float
    sequence: int


@dataclass
class _Flight:
    task: asyncio.Task
    scope: str
    epoch: tuple[int, int]
    waiters: int = 0


class SearchCache:
    TTL = 120
    MAX_ENTRIES = 64

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: OrderedDict[tuple, _Entry] = OrderedDict()
        self._flights: dict[tuple, _Flight] = {}
        self._tasks: set[asyncio.Task] = set()
        self._scope_epochs: dict[str, int] = {}
        self._generation = 0
        self._sequence = 0
        self._closed = False

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def _epoch(self, scope: str) -> tuple[int, int]:
        return self._generation, self._scope_epochs.get(scope, 0)

    @staticmethod
    def _cancel(task: asyncio.Task) -> None:
        if not task.done() and not task.get_loop().is_closed():
            task.get_loop().call_soon_threadsafe(task.cancel)

    def invalidate(self, scope: str | None = None) -> None:
        """Forget snippets and revoke in-flight delivery, including from worker threads."""
        with self._lock:
            if scope is None:
                scopes = {key[0] for key in self._entries} | {
                    flight.scope for flight in self._flights.values()
                }
            else:
                scopes = {scope}
            for value in scopes:
                self._scope_epochs[value] = self._scope_epochs.get(value, 0) + 1
            for key in list(self._entries):
                if scope is None or key[0] == scope:
                    del self._entries[key]
            for key, flight in list(self._flights.items()):
                if scope is None or flight.scope == scope:
                    del self._flights[key]
                    self._cancel(flight.task)

    def advance_generation(self) -> int:
        with self._lock:
            self._generation += 1
            self.invalidate()
            return self._generation

    def _done(self, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks.discard(task)
        if not task.cancelled():
            # A cancelled final waiter still must not leave an unobserved exception.
            task.exception()

    async def _run(self, key, epoch, sequence, fetch):
        value = await fetch()
        with self._lock:
            attached = any(
                item.task is asyncio.current_task() and item.waiters > 0
                for item in self._flights.values()
            )
            if self._closed or self._epoch(key[0]) != epoch or not attached:
                raise SearchCacheError("search_cache_invalidated")
            if (
                isinstance(value, dict)
                and value.get("status") == "ok"
                and isinstance(value.get("sources"), list)
                and all(
                    isinstance(item, dict)
                    and item.get("status") == "snippet"
                    and not item.get("text")
                    for item in value["sources"]
                )
            ):
                previous = self._entries.get(key)
                if previous is None or previous.sequence <= sequence:
                    self._entries[key] = _Entry(copy.deepcopy(value), self._clock(), sequence)
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.MAX_ENTRIES:
                        self._entries.popitem(last=False)
        return value

    async def get(
        self,
        *,
        scope: str,
        generation: int,
        query: str,
        locale: str,
        fetch: Callable[[], Awaitable[dict]],
        force_refresh: bool = False,
    ) -> dict:
        """Share only the network execution; every caller receives its own result copy."""
        key = (scope, generation, query, locale)
        flight_key = (*key, force_refresh)
        with self._lock:
            if self._closed:
                raise SearchCacheError("search_cache_closed")
            if generation != self._generation:
                raise SearchCacheError("search_configuration_changed")
            now = self._clock()
            for expired, entry in list(self._entries.items()):
                if now - entry.created >= self.TTL:
                    del self._entries[expired]
            cached = self._entries.get(key)
            if cached is not None and not force_refresh:
                self._entries.move_to_end(key)
                return {
                    **copy.deepcopy(cached.value),
                    "cache": {"status": "hit", "age_seconds": max(0, now - cached.created)},
                }
            flight = self._flights.get(flight_key)
            status = "coalesced" if flight is not None else "miss"
            if flight is None:
                if len(self._flights) >= self.MAX_ENTRIES:
                    raise SearchCacheError("search_cache_busy")
                self._sequence += 1
                task = asyncio.create_task(
                    self._run(key, self._epoch(scope), self._sequence, fetch)
                )
                flight = _Flight(task, scope, self._epoch(scope))
                self._flights[flight_key] = flight
                self._tasks.add(task)
                task.add_done_callback(self._done)
            elif flight.task.get_loop() is not asyncio.get_running_loop():
                raise SearchCacheError("search_cache_event_loop_changed")
            flight.waiters += 1
        try:
            value = await asyncio.shield(flight.task)
            with self._lock:
                if self._closed or self._epoch(scope) != flight.epoch:
                    raise SearchCacheError("search_cache_invalidated")
                return {
                    **copy.deepcopy(value),
                    "cache": {"status": status, "age_seconds": 0},
                }
        finally:
            last = False
            with self._lock:
                flight.waiters -= 1
                if flight.waiters == 0:
                    if self._flights.get(flight_key) is flight:
                        del self._flights[flight_key]
                    if not flight.task.done():
                        self._cancel(flight.task)
                        last = True
            if last:
                # Finish releasing the connection before cancellation is acknowledged.
                drain = asyncio.gather(flight.task, return_exceptions=True)
                while not drain.done():
                    try:
                        await asyncio.shield(drain)
                    except asyncio.CancelledError:
                        continue

    async def close(self) -> None:
        with self._lock:
            self._closed = True
            self.invalidate()
            tasks = tuple(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
