"""盘前计划 + 程序化复盘 — 策略发现 → 盘前计划 → 复盘 全流程闭环。

闭环数据流:
  1. 策略发现   evolution.load_recommendations / get_applied_overrides
                (策略进化推荐/已应用参数)
  2. 盘前计划   generate_plan: 推荐策略全市场扫描 (engine.run rows) +
                自选股补充池合并 → 固化当日计划 (data/plans/*.json)
  3. 复盘       review_plan: 拉执行日 enriched 行情逐标的对照计划
                (触发区间/开盘成交/收盘与最高收益) → 结构化复盘
                (data/reviews/*.json) + 策略表现反馈回写
                (data/evolution_review_feedback.jsonl, 供下轮进化参考)

存储 (全部在 data_dir 下, 与 TSP 惯例一致):
  plans/                     {plan_id}.json
  reviews/                   {review_id}.json
  evolution_review_feedback.jsonl   策略反馈 (append)
"""
from __future__ import annotations

import json
import logging
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

FEEDBACK_PATH_REL = "evolution_review_feedback.jsonl"


# ── 存储 ────────────────────────────────────────────────────

def _plans_dir(data_dir: Path) -> Path:
    p = data_dir / "plans"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _reviews_dir(data_dir: Path) -> Path:
    p = data_dir / "reviews"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _feedback_path(data_dir: Path) -> Path:
    return data_dir / FEEDBACK_PATH_REL


def list_plans(data_dir: Path, trade_date: str | None = None) -> list[dict]:
    plans = []
    for f in sorted(_plans_dir(data_dir).glob("*.json")):
        try:
            plans.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            logger.warning("读取计划 %s 失败: %s", f.name, e)
    plans.sort(key=lambda p: (p.get("trade_date", ""), p.get("plan_id", "")), reverse=True)
    if trade_date:
        plans = [p for p in plans if p.get("trade_date") == trade_date]
    return plans


def get_plan(data_dir: Path, plan_id: str) -> dict | None:
    f = _plans_dir(data_dir) / f"{plan_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("读取计划 %s 失败: %s", plan_id, e)
        return None


def list_reviews(data_dir: Path, trade_date: str | None = None) -> list[dict]:
    reviews = []
    for f in sorted(_reviews_dir(data_dir).glob("*.json")):
        try:
            reviews.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            logger.warning("读取复盘 %s 失败: %s", f.name, e)
    reviews.sort(key=lambda r: (r.get("trade_date", ""), r.get("review_id", "")), reverse=True)
    if trade_date:
        reviews = [r for r in reviews if r.get("trade_date") == trade_date]
    return reviews


def get_review(data_dir: Path, review_id: str) -> dict | None:
    f = _reviews_dir(data_dir) / f"{review_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("读取复盘 %s 失败: %s", review_id, e)
        return None


def _next_id(prefix: str, trade_date: str, data_dir: Path, ext: str) -> str:
    import itertools

    for i in itertools.count(1):
        candidate = f"{prefix}{trade_date.replace('-', '')}-{i:03d}"
        f = _plans_dir(data_dir) / f"{candidate}.json" if ext == "plan" else _reviews_dir(data_dir) / f"{candidate}.json"
        if not f.exists():
            return candidate


# ── 盘前计划生成 ───────────────────────────────────────────

def _latest_regime(data_dir: Path) -> dict:
    try:
        from app.services.regime_builder import load_regime_history
        df = load_regime_history(data_dir)
        if df.is_empty() or "date" not in df.columns:
            return {}
        row = df.sort("date", descending=True).head(1).to_dicts()[0]
        row["date"] = str(row.get("date"))
        return row
    except Exception as e:  # noqa: BLE001
        logger.warning("regime 读取失败: %s", e)
        return {}


def _watchlist_symbols(data_dir: Path) -> list[str]:
    p = data_dir / "user_data" / "watchlist.parquet"
    if not p.exists():
        return []
    try:
        df = pl.read_parquet(p)
        if "symbol" not in df.columns:
            return []
        return [s for s in df["symbol"].drop_nulls().cast(pl.Utf8).to_list() if s]
    except Exception as e:  # noqa: BLE001
        logger.warning("自选读取失败: %s", e)
        return []


def _resolve_strategy_params(data_dir: Path) -> list[dict]:
    """盘前扫描策略来源: 已应用进化参数优先, 其次最新推荐, 兜底引擎默认。"""
    from app.backtest.evolution import get_applied_overrides, load_recommendations

    applied = get_applied_overrides(data_dir)
    if applied:
        return [
            {"strategy_id": sid, "params": dict(rec.get("params") or {}),
             "name": rec.get("name") or "", "source": "applied"}
            for sid, rec in applied.items()
        ]

    records = load_recommendations(data_dir, limit=5)
    for rec in records:
        recommendation = rec.get("recommendation") or {}
        strategy_params = recommendation.get("strategy_params") or {}
        if strategy_params:
            return [{
                "strategy_id": rec.get("strategy_id", ""),
                "params": dict(strategy_params),
                "name": recommendation.get("name") or "",
                "source": f"recommended@{rec.get('run_id', '')[:8]}",
            }]
    return []


def generate_plan(
    data_dir: Path,
    *,
    trade_date: str | None = None,
    max_entries: int = 20,
    max_per_strategy: int = 8,
    engine=None,  # noqa: ANN001
    repo=None,  # noqa: ANN001
    use_evolution: bool = True,
    tp_pct: float = 0.05,
    stop_pct: float = 0.03,
) -> dict:
    """盘前计划: 进化推荐策略全市场扫描 + 自选股补充池 → 固化计划。

    trade_date: 计划交易日 (YYYY-MM-DD); None 时取数据最新日。
    """
    from app.services.screener import ScreenerService
    from app.strategy.engine import StrategyResult

    if repo is None:
        from app.tickflow.repository import DataStore, KlineRepository
        repo = KlineRepository(DataStore(data_dir=data_dir))
    svc = ScreenerService(repo, asset_type="stock")
    as_of = trade_date or svc.latest_date()
    if not as_of:
        return {"ok": False, "error": "无可用行情数据日期, 请先同步/运行盘后管道"}

    engine = engine or StrategyEngine()
    regime = _latest_regime(data_dir)

    strategy_sources: list[dict] = []
    if use_evolution:
        strategy_sources = _resolve_strategy_params(data_dir)
    if not strategy_sources:
        fallback = [
            {"strategy_id": meta["id"], "params": {}, "name": meta.get("name", ""), "source": "default"}
            for meta in engine.list_strategies()
            if not meta.get("research_only") and "stock" in meta.get("asset_types", ["stock"])
            and "1d" in meta.get("timeframes", ["1d"])
        ]
        strategy_sources = fallback[:2]

    entries: list[dict] = []
    seen: set[str] = set()

    for src in strategy_sources[:4]:
        sid = src["strategy_id"]
        try:
            context = svc.build_strategy_context(
                engine, as_of, [sid], timeframe="1d",
                params_map={sid: src["params"]},
                overrides_map={sid: {}},
            )
            result = engine.run(sid, context, params=src["params"] or None)
        except Exception as e:  # noqa: BLE001
            logger.warning("计划扫描策略 %s 失败: %s", sid, e)
            continue
        entry_hits = {str(h["symbol"]).zfill(6): h.get("signals") or [] for h in getattr(result, "entry_signal_hits", [])}
        exit_hits = {str(h["symbol"]).zfill(6): h.get("signals") or [] for h in getattr(result, "exit_signal_hits", [])}
        signals = _strategy_signal_defs(engine, sid)
        rows = _ranked_rows(result)
        for row in rows[:max_per_strategy]:
            symbol = str(row.get("symbol") or "").split(".")[0].zfill(6)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            entries.append(_make_entry(symbol, sid, row, src, as_of, source="evolved",
                                       entry_hits=entry_hits.get(symbol) or [],
                                       exit_hits=exit_hits.get(symbol) or [],
                                       signals=signals,
                                       tp_pct=tp_pct, stop_pct=stop_pct))

    # 自选股补充池: 用第一个可用策略对自选跑单池信号
    watch_symbols = [s for s in _watchlist_symbols(data_dir) if s not in seen]
    if watch_symbols and strategy_sources:
        base = strategy_sources[0]
        try:
            context = svc.build_strategy_context(
                engine, as_of, [base["strategy_id"]], timeframe="1d",
                params_map={base["strategy_id"]: base["params"]},
                overrides_map={base["strategy_id"]: {}},
            )
            result = engine.run(base["strategy_id"], context, pool=watch_symbols,
                                params=base["params"] or None)
            entry_hits = {str(h["symbol"]).zfill(6): h.get("signals") or [] for h in getattr(result, "entry_signal_hits", [])}
            exit_hits = {str(h["symbol"]).zfill(6): h.get("signals") or [] for h in getattr(result, "exit_signal_hits", [])}
            signals = _strategy_signal_defs(engine, base["strategy_id"])
            for row in _ranked_rows(result):
                symbol = str(row.get("symbol") or "").split(".")[0].zfill(6)
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                entries.append(_make_entry(symbol, base["strategy_id"], row, base, as_of,
                                           source="watchlist",
                                           entry_hits=entry_hits.get(symbol) or [],
                                           exit_hits=exit_hits.get(symbol) or [],
                                           signals=signals,
                                           tp_pct=tp_pct, stop_pct=stop_pct))
        except Exception as e:  # noqa: BLE001
            logger.warning("自选补充扫描失败: %s", e)

    if not entries:
        return {"ok": False, "error": "本次没有选出任何标的 (策略无信号或数据缺失)"}

    entries = entries[:max_entries]
    plan_id = _next_id("P", str(as_of), data_dir, "plan")
    plan = {
        "plan_id": plan_id,
        "trade_date": str(as_of),
        "regime": regime,
        "source": {
            "strategies": [
                {"strategy_id": s["strategy_id"], "name": s.get("name", ""), "source": s.get("source", "")}
                for s in strategy_sources[:4]
            ],
        },
        "entries": entries,
        "status": "planned",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = _plans_dir(data_dir) / f"{plan_id}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, **plan}


def _ranked_rows(result) -> list[dict]:
    """按 score 降序取策略结果明细行。"""
    rows = getattr(result, "rows", None) or []
    if not rows:
        return []
    scored = [r for r in rows if r.get("symbol")]
    scored.sort(key=lambda r: (r.get("score") if isinstance(r.get("score"), (int, float)) else -1e9),
                reverse=True)
    return scored


def _make_entry(symbol: str, strategy_id: str, row: dict, src: dict, as_of: date_cls,
                source: str, *, entry_hits: list[str] | None = None,
                exit_hits: list[str] | None = None, signals: dict | None = None,
                tp_pct: float = 0.05, stop_pct: float = 0.03) -> dict:
    ref = _last_close(as_of, src, row)
    entry_low = round(ref * 0.995, 3) if ref else None
    entry_high = round(ref * 1.005, 3) if ref else None
    stop = round(ref * (1 - stop_pct), 3) if ref else None
    take_profit = round(ref * (1 + tp_pct), 3) if ref else None
    return {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "strategy_name": src.get("name", strategy_id),
        "score": round(float(row.get("score") or 0), 2),
        "source": source,
        "signal": _signal_text(row),
        "entry_signal": (entry_hits[0] if entry_hits else ((signals or {}).get("entry") or "")) or "无",
        "exit_signal": (exit_hits[0] if exit_hits else ((signals or {}).get("exit") or "")) or "无",
        "reference_price": ref,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "take_profit": take_profit,
        "stop_loss": stop,
        "position_pct": 0,  # 盘前等权示意, 实盘按资金分配
    }


def _strategy_signal_defs(engine, strategy_id: str) -> dict:
    """策略声明的加入/退出信号名 (entry_signals[0] / exit_signals[0] 可读名)。"""
    try:
        s = engine.get(strategy_id)
        return {
            "entry": (s.entry_signals or [""])[0],
            "exit": (s.exit_signals or [""])[0],
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("读取策略 %s 信号定义失败: %s", strategy_id, e)
        return {"entry": "", "exit": ""}


def _last_close(as_of: date_cls, src: dict, row: dict) -> float | None:
    val = row.get("close") or row.get("price") or row.get("last")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _signal_text(row: dict) -> str:
    for key in ("signal", "signal_id", "entry_signal", "reason"):
        if row.get(key):
            return str(row[key])
    return ""


# ── 复盘 ────────────────────────────────────────────────────

def _list_trade_dates(data_dir: Path, start: str, track_days: int) -> list[str]:
    """从 enriched 分区列交易日, 从 start 起取连续 track_days 个。"""
    day_dir = data_dir / "kline_daily_enriched"
    if not day_dir.exists():
        return []
    dates = sorted(d.name.split("=", 1)[1] for d in day_dir.glob("date=*"))
    dates = [d for d in dates if d >= start]
    return dates[:track_days]


def _load_enriched_day(data_dir: Path, trade_date: str) -> pl.DataFrame:
    """读执行日 enriched 行情 (date= 分区 parquet)。"""
    day_dir = data_dir / "kline_daily_enriched" / f"date={trade_date}"
    parts = sorted(day_dir.glob("*.parquet")) if day_dir.exists() else []
    frames = []
    for part in parts:
        try:
            frames.append(pl.read_parquet(part))
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 enriched 分区 %s 失败: %s", part, e)
    if not frames:
        return pl.DataFrame()
    df = pl.concat(frames)
    if "symbol" in df.columns:
        df = df.with_columns(pl.col("symbol").cast(pl.Utf8))
    return df


def review_plan(
    data_dir: Path,
    plan_id: str,
    *,
    trade_date: str | None = None,
    track_days: int = 1,
    buy_slack_pct: float = 0.005,
) -> dict:
    """程序化复盘: 对照计划逐标的算触发/收益/胜率, 回写策略反馈。

    track_days: 持有期跟踪天数 (交易日, 含计划执行日)。
      =1 单日口径: 执行日触发即按 止盈>止损>收盘 当日退出 (历史兼容);
      >1 持有期口径: 加入后逐日跟踪到 止盈/止损 触及或到期(max_hold)退出,
        输出 exit_date/hold_days, 完整还原"从加入到退出"的收益。
    """
    plan = get_plan(data_dir, plan_id)
    if plan is None:
        return {"ok": False, "error": f"计划 {plan_id} 不存在"}
    if plan.get("status") == "reviewed":
        prior = next((r for r in list_reviews(data_dir, None) if r.get("plan_id") == plan_id), None)
        if prior:
            return {"ok": True, "already_reviewed": True, **prior}

    as_of = trade_date or str(plan.get("trade_date") or "")
    if not as_of:
        return {"ok": False, "error": "缺少复盘日期"}
    track_days = max(1, int(track_days or 1))
    dates = _list_trade_dates(data_dir, as_of, track_days)
    if not dates:
        return {"ok": False, "error": f"{as_of} 起无 enriched 行情, 无法复盘"}

    day_maps: list[dict] = []
    for d in dates:
        df = _load_enriched_day(data_dir, d)
        day_maps.append({str(r["symbol"]).zfill(6): r for r in df.to_dicts()})

    results: list[dict] = []
    for entry in plan.get("entries", []):
        results.append(_track_entry(entry, day_maps, dates, buy_slack_pct))

    stats = _aggregate(results)
    strategy_feedback = _strategy_feedback(results)
    review_id = _next_id("R", as_of, data_dir, "review")
    review = {
        "review_id": review_id,
        "plan_id": plan_id,
        "trade_date": as_of,
        "summary": stats,
        "results": results,
        "strategy_feedback": strategy_feedback,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = _reviews_dir(data_dir) / f"{review_id}.json"
    out.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    # 更新计划状态
    plan["status"] = "reviewed"
    plan["review_id"] = review_id
    (_plans_dir(data_dir) / f"{plan_id}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    # 策略反馈回写 (供下轮进化参考)
    _append_feedback(data_dir, as_of, strategy_feedback)
    return {"ok": True, **review}


def _track_entry(entry: dict, day_maps: list[dict], dates: list[str], slack: float) -> dict:
    """逐标的完整交易: 执行日触发建仓 → 逐日跟踪到 止盈/止损/到期 退出。"""
    symbol = str(entry.get("symbol") or "").zfill(6)
    base = {
        "symbol": symbol,
        "strategy_id": entry.get("strategy_id", ""),
        "strategy_name": entry.get("strategy_name", ""),
        "source": entry.get("source", ""),
        "score": entry.get("score"),
        "reference_price": entry.get("reference_price"),
        "entry_signal": entry.get("entry_signal", ""),
        "exit_signal": entry.get("exit_signal", ""),
        "entry_low": entry.get("entry_low"),
        "entry_high": entry.get("entry_high"),
        "take_profit": entry.get("take_profit"),
        "stop_loss": entry.get("stop_loss"),
    }
    first = day_maps[0].get(symbol) if day_maps else None
    if first is None:
        return {**base, "hit": None, "fill_price": None, "exit_price": None,
                "exit_date": None, "exit_reason": None, "hold_days": None,
                "pnl_pct": None, "best_pnl_pct": None, "note": "无行情"}

    o, h, l, c = (float(first.get(k)) for k in ("open", "high", "low", "close"))
    entry_low = entry.get("entry_low")
    entry_high = entry.get("entry_high")
    triggered = (entry_low is not None and entry_high is not None
                 and l <= entry_high and h >= entry_low)
    if not triggered:
        note = "未触发(区间外)" if entry_low is not None else "无参考区间"
        return {**base, "open": o, "high": h, "low": l, "close": c,
                "hit": False, "fill_price": None, "exit_price": None,
                "exit_date": None, "exit_reason": None, "hold_days": None,
                "pnl_pct": None, "best_pnl_pct": None, "note": note}

    fill = o
    tp = entry.get("take_profit")
    sl = entry.get("stop_loss")
    exit_price: float | None = None
    reason = "close"
    exit_date = dates[0]
    # 执行日判定: 止盈/止损优先, 否则进入持有期跟踪
    if tp is not None and h >= tp:
        exit_price, reason = tp, "take_profit"
    elif sl is not None and l <= sl:
        exit_price, reason = sl, "stop_loss"
    elif len(dates) == 1:
        exit_price, reason = c, "close"
    else:
        for i in range(1, len(dates)):
            row_i = day_maps[i].get(symbol)
            if row_i is None:
                continue  # 停牌/无行情: 顺延跟踪
            hi, lo, cl = (float(row_i.get(k)) for k in ("high", "low", "close"))
            if tp is not None and hi >= tp:
                exit_price, reason, exit_date = tp, "take_profit", dates[i]
                break
            if sl is not None and lo <= sl:
                exit_price, reason, exit_date = sl, "stop_loss", dates[i]
                break
            if i == len(dates) - 1:
                exit_price, reason, exit_date = cl, "max_hold", dates[i]
        if exit_price is None:  # 全部后续日期停牌
            exit_price, reason = c, "max_hold"
    pnl = (exit_price / fill - 1.0) if fill else None
    best = (h / fill - 1.0) if fill else None
    hold_days = dates.index(exit_date) if exit_date else 0
    return {**base, "open": o, "high": h, "low": l, "close": c,
            "hit": True, "fill_price": fill, "exit_price": exit_price,
            "exit_date": exit_date, "exit_reason": reason, "hold_days": hold_days,
            "pnl_pct": pnl, "best_pnl_pct": best, "note": f"触发·{reason}"}


def _result_row(entry: dict, row: dict | None, slack: float) -> dict:
    base = {
        "symbol": str(entry.get("symbol") or "").zfill(6),
        "strategy_id": entry.get("strategy_id", ""),
        "strategy_name": entry.get("strategy_name", ""),
        "source": entry.get("source", ""),
        "score": entry.get("score"),
        "reference_price": entry.get("reference_price"),
        "entry_signal": entry.get("entry_signal", ""),
        "exit_signal": entry.get("exit_signal", ""),
        "entry_low": entry.get("entry_low"),
        "entry_high": entry.get("entry_high"),
        "take_profit": entry.get("take_profit"),
        "stop_loss": entry.get("stop_loss"),
    }
    if row is None:
        return {**base, "hit": None, "fill_price": None, "exit_price": None,
                "exit_reason": None, "pnl_pct": None, "best_pnl_pct": None,
                "note": "无行情"}

    o, h, l, c = (float(row.get(k)) for k in ("open", "high", "low", "close"))
    entry_low = entry.get("entry_low")
    entry_high = entry.get("entry_high")
    triggered = (entry_low is not None and entry_high is not None
                 and l <= entry_high and h >= entry_low)
    if not triggered:
        note = "未触发(区间外)" if entry_low is not None else "无参考区间"
        return {**base, "open": o, "high": h, "low": l, "close": c,
                "hit": False, "fill_price": None, "exit_price": None,
                "exit_reason": None, "pnl_pct": None, "best_pnl_pct": None, "note": note}

    # 加入→退出完整交易: 开盘价建仓; 日线维度按 止盈>止损>收盘 优先级模拟退出
    fill = o
    tp = entry.get("take_profit")
    sl = entry.get("stop_loss")
    if tp is not None and h >= tp:
        exit_price, reason = tp, "take_profit"
    elif sl is not None and l <= sl:
        exit_price, reason = sl, "stop_loss"
    else:
        exit_price, reason = c, "close"
    pnl = (exit_price / fill - 1.0) if fill else None
    best = (h / fill - 1.0) if fill else None
    return {**base, "open": o, "high": h, "low": l, "close": c,
            "hit": True, "fill_price": fill, "exit_price": exit_price,
            "exit_reason": reason, "pnl_pct": pnl,
            "best_pnl_pct": best, "note": f"触发·{reason}"}


def _aggregate(results: list[dict]) -> dict:
    with_data = [r for r in results if r.get("fill_price") is not None]
    hits = [r for r in with_data if r.get("hit")]
    wins = [r for r in hits if (r.get("pnl_pct") or 0) > 0]
    pnls = [r.get("pnl_pct") for r in with_data if r.get("pnl_pct") is not None]
    reasons = {}
    holds = []
    for r in hits:
        reasons[r.get("exit_reason") or "close"] = reasons.get(r.get("exit_reason") or "close", 0) + 1
        if r.get("hold_days") is not None:
            holds.append(r["hold_days"])
    return {
        "planned": len(results),
        "with_data": len(with_data),
        "triggered": len(hits),
        "untriggered": len(with_data) - len(hits),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(hits), 4) if hits else 0.0,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        "avg_best_pnl_pct": round(
            sum(r.get("best_pnl_pct") or 0 for r in hits) / len(hits), 4) if hits else 0.0,
        "exits": reasons,
        "avg_hold_days": round(sum(holds) / len(holds), 2) if holds else 0.0,
        "best_symbol": max((r for r in with_data if r.get("pnl_pct") is not None),
                           key=lambda r: r["pnl_pct"], default=None)["symbol"] if with_data else None,
        "worst_symbol": min((r for r in with_data if r.get("pnl_pct") is not None),
                            key=lambda r: r["pnl_pct"], default=None)["symbol"] if with_data else None,
    }


def _strategy_feedback(results: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for r in results:
        sid = r.get("strategy_id") or "unknown"
        g = groups.setdefault(sid, {"planned": 0, "triggered": 0, "wins": 0, "pnls": []})
        g["planned"] += 1
        if r.get("fill_price") is not None:
            g["triggered"] += 1
            if (r.get("pnl_pct") or 0) > 0:
                g["wins"] += 1
            if r.get("pnl_pct") is not None:
                g["pnls"].append(r["pnl_pct"])
    out = []
    for sid, g in groups.items():
        hit_pnls = g["pnls"]
        out.append({
            "strategy_id": sid,
            "planned": g["planned"],
            "triggered": g["triggered"],
            "win_rate": round(g["wins"] / g["triggered"], 4) if g["triggered"] else 0.0,
            "avg_pnl_pct": round(sum(hit_pnls) / len(hit_pnls), 4) if hit_pnls else 0.0,
        })
    out.sort(key=lambda x: x["avg_pnl_pct"], reverse=True)
    return out


def _append_feedback(data_dir: Path, trade_date: str, strategy_feedback: list[dict]) -> None:
    path = _feedback_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for item in strategy_feedback:
            record = {"trade_date": trade_date, **item,
                      "recorded_at": datetime.now().isoformat(timespec="seconds")}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_feedback(data_dir: Path, limit: int = 50) -> list[dict]:
    path = _feedback_path(data_dir)
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    except Exception as e:  # noqa: BLE001
        logger.warning("读取策略反馈失败: %s", e)
    return list(reversed(records))[:limit]


# ── 闭环总览 ───────────────────────────────────────────────

def workflow_overview(data_dir: Path) -> dict:
    from app.backtest.evolution import get_applied_overrides, load_recommendations
    plans = list_plans(data_dir, None)
    reviews = list_reviews(data_dir, None)
    latest_plan = plans[0] if plans else None
    latest_review = reviews[0] if reviews else None
    applied = get_applied_overrides(data_dir)
    return {
        "latest_plan": {
            "plan_id": latest_plan.get("plan_id"),
            "trade_date": latest_plan.get("trade_date"),
            "status": latest_plan.get("status"),
            "entries": len(latest_plan.get("entries", [])),
        } if latest_plan else None,
        "latest_review": {
            "review_id": latest_review.get("review_id"),
            "plan_id": latest_review.get("plan_id"),
            "trade_date": latest_review.get("trade_date"),
            "summary": latest_review.get("summary"),
        } if latest_review else None,
        "applied_evolution": [{"strategy_id": sid} for sid in applied.keys()],
        "recommendations_count": len(load_recommendations(data_dir, limit=100)),
        "feedback_count": len(load_feedback(data_dir, limit=1000)),
    }
