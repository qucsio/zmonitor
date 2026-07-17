"""Print the matching funnel stage-by-stage so we can see WHERE pairs are lost.

Usage: python manage.py diagnose_matching [--sample N]
Read-only: touches no network, only counts what's already in the DB (Kalshi markets are
fetched lazily during matching, so 'not normalized' Kalshi markets are expected)."""
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from scanner import matching
from scanner.models import (NormalizedMarket, RawEvent, RawMarket,
                            VENUE_KALSHI, VENUE_POLYMARKET)

WINNER = matching.WINNER_TYPES


class Command(BaseCommand):
    help = "Diagnose the PM<->Kalshi matching funnel."

    def add_arguments(self, parser):
        parser.add_argument("--sample", type=int, default=15,
                            help="show N example PM markets that found NO candidate events")

    def _h(self, t):
        self.stdout.write(self.style.MIGRATE_HEADING(t))

    def handle(self, *args, **opts):
        now = timezone.now()

        # -------------------------------------------------- raw counts
        self._h("RAW MARKETS")
        for venue, label in ((VENUE_POLYMARKET, "polymarket"), (VENUE_KALSHI, "kalshi")):
            total = RawMarket.objects.filter(venue=venue).count()
            open_ = RawMarket.objects.filter(venue=venue, closed=False).count()
            normd = NormalizedMarket.objects.filter(venue=venue).count()
            self.stdout.write(f"  {label:11} total={total:>7} open={open_:>7} normalized={normd:>7}")

        # -------------------------------------------------- PM funnel
        self._h("POLYMARKET normalized market_type breakdown (open only)")
        rows = (NormalizedMarket.objects.filter(venue=VENUE_POLYMARKET, market__closed=False)
                .values("market_type").annotate(n=Count("id")).order_by("-n"))
        for r in rows:
            self.stdout.write(f"  {r['market_type'] or '(none)':16} {r['n']:>7}")

        pm_winner = NormalizedMarket.objects.filter(
            venue=VENUE_POLYMARKET, market_type__in=WINNER, market__closed=False)
        pm_winner_open = pm_winner.filter(
            Q(market__close_time__gte=now) | Q(market__close_time__isnull=True))
        pm_teams = pm_winner_open.exclude(canonical_team_a=None).exclude(canonical_team_b=None)
        pm_matched = pm_teams.filter(market__matching_status="matched")

        self._h("POLYMARKET funnel -> matchable universe")
        self.stdout.write(f"  winner-type & open ............ {pm_winner.count():>7}")
        self.stdout.write(f"  + close_time not passed ....... {pm_winner_open.count():>7}")
        self.stdout.write(f"  + both canonical teams ........ {pm_teams.count():>7}  <- 'considered'")
        self.stdout.write(f"      of which already matched .. {pm_matched.count():>7}  (skipped when incremental)")
        no_teams = pm_winner_open.filter(Q(canonical_team_a=None) | Q(canonical_team_b=None)).count()
        self.stdout.write(f"  winner-type but MISSING teams . {no_teams:>7}  <- lost here")

        # -------------------------------------------------- Kalshi index
        self._h("KALSHI event team index (drives candidate blocking)")
        n_events = RawEvent.objects.filter(venue=VENUE_KALSHI).count()
        index = matching.build_kalshi_event_index()
        # events that yielded at least one parseable team
        parsed_events = set()
        for teams in index.values():
            parsed_events |= teams
        self.stdout.write(f"  kalshi events ................. {n_events:>7}")
        self.stdout.write(f"  events with >=1 parsed team ... {len(parsed_events):>7}  <- events reachable by matching")
        self.stdout.write(f"  distinct canonical teams ...... {len(index):>7}")

        # -------------------------------------------------- candidate reachability
        self._h("PM considered -> candidate Kalshi events (blocking hit rate)")
        considered = pm_teams
        with_cand = 0
        without = []
        for pm in considered.iterator():
            cand = set()
            for c in (pm.canonical_team_a, pm.canonical_team_b):
                cand |= index.get(c, set())
            if cand:
                with_cand += 1
            elif len(without) < opts["sample"]:
                without.append(pm)
        total_considered = considered.count()
        self.stdout.write(f"  considered PM markets ......... {total_considered:>7}")
        self.stdout.write(f"  found >=1 candidate event ..... {with_cand:>7}")
        self.stdout.write(f"  found NONE (team not in index)  {total_considered - with_cand:>7}  <- lost here")

        if without:
            self._h(f"SAMPLE PM markets with NO Kalshi candidate (first {len(without)})")
            for pm in without:
                self.stdout.write(
                    f"  [{pm.game or '?':8}] a={pm.canonical_team_a!r:22} b={pm.canonical_team_b!r:22} "
                    f":: {(pm.market.title or '')[:60]!r}")

        # -------------------------------------------------- last run stats
        self._h("LAST MATCHING RUN (redis)")
        st = matching.get_matching_status()
        if st:
            self.stdout.write(f"  state={st.get('state')} finished={st.get('finished')}")
            self.stdout.write(f"  stats={st.get('stats')}")
        else:
            self.stdout.write("  (no status recorded)")
