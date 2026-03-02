import functools
import time
from typing import Dict


def async_ttl_cache(ttl: float = 2.0):
    """Cache async function results for *ttl* seconds, keyed by arguments."""

    def decorator(fn):
        _cache: Dict[tuple, tuple] = {}

        def _make_key(args, kwargs):
            parts = []
            for a in args:
                try:
                    hash(a)
                    parts.append(a)
                except TypeError:
                    parts.append(id(a))
            for k, v in sorted(kwargs.items()):
                try:
                    hash(v)
                    parts.append((k, v))
                except TypeError:
                    parts.append((k, id(v)))
            return tuple(parts)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            key = _make_key(args, kwargs)
            now = time.monotonic()
            if key in _cache:
                cached_at, result = _cache[key]
                if now - cached_at < ttl:
                    return result
            result = await fn(*args, **kwargs)
            _cache[key] = (now, result)
            return result

        wrapper.cache_clear = lambda: _cache.clear()
        return wrapper

    return decorator
