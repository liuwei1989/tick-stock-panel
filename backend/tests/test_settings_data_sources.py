from __future__ import annotations

from types import SimpleNamespace

from app.api import settings


def test_list_data_sources_includes_registered_builtin_provider(monkeypatch) -> None:
    from app.data_providers import custom, registry

    monkeypatch.setattr(
        registry,
        "builtin_sources",
        lambda: {
            "zzshare": SimpleNamespace(
                display="Zzshare",
                capabilities=SimpleNamespace(
                    daily=True,
                    adj_factor=True,
                    realtime=False,
                    minute=True,
                    depth5=False,
                    financial=False,
                ),
            ),
        },
    )
    monkeypatch.setattr(custom, "list_plugins", lambda: [])
    monkeypatch.setattr(custom, "list_sources", lambda: [])
    monkeypatch.setattr(custom, "errors", lambda: [])
    monkeypatch.setattr(custom, "data_sources_dir", lambda: "/tmp/data_sources")

    result = settings.list_data_sources()

    assert result["builtin"] == [
        {
            "name": "tickflow",
            "display_name": "TickFlow",
            "datasets": ["daily", "adj_factor", "realtime", "minute"],
        },
        {
            "name": "zzshare",
            "display_name": "Zzshare",
            "datasets": ["daily", "adj_factor", "minute"],
        },
    ]
