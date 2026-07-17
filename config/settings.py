import os
from decimal import Decimal
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(key, default=False):
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_decimal(key, default):
    val = os.getenv(key)
    return Decimal(val) if val not in (None, "") else Decimal(str(default))


def env_int(key, default):
    val = os.getenv(key)
    return int(val) if val not in (None, "") else default


SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
DEBUG = env_bool("DEBUG", True)
APP_ENV = os.getenv("APP_ENV", "dev")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [f"http://{h}:8000" for h in ALLOWED_HOSTS] + [f"http://{h}" for h in ALLOWED_HOSTS]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "scanner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        os.getenv("DATABASE_URL", "postgres://arb:arb@postgres:5432/arbscanner"),
        conn_max_age=60,
    )
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---- Redis / Celery ----
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_DEFAULT_QUEUE = "discovery"
CELERY_TIMEZONE = "UTC"

CELERY_BEAT_SCHEDULE = {
    "discovery-all": {
        "task": "scanner.tasks.discover_all",
        "schedule": float(env_int("DISCOVERY_INTERVAL_SEC", 900)),
    },
    "match-all": {
        "task": "scanner.tasks.match_markets_task",
        "schedule": float(env_int("MATCHING_INTERVAL_SEC", 30) * 30),  # ~15 min
    },
    "reap-stale": {
        "task": "scanner.tasks.reap_stale_task",
        "schedule": float(env_int("REAP_INTERVAL_SEC", 300)),
    },
}

# ---- Scanner config ----
SCANNER = {
    "DISCOVERY_INTERVAL_SEC": env_int("DISCOVERY_INTERVAL_SEC", 900),
    "REAP_INTERVAL_SEC": env_int("REAP_INTERVAL_SEC", 300),
    "DISCOVERY_MAX_PAGES": env_int("DISCOVERY_MAX_PAGES", 40),
    "DISCOVERY_PAGE_THROTTLE_MS": env_int("DISCOVERY_PAGE_THROTTLE_MS", 250),
    # Polymarket tag slugs to keep (substring match against event tag slugs/labels)
    "DISCOVERY_PM_TAGS": [t.strip().lower() for t in os.getenv(
        "DISCOVERY_PM_TAGS",
        "esports,csgo,cs2,counter-strike,dota,league-of-legends,valorant,"
        "rocket-league,overwatch,nba,nfl,nhl,mlb,soccer,tennis,ufc,mma,boxing,f1",
    ).split(",") if t.strip()],
    # Kalshi event categories to keep (case-insensitive substring match)
    "DISCOVERY_KALSHI_CATEGORIES": [c.strip().lower() for c in os.getenv(
        "DISCOVERY_KALSHI_CATEGORIES", "sports,esports",
    ).split(",") if c.strip()],
    "MATCHING_INTERVAL_SEC": env_int("MATCHING_INTERVAL_SEC", 30),
    "ORDERBOOK_MODE": os.getenv("ORDERBOOK_MODE", "rest"),
    "ORDERBOOK_REFRESH_SEC": env_int("ORDERBOOK_REFRESH_SEC", 1),
    "ORDERBOOK_REST_RESYNC_SEC": env_int("ORDERBOOK_REST_RESYNC_SEC", 30),
    "STALE_BOOK_MS": env_int("STALE_BOOK_MS", 2000),
    "OPPORTUNITY_NET_EDGE_THRESHOLD": env_decimal("OPPORTUNITY_NET_EDGE_THRESHOLD", "0.01"),
    "OPPORTUNITY_EDGE_POINT_INTERVAL_SEC": env_int("OPPORTUNITY_EDGE_POINT_INTERVAL_SEC", 1),
    "EDGE_POINT_DELTA_THRESHOLD": env_decimal("EDGE_POINT_DELTA_THRESHOLD", "0.001"),
    "DEFAULT_FEE_BUFFER": env_decimal("DEFAULT_FEE_BUFFER", "0.005"),
    "DEFAULT_SLIPPAGE_BUFFER": env_decimal("DEFAULT_SLIPPAGE_BUFFER", "0.005"),
    # Kalshi trading fee: ceil_cents(rate * contracts * P * (1-P)); rate ~0.07. PM CLOB is 0%.
    "KALSHI_FEE_RATE": env_decimal("KALSHI_FEE_RATE", "0.07"),
    "PM_FEE_RATE": env_decimal("PM_FEE_RATE", "0"),
    # Per-contract slippage buffer applied to the marginal edge when walking the book.
    "SLIPPAGE_PER_CONTRACT": env_decimal("SLIPPAGE_PER_CONTRACT", "0.002"),
    "VWAP_SIZES_USD": [Decimal(x) for x in os.getenv("VWAP_SIZES_USD", "10,25,50,100,250,500,1000").split(",") if x.strip()],
    "POLYMARKET_ENABLED": env_bool("POLYMARKET_ENABLED", True),
    "POLYMARKET_GAMMA_BASE": os.getenv("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com"),
    "POLYMARKET_CLOB_BASE": os.getenv("POLYMARKET_CLOB_BASE", "https://clob.polymarket.com"),
    "POLYMARKET_WS_MARKET_URL": os.getenv("POLYMARKET_WS_MARKET_URL", ""),
    "POLYMARKET_PROXY_URL": os.getenv("POLYMARKET_PROXY_URL", "") or None,
    "KALSHI_ENABLED": env_bool("KALSHI_ENABLED", True),
    "KALSHI_ENV": os.getenv("KALSHI_ENV", "prod"),
    "KALSHI_API_BASE": os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2"),
    "KALSHI_WS_URL": os.getenv("KALSHI_WS_URL", ""),
    "KALSHI_KEY_ID": os.getenv("KALSHI_KEY_ID", ""),
    "KALSHI_PRIVATE_KEY_PATH": os.getenv("KALSHI_PRIVATE_KEY_PATH", ""),
    "KALSHI_PROXY_URL": os.getenv("KALSHI_PROXY_URL", "") or None,
    "LLM_MATCHING_ENABLED": env_bool("LLM_MATCHING_ENABLED", False),
    "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "openai"),
    "LLM_MODEL": os.getenv("LLM_MODEL", "gpt-4o-mini"),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
    "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "OPENAI_PROXY_URL": os.getenv("OPENAI_PROXY_URL", "") or None,
    # Matching thresholds
    "MATCH_AUTO_THRESHOLD": env_decimal("MATCH_AUTO_THRESHOLD", "0.95"),
    "MATCH_REVIEW_THRESHOLD": env_decimal("MATCH_REVIEW_THRESHOLD", "0.85"),
    "MATCH_TIME_WINDOW_HOURS": env_int("MATCH_TIME_WINDOW_HOURS", 24),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"scanner": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
