# Polymarket × Kalshi Arbitrage Scanner

Monitoring service for buy/buy arbitrage opportunities between Polymarket and Kalshi.
**No trading / order placement.** Read-only scanner.

## Architecture principle
PostgreSQL = metadata, matched pairs, opportunity history.
Realtime state = in-memory (orderbook worker) + Redis.
PostgreSQL is **not** a realtime message bus.

## Stages status
- [x] Stage 0 — Project skeleton (Docker Compose, Django, Postgres, Redis, health page)
- [x] Stage 1 — `api_probe` management command (saves real API samples)
- [x] Stage 2 — Discovery (Polymarket + Kalshi, idempotent upsert, markets UI)
- [x] Stage 3 — Normalization (market_type/teams/map, team aliases)
- [x] Stage 4 — Matching (team blocking + scoring + optional OpenAI gray-zone, pairs UI)
- [x] Stage 5 — Orderbook REST polling (PM books + Kalshi ask reconstruction, Decimal, Redis)
- [x] Stage 6 — Fork calculation (buy/buy A/B, VWAP ladder, fee/slippage, stale-book flags)
- [x] Stage 7 — Opportunity lifecycle + edge dynamics (open/update/close, edge-point policy, chart)
- [ ] Stage 8 — Live dashboard (SSE)
- [ ] Stage 9 — Hybrid WS mode

## Quick start
```bash
cp .env.example .env        # edit secrets/keys as needed
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py api_probe
```

Then open:
- http://localhost:8005/            dashboard
- http://localhost:8005/health/     health page
- http://localhost:8005/admin/      Django admin

## api_probe
Calls the real Polymarket (Gamma + CLOB) and Kalshi endpoints, saves responses
into `samples/*.json` and writes `samples/api_probe_report.md` summarizing which
fields actually came back. Kalshi auth is optional — set `KALSHI_KEY_ID` and
`KALSHI_PRIVATE_KEY_PATH` in `.env` to probe authenticated endpoints.
```bash
docker compose exec web python manage.py api_probe
```
