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


def _series_of(event_ticker, selected):
    """A Kalshi event_ticker is '<SERIES>-<...>'; series tickers may contain dashes."""
    for st in selected:
        if event_ticker == st or event_ticker.startswith(st + "-"):
            return True
    return False


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


def _save_event_stub(event_ticker, seen_events):
    if event_ticker in seen_events:
        return
    seen_events.add(event_ticker)
    upsert_event(VENUE_KALSHI, event_ticker, {
        "title": event_ticker,
        "sport": event_ticker.split("-", 1)[0],
        "raw_json": {"event_ticker": event_ticker},
    })


def discover(page_size=1000):
    """Flat scan of open markets, filtered client-side to selected sports/esports series.
    Far fewer requests than per-series fetching (Kalshi has hundreds of sports series)."""
    max_pages = max(settings.SCANNER["DISCOVERY_MAX_PAGES"], 100)
    throttle = settings.SCANNER["DISCOVERY_PAGE_THROTTLE_MS"] / 1000.0
    counters = {"markets_seen": 0, "markets_new": 0, "markets_updated": 0}

    selected = _selected_series()
    logger.info("kalshi: %d series selected for discovery", len(selected))
    if not selected:
        return counters

    seen_events = set()
    cursor = None
    for _ in range(max_pages):
        r = kalshi_client.get_markets(limit=page_size, cursor=cursor, status="open")
        if not r.ok or not isinstance(r.data, dict):
            logger.warning("kalshi markets fetch failed: %s", r.error)
            break
        markets = r.data.get("markets", [])
        if not markets:
            break
        for m in markets:
            if not isinstance(m, dict) or not m.get("ticker"):
                continue
            event_ticker = m.get("event_ticker") or ""
            if not _series_of(event_ticker, selected):
                continue
            _save_event_stub(event_ticker, seen_events)
            counters["markets_seen"] += 1
            if _save_market(m, event_ticker):
                counters["markets_new"] += 1
            else:
                counters["markets_updated"] += 1
        cursor = r.data.get("cursor")
        if not cursor:
            break
        if throttle:
            time.sleep(throttle)
    return counters
