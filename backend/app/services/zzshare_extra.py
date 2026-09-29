"""zzshare 扩展数据同步 — 题材/涨停复盘/龙虎榜/情绪, 落盘供驾驶舱聚合。

zzshare SDK 共 56 个接口 (DataApi.SHORTCUTS), pip 包仅封装 8 个方法,
其余为 HTTP 端点模板, 本模块按 SHORTCUTS 统一调用:

  - 题材/板块 : plates_rank / plates_rank_days_new / plates_stocks / plates_list
                → data/topic_rank/{date}.json (当日+持续性+新进+热门成分股)
  - 涨停复盘   : uplimit_stocks / uplimit_hot / review_uplimit_reason
                → data/uplimit/{date}.json (含竞价 auction_* 字段)
  - 龙虎榜    : lhb_list / lhb_detail
                → data/lhb/{date}.json
  - 情绪     : market_sentiment
                → data/sentiment/part.parquet (时序)

驾驶舱 cockpit.py 直接读这些产物做聚合展示; 同步可独立 CLI 跑:
  python -m app.services.zzshare_extra --topics --uplimit --lhb --sentiment --days 5
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

import polars as pl

from app.services.zzshare_sync import _client, _with_retry, list_trade_days

logger = logging.getLogger(__name__)

Progress = Callable[[str], None]

_DAY_FMT = "%Y%m%d"

# 板块类型: 17=题材, 15=概念, 14=行业
PLATE_TYPES = {17: "题材", 15: "概念", 14: "行业"}
_PLATE_TYPE_DEFAULT = 15  # 概念


def _data_dir() -> Path:
    from app.config import settings
    return settings.data_dir


def _emit(progress: Progress | None, message: str) -> None:
    if progress:
        progress(message)
    logger.info("zzshare_extra: %s", message)


def _call_shortcut(name: str, **kwargs):
    """按 DataApi.SHORTCUTS 拼路径 + 参数调用任意接口。"""
    from zzshare.client import DataApi

    sc = DataApi.SHORTCUTS
    path_tpl, params_spec, conv, desc = sc[name]
    path = path_tpl
    q = dict(kwargs)
    for m in re.findall(r"\{(\w+)\}", path_tpl):
        if m in kwargs:
            path = path.replace("{" + m + "}", str(kwargs[m]))
            q.pop(m, None)
    return _with_retry(lambda: _client().query(path, q), name)


# ───────────────────────── 题材/板块 ─────────────────────────

def sync_topics(data_dir: Path | None = None, days: int = 5,
                progress: Progress | None = None) -> dict:
    """同步最近 N 个交易日的板块热度 + 区间持续性 + 热门成分股。

    落盘 data/topic_rank/{date}.json: {"date", "plates": [{plate_code, plate_name,
    score, rate, trade_money, money_leader, sum_rate, sum_score, days, is_new,
    top_stocks: [{stock_code, stock_name}]}]}
    """
    d = data_dir or _data_dir()
    out = d / "topic_rank"
    out.mkdir(parents=True, exist_ok=True)
    trade_days = list_trade_days(days)[-days:]
    written, total_plates = 0, 0
    for day in trade_days:
        day_file = out / f"{day}.json"
        if day_file.exists():
            continue
        date1 = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        rank = _call_shortcut("plates_rank", plate_type=_PLATE_TYPE_DEFAULT,
                              date1=date1, limit=20) or []
        days_new = _call_shortcut("plates_rank_days_new",
                                  plate_type=_PLATE_TYPE_DEFAULT, date2=date1,
                                  n_days=5, n_type=3, limit=20, prev_days=3) or []
        # 成分股只拉排名前 6 板块
        rows = []
        for p in rank[:6]:
            code = str(p.get("plate_code") or "")
            stocks = []
            if code:
                try:
                    stocks = _call_shortcut("plates_stocks",
                                            plate_type=_PLATE_TYPE_DEFAULT,
                                            plate_code=code, date=date1) or []
                except Exception:  # noqa: BLE001
                    stocks = []
            p2 = next((x for x in days_new if str(x.get("plate_code") or "") == code), {})
            rows.append({
                "plate_code": code,
                "plate_name": p.get("plate_name"),
                "score": p.get("score"),
                "rate": p.get("rate"),
                "trade_money": p.get("trade_money"),
                "money_leader": p.get("money_leader"),
                "sum_rate": p2.get("sum_rate"),
                "sum_score": p2.get("sum_score"),
                "days": p2.get("days"),
                "is_new": bool(p2.get("is_new")),
                "top_stocks": [{"stock_code": s.get("stock_code"),
                                "stock_name": s.get("stock_name")}
                               for s in stocks[:8]],
            })
        day_file.write_text(json.dumps({"date": day, "plates": rows},
                                       ensure_ascii=False, indent=1),
                            encoding="utf-8")
        written += 1
        total_plates += len(rows)
        _emit(progress, f"[topics] {day} 写入 {len(rows)} 板块")
        time.sleep(1)
    return {"days": len(trade_days), "written": written, "plates": total_plates}


# ───────────────────────── 涨停复盘 ─────────────────────────

def sync_uplimit(data_dir: Path | None = None, days: int = 5,
                 progress: Progress | None = None) -> dict:
    """同步最近 N 个交易日涨停股票列表 (含竞价字段)。

    落盘 data/uplimit/{date}.json: {"date", "stocks": [{ts_code, name,
    pct_chg, fd_max, first_time, auction_money, ...}]}
    """
    d = data_dir or _data_dir()
    out = d / "uplimit"
    out.mkdir(parents=True, exist_ok=True)
    trade_days = list_trade_days(days)[-days:]
    written, total = 0, 0
    for day in trade_days:
        day_file = out / f"{day}.json"
        if day_file.exists():
            continue
        date1 = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        stocks = _call_shortcut("uplimit_stocks", date1=date1) or []
        day_file.write_text(json.dumps({"date": day, "stocks": stocks},
                                       ensure_ascii=False, indent=1),
                            encoding="utf-8")
        written += 1
        total += len(stocks)
        _emit(progress, f"[uplimit] {day} 涨停 {len(stocks)} 只")
        time.sleep(1)
    return {"days": len(trade_days), "written": written, "stocks": total}


# ───────────────────────── 龙虎榜 ─────────────────────────

def sync_lhb(data_dir: Path | None = None, days: int = 3,
             progress: Progress | None = None) -> dict:
    """同步最近 N 个交易日龙虎榜 (列表 + TOP 个股详情)。

    落盘 data/lhb/{date}.json: {"date", "list": [...], "details": {...}}
    """
    d = data_dir or _data_dir()
    out = d / "lhb"
    out.mkdir(parents=True, exist_ok=True)
    trade_days = list_trade_days(days)[-days:]
    written, total = 0, 0
    for day in trade_days:
        day_file = out / f"{day}.json"
        if day_file.exists():
            continue
        date1 = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        lst = _call_shortcut("lhb_list", date1=date1) or []
        details = {}
        for item in lst[:3]:
            code = str(item.get("stock_code") or item.get("ts_code") or "")[:6]
            if not code:
                continue
            try:
                det = _call_shortcut("lhb_detail", date1=date1, stock_code=code)
                if det:
                    details[code] = det
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        day_file.write_text(json.dumps({"date": day, "list": lst, "details": details},
                                       ensure_ascii=False, indent=1),
                            encoding="utf-8")
        written += 1
        total += len(lst)
        _emit(progress, f"[lhb] {day} 上榜 {len(lst)} 只")
        time.sleep(1)
    return {"days": len(trade_days), "written": written, "stocks": total}


# ───────────────────────── 情绪 ─────────────────────────

def sync_sentiment(data_dir: Path | None = None, days: int = 30,
                   progress: Progress | None = None) -> dict:
    """同步最近 N 天市场情绪 K 线 → data/sentiment/part.parquet 时序。"""
    d = data_dir or _data_dir()
    out = d / "sentiment"
    out.mkdir(parents=True, exist_ok=True)
    from datetime import date as date_cls, timedelta

    end = date_cls.today()
    start = end - timedelta(days=days * 2)
    rows = _call_shortcut("market_sentiment",
                          date1=start.isoformat(), date2=end.isoformat()) or []
    if not rows:
        return {"days": 0, "rows": 0}
    df = pl.DataFrame(rows)
    if "date" in df.columns:
        df = df.with_columns(pl.col("date").cast(pl.Utf8).str.strptime(
            pl.Date, "%Y%m%d", strict=False).alias("date"))
    df.write_parquet(out / "part.parquet")
    _emit(progress, f"[sentiment] {len(rows)} 行情绪 K 线")
    return {"days": len(rows), "rows": len(rows)}


# ───────────────────────── 概念成分 (主线认证) ─────────────────────────

_EXT_CONCEPT_ID = "zzshare_concept"


def sync_concepts(data_dir: Path | None = None, top_n: int = 30,
                  progress: Progress | None = None) -> dict:
    """同步最新交易日概念成分 → ext_data/zzshare_concept (timeseries)。

    plates_rank(15概念, TOP N) → plates_stocks 每概念成分 → 展开 (symbol, concept)
    写入 data/ext_data/zzshare_concept/timeseries/date={date}.parquet,
    并写 config.json。market_mainline._load_concept_map_df 读最新 date 即可点亮主线认证。
    """

    d = data_dir or _data_dir()
    days = list_trade_days(5)
    latest = days[-1]
    date1 = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"

    base = d / "ext_data" / _EXT_CONCEPT_ID
    cfg_path = base / "config.json"
    if not cfg_path.exists():
        cfg = {
            "id": _EXT_CONCEPT_ID,
            "label": "zzshare 概念成分",
            "mode": "timeseries",
            "fields": [{"name": "symbol", "dtype": "string", "label": "代码"},
                       {"name": "concept", "dtype": "string", "label": "概念"}],
            "description": "zzshare plates_rank(概念TOP) + plates_stocks 成分, 主线认证维度",
            "symbol_map": {"type": "mapped", "col": "symbol"},
            "code_map": {},
        }
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                            encoding="utf-8")

    rank = _call_shortcut("plates_rank", plate_type=15, date1=date1, limit=top_n) or []
    rows: list[dict] = []
    plates_done = 0
    for p in rank:
        code = str(p.get("plate_code") or "")
        name = p.get("plate_name") or code
        if not code:
            continue
        try:
            stocks = _call_shortcut("plates_stocks", plate_type=15,
                                    plate_code=code, date=date1) or []
        except Exception:  # noqa: BLE001
            stocks = []
        for s in stocks:
            sc = str(s.get("stock_code") or "")[-6:]
            if sc:
                rows.append({"symbol": sc, "concept": name})
        plates_done += 1
        time.sleep(0.8)
    if not rows:
        _emit(progress, f"[concepts] {latest} 无成分数据")
        return {"date": latest, "rows": 0, "plates": 0}

    df = pl.DataFrame(rows).unique()
    df = df.with_columns(pl.lit(latest).alias("date"))
    ts_dir = base / "timeseries"
    ts_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(ts_dir / f"date={latest}.parquet")
    # 清理旧分区, 保持 timeseries 目录只含最近数据
    for old in ts_dir.glob("date=*.parquet"):
        if old.name != f"date={latest}.parquet":
            old.unlink(missing_ok=True)
    _emit(progress, f"[concepts] {latest} {len(df)} 行 × {plates_done} 板块 → {_EXT_CONCEPT_ID}")
    return {"date": latest, "rows": len(df), "plates": plates_done}


# ───────────────────────── 驾驶舱摘要 ─────────────────────────

def topics_summary(data_dir: Path) -> dict:
    """最新交易日题材摘要 (供驾驶舱): TOP 题材 + 持续性 + 新进。"""
    out = data_dir / "topic_rank"
    if not out.exists():
        return {"available": False, "date": None, "top": []}
    files = sorted(out.glob("*.json"))
    if not files:
        return {"available": False, "date": None, "top": []}
    latest = files[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    top = []
    for p in data.get("plates", [])[:6]:
        top.append({
            "plate_name": p.get("plate_name"),
            "score": p.get("score"),
            "rate": p.get("rate"),
            "is_new": p.get("is_new"),
            "days": p.get("days"),
            "sum_rate": p.get("sum_rate"),
            "leader": (p.get("top_stocks") or [{}])[0].get("stock_name") if p.get("top_stocks") else None,
        })
    return {"available": True, "date": data.get("date"), "top": top}


def uplimit_summary(data_dir: Path) -> dict:
    """最新交易日涨停摘要 (含竞价): 家数/最高连板/竞价溢价。"""
    out = data_dir / "uplimit"
    if not out.exists():
        return {"available": False, "date": None, "count": 0}
    files = sorted(out.glob("*.json"))
    if not files:
        return {"available": False, "date": None, "count": 0}
    latest = files[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    stocks = data.get("stocks", [])
    max_fd = 0
    for s in stocks:
        try:
            max_fd = max(max_fd, int(s.get("fd_max") or 0))
        except (TypeError, ValueError):
            pass
    return {"available": True, "date": data.get("date"), "count": len(stocks),
            "max_consecutive": max_fd, "sample": stocks[:5]}


def lhb_summary(data_dir: Path) -> dict:
    """最新交易日龙虎榜摘要: 上榜家数 + 机构/游资买入信号。"""
    out = data_dir / "lhb"
    if not out.exists():
        return {"available": False, "date": None, "count": 0}
    files = sorted(out.glob("*.json"))
    if not files:
        return {"available": False, "date": None, "count": 0}
    latest = files[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    lst = data.get("list", [])
    net_buy = []
    for item in lst:
        try:
            net = float(item.get("buy_in") or 0)
            if net > 0:
                net_buy.append({"name": item.get("stock_name") or item.get("name"),
                                "net": net})
        except (TypeError, ValueError):
            continue
    net_buy.sort(key=lambda x: -x["net"])
    return {"available": True, "date": data.get("date"), "count": len(lst),
            "top_net_buy": net_buy[:3]}


def sentiment_summary(data_dir: Path) -> dict:
    """市场情绪 K 线最新值 + 与 regime 涨停家数的交叉核验。"""
    p = data_dir / "sentiment" / "part.parquet"
    if not p.exists():
        return {"available": False}
    try:
        df = pl.read_parquet(p)
        if df.is_empty():
            return {"available": False}
        last = df.tail(1).to_dicts()[0]
        out = {"available": True,
               "date": str(last.get("date"))[:10],
               "p_close": last.get("p_close"),
               "p_open": last.get("p_open")}
        cross = _sentiment_regime_crosscheck(data_dir, df)
        if cross:
            out["crosscheck"] = cross
        return out
    except Exception:  # noqa: BLE001
        return {"available": False}


def _sentiment_regime_crosscheck(data_dir: Path, sent_df: pl.DataFrame) -> list[dict]:
    """最近 5 日: 情绪K线涨跌 vs regime 涨停家数/分数, 输出一致性对照。"""
    try:
        reg = pl.read_parquet(data_dir / "regime_history" / "part.parquet")
    except Exception:  # noqa: BLE001
        return []
    if reg.is_empty():
        return []
    sdf = sent_df.with_columns(pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("d"))
    rdf = reg.with_columns(pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("d"))
    merged = sdf.join(rdf, on="d", how="inner").sort("d")
    rows = []
    prev_close = None
    for r in merged.tail(5).to_dicts():
        close = r.get("p_close")
        chg = None
        if prev_close is not None and close is not None and prev_close not in (None, 0):
            try:
                chg = close / prev_close - 1.0
            except (TypeError, ZeroDivisionError):
                chg = None
        rows.append({
            "date": r.get("d"),
            "sentiment_pct": round(chg * 100, 2) if chg is not None else None,
            "limit_up": r.get("limit_up"),
            "score": r.get("score"),
            "phase": r.get("phase"),
        })
        if close is not None:
            prev_close = close
    return rows


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="zzshare 扩展数据同步 (题材/涨停/龙虎榜/情绪)")
    parser.add_argument("--topics", action="store_true", help="同步题材/板块热度")
    parser.add_argument("--uplimit", action="store_true", help="同步涨停复盘")
    parser.add_argument("--lhb", action="store_true", help="同步龙虎榜")
    parser.add_argument("--sentiment", action="store_true", help="同步情绪 K 线")
    parser.add_argument("--concepts", action="store_true", help="同步概念成分 (主线认证)")
    parser.add_argument("--all", action="store_true", help="全部同步")
    parser.add_argument("--days", type=int, default=5, help="同步最近 N 个交易日 (情绪默认 30)")
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    d = Path(args.data_dir) if args.data_dir else _data_dir()

    def _p(m: str) -> None:
        print(f"[sync] {m}", flush=True)

    results = {}
    if args.all or args.concepts:
        results["concepts"] = sync_concepts(d, progress=_p)
    if args.all or args.topics:
        results["topics"] = sync_topics(d, days=args.days, progress=_p)
    if args.all or args.uplimit:
        results["uplimit"] = sync_uplimit(d, days=args.days, progress=_p)
    if args.all or args.lhb:
        results["lhb"] = sync_lhb(d, days=args.days, progress=_p)
    if args.all or args.sentiment:
        results["sentiment"] = sync_sentiment(d, days=30, progress=_p)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
