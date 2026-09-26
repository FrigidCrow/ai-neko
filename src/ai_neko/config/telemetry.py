"""Process-local metrics: bounded memory buffer, batched JSONL sink, percentiles.

Records carry only a metric name, a finite numeric value and coarse tags
(status/error codes). Never user content, prompts, URLs or credentials. The
JSONL sink under ``logs/`` is the G6 reporting input; in-memory series back
percentile queries without reading the file back.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from pathlib import Path

_MAX_SERIES = 10_000
_TRIM_SERIES = 5_000
_MAX_PENDING = 2_000


class Metrics:
    """Cheap to call from hot paths; disk writes happen only on flush."""

    def __init__(self, path: Path | None, *, flush_threshold: int = 64):
        self._path = path
        self._lock = threading.Lock()
        self._pending: deque[dict] = deque()
        self._series: dict[str, list[float]] = {}
        self._dropped = 0
        if not isinstance(flush_threshold, int) or not 1 <= flush_threshold <= 4096:
            raise ValueError("flush_threshold must be between 1 and 4096")
        self._flush_threshold = flush_threshold

    def record(self, metric: str, value: float, **tags: object) -> None:
        if not isinstance(metric, str) or not metric.isascii() or not metric.islower():
            return
        if len(metric) > 64 or not metric.replace("_", "a").isalnum() or metric[0].isdigit():
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        number = float(value)
        if not math.isfinite(number):
            return
        entry = {"ts": round(time.time(), 3), "metric": metric, "value": round(number, 3)}
        clean_tags = {
            key: item
            for key, item in tags.items()
            if isinstance(item, str)
            and item.isascii()
            and len(item) <= 32
            and item.replace("_", "-").replace("-", "a").isalnum()
        }
        if clean_tags:
            entry["tags"] = clean_tags
        flush = False
        with self._lock:
            self._pending.append(entry)
            series = self._series.setdefault(metric, [])
            series.append(number)
            if len(series) > _MAX_SERIES:
                del series[:_TRIM_SERIES]
            flush = len(self._pending) >= self._flush_threshold
        if flush:
            self.flush()

    def flush(self) -> int:
        """Write buffered records; returns the attempted batch size."""
        with self._lock:
            batch = list(self._pending)
            self._pending.clear()
        if not batch:
            return 0
        if self._path is None:
            return len(batch)
        try:
            payload = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in batch)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
        except OSError:
            # Keep the newest records within a bounded retry buffer; count drops.
            with self._lock:
                room = _MAX_PENDING - len(self._pending)
                self._pending.extendleft(reversed(batch[-room:] if room > 0 else []))
                self._dropped += max(0, len(batch) - max(0, room))
            return 0
        return len(batch)

    def percentiles(self, metric: str, *, points: tuple[int, ...] = (50, 95, 99)) -> dict | None:
        with self._lock:
            values = sorted(self._series.get(metric, []))
        if not values:
            return None
        result = {"count": len(values), "mean": round(sum(values) / len(values), 3)}
        for point in points:
            if not isinstance(point, int) or not 1 <= point <= 99:
                continue
            index = min(len(values) - 1, max(0, math.ceil(point / 100 * len(values)) - 1))
            result[f"p{point}"] = round(values[index], 3)
        return result

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)
