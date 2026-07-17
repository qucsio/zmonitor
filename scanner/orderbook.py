"""Orderbook fetch + normalization + VWAP + buy/buy fork calculation.

All money math uses Decimal. Kalshi returns bids only (yes_dollars / no_dollars);
asks are reconstructed as (1 - opposite_side_bid).
"""
import json
import logging
import time
from decimal import ROUND_CEILING, Decimal, getcontext

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


# ---------------------------------------------------------------- fees
def _kalshi_fee_rate(price):
    """Kalshi per-contract fee *rate* (unrounded): rate * P * (1-P). The venue rounds the
    aggregate up to the cent; we integrate the raw rate and round the total in the walk."""
    if price is None:
        return Decimal("0")
    rate = settings.SCANNER["KALSHI_FEE_RATE"]
    return rate * price * (ONE - price)


def _round_up_cent(x):
    return (x * 100).to_integral_value(rounding=ROUND_CEILING) / 100 if x > 0 else Decimal("0")


# ---------------------------------------------------------------- executable walk
def _walk_executable(pm_asks, kalshi_asks, slip_per_contract):
    """Walk both ask ladders contract-by-contract (in constant-price level chunks) and add
    contracts while the *marginal* net edge stays >= 0. This is the execution-correct notion
    of arb size: blended VWAP would keep averaging in losing contracts, this stops at the
    real boundary. Kalshi fee is price-dependent (applied per level); PM CLOB is 0%.
    Returns market-side numbers (no capital cap; balance is applied later at entry)."""
    i = j = 0
    pm_rem = pm_asks[0][1] if pm_asks else Decimal("0")
    k_rem = kalshi_asks[0][1] if kalshi_asks else Decimal("0")
    contracts = Decimal("0")
    gross_sum = Decimal("0")          # Σ (1 - pm - k) over profitable contracts
    fee_raw = Decimal("0")            # Σ raw kalshi fee (rounded once at the end)
    pm_cost = k_cost = Decimal("0")
    top_net = None

    while i < len(pm_asks) and j < len(kalshi_asks):
        pm_p, k_p = pm_asks[i][0], kalshi_asks[j][0]
        marg_gross = ONE - pm_p - k_p
        marg_fee = _kalshi_fee_rate(k_p)
        marg_net = marg_gross - marg_fee - slip_per_contract
        if marg_net <= 0:
            break
        if top_net is None:
            top_net = marg_net
        chunk = min(pm_rem, k_rem)
        if chunk <= 0:
            break
        contracts += chunk
        gross_sum += marg_gross * chunk
        fee_raw += marg_fee * chunk
        pm_cost += pm_p * chunk
        k_cost += k_p * chunk
        pm_rem -= chunk
        k_rem -= chunk
        if pm_rem <= 0:
            i += 1
            pm_rem = pm_asks[i][1] if i < len(pm_asks) else Decimal("0")
        if k_rem <= 0:
            j += 1
            k_rem = kalshi_asks[j][1] if j < len(kalshi_asks) else Decimal("0")

    fee_total = _round_up_cent(fee_raw)
    slip_total = slip_per_contract * contracts
    profit = gross_sum - fee_total - slip_total
    exec_edge = (profit / contracts) if contracts > 0 else None
    return {
        "top_net_edge": top_net,          # marginal edge at top of book (best achievable)
        "exec_contracts": contracts,      # profitable depth in contracts
        "exec_size_usd": pm_cost + k_cost,  # capital deployed to take that depth
        "exec_edge": exec_edge,           # blended net over the profitable region
        "profit_usd": profit,             # Σ marginal net = max extractable $ (market-side)
        "top_pm_price": pm_asks[0][0] if pm_asks else None,
        "top_kalshi_price": kalshi_asks[0][0] if kalshi_asks else None,
    }


# ---------------------------------------------------------------- forks
def _fork(pm_leg_asks, kalshi_leg_asks, sizes, slip_per_contract):
    """Compute a buy/buy fork. `ladder` (blended VWAP per $-rung) is for display only;
    the headline exec_* / profit numbers come from the marginal book walk."""
    ladder = []
    for size in sizes:
        pm_p, pm_f = vwap_buy(pm_leg_asks, size)
        k_p, k_f = vwap_buy(kalshi_leg_asks, size)
        if pm_p is None or k_p is None:
            ladder.append({"size": str(size), "cost": None, "gross": None, "net": None,
                           "fillable": str(min(pm_f, k_f))})
            continue
        cost = pm_p + k_p
        gross = ONE - cost
        # blended net incl. price-dependent kalshi fee at the blended kalshi price
        net = gross - _kalshi_fee_rate(k_p) - slip_per_contract
        ladder.append({
            "size": str(size), "pm_vwap": str(pm_p), "kalshi_vwap": str(k_p),
            "cost": str(cost), "gross": str(gross), "net": str(net),
            "fillable": str(min(pm_f, k_f)),
        })

    ex = _walk_executable(pm_leg_asks, kalshi_leg_asks, slip_per_contract)

    def s(v):
        return str(v) if v is not None else None

    return {
        # headline: marginal top-of-book edge + executable depth/profit
        "top_net_edge": s(ex["top_net_edge"]),
        "exec_contracts": s(ex["exec_contracts"]),
        "exec_size_usd": s(ex["exec_size_usd"]),
        "exec_edge": s(ex["exec_edge"]),
        "profit_usd": s(ex["profit_usd"]),
        "best_pm_vwap": s(ex["top_pm_price"]),
        "best_kalshi_vwap": s(ex["top_kalshi_price"]),
        # compat: keep old keys pointing at the execution-correct values
        "best_net_edge": s(ex["top_net_edge"]),
        "best_gross_edge": s(ex["top_net_edge"]),
        "max_size_usd": s(ex["exec_size_usd"]),
        "ladder": ladder,
        "pm_depth": str(_depth(pm_leg_asks)),
        "kalshi_depth": str(_depth(kalshi_leg_asks)),
    }


def compute_forks(pair: MatchedPair, books, slip=None):
    """Fork A: PM-yes + Kalshi-otherside ; Fork B: PM-no + Kalshi-otherside.
    Direction respects outcome_mapping (which Kalshi side is the same team as pm_yes)."""
    slip = slip if slip is not None else settings.SCANNER["SLIPPAGE_PER_CONTRACT"]
    sizes = settings.SCANNER["VWAP_SIZES_USD"]

    direct = pair.outcome_mapping.get("pm_yes") == "kalshi_yes"

    # Fork A covers team_a via PM-yes; the complementary Kalshi leg pays if team_b wins.
    a_kalshi = books["k_no_asks"] if direct else books["k_yes_asks"]
    fork_a = _fork(books["pm_yes_asks"], a_kalshi, sizes, slip)
    fork_a["direction"] = "pm_yes_kalshi_" + ("no" if direct else "yes")

    b_kalshi = books["k_yes_asks"] if direct else books["k_no_asks"]
    fork_b = _fork(books["pm_no_asks"], b_kalshi, sizes, slip)
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
