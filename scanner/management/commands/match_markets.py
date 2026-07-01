from django.core.management.base import BaseCommand

from scanner.matching import run_matching
from scanner.normalize import normalize_pending
from scanner.models import VENUE_POLYMARKET


class Command(BaseCommand):
    help = "Normalize Polymarket markets and match them against Kalshi (lazy-fetched)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None,
                            help="limit number of PM markets considered")
        parser.add_argument("--skip-normalize", action="store_true")
        parser.add_argument("--max-event-fetches", type=int, default=200)

    def handle(self, *args, **opts):
        if not opts["skip_normalize"]:
            n = normalize_pending(venue=VENUE_POLYMARKET, limit=opts["limit"])
            self.stdout.write(f"Normalized {n} Polymarket markets")
        stats = run_matching(limit=opts["limit"],
                             max_event_fetches=opts["max_event_fetches"])
        self.stdout.write(self.style.SUCCESS(
            "matching: " + " ".join(f"{k}={v}" for k, v in stats.items())))
