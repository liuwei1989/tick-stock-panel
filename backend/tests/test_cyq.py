from __future__ import annotations

from datetime import date, timedelta

import polars as pl
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.stock_analysis import router
from app.indicators.cyq import compute_chip_metrics


def _bars(turnover: float = 5.0) -> pl.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for i in range(40):
        close = 10 + i * 0.05
        rows.append({
            "date": start + timedelta(days=i),
            "open": close - 0.03,
            "high": close + 0.08,
            "low": close - 0.08,
            "close": close,
            "volume": 1000.0 + i * 10,
            "turnover_rate": turnover,
        })
    return pl.DataFrame(rows)


def test_chip_metrics_returns_cost_bands_and_profit_ratio() -> None:
    result = compute_chip_metrics(_bars())

    assert result["available"] is True
    assert result["trading_days"] == 40
    assert result["as_of"] == "2026-02-09"
    assert result["cost_70"]["lower"] < result["cost_70"]["upper"]
    assert result["cost_90"]["lower"] <= result["cost_70"]["lower"]
    assert result["cost_90"]["upper"] >= result["cost_70"]["upper"]
    assert 0 <= result["profitable_ratio"] <= 1
    assert result["average_cost"] is not None


def test_chip_metrics_requires_minimum_history_and_turnover() -> None:
    assert compute_chip_metrics(_bars().head(19))["available"] is False
    assert compute_chip_metrics(_bars().drop("turnover_rate"))["available"] is False


def test_chip_metrics_turnover_decay_weights_recent_cost_more() -> None:
    low_turnover = compute_chip_metrics(_bars(turnover=0.0))
    high_turnover = compute_chip_metrics(_bars(turnover=35.0))

    assert high_turnover["available"] is True
    assert low_turnover["average_cost"] < high_turnover["average_cost"]


def test_cyq_api_uses_repository_and_returns_explicit_unavailable_state() -> None:
    class Repo:
        def resolve_asset_type(self, _symbol: str) -> str:
            return "stock"

        def get_daily_asset(self, *_args) -> pl.DataFrame:
            return _bars()

    app = FastAPI()
    app.include_router(router)
    app.state.repo = Repo()
    response = TestClient(app).get("/api/stock-analysis/cyq", params={"symbol": "000001.SZ"})

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000001.SZ"
    assert body["available"] is True
    assert body["cost_90"]["lower"] < body["cost_90"]["upper"]
