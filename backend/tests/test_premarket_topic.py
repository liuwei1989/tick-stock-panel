"""盘前研报 + 题材表格 service/API 测试。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.premarket_report import router as pm_router
from app.api.topic_table import router as tt_router
from app.services import premarket_report as pm
from app.services import topic_table as tt


def _seed(tmp_path: Path) -> None:
    """造 mainline_history (member_a 连续金牌) + regime + plans。"""
    (tmp_path / "mainline_history").mkdir(parents=True, exist_ok=True)
    days = [(date(2026, 9, 22) + timedelta(days=i)).isoformat() for i in range(3)]
    rows = []
    for d in days:
        rows.append({"date": d, "kind": "concept", "member": "AI算力",
                     "limit_up_count": 5, "ge2_count": 2, "max_boards": 3,
                     "boards_sum": 8, "rungs_filled": 2, "leader_symbol": "000001",
                     "score": 80.0, "rank": 1})
    pl.DataFrame(rows).write_parquet(tmp_path / "mainline_history" / "part.parquet")
    (tmp_path / "regime_history").mkdir(exist_ok=True)
    pl.DataFrame([{
        "date": "2026-09-25", "score": 72.0, "state": "active",
        "state_label": "活跃", "phase": "up", "phase_label": "主升",
        "max_consecutive": 3, "first_board": 10, "ge2_count": 5,
        "promo_rate": 0.6, "seal_rate": 0.7,
    }]).write_parquet(tmp_path / "regime_history" / "part.parquet")


def _pm_client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(pm_router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    return TestClient(app)


def test_premarket_structured_gold(tmp_path):
    _seed(tmp_path)
    s = pm.build_structured(tmp_path, date(2026, 9, 25))
    assert s["available"] is True
    assert s["mainline_top"][0]["member"] == "AI算力"
    assert s["mainline_top"][0]["level"] == "gold"
    assert "AI算力" in s["summary"]
    assert s["catalysts"], "金牌主线应给出催化锚点"


def test_premarket_markdown(tmp_path):
    _seed(tmp_path)
    md = pm.build_markdown(tmp_path, date(2026, 9, 25))
    assert "盘前结构化研报" in md
    assert "AI算力" in md
    assert "000001" in md


def test_premarket_api_context_and_save(tmp_path):
    _seed(tmp_path)
    c = _pm_client(tmp_path)
    r = c.get("/api/premarket-report/context")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    # 保存并读回
    pm.save_report(tmp_path, d)
    r2 = c.get("/api/premarket-report")
    assert r2.status_code == 200
    assert r2.json()["as_of"] == d["as_of"]


def test_premarket_ai_fallback(tmp_path, monkeypatch):
    """AI 未配置 → generate 返回规则版 fallback。"""
    _seed(tmp_path)
    c = _pm_client(tmp_path)
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: False)
    r = c.post("/api/premarket-report/generate", json={})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["fallback"] is True
    assert d["report"]["ai"] is False


def test_topic_table_available(tmp_path):
    _seed(tmp_path)
    t = tt.get_topic_table(tmp_path)
    assert t["available"] is True
    assert t["rows"][0]["member"] == "AI算力"
    assert t["rows"][0]["level"] == "gold"
    assert t["gold_count"] == 1


def test_topic_members_save_and_ocr_unavailable(tmp_path):
    _seed(tmp_path)
    # 保存自定义成分
    tt.save_topic_members(tmp_path, "AI算力", ["000001", "300001"])
    m = tt.get_topic_members(tmp_path, "AI算力")
    assert m["leader_symbol"] == "000001"
    assert m["custom_count"] == 2
    # OCR 无引擎时给出明确错误
    res = tt.import_topic_image(tmp_path, "AI算力", b"\x89PNG\r\n")
    if not res.get("ok"):
        assert "OCR" in res.get("message", "") or "不可用" in res.get("message", "")


def test_topic_table_api(tmp_path):
    _seed(tmp_path)
    app = FastAPI()
    app.include_router(tt_router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    c = TestClient(app)
    r = c.get("/api/topic-table")
    assert r.status_code == 200
    assert r.json()["available"] is True
    r2 = c.put("/api/topic-table/AI算力/members", json={"symbols": ["000001"]})
    assert r2.status_code == 200
    r3 = c.get("/api/topic-table/AI算力/members")
    assert r3.json()["custom_count"] == 1
