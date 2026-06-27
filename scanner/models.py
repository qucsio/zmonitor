from django.db import models

VENUE_POLYMARKET = "polymarket"
VENUE_KALSHI = "kalshi"


class RawEvent(models.Model):
    venue = models.CharField(max_length=32)
    venue_event_id = models.CharField(max_length=255)

    title = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=255, null=True, blank=True)
    sport = models.CharField(max_length=255, null=True, blank=True)

    status = models.CharField(max_length=64, null=True, blank=True)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)

    raw_json = models.JSONField()

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    updated_at_remote = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("venue", "venue_event_id")
        indexes = [models.Index(fields=["venue", "status"])]

    def __str__(self):
        return f"{self.venue}:{self.venue_event_id}"


class RawMarket(models.Model):
    venue = models.CharField(max_length=32)
    venue_market_id = models.CharField(max_length=255)
    venue_event_id = models.CharField(max_length=255, null=True, blank=True)

    title = models.TextField(null=True, blank=True)
    question = models.TextField(null=True, blank=True)
    rules_text = models.TextField(null=True, blank=True)
    rules_hash = models.CharField(max_length=128, null=True, blank=True)

    status = models.CharField(max_length=64, null=True, blank=True)

    active = models.BooleanField(null=True, blank=True)
    closed = models.BooleanField(null=True, blank=True)
    archived = models.BooleanField(null=True, blank=True)
    accepting_orders = models.BooleanField(null=True, blank=True)
    enable_orderbook = models.BooleanField(null=True, blank=True)

    start_time = models.DateTimeField(null=True, blank=True)
    close_time = models.DateTimeField(null=True, blank=True)

    raw_json = models.JSONField()

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    updated_at_remote = models.DateTimeField(null=True, blank=True)

    matching_status = models.CharField(max_length=64, default="pending")
    # pending / normalized / matched / rejected / needs_review / ignored

    class Meta:
        unique_together = ("venue", "venue_market_id")
        indexes = [
            models.Index(fields=["venue", "status"]),
            models.Index(fields=["matching_status"]),
        ]

    def __str__(self):
        return f"{self.venue}:{self.venue_market_id}"


class MarketOutcome(models.Model):
    market = models.ForeignKey(RawMarket, on_delete=models.CASCADE, related_name="outcomes")

    venue = models.CharField(max_length=32)
    outcome_side = models.CharField(max_length=16)  # yes / no
    outcome_name = models.TextField(null=True, blank=True)

    token_id = models.CharField(max_length=255, null=True, blank=True)  # Polymarket
    ticker = models.CharField(max_length=255, null=True, blank=True)    # Kalshi

    raw_json = models.JSONField(null=True, blank=True)

    class Meta:
        unique_together = ("market", "outcome_side")


class NormalizedMarket(models.Model):
    market = models.OneToOneField(RawMarket, on_delete=models.CASCADE, related_name="normalized")

    venue = models.CharField(max_length=32)

    game = models.CharField(max_length=128, null=True, blank=True)
    tournament = models.CharField(max_length=255, null=True, blank=True)

    team_a = models.CharField(max_length=255, null=True, blank=True)
    team_b = models.CharField(max_length=255, null=True, blank=True)
    canonical_team_a = models.CharField(max_length=255, null=True, blank=True)
    canonical_team_b = models.CharField(max_length=255, null=True, blank=True)

    start_time_utc = models.DateTimeField(null=True, blank=True)

    market_type = models.CharField(max_length=64, default="unknown")
    # match_winner / series_winner / map_winner / total / spread / unknown

    map_number = models.IntegerField(null=True, blank=True)
    bo_format = models.CharField(max_length=32, null=True, blank=True)

    outcome_yes = models.CharField(max_length=255, null=True, blank=True)
    outcome_no = models.CharField(max_length=255, null=True, blank=True)

    resolution_source = models.TextField(null=True, blank=True)
    rules_summary = models.TextField(null=True, blank=True)

    parser_confidence = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    normalization_status = models.CharField(max_length=64, default="ok")
    risk_flags = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class MatchedPair(models.Model):
    polymarket_market = models.ForeignKey(RawMarket, on_delete=models.CASCADE, related_name="pm_pairs")
    kalshi_market = models.ForeignKey(RawMarket, on_delete=models.CASCADE, related_name="kalshi_pairs")

    status = models.CharField(max_length=64, default="candidate")
    # candidate / matched / rejected / needs_review / disabled / stale_rules

    confidence = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    match_score = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)

    game = models.CharField(max_length=128, null=True, blank=True)
    tournament = models.CharField(max_length=255, null=True, blank=True)

    canonical_team_a = models.CharField(max_length=255, null=True, blank=True)
    canonical_team_b = models.CharField(max_length=255, null=True, blank=True)

    start_time_utc = models.DateTimeField(null=True, blank=True)

    market_type = models.CharField(max_length=64, default="unknown")
    map_number = models.IntegerField(null=True, blank=True)

    outcome_mapping = models.JSONField(default=dict)
    risk_flags = models.JSONField(default=list)

    reject_reason = models.CharField(max_length=255, null=True, blank=True)

    rules_compatibility_score = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    rules_hash_pm = models.CharField(max_length=128, null=True, blank=True)
    rules_hash_kalshi = models.CharField(max_length=128, null=True, blank=True)

    last_checked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("polymarket_market", "kalshi_market")
        indexes = [models.Index(fields=["status"])]


class OpportunityEvent(models.Model):
    pair = models.ForeignKey(MatchedPair, on_delete=models.CASCADE, related_name="opportunities")

    direction = models.CharField(max_length=64)
    # pm_yes_kalshi_no / pm_no_kalshi_yes

    status = models.CharField(max_length=64, default="open")
    # open / closed / stale / invalidated

    ts_start = models.DateTimeField(auto_now_add=True)
    ts_last_seen = models.DateTimeField(auto_now=True)
    ts_end = models.DateTimeField(null=True, blank=True)

    first_gross_edge = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    first_net_edge = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)

    last_gross_edge = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    last_net_edge = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)

    max_gross_edge = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    max_net_edge = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)

    max_size_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    close_reason = models.CharField(max_length=255, null=True, blank=True)
    risk_flags = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["status"]), models.Index(fields=["pair", "status"])]


class OpportunityEdgePoint(models.Model):
    opportunity = models.ForeignKey(OpportunityEvent, on_delete=models.CASCADE, related_name="edge_points")

    ts = models.DateTimeField(auto_now_add=True)

    gross_edge = models.DecimalField(max_digits=18, decimal_places=8)
    net_edge = models.DecimalField(max_digits=18, decimal_places=8)

    size_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    pm_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    kalshi_price = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)

    pm_book_age_ms = models.IntegerField(null=True, blank=True)
    kalshi_book_age_ms = models.IntegerField(null=True, blank=True)

    pm_vwap = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    kalshi_vwap = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)

    risk_flags = models.JSONField(default=list)

    class Meta:
        indexes = [models.Index(fields=["opportunity", "ts"])]


class DiscoveryRun(models.Model):
    venue = models.CharField(max_length=32)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=64, default="running")
    markets_seen = models.IntegerField(default=0)
    markets_new = models.IntegerField(default=0)
    markets_updated = models.IntegerField(default=0)

    error_text = models.TextField(null=True, blank=True)


class ApiHealthLog(models.Model):
    venue = models.CharField(max_length=32)
    endpoint = models.CharField(max_length=255)

    status_code = models.IntegerField(null=True, blank=True)
    ok = models.BooleanField(default=True)

    latency_ms = models.IntegerField(null=True, blank=True)
    error_text = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["venue", "created_at"]), models.Index(fields=["ok"])]
