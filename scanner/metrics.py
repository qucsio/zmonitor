"""Lightweight request-rate counters in Redis, so we can see how close we are to
venue rate limits instead of hoping."""
import time

import redis
from django.conf import settings

_WINDOW_MIN = 5  # rolling window for the summary

# Conservative per-minute reference limits (docs-based, lowest tier / single-query).
# Kalshi Basic: 200 read tokens/s ÷ 10 = 20 req/s = 1200/min.
# Polymarket /book: 1500 per 10s = ~9000/min (per-IP, Cloudflare).
LIMIT_PER_MIN = {"kalshi": 1200, "polymarket": 9000}


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
            limit = LIMIT_PER_MIN.get(venue, 0)
            usage = max(last_min, window / _WINDOW_MIN)
            ratio = (usage / limit) if limit else 0
            level = "danger" if (errs or ratio >= 0.8) else "warn" if ratio >= 0.5 else "ok"
            out[venue] = {
                "last_min": last_min,
                "per_min_avg": round(window / _WINDOW_MIN, 1),
                "err429_window": errs,
                "limit_per_min": limit,
                "pct": round(ratio * 100, 1),
                "level": level,
            }
    except Exception:  # noqa: BLE001
        pass
    return out
