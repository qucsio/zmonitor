import contextlib

import redis
from celery import shared_task
from django.conf import settings

from scanner.models import VENUE_KALSHI, VENUE_POLYMARKET


@shared_task
def ping():
    return "pong"


@contextlib.contextmanager
def _redis_lock(key, ttl):
    """Best-effort distributed lock so overlapping beat ticks don't stack runs."""
    client = redis.from_url(settings.REDIS_URL)
    acquired = client.set(key, "1", nx=True, ex=ttl)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                client.delete(key)


@shared_task(queue="discovery")
def discover_venue(venue):
    from scanner.discovery.runner import run_discovery

    run = run_discovery(venue)
    return {"venue": venue, "status": run.status, "seen": run.markets_seen,
            "new": run.markets_new, "updated": run.markets_updated}


@shared_task(queue="discovery")
def discover_all():
    # Lock TTL a bit above the interval; if a previous run is still going, skip.
    ttl = max(settings.SCANNER["DISCOVERY_INTERVAL_SEC"] * 2, 900)
    with _redis_lock("lock:discover_all", ttl) as acquired:
        if not acquired:
            return {"skipped": "another discovery run in progress"}
        results = []
        if settings.SCANNER["POLYMARKET_ENABLED"]:
            results.append(discover_venue.run(VENUE_POLYMARKET))
        if settings.SCANNER["KALSHI_ENABLED"]:
            results.append(discover_venue.run(VENUE_KALSHI))
        return results
