"""Candidate generation + scoring + optional LLM tie-break -> MatchedPair."""
import logging
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from rapidfuzz import fuzz

from scanner import llm
from scanner.discovery.kalshi import fetch_markets_for_event
from scanner.models import (MatchedPair, NormalizedMarket, RawEvent, RawMarket,
                            VENUE_KALSHI, VENUE_POLYMARKET)
from scanner.normalize import _fold, canonical_team, normalize_market, parse_teams

TEAM_SIM_THRESHOLD = 0.85


def _sim(a, b):
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(_fold(a).lower(), _fold(b).lower()) / 100.0


def _team_alignment(pm, k):
    """Return (strength, orientation) where orientation is 'direct' (pm_a~kalshi_yes)
    or 'swap' (pm_a~kalshi_no). strength = min similarity of the better pairing."""
    a, b = pm.team_a, pm.team_b
    ka, kb = k.team_a, k.team_b  # ka is the kalshi YES side
    direct = min(_sim(a, ka), _sim(b, kb))
    swap = min(_sim(a, kb), _sim(b, ka))
    if direct >= swap:
        return direct, "direct"
    return swap, "swap"

logger = logging.getLogger("scanner")

WINNER_TYPES = {"match_winner", "map_winner", "series_winner", "set_winner"}
HARD_FLAGS = {"different_market_type", "different_map_number",
              "outcome_mapping_uncertain", "market_closed_or_paused"}


# ------------------------------------------------------------ kalshi event index
def _event_teams(ev: RawEvent):
    a, b = parse_teams(ev.title or "", (ev.raw_json or {}).get("sub_title") or "")
    return canonical_team(a), canonical_team(b)


def build_kalshi_event_index():
    """canonical_team -> set(event_ticker)."""
    index = {}
    for ev in RawEvent.objects.filter(venue=VENUE_KALSHI).iterator():
        ca, cb = _event_teams(ev)
        for c in (ca, cb):
            if c:
                index.setdefault(c, set()).add(ev.venue_event_id)
    return index


# ------------------------------------------------------------ scoring
def score_pair(pm: NormalizedMarket, k: NormalizedMarket):
    hard, soft = [], []
    score = Decimal("0")

    strength, orientation = _team_alignment(pm, k)
    one_side = max(_sim(pm.team_a, k.team_a), _sim(pm.team_a, k.team_b),
                   _sim(pm.team_b, k.team_a), _sim(pm.team_b, k.team_b))
    if strength >= TEAM_SIM_THRESHOLD:
        score += Decimal("0.6")
    elif one_side >= TEAM_SIM_THRESHOLD:
        score += Decimal("0.3")
        hard.append("different_teams")

    if pm.market_type == k.market_type:
        score += Decimal("0.2")
    else:
        hard.append("different_market_type")

    if (pm.map_number or None) == (k.map_number or None):
        score += Decimal("0.1")
    else:
        hard.append("different_map_number")

    mapping = None
    if strength >= TEAM_SIM_THRESHOLD:
        mapping = ({"pm_yes": "kalshi_yes", "pm_no": "kalshi_no"} if orientation == "direct"
                   else {"pm_yes": "kalshi_no", "pm_no": "kalshi_yes"})
        score += Decimal("0.1")
    else:
        hard.append("outcome_mapping_uncertain")

    return min(score, Decimal("1")), hard, soft, mapping


def _llm_ctx(nm: NormalizedMarket):
    return {
        "venue": nm.venue, "game": nm.game, "market_type": nm.market_type,
        "map_number": nm.map_number, "team_a": nm.team_a, "team_b": nm.team_b,
        "outcome_yes": nm.outcome_yes, "outcome_no": nm.outcome_no,
        "rules": (nm.rules_summary or "")[:800],
    }


# ------------------------------------------------------------ orchestration
def _kalshi_norms_for_event(event_ticker, cache, fetched, max_fetches):
    """Return (and cache once per run) the list of normalized Kalshi markets for an
    event, lazily fetching + normalizing on first touch."""
    if event_ticker in cache:
        return cache[event_ticker]
    if event_ticker not in fetched and len(fetched) < max_fetches:
        fetched.add(event_ticker)
        # Always (re)fetch within budget so closed/resolved status stays fresh.
        fetch_markets_for_event(event_ticker)
        for m in RawMarket.objects.filter(
                venue=VENUE_KALSHI, venue_event_id=event_ticker):
            normalize_market(m)
    norms = list(NormalizedMarket.objects.filter(
        venue=VENUE_KALSHI, market__venue_event_id=event_ticker).select_related("market"))
    cache[event_ticker] = norms
    return norms


def _status_for(score, hard):
    auto = settings.SCANNER["MATCH_AUTO_THRESHOLD"]
    review = settings.SCANNER["MATCH_REVIEW_THRESHOLD"]
    if hard:
        return "rejected" if score < review else "needs_review"
    if score >= auto:
        return "matched"
    if score >= review:
        return "needs_review"
    return "candidate"


def _save_pair(pm_market, k_market, pm_norm, k_norm, score, hard, mapping):
    status = _status_for(score, hard)

    # LLM tie-break for the gray zone / borderline hard flags
    if status in ("needs_review",) and settings.SCANNER["LLM_MATCHING_ENABLED"]:
        verdict = llm.classify(_llm_ctx(pm_norm), _llm_ctx(k_norm))
        if verdict:
            conf = Decimal(str(verdict.get("confidence", 0)))
            if verdict.get("same_event") and verdict.get("same_outcome") and conf >= settings.SCANNER["MATCH_AUTO_THRESHOLD"] and not verdict.get("risk_flags"):
                status = "matched"
            elif conf < settings.SCANNER["MATCH_REVIEW_THRESHOLD"]:
                status = "rejected"
            hard = list(set(hard) | set(verdict.get("risk_flags") or []))

    MatchedPair.objects.update_or_create(
        polymarket_market=pm_market, kalshi_market=k_market,
        defaults={
            "status": status,
            "confidence": score,
            "match_score": score,
            "game": pm_norm.game or k_norm.game,
            "canonical_team_a": pm_norm.canonical_team_a,
            "canonical_team_b": pm_norm.canonical_team_b,
            "start_time_utc": pm_norm.start_time_utc,
            "market_type": pm_norm.market_type,
            "map_number": pm_norm.map_number,
            "outcome_mapping": mapping or {},
            "risk_flags": hard,
            "rules_hash_pm": pm_market.rules_hash,
            "rules_hash_kalshi": k_market.rules_hash,
            "last_checked_at": timezone.now(),
        },
    )
    if status == "matched":
        for m in (pm_market, k_market):
            if m.matching_status != "matched":
                m.matching_status = "matched"
                m.save(update_fields=["matching_status"])
    return status


def _set_status(**fields):
    import json
    import redis
    try:
        client = redis.from_url(settings.REDIS_URL)
        prev = client.get("scanner:matching_status")
        state = json.loads(prev) if prev else {}
        state.update(fields)
        client.set("scanner:matching_status", json.dumps(state), ex=86400)
    except Exception:  # noqa: BLE001
        pass


def get_matching_status():
    import json
    import redis
    try:
        client = redis.from_url(settings.REDIS_URL)
        raw = client.get("scanner:matching_status")
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def run_matching(limit=None, max_event_fetches=200):
    """Match normalized Polymarket winner-markets against Kalshi via team blocking."""
    _set_status(state="running", started=timezone.now().isoformat(),
                finished=None, stats=None)
    index = build_kalshi_event_index()
    logger.info("kalshi event index: %d teams", len(index))

    now = timezone.now()
    pm_qs = NormalizedMarket.objects.filter(
        venue=VENUE_POLYMARKET, market_type__in=WINNER_TYPES,
        market__closed=False,
    ).exclude(canonical_team_a=None).exclude(canonical_team_b=None).filter(
        models.Q(market__close_time__gte=now) | models.Q(market__close_time__isnull=True)
    ).order_by("-market_id")
    if limit:
        pm_qs = pm_qs[:limit]

    fetched = set()
    norm_cache = {}
    stats = {"considered": 0, "with_cand_events": 0, "scored": 0, "below_floor": 0,
             "pairs": 0, "matched": 0, "needs_review": 0, "candidate": 0, "rejected": 0}

    for pm in pm_qs.iterator():
        stats["considered"] += 1
        cand_events = set()
        for c in (pm.canonical_team_a, pm.canonical_team_b):
            cand_events |= index.get(c, set())
        if not cand_events:
            continue
        stats["with_cand_events"] += 1

        best = None  # (score, k_norm, hard, mapping)
        for et in cand_events:
            for k in _kalshi_norms_for_event(et, norm_cache, fetched, max_event_fetches):
                sc, hard, soft, mapping = score_pair(pm, k)
                if best is None or sc > best[0]:
                    best = (sc, k, hard, mapping)

        if best is None:
            continue
        stats["scored"] += 1
        sc, k_norm, hard, mapping = best
        # Persist only meaningful pairs: skip weak matches that also have hard
        # conflicts (e.g. one team matches but the opponent differs) — pure noise.
        review = settings.SCANNER["MATCH_REVIEW_THRESHOLD"]
        if hard and sc < review:
            stats["below_floor"] += 1
            continue
        status = _save_pair(pm.market, k_norm.market, pm, k_norm, sc, hard, mapping)
        stats["pairs"] += 1
        stats[status] = stats.get(status, 0) + 1

    stats["rechecked"] = _recheck_existing_pairs()
    stats["deduped"] = _dedupe_pairs()
    _set_status(state="done", finished=timezone.now().isoformat(), stats=stats)
    return stats


def _recheck_existing_pairs():
    """Re-score existing matched/needs_review pairs against CURRENT normalization and
    flip stale ones (e.g. PM now set_winner vs Kalshi match_winner -> rejected).
    Without this, pairs that stop matching are never updated (weak+hard are skipped)."""
    changed = 0
    qs = MatchedPair.objects.filter(status__in=["matched", "needs_review"]).select_related(
        "polymarket_market", "kalshi_market")
    for p in qs.iterator():
        pmn = getattr(p.polymarket_market, "normalized", None)
        kn = getattr(p.kalshi_market, "normalized", None)
        if not pmn or not kn:
            continue
        sc, hard, soft, mapping = score_pair(pmn, kn)
        new_status = _status_for(sc, hard)
        if new_status != p.status or p.market_type != pmn.market_type:
            p.status = new_status
            p.match_score = sc
            p.confidence = sc
            p.market_type = pmn.market_type
            p.map_number = pmn.map_number
            p.outcome_mapping = mapping or {}
            p.risk_flags = hard
            p.save(update_fields=["status", "match_score", "confidence", "market_type",
                                  "map_number", "outcome_mapping", "risk_flags", "updated_at"])
            changed += 1
    return changed


def _dedupe_pairs():
    """Collapse duplicates from both sides:
      - one PM market can't be two matched pairs (Kalshi lists a binary market as two
        tickers, e.g. ...-T1 and ...-BLG — same market economically)
      - one Kalshi market can't be two matched PM markets
    Keep the best-scoring pair; demote the rest to needs_review."""
    from django.db.models import Count

    demoted = 0
    for field in ("polymarket_market", "kalshi_market"):
        dupes = (MatchedPair.objects.filter(status="matched")
                 .values(field).annotate(n=Count("id")).filter(n__gt=1))
        for d in dupes:
            pairs = list(MatchedPair.objects.filter(
                status="matched", **{field: d[field]}).order_by("-match_score", "-id"))
            for extra in pairs[1:]:
                extra.status = "needs_review"
                extra.reject_reason = "duplicate_" + field
                if extra.reject_reason not in extra.risk_flags:
                    extra.risk_flags = extra.risk_flags + [extra.reject_reason]
                extra.save(update_fields=["status", "reject_reason", "risk_flags", "updated_at"])
                demoted += 1
    return demoted
