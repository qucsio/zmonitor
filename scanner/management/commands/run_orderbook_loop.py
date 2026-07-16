import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from scanner.orderbook import process_matched_pairs

logger = logging.getLogger("scanner")


class Command(BaseCommand):
    help = "Continuously refresh orderbooks + forks for matched pairs (REST mode)."

    def handle(self, *args, **opts):
        interval = settings.SCANNER["ORDERBOOK_REFRESH_SEC"]
        self.stdout.write(f"orderbook loop started (refresh {interval}s, REST mode)")
        while True:
            t0 = time.time()
            try:
                n = process_matched_pairs()
                logger.info("orderbook cycle: %d pairs in %.1fs", n, time.time() - t0)
            except Exception:  # noqa: BLE001
                logger.exception("orderbook cycle failed")
            elapsed = time.time() - t0
            time.sleep(max(0.0, interval - elapsed))
