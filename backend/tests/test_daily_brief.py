import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.services import daily_brief


def test_rules_brief_explains_sentiment_and_portfolio_review(monkeypatch, tmp_path):
    facts = {
        "market": {
            "regime": {"available": True, "score": 72, "phase_label": "主升"},
            "mainline": {"items": [{"member": "人工智能", "leader_symbol": "000001.SZ"}]},
            "alerts": [],
        },
        "portfolio": {"accounts": [{"alerts": [{"symbol": "600519.SH", "message": "浮亏"}]}]},
        "research": [],
    }
    monkeypatch.setattr(daily_brief, "_facts", lambda *args: facts)
    brief = daily_brief._rules(facts, date(2026, 10, 8))
    assert brief.sentiment == "偏强"
    assert brief.buy[0].symbol == "000001.SZ"
    assert brief.sell[0].symbol == "600519.SH"


@pytest.mark.asyncio
async def test_generate_uses_agent_json_and_persists(monkeypatch, tmp_path):
    facts = {"market": {}, "portfolio": {"accounts": []}, "research": []}
    monkeypatch.setattr(daily_brief, "_facts", lambda *args: facts)
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: True)

    async def fake_generate(*args, **kwargs):
        return json.dumps({
            "sentiment": "中性", "sentiment_score": 50,
            "sentiment_reason": "证据有限", "buy": [], "sell": [],
        }, ensure_ascii=False)

    monkeypatch.setattr("app.services.ai_provider.generate_ai_text", fake_generate)
    repo = SimpleNamespace()
    result = await daily_brief.generate(repo, tmp_path, as_of=date(2026, 10, 8), force=True)
    assert result["source"] == "agent"
    assert daily_brief.load(tmp_path)["sentiment"] == "中性"


def test_format_push_has_buy_sell_and_disclaimer():
    text = daily_brief.format_push({
        "sentiment": "偏弱", "sentiment_reason": "环境分下降", "buy": [],
        "sell": [{"symbol": "600519.SH", "action": "review", "reason": "浮亏"}],
    })
    assert "市场情绪" in text and "600519.SH" in text and "不会自动下单" in text


def test_push_uses_selected_channels_and_skips_when_none(monkeypatch, tmp_path):
    from app.services import preferences

    monkeypatch.setattr(preferences, "get_review_push_channels", lambda: [])
    assert daily_brief.push({"as_of": "2026-10-08"}) == 0
    monkeypatch.setattr(preferences, "get_review_push_channels", lambda: ["telegram"])
    sent = []
    from app.services import notification_channels

    monkeypatch.setattr(
        notification_channels, "dispatch", lambda *a, **kw: sent.append((a, kw)) or True
    )
    assert daily_brief.push({"as_of": "2026-10-08", "sentiment": "偏强"}) == 1
    assert sent and sent[0][1]["event_key"] == "daily_action_brief:2026-10-08"


@pytest.mark.asyncio
async def test_scheduled_tick_generates_once_and_marks_successful_push(monkeypatch, tmp_path):
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    calls = []
    brief = {"as_of": "2026-10-08", "sentiment": "中性", "buy": [], "sell": []}
    async def fake_generate(*args, **kwargs):
        return brief

    monkeypatch.setattr(daily_brief, "generate", fake_generate)
    monkeypatch.setattr(daily_brief, "push", lambda value: calls.append(value) or 1)
    monkeypatch.setattr("app.services.trading_day.is_trading_day", lambda now: True)
    now = datetime(2026, 10, 8, 15, 40, tzinfo=daily_brief.cn_now().tzinfo)

    first = await daily_brief.scheduled_tick(repo, now=now)
    second = await daily_brief.scheduled_tick(repo, now=now)

    assert first["status"] == "sent"
    assert second["status"] == "already_sent"
    assert len(calls) == 1
