from django.core.management.base import BaseCommand

from scanner.models import (DiscoveryRun, MarketOutcome, MatchedPair,
                            NormalizedMarket, RawEvent, RawMarket)


class Command(BaseCommand):
    help = "Wipe discovered raw events/markets (and derived rows) for a clean re-discovery."

    def add_arguments(self, parser):
        parser.add_argument("--venue", default="all", choices=["polymarket", "kalshi", "all"])
        parser.add_argument("--yes", action="store_true", help="skip confirmation")

    def handle(self, *args, **opts):
        venue = opts["venue"]
        mkt = RawMarket.objects.all()
        ev = RawEvent.objects.all()
        runs = DiscoveryRun.objects.all()
        if venue != "all":
            mkt = mkt.filter(venue=venue)
            ev = ev.filter(venue=venue)
            runs = runs.filter(venue=venue)

        m_count, e_count = mkt.count(), ev.count()
        if not opts["yes"]:
            self.stdout.write(f"Will delete {m_count} markets and {e_count} events "
                              f"(venue={venue}) plus outcomes/normalized/pairs. Re-run with --yes.")
            return

        # MatchedPair / NormalizedMarket / MarketOutcome cascade from RawMarket FK,
        # but MatchedPair has FKs to two markets — delete pairs first if venue-scoped.
        MatchedPair.objects.filter(
            polymarket_market__in=mkt).delete()
        MatchedPair.objects.filter(kalshi_market__in=mkt).delete()
        NormalizedMarket.objects.filter(market__in=mkt).delete()
        MarketOutcome.objects.filter(market__in=mkt).delete()
        deleted_m = mkt.delete()
        deleted_e = ev.delete()
        runs.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Deleted markets={deleted_m} events={deleted_e}"))
