from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/", views.dashboard, name="dashboard_alt"),
    path("markets/", views.markets, name="markets"),
    path("markets/<int:pk>/", views.market_detail, name="market_detail"),
    path("health/", views.health, name="health"),
    path("discovery/run/", views.run_discovery_view, name="discovery_run"),
]
