import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from scanner.clients import kalshi, polymarket

SAMPLES_DIR = Path(settings.BASE_DIR) / "samples"


def _save_json(name, data):
    SAMPLES_DIR.mkdir(exist_ok=True)
    path = SAMPLES_DIR / name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
    return path


def _field_names(obj):
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if isinstance(obj, dict):
        return sorted(obj.keys())
    return []


class Command(BaseCommand):
    help = "Probe Polymarket & Kalshi APIs and save real response samples."

    def handle(self, *args, **opts):
        report = ["# API Probe Report", ""]
        SAMPLES_DIR.mkdir(exist_ok=True)

        # ---------------- Polymarket ----------------
        report.append("## Polymarket")
        if settings.SCANNER["POLYMARKET_ENABLED"]:
            self._probe_polymarket(report)
        else:
            report.append("- disabled")
        report.append("")

        # ---------------- Kalshi ----------------
        report.append("## Kalshi")
        if settings.SCANNER["KALSHI_ENABLED"]:
            self._probe_kalshi(report)
        else:
            report.append("- disabled")
        report.append("")

        report_path = SAMPLES_DIR / "api_probe_report.md"
        report_path.write_text("\n".join(report), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Report written to {report_path}"))

    # ------------------------------------------------------------------
    def _probe_polymarket(self, report):
        # events
        r = polymarket.get_events(limit=20)
        if r.ok:
            _save_json("polymarket_events_response.json", r.data)
            report.append(f"- events: OK ({r.status_code}, {r.latency_ms}ms), "
                          f"count={len(r.data) if isinstance(r.data, list) else 'n/a'}")
            report.append(f"  - fields: {_field_names(r.data)}")
        else:
            report.append(f"- events: FAIL ({r.status_code}) {r.error}")

        # markets - filter for usable ones
        r = polymarket.get_markets(limit=50)
        usable_tokens = []
        if r.ok:
            _save_json("polymarket_markets_response.json", r.data)
            markets = r.data if isinstance(r.data, list) else r.data.get("data", [])
            report.append(f"- markets: OK ({r.status_code}), count={len(markets)}")
            report.append(f"  - fields: {_field_names(markets)}")
            for m in markets:
                if not isinstance(m, dict):
                    continue
                if m.get("enableOrderBook") and m.get("active") and not m.get("closed"):
                    raw = m.get("clobTokenIds")
                    if raw:
                        try:
                            ids = json.loads(raw) if isinstance(raw, str) else raw
                            usable_tokens.extend(ids)
                        except Exception:  # noqa: BLE001
                            pass
                if len(usable_tokens) >= 10:
                    break
            report.append(f"  - usable clobTokenIds found: {len(usable_tokens)}")
        else:
            report.append(f"- markets: FAIL ({r.status_code}) {r.error}")

        # books
        if usable_tokens:
            r = polymarket.get_book(usable_tokens[0])
            if r.ok:
                _save_json("polymarket_books_response.json", r.data)
                report.append(f"- book: OK, fields: {_field_names(r.data)}")
            else:
                report.append(f"- book: FAIL ({r.status_code}) {r.error}")
        else:
            report.append("- book: SKIPPED (no usable tokens)")

    # ------------------------------------------------------------------
    def _probe_kalshi(self, report):
        has_auth = bool(settings.SCANNER["KALSHI_KEY_ID"] and settings.SCANNER["KALSHI_PRIVATE_KEY_PATH"])
        report.append(f"- auth configured: {has_auth}")

        r = kalshi.get_limits()
        if r.ok:
            _save_json("kalshi_limits_response.json", r.data)
            report.append(f"- exchange/status: OK, fields: {_field_names(r.data)}")
        else:
            report.append(f"- exchange/status: FAIL ({r.status_code}) {r.error}")

        if has_auth:
            r = kalshi.get_account_limits()
            if r.ok:
                _save_json("kalshi_account_limits_response.json", r.data)
                report.append(f"- portfolio/balance: OK, fields: {_field_names(r.data)}")
            else:
                report.append(f"- portfolio/balance: FAIL ({r.status_code}) {r.error}")

        r = kalshi.get_markets(limit=50, status="open")
        first_ticker = None
        if r.ok:
            _save_json("kalshi_markets_response.json", r.data)
            markets = r.data.get("markets", []) if isinstance(r.data, dict) else []
            report.append(f"- markets: OK, count={len(markets)}")
            report.append(f"  - fields: {_field_names(markets)}")
            if markets:
                first_ticker = markets[0].get("ticker")
        else:
            report.append(f"- markets: FAIL ({r.status_code}) {r.error}")

        if first_ticker:
            r = kalshi.get_orderbook(first_ticker)
            if r.ok:
                _save_json("kalshi_orderbook_response.json", r.data)
                report.append(f"- orderbook ({first_ticker}): OK, fields: {_field_names(r.data)}")
            else:
                report.append(f"- orderbook: FAIL ({r.status_code}) {r.error}")
        else:
            report.append("- orderbook: SKIPPED (no ticker)")
