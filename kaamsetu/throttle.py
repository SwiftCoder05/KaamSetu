"""Small cache-backed rate limiter for login, signup, AI and import endpoints."""

import time

from django.core.cache import cache


def hit(key: str, limit: int, window_seconds: int) -> bool:
    """Records one attempt. Returns False when `limit` attempts were already made inside the window."""
    bucket = f"throttle:{key}:{int(time.time() // window_seconds)}"
    cache.add(bucket, 0, window_seconds + 5)
    return cache.incr(bucket) <= limit


def client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR", "unknown")
