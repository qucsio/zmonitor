from django.contrib import admin

from . import models


@admin.register(models.RawEvent)
class RawEventAdmin(admin.ModelAdmin):
    list_display = ("venue", "venue_event_id", "title", "status", "start_time", "last_seen_at")
    list_filter = ("venue", "status")
    search_fields = ("venue_event_id", "title")


@admin.register(models.RawMarket)
class RawMarketAdmin(admin.ModelAdmin):
    list_display = ("venue", "venue_market_id", "title", "status", "matching_status",
                    "active", "closed", "enable_orderbook", "last_seen_at")
    list_filter = ("venue", "status", "matching_status", "active", "closed", "enable_orderbook")
    search_fields = ("venue_market_id", "title", "question")


@admin.register(models.MarketOutcome)
class MarketOutcomeAdmin(admin.ModelAdmin):
    list_display = ("market", "venue", "outcome_side", "outcome_name", "token_id", "ticker")
    list_filter = ("venue", "outcome_side")


@admin.register(models.NormalizedMarket)
class NormalizedMarketAdmin(admin.ModelAdmin):
    list_display = ("market", "venue", "game", "market_type", "map_number",
                    "canonical_team_a", "canonical_team_b", "normalization_status")
    list_filter = ("venue", "game", "market_type", "normalization_status")


@admin.register(models.MatchedPair)
class MatchedPairAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "confidence", "game", "market_type", "map_number",
                    "canonical_team_a", "canonical_team_b", "updated_at")
    list_filter = ("status", "game", "market_type")


@admin.register(models.OpportunityEvent)
class OpportunityEventAdmin(admin.ModelAdmin):
    list_display = ("id", "pair", "direction", "status", "ts_start", "ts_end",
                    "max_net_edge", "max_size_usd")
    list_filter = ("status", "direction")


@admin.register(models.OpportunityEdgePoint)
class OpportunityEdgePointAdmin(admin.ModelAdmin):
    list_display = ("id", "opportunity", "ts", "gross_edge", "net_edge", "size_usd")


@admin.register(models.DiscoveryRun)
class DiscoveryRunAdmin(admin.ModelAdmin):
    list_display = ("id", "venue", "status", "started_at", "finished_at",
                    "markets_seen", "markets_new", "markets_updated")
    list_filter = ("venue", "status")


@admin.register(models.ApiHealthLog)
class ApiHealthLogAdmin(admin.ModelAdmin):
    list_display = ("id", "venue", "endpoint", "status_code", "ok", "latency_ms", "created_at")
    list_filter = ("venue", "ok")
