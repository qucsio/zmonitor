"""Optional LLM tie-breaker for the matching gray zone. OpenAI-compatible API."""
import hashlib
import json
import logging

import httpx
import redis
from django.conf import settings

logger = logging.getLogger("scanner")

SYSTEM_PROMPT = (
    "You compare two prediction-market contracts, one from Polymarket and one from "
    "Kalshi, and decide whether they refer to the exact same tradeable outcome. "
    "Respond with STRICT JSON only, no prose. Schema: "
    '{"same_event": bool, "same_market_type": bool, "same_map_number": bool, '
    '"same_outcome": bool, "confidence": number (0..1), "risk_flags": [string], '
    '"reason": string}. Be conservative: if rules or resolution differ, lower confidence.'
)


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _cache_key(pm_ctx, kalshi_ctx):
    raw = json.dumps([pm_ctx, kalshi_ctx], sort_keys=True, ensure_ascii=False)
    return "llm_match:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def classify(pm_ctx, kalshi_ctx):
    """Return dict per schema, or None if LLM disabled/unavailable. Cached in Redis."""
    if not settings.SCANNER["LLM_MATCHING_ENABLED"]:
        return None
    cache = _redis()
    key = _cache_key(pm_ctx, kalshi_ctx)
    try:
        cached = cache.get(key)
        if cached:
            return json.loads(cached)
    except Exception:  # noqa: BLE001
        pass

    result = _call_openai(pm_ctx, kalshi_ctx)
    if result is not None:
        try:
            cache.set(key, json.dumps(result), ex=7 * 24 * 3600)
        except Exception:  # noqa: BLE001
            pass
    return result


def _call_openai(pm_ctx, kalshi_ctx):
    api_key = settings.SCANNER["OPENAI_API_KEY"]
    if not api_key:
        logger.warning("LLM enabled but OPENAI_API_KEY is empty")
        return None
    base = settings.SCANNER["OPENAI_BASE_URL"].rstrip("/")
    user_msg = (
        "Polymarket contract:\n" + json.dumps(pm_ctx, ensure_ascii=False, indent=2)
        + "\n\nKalshi contract:\n" + json.dumps(kalshi_ctx, ensure_ascii=False, indent=2)
    )
    payload = {
        "model": settings.SCANNER["LLM_MODEL"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=45.0,
            proxy=settings.SCANNER["OPENAI_PROXY_URL"],
        )
        if not resp.is_success:
            logger.warning("LLM call failed %s: %s", resp.status_code, resp.text[:300])
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM call error: %s", exc)
        return None
