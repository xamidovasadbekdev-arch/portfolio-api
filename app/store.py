"""Key-value storage: Upstash Redis in production, in memory for tests and local runs."""

import os
import time

from app.config import get_settings


class MemoryStore:
    """Small stand-in with the same methods we use from upstash_redis.Redis."""

    def __init__(self):
        self._data: dict = {}
        self._expires: dict[str, float] = {}

    def _alive(self, key):
        if key in self._expires and self._expires[key] < time.time():
            self._data.pop(key, None)
            self._expires.pop(key, None)
        return key in self._data

    def get(self, key):
        return self._data[key] if self._alive(key) else None

    def incr(self, key):
        value = int(self.get(key) or 0) + 1
        self._data[key] = str(value)
        return value

    def expire(self, key, seconds):
        self._expires[key] = time.time() + int(seconds)
        return 1

    def delete(self, *keys):
        for key in keys:
            self._data.pop(key, None)
            self._expires.pop(key, None)
        return len(keys)

    def hgetall(self, key):
        return dict(self._data[key]) if self._alive(key) else {}

    def hset(self, key, values):
        self._data.setdefault(key, {}).update({k: str(v) for k, v in values.items()})
        return len(values)

    def set(self, key, value, nx=None):
        if nx and self._alive(key):
            return None
        self._data[key] = str(value)
        return True

    def rpush(self, key, *elements):
        items = self._data.setdefault(key, [])
        items.extend(str(e) for e in elements)
        return len(items)

    def lrange(self, key, start, stop):
        items = self._data.get(key, []) if self._alive(key) else []
        return items[start:] if stop == -1 else items[start : stop + 1]

    def lrem(self, key, count, element):
        items = self._data.get(key, [])
        before = len(items)
        self._data[key] = [item for item in items if item != str(element)]
        return before - len(self._data[key])


_store = None


def get_store():
    global _store
    if _store is None:
        settings = get_settings()
        if settings.redis_url and settings.redis_token:
            from upstash_redis import Redis

            _store = Redis(url=settings.redis_url, token=settings.redis_token)
        elif os.getenv("VERCEL"):
            # On Vercel every request can hit a fresh instance, so memory is not an option.
            raise RuntimeError("Upstash Redis is not configured (KV_REST_API_URL / KV_REST_API_TOKEN).")
        else:
            # Local development without Upstash: data lives until the process stops.
            _store = MemoryStore()
    return _store


def use_store(store):
    """Swap the store (tests)."""
    global _store
    _store = store
