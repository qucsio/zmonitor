from django.core.management.base import BaseCommand

from scanner.orderbook import process_matched_pairs


class Command(BaseCommand):
    help = "Fetch orderbooks for matched pairs once, compute forks, write Redis."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--calculate", action="store_true",
                            help="(compat flag; forks are always calculated)")

    def handle(self, *args, **opts):
        results = process_matched_pairs(limit=opts["limit"])
        results.sort(key=lambda r: max(
            float(r["fork_a_net"] or -9), float(r["fork_b_net"] or -9)), reverse=True)
        for r in results:
            self.stdout.write(
                f"  /pairs/{r['id']}/  forkA_net={r['fork_a_net']}  forkB_net={r['fork_b_net']}")
        self.stdout.write(self.style.SUCCESS(
            f"Processed {len(results)} pairs (best edge first). Open the URLs above."))
