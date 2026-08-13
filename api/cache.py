"""In-process TTL cache — the API's whole caching story (spec §3.2).

Keyed by call args; a thundering herd collapses to one DB round-trip per TTL
window per distinct query. Single-process deployment (uvicorn --workers 1),
so process-local state is the correct scope.
"""

from __future__ import annotations

import functools
import threading
import time


def ttl(seconds: float):
    def deco(fn):
        store: dict[tuple, tuple[float, object]] = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            with lock:
                hit = store.get(key)
                if hit and now - hit[0] < seconds:
                    return hit[1]
            value = fn(*args, **kwargs)  # outside the lock: don't serialize DB calls
            with lock:
                store[key] = (now, value)
            return value

        wrapped.cache_clear = store.clear  # for tests
        return wrapped

    return deco
