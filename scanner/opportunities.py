"""Opportunity lifecycle: open/update/close OpportunityEvent from live fork state,
and write OpportunityEdgePoint only per the write policy (not every tick)."""
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from scanner.models import OpportunityEdgePoint, OpportunityEvent

BOOK_BAD_FLAGS = {"pm_book_unavailable", "kalshi_book_unavailable",
                  "stale_pm_book", "stale_kalshi_book", "book_crossed_or_invalid"}


def _dec(x):
    if x in (None, ""):
        return None
    try:
        return Decimal(str(x))
    except Exception:  # noqa: BLE001
        return None


def _write_point(opp, fork, state, net, gross, size):
    OpportunityEdgePoint.objects.create(
        opportunity=opp,
        gross_edge=gross if gross is not None else Decimal("0"),
        net_edge=net if net is not None else Decimal("0"),
        size_usd=size,
        pm_vwap=_dec(fork.get("best_pm_vwap")),
        kalshi_vwap=_dec(fork.get("best_kalshi_vwap")),
        pm_price=_dec(fork.get("best_pm_vwap")),
        kalshi_price=_dec(fork.get("best_kalshi_vwap")),
        pm_book_age_ms=state.get("pm_book_age_ms"),
        kalshi_book_age_ms=state.get("kalshi_book_age_ms"),
        risk_flags=state.get("risk_flags") or [],
    )


def process(pair, state):
    """Evaluate both forks of a pair and drive opportunity lifecycle."""
    thr = settings.SCANNER["OPPORTUNITY_NET_EDGE_THRESHOLD"]
    interval = settings.SCANNER["OPPORTUNITY_EDGE_POINT_INTERVAL_SEC"]
    delta = settings.SCANNER["EDGE_POINT_DELTA_THRESHOLD"]
    risk = state.get("risk_flags") or []
    book_bad = any(f in BOOK_BAD_FLAGS for f in risk)
    opened = closed = 0

    for fork_key in ("fork_a", "fork_b"):
        fork = state.get(fork_key) or {}
        direction = fork.get("direction")
        if not direction:
            continue
        net = _dec(fork.get("best_net_edge"))
        gross = _dec(fork.get("best_gross_edge"))
        size = _dec(fork.get("max_size_usd")) or Decimal("0")

        opp = OpportunityEvent.objects.filter(
            pair=pair, direction=direction, status="open").first()
        is_opp = (net is not None and net >= thr and not book_bad
                  and pair.status == "matched")

        if is_opp:
            if opp is None:
                opp = OpportunityEvent.objects.create(
                    pair=pair, direction=direction, status="open",
                    first_gross_edge=gross, first_net_edge=net,
                    last_gross_edge=gross, last_net_edge=net,
                    max_gross_edge=gross, max_net_edge=net,
                    max_size_usd=size, risk_flags=risk)
                _write_point(opp, fork, state, net, gross, size)
                opened += 1
            else:
                new_max = net > (opp.max_net_edge if opp.max_net_edge is not None else Decimal("-1"))
                opp.last_gross_edge = gross
                opp.last_net_edge = net
                if new_max:
                    opp.max_net_edge = net
                    opp.max_gross_edge = gross
                if size > (opp.max_size_usd or Decimal("0")):
                    opp.max_size_usd = size
                opp.save()
                last = opp.edge_points.order_by("-ts").first()
                due = last is None
                if last is not None:
                    age = (timezone.now() - last.ts).total_seconds()
                    due = (age >= interval
                           or abs(net - last.net_edge) >= delta
                           or new_max)
                if due:
                    _write_point(opp, fork, state, net, gross, size)
        else:
            if opp is not None:
                opp.status = "stale" if book_bad else "closed"
                opp.ts_end = timezone.now()
                opp.close_reason = "book_stale" if book_bad else "below_threshold"
                if net is not None:
                    opp.last_net_edge = net
                    opp.last_gross_edge = gross
                opp.save()
                _write_point(opp, fork, state, net, gross, size)
                closed += 1

    return {"opened": opened, "closed": closed}
