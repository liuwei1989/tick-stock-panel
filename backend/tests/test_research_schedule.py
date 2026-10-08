from datetime import datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.market_time import CN_TZ
from app.services import research_schedule as schedule
from app.services.research import ResearchLedger


def repo_at(path):
    return SimpleNamespace(
        store=SimpleNamespace(data_dir=path),
        resolve_asset_type=lambda _s: "stock",
        get_daily_asset=lambda _a, _s, start, end: pl.DataFrame({"date": [end], "close": [10.0]}),
    )


@pytest.mark.asyncio
async def test_disabled_schedule_does_not_probe_or_generate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        schedule, "is_trading_day", lambda _: pytest.fail("disabled must not probe")
    )
    assert (await schedule.tick(repo_at(tmp_path)))["status"] == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(("verdict", "status"), [(False, "holiday"), (None, "calendar_unknown")])
async def test_schedule_skips_holiday_and_unknown(tmp_path, monkeypatch, verdict, status):
    config = schedule.ScheduleConfig(enabled=True, symbols=["000001.SZ"])
    schedule.save_config(tmp_path, config)
    monkeypatch.setattr(schedule, "is_trading_day", lambda _: verdict)
    result = await schedule.tick(repo_at(tmp_path), now=datetime(2026, 9, 30, 18, 1, tzinfo=CN_TZ))
    assert result["status"] == status
    assert not ResearchLedger(tmp_path).list_batches()


@pytest.mark.asyncio
async def test_schedule_idempotent_after_restart_and_uses_beijing_time(tmp_path, monkeypatch):
    schedule.save_config(tmp_path, schedule.ScheduleConfig(enabled=True, symbols=["000001.SZ"]))
    monkeypatch.setattr(schedule, "is_trading_day", lambda _: True)
    starts = []
    monkeypatch.setattr(schedule, "start_batch", lambda *a: starts.append(a[-1]))
    now = datetime.fromisoformat("2026-09-30T10:01:00+00:00")
    first = await schedule.tick(repo_at(tmp_path), now=now)
    assert first["status"] == "started"
    ledger = ResearchLedger(tmp_path)
    ledger.update_batch(starts[0], status="succeeded")
    second = await schedule.tick(repo_at(tmp_path), now=now)
    assert second["status"] == "already_run"
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_schedule_waits_for_current_daily_data(tmp_path, monkeypatch):
    schedule.save_config(tmp_path, schedule.ScheduleConfig(enabled=True, symbols=["000001.SZ"]))
    monkeypatch.setattr(schedule, "is_trading_day", lambda _: True)
    repo = repo_at(tmp_path)
    repo.get_daily_asset = lambda *_: pl.DataFrame()
    result = await schedule.tick(repo, now=datetime(2026, 9, 30, 18, 1, tzinfo=CN_TZ))
    assert result["status"] == "data_pending"
    assert result["missing_symbols"] == ["000001.SZ"]
    assert not ResearchLedger(tmp_path).list_batches()


def test_schedule_requires_symbols_and_post_settlement_time(tmp_path):
    with pytest.raises(ValueError):
        schedule.ScheduleConfig(enabled=True)
    with pytest.raises(ValueError):
        schedule.ScheduleConfig(hour=14)
    with pytest.raises(ValueError):
        schedule.ScheduleConfig(symbols=["AAPL"])
    with pytest.raises(ValueError):
        schedule.ScheduleConfig(symbols=["000001"])


def test_schedule_api_validation_and_defaults(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.research import router

    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo_at(tmp_path)
    client = TestClient(app)
    assert client.get("/api/research/schedule").json()["config"]["enabled"] is False
    assert client.put("/api/research/schedule", json={"enabled": True}).status_code == 422
    assert (
        client.post(
            "/api/research/batches", json={"symbols": ["000001"], "request_id": "invalid"}
        ).status_code
        == 422
    )
    response = client.put(
        "/api/research/schedule", json={"enabled": True, "symbols": ["000001.SZ"]}
    )
    assert response.status_code == 200
    assert client.get("/api/research/schedule").json()["config"]["symbols"] == ["000001.SZ"]
