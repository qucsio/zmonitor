import time

import redis
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

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
        "recent_runs": models.DiscoveryRun.objects.order_by("-started_at")[:10],
    }
    return render(request, "scanner/dashboard.html", ctx)


def markets(request):
    qs = models.RawMarket.objects.all().order_by("-last_seen_at")

    venue = request.GET.get("venue") or ""
    status = request.GET.get("status") or ""
    matching_status = request.GET.get("matching_status") or ""
    enable_ob = request.GET.get("enable_orderbook") or ""
    search = request.GET.get("q") or ""

    if venue:
        qs = qs.filter(venue=venue)
    if status:
        qs = qs.filter(status=status)
    if matching_status:
        qs = qs.filter(matching_status=matching_status)
    if enable_ob in ("true", "false"):
        qs = qs.filter(enable_orderbook=(enable_ob == "true"))
    if search:
        qs = qs.filter(title__icontains=search) | qs.filter(question__icontains=search)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "scanner/markets.html", {
        "page": page,
        "filters": {"venue": venue, "status": status, "matching_status": matching_status,
                    "enable_orderbook": enable_ob, "q": search},
        "matching_states": ["pending", "normalized", "matched", "rejected", "needs_review", "ignored"],
        "total": paginator.count,
    })


def market_detail(request, pk):
    market = get_object_or_404(models.RawMarket, pk=pk)
    return render(request, "scanner/market_detail.html", {
        "market": market,
        "outcomes": market.outcomes.all(),
        "normalized": getattr(market, "normalized", None),
    })


def pairs(request):
    qs = models.MatchedPair.objects.select_related(
        "polymarket_market", "kalshi_market").order_by("-match_score", "-updated_at")
    status = request.GET.get("status") or ""
    game = request.GET.get("game") or ""
    if status:
        qs = qs.filter(status=status)
    if game:
        qs = qs.filter(game=game)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page"))
    counts = {s: models.MatchedPair.objects.filter(status=s).count()
              for s in ["matched", "candidate", "needs_review", "rejected", "disabled"]}
    return render(request, "scanner/pairs.html", {
        "page": page, "filters": {"status": status, "game": game},
        "counts": counts, "total": paginator.count,
        "statuses": ["candidate", "matched", "needs_review", "rejected", "disabled"],
    })


def pair_detail(request, pk):
    pair = get_object_or_404(
        models.MatchedPair.objects.select_related("polymarket_market", "kalshi_market"), pk=pk)
    pm = pair.polymarket_market
    k = pair.kalshi_market
    return render(request, "scanner/pair_detail.html", {
        "pair": pair,
        "pm": pm, "kalshi": k,
        "pm_norm": getattr(pm, "normalized", None),
        "k_norm": getattr(k, "normalized", None),
        "pm_outcomes": pm.outcomes.all(),
        "k_outcomes": k.outcomes.all(),
    })


@require_POST
def pair_action(request, pk, action):
    pair = get_object_or_404(models.MatchedPair, pk=pk)
    mapping = {"mark-matched": "matched", "reject": "rejected",
               "needs-review": "needs_review", "disable": "disabled"}
    if action in mapping:
        pair.status = mapping[action]
        if action == "reject":
            pair.reject_reason = request.POST.get("reason", "manual")
        pair.save(update_fields=["status", "reject_reason", "updated_at"])
        messages.success(request, f"Pair {pk} -> {mapping[action]}")
    return redirect(request.META.get("HTTP_REFERER", "/pairs/"))


@require_POST
def run_matching_view(request):
    from .tasks import match_markets_task

    try:
        match_markets_task.delay()
    except Exception:  # noqa: BLE001
        match_markets_task.run()
    messages.success(request, "Matching queued")
    return redirect("pairs")


@require_POST
def run_discovery_view(request):
    from .tasks import discover_venue

    venue = request.POST.get("venue", "all")
    venues = ["polymarket", "kalshi"] if venue == "all" else [venue]
    for v in venues:
        try:
            discover_venue.delay(v)
        except Exception:  # noqa: BLE001  (broker down -> run inline)
            discover_venue.run(v)
    messages.success(request, f"Discovery queued for: {', '.join(venues)}")
    return redirect("dashboard")
