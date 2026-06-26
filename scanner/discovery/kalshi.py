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
    if not wanted:
        return True
    haystack = " ".join(filter(None, [
        s.get("category"), s.get("ticker"), s.get("title"),
        " ".join(s.get("tags") or []) if isinstance(s.get("tags"), list) else s.get("tags"),
    ])).lower()
    return any(w in haystack for w in wanted)


def _selected_series():
    """One /series call, filter client-side by configured categories/keywords."""
    wanted = settings.SCANNER["DISCOVERY_KALSHI_CATEGORIES"]
    r = kalshi_client.get_series()
    if not r.ok or not isinstance(r.data, dict):
        logger.warning("kalshi series fetch failed: %s", r.error)
        return []
    series = r.data.get("series") or r.data.get("series_list") or []
    return [s.get("ticker") for s in series
            if isinstance(s, dict) and s.get("ticker") and _series_matches(s, wanted)]


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


def _discover_series(series_ticker, page_size, max_pages, throttle, counters):
    cursor = None
    for _ in range(max_pages):
        r = kalshi_client.get_events(limit=page_size, cursor=cursor, status="open", params={
            "series_ticker": series_ticker, "with_nested_markets": "true",
        })
        if not r.ok or not isinstance(r.data, dict):
            logger.warning("kalshi events fetch failed (%s): %s", series_ticker, r.error)
            break
        events = r.data.get("events", [])
        if not events:
            break
        for ev in events:
            if not isinstance(ev, dict):
                continue
            _save_event(ev)
            for m in ev.get("markets") or []:
                if not isinstance(m, dict) or not m.get("ticker"):
                    continue
                counters["markets_seen"] += 1
                if _save_market(m, ev.get("event_ticker")):
                    counters["markets_new"] += 1
                else:
                    counters["markets_updated"] += 1
        cursor = r.data.get("cursor")
        if not cursor:
            break
        if throttle:
            time.sleep(throttle)


def discover(page_size=200):
    """Server-side filtered discovery: select sports/esports series, then their events."""
    max_pages = settings.SCANNER["DISCOVERY_MAX_PAGES"]
    throttle = settings.SCANNER["DISCOVERY_PAGE_THROTTLE_MS"] / 1000.0
    counters = {"markets_seen": 0, "markets_new": 0, "markets_updated": 0}

    series = _selected_series()
    logger.info("kalshi: %d series selected for discovery", len(series))
    for st in series:
        _discover_series(st, page_size, max_pages, throttle, counters)
        if throttle:
            time.sleep(throttle)
    return counters
