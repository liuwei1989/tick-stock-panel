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
_TOKEN_CFG_DIR = "config"
_TOKEN_CFG_FILE = "zzshare.json"


def _token_config_path() -> Path | None:
    try:
        d = _default_data_dir()
    except Exception:  # noqa: BLE001
        d = None
    return (d / _TOKEN_CFG_DIR / _TOKEN_CFG_FILE) if d else None


def _tokens() -> list[str]:
    """token 池, 优先级: 本地配置 (data/config/zzshare.json) > 环境变量 ZZSHARE_TOKENS。

    每次读取实时生效 — 后台更新配置后无需重启进程, 下次请求即用新 token。
    """
    import json
    import os

    cfg = _token_config_path()
    if cfg and cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            tokens = [t.strip() for t in data.get("tokens") or [] if isinstance(t, str) and t.strip()]
            if tokens:
                return tokens
        except Exception:  # noqa: BLE001 配置损坏时回退环境变量
            logger.warning("zzshare token 配置解析失败, 回退环境变量: %s", cfg)
    return [t.strip() for t in os.getenv("ZZSHARE_TOKENS", "").split(",") if t.strip()]


def get_tokens() -> list[str]:
    """当前生效 token 池 (供管理 API 读取)。"""
    return _tokens()


def set_tokens(tokens: list[str]) -> int:
    """后台配置 token 池: 写入 data/config/zzshare.json, 热生效 (下次请求即用)。"""
    import json

    cleaned = [t.strip() for t in tokens if isinstance(t, str) and t.strip()]
    if not cleaned:
        raise ValueError("tokens 不能为空")
    cfg = _token_config_path()
    if cfg is None:
        raise RuntimeError("无法定位 data/config 目录")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"tokens": cleaned}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    logger.info("zzshare token 池已更新为 %d 个 token", len(cleaned))
    return len(cleaned)


def ensure_tokens_config() -> None:
    """首次使用时把环境变量 token 固化到本地配置, 使 token 真正"可后台配置"。"""
    import os

    if _tokens_from_config():
        return
    env_tokens = [t.strip() for t in os.getenv("ZZSHARE_TOKENS", "").split(",") if t.strip()]
    if env_tokens:
        set_tokens(env_tokens)


def _tokens_from_config() -> list[str]:
    cfg = _token_config_path()
    if not cfg or not cfg.exists():
        return []
    try:
        import json
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return [t.strip() for t in data.get("tokens") or [] if t.strip()]
    except Exception:  # noqa: BLE001
        return []


def _client() -> "DataApi":
    from zzshare.client import DataApi

    global _token_idx
    tokens = _tokens()
    client = DataApi(token=(tokens[_token_idx % len(tokens)] if tokens else ""), timeout=20)
    if tokens:
        _token_idx += 1  # 轮询, 多 token 分摊限流
    # 429 由外层 _with_retry 统一换 token 重试; 内部同 token 长睡重试只拖慢
    _disable_internal_retry(client)
    return client


def _disable_internal_retry(client: "DataApi") -> None:
    """替换 client 内部 _request_with_retry: 429/网络错误直接抛异常, 不做长睡重试。

    外层 _with_retry 负责换 token + 短退避, 多 token 时 429 立刻换 token 重试,
    避免同 token 反复 sleep Retry-After (15~52s) 导致同步蜗牛化。
    """
    import requests

    def _no_long_retry(url: str, params: dict | None = None, max_retries: int = 3):
        res = requests.get(url, params=params, headers=client.headers, timeout=client.timeout)
        if res.status_code == 429:
            raise RuntimeError("zzshare 429 rate limited (Retry-After=%s)"
                               % res.headers.get("Retry-After"))
        if res.status_code >= 400:
            raise RuntimeError("zzshare http %s: %s" % (res.status_code, res.text[:200]))
        return res

    client._request_with_retry = _no_long_retry  # type: ignore[assignment]


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


def _resolve_minute_pool(data_dir: Path, pool: str) -> list[str]:
    """分钟回填股票池: limitup=最新交易日涨停/连板股; all=最新交易日全市场。"""
    enriched_dir = data_dir / "kline_daily_enriched"
    dates = sorted(part.parent.name.replace("date=", "") for part in enriched_dir.glob("date=*/part.parquet"))
    if not dates:
        return []
    latest = dates[-1]
    df = pl.read_parquet(enriched_dir / f"date={latest}" / "part.parquet")
    syms = df["symbol"].cast(pl.Utf8).unique().to_list()
    if pool == "limitup":
        if "consecutive_limit_ups" not in df.columns:
            return sorted(syms)[:500]
        up = df.filter(pl.col("consecutive_limit_ups").fill_null(0) >= 1)
        if up.is_empty():
            return sorted(syms)[:500]
        return sorted(up["symbol"].cast(pl.Utf8).unique().to_list())
    return sorted(syms)


def sync_minute(
    days: int = 10,
    *,
    data_dir: Path | None = None,
    pool: str = "limitup",
    symbols: list[str] | None = None,
    freq: str = "1m",
    workers: int = 4,
    progress: Progress | None = None,
) -> dict:
    """回填最近 N 个交易日 × 股票池的分钟 K → kline_minute (zzshare 数据源)。

    zzshare stk_mins 为「单股×区间」粒度, 全市场×全历史不可行; 因此按
    「交易日 × 池」拉取: 默认池 = 最新交易日涨停/连板股 (短线能力直接相关),
    可传 --symbols 显式覆盖。落盘复用 _write_minute_partition (按 date= 分区,
    与 kline_sync 分钟存储同 schema: symbol/datetime/open/high/low/close/volume/amount)。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.services.kline_sync import _write_minute_partition

    d = Path(data_dir) if data_dir else _default_data_dir()
    minute_dir = d / "kline_minute"
    minute_dir.mkdir(parents=True, exist_ok=True)

    # 交易日: zzshare 日历, 取最近 N 个
    from datetime import datetime as _dt
    trade_days = list_trade_days(days)
    if not trade_days:
        raise RuntimeError("zzshare 交易日历为空, 无法同步分钟")
    _emit(progress, f"分钟回填交易日 {len(trade_days)} 天 ({trade_days[0]}~{trade_days[-1]})")

    # 股票池
    syms = list(symbols) if symbols else _resolve_minute_pool(d, pool)
    if not syms:
        raise RuntimeError("分钟股票池为空 (enriched 无数据; 可用 --symbols 指定)")
    _emit(progress, f"分钟股票池 {len(syms)} 只 (pool={pool})")

    # ts_code 后缀: instruments 维表 exchange → 交易所代码
    inst = pl.read_parquet(d / "instruments" / "instruments.parquet").select(
        pl.col("symbol").cast(pl.Utf8), "exchange",
    ).unique(subset=["symbol"])
    ex_map = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}
    suffix = {
        str(r["symbol"]): f"{r['symbol']}.{ex_map.get(str(r['exchange']).upper(), 'SH')}"
        for r in inst.to_dicts()
    }

    client = _client()
    rows_written = 0
    days_done = 0

    def _fetch_day(sym: str, trade_date: str) -> pl.DataFrame:
        ts_code = suffix.get(sym, sym)
        raw = _with_retry(
            lambda: client.stk_mins(ts_code=ts_code, trade_time=trade_date, freq=freq),
            f"{sym} {trade_date} 分钟",
        )
        import pandas as pd
        if raw is None or (isinstance(raw, pd.DataFrame) and raw.empty):
            return pl.DataFrame()
        df = pl.from_pandas(pd.DataFrame(raw), include_index=False)
        if df.is_empty():
            return df
        df = df.rename({k: v for k, v in {
            "ts_code": "symbol", "vol": "volume", "amt": "amount",
        }.items() if k in df.columns})
        df = df.with_columns(pl.col("symbol").cast(pl.Utf8).map_elements(
            lambda v: str(v).split(".")[0].zfill(6), return_dtype=pl.Utf8,
        ))
        if "trade_time" in df.columns:
            df = df.with_columns(
                pl.col("trade_time").cast(pl.Utf8).str.strptime(
                    pl.Datetime("us"), "%Y%m%d%H%M"
                ).alias("datetime"),
            ).drop("trade_time")
        for col in ("open", "high", "low", "close", "volume", "amount"):
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
        keep = [c for c in ("symbol", "datetime", "open", "high", "low", "close", "volume", "amount")
                if c in df.columns]
        return df.select(keep)

    for trade_date in trade_days:
        parts: list[pl.DataFrame] = []
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool_exec:
            futures = {pool_exec.submit(_fetch_day, sym, trade_date): sym for sym in syms}
            for fut in as_completed(futures):
                done += 1
                df = fut.result()
                if not df.is_empty():
                    parts.append(df)
                if done % 50 == 0:
                    _emit(progress, f"[{trade_date}] {done}/{len(syms)}")
        if parts:
            day_df = pl.concat(parts)
            n = _write_minute_partition(day_df, minute_dir)
            rows_written += n
        days_done += 1
        _emit(progress, f"[{trade_date}] 完成 ({done} 只, 写入 {rows_written} 行累计)")

    return {"days": days_done, "pool_size": len(syms), "rows_written": rows_written}


def sync_instruments(
    data_dir: Path | None = None,
    *,
    progress: Progress | None = None,
) -> int:
    """同步全市场股票维表 → data/instruments/instruments.parquet (zzshare 数据源)。

    基础字段 (symbol/name/exchange) 来自 zzshare stock_basic; listing_date 该接口
    返回空串, 用本地 kline_daily 每只股票的最早日期推断 (数据窗口内近似, 用于
    注册制新股无涨跌幅窗口判定; 老股不受影响)。float_shares/limit_up/limit_down
    zzshare 未提供 → None, 上层走理论价/规则退化路径。

    写入列与 tickflow 版本维表兼容 (instrument_sync._flatten_instruments 同 schema)。
    返回写入行数。
    """
    from app.data_providers.zzshare_provider import ZzshareProvider
    from app.tickflow.repository import KlineRepository
    from app.services.fs_utils import atomic_write_parquet

    d = Path(data_dir) if data_dir else _default_data_dir()
    repo = KlineRepository(_store(d))
    df = ZzshareProvider().get_instruments("stock")
    if df.is_empty():
        raise RuntimeError("zzshare 股票列表为空, 无法同步 instruments")
    _emit(progress, f"zzshare stock_basic: {df.height} 只")

    # listing_date 兜底: 本地 kline_daily 最早日期 (数据窗口内近似上市日期)
    if df["listing_date"].null_count() == df.height:
        daily_dir = d / "kline_daily"
        parts = list(daily_dir.glob("date=*/part.parquet"))
        if parts:
            first = pl.scan_parquet(sorted(parts)[0])
            try:
                earliest = (
                    first.select(["symbol", "date"])
                    .group_by("symbol").agg(pl.col("date").min().alias("listing_date"))
                    .collect()
                )
                df = df.drop("listing_date").join(
                    earliest.with_columns(pl.col("symbol").str.to_uppercase()),
                    on="symbol", how="left",
                )
                _emit(progress, f"listing_date 由本地日线推断: {earliest.height} 只")
            except Exception as e:  # noqa: BLE001
                logger.warning("listing_date 推断失败: %s", e)
    elif df["listing_date"].null_count() > 0:
        daily_dir = d / "kline_daily"
        parts = list(daily_dir.glob("date=*/part.parquet"))
        if parts:
            first = pl.scan_parquet(sorted(parts)[0])
            try:
                earliest = (
                    first.select(["symbol", "date"])
                    .group_by("symbol").agg(pl.col("date").min().alias("listing_date"))
                    .collect()
                )
                missing = df.filter(pl.col("listing_date").is_null()).select("symbol")
                joined = missing.join(
                    earliest.with_columns(pl.col("symbol").str.to_uppercase()),
                    on="symbol", how="left",
                )
                fill_map = dict(zip(joined["symbol"].to_list(), joined["listing_date"].to_list(), strict=False))
                df = df.with_columns(
                    pl.when(pl.col("symbol").is_in(list(fill_map.keys())))
                    .then(pl.col("symbol").map_elements(lambda s: fill_map.get(s), return_dtype=pl.Date))
                    .otherwise(pl.col("listing_date"))
                    .alias("listing_date")
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("listing_date 局部推断失败: %s", e)

    out = d / "instruments" / "instruments.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(df.sort("symbol"), out)
    _emit(progress, f"instruments 写入: {df.height} 行 → {out}")
    return df.height


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
    parser.add_argument("--minute-days", type=int, default=0,
                        help=">0 时执行分钟回填: 最近 N 个交易日")
    parser.add_argument("--minute-pool", type=str, default="limitup",
                        help="分钟股票池: limitup(最新涨停/连板股) | all(全市场)")
    parser.add_argument("--minute-symbols", type=str, default="",
                        help="分钟股票池显式覆盖: 逗号分隔 6 位代码")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    def _progress(message: str) -> None:
        print(f"[sync] {message}", flush=True)

    result = sync_daily(args.days, data_dir=Path(args.data_dir) if args.data_dir else None,
                        progress=_progress, force_refresh=args.force, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.minute_days > 0:
        symbols = [x.strip() for x in args.minute_symbols.split(",") if x.strip()] if args.minute_symbols else None
        mresult = sync_minute(args.minute_days, data_dir=Path(args.data_dir) if args.data_dir else None,
                              pool=args.minute_pool, symbols=symbols, progress=_progress)
        print(json.dumps({"minute": mresult}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
