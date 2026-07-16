"""Lightweight request-rate counters in Redis, so we can see how close we are to
venue rate limits instead of hoping."""
import time

import redis
from django.conf import settings

_WINDOW_MIN = 5  # rolling window for the summary


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def record_request(venue, status_code):
    """Count one request (and 429s) into a per-minute bucket."""
    try:
        client = _redis()
        bucket = int(time.time() // 60)
        pipe = client.pipeline()
        pipe.incr(f"rate:{venue}:{bucket}")
        pipe.expire(f"rate:{venue}:{bucket}", 600)
        if status_code == 429:
            pipe.incr(f"rate429:{venue}:{bucket}")
            pipe.expire(f"rate429:{venue}:{bucket}", 600)
        pipe.execute()
    except Exception:  # noqa: BLE001
        pass


def rate_summary():
    """Per-venue requests in the last minute and over the rolling window + 429 count."""
    out = {}
    try:
        client = _redis()
        now_bucket = int(time.time() // 60)
        for venue in ("polymarket", "kalshi"):
            last_min = int(client.get(f"rate:{venue}:{now_bucket}") or 0)
            window = 0
            errs = 0
            for i in range(_WINDOW_MIN):
                b = now_bucket - i
                window += int(client.get(f"rate:{venue}:{b}") or 0)
                errs += int(client.get(f"rate429:{venue}:{b}") or 0)
            out[venue] = {
                "last_min": last_min,
                "per_min_avg": round(window / _WINDOW_MIN, 1),
                "err429_window": errs,
            }
    except Exception:  # noqa: BLE001
        pass
    return out
