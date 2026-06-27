import hashlib

from django.utils.dateparse import parse_datetime

from scanner.models import MarketOutcome, RawEvent, RawMarket


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        # epoch seconds
        from datetime import datetime, timezone
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    try:
        return parse_datetime(str(value))
    except (ValueError, TypeError):
        return None


def rules_hash(text):
    if not text:
        return None
    norm = " ".join(str(text).split()).lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def upsert_event(venue, venue_event_id, defaults):
    if not venue_event_id:
        return None, False
    obj, created = RawEvent.objects.update_or_create(
        venue=venue, venue_event_id=str(venue_event_id), defaults=defaults
    )
    return obj, created


def upsert_market(venue, venue_market_id, defaults):
    """Returns (obj, created). Preserves matching_status on update."""
    defaults = dict(defaults)
    defaults.pop("matching_status", None)  # never reset matching pipeline state
    obj, created = RawMarket.objects.update_or_create(
        venue=venue, venue_market_id=str(venue_market_id), defaults=defaults
    )
    return obj, created


def _clip(value, n=255):
    if isinstance(value, str) and len(value) > n:
        return value[:n]
    return value


def upsert_outcome(market, outcome_side, defaults):
    defaults = dict(defaults)
    defaults["token_id"] = _clip(defaults.get("token_id"))
    defaults["ticker"] = _clip(defaults.get("ticker"))
    obj, _ = MarketOutcome.objects.update_or_create(
        market=market, outcome_side=outcome_side, defaults=defaults
    )
    return obj
