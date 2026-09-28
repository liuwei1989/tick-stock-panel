"""zzshare token 后台配置测试: 本地配置热生效 + 管理 API。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import zzshare_sync


def _patch_data_dir(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "config" / "zzshare.json"

    def fake_default():
        return tmp_path

    monkeypatch.setattr(zzshare_sync, "_default_data_dir", fake_default)


def test_set_tokens_writes_config_and_hot_reloads(monkeypatch, tmp_path) -> None:
    _patch_data_dir(monkeypatch, tmp_path)
    n = zzshare_sync.set_tokens(["tok-a", "tok-b"])
    assert n == 2
    assert zzshare_sync.get_tokens() == ["tok-a", "tok-b"]
    # 热生效: 直接改文件, 下次读取即新 token
    cfg = tmp_path / "config" / "zzshare.json"
    cfg.write_text('{"tokens": ["tok-new"]}', encoding="utf-8")
    assert zzshare_sync.get_tokens() == ["tok-new"]


def test_set_tokens_rejects_empty(monkeypatch, tmp_path) -> None:
    _patch_data_dir(monkeypatch, tmp_path)
    import pytest
    with pytest.raises(ValueError):
        zzshare_sync.set_tokens([])
    with pytest.raises(ValueError):
        zzshare_sync.set_tokens(["", "  "])


def test_admin_api_get_put(monkeypatch, tmp_path) -> None:
    _patch_data_dir(monkeypatch, tmp_path)
    # 未设密码 + 本机判定放行, 绕过认证中间件
    from app.api import auth as auth_api
    monkeypatch.setattr(auth_api, "_is_local_network", lambda host: True)
    zzshare_sync.set_tokens(["54d9f655de27b8ccc4dabe352acccaf7e6f6c42f384d91ba8887150c24059f4c"])
    client = TestClient(app)

    resp = client.get("/api/admin/zzshare-tokens")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["tokens"][0] == "54d9f6...9f4c"

    resp = client.put("/api/admin/zzshare-tokens", json={"tokens": ["tok-1", "tok-2"]})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2
    assert zzshare_sync.get_tokens() == ["tok-1", "tok-2"]

    resp = client.put("/api/admin/zzshare-tokens", json={"tokens": []})
    assert resp.status_code == 422
