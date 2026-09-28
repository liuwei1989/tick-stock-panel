"""策略进化 API — applied 参数端点。"""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.evolution import router


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    return TestClient(app)


def test_evolution_applied_empty(tmp_path):
    resp = _client(tmp_path).get("/api/evolution/applied")
    assert resp.status_code == 200
    assert resp.json() == {"applied": {}}


def test_evolution_applied_returns_overrides(tmp_path):
    applied = {
        "strategy_a": {
            "run_id": "strategy_a-abc123",
            "params": {"lookback": 30, "threshold": 1.2},
            "exec_params": {"max_positions": 5, "holding_days": 5},
            "applied_at": "2026-09-28",
            "name": "候选-加速",
        }
    }
    (tmp_path / "evolution_applied.json").write_text(
        json.dumps(applied, ensure_ascii=False), encoding="utf-8")
    resp = _client(tmp_path).get("/api/evolution/applied")
    assert resp.status_code == 200
    assert resp.json()["applied"]["strategy_a"]["name"] == "候选-加速"
