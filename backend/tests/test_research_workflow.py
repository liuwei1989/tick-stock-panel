from __future__ import annotations

import asyncio
import json
from datetime import date

import polars as pl
import pytest

from app.services.research import (
    ResearchLedger,
    build_artifact,
    build_context,
    evaluate_outcome,
    parse_decision,
)
from app.services.research_skills import load_skills, run_skills, select_skills


def test_decision_validation_does_not_invent_scores_or_evidence():
    decision, status = parse_decision("plain markdown")
    assert status == "unstructured"
    assert decision.score is None
    decision, status = parse_decision('{"score":101,"direction":"bullish"}')
    assert status == "invalid"
    assert decision.direction == "unknown"
    decision, status = parse_decision(
        '```json\n{"summary":"趋势平稳","score":65,"direction":"neutral","evidence_ids":["kline"]}\n```'
    )
    assert status == "validated" and decision.score == 65


def test_context_quality_and_future_news_are_explicit():
    context = build_context(
        "000001.SZ",
        "stock",
        [{"date": "2026-09-28", "close": 10}],
        {},
        [{"title": "future", "published_at": "2026-10-01"}],
        as_of=date(2026, 9, 30),
    )
    assert context.blocks[0].as_of == "2026-09-28"
    assert context.blocks[0].status == "unknown"  # freshness cannot be inferred from weekdays
    assert context.blocks[1].status == "missing"
    assert context.blocks[2].status == "missing"
    decision, status = parse_decision('{"summary":"结论","score":60,"evidence_ids":["invented"]}')
    artifact = build_artifact("r1", context, decision, status, [], {})
    assert artifact.thesis.score is None
    assert "unverified_evidence" in artifact.data_quality.limitations


def test_ledger_versions_and_idempotent_signals(tmp_path):
    ledger = ResearchLedger(tmp_path)
    ledger.create_run("r1", "000001.SZ", "standard")
    ledger.finish_run("r1", "succeeded", report_id="report1")
    ledger.upsert_signal("r1", {"symbol": "000001.SZ", "status": "watching"})
    ledger.upsert_signal("r1", {"symbol": "000001.SZ", "status": "watching"})
    assert len(ledger.list_signals()) == 1
    assert ResearchLedger(tmp_path).get_run("r1")["report_id"] == "report1"
    first = ledger.transition_signal(
        "r1", "review_required", expected_version=1, reason="new evidence"
    )
    assert first["version"] == 2 and first["history"][0]["reason"] == "new evidence"
    with pytest.raises(ValueError, match="version"):
        ledger.transition_signal("r1", "dismissed", expected_version=1, reason="stale")
    with pytest.raises(ValueError):
        ledger.transition_signal("r1", "nonsense", expected_version=2, reason="invalid")


def test_outcome_uses_observations_after_signal_and_same_price_basis():
    bars = pl.DataFrame(
        {
            "date": [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)],
            "close": [10.0, 12.0, 9.0],
        }
    )
    result = evaluate_outcome(bars, "2026-09-28", date(2026, 9, 28), 2)
    assert result["status"] == "pending"  # creation date bars cannot be a realised outcome
    result = evaluate_outcome(bars, "2026-09-28", date(2026, 9, 30), 2)
    assert result["return_ratio"] == pytest.approx(-0.1)
    assert result["observations"] == 2
    assert result["basis"] == "adjusted_close"
    assert result["status"] == "observed"


def test_weekend_signal_uses_last_known_close_and_later_observations():
    bars = pl.DataFrame({"date": [date(2026, 9, 25), date(2026, 9, 28)], "close": [10.0, 11.0]})
    result = evaluate_outcome(bars, "2026-09-26", date(2026, 9, 28), 1)
    assert result["status"] == "observed"
    assert result["start_date"] == "2026-09-25"
    assert result["signal_date"] == "2026-09-26"
    assert result["return_ratio"] == pytest.approx(0.1)


def test_skill_catalog_is_complete_and_custom_failures_are_isolated(tmp_path):
    folder = tmp_path / "research_skills"
    folder.mkdir()
    (folder / "bad.yaml").write_text("!!python/object/apply:os.system [false]")
    (folder / "custom.yaml").write_text(
        "name: local_test\ndisplay_name: 本地\ncategory: framework\ninstructions: 检查证据\n"
    )
    skills, errors = load_skills(tmp_path)
    assert len(skills) == 16 and len(errors) == 1
    assert len(select_skills(skills, ["auto"], "趋势分析")) >= 1
    with pytest.raises(ValueError):
        select_skills(skills, ["absent"], "")


@pytest.mark.asyncio
async def test_skill_scheduler_bounds_concurrency_and_returns_partial(tmp_path):
    skills, _ = load_skills(tmp_path)
    selected = select_skills(skills, ["bull_trend", "event_driven"], "")
    active = peak = 0

    async def invoke(system, user):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            if "事件驱动" in system:
                await asyncio.sleep(0.1)
            return "依据现有证据"
        finally:
            active -= 1

    results = await run_skills(selected, "evidence", invoke, concurrency=1, timeout=0.01)
    assert peak == 1
    assert [r["status"] for r in results] == ["completed", "degraded"]
    assert results[-1]["failure_code"] == "timeout"


def test_research_api_state_conflict_and_outcome(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.research import router

    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    client = TestClient(app)
    assert len(client.get("/api/research/skills").json()["skills"]) == 15
    assert client.get("/api/research/runs/missing").status_code == 404
    ledger = ResearchLedger(tmp_path)
    ledger.upsert_signal("r1", {"symbol": "000001.SZ", "status": "watching"})
    url = "/api/research/signals/r1/status"
    body = {"status": "review_required", "expected_version": 1, "reason": "核验新证据"}
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 409
    assert client.post(url, json={**body, "status": "executed"}).status_code == 422


def test_portfolio_risk_missing_prices_never_uses_cost_as_a_live_quote():
    from app.services.portfolio_risk import evaluate_portfolio

    report = evaluate_portfolio({"cash": 1000}, {"AAA": {"qty": 100, "avg_cost": 10}}, {}, [])
    assert report["available"] is False
    assert report["exposure_ratio"] is None
    assert report["missing_prices"] == ["AAA"]
    report = evaluate_portfolio(
        {"cash": 1000}, {"AAA": {"qty": 100, "avg_cost": 10}}, {"AAA": 8}, []
    )
    assert report["available"] is True
    assert report["exposure_ratio"] == pytest.approx(800 / 1800)
    assert report["holdings"][0]["pnl_ratio"] == pytest.approx(-0.2)
    assert any(a["kind"] == "concentration" for a in report["alerts"])
    assert any(a["kind"] == "loss" for a in report["alerts"])


def test_archive_is_once_per_run_and_trajectory_survives(tmp_path, monkeypatch):
    from test_stock_analyzer_agents import Repo, _bars

    from app.services import ai_provider, stock_analyzer, stock_reports

    reports = []
    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *_: _bars())
    monkeypatch.setattr(stock_analyzer, "_load_financials", lambda *_: {})

    async def news(_symbol):
        return []

    monkeypatch.setattr(stock_analyzer, "_load_stock_news", news)

    def save(report):
        reports.append(report)
        return report

    monkeypatch.setattr(stock_reports, "save_report", save)

    async def stream(*args, **kwargs):
        yield '正文\n```json\n{"summary":"结论","score":60,"direction":"neutral","evidence_ids":["kline"]}\n```'

    monkeypatch.setattr(ai_provider, "stream_ai_text", stream)

    async def run():
        return [
            json.loads(line)
            async for line in stock_analyzer.analyze_stock_stream(
                Repo(), tmp_path, "000001.SZ", skill_ids=["bull_trend"]
            )
        ]

    events = asyncio.run(run())
    assert len(reports) == 1
    assert events[-1]["report"]["id"] == reports[0]["id"]
    ledger = ResearchLedger(tmp_path)
    assert len(ledger.list_signals()) == 1
    assert ledger.list_runs()[0]["status"] == "succeeded"
    assert any(e["stage"] == "skill:bull_trend" for e in ledger.list_runs()[0]["trajectory"])


def test_outcome_cutoff_does_not_include_intraday_bar():
    from datetime import datetime

    from app.market_time import CN_TZ
    from app.services.research import outcome_cutoff

    assert outcome_cutoff(datetime(2026, 9, 30, 14, 59, tzinfo=CN_TZ)) == date(2026, 9, 29)
    assert outcome_cutoff(datetime(2026, 9, 30, 15, 35, tzinfo=CN_TZ)) == date(2026, 9, 30)


def test_news_future_entries_are_removed_before_prompt():
    from app.services.research import eligible_news

    rows = [
        {"title": "future", "published_date": "2026-10-01"},
        {"title": "known", "published_date": "2026-09-30"},
        {"title": "undated"},
    ]
    assert [r["title"] for r in eligible_news(rows, date(2026, 9, 30))] == ["known", "undated"]


def test_outcome_api_persists_observation_and_rejects_stale_write(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import research

    frame = pl.DataFrame({"date": [date(2026, 9, 28), date(2026, 9, 29)], "close": [10.0, 11.0]})
    app = FastAPI()
    app.include_router(research.router)
    app.state.repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path), get_daily_asset=lambda *_: frame
    )
    monkeypatch.setattr(research, "outcome_cutoff", lambda: date(2026, 9, 29))
    ledger = ResearchLedger(tmp_path)
    ledger.upsert_signal(
        "signal",
        {
            "symbol": "000001.SZ",
            "asset_type": "stock",
            "signal_date": "2026-09-28",
            "status": "watching",
        },
    )
    client = TestClient(app)
    result = client.post(
        "/api/research/signals/signal/outcome", json={"expected_version": 1, "horizon": 1}
    )
    assert result.status_code == 200
    assert result.json()["outcome"]["return_ratio"] == pytest.approx(0.1)
    assert ledger.get_signal("signal")["status"] == "evaluated"
    assert (
        client.post(
            "/api/research/signals/signal/outcome", json={"expected_version": 1, "horizon": 1}
        ).status_code
        == 409
    )


def test_portfolio_risk_api_empty_and_unavailable_prices(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.research import router
    from app.services.portfolio_risk import paper

    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path), latest_daily_date=lambda: None
    )
    client = TestClient(app)
    monkeypatch.setattr(paper, "list_account_ids", lambda *_: [])
    assert client.get("/api/research/portfolio-risk").json() == {"accounts": []}
    monkeypatch.setattr(paper, "list_account_ids", lambda *_: ["one"])
    monkeypatch.setattr(paper, "get_account", lambda *_: {"cash": 1000, "name": "test"})
    monkeypatch.setattr(
        paper, "load_positions", lambda *_: {"000001.SZ": {"qty": 100, "avg_cost": 10}}
    )
    monkeypatch.setattr(paper, "load_nav", lambda *_: [])
    report = client.get("/api/research/portfolio-risk").json()["accounts"][0]
    assert report["available"] is False
    assert report["equity"] is None
    assert report["missing_prices"] == ["000001.SZ"]


@pytest.mark.asyncio
async def test_analysis_capacity_rejects_before_calling_provider(tmp_path, monkeypatch):
    import threading

    from app.services import stock_analyzer

    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(stock_analyzer, "_ANALYSIS_SLOTS", slots)
    events = [
        json.loads(raw)
        async for raw in stock_analyzer.analyze_stock_stream(None, tmp_path, "000001.SZ")
    ]
    assert events[-1]["type"] == "error"
    assert events[-1]["failure_code"] == "capacity"
    assert ResearchLedger(tmp_path).list_runs() == []
    slots.release()
