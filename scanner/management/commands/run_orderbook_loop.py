import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Orderbook worker loop (placeholder until Stage 5)."

    def handle(self, *args, **opts):
        self.stdout.write("orderbook worker started (stub) — idle until Stage 5 is implemented")
        while True:
            time.sleep(5)
