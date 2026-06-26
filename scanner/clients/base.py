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


def request(method, url, *, venue, log=True, **kwargs):
    """Thin httpx wrapper that records latency and logs ApiHealthLog."""
    from scanner.models import ApiHealthLog

    t0 = time.time()
    status_code = None
    error = None
    data = None
    ok = False
    try:
        timeout = kwargs.pop("timeout", 30.0)
        resp = httpx.request(method, url, timeout=timeout, **kwargs)
        status_code = resp.status_code
        ok = resp.is_success
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = resp.text
        if not ok:
            error = f"HTTP {status_code}: {str(data)[:500]}"
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
