from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl

from app.api import kline


def _frame(days: int) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["301716.SZ"] * days,
        "date": [date(2026, 9, 30).replace(day=30 - i) for i in range(days)],
        "open": [10.0] * days,
        "high": [11.0] * days,
        "low": [9.0] * days,
        "close": [10.5] * days,
        "volume": [100.0] * days,
    })


def test_daily_refills_sparse_long_stock_range(monkeypatch) -> None:
    class Repo:
        current = _frame(1)

        def resolve_asset_type(self, symbol: str) -> str:
            return "stock"

        def get_instruments(self) -> pl.DataFrame:
            return pl.DataFrame()

        def get_daily_asset(self, asset_type, symbol, start, end, columns=None):
            return self.current

    repo = Repo()
    capset = object()
    calls = []

    def refill(symbols, got_repo, got_capset, **kwargs):
        calls.append((symbols, got_repo, got_capset, kwargs))
        repo.current = _frame(6)
        return 5

    monkeypatch.setattr(kline.kline_sync, "sync_and_persist_daily_batch", refill)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=repo, capabilities=capset, quote_service=None,
    )))

    response = kline.get_daily(
        request,
        symbol="301716.SZ",
        days=120,
        start_date="2026-03-30",
        end_date="2026-09-30",
        ext_columns=None,
    )

    assert len(calls) == 1
    assert calls[0][0:3] == (["301716.SZ"], repo, capset)
    assert calls[0][3]["start_date"].date() == date(2026, 3, 30)
    assert calls[0][3]["end_date"].date() == date(2026, 9, 30)
    assert len(response["rows"]) == 6


def test_daily_does_not_refill_short_or_adequate_ranges(monkeypatch) -> None:
    class Repo:
        def resolve_asset_type(self, symbol: str) -> str:
            return "stock"

        def get_instruments(self) -> pl.DataFrame:
            return pl.DataFrame()

        def get_daily_asset(self, asset_type, symbol, start, end, columns=None):
            return _frame(6)

    monkeypatch.setattr(
        kline.kline_sync,
        "sync_and_persist_daily_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected refill")),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=Repo(), capabilities=object(), quote_service=None,
    )))

    kline.get_daily(
        request,
        symbol="301716.SZ",
        days=120,
        start_date="2026-03-30",
        end_date="2026-09-30",
        ext_columns=None,
    )
