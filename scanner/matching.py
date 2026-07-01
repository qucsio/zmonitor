"""Candidate generation + scoring + optional LLM tie-break -> MatchedPair."""
import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from scanner import llm
from scanner.discovery.kalshi import fetch_markets_for_event
from scanner.models import (MatchedPair, NormalizedMarket, RawEvent, RawMarket,
                            VENUE_KALSHI, VENUE_POLYMARKET)
from scanner.normalize import canonical_team, normalize_market, parse_teams

logger = logging.getLogger("scanner")

WINNER_TYPES = {"match_winner", "map_winner", "series_winner"}
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

    pm_teams = {pm.canonical_team_a, pm.canonical_team_b} - {None}
    k_teams = {k.canonical_team_a, k.canonical_team_b} - {None}
    if pm_teams and pm_teams == k_teams:
        score += Decimal("0.6")
    elif pm_teams & k_teams:
        score += Decimal("0.3")

    if pm.market_type == k.market_type:
        score += Decimal("0.2")
    else:
        hard.append("different_market_type")

    if (pm.map_number or None) == (k.map_number or None):
        score += Decimal("0.1")
    else:
        hard.append("different_map_number")

    mapping = _outcome_mapping(pm, k)
    if mapping:
        score += Decimal("0.1")
    else:
        hard.append("outcome_mapping_uncertain")

    return min(score, Decimal("1")), hard, soft, mapping


def _outcome_mapping(pm, k):
    if not (pm.canonical_team_a and k.canonical_team_a and k.canonical_team_b):
        return None
    if pm.canonical_team_a == k.canonical_team_a and pm.canonical_team_b == k.canonical_team_b:
        return {"pm_yes": "kalshi_yes", "pm_no": "kalshi_no"}
    if pm.canonical_team_a == k.canonical_team_b and pm.canonical_team_b == k.canonical_team_a:
        return {"pm_yes": "kalshi_no", "pm_no": "kalshi_yes"}
    return None


def _llm_ctx(nm: NormalizedMarket):
    return {
        "venue": nm.venue, "game": nm.game, "market_type": nm.market_type,
        "map_number": nm.map_number, "team_a": nm.team_a, "team_b": nm.team_b,
        "outcome_yes": nm.outcome_yes, "outcome_no": nm.outcome_no,
        "rules": (nm.rules_summary or "")[:800],
    }


# ------------------------------------------------------------ orchestration
def _ensure_kalshi_markets(event_ticker, fetched):
    if event_ticker in fetched:
        return
    fetched.add(event_ticker)
    if not RawMarket.objects.filter(venue=VENUE_KALSHI, venue_event_id=event_ticker).exists():
        fetch_markets_for_event(event_ticker)
    for m in RawMarket.objects.filter(venue=VENUE_KALSHI, venue_event_id=event_ticker):
        if not hasattr(m, "normalized"):
            normalize_market(m)


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


def run_matching(limit=None, max_event_fetches=200):
    """Match normalized Polymarket winner-markets against Kalshi via team blocking."""
    index = build_kalshi_event_index()
    logger.info("kalshi event index: %d teams", len(index))

    pm_qs = NormalizedMarket.objects.filter(
        venue=VENUE_POLYMARKET, market_type__in=WINNER_TYPES,
    ).exclude(canonical_team_a=None).exclude(canonical_team_b=None).order_by("market_id")
    if limit:
        pm_qs = pm_qs[:limit]

    fetched = set()
    stats = {"considered": 0, "pairs": 0, "matched": 0, "needs_review": 0,
             "candidate": 0, "rejected": 0}

    for pm in pm_qs.iterator():
        stats["considered"] += 1
        cand_events = set()
        for c in (pm.canonical_team_a, pm.canonical_team_b):
            cand_events |= index.get(c, set())
        if not cand_events:
            continue

        best = None  # (score, k_norm, hard, mapping)
        for et in cand_events:
            if len(fetched) < max_event_fetches:
                _ensure_kalshi_markets(et, fetched)
            for k in NormalizedMarket.objects.filter(
                venue=VENUE_KALSHI, market__venue_event_id=et,
                market_type=pm.market_type,
            ):
                sc, hard, soft, mapping = score_pair(pm, k)
                if best is None or sc > best[0]:
                    best = (sc, k, hard, mapping)

        if not best or best[0] < settings.SCANNER["MATCH_REVIEW_THRESHOLD"] and best[2]:
            continue
        sc, k_norm, hard, mapping = best
        if sc < Decimal("0.3"):
            continue
        status = _save_pair(pm.market, k_norm.market, pm, k_norm, sc, hard, mapping)
        stats["pairs"] += 1
        stats[status] = stats.get(status, 0) + 1

    return stats
