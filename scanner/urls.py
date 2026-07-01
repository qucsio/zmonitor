from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/", views.dashboard, name="dashboard_alt"),
    path("markets/", views.markets, name="markets"),
    path("markets/<int:pk>/", views.market_detail, name="market_detail"),
    path("pairs/", views.pairs, name="pairs"),
    path("pairs/<int:pk>/", views.pair_detail, name="pair_detail"),
    path("pairs/<int:pk>/<str:action>/", views.pair_action, name="pair_action"),
    path("health/", views.health, name="health"),
    path("discovery/run/", views.run_discovery_view, name="discovery_run"),
    path("matching/run/", views.run_matching_view, name="matching_run"),
]
