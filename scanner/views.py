import time

import redis
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

from . import models


def _redis_client():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _check_postgres():
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _check_redis():
    try:
        t0 = time.time()
        _redis_client().ping()
        return True, int((time.time() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def health(request):
    pg_ok, pg_err = _check_postgres()
    redis_ok, redis_info = _check_redis()
    payload = {
        "status": "ok" if (pg_ok and redis_ok) else "degraded",
        "postgres": {"ok": pg_ok, "error": pg_err},
        "redis": {"ok": redis_ok, "latency_ms": redis_info if redis_ok else None,
                  "error": None if redis_ok else redis_info},
    }
    if request.GET.get("format") == "json" or request.headers.get("Accept") == "application/json":
        return JsonResponse(payload, status=200 if payload["status"] == "ok" else 503)

    last_errors = models.ApiHealthLog.objects.filter(ok=False).order_by("-created_at")[:20]
    return render(request, "scanner/health.html", {
        "payload": payload,
        "last_errors": last_errors,
    })


def dashboard(request):
    ctx = {
        "counters": {
            "polymarket_events": models.RawEvent.objects.filter(venue="polymarket").count(),
            "polymarket_markets": models.RawMarket.objects.filter(venue="polymarket").count(),
            "kalshi_events": models.RawEvent.objects.filter(venue="kalshi").count(),
            "kalshi_markets": models.RawMarket.objects.filter(venue="kalshi").count(),
            "matched_pairs": models.MatchedPair.objects.filter(status="matched").count(),
            "candidate_pairs": models.MatchedPair.objects.filter(status="candidate").count(),
            "needs_review_pairs": models.MatchedPair.objects.filter(status="needs_review").count(),
            "rejected_pairs": models.MatchedPair.objects.filter(status="rejected").count(),
            "open_opportunities": models.OpportunityEvent.objects.filter(status="open").count(),
        },
        "last_discovery": models.DiscoveryRun.objects.order_by("-started_at").first(),
    }
    return render(request, "scanner/dashboard.html", ctx)
