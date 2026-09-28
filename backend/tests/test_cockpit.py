"""驾驶舱 (cockpit) 聚合服务测试 — 数据健康/主线认证/提醒聚合。"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.services.cockpit import cockpit_overview, data_health, mainline_certification


def _mkdirs(data_dir: Path) -> None:
    for sub in ("kline_daily", "kline_daily_enriched", "plans", "reviews"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)


def _seed_mainline(data_dir: Path, kind: str = "concept") -> None:
    """造 3 天主线: member_a 连续 rank1 高分 (应认证 gold), member_b 连续但弱 (up),
    member_c 仅末日出强分 (pulse)。"""
    days = [(date(2026, 9, 22) + timedelta(days=i)).isoformat() for i in range(3)]
    rows = []
    for i, d in enumerate(days):
        rows.append({"date": d, "kind": kind, "member": "member_a",
                     "limit_up_count": 5, "ge2_count": 2, "max_boards": 3,
                     "boards_sum": 8, "rungs_filled": 2, "leader_symbol": "000001",
                     "score": 80.0, "rank": 1})
        rows.append({"date": d, "kind": kind, "member": "member_b",
                     "limit_up_count": 2, "ge2_count": 0, "max_boards": 1,
                     "boards_sum": 2, "rungs_filled": 0, "leader_symbol": "600000",
                     "score": 55.0, "rank": 2})
    rows.append({"date": days[-1], "kind": kind, "member": "member_c",
                 "limit_up_count": 3, "ge2_count": 1, "max_boards": 2,
                 "boards_sum": 4, "rungs_filled": 1, "leader_symbol": "300001",
                 "score": 70.0, "rank": 3})
    (data_dir / "mainline_history" / "part.parquet").parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(data_dir / "mainline_history" / "part.parquet")


def _seed_regime(data_dir: Path) -> None:
    (data_dir / "regime_history").mkdir(parents=True, exist_ok=True)
    pl.DataFrame([{
        "date": "2026-09-25", "score": 72.0, "state": "active",
        "state_label": "活跃", "phase": "up", "phase_label": "主升",
        "max_consecutive": 3, "first_board": 10, "ge2_count": 5,
        "promo_rate": 0.6, "seal_rate": 0.7,
    }]).write_parquet(data_dir / "regime_history" / "part.parquet")


def test_cockpit_empty_data_dir(tmp_path):
    _mkdirs(tmp_path)
    out = cockpit_overview(tmp_path)
    assert out["status"] == "attention"
    levels = {a["level"] for a in out["alerts"]}
    assert "error" in levels
    assert not out["mainline"]["available"]
    assert not out["regime"]["available"]
    assert all(not l["partitions"] for l in out["health"]["layers"])


def test_data_health_partitions(tmp_path):
    _mkdirs(tmp_path)
    (tmp_path / "kline_daily" / "date=2026-09-24").mkdir()
    (tmp_path / "kline_daily" / "date=2026-09-25").mkdir()
    (tmp_path / "kline_daily_enriched" / "date=2026-09-25").mkdir()
    health = data_health(tmp_path)
    layers = {l["key"]: l for l in health["layers"]}
    assert layers["kline_daily"]["partitions"] == 2
    assert layers["kline_daily"]["latest"] == "2026-09-25"
    assert health["enriched_behind_daily"] is False


def test_mainline_gold_certification(tmp_path):
    _mkdirs(tmp_path)
    _seed_mainline(tmp_path)
    cert = mainline_certification(tmp_path)
    assert cert["available"] is True
    by_member = {i["member"]: i for i in cert["items"]}
    assert by_member["member_a"]["level"] == "gold"      # 3 天连续 rank1 + 均分 80
    assert by_member["member_a"]["streak_days"] >= 3
    assert by_member["member_b"]["level"] == "up"        # 连续上榜但均分 <60
    assert by_member["member_c"]["level"] == "pulse"     # 仅末日出强分
    assert cert["gold_count"] == 1
    assert cert["leaders"][0]["symbol"] == "000001"


def test_cockpit_api_overview(tmp_path):
    """API 层: 挂独立 router, 空数据返回 attention + 健康层。"""
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.cockpit import router

    _mkdirs(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    resp = TestClient(app).get("/api/cockpit/overview")
    assert resp.status_code == 200
    d = resp.json()
    assert d["status"] == "attention"
    assert len(d["health"]["layers"]) == 11


def test_cockpit_full_data_alerts(tmp_path):
    _mkdirs(tmp_path)
    _seed_mainline(tmp_path)
    _seed_regime(tmp_path)
    # 计划已生成未复盘 → 应有 warn
    (tmp_path / "plans" / "P20260925-001.json").write_text(
        '{"plan_id":"P20260925-001","trade_date":"2026-09-25","status":"pending",'
        '"entries":[{"symbol":"000001"}]}', encoding="utf-8")
    out = cockpit_overview(tmp_path)
    titles = [a["title"] for a in out["alerts"]]
    assert any("待复盘" in t for t in titles)
    assert any(a["level"] == "error" for a in out["alerts"])  # enriched 仍空
    assert out["regime"]["available"] is True
