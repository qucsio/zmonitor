import logging
import time

from django.conf import settings

from scanner.clients import kalshi as kalshi_client
from scanner.models import VENUE_KALSHI

from .common import parse_dt, rules_hash, upsert_event, upsert_market, upsert_outcome

logger = logging.getLogger("scanner")

OPEN_STATES = {"active", "open"}
CLOSED_STATES = {"closed", "settled", "finalized", "determined"}


def _event_matches(ev):
    wanted = settings.SCANNER["DISCOVERY_KALSHI_CATEGORIES"]
    if not wanted:
        return True
    haystack = " ".join(filter(None, [
        ev.get("category"), ev.get("series_ticker"), ev.get("event_ticker"), ev.get("title"),
    ])).lower()
    return any(w in haystack for w in wanted)


def _save_event(ev):
    upsert_event(
        VENUE_KALSHI,
        ev.get("event_ticker"),
        {
            "title": ev.get("title") or ev.get("sub_title"),
            "category": ev.get("category"),
            "sport": ev.get("series_ticker"),
            "status": ev.get("status"),
            "raw_json": ev,
        },
    )


def _save_market(m, event_ticker):
    status = m.get("status")
    rules_text = " ".join(filter(None, [m.get("rules_primary"), m.get("rules_secondary")])) or None
    is_mve = bool(m.get("mve_selected_legs") or m.get("mve_collection_ticker"))

    market, created = upsert_market(
        VENUE_KALSHI,
        m.get("ticker"),
        {
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
        },
    )

    upsert_outcome(market, "yes", {
        "venue": VENUE_KALSHI, "outcome_name": m.get("yes_sub_title") or "Yes",
        "ticker": m.get("ticker"), "token_id": None, "raw_json": None,
    })
    upsert_outcome(market, "no", {
        "venue": VENUE_KALSHI, "outcome_name": m.get("no_sub_title") or "No",
        "ticker": m.get("ticker"), "token_id": None, "raw_json": None,
    })
    return created


def discover(page_size=200):
    """Events-driven discovery (nested markets) filtered to configured categories."""
    max_pages = settings.SCANNER["DISCOVERY_MAX_PAGES"]
    throttle = settings.SCANNER["DISCOVERY_PAGE_THROTTLE_MS"] / 1000.0
    seen = new = updated = 0
    cursor = None

    for _ in range(max_pages):
        r = kalshi_client.get_events(
            limit=page_size, cursor=cursor, status="open",
            params={"with_nested_markets": "true"},
        )
        if not r.ok or not isinstance(r.data, dict):
            logger.warning("kalshi events fetch failed: %s", r.error)
            break
        events = r.data.get("events", [])
        if not events:
            break
        for ev in events:
            if not isinstance(ev, dict) or not _event_matches(ev):
                continue
            _save_event(ev)
            for m in ev.get("markets") or []:
                if not isinstance(m, dict) or not m.get("ticker"):
                    continue
                seen += 1
                if _save_market(m, ev.get("event_ticker")):
                    new += 1
                else:
                    updated += 1
        cursor = r.data.get("cursor")
        if not cursor:
            break
        if throttle:
            time.sleep(throttle)
    return {"markets_seen": seen, "markets_new": new, "markets_updated": updated}
