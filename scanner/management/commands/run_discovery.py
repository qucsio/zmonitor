from django.core.management.base import BaseCommand

from scanner.discovery.runner import run_discovery
from scanner.models import VENUE_KALSHI, VENUE_POLYMARKET


class Command(BaseCommand):
    help = "Run discovery for a venue (polymarket / kalshi / all)."

    def add_arguments(self, parser):
        parser.add_argument("--venue", default="all",
                            choices=["polymarket", "kalshi", "all"])
        parser.add_argument("--full", action="store_true",
                            help="full re-scan (default: incremental, stop at known)")

    def handle(self, *args, **opts):
        venue = opts["venue"]
        venues = [VENUE_POLYMARKET, VENUE_KALSHI] if venue == "all" else [venue]
        for v in venues:
            run = run_discovery(v, incremental=not opts["full"])
            self.stdout.write(self.style.SUCCESS(
                f"[{v}] status={run.status} seen={run.markets_seen} "
                f"new={run.markets_new} updated={run.markets_updated}"
                + (f" error={run.error_text}" if run.error_text else "")
            ))
