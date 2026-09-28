"""Zzshare 数据源 Provider — TSP 数据源插件体系中的内置源。

能力: instruments (股票列表) / daily (日K) / adj_factor (除权因子, pre_close 反推)。
与其他 provider 一样返回规范化 polars schema (normalizer 契约), 存储/指标/回测
保持数据源无关。日K按交易日全市场拉取 (zzshare daily trade_date 粒度),
并发拉取 + token 轮询 (ZZSHARE_TOKENS 逗号分隔) 缓解单请求 ~20s 的慢速/不稳定。

接入点: registry._PROVIDERS (get_provider("zzshare")) + preferences 合法源列表 +
capabilities 候选矩阵 (设置页数据源路由)。路由到本源的场景由上层 (kline_sync /
instrument_sync / daily_pipeline) 自动走 provider 契约拉数并落盘。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import polars as pl

from app.data_providers.base import AssetType, MarketDataProvider, ProviderCapabilities
from app.data_providers.normalizer import ADJ_FACTOR_COLS, DAILY_COLS, INSTRUMENT_COLS
from app.services import zzshare_sync

logger = zzshare_sync.logger

_EXCHANGE_MAP = {
    "SSE": "SH",
    "SZSE": "SZ",
    "BSE": "BJ",
    "SS": "SH",
    "SH": "SH",
    "SZ": "SZ",
    "BJ": "BJ",
}

# 并发拉取 worker 数 (单请求慢, 并行显著提速)
_FETCH_WORKERS = 4


class ZzshareProvider(MarketDataProvider):
    name = "zzshare"
    display = "Zzshare"

    capabilities = ProviderCapabilities(
        instruments=True,
        daily=True,
        adj_factor=True,
        minute=True,
    )

    # ── 交易日区间 ──────────────────────────────────────────

    def _trade_days_in_range(
        self,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> list[str]:
        """区间 [start_time, end_time] 内的交易日 (YYYYMMDD, 升序)。

        zzshare trade_days 支持 day_start/day_end 区间查询。
        """
        import pandas as pd

        if start_time is None and end_time is None:
            return zzshare_sync.list_trade_days(365)
        day_start = start_time.strftime("%Y%m%d") if start_time else None
        day_end = end_time.strftime("%Y%m%d") if end_time else None

        kwargs: dict = {}
        if day_start:
            kwargs["day_start"] = day_start
        if day_end:
            kwargs["day_end"] = day_end
        rows = zzshare_sync._with_retry(
            lambda: zzshare_sync._client().trade_days(days=365, **kwargs),
            "区间交易日历",
        )
        days: list[str] = []
        for row in rows or []:
            value = row.get("trade_date") if isinstance(row, dict) else row
            raw = str(value).replace("-", "")
            if len(raw) == 8 and raw.isdigit():
                days.append(raw)
        out = sorted(set(days))
        if start_time and end_time:
            out = [d for d in out if day_start <= d <= day_end]
        elif start_time:
            out = [d for d in out if d >= day_start]
        elif end_time:
            out = [d for d in out if d <= day_end]
        return out

    # ── instruments ─────────────────────────────────────────

    def get_instruments(self, asset_type: AssetType = "stock") -> pl.DataFrame:
        if asset_type != "stock":
            return pl.DataFrame()
        import pandas as pd

        raw = zzshare_sync._with_retry(
            lambda: zzshare_sync._client().stock_basic(list_status="L"),
            "股票列表",
        )
        if raw is None or (isinstance(raw, pd.DataFrame) and raw.empty):
            return pl.DataFrame()
        if not isinstance(raw, pd.DataFrame):
            raw = pd.DataFrame(raw)
        df = pl.from_pandas(raw, include_index=False)
        if "ts_code" in df.columns and "symbol" not in df.columns:
            df = df.with_columns(pl.col("ts_code").alias("symbol"))
        if "symbol" not in df.columns:
            return pl.DataFrame()
        rows: list[dict] = []
        for item in df.to_dicts():
            symbol = str(item.get("symbol") or "").split(".")[0].zfill(6)
            if not symbol:
                continue
            ex_raw = str(item.get("exchange") or "").upper()
            row = {
                "symbol": symbol,
                "name": item.get("name") or symbol,
                "code": symbol,
                "exchange": _EXCHANGE_MAP.get(ex_raw, ex_raw),
                "asset_type": "stock",
                "source": self.name,
            }
            # 涨跌停信号 / 新股无涨跌幅窗口 / 换手率所需的维表字段:
            # listing_date 来自 stock_basic.list_date; float_shares / limit_up /
            # limit_down 该接口无 → 置空, 由上层走理论价/规则退化路径。
            raw_list = item.get("list_date") or item.get("listing_date") or ""
            listing_date = str(raw_list)[:10] if raw_list else None
            if listing_date:
                listing_date = f"{listing_date[:4]}-{listing_date[4:6]}-{listing_date[6:8]}"
                row["listing_date"] = listing_date
            fsh = item.get("float_share") or item.get("float_shares")
            if fsh:
                row["float_shares"] = float(fsh)
            rows.append(row)
        if not rows:
            return pl.DataFrame()
        df_out = pl.DataFrame(rows)
        # 显式补齐可选维表列 (首个 dict 的键决定 polars schema, 缺列须补 None)
        for col, dtype in (("listing_date", pl.Date), ("float_shares", pl.Float64),
                           ("limit_up", pl.Float64), ("limit_down", pl.Float64)):
            if col not in df_out.columns:
                df_out = df_out.with_columns(pl.lit(None).cast(dtype).alias(col))
        return (
            df_out
            .with_columns(pl.lit(datetime.now().date().isoformat()).alias("as_of"))
            .select([*INSTRUMENT_COLS, "listing_date", "float_shares",
                     "limit_up", "limit_down", "as_of"])
            .unique(subset=["symbol"], keep="last")
            .sort("symbol")
        )

    # ── daily ───────────────────────────────────────────────

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType = "stock",
        on_chunk_done=None,  # noqa: ANN001
    ) -> pl.DataFrame:
        if asset_type != "stock":
            return pl.DataFrame()
        days = self._trade_days_in_range(start_time, end_time)
        if not days:
            return pl.DataFrame()
        symbol_set: set[str] | None = {s.split(".")[0].zfill(6) for s in symbols} if symbols else None

        parts: list[pl.DataFrame] = []
        done = 0
        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
            futures = {pool.submit(zzshare_sync.fetch_daily, d): d for d in days}
            for future in as_completed(futures):
                done += 1
                df = future.result()
                if on_chunk_done:
                    try:
                        on_chunk_done(done, len(days))
                    except Exception:  # noqa: BLE001
                        pass
                if df.is_empty():
                    continue
                if symbol_set is not None:
                    df = df.filter(pl.col("symbol").is_in(symbol_set))
                if df.is_empty():
                    continue
                parts.append(df)

        if not parts:
            return pl.DataFrame()
        out = pl.concat(parts)
        keep = [c for c in DAILY_COLS if c in out.columns]
        if "quote_ts" not in out.columns:
            out = out.with_columns(pl.lit(None).cast(pl.Int64).alias("quote_ts"))
            keep = DAILY_COLS
        out = out.select(keep).sort(["symbol", "date"])
        return out

    # ── adj_factor ──────────────────────────────────────────

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType = "stock",
        on_chunk_done=None,  # noqa: ANN001
    ) -> pl.DataFrame:
        if asset_type != "stock":
            return pl.DataFrame()
        days = self._trade_days_in_range(start_time, end_time)
        if not days:
            return pl.DataFrame()
        symbol_set: set[str] | None = {s.split(".")[0].zfill(6) for s in symbols} if symbols else None

        parts: list[pl.DataFrame] = []
        done = 0
        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
            futures = {pool.submit(zzshare_sync.fetch_daily, d): d for d in days}
            for future in as_completed(futures):
                done += 1
                df = future.result()
                if on_chunk_done:
                    try:
                        on_chunk_done(done, len(days))
                    except Exception:  # noqa: BLE001
                        pass
                if df.is_empty():
                    continue
                if symbol_set is not None:
                    df = df.filter(pl.col("symbol").is_in(symbol_set))
                if df.is_empty():
                    continue
                factors = zzshare_sync.build_ex_factors(df)
                if not factors.is_empty():
                    parts.append(factors)

        if not parts:
            return pl.DataFrame()
        out = pl.concat(parts).unique(subset=["symbol", "trade_date"], keep="last")
        return out.select(ADJ_FACTOR_COLS).sort(["symbol", "trade_date"])

    # ── minute ──────────────────────────────────────────────

    _FREQ_MAP = {"1m": "1min", "1min": "1min", "5m": "5min", "5min": "5min",
                 "15m": "15min", "30m": "30min", "60m": "60min"}

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType = "stock",
        freq: str = "1m",
        on_chunk_done=None,  # noqa: ANN001
    ) -> pl.DataFrame:
        if asset_type != "stock" or not symbols:
            return pl.DataFrame()
        import pandas as pd

        zfreq = self._FREQ_MAP.get(str(freq).lower(), str(freq).lower())
        kwargs: dict = {"freq": zfreq}
        if start_time is not None:
            kwargs["start_time"] = start_time.strftime("%Y-%m-%d %H:%M")
        if end_time is not None:
            kwargs["end_time"] = end_time.strftime("%Y-%m-%d %H:%M")

        symbol_list = [s.split(".")[0].zfill(6) for s in symbols]

        def _fetch_one(sym: str) -> pl.DataFrame:
            raw = zzshare_sync._with_retry(
                lambda: zzshare_sync._client().stk_mins(ts_code=sym, **kwargs),
                f"{sym} 分钟K",
            )
            if raw is None or (isinstance(raw, pd.DataFrame) and raw.empty):
                return pl.DataFrame()
            if not isinstance(raw, pd.DataFrame):
                raw = pd.DataFrame(raw)
            df = pl.from_pandas(raw, include_index=False)
            if df.is_empty():
                return df
            rename_map = {
                "ts_code": "symbol",
                "vol": "volume",
                "amt": "amount",
            }
            df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})
            df = df.with_columns(pl.col("symbol").map_elements(
                lambda v: str(v).split(".")[0].zfill(6), return_dtype=pl.Utf8,
            ))
            if "trade_time" in df.columns:
                df = df.rename({"trade_time": "datetime"}).with_columns(
                    pl.col("datetime").str.strptime(pl.Datetime("us"), "%Y%m%d%H%M")
                    .alias("datetime"),
                )
            for col in ("open", "high", "low", "close", "volume", "amount"):
                if col in df.columns:
                    df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
            keep = [c for c in ("symbol", "datetime", "open", "high", "low", "close", "volume", "amount")
                    if c in df.columns]
            return df.select(keep)

        parts: list[pl.DataFrame] = []
        done = 0
        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
            futures = {pool.submit(_fetch_one, sym): sym for sym in symbol_list}
            for future in as_completed(futures):
                done += 1
                df = future.result()
                if on_chunk_done:
                    try:
                        on_chunk_done(done, len(symbol_list))
                    except Exception:  # noqa: BLE001
                        pass
                if not df.is_empty():
                    parts.append(df)

        if not parts:
            return pl.DataFrame()
        out = pl.concat(parts)
        # 收口为北京墙钟 naive (zzshare trade_time 即北京时间, 无需时区换算)
        dt = out["datetime"].dtype
        if not (isinstance(dt, pl.Datetime) and dt.time_unit == "us"):
            out = out.with_columns(pl.col("datetime").cast(pl.Datetime("us")))
        return out.sort(["symbol", "datetime"])
