import logging
import time

from django.conf import settings

from scanner.clients import kalshi as kalshi_client
from scanner.models import VENUE_KALSHI

from .common import parse_dt, rules_hash, upsert_event, upsert_market, upsert_outcome

logger = logging.getLogger("scanner")

OPEN_STATES = {"active", "open"}
CLOSED_STATES = {"closed", "settled", "finalized", "determined"}


def _series_matches(s, wanted):
    """Exact category match; esports (no distinct category) caught by keyword."""
    cat = (s.get("category") or "").strip().lower()
    if cat and cat in wanted:
        return True
    hay = " ".join(filter(None, [s.get("ticker"), s.get("title")])).lower()
    return any(w in hay for w in wanted if w != "sports")


def _selected_series():
    """One /series call; return the set of series tickers in wanted categories."""
    wanted = settings.SCANNER["DISCOVERY_KALSHI_CATEGORIES"]
    r = kalshi_client.get_series()
    if not r.ok or not isinstance(r.data, dict):
        logger.warning("kalshi series fetch failed: %s", r.error)
        return set()
    series = r.data.get("series") or r.data.get("series_list") or []
    return {s.get("ticker") for s in series
            if isinstance(s, dict) and s.get("ticker") and _series_matches(s, wanted)}


def _series_of(event_ticker, selected):
    for st in selected:
        if event_ticker == st or event_ticker.startswith(st + "-"):
            return True
    return False


def _event_matches(ev, wanted, selected):
    cat = (ev.get("category") or "").strip().lower()
    if cat and cat in wanted:
        return True
    if _series_of(ev.get("event_ticker") or "", selected):
        return True
    hay = " ".join(filter(None, [
        ev.get("event_ticker"), ev.get("title"), ev.get("series_ticker"),
    ])).lower()
    return any(w in hay for w in wanted if w != "sports")


def _save_event(ev):
    upsert_event(VENUE_KALSHI, ev.get("event_ticker"), {
        "title": ev.get("title") or ev.get("sub_title"),
        "category": ev.get("category"),
        "sport": ev.get("series_ticker"),
        "status": ev.get("status"),
        "raw_json": ev,
    })


def _save_market(m, event_ticker):
    status = m.get("status")
    rules_text = " ".join(filter(None, [m.get("rules_primary"), m.get("rules_secondary")])) or None
    is_mve = bool(m.get("mve_selected_legs") or m.get("mve_collection_ticker"))

    market, created = upsert_market(VENUE_KALSHI, m.get("ticker"), {
        "venue_event_id": m.get("event_ticker") or event_ticker,
        "title": m.get("title"),
        "question": m.get("yes_sub_title") or m.get("title"),
        "rules_text": rules_text,
        "rules_hash": rules_hash(rules_text),
        "status": status,
        "active": status in OPEN_STATES,
        "closed": status in CLOSED_STATES,
        "archived": False,
        "accepting_orders": status in OPEN_STATES,
        "enable_orderbook": not is_mve,
        "start_time": parse_dt(m.get("open_time")),
        "close_time": parse_dt(m.get("close_time")),
        "raw_json": m,
        "updated_at_remote": parse_dt(m.get("updated_time")),
    })

    upsert_outcome(market, "yes", {
        "venue": VENUE_KALSHI, "outcome_name": m.get("yes_sub_title") or "Yes",
        "ticker": m.get("ticker"), "token_id": None, "raw_json": None,
    })
    upsert_outcome(market, "no", {
        "venue": VENUE_KALSHI, "outcome_name": m.get("no_sub_title") or "No",
        "ticker": m.get("ticker"), "token_id": None, "raw_json": None,
    })
    return created


def discover(page_size=200, incremental=True):
    """Events-only discovery (lightweight). Markets are fetched lazily at matching time.
    incremental=True stops after a few consecutive pages with no NEW matched events
    (events are newest-first, so new ones cluster at the front)."""
    max_pages = max(settings.SCANNER["DISCOVERY_MAX_PAGES"], 200)
    throttle = settings.SCANNER["DISCOVERY_PAGE_THROTTLE_MS"] / 1000.0
    wanted = settings.SCANNER["DISCOVERY_KALSHI_CATEGORIES"]
    counters = {"markets_seen": 0, "markets_new": 0, "markets_updated": 0}

    selected = _selected_series()
    logger.info("kalshi: %d series in wanted categories", len(selected))

    cursor = None
    empty_streak = 0
    for _ in range(max_pages):
        r = kalshi_client.get_events(limit=page_size, cursor=cursor, status="open")
        if not r.ok or not isinstance(r.data, dict):
            logger.warning("kalshi events fetch failed: %s", r.error)
            break
        events = r.data.get("events", [])
        if not events:
            break
        page_new = 0
        for ev in events:
            if not isinstance(ev, dict) or not ev.get("event_ticker"):
                continue
            if not _event_matches(ev, wanted, selected):
                continue
            counters["markets_seen"] += 1
            _, created = upsert_event(VENUE_KALSHI, ev.get("event_ticker"), {
                "title": ev.get("title") or ev.get("sub_title"),
                "category": ev.get("category"),
                "sport": ev.get("series_ticker"),
                "status": ev.get("status"),
                "raw_json": ev,
            })
            if created:
                counters["markets_new"] += 1
                page_new += 1
            else:
                counters["markets_updated"] += 1
        empty_streak = empty_streak + 1 if page_new == 0 else 0
        if incremental and empty_streak >= 3:
            break
        cursor = r.data.get("cursor")
        if not cursor:
            break
        if throttle:
            time.sleep(throttle)
    return counters


def fetch_markets_for_event(event_ticker, page_size=1000):
    """Lazily pull and store all markets of a single Kalshi event. Used by matching.
    Returns number of markets saved."""
    saved = 0
    cursor = None
    for _ in range(20):
        # status=None -> all statuses, so resolved markets flip closed=True
        r = kalshi_client.get_markets(limit=page_size, cursor=cursor, status=None,
                                      params={"event_ticker": event_ticker})
        if not r.ok or not isinstance(r.data, dict):
            logger.warning("kalshi markets fetch failed (%s): %s", event_ticker, r.error)
            break
        markets = r.data.get("markets", [])
        for m in markets:
            if isinstance(m, dict) and m.get("ticker"):
                _save_market(m, event_ticker)
                saved += 1
        cursor = r.data.get("cursor")
        if not cursor:
            break
    return saved
