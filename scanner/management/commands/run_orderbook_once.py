from django.core.management.base import BaseCommand

from scanner.orderbook import process_matched_pairs


class Command(BaseCommand):
    help = "Fetch orderbooks for matched pairs once, compute forks, write Redis."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--calculate", action="store_true",
                            help="(compat flag; forks are always calculated)")

    def handle(self, *args, **opts):
        n = process_matched_pairs(limit=opts["limit"])
        self.stdout.write(self.style.SUCCESS(f"Processed {n} matched pairs"))
