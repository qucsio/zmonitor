"""Opportunity lifecycle: open/update/close OpportunityEvent from live fork state,
and write OpportunityEdgePoint only per the write policy (not every tick).

Edge is execution-correct: `net`/`top_net_edge` is the marginal top-of-book edge (net of
the price-dependent Kalshi fee), and `profit_usd` is the integral of marginal net over the
profitable depth of the book (see orderbook._walk_executable). Ranking is by profit_usd."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from scanner.models import OpportunityEdgePoint, OpportunityEvent

BOOK_BAD_FLAGS = {"pm_book_unavailable", "kalshi_book_unavailable",
                  "stale_pm_book", "stale_kalshi_book", "book_crossed_or_invalid"}

# open opportunities with no fresh update for this long are force-closed by the sweep.
STALE_OPP_TIMEOUT_SEC = 180


def _dec(x):
    if x in (None, ""):
        return None
    try:
        return Decimal(str(x))
    except Exception:  # noqa: BLE001
        return None


def _ladder_snapshot(fork):
    """Compact ladder for depth analysis: [size, net, fillable] per rung."""
    return [[r.get("size"), r.get("net"), r.get("fillable")] for r in (fork.get("ladder") or [])]


def _write_point(opp, fork, state, net, gross, size):
    OpportunityEdgePoint.objects.create(
        opportunity=opp,
        gross_edge=gross if gross is not None else Decimal("0"),
        net_edge=net if net is not None else Decimal("0"),
        size_usd=size,
        exec_edge=_dec(fork.get("exec_edge")),
        exec_size_usd=_dec(fork.get("exec_size_usd")),
        profit_usd=_dec(fork.get("profit_usd")),
        pm_vwap=_dec(fork.get("best_pm_vwap")),
        kalshi_vwap=_dec(fork.get("best_kalshi_vwap")),
        pm_price=_dec(fork.get("best_pm_vwap")),
        kalshi_price=_dec(fork.get("best_kalshi_vwap")),
        pm_book_age_ms=state.get("pm_book_age_ms"),
        kalshi_book_age_ms=state.get("kalshi_book_age_ms"),
        risk_flags=state.get("risk_flags") or [],
    )


def _compute_aggregates(opp):
    """Cheap one-shot pass over edge_points at close time. Fills lifetime aggregates
    (duration / time-weighted avg edge & profit / time above threshold) so later analysis
    needs no scan of the edge points."""
    thr = _dec(settings.SCANNER["OPPORTUNITY_NET_EDGE_THRESHOLD"]) or Decimal("0")
    end = opp.ts_end or timezone.now()
    opp.duration_sec = int((end - opp.ts_start).total_seconds())

    pts = list(opp.edge_points.order_by("ts").values_list("ts", "net_edge", "profit_usd"))
    opp.edge_point_count = len(pts)
    if not pts:
        opp.avg_net_edge = opp.last_net_edge
        opp.avg_profit_usd = opp.last_profit_usd
        opp.time_above_threshold_sec = None
        return
    # Time-weighted: each point's value holds until the next point (step function).
    total = w_net = w_profit = above = Decimal("0")
    for i, (ts, net, profit) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else end
        dt = Decimal(str(max((nxt - ts).total_seconds(), 0)))
        total += dt
        w_net += (net or Decimal("0")) * dt
        w_profit += (profit or Decimal("0")) * dt
        if net is not None and net >= thr:
            above += dt
    opp.avg_net_edge = (w_net / total) if total > 0 else pts[-1][1]
    opp.avg_profit_usd = (w_profit / total) if total > 0 else pts[-1][2]
    opp.time_above_threshold_sec = int(above)


def _close_opp(opp, status, close_reason, net=None, gross=None, ts_end=None):
    """Finalize an open opportunity: set end state and compute lifetime aggregates once.
    `ts_end` may be back-dated (sweep) to the last real observation instead of now()."""
    opp.status = status
    opp.ts_end = ts_end or timezone.now()
    opp.close_reason = close_reason
    if net is not None:
        opp.last_net_edge = net
        opp.last_gross_edge = gross
    _compute_aggregates(opp)
    opp.save()


def sweep_stale(timeout_sec=STALE_OPP_TIMEOUT_SEC):
    """Force-close open opportunities that stopped receiving updates. Runs on the reaper
    cadence (not the hot loop), so it costs one query + a few saves. Normal open->close is
    detected at the 1s hot-loop granularity; this only catches opportunities orphaned when
    their pair left the live set (market resolved / archived / close_time passed).

    ts_end is back-dated to ts_last_seen (last real observation) so a 5-second arb that got
    orphaned does not get its duration inflated by the detection lag."""
    cutoff = timezone.now() - timedelta(seconds=timeout_sec)
    stale = OpportunityEvent.objects.filter(status="open", ts_last_seen__lt=cutoff)
    closed = 0
    for opp in stale:
        pair = opp.pair
        market_gone = (pair.status != "matched"
                       or pair.polymarket_market.closed
                       or pair.kalshi_market.closed)
        reason = "market_resolved" if market_gone else "stale_no_updates"
        _close_opp(opp, status="expired", close_reason=reason, ts_end=opp.ts_last_seen)
        closed += 1
    return closed


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
        net = _dec(fork.get("top_net_edge"))
        gross = net  # marginal top-of-book; gross==net at the boundary contract
        size = _dec(fork.get("exec_size_usd")) or Decimal("0")
        profit = _dec(fork.get("profit_usd")) or Decimal("0")

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
                    max_size_usd=size,
                    first_profit_usd=profit, last_profit_usd=profit, max_profit_usd=profit,
                    ladder_open=_ladder_snapshot(fork),
                    ladder_max=_ladder_snapshot(fork),
                    risk_flags=risk)
                _write_point(opp, fork, state, net, gross, size)
                opened += 1
            else:
                new_max_edge = net > (opp.max_net_edge if opp.max_net_edge is not None else Decimal("-1"))
                new_max_profit = profit > (opp.max_profit_usd if opp.max_profit_usd is not None else Decimal("-1"))
                opp.last_gross_edge = gross
                opp.last_net_edge = net
                opp.last_profit_usd = profit
                if new_max_edge:
                    opp.max_net_edge = net
                    opp.max_gross_edge = gross
                if size > (opp.max_size_usd or Decimal("0")):
                    opp.max_size_usd = size
                if new_max_profit:
                    opp.max_profit_usd = profit
                    opp.ladder_max = _ladder_snapshot(fork)  # snapshot at peak profit
                opp.save()
                last = opp.edge_points.order_by("-ts").first()
                due = last is None
                if last is not None:
                    age = (timezone.now() - last.ts).total_seconds()
                    due = (age >= interval
                           or abs(net - last.net_edge) >= delta
                           or new_max_edge or new_max_profit)
                if due:
                    _write_point(opp, fork, state, net, gross, size)
        else:
            if opp is not None:
                _write_point(opp, fork, state, net, gross, size)
                _close_opp(opp,
                           status="stale" if book_bad else "closed",
                           close_reason="book_stale" if book_bad else "below_threshold",
                           net=net, gross=gross)
                closed += 1

    return {"opened": opened, "closed": closed}
