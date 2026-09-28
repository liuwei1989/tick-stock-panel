"""zzshare 数据源同步 — 补充本地缺失的 A 股日线行情数据。

zzshare (https://api.zizizaizai.com) 提供全市场日线/交易日历等免费 A 股数据。
本模块把 zzshare 日线行情接入 TSP 官方数据链:

  1. 交易日历   → zzshare trade_days 接口取最近 N 个交易日
  2. 逐日日线   → daily(trade_date, limit=6000) 拉全市场原始 OHLCV
  3. 除权因子   → 从 pre_close 反推 ex_factor (close[t-1]/pre_close[t] ≠1 即除权事件),
                  写 data/adj_factor/all.parquet (TSP 官方读取路径)
  4. kline_daily → repository.append_daily (按 date= 分区, merge-upsert 幂等, 已存在日期跳过)
  5. enriched   → indicators.pipeline.run_pipeline 全量计算
                  (读 kline_daily + adj_factor → 前复权 + 指标 + 存储列)

与官方路径对齐: adj_factor 结构 symbol/trade_date/ex_factor (个股级非累积), 
前复权原理见 indicators/pipeline._apply_adj_factor。
"""
from __future__ import annotations

import logging
import time
from datetime import date as date_cls
from pathlib import Path
from typing import Callable

import polars as pl

from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)

Progress = Callable[[str], None]

DAILY_LIMIT = 6000  # zzshare 单日全市场行数上限
EX_FACTOR_TOL = 1e-4  # |close[t-1]/pre_close[t] - 1| 超过该阈值视为除权事件

_DAY_FORMAT = "%Y%m%d"


def _emit(progress: Progress | None, message: str) -> None:
    if progress:
        progress(message)
    logger.info("zzshare_sync: %s", message)


_token_idx = 0


def _tokens() -> list[str]:
    import os

    return [t.strip() for t in os.getenv("ZZSHARE_TOKENS", "").split(",") if t.strip()]


def _client() -> "DataApi":
    from zzshare.client import DataApi

    global _token_idx
    tokens = _tokens()
    if tokens:
        token = tokens[_token_idx % len(tokens)]
        _token_idx += 1  # 轮询, 多 token 分摊限流
        return DataApi(token=token, timeout=20)
    return DataApi(timeout=20)


def _with_retry(action: Callable[[], object], label: str, attempts: int = 3) -> object:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts:
                time.sleep(1.2 * attempt)
    raise RuntimeError(f"{label} 失败: {last_error}")


def list_trade_days(days: int) -> list[str]:
    """最近 N 个交易日 (YYYYMMDD, 升序)。"""
    rows = _with_retry(lambda: _client().trade_days(days=days), "拉取交易日历")
    days_out: list[str] = []
    for row in rows or []:
        if isinstance(row, str):
            raw = row.replace("-", "")
            if len(raw) == 8 and raw.isdigit():
                days_out.append(raw)
            continue
        if isinstance(row, dict):
            value = row.get("trade_date") or row.get("date") or row.get("day")
            if not value:
                continue
            raw = str(value).replace("-", "")
            if len(raw) == 8 and raw.isdigit():
                days_out.append(raw)
    return sorted(set(days_out))


def fetch_daily(trade_date: str) -> pl.DataFrame:
    """拉取某交易日全市场日线, 标准化为 TSP 字段。"""
    import pandas as pd

    df = _with_retry(
        lambda: _client().daily(trade_date=trade_date.replace("-", ""), limit=DAILY_LIMIT),
        f"{trade_date} 全市场日线",
    )
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return pl.DataFrame()
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)

    renamed = df.rename(columns={"ts_code": "symbol", "vol": "volume", "trade_date": "date"})
    renamed["symbol"] = renamed["symbol"].map(lambda v: str(v).split(".")[0].zfill(6))
    renamed["date"] = pd.to_datetime(renamed["date"], format="%Y%m%d").dt.date

    cols = [c for c in ["symbol", "date", "open", "high", "low", "close", "pre_close",
                        "change", "pct_chg", "volume", "amount"] if c in renamed.columns]
    out = pl.from_pandas(renamed[cols], include_index=False)
    for col in ["open", "high", "low", "close", "pre_close", "change", "pct_chg", "volume", "amount"]:
        if col in out.columns:
            out = out.with_columns(pl.col(col).cast(pl.Float64, strict=False))
    if "date" in out.columns:
        out = out.with_columns(pl.col("date").cast(pl.Date, strict=False))
    if "symbol" in out.columns:
        out = out.with_columns(pl.col("symbol").cast(pl.Utf8, strict=False))
    return out


def build_ex_factors(daily: pl.DataFrame) -> pl.DataFrame:
    """从 pre_close 反推除权因子表 (symbol/trade_date/ex_factor)。

    ex_factor = close[t-1] / pre_close[t]; 无除权日为 1 (噪声容差内忽略)。
    结构对齐 TSP: adj_factor/all.parquet, symbol/trade_date/ex_factor。
    """
    required = {"symbol", "date", "close", "pre_close"}
    if daily.is_empty() or not required.issubset(daily.columns):
        return pl.DataFrame()

    df = (
        daily.select("symbol", "date", "close", "pre_close")
        .filter(pl.col("pre_close").is_not_null() & (pl.col("close") > 0) & (pl.col("pre_close") > 0))
        .sort(["symbol", "date"])
    )
    df = df.with_columns(
        (pl.col("close").shift(1).over("symbol") / pl.col("pre_close")).alias("_ratio")
    )
    factors = (
        df.filter(pl.col("_ratio").is_not_null())
        .with_columns((pl.col("_ratio") - 1.0).abs().alias("_dev"))
        .filter(pl.col("_dev") > EX_FACTOR_TOL)
        .select(
            pl.col("symbol"),
            pl.col("date").alias("trade_date"),
            pl.col("_ratio").alias("ex_factor"),
        )
        .unique(subset=["symbol", "trade_date"], keep="last")
        .sort(["symbol", "trade_date"])
    )
    return factors


def save_adj_factor(data_dir: Path, factors: pl.DataFrame) -> int:
    """写 adj_factor/all.parquet (merge 增量)。返回写入/更新行数。"""
    if factors.is_empty():
        return 0
    out = data_dir / "adj_factor" / "all.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = pl.DataFrame()
    if out.exists():
        try:
            existing = pl.read_parquet(out)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取既有 adj_factor 失败, 全量覆盖: %s", e)
    merged = (
        pl.concat([existing, factors.select("symbol", "trade_date", "ex_factor")])
        .unique(subset=["symbol", "trade_date"], keep="last")
        .sort(["symbol", "trade_date"])
    )
    merged.write_parquet(out)
    return len(factors)


def _existing_daily_dates(data_dir: Path) -> set[str]:
    daily_dir = data_dir / "kline_daily"
    if not daily_dir.exists():
        return set()
    dates: set[str] = set()
    for partition in daily_dir.glob("date=*"):
        dates.add(partition.stem.split("=")[1])
    return dates


def sync_daily(
    days: int = 260,
    *,
    data_dir: Path | None = None,
    repo: KlineRepository | None = None,
    progress: Progress | None = None,
    force_refresh: bool = False,
    workers: int = 4,
) -> dict:
    """同步最近 N 个交易日全市场日线 + 除权因子, 并触发 enriched 全量计算。

    并发策略: 按交易日分片并发拉取 (zzshare 单请求 ~20s 且连接不稳定,
    多 worker + token 轮询显著提速); 除权因子统一合并写入, 避免并发写冲突。

    返回统计: {days_checked, days_fetched, rows, factor_events, enriched_rows}。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    d = Path(data_dir) if data_dir else _default_data_dir()
    repo = repo or KlineRepository(_store(d))
    trade_days = list_trade_days(days)
    if not trade_days:
        raise RuntimeError("zzshare 交易日历为空, 无法同步")
    _emit(progress, f"交易日历 {len(trade_days)} 天 ({trade_days[0]}~{trade_days[-1]})")

    existing = set() if force_refresh else _existing_daily_dates(d)
    todo = [d for d in trade_days if d not in existing]
    _emit(progress, f"待拉取 {len(todo)} 天 (已有 {len(trade_days) - len(todo)} 天跳过, workers={workers})")

    fetched_days = 0
    rows = 0
    factors_all: list[pl.DataFrame] = []

    def _sync_one(trade_date: str) -> tuple[str, int, pl.DataFrame]:
        df = fetch_daily(trade_date)
        if df.is_empty():
            return trade_date, 0, pl.DataFrame()
        repo.append_daily(df)
        factors = build_ex_factors(df)
        return trade_date, len(df), factors

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_sync_one, td): td for td in todo}
        for future in as_completed(futures):
            td, n, factors = future.result()
            done += 1
            if n == 0:
                _emit(progress, f"[{done}/{len(todo)}] {td} 无数据, 跳过")
                continue
            fetched_days += 1
            rows += n
            if not factors.is_empty():
                factors_all.append(factors)
            _emit(progress, f"[{done}/{len(todo)}] {td} 写入 {n} 行, 因子 {len(factors)} 个")

    factors_total = 0
    if factors_all:
        merged = pl.concat(factors_all)
        factors_total = save_adj_factor(d, merged)
    _emit(progress, f"日线同步完成: {fetched_days} 天 / {rows} 行, 除权因子 {factors_total} 个")

    # enriched 全量计算 (官方路径: 读 kline_daily + adj_factor → 前复权 + 指标)
    from app.indicators.pipeline import run_pipeline

    enriched_rows = run_pipeline(d)
    _emit(progress, f"enriched 计算完成: {enriched_rows} 行")

    return {
        "days_checked": len(trade_days),
        "days_fetched": fetched_days,
        "rows": rows,
        "factor_events": factors_total,
        "enriched_rows": enriched_rows,
    }


def _store(data_dir: Path):
    from app.tickflow.repository import DataStore

    return DataStore(data_dir=data_dir)


def _default_data_dir() -> Path:
    from app.config import settings

    return settings.data_dir


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="zzshare 数据源同步 (补充 TSP 缺失日线)")
    parser.add_argument("--days", type=int, default=260, help="拉取最近 N 个交易日")
    parser.add_argument("--workers", type=int, default=4, help="并发拉取 worker 数")
    parser.add_argument("--data-dir", type=str, default=None, help="数据目录 (默认 settings.data_dir)")
    parser.add_argument("--force", action="store_true", help="强制重拉已存在日期")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    def _progress(message: str) -> None:
        print(f"[sync] {message}", flush=True)

    result = sync_daily(args.days, data_dir=Path(args.data_dir) if args.data_dir else None,
                        progress=_progress, force_refresh=args.force, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
