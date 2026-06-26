from django.conf import settings

from .base import request


def _cfg(key):
    return settings.SCANNER[key]


def get_events(limit=50, offset=0, closed=False, params=None):
    base = _cfg("POLYMARKET_GAMMA_BASE")
    q = {"limit": limit, "offset": offset, "closed": str(closed).lower()}
    if params:
        q.update(params)
    return request("GET", f"{base}/events", venue="polymarket", params=q)


def get_markets(limit=50, offset=0, closed=False, params=None):
    base = _cfg("POLYMARKET_GAMMA_BASE")
    q = {"limit": limit, "offset": offset, "closed": str(closed).lower()}
    if params:
        q.update(params)
    return request("GET", f"{base}/markets", venue="polymarket", params=q)


def get_book(token_id):
    base = _cfg("POLYMARKET_CLOB_BASE")
    return request("GET", f"{base}/book", venue="polymarket", params={"token_id": token_id})


def get_books(token_ids):
    """POST /books with [{"token_id": ...}]"""
    base = _cfg("POLYMARKET_CLOB_BASE")
    body = [{"token_id": t} for t in token_ids]
    return request("POST", f"{base}/books", venue="polymarket", json=body)
