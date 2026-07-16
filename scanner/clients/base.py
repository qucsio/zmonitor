import logging
import time

import httpx

logger = logging.getLogger("scanner")


class ApiResult:
    def __init__(self, ok, status_code, data, latency_ms, error=None):
        self.ok = ok
        self.status_code = status_code
        self.data = data
        self.latency_ms = latency_ms
        self.error = error


def request(method, url, *, venue, log=True, expected_ok=(), **kwargs):
    """Thin httpx wrapper that records latency and logs ApiHealthLog.
    `expected_ok` = status codes to treat as non-errors (e.g. 422 pagination end)."""
    from scanner.models import ApiHealthLog

    t0 = time.time()
    status_code = None
    error = None
    data = None
    ok = False
    try:
        timeout = kwargs.pop("timeout", 30.0)
        proxy = kwargs.pop("proxy", None) or None
        # simple 429 backoff: retry a couple times with growing delay
        for attempt in range(3):
            resp = httpx.request(method, url, timeout=timeout, proxy=proxy, **kwargs)
            if resp.status_code != 429:
                break
            time.sleep(0.5 * (attempt + 1))
        status_code = resp.status_code
        ok = resp.is_success
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = resp.text
        if not ok and status_code not in expected_ok:
            error = f"HTTP {status_code}: {str(data)[:500]}"
        elif not ok:
            ok = True  # expected non-2xx (e.g. pagination boundary) — not a health error
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        ok = False
    latency_ms = int((time.time() - t0) * 1000)

    if log:
        try:
            ApiHealthLog.objects.create(
                venue=venue,
                endpoint=url[:255],
                status_code=status_code,
                ok=ok,
                latency_ms=latency_ms,
                error_text=error,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to write ApiHealthLog")

    return ApiResult(ok, status_code, data, latency_ms, error)
