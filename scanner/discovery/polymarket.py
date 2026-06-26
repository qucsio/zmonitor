import json
import logging
import time

from django.conf import settings

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


def _event_matches(ev):
    wanted = settings.SCANNER["DISCOVERY_PM_TAGS"]
    if not wanted:
        return True
    tags = ev.get("tags") or []
    for t in tags:
        slug = (t.get("slug") or "").lower()
        label = (t.get("label") or "").lower()
        for w in wanted:
            if w in slug or w in label:
                return True
    return False


def _save_event(ev):
    tags = ev.get("tags") or []
    upsert_event(
        VENUE_POLYMARKET,
        ev.get("id"),
        {
            "title": ev.get("title"),
            "category": tags[0].get("label") if tags else None,
            "sport": tags[0].get("slug") if tags else None,
            "status": "closed" if ev.get("closed") else "active",
            "start_time": parse_dt(ev.get("startDate")),
            "end_time": parse_dt(ev.get("endDate")),
            "raw_json": ev,
            "updated_at_remote": parse_dt(ev.get("updatedAt")),
        },
    )


def _save_market(m, venue_event_id):
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
        upsert_outcome(market, side, {
            "venue": VENUE_POLYMARKET,
            "outcome_name": outcomes[idx] if idx < len(outcomes) else side,
            "token_id": str(token_ids[idx]) if idx < len(token_ids) else None,
            "ticker": None,
            "raw_json": None,
        })
    return created


def discover(page_size=100):
    """Events-driven discovery filtered to configured sports/esports tags."""
    max_pages = settings.SCANNER["DISCOVERY_MAX_PAGES"]
    throttle = settings.SCANNER["DISCOVERY_PAGE_THROTTLE_MS"] / 1000.0
    seen = new = updated = 0
    offset = 0

    for _ in range(max_pages):
        r = pm_client.get_events(limit=page_size, offset=offset, closed=False)
        if not r.ok:
            logger.warning("polymarket events fetch failed: %s", r.error)
            break
        events = r.data if isinstance(r.data, list) else r.data.get("data", [])
        if not events:
            break
        for ev in events:
            if not isinstance(ev, dict) or not _event_matches(ev):
                continue
            _save_event(ev)
            for m in ev.get("markets") or []:
                if not isinstance(m, dict) or not _is_usable(m):
                    continue
                seen += 1
                if _save_market(m, ev.get("id")):
                    new += 1
                else:
                    updated += 1
        if len(events) < page_size:
            break
        offset += page_size
        if throttle:
            time.sleep(throttle)
    return {"markets_seen": seen, "markets_new": new, "markets_updated": updated}
