from django.conf import settings

from .base import request


def _cfg(key):
    return settings.SCANNER[key]


def _proxy():
    return settings.SCANNER.get("POLYMARKET_PROXY_URL")


def get_events(limit=50, offset=0, closed=False, params=None):
    base = _cfg("POLYMARKET_GAMMA_BASE")
    q = {"limit": limit, "offset": offset, "closed": str(closed).lower()}
    if params:
        q.update(params)
    return request("GET", f"{base}/events", venue="polymarket", params=q, proxy=_proxy(),
                   expected_ok=(422,))


def get_markets(limit=50, offset=0, closed=False, params=None):
    base = _cfg("POLYMARKET_GAMMA_BASE")
    q = {"limit": limit, "offset": offset, "closed": str(closed).lower()}
    if params:
        q.update(params)
    return request("GET", f"{base}/markets", venue="polymarket", params=q, proxy=_proxy())


def get_book(token_id):
    base = _cfg("POLYMARKET_CLOB_BASE")
    return request("GET", f"{base}/book", venue="polymarket", params={"token_id": token_id}, proxy=_proxy())


def get_books(token_ids):
    """POST /books with [{"token_id": ...}]"""
    base = _cfg("POLYMARKET_CLOB_BASE")
    body = [{"token_id": t} for t in token_ids]
    return request("POST", f"{base}/books", venue="polymarket", json=body, proxy=_proxy())
