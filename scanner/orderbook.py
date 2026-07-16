"""Orderbook fetch + normalization + VWAP + buy/buy fork calculation.

All money math uses Decimal. Kalshi returns bids only (yes_dollars / no_dollars);
asks are reconstructed as (1 - opposite_side_bid).
"""
import json
import logging
import time
from decimal import Decimal, getcontext

import redis
from django.conf import settings
from django.utils import timezone

from scanner.clients import kalshi as kalshi_client
from scanner.clients import polymarket as pm_client
from scanner.models import MatchedPair

getcontext().prec = 28
ONE = Decimal("1")
logger = logging.getLogger("scanner")


def _dec(x):
    try:
        return Decimal(str(x))
    except Exception:  # noqa: BLE001
        return None


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------- level parsing
def _pm_levels(book):
    """Polymarket book -> (bids desc, asks asc) as [(price, size)]."""
    bids = sorted(((_dec(b["price"]), _dec(b["size"])) for b in (book or {}).get("bids", [])),
                  key=lambda x: x[0], reverse=True)
    asks = sorted(((_dec(a["price"]), _dec(a["size"])) for a in (book or {}).get("asks", [])),
                  key=lambda x: x[0])
    return bids, asks


def _kalshi_bid_levels(ob):
    """Kalshi orderbook -> (yes_bids, no_bids) each [(price, size)] sorted desc by price."""
    o = (ob or {}).get("orderbook_fp") or (ob or {}).get("orderbook") or {}
    yes = o.get("yes_dollars") or o.get("yes") or []
    no = o.get("no_dollars") or o.get("no") or []

    def parse(arr):
        out = []
        for row in arr:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                p, s = _dec(row[0]), _dec(row[1])
                if p is not None and s is not None:
                    out.append((p, s))
        return sorted(out, key=lambda x: x[0], reverse=True)

    return parse(yes), parse(no)


def _reconstruct_asks(opposite_bids):
    """A bid of price q on the opposite side = an ask at (1 - q) on this side."""
    return sorted(((ONE - p, s) for p, s in opposite_bids), key=lambda x: x[0])


def _best_bid(bids):
    return bids[0][0] if bids else None


def _best_ask(asks):
    return asks[0][0] if asks else None


def vwap_buy(asks, target):
    """Average price to buy `target` contracts walking ascending-price asks.
    Returns (avg_price, filled_contracts)."""
    if not asks or target <= 0:
        return None, Decimal("0")
    filled = Decimal("0")
    cost = Decimal("0")
    for price, size in asks:
        take = min(size, target - filled)
        if take <= 0:
            break
        cost += take * price
        filled += take
        if filled >= target:
            break
    if filled <= 0:
        return None, Decimal("0")
    return (cost / filled), filled


def _depth(asks):
    return sum((s for _, s in asks), Decimal("0"))


# ---------------------------------------------------------------- fetch
def fetch_pair_books(pair: MatchedPair):
    """Fetch raw books for a pair. Returns dict with normalized levels + ages, or None."""
    pm = pair.polymarket_market
    outs = {o.outcome_side: o for o in pm.outcomes.all()}
    yes_token = outs.get("yes").token_id if outs.get("yes") else None
    no_token = outs.get("no").token_id if outs.get("no") else None
    if not (yes_token and no_token):
        return None

    now_ms = int(time.time() * 1000)
    ry = pm_client.get_book(yes_token)
    rn = pm_client.get_book(no_token)
    # Both books 404 -> market resolved/removed; self-prune so it stops being active.
    if ry.status_code == 404 and rn.status_code == 404:
        if pm.closed is not True:
            pm.closed = True
            pm.save(update_fields=["closed"])
        return None
    pm_yes_bids, pm_yes_asks = _pm_levels(ry.data if ry.ok else {})
    pm_no_bids, pm_no_asks = _pm_levels(rn.data if rn.ok else {})
    pm_ts = _dec((ry.data or {}).get("timestamp")) if ry.ok else None
    pm_age = (now_ms - int(pm_ts)) if pm_ts else None

    rk = kalshi_client.get_orderbook(pair.kalshi_market.venue_market_id)
    k_yes_bids, k_no_bids = _kalshi_bid_levels(rk.data if rk.ok else {})
    k_yes_asks = _reconstruct_asks(k_no_bids)   # buy YES = 1 - best NO bid
    k_no_asks = _reconstruct_asks(k_yes_bids)   # buy NO  = 1 - best YES bid

    return {
        "pm_yes_bids": pm_yes_bids, "pm_yes_asks": pm_yes_asks,
        "pm_no_bids": pm_no_bids, "pm_no_asks": pm_no_asks,
        "k_yes_bids": k_yes_bids, "k_no_bids": k_no_bids,
        "k_yes_asks": k_yes_asks, "k_no_asks": k_no_asks,
        "pm_ok": ry.ok and rn.ok, "kalshi_ok": rk.ok,
        "pm_book_age_ms": pm_age, "kalshi_book_age_ms": None,
    }


# ---------------------------------------------------------------- forks
def _fork(pm_leg_asks, kalshi_leg_asks, sizes, fee, slip):
    """Compute a buy/buy fork over the size ladder. Returns dict."""
    ladder = []
    best_net = best_gross = best_pm = best_kalshi = None
    max_size = Decimal("0")
    for size in sizes:
        pm_p, pm_f = vwap_buy(pm_leg_asks, size)
        k_p, k_f = vwap_buy(kalshi_leg_asks, size)
        if pm_p is None or k_p is None:
            ladder.append({"size": str(size), "cost": None, "gross": None, "net": None,
                           "fillable": str(min(pm_f, k_f))})
            continue
        fillable = min(pm_f, k_f)
        cost = pm_p + k_p
        gross = ONE - cost
        net = gross - fee - slip
        ladder.append({
            "size": str(size), "pm_vwap": str(pm_p), "kalshi_vwap": str(k_p),
            "cost": str(cost), "gross": str(gross), "net": str(net),
            "fillable": str(fillable),
        })
        if fillable >= size:
            max_size = size
        if best_net is None:
            best_net, best_gross = net, gross           # smallest size = top of book
            best_pm, best_kalshi = pm_p, k_p
    return {
        "best_net_edge": str(best_net) if best_net is not None else None,
        "best_gross_edge": str(best_gross) if best_gross is not None else None,
        "best_pm_vwap": str(best_pm) if best_pm is not None else None,
        "best_kalshi_vwap": str(best_kalshi) if best_kalshi is not None else None,
        "max_size_usd": str(max_size),
        "ladder": ladder,
        "pm_depth": str(_depth(pm_leg_asks)),
        "kalshi_depth": str(_depth(kalshi_leg_asks)),
    }


def compute_forks(pair: MatchedPair, books, fee=None, slip=None):
    """Fork A: PM-yes + Kalshi-otherside ; Fork B: PM-no + Kalshi-otherside.
    Direction respects outcome_mapping (which Kalshi side is the same team as pm_yes)."""
    fee = fee if fee is not None else settings.SCANNER["DEFAULT_FEE_BUFFER"]
    slip = slip if slip is not None else settings.SCANNER["DEFAULT_SLIPPAGE_BUFFER"]
    sizes = settings.SCANNER["VWAP_SIZES_USD"]

    direct = pair.outcome_mapping.get("pm_yes") == "kalshi_yes"

    # Fork A covers team_a via PM-yes; the complementary Kalshi leg pays if team_b wins.
    a_kalshi = books["k_no_asks"] if direct else books["k_yes_asks"]
    fork_a = _fork(books["pm_yes_asks"], a_kalshi, sizes, fee, slip)
    fork_a["direction"] = "pm_yes_kalshi_" + ("no" if direct else "yes")

    b_kalshi = books["k_yes_asks"] if direct else books["k_no_asks"]
    fork_b = _fork(books["pm_no_asks"], b_kalshi, sizes, fee, slip)
    fork_b["direction"] = "pm_no_kalshi_" + ("yes" if direct else "no")

    return fork_a, fork_b


# ---------------------------------------------------------------- orchestration
def _risk_flags(books):
    flags = []
    if not books["pm_ok"]:
        flags.append("pm_book_unavailable")
    if not books["kalshi_ok"]:
        flags.append("kalshi_book_unavailable")
    # NB: PM book "timestamp" is the book's last-change time, not our fetch time.
    # In REST mode the snapshot is always current, so we do NOT flag stale from it.
    # Real staleness (missed WS updates / resync gaps) is handled in ws/hybrid mode.
    if settings.SCANNER["ORDERBOOK_MODE"] != "rest":
        stale = settings.SCANNER["STALE_BOOK_MS"]
        if books["pm_book_age_ms"] and books["pm_book_age_ms"] > stale:
            flags.append("stale_pm_book")
    return flags


def process_pair(pair: MatchedPair):
    """Fetch, compute forks, write Redis latest_pair_state. Returns the state dict."""
    books = fetch_pair_books(pair)
    if books is None:
        return None
    fork_a, fork_b = compute_forks(pair, books)
    flags = _risk_flags(books)

    state = {
        "pair_id": pair.id,
        "updated_at": timezone.now().isoformat(),
        "pm_yes_bid": str(_best_bid(books["pm_yes_bids"]) or ""),
        "pm_yes_ask": str(_best_ask(books["pm_yes_asks"]) or ""),
        "pm_no_bid": str(_best_bid(books["pm_no_bids"]) or ""),
        "pm_no_ask": str(_best_ask(books["pm_no_asks"]) or ""),
        "kalshi_yes_bid": str(_best_bid(books["k_yes_bids"]) or ""),
        "kalshi_yes_ask": str(_best_ask(books["k_yes_asks"]) or ""),
        "kalshi_no_bid": str(_best_bid(books["k_no_bids"]) or ""),
        "kalshi_no_ask": str(_best_ask(books["k_no_asks"]) or ""),
        "fork_a": fork_a,
        "fork_b": fork_b,
        "risk_flags": flags,
        "pm_book_age_ms": books["pm_book_age_ms"],
        "kalshi_book_age_ms": books["kalshi_book_age_ms"],
    }
    try:
        _redis().set(f"latest_pair_state:{pair.id}", json.dumps(state), ex=3600)
    except Exception:  # noqa: BLE001
        logger.exception("failed writing latest_pair_state")

    try:
        from scanner import opportunities
        opportunities.process(pair, state)
    except Exception:  # noqa: BLE001
        logger.exception("opportunity processing failed for %s", pair.id)
    return state


def get_pair_state(pair_id):
    try:
        raw = _redis().get(f"latest_pair_state:{pair_id}")
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def _best_net(state):
    if not state:
        return None
    vals = []
    for f in ("fork_a", "fork_b"):
        v = state.get(f, {}).get("best_net_edge")
        if v is not None:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
    return max(vals) if vals else None


def _poll_interval(state):
    """Tiered polling: near-arb pairs fast, deeply-negative pairs slow. Keeps the
    request rate sane (163 pairs * 1s would blow past venue rate limits)."""
    if state is None:
        return 0  # never polled -> poll now
    best = _best_net(state)
    thr = float(settings.SCANNER["OPPORTUNITY_NET_EDGE_THRESHOLD"])
    if best is None:
        return 30
    if best >= thr - 0.02:       # hot: at/near an opportunity
        return settings.SCANNER["ORDERBOOK_REFRESH_SEC"]
    if best >= thr - 0.10:       # warm
        return 15
    return 60                    # cold


def _is_due(state):
    if state is None:
        return True
    interval = _poll_interval(state)
    try:
        from django.utils.dateparse import parse_datetime
        age = (timezone.now() - parse_datetime(state["updated_at"])).total_seconds()
        return age >= interval
    except Exception:  # noqa: BLE001
        return True


def process_matched_pairs(limit=None, respect_tiers=True):
    from django.db.models import Q

    now = timezone.now()
    qs = (MatchedPair.objects.filter(
        status="matched", kalshi_market__closed=False, polymarket_market__closed=False)
        .filter(Q(kalshi_market__close_time__gte=now) | Q(kalshi_market__close_time__isnull=True))
        .filter(Q(polymarket_market__close_time__gte=now) | Q(polymarket_market__close_time__isnull=True))
        .select_related("polymarket_market", "kalshi_market")
        .order_by("polymarket_market__close_time"))
    if limit:
        qs = qs[:limit]
    results = []
    for pair in qs:
        try:
            if respect_tiers and not _is_due(get_pair_state(pair.id)):
                continue
            state = process_pair(pair)
            if state:
                results.append({
                    "id": pair.id,
                    "fork_a_net": state["fork_a"].get("best_net_edge"),
                    "fork_b_net": state["fork_b"].get("best_net_edge"),
                })
        except Exception:  # noqa: BLE001
            logger.exception("process_pair failed for %s", pair.id)
    return results
