from django.core.management.base import BaseCommand

from scanner.normalize import normalize_pending


class Command(BaseCommand):
    help = "Normalize RawMarkets into NormalizedMarket rows."

    def add_arguments(self, parser):
        parser.add_argument("--venue", default=None, choices=["polymarket", "kalshi"])
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **opts):
        n = normalize_pending(venue=opts["venue"], limit=opts["limit"])
        self.stdout.write(self.style.SUCCESS(f"Normalized {n} markets"))
