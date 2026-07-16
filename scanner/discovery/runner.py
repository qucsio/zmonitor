import logging

from django.utils import timezone

from scanner.models import DiscoveryRun, VENUE_KALSHI, VENUE_POLYMARKET

from . import kalshi, polymarket

logger = logging.getLogger("scanner")

_VENUES = {
    VENUE_POLYMARKET: polymarket.discover,
    VENUE_KALSHI: kalshi.discover,
}


def run_discovery(venue):
    if venue not in _VENUES:
        raise ValueError(f"unknown venue: {venue}")

    from scanner import jobs
    jobs.job_started("discovery")
    run = DiscoveryRun.objects.create(venue=venue, status="running")
    try:
        counts = _VENUES[venue]()
        run.status = "ok"
        run.markets_seen = counts["markets_seen"]
        run.markets_new = counts["markets_new"]
        run.markets_updated = counts["markets_updated"]
    except Exception as exc:  # noqa: BLE001
        logger.exception("discovery failed for %s", venue)
        run.status = "error"
        run.error_text = str(exc)
    finally:
        run.finished_at = timezone.now()
        run.save()
        jobs.job_finished("discovery", result={
            "venue": venue, "status": run.status,
            "seen": run.markets_seen, "new": run.markets_new, "updated": run.markets_updated})
    return run
