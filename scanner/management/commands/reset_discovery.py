from django.core.management.base import BaseCommand
from django.db import connection, transaction

from scanner.models import (DiscoveryRun, MarketOutcome, MatchedPair,
                            NormalizedMarket, RawEvent, RawMarket)


class Command(BaseCommand):
    help = "Wipe discovered raw events/markets (and derived rows) for a clean re-discovery."

    def add_arguments(self, parser):
        parser.add_argument("--venue", default="all", choices=["polymarket", "kalshi", "all"])
        parser.add_argument("--yes", action="store_true", help="skip confirmation")

    def handle(self, *args, **opts):
        venue = opts["venue"]

        if venue == "all":
            # Fast path: TRUNCATE all discovery tables in one shot.
            if not opts["yes"]:
                self.stdout.write("Will TRUNCATE all discovery tables. Re-run with --yes.")
                return
            models = [MatchedPair, NormalizedMarket, MarketOutcome,
                      RawMarket, RawEvent, DiscoveryRun]
            tables = [m._meta.db_table for m in models]
            with connection.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")
            self.stdout.write(self.style.SUCCESS(f"Truncated: {', '.join(tables)}"))
            return

        mkt = RawMarket.objects.filter(venue=venue)
        ev = RawEvent.objects.filter(venue=venue)
        runs = DiscoveryRun.objects.filter(venue=venue)

        m_ids = list(mkt.values_list("id", flat=True))
        if not opts["yes"]:
            self.stdout.write(f"Will delete {len(m_ids)} markets and {ev.count()} events "
                              f"(venue={venue}). Re-run with --yes.")
            return

        with transaction.atomic():
            MatchedPair.objects.filter(polymarket_market_id__in=m_ids).delete()
            MatchedPair.objects.filter(kalshi_market_id__in=m_ids).delete()
            NormalizedMarket.objects.filter(market_id__in=m_ids).delete()
            MarketOutcome.objects.filter(market_id__in=m_ids).delete()
            deleted_m = RawMarket.objects.filter(id__in=m_ids)._raw_delete(RawMarket.objects.db)
            deleted_e = ev.delete()
            runs.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted markets={deleted_m} events={deleted_e}"))
