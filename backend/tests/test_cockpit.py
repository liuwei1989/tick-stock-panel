"""驾驶舱 (cockpit) 聚合服务测试 — 数据健康/主线认证/提醒聚合/新鲜度/节点时间线。"""
from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from app.services.cockpit import (
    cockpit_overview,
    cockpit_session,
    data_health,
    mainline_certification,
)


def _mkdirs(data_dir: Path) -> None:
    for sub in ("kline_daily", "kline_daily_enriched", "plans", "reviews"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)


def _layer(health: dict, key: str) -> dict:
    return next(lyr for lyr in health["layers"] if lyr["key"] == key)


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
    assert len(d["health"]["layers"]) == 14


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


def test_freshness_realtime_stale_and_missing(tmp_path):
    """分钟/实时层: 当天 fresh, 交易日滞后 stale, 缺失 missing。"""
    _mkdirs(tmp_path)
    as_of = date(2026, 9, 29)  # 周二, 交易日
    (tmp_path / "kline_minute" / "date=2026-09-29").mkdir(parents=True)
    h = data_health(tmp_path, as_of=as_of)
    assert _layer(h, "kline_minute")["freshness"]["status"] == "fresh"

    shutil.rmtree(tmp_path / "kline_minute" / "date=2026-09-29")
    (tmp_path / "kline_minute" / "date=2026-09-28").mkdir(parents=True)  # 滞后 1 交易日
    h = data_health(tmp_path, as_of=as_of)
    fr = _layer(h, "kline_minute")["freshness"]
    assert fr["status"] == "stale" and fr["lag_days"] == 1

    shutil.rmtree(tmp_path / "kline_minute")
    h = data_health(tmp_path, as_of=as_of)
    assert _layer(h, "kline_minute")["freshness"]["status"] == "missing"


def test_freshness_daily_t_plus_one_tolerance(tmp_path):
    """日频层: 容忍 T+1 (上一交易日 fresh), 更早 stale。"""
    _mkdirs(tmp_path)
    as_of = date(2026, 9, 29)
    (tmp_path / "kline_daily" / "date=2026-09-24").mkdir(parents=True)  # 上周五, 滞后>1交易日
    h = data_health(tmp_path, as_of=as_of)
    assert _layer(h, "kline_daily")["freshness"]["status"] == "stale"

    shutil.rmtree(tmp_path / "kline_daily" / "date=2026-09-24")
    (tmp_path / "kline_daily" / "date=2026-09-28").mkdir(parents=True)  # 周一 (T+1)
    h = data_health(tmp_path, as_of=as_of)
    assert _layer(h, "kline_daily")["freshness"]["status"] == "fresh"


def test_freshness_stale_alerts(tmp_path):
    """滞后层应聚合为 warn 提醒 (链路 vs 实时源分开), 并带可处理动作。"""
    _mkdirs(tmp_path)
    stale_day = (date.today() - timedelta(days=10)).isoformat()  # 远早于上一交易日 → stale
    (tmp_path / "kline_daily" / f"date={stale_day}").mkdir(parents=True)
    out = cockpit_overview(tmp_path)
    stale = next(a for a in out["alerts"] if "数据链路滞后" in a["title"])
    assert stale["level"] == "warn"
    assert stale["action"] == "pipeline"  # 链路滞后 → 运行盘后管道


def test_alert_actions_mapping(tmp_path):
    """各类提醒应映射到正确的处理动作 (供驾驶舱一键处理)。"""
    _mkdirs(tmp_path)
    out = cockpit_overview(tmp_path)
    by_title = {a["title"]: a for a in out["alerts"]}
    assert by_title["日线数据为空"]["action"] == "pipeline"
    assert by_title["富化行情未生成"]["action"] == "rebuild_enriched"
    assert by_title["市场环境未生成"]["action"] == "regime_recompute"
    assert by_title["主线认证不可用"]["action"] == "mainline_recompute"
    assert by_title["今日尚无计划"]["action"] == "generate_plan"
    assert by_title["未应用进化参数"]["action"] == "evolution_run"
    # 计划待复盘 → review_plan 且带 plan_id
    (tmp_path / "plans" / "P20260925-001.json").write_text(
        '{"plan_id":"P20260925-001","trade_date":"2026-09-25","status":"pending",'
        '"entries":[{"symbol":"000001"}]}', encoding="utf-8")
    out2 = cockpit_overview(tmp_path)
    pend = next(a for a in out2["alerts"] if "待复盘" in a["title"])
    assert pend["action"] == "review_plan"
    assert pend["action_payload"]["plan_id"] == "P20260925-001"


def test_session_node_states(tmp_path):
    """节点时间线: 按时间标 done/active/pending, 并交叉引用今日数据。"""
    noon = datetime(2026, 9, 29, 10, 30)  # 周二 10:30 → 早盘 active
    s = cockpit_session(now=noon)
    assert s["current"] == "早盘"
    assert s["trading_day"] is True
    states = {n["key"]: n["state"] for n in s["nodes"]}
    assert states["premarket"] == "done"
    assert states["morning"] == "active"
    assert states["review"] == "pending"

    eve = datetime(2026, 9, 29, 17, 30)  # 盘后 → 全 done, 当前=复盘
    s2 = cockpit_session(now=eve)
    assert s2["current"] == "复盘"
    assert all(n["state"] == "done" for n in s2["nodes"])

    # data_ready 交叉引用: 今日有计划/复盘 + 分钟新鲜
    health = {"layers": [
        {"key": "plans", "latest": "2026-09-29"},
        {"key": "reviews", "latest": "2026-09-29"},
        {"key": "kline_minute", "freshness": {"status": "fresh"}},
    ]}
    s3 = cockpit_session(health=health, now=noon)
    byk = {n["key"]: n for n in s3["nodes"]}
    assert byk["premarket"]["data_ready"] is True
    assert byk["review"]["data_ready"] is True
    assert byk["afternoon"]["data_ready"] is True


def test_session_non_trading_day(tmp_path):
    """非交易日: 不标进行中, 当前语境=休市。"""
    sat = datetime(2026, 9, 26, 10, 30)  # 周六
    s = cockpit_session(now=sat)
    assert s["trading_day"] is False
    assert s["current"] == "休市"
    assert all(n["state"] == "done" for n in s["nodes"])
