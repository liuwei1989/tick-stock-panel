"""zzshare 数据源同步测试 — 除权因子反推 / adj_factor 持久化 merge。

被测纯函数 (不依赖网络):
- build_ex_factors: 从 pre_close 反推除权因子表 (symbol/trade_date/ex_factor)
- save_adj_factor: 写 adj_factor/all.parquet, 幂等 merge
- list_trade_days: 兼容 str/dict 行格式
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.services import zzshare_sync


def _daily(dates: list[date], closes: list[float], pre_closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["000001"] * len(dates),
        "date": dates,
        "open": closes, "high": closes, "low": closes,
        "close": closes,
        "pre_close": pre_closes,
        "volume": [100.0] * len(dates),
        "amount": [1000.0] * len(dates),
    })


# ── build_ex_factors ─────────────────────────────────────────

def test_no_dividend_no_factors():
    df = _daily(
        [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)],
        [10.0, 10.2, 10.4],
        [10.0, 10.0, 10.2],
    )
    factors = zzshare_sync.build_ex_factors(df)
    assert factors.is_empty()


def test_dividend_day_detected():
    # 9-23 除权: pre_close=9.8, 前收盘 10.0 → ex_factor=10/9.8≈1.0204
    df = _daily(
        [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)],
        [10.0, 9.9, 9.95],
        [10.0, 9.8, 9.9],
    )
    factors = zzshare_sync.build_ex_factors(df)
    assert len(factors) == 1
    row = factors.to_dicts()[0]
    assert row["symbol"] == "000001"
    assert row["trade_date"] == date(2026, 9, 23)
    assert row["ex_factor"] == pytest.approx(10.0 / 9.8, rel=1e-6)


def test_pre_close_null_ignored():
    df = _daily(
        [date(2026, 9, 22), date(2026, 9, 23)],
        [10.0, 9.9],
        [None, 9.8],
    )
    factors = zzshare_sync.build_ex_factors(df)
    assert factors.is_empty()  # 首行无前值, pre_close 为 None 跳过


def test_noise_within_tolerance_ignored():
    # 微小噪声 (浮点误差级) 不算除权事件
    df = _daily(
        [date(2026, 9, 22), date(2026, 9, 23)],
        [10.0, 10.01],
        [10.0, 10.000001],
    )
    factors = zzshare_sync.build_ex_factors(df)
    assert factors.is_empty()


# ── save_adj_factor ─────────────────────────────────────────

def test_save_adj_factor_merge_idempotent(tmp_path):
    factors = pl.DataFrame({
        "symbol": ["000001", "000002"],
        "trade_date": [date(2026, 9, 23), date(2026, 9, 23)],
        "ex_factor": [1.02, 1.05],
    })
    n1 = zzshare_sync.save_adj_factor(tmp_path, factors)
    assert n1 == 2
    out = tmp_path / "adj_factor" / "all.parquet"
    assert out.exists()
    assert len(pl.read_parquet(out)) == 2

    # 重复写 (同 key) → merge 去重, 行数不变
    n2 = zzshare_sync.save_adj_factor(tmp_path, factors)
    assert n2 == 2
    assert len(pl.read_parquet(out)) == 2

    # 新事件 → 追加
    more = pl.DataFrame({
        "symbol": ["000003"],
        "trade_date": [date(2026, 9, 24)],
        "ex_factor": [1.10],
    })
    zzshare_sync.save_adj_factor(tmp_path, more)
    df = pl.read_parquet(out)
    assert len(df) == 3
    assert df.filter(pl.col("symbol") == "000003")["ex_factor"][0] == pytest.approx(1.10)


def test_save_adj_factor_empty():
    assert zzshare_sync.save_adj_factor(tmp_path := __import__("tempfile").mkdtemp(), pl.DataFrame()) == 0


# ── list_trade_days 行格式兼容 ──────────────────────────────

def test_list_trade_days_str_and_dict(monkeypatch):
    from types import SimpleNamespace

    def fake_str(days):
        return ["2026-09-22", "2026-09-23"]
    monkeypatch.setattr(zzshare_sync, "_client", lambda: SimpleNamespace(trade_days=fake_str))
    days = zzshare_sync.list_trade_days(2)
    assert days == ["20260922", "20260923"]

    def fake_dict(days):
        return [{"trade_date": "20260924"}, {"date": "2026-09-25"}]
    monkeypatch.setattr(zzshare_sync, "_client", lambda: SimpleNamespace(trade_days=fake_dict))
    days = zzshare_sync.list_trade_days(2)
    assert days == ["20260924", "20260925"]
