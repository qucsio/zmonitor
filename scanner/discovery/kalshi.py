import logging

from scanner.clients import kalshi as kalshi_client
from scanner.models import VENUE_KALSHI

from .common import parse_dt, rules_hash, upsert_event, upsert_market, upsert_outcome

logger = logging.getLogger("scanner")

OPEN_STATES = {"active", "open"}
CLOSED_STATES = {"closed", "settled", "finalized", "determined"}


def _save_event(ev):
    if not ev:
        return
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


def _save_market(m):
    status = m.get("status")
    rules_text = " ".join(filter(None, [m.get("rules_primary"), m.get("rules_secondary")])) or None
    is_mve = bool(m.get("mve_selected_legs") or m.get("mve_collection_ticker"))

    market, created = upsert_market(
        VENUE_KALSHI,
        m.get("ticker"),
        {
            "venue_event_id": m.get("event_ticker"),
            "title": m.get("title"),
            "question": m.get("yes_sub_title") or m.get("title"),
            "rules_text": rules_text,
            "rules_hash": rules_hash(rules_text),
            "status": status,
            "active": status in OPEN_STATES,
            "closed": status in CLOSED_STATES,
            "archived": False,
            "accepting_orders": status in OPEN_STATES,
            "enable_orderbook": not is_mve,  # multivariate combos have no standalone book
            "start_time": parse_dt(m.get("open_time")),
            "close_time": parse_dt(m.get("close_time")),
            "raw_json": m,
            "updated_at_remote": parse_dt(m.get("updated_time")),
        },
    )

    upsert_outcome(market, "yes", {
        "venue": VENUE_KALSHI,
        "outcome_name": m.get("yes_sub_title") or "Yes",
        "ticker": m.get("ticker"),
        "token_id": None,
        "raw_json": None,
    })
    upsert_outcome(market, "no", {
        "venue": VENUE_KALSHI,
        "outcome_name": m.get("no_sub_title") or "No",
        "ticker": m.get("ticker"),
        "token_id": None,
        "raw_json": None,
    })
    return created


def _discover_events(max_pages=20, page_size=200):
    cursor = None
    for _ in range(max_pages):
        r = kalshi_client.get_events(limit=page_size, cursor=cursor, status="open")
        if not r.ok or not isinstance(r.data, dict):
            break
        for ev in r.data.get("events", []):
            _save_event(ev)
        cursor = r.data.get("cursor")
        if not cursor:
            break


def discover(max_pages=50, page_size=200):
    """Page through Kalshi open markets via cursor pagination. Returns counts."""
    try:
        _discover_events()
    except Exception:  # noqa: BLE001
        logger.exception("kalshi events discovery failed (non-fatal)")

    seen = new = updated = 0
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
            seen += 1
            created = _save_market(m)
            if created:
                new += 1
            else:
                updated += 1
        cursor = r.data.get("cursor")
        if not cursor:
            break
    return {"markets_seen": seen, "markets_new": new, "markets_updated": updated}
