import httpx
import pytest

from app.services.notification_channels import build_request, deliver


@pytest.mark.parametrize(
    "channel, config",
    [
        (
            "dingtalk",
            {"url": "https://oapi.dingtalk.com/robot/send?access_token=test", "secret": "sign"},
        ),
        ("telegram", {"token": "test", "chat_id": "123"}),
        ("discord", {"url": "https://discord.com/api/webhooks/test"}),
        ("slack", {"url": "https://hooks.slack.com/services/test"}),
        ("gotify", {"url": "https://gotify.test", "token": "test"}),
        ("ntfy", {"url": "https://ntfy.test/topic"}),
        ("pushover", {"token": "test", "user": "test"}),
        ("pushplus", {"token": "test"}),
        ("serverchan3", {"sendkey": "sctp123tABC"}),
        (
            "astrbot",
            {
                "url": "https://astrbot.test/push",
                "token": "test",
                "platform": "aiocqhttp",
                "target_type": "GroupMessage",
                "target_id": "123",
            },
        ),
    ],
)
def test_channel_wire_contract_and_bounded_body(channel, config):
    url, kwargs = build_request(channel, config, "研报", "中" * 20000, timestamp=1234567890000)
    assert url.startswith("https://")
    assert kwargs.get("json") or kwargs.get("data") or kwargs.get("content")
    assert len(str(kwargs)) < 30000
    if channel == "discord":
        assert kwargs["json"]["allowed_mentions"] == {"parse": []}
    if channel == "dingtalk":
        assert "timestamp=1234567890000" in url and "sign=" in url


def test_delivery_retries_transient_and_records_no_secret():
    calls = []

    def send(url, **kwargs):
        calls.append(url)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": True})

    result = deliver(
        "telegram",
        {"token": "secret", "chat_id": "123"},
        "title",
        "body",
        send=send,
        sleep=lambda _: None,
    )
    assert result["status"] == "sent" and result["attempts"] == 2
    assert "secret" not in str(result)


def test_business_error_is_not_a_success():
    result = deliver(
        "dingtalk",
        {"url": "https://example.test"},
        "title",
        "body",
        send=lambda *a, **k: httpx.Response(200, json={"errcode": 310000, "errmsg": "secret"}),
        sleep=lambda _: None,
    )
    assert result["status"] == "failed" and "secret" not in str(result)


def test_invalid_channel_and_url_fail_closed():
    with pytest.raises(ValueError):
        build_request("unknown", {}, "", "")
    with pytest.raises(ValueError):
        build_request("discord", {"url": "file:///secret"}, "", "")


def test_channel_settings_api_never_returns_credentials(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import secrets_store
    from app.api.notification_channels import router

    monkeypatch.setattr(secrets_store, "_path", lambda: tmp_path / "secrets.json")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.put(
        "/api/notification-channels/telegram",
        json={"values": {"token": "PRIVATE_TOKEN", "chat_id": "PRIVATE_CHAT"}},
    )
    assert response.status_code == 200
    assert "PRIVATE" not in response.text
    response = client.get("/api/notification-channels")
    assert "PRIVATE" not in response.text
    telegram = next(row for row in response.json()["channels"] if row["id"] == "telegram")
    assert telegram["configured"] is True
    assert set(telegram["set_fields"]) == {"token", "chat_id"}


def test_concurrent_secret_saves_preserve_other_channel_credentials(tmp_path, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app import secrets_store

    monkeypatch.setattr(secrets_store, "_path", lambda: tmp_path / "secrets.json")
    original_load = secrets_store.load

    def slow_load():
        value = original_load()
        time.sleep(0.005)
        return value

    monkeypatch.setattr(secrets_store, "load", slow_load)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda i: secrets_store.save({f"channel_{i}": str(i)}), range(16)))
    assert len(original_load()) == 16


def test_monitor_and_review_route_only_selected_extra_channels(monkeypatch):
    from types import SimpleNamespace

    from app import secrets_store
    from app.jobs import daily_pipeline
    from app.services import notification_channels, preferences
    from app.services.quote_service import QuoteService

    for name in (
        "get_feishu_webhook_url",
        "get_feishu_webhook_secret",
        "get_wecom_webhook_url",
        "get_custom_webhook_url",
    ):
        monkeypatch.setattr(preferences, name, lambda: "")
    monkeypatch.setattr(preferences, "get_email_smtp_config", lambda: {})
    monkeypatch.setattr(secrets_store, "get_custom_webhook_secret", lambda: "")
    monkeypatch.setattr(secrets_store, "get_email_smtp_password", lambda: "")
    monkeypatch.setattr(
        notification_channels, "configs", lambda: {"telegram": {"token": "test", "chat_id": "test"}}
    )
    sent = []
    monkeypatch.setattr(
        notification_channels,
        "dispatch",
        lambda executor, ch, title, body, **kw: sent.append(ch) or True,
    )
    engine = SimpleNamespace(
        rules={"selected": {"webhook_channels": ["telegram"]}, "muted": {"webhook_channels": []}}
    )
    QuoteService._maybe_send_webhook(
        object.__new__(QuoteService),
        [
            {"rule_id": key, "source": "price", "symbol": "000001.SZ", "message": "test"}
            for key in engine.rules
        ],
        engine,
    )
    assert sent == ["telegram"]
    monkeypatch.setattr(preferences, "get_review_push_channels", lambda: ["slack"])
    daily_pipeline._maybe_push_review("review", {"as_of": "2026-09-30"})
    assert sent == ["telegram", "slack"]
