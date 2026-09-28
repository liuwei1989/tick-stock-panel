"""工作流闭环测试 — 盘前计划生成 + 程序化复盘 + 闭环总览。"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from app.services import workflow as wf
from app.strategy.engine import StrategyResult


def _fake_engine(run_map: dict):
    class FakeEngine:
        def list_strategies(self):
            return [
                {"id": "strategy_a", "name": "策略A", "research_only": False,
                 "asset_types": ["stock"], "timeframes": ["1d"]},
                {"id": "strategy_b", "name": "策略B", "research_only": False,
                 "asset_types": ["stock"], "timeframes": ["1d"]},
            ]

        def run(self, strategy_id, context, pool=None, params=None, overrides=None):
            if strategy_id in run_map:
                return run_map[strategy_id]
            return StrategyResult(as_of=context.as_of, strategy_id=strategy_id)
    return FakeEngine()


def _row(symbol: str, score: float, close: float, signal: str = "s") -> dict:
    return {"symbol": symbol, "score": score, "close": close, "signal": signal}


def _make_context(engine, as_of, sids, **kwargs):
    class Ctx:
        pass
    Ctx.as_of = as_of
    return Ctx()


def _patch_screener(monkeypatch, as_of: str):
    import types

    class FakeSvc:
        def __init__(self, repo=None, asset_type="stock"):
            pass

        def latest_date(self):
            return as_of

        def build_strategy_context(self, engine, as_of, sids, timeframe="1d", **kw):
            return _make_context(engine, as_of, sids)

    monkeypatch.setattr("app.services.screener.ScreenerService", FakeSvc)
    return FakeSvc


def _regime(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def test_generate_plan_evolved_scan_and_watchlist(tmp_path, monkeypatch):
    data_dir = _regime(tmp_path)
    _patch_screener(monkeypatch, "2026-09-24")
    # 自选补充池
    wl = data_dir / "user_data"
    wl.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600001", "600002"]}).write_parquet(wl / "watchlist.parquet")
    # 已应用进化参数 → 盘前用进化策略扫描
    applied = data_dir / "evolution_applied.json"
    applied.write_text(json.dumps({"strategy_a": {"params": {"lookback": 5}, "name": "进化A"}}),
                       encoding="utf-8")
    engine = _fake_engine({
        "strategy_a": StrategyResult(
            as_of=__import__("datetime").date(2026, 9, 24), strategy_id="strategy_a",
            rows=[_row("000001.SZ", 92.0, 11.2), _row("000002.SZ", 88.0, 22.5)],
            total=2),
    })
    plan = wf.generate_plan(data_dir, engine=engine, max_entries=20, max_per_strategy=8)
    assert plan["ok"] is True
    assert plan["trade_date"] == "2026-09-24"
    assert plan["status"] == "planned"
    assert plan["regime"] == {}
    symbols = {e["symbol"] for e in plan["entries"]}
    assert "000001" in symbols and "000002" in symbols
    # 全部条目来自进化扫描 (自选标的未在扫描结果中, watchlist 池也要有信号才进)
    assert all(e["source"] == "evolved" for e in plan["entries"])
    # 计划文件已落盘
    assert (data_dir / "plans" / f"{plan['plan_id']}.json").exists()


def test_generate_plan_watchlist_merge(monkeypatch, tmp_path):
    data_dir = _regime(tmp_path)
    _patch_screener(monkeypatch, "2026-09-24")
    wl = data_dir / "user_data"
    wl.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600001", "600002"]}).write_parquet(wl / "watchlist.parquet")
    applied = data_dir / "evolution_applied.json"
    applied.write_text(json.dumps({"strategy_a": {"params": {}}}), encoding="utf-8")

    class Watcher:
        def __init__(self):
            self.pool_calls = []

        def run(self, strategy_id, context, pool=None, params=None, overrides=None):
            if pool == ["600001", "600002"]:
                self.pool_calls.append(1)
                return StrategyResult(
                    as_of=__import__("datetime").date(2026, 9, 24), strategy_id=strategy_id,
                    rows=[_row("600001", 75.0, 9.8), _row("600002", 60.0, 5.2)],
                    total=2)
            return StrategyResult(as_of=context.as_of, strategy_id=strategy_id)

    engine = Watcher()
    plan = wf.generate_plan(data_dir, engine=engine, max_entries=20, max_per_strategy=8)
    assert plan["ok"] is True
    assert engine.pool_calls, "自选补充池应被扫描"
    symbols = {e["symbol"] for e in plan["entries"]}
    assert "600001" in symbols and "600002" in symbols
    assert all(e["source"] == "watchlist" for e in plan["entries"])
    assert all(e["entry_low"] and e["entry_high"] and e["reference_price"] for e in plan["entries"])


def test_generate_plan_no_evolution_no_signal(tmp_path, monkeypatch):
    data_dir = _regime(tmp_path)
    _patch_screener(monkeypatch, "2026-09-24")
    engine = _fake_engine({})
    plan = wf.generate_plan(data_dir, engine=engine, use_evolution=False, max_entries=20)
    # 无推荐且默认策略无信号 → 明确报错而非空计划
    assert plan["ok"] is False
    assert "没有选出" in plan["error"]


def _seed_enriched_day(data_dir: Path, trade_date: str, rows: list[dict]) -> None:
    day = data_dir / "kline_daily_enriched" / f"date={trade_date}"
    day.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(day / "part.parquet")


def _make_plan_file(data_dir: Path, entries: list[dict], trade_date: str = "2026-09-24") -> dict:
    plans = data_dir / "plans"
    plans.mkdir(parents=True)
    plan = {
        "plan_id": "P20260924-001", "trade_date": trade_date, "regime": {},
        "source": {"strategies": [{"strategy_id": "strategy_a", "source": "applied"}]},
        "entries": entries, "status": "planned",
        "created_at": "2026-09-24T08:00:00",
    }
    (plans / "P20260924-001.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return plan


def test_review_plan_hit_and_miss(tmp_path):
    data_dir = _regime(tmp_path)
    _make_plan_file(data_dir, [
        {"symbol": "000001", "strategy_id": "strategy_a", "strategy_name": "策略A",
         "source": "evolved", "score": 92.0, "reference_price": 11.0,
         "entry_low": 10.945, "entry_high": 11.055},
        {"symbol": "600000", "strategy_id": "strategy_a", "strategy_name": "策略A",
         "source": "evolved", "score": 88.0, "reference_price": 9.0,
         "entry_low": 8.955, "entry_high": 9.045},
    ])
    # 000001 触发 (low 10.9 ≤ 11.055), 600000 未触发 (high 8.9 < 8.955)
    _seed_enriched_day(data_dir, "2026-09-25", [
        {"symbol": "000001", "open": 11.0, "high": 11.8, "low": 10.9, "close": 11.6},
        {"symbol": "600000", "open": 8.9, "high": 8.9, "low": 8.7, "close": 8.8},
    ])
    review = wf.review_plan(data_dir, "P20260924-001", trade_date="2026-09-25")
    assert review["ok"] is True
    assert review["trade_date"] == "2026-09-25"
    by_sym = {r["symbol"]: r for r in review["results"]}
    assert by_sym["000001"]["hit"] is True
    assert by_sym["000001"]["fill_price"] == 11.0
    assert abs(by_sym["000001"]["pnl_pct"] - (11.6 / 11.0 - 1)) < 1e-9
    assert by_sym["600000"]["hit"] is False
    assert by_sym["600000"]["pnl_pct"] is None
    summary = review["summary"]
    assert summary["planned"] == 2 and summary["triggered"] == 1
    assert summary["win_rate"] == 1.0
    # 策略反馈已写
    fb = wf.load_feedback(data_dir)
    assert len(fb) == 1 and fb[0]["strategy_id"] == "strategy_a"
    assert fb[0]["trade_date"] == "2026-09-25"
    # 计划状态已更新
    plan = wf.get_plan(data_dir, "P20260924-001")
    assert plan["status"] == "reviewed" and plan["review_id"] == review["review_id"]


def test_review_plan_already_reviewed(tmp_path):
    data_dir = _regime(tmp_path)
    _make_plan_file(data_dir, [
        {"symbol": "000001", "strategy_id": "strategy_a", "reference_price": 11.0,
         "entry_low": 10.945, "entry_high": 11.055},
    ])
    _seed_enriched_day(data_dir, "2026-09-25", [
        {"symbol": "000001", "open": 11.0, "high": 11.2, "low": 10.95, "close": 11.1},
    ])
    first = wf.review_plan(data_dir, "P20260924-001", trade_date="2026-09-25")
    second = wf.review_plan(data_dir, "P20260924-001", trade_date="2026-09-25")
    assert first["ok"] is True and second["ok"] is True
    assert second.get("already_reviewed") is True
    assert second["review_id"] == first["review_id"]
    # 反馈只写一次
    assert len(wf.load_feedback(data_dir)) == 1


def test_review_plan_no_data(tmp_path):
    data_dir = _regime(tmp_path)
    _make_plan_file(data_dir, [{"symbol": "000001", "strategy_id": "strategy_a",
                                "reference_price": 11.0}])
    review = wf.review_plan(data_dir, "P20260924-001", trade_date="2026-09-25")
    assert review["ok"] is False
    assert "无 enriched" in review["error"]


def test_workflow_overview_and_lists(tmp_path):
    data_dir = _regime(tmp_path)
    overview = wf.workflow_overview(data_dir)
    assert overview["latest_plan"] is None and overview["latest_review"] is None
    assert overview["feedback_count"] == 0
    assert wf.list_plans(data_dir) == []
    assert wf.list_reviews(data_dir) == []
    assert wf.get_plan(data_dir, "missing") is None
    assert wf.get_review(data_dir, "missing") is None
