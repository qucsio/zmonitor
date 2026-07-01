from django.core.management.base import BaseCommand

from scanner.discovery.kalshi import fetch_markets_for_event


class Command(BaseCommand):
    help = "Lazily fetch & store markets for a single Kalshi event_ticker (matching helper)."

    def add_arguments(self, parser):
        parser.add_argument("event_ticker")

    def handle(self, *args, **opts):
        et = opts["event_ticker"]
        n = fetch_markets_for_event(et)
        self.stdout.write(self.style.SUCCESS(f"[{et}] saved {n} markets"))
