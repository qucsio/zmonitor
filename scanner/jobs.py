"""Lightweight background-job status in Redis, so the UI can show what runs when."""
import json

import redis
from django.conf import settings
from django.utils import timezone

JOB_NAMES = ["discovery", "matching", "orderbook", "reaper"]


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def set_job(name, **fields):
    try:
        client = _redis()
        key = f"job:{name}"
        prev = client.get(key)
        state = json.loads(prev) if prev else {}
        state.update(fields)
        client.set(key, json.dumps(state), ex=7 * 24 * 3600)
    except Exception:  # noqa: BLE001
        pass


def job_started(name):
    set_job(name, state="running", started=timezone.now().isoformat(),
            finished=None, error=None)


def job_finished(name, result=None, error=None):
    fields = {"state": "error" if error else "idle",
              "finished": timezone.now().isoformat()}
    if result is not None:
        fields["result"] = result
    if error is not None:
        fields["error"] = str(error)[:500]
    set_job(name, **fields)


def get_job(name):
    try:
        raw = _redis().get(f"job:{name}")
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def all_jobs():
    intervals = {
        "discovery": settings.SCANNER["DISCOVERY_INTERVAL_SEC"],
        "matching": settings.SCANNER["MATCHING_INTERVAL_SEC"] * 10,
        "orderbook": settings.SCANNER["ORDERBOOK_REFRESH_SEC"],
        "reaper": settings.SCANNER.get("REAP_INTERVAL_SEC", 300),
    }
    out = []
    for name in JOB_NAMES:
        job = {"state": None, "started": None, "finished": None,
               "result": None, "error": None}
        job.update(get_job(name) or {})
        job["name"] = name
        job["interval_sec"] = intervals.get(name)
        out.append(job)
    return out
