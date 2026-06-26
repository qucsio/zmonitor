from celery import shared_task

from scanner.models import VENUE_KALSHI, VENUE_POLYMARKET


@shared_task
def ping():
    return "pong"


@shared_task(queue="discovery")
def discover_venue(venue):
    from scanner.discovery.runner import run_discovery

    run = run_discovery(venue)
    return {"venue": venue, "status": run.status, "seen": run.markets_seen,
            "new": run.markets_new, "updated": run.markets_updated}


@shared_task(queue="discovery")
def discover_all():
    from django.conf import settings

    results = []
    if settings.SCANNER["POLYMARKET_ENABLED"]:
        results.append(discover_venue.run(VENUE_POLYMARKET))
    if settings.SCANNER["KALSHI_ENABLED"]:
        results.append(discover_venue.run(VENUE_KALSHI))
    return results
