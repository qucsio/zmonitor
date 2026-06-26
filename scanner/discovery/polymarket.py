import json
import logging

from scanner.clients import polymarket as pm_client
from scanner.models import VENUE_POLYMARKET

from .common import parse_dt, rules_hash, upsert_event, upsert_market, upsert_outcome

logger = logging.getLogger("scanner")


def _loads(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _is_usable(m):
    return bool(
        m.get("active")
        and not m.get("closed")
        and not m.get("archived")
        and m.get("enableOrderBook")
        and m.get("acceptingOrders")
        and m.get("clobTokenIds")
    )


def _save_event(ev):
    if not ev:
        return
    upsert_event(
        VENUE_POLYMARKET,
        ev.get("id"),
        {
            "title": ev.get("title"),
            "category": (ev.get("tags") or [{}])[0].get("label") if ev.get("tags") else None,
            "sport": None,
            "status": "closed" if ev.get("closed") else "active",
            "start_time": parse_dt(ev.get("startDate")),
            "end_time": parse_dt(ev.get("endDate")),
            "raw_json": ev,
            "updated_at_remote": parse_dt(ev.get("updatedAt")),
        },
    )


def _save_market(m):
    events = m.get("events") or []
    event = events[0] if events else None
    venue_event_id = event.get("id") if event else None
    rules_text = m.get("description")

    status = "closed" if m.get("closed") else ("active" if m.get("active") else None)

    market, created = upsert_market(
        VENUE_POLYMARKET,
        m.get("id"),
        {
            "venue_event_id": str(venue_event_id) if venue_event_id else None,
            "title": m.get("groupItemTitle") or m.get("question"),
            "question": m.get("question"),
            "rules_text": rules_text,
            "rules_hash": rules_hash(rules_text),
            "status": status,
            "active": m.get("active"),
            "closed": m.get("closed"),
            "archived": m.get("archived"),
            "accepting_orders": m.get("acceptingOrders"),
            "enable_orderbook": m.get("enableOrderBook"),
            "start_time": parse_dt(m.get("startDate")),
            "close_time": parse_dt(m.get("endDate")),
            "raw_json": m,
            "updated_at_remote": parse_dt(m.get("updatedAt")),
        },
    )

    outcomes = _loads(m.get("outcomes"), ["Yes", "No"])
    token_ids = _loads(m.get("clobTokenIds"), [])
    for idx, side in enumerate(("yes", "no")):
        upsert_outcome(
            market,
            side,
            {
                "venue": VENUE_POLYMARKET,
                "outcome_name": outcomes[idx] if idx < len(outcomes) else side,
                "token_id": str(token_ids[idx]) if idx < len(token_ids) else None,
                "ticker": None,
                "raw_json": None,
            },
        )

    _save_event(event)
    return created


def discover(max_pages=20, page_size=100):
    """Page through Polymarket markets, upserting usable ones. Returns counts."""
    seen = new = updated = 0
    offset = 0
    for _ in range(max_pages):
        r = pm_client.get_markets(limit=page_size, offset=offset, closed=False)
        if not r.ok:
            logger.warning("polymarket markets fetch failed: %s", r.error)
            break
        markets = r.data if isinstance(r.data, list) else r.data.get("data", [])
        if not markets:
            break
        for m in markets:
            if not isinstance(m, dict) or not _is_usable(m):
                continue
            seen += 1
            created = _save_market(m)
            if created:
                new += 1
            else:
                updated += 1
        if len(markets) < page_size:
            break
        offset += page_size
    return {"markets_seen": seen, "markets_new": new, "markets_updated": updated}
