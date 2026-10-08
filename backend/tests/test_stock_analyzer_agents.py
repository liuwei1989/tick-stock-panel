from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import polars as pl
import pytest

from app.services import ai_provider, stock_analyzer, stock_reports


def _bars() -> pl.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index in range(100):
        close = 10 + index * 0.05
        rows.append({
            "date": start + timedelta(days=index),
            "open": close - 0.02,
            "high": close + 0.08,
            "low": close - 0.08,
            "close": close,
            "volume": 1000 + index,
        })
    return pl.DataFrame(rows)


class Repo:
    def resolve_asset_type(self, _symbol: str) -> str:
        return "stock"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_stages"),
    [
        ("quick", []),
        ("standard", ["technical", "intel", "synthesis"]),
        ("full", ["technical", "intel", "risk", "synthesis"]),
    ],
)
async def test_stock_analysis_agent_modes_emit_bounded_stage_trajectory(
    tmp_path, monkeypatch, mode: str, expected_stages: list[str],
) -> None:
    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *_args: _bars())
    monkeypatch.setattr(stock_analyzer, "_load_financials", lambda *_args: {})
    monkeypatch.setattr(stock_reports, "list_reports", lambda: [])
    monkeypatch.setattr(stock_reports, "save_report", lambda _report: None)
    monkeypatch.setattr(pl, "read_parquet", lambda *_args, **_kwargs: pl.DataFrame())

    calls: list[tuple[str, str]] = []

    async def fake_stream(messages, **_kwargs):
        calls.append((messages[0]["content"], messages[1]["content"]))
        yield f"stage result {len(calls)}"

    monkeypatch.setattr(ai_provider, "stream_ai_text", fake_stream)
    events = [
        json.loads(line)
        async for line in stock_analyzer.analyze_stock_stream(
            Repo(), tmp_path, "000001.SZ", mode=mode,
        )
    ]

    stages = [
        event["stage"] for event in events
        if event["type"] == "agent_stage" and event["status"] == "started"
    ]
    assert stages == expected_stages
    assert len(calls) == (1 if mode == "quick" else len(expected_stages))
    for stage in [event for event in events if event["type"] == "agent_stage"]:
        assert isinstance(stage.get("duration_ms"), int)
        assert stage["duration_ms"] >= 0
    assert events[-1]["type"] == "done"
    assert "stage result" in "".join(event.get("content", "") for event in events)


@pytest.mark.asyncio
async def test_noncritical_agent_failure_degrades_but_synthesis_continues(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *_args: _bars())
    monkeypatch.setattr(stock_analyzer, "_load_financials", lambda *_args: {})
    monkeypatch.setattr(stock_reports, "list_reports", lambda: [])
    monkeypatch.setattr(stock_reports, "save_report", lambda _report: None)
    monkeypatch.setattr(pl, "read_parquet", lambda *_args, **_kwargs: pl.DataFrame())

    async def fake_stream(messages, **_kwargs):
        if "基本面与信息核验 Agent" in messages[0]["content"]:
            raise RuntimeError("intel stage unavailable")
        yield "stage result"

    monkeypatch.setattr(ai_provider, "stream_ai_text", fake_stream)
    events = [
        json.loads(line)
        async for line in stock_analyzer.analyze_stock_stream(
            Repo(), tmp_path, "000001.SZ", mode="standard",
        )
    ]

    intel = next(event for event in events if event.get("stage") == "intel" and event.get("status") == "degraded")
    assert intel["failure_code"] == "provider_error"
    assert "intel stage unavailable" not in intel["message"]
    assert intel["failure_code"] == "provider_error"
    assert isinstance(intel["duration_ms"], int)
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_technical_stage_failure_falls_back_to_raw_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *_args: _bars())
    monkeypatch.setattr(stock_analyzer, "_load_financials", lambda *_args: {})
    monkeypatch.setattr(stock_reports, "list_reports", lambda: [])
    monkeypatch.setattr(stock_reports, "save_report", lambda _report: None)
    monkeypatch.setattr(pl, "read_parquet", lambda *_args, **_kwargs: pl.DataFrame())
    synthesis_messages: list[str] = []

    async def fake_stream(messages, **_kwargs):
        if "技术面分析 Agent" in messages[0]["content"]:
            raise RuntimeError("technical stage unavailable")
        if messages[0]["content"] == stock_analyzer._SYSTEM_PROMPT:
            synthesis_messages.append(messages[1]["content"])
        yield "synthesized from raw data"

    monkeypatch.setattr(ai_provider, "stream_ai_text", fake_stream)
    events = [
        json.loads(line)
        async for line in stock_analyzer.analyze_stock_stream(
            Repo(), tmp_path, "000001.SZ", mode="standard",
        )
    ]

    technical = next(
        event for event in events
        if event.get("stage") == "technical" and event.get("status") == "degraded"
    )
    assert technical["failure_code"] == "provider_error"
    assert "technical stage unavailable" not in technical["message"]
    assert "技术分析阶段未能完成" in synthesis_messages[0]
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_full_mode_runs_portfolio_agent_only_for_held_symbol(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *_args: _bars())
    monkeypatch.setattr(stock_analyzer, "_load_financials", lambda *_args: {})
    monkeypatch.setattr(stock_reports, "list_reports", lambda: [])
    monkeypatch.setattr(stock_reports, "save_report", lambda _report: None)
    monkeypatch.setattr(pl, "read_parquet", lambda *_args, **_kwargs: pl.DataFrame())
    monkeypatch.setattr("app.strategy.paper.list_account_ids", lambda _data_dir: ["test"])
    monkeypatch.setattr(
        "app.strategy.paper.load_positions",
        lambda *_args, **_kwargs: {"000001.SZ": {"qty": 100, "avg_cost": 10.5}},
    )

    calls: list[str] = []

    async def fake_stream(messages, **_kwargs):
        calls.append(messages[0]["content"])
        yield "stage result"

    monkeypatch.setattr(ai_provider, "stream_ai_text", fake_stream)
    events = [
        json.loads(line)
        async for line in stock_analyzer.analyze_stock_stream(
            Repo(), tmp_path, "000001.SZ", mode="full",
        )
    ]

    portfolio = [event for event in events if event.get("stage") == "portfolio"]
    assert [event["status"] for event in portfolio] == ["started", "completed"]
    assert stock_analyzer._PORTFOLIO_AGENT_PROMPT in calls
    assert len(calls) == 5  # technical + intel + portfolio + risk + synthesis
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_agent_stage_timeout_degrades_and_continues(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stock_analyzer, "_load_kline", lambda *_args: _bars())
    monkeypatch.setattr(stock_analyzer, "_load_financials", lambda *_args: {})
    monkeypatch.setattr(stock_reports, "list_reports", lambda: [])
    monkeypatch.setattr(stock_reports, "save_report", lambda _report: None)
    monkeypatch.setattr(pl, "read_parquet", lambda *_args, **_kwargs: pl.DataFrame())
    monkeypatch.setattr(stock_analyzer, "_AGENT_STAGE_TIMEOUT_SECONDS", 0.01)

    async def fake_stream(messages, **_kwargs):
        if messages[0]["content"] == stock_analyzer._TECHNICAL_AGENT_PROMPT:
            await asyncio.sleep(0.05)
        yield "stage result"

    monkeypatch.setattr(ai_provider, "stream_ai_text", fake_stream)
    events = [
        json.loads(line)
        async for line in stock_analyzer.analyze_stock_stream(
            Repo(), tmp_path, "000001.SZ", mode="standard",
        )
    ]

    technical = next(
        event for event in events
        if event.get("stage") == "technical" and event.get("status") == "degraded"
    )
    assert technical["failure_code"] == "timeout"
    assert any(event.get("stage") == "synthesis" and event.get("status") == "completed" for event in events)
    assert events[-1]["type"] == "done"
