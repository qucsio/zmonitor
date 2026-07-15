"""Deterministic normalization of RawMarket / RawEvent into a common structure."""
import re
import unicodedata

import yaml
from django.conf import settings

from scanner.models import (MarketOutcome, NormalizedMarket, RawEvent, RawMarket,
                            VENUE_KALSHI, VENUE_POLYMARKET)


def _fold(s):
    """Strip accents/diacritics: QUINTESSÊNCIA -> quintessencia."""
    if not s:
        return s
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

# ---------------------------------------------------------------- team aliases
_ALIAS_MAP = None


def _load_aliases():
    global _ALIAS_MAP
    if _ALIAS_MAP is not None:
        return _ALIAS_MAP
    path = settings.BASE_DIR / "config" / "team_aliases.yml"
    alias_map = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        for canonical, aliases in data.items():
            alias_map[canonical.lower()] = canonical
            for a in aliases or []:
                alias_map[str(a).lower().strip()] = canonical
    except FileNotFoundError:
        pass
    _ALIAS_MAP = alias_map
    return alias_map


def canonical_team(name):
    if not name:
        return None
    key = _fold(name).strip().lower()
    amap = _load_aliases()
    if key in amap:
        return amap[key]
    # normalize to a slug-ish canonical (accents already folded)
    return re.sub(r"[^a-z0-9]+", "_", key).strip("_") or None


# ---------------------------------------------------------------- game / type
GAME_KEYWORDS = {
    "cs2": "cs2", "csgo": "cs2", "counter-strike": "cs2", "counter strike": "cs2",
    "dota": "dota2", "dota 2": "dota2",
    "league of legends": "lol", "lol": "lol", "lck": "lol", "lec": "lol",
    "valorant": "valorant", "vct": "valorant",
    "overwatch": "overwatch", "rocket league": "rocket_league",
    "nba": "nba", "nfl": "nfl", "nhl": "nhl", "mlb": "mlb",
    "soccer": "soccer", "premier league": "soccer", "la liga": "soccer",
    "tennis": "tennis", "ufc": "ufc", "mma": "mma", "boxing": "boxing",
    "formula 1": "f1", "f1": "f1",
}

_VS_SPLIT = re.compile(r"\s+(?:vs\.?|v\.?|@|versus)\s+", re.IGNORECASE)
_MAP_RE = re.compile(r"\bmap\s*(\d+)\b", re.IGNORECASE)
_SET_RE = re.compile(r"\bset\s*(\d+)\b", re.IGNORECASE)
_BO_RE = re.compile(r"\b(?:bo|best[\s-]*of[\s-]*)(\d+)\b", re.IGNORECASE)


def _set_number(*texts):
    for text in texts:
        if not text:
            continue
        m = _SET_RE.search(text)
        if m:
            return int(m.group(1))
    return None


def detect_game(*texts):
    hay = " ".join(t for t in texts if t).lower()
    for kw, game in GAME_KEYWORDS.items():
        if kw in hay:
            return game
    return None


def kalshi_type_from_ticker(event_ticker, ticker):
    """Kalshi tickers encode the market type reliably, e.g.
    KXCS2GAME (match), KXCS2MAP-...-1 (map 1), KXCS2TOTALMAPS (total)."""
    et = (event_ticker or "").upper()
    tk = (ticker or "").upper()
    hay = et + " " + tk
    map_number = None
    if "TOTALMAPS" in hay or "TOTAL" in hay:
        return "total", None
    if "SPREAD" in hay or "WINMARGIN" in hay or "HANDICAP" in hay or "MARGIN" in hay or "HDCP" in hay:
        return "spread", None
    if "SET" in hay:
        m = re.search(r"-(\d{1,2})(?:-|$)", et) or re.search(r"-(\d{1,2})(?:-|$)", tk)
        return "set_winner", (int(m.group(1)) if m else None)
    if "MAP" in hay:
        m = re.search(r"-(\d{1,2})(?:-|$)", et) or re.search(r"-(\d{1,2})(?:-|$)", tk)
        if m:
            map_number = int(m.group(1))
        return "map_winner", map_number
    if "SERIES" in hay:
        return "series_winner", None
    if "GAME" in hay or "MATCH" in hay or "MONEYLINE" in hay or "WINNER" in hay or "WINS" in hay:
        return "match_winner", None
    return None, None


def detect_market_type(text, map_number=None):
    t = (text or "").lower()
    # spread / handicap / total must win BEFORE map (a "Map 3 Handicap" is a spread)
    if any(k in t for k in ("handicap", "spread", "+/-", "cover")) or re.search(r"\(\s*[+-]\d", t):
        return "spread"
    if any(k in t for k in ("total", "over/under", "o/u")) or re.search(r"\b(over|under)\s+\d", t):
        return "total"
    if map_number or re.search(r"\bmap\b", t):
        return "map_winner"
    if any(k in t for k in ("series", "best of", "bo3", "bo5", "advance", "win the series")):
        return "series_winner"
    if any(k in t for k in ("win", "winner", "beat", "defeat", "to win")):
        return "match_winner"
    return "unknown"


def parse_teams(*texts):
    for text in texts:
        if not text:
            continue
        parts = _VS_SPLIT.split(text.strip())
        if len(parts) == 2:
            a = re.sub(r"[?.!]+$", "", parts[0]).strip()
            b = re.sub(r"[?.!]+$", "", parts[1]).strip()
            # drop leading "Will "/"Who wins " noise
            a = re.sub(r"^(will|who wins|does)\s+", "", a, flags=re.IGNORECASE).strip()
            if a and b and len(a) < 60 and len(b) < 60:
                return a, b
    return None, None


def _map_number(*texts):
    for text in texts:
        if not text:
            continue
        m = _MAP_RE.search(text)
        if m:
            return int(m.group(1))
    return None


def _bo_format(*texts):
    for text in texts:
        if not text:
            continue
        m = _BO_RE.search(text)
        if m:
            return f"bo{m.group(1)}"
    return None


# ---------------------------------------------------------------- normalize
_KALSHI_MATCH_RE = re.compile(
    r"\bin the (.+?)\s+vs\.?\s+(.+?)\s+(?:match|series|game|maps?)\b", re.IGNORECASE)


def _kalshi_teams(market, yes_name, title, question):
    """Both teams live in the event title ('A vs. B'); yes_sub_title says which one
    the YES side resolves to. Return (team_a=yes_team, team_b=other)."""
    pa = pb = None
    m = _KALSHI_MATCH_RE.search(title or "")
    if m:
        pa, pb = m.group(1).strip(), m.group(2).strip()
    if not pa:
        ev_title = (RawEvent.objects.filter(
            venue=VENUE_KALSHI, venue_event_id=market.venue_event_id)
            .values_list("title", flat=True).first())
        pa, pb = parse_teams(ev_title or "", title or "")
    if not pa or not pb:
        # fallback: single team from yes_sub_title
        return (yes_name if yes_name and yes_name.lower() not in ("yes", "no") else None), None

    # orient so team_a corresponds to the YES side
    if yes_name and canonical_team(yes_name) == canonical_team(pb):
        return pb, pa
    return pa, pb


def normalize_market(market: RawMarket) -> NormalizedMarket:
    outcomes = {o.outcome_side: o for o in market.outcomes.all()}
    yes_name = outcomes.get("yes").outcome_name if outcomes.get("yes") else None
    no_name = outcomes.get("no").outcome_name if outcomes.get("no") else None

    title = market.title or ""
    question = market.question or ""
    rules = market.rules_text or ""

    map_number = _map_number(title, question, rules)
    set_number = _set_number(title, question)
    market_type = detect_market_type(" ".join([title, question]), map_number)

    # "Set N winner" is a distinct partial market (tennis) — must not match a full
    # match_winner. Reuse map_number slot to carry the set number.
    if set_number and market_type in ("match_winner", "map_winner", "unknown"):
        market_type = "set_winner"
        map_number = set_number

    # Kalshi: prefer ticker-encoded type (far more reliable than title text)
    if market.venue == VENUE_KALSHI:
        kt, kmap = kalshi_type_from_ticker(market.venue_event_id, market.venue_market_id)
        if kt:
            market_type = kt
        if kmap is not None:
            map_number = kmap

    game = detect_game(title, question, rules, market.venue_event_id or "")

    # teams
    team_a, team_b = None, None
    if market.venue == VENUE_KALSHI:
        team_a, team_b = _kalshi_teams(market, yes_name, title, question)
    else:
        # Polymarket: outcome names ARE the teams for winner markets
        if market_type in ("match_winner", "map_winner", "series_winner"):
            if yes_name and no_name and yes_name.lower() not in ("yes", "no"):
                team_a, team_b = yes_name, no_name
        if not team_a:
            team_a, team_b = parse_teams(title, question)

    risk_flags = []
    if market_type == "unknown":
        risk_flags.append("market_type_unknown")
    if not rules:
        risk_flags.append("rules_missing")

    _clip = lambda s: s[:250] if isinstance(s, str) and len(s) > 250 else s  # noqa: E731
    defaults = {
        "venue": market.venue,
        "game": game,
        "team_a": _clip(team_a),
        "team_b": _clip(team_b),
        "canonical_team_a": _clip(canonical_team(team_a)),
        "canonical_team_b": _clip(canonical_team(team_b)),
        "start_time_utc": market.start_time or market.close_time,
        "market_type": market_type,
        "map_number": map_number,
        "bo_format": _bo_format(title, question, rules),
        "outcome_yes": yes_name,
        "outcome_no": no_name,
        "resolution_source": (market.raw_json or {}).get("resolutionSource") or None,
        "rules_summary": (rules[:2000] or None),
        "parser_confidence": _confidence(market_type, team_a, team_b),
        "normalization_status": "ok" if market_type != "unknown" else "partial",
        "risk_flags": risk_flags,
    }
    obj, _ = NormalizedMarket.objects.update_or_create(market=market, defaults=defaults)

    if market.matching_status == "pending":
        market.matching_status = "normalized"
        market.save(update_fields=["matching_status"])
    return obj


def _confidence(market_type, a, b):
    score = 0.0
    if market_type != "unknown":
        score += 0.5
    if a and b:
        score += 0.5
    return round(score, 4)


def normalize_pending(venue=None, limit=None):
    # include 'matched' so re-runs refresh normalization (e.g. new set_winner rule)
    qs = RawMarket.objects.filter(
        matching_status__in=["pending", "normalized", "matched"])
    if venue:
        qs = qs.filter(venue=venue)
    qs = qs.order_by("id")
    if limit:
        qs = qs[:limit]
    count = 0
    for market in qs.iterator():
        normalize_market(market)
        count += 1
    return count
