"""ZzshareProvider 数据源插件测试 — 标准化/过滤/注册集成 (mock 网络层)。"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.data_providers.zzshare_provider import ZzshareProvider


def _fake_daily_df(symbols: list[str], days: list[str]) -> pl.DataFrame:
    rows: list[dict] = []
    for sym in symbols:
        for i, d in enumerate(days):
            rows.append({
                "symbol": sym,
                "date": d,
                "open": 10.0 + i, "high": 11.0 + i, "low": 9.5 + i,
                "close": 10.5 + i, "pre_close": 10.0 + i,
                "change": 0.5, "pct_chg": 5.0,
                "volume": 1000.0 + i, "amount": 10000.0 + i,
            })
    return pl.DataFrame(rows)


def _stub_days(monkeypatch, days: list[str]):
    """固定交易日区间, 避免真实网络调用。"""
    monkeypatch.setattr(ZzshareProvider, "_trade_days_in_range", lambda self, s, e: days)


# ── instruments 标准化 ────────────────────────────────────

def test_get_instruments_normalized(monkeypatch):
    import pandas as pd

    raw = pd.DataFrame([
        {"ts_code": "600000.SH", "name": "浦发银行", "exchange": "SSE"},
        {"ts_code": "000001.SZ", "name": "平安银行", "exchange": "SZSE"},
        {"ts_code": "830799.BJ", "name": "艾融软件", "exchange": "BSE"},
    ])
    monkeypatch.setattr("app.services.zzshare_sync._with_retry", lambda fn, *a, **k: raw)
    df = ZzshareProvider().get_instruments("stock")
    # 增强 schema: 原 6 列 + listing_date/float_shares/limit_up/limit_down/as_of
    assert df.shape == (3, 11)
    assert df.columns == ["symbol", "name", "code", "exchange", "asset_type",
                          "source", "listing_date", "float_shares", "limit_up",
                          "limit_down", "as_of"]
    assert df["listing_date"].to_list() == [None, None, None]
    # sort by symbol: 000001(SZ) < 600000(SH) < 830799(BJ)
    assert df["exchange"].to_list() == ["SZ", "SH", "BJ"]
    assert df["source"].to_list() == ["zzshare"] * 3


def test_get_instruments_empty_for_etf():
    assert ZzshareProvider().get_instruments("etf").is_empty()


# ── daily 规范化与过滤 ─────────────────────────────────────

def test_get_daily_filter_and_normalize(monkeypatch):
    _stub_days(monkeypatch, ["20260921", "20260922"])
    df = _fake_daily_df(
        ["000001", "600000"],
        ["2026-09-21", "2026-09-22"],
    )
    monkeypatch.setattr(
        "app.services.zzshare_sync.fetch_daily",
        lambda d: df.filter(pl.col("date") == d[:4] + "-" + d[4:6] + "-" + d[6:]),
    )
    out = ZzshareProvider().get_daily(
        ["000001", "600000"],
        start_time=datetime(2026, 9, 20), end_time=datetime(2026, 9, 23),
        asset_type="stock",
    )
    assert out.shape == (4, 9)
    assert "quote_ts" in out.columns
    assert set(out["symbol"].unique().to_list()) == {"000001", "600000"}
    assert out["date"].min() == "2026-09-21"


def test_get_daily_symbol_filter(monkeypatch):
    _stub_days(monkeypatch, ["20260921"])
    df = _fake_daily_df(["000001", "000002"], ["2026-09-21"])
    monkeypatch.setattr("app.services.zzshare_sync.fetch_daily", lambda d: df)
    out = ZzshareProvider().get_daily(
        ["000001"], start_time=datetime(2026, 9, 20), end_time=datetime(2026, 9, 22)
    )
    assert out.shape == (1, 9)
    assert out["symbol"].to_list() == ["000001"]


# ── adj_factor 反推 ───────────────────────────────────────

def test_get_adj_factors_detect_dividend(monkeypatch):
    _stub_days(monkeypatch, ["20260921", "20260922"])
    # 9-22 除权: pre_close=9.8, 前收盘 10.0 → ex_factor=1.0204
    df = pl.DataFrame({
        "symbol": ["000001", "000001", "000002", "000002"],
        "date": ["2026-09-21", "2026-09-22", "2026-09-21", "2026-09-22"],
        "close": [10.0, 9.9, 20.0, 20.5],
        "pre_close": [10.0, 9.8, 20.0, 20.0],
        "volume": [1.0] * 4, "amount": [10.0] * 4,
    })
    monkeypatch.setattr("app.services.zzshare_sync.fetch_daily", lambda d: df)
    out = ZzshareProvider().get_adj_factors(
        ["000001", "000002"],
        start_time=datetime(2026, 9, 20), end_time=datetime(2026, 9, 24),
    )
    assert out.shape == (1, 3)
    row = out.to_dicts()[0]
    assert row["symbol"] == "000001"
    assert row["ex_factor"] == pytest.approx(10.0 / 9.8, rel=1e-6)


# ── 交易日区间 ────────────────────────────────────────────

def test_trade_days_range_filter(monkeypatch):
    monkeypatch.setattr(
        "app.services.zzshare_sync._with_retry",
        lambda fn, *a, **k: ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-28"],
    )
    p = ZzshareProvider()
    days = p._trade_days_in_range(datetime(2026, 9, 22), datetime(2026, 9, 24))
    assert days == ["20260922", "20260923", "20260924"]


# ── minute ────────────────────────────────────────────────

def test_get_minute_normalized(monkeypatch):
    import pandas as pd

    raw = pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_time": "202609281030",
         "open": 11.2, "high": 11.4, "low": 11.1, "close": 11.3, "vol": 12345, "amount": 1.4e6},
        {"ts_code": "000001.SZ", "trade_time": "202609281031",
         "open": 11.3, "high": 11.5, "low": 11.2, "close": 11.4, "vol": 6789, "amount": 7.7e5},
        {"ts_code": "600000.SH", "trade_time": "202609281030",
         "open": 7.1, "high": 7.2, "low": 7.0, "close": 7.15, "vol": 9999, "amount": 7.1e5},
    ])
    def fake_retry(action, label, attempts=3):
        return action()
    monkeypatch.setattr("app.services.zzshare_sync._with_retry", fake_retry)
    monkeypatch.setattr(
        "app.services.zzshare_sync._client",
        lambda: SimpleNamespace(
            stk_mins=lambda ts_code, **k: raw[raw["ts_code"].str.startswith(str(ts_code).split(".")[0])],
        ),
    )
    out = ZzshareProvider().get_minute(
        ["000001", "600000"],
        start_time=datetime(2026, 9, 28, 9, 30), end_time=datetime(2026, 9, 28, 15, 0),
    )
    assert out.shape == (3, 8)
    assert list(out.columns) == ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
    assert set(out["symbol"].unique().to_list()) == {"000001", "600000"}
    # trade_time(YYYYMMDDHHMM) → 北京墙钟 naive
    assert out["datetime"].dtype == pl.Datetime("us")
    assert out["datetime"].min() == datetime(2026, 9, 28, 10, 30)


def test_get_minute_empty_when_no_data(monkeypatch):
    import pandas as pd

    def fake_retry(action, label, attempts=3):
        return action()
    monkeypatch.setattr("app.services.zzshare_sync._with_retry", fake_retry)
    monkeypatch.setattr(
        "app.services.zzshare_sync._client",
        lambda: SimpleNamespace(
            stk_mins=lambda ts_code, **k: pd.DataFrame(columns=[
                "ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"]),
        ),
    )
    out = ZzshareProvider().get_minute(
        ["000001"], start_time=datetime(2026, 9, 28, 9, 30), end_time=datetime(2026, 9, 28, 15, 0), freq="5m",
    )
    assert out.is_empty()


# ── 注册集成 ──────────────────────────────────────────────

def test_registry_and_preferences():
    from app.data_providers.registry import _PROVIDERS, get_provider
    assert "zzshare" in _PROVIDERS
    p = get_provider("zzshare")
    assert p.name == "zzshare"
    assert p.capabilities.daily and p.capabilities.adj_factor and p.capabilities.instruments

    from app.services.preferences import _ALLOWED_DATA_PROVIDERS
    assert "zzshare" in _ALLOWED_DATA_PROVIDERS


def test_capabilities_matrix_candidate():
    from app.data_providers.capabilities import build_capability_matrix
    m = build_capability_matrix({"daily_data_provider": "zzshare"}, "none")
    daily = next(c for c in m["capabilities"] if c["id"] == "daily")
    assert "zzshare" in [c["name"] for c in daily["candidates"]]
    assert daily["usable"] is True
