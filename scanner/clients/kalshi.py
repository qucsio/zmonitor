import base64
import time

from django.conf import settings

from .base import request


def _cfg(key):
    return settings.SCANNER[key]


def _load_private_key():
    path = _cfg("KALSHI_PRIVATE_KEY_PATH")
    if not path:
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        with open(path, "rb") as fh:
            return load_pem_private_key(fh.read(), password=None)
    except Exception:  # noqa: BLE001
        return None


def _sign(method, path):
    """Build Kalshi auth headers via RSA-PSS signing of timestamp+method+path."""
    key_id = _cfg("KALSHI_KEY_ID")
    pkey = _load_private_key()
    if not key_id or not pkey:
        return {}

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    ts = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode("utf-8")
    signature = pkey.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


def _path(endpoint):
    # path used for signing is everything after the host, including /trade-api/v2
    base = _cfg("KALSHI_API_BASE")
    # base looks like https://.../trade-api/v2
    prefix = base.split("kalshi.com", 1)[-1] if "kalshi.com" in base else ""
    return prefix + endpoint


def _get(endpoint, params=None):
    base = _cfg("KALSHI_API_BASE")
    headers = _sign("GET", _path(endpoint))
    return request("GET", f"{base}{endpoint}", venue="kalshi", params=params,
                   headers=headers, proxy=_cfg("KALSHI_PROXY_URL"))


def get_limits():
    return _get("/exchange/status")  # public; account limits below


def get_account_limits():
    # requires auth
    return _get("/portfolio/balance")


def get_markets(limit=100, cursor=None, status="open", params=None):
    q = {"limit": limit, "status": status}
    if cursor:
        q["cursor"] = cursor
    if params:
        q.update(params)
    return _get("/markets", params=q)


def get_events(limit=100, cursor=None, status="open", params=None):
    q = {"limit": limit, "status": status}
    if cursor:
        q["cursor"] = cursor
    if params:
        q.update(params)
    return _get("/events", params=q)


def get_series(category=None, tags=None):
    q = {}
    if category:
        q["category"] = category
    if tags:
        q["tags"] = tags
    return _get("/series", params=q or None)


def get_orderbook(ticker, depth=None):
    q = {}
    if depth:
        q["depth"] = depth
    return _get(f"/markets/{ticker}/orderbook", params=q or None)
