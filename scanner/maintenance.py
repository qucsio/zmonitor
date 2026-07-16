"""Reaper: move resolved/expired markets & pairs out of the live set into archive."""
import logging
from datetime import timedelta

from django.utils import timezone

from scanner import jobs
from scanner.models import DiscoveryRun, MatchedPair, RawMarket

logger = logging.getLogger("scanner")


def reap_stale():
    """Mark markets whose close_time has passed as closed, and archive pairs whose
    markets are closed. Kept in DB (queryable) but out of the live views."""
    jobs.job_started("reaper")
    try:
        now = timezone.now()
        closed_by_time = RawMarket.objects.filter(
            closed=False, close_time__isnull=False, close_time__lt=now).update(closed=True)

        # Pairs whose either market is closed -> archived (unless the user disabled/rejected).
        archived = MatchedPair.objects.filter(
            status__in=["matched", "needs_review", "candidate"]
        ).filter(
            models_or_closed()
        ).update(status="archived")

        # orphaned discovery runs (process killed mid-run) -> mark errored
        stale_runs = DiscoveryRun.objects.filter(
            finished_at__isnull=True, started_at__lt=now - timedelta(minutes=30)
        ).update(status="error", finished_at=now, error_text="orphaned (no finish)")

        result = {"markets_closed": closed_by_time, "pairs_archived": archived,
                  "stale_runs_cleared": stale_runs}
        jobs.job_finished("reaper", result=result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("reaper failed")
        jobs.job_finished("reaper", error=exc)
        return {"error": str(exc)}


def models_or_closed():
    from django.db.models import Q
    return Q(polymarket_market__closed=True) | Q(kalshi_market__closed=True)
