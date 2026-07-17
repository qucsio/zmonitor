import time
from decimal import Decimal

from django.core.management.base import BaseCommand

from scanner import opportunities
from scanner.models import MatchedPair, RawMarket, VENUE_KALSHI, VENUE_POLYMARKET


def _fake_state(net):
    """Build a minimal live-state dict with a given net edge on fork A."""
    net = Decimal(str(net))
    size = Decimal("150.00")
    fork_a = {
        "direction": "pm_yes_kalshi_no",
        "top_net_edge": str(net),
        "exec_edge": str(net), "exec_size_usd": str(size), "exec_contracts": "150",
        "profit_usd": str((net * size).quantize(Decimal("0.0001"))),
        "best_pm_vwap": "0.50", "best_kalshi_vwap": str(Decimal("0.49") - net),
        "max_size_usd": str(size),
        "ladder": [{"size": "150", "net": str(net), "fillable": "150"}],
    }
    fork_b = {"direction": "pm_no_kalshi_yes", "top_net_edge": "-0.05",
              "exec_size_usd": "0.00", "profit_usd": "0", "max_size_usd": "0.00", "ladder": []}
    return {"fork_a": fork_a, "fork_b": fork_b, "risk_flags": [],
            "pm_book_age_ms": 100, "kalshi_book_age_ms": 120}


class Command(BaseCommand):
    help = "Create a fake matched pair and drive fake edge updates to test lifecycle."

    def handle(self, *args, **opts):
        pm, _ = RawMarket.objects.get_or_create(
            venue=VENUE_POLYMARKET, venue_market_id="SIM_PM",
            defaults={"title": "SIM PM", "raw_json": {}, "closed": False})
        k, _ = RawMarket.objects.get_or_create(
            venue=VENUE_KALSHI, venue_market_id="SIM_KALSHI",
            defaults={"title": "SIM Kalshi", "raw_json": {}, "closed": False})
        pair, _ = MatchedPair.objects.get_or_create(
            polymarket_market=pm, kalshi_market=k,
            defaults={"status": "matched", "market_type": "match_winner",
                      "outcome_mapping": {"pm_yes": "kalshi_yes", "pm_no": "kalshi_no"}})
        pair.status = "matched"
        pair.save(update_fields=["status"])

        # below threshold -> above (open) -> rising (max) -> falling -> below (close)
        sequence = ["0.000", "0.005", "0.020", "0.035", "0.028", "0.012", "-0.002"]
        for net in sequence:
            res = opportunities.process(pair, _fake_state(net))
            self.stdout.write(f"net={net} -> {res}")
            time.sleep(1.1)

        opp = pair.opportunities.order_by("-id").first()
        self.stdout.write(self.style.SUCCESS(
            f"Done. Opportunity #{opp.id if opp else '-'} status={opp.status if opp else '-'} "
            f"points={opp.edge_points.count() if opp else 0} "
            f"max_net={opp.max_net_edge if opp else '-'}  "
            f"View: /opportunities/{opp.id if opp else ''}/"))
