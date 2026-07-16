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
                from scanner import jobs
                jobs.job_started("orderbook")
                res = process_matched_pairs()
                dur = time.time() - t0
                logger.info("orderbook cycle: %d pairs in %.1fs", len(res), dur)
                jobs.job_finished("orderbook", result={"pairs": len(res),
                                  "duration_sec": round(dur, 1)})
            except Exception as exc:  # noqa: BLE001
                logger.exception("orderbook cycle failed")
                from scanner import jobs
                jobs.job_finished("orderbook", error=exc)
            elapsed = time.time() - t0
            time.sleep(max(0.0, interval - elapsed))
