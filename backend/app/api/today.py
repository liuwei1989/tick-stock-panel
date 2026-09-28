"""今日操作台聚合 API。

把看板、市场环境、自选和告警收敛成一个只读执行入口。这里不新增业务存储，
所有数据都来自现有 repository 和服务，避免操作台与各页面出现两套口径。
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import polars as pl
from fastapi import APIRouter, Request

from app.market_time import cn_now, cn_today
from app.services import alert_store, watchlist
from app.services.market_mainline import load_mainline_history
from app.services.market_phase import PHASE_LABELS
from app.services.regime_builder import STATE_LABELS, load_regime_history

router = APIRouter(prefix="/api/today", tags=["today"])


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_safe(value: Any) -> Any:
    """将 Parquet 中可能出现的特殊浮点值转换为可传输的 JSON 值。"""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _watchlist_snapshot(request: Request, symbols: list[dict]) -> list[dict]:
    """从最新 enriched 快照批量读取自选状态，不逐只请求行情。"""
    if not symbols:
        return []
    repo = request.app.state.repo
    try:
        enriched, _ = repo.get_enriched_latest()
    except Exception:  # noqa: BLE001
        enriched = pl.DataFrame()
    by_symbol = {
        str(row.get("symbol")): row
        for row in (enriched.to_dicts() if not enriched.is_empty() else [])
    }
    try:
        name_map = repo.get_name_map([str(entry.get("symbol") or "") for entry in symbols])
    except Exception:  # noqa: BLE001
        name_map = {}
    rows: list[dict] = []
    for entry in symbols[:20]:
        symbol = str(entry.get("symbol") or "")
        quote = by_symbol.get(symbol, {})
        change = _finite(quote.get("change_pct"))
        if change is not None and change <= -0.03:
            status, priority = "关注回撤", "risk"
        elif change is not None and change >= 0.03:
            status, priority = "强势跟踪", "focus"
        else:
            status, priority = "继续观察", "normal"
        rows.append({
            "symbol": symbol,
            "name": quote.get("name") or entry.get("name") or name_map.get(symbol),
            "close": _finite(quote.get("close")),
            "change_pct": change,
            "status": status,
            "priority": priority,
            "group_ids": entry.get("group_ids") or [],
        })
    return rows


def _latest_regime(request: Request) -> dict | None:
    df = load_regime_history(request.app.state.repo.store.data_dir)
    if df.is_empty() or "date" not in df.columns:
        return None
    row = df.sort("date", descending=True).head(1).to_dicts()[0]
    row["date"] = str(row.get("date")) if row.get("date") is not None else None
    if row.get("state"):
        row["state_label"] = STATE_LABELS.get(row["state"], row["state"])
    if row.get("phase"):
        row["phase_label"] = PHASE_LABELS.get(row["phase"], row["phase"])
    return row


def _latest_mainline(request: Request) -> list[dict]:
    df = load_mainline_history(request.app.state.repo.store.data_dir, "concept")
    if df.is_empty() or "date" not in df.columns:
        return []
    latest = df.get_column("date").max()
    if latest is None:
        return []
    return _json_safe(df.filter(pl.col("date") == latest).sort("rank").head(5).to_dicts())


def _next_action(regime: dict | None, watch_rows: list[dict], alerts: list[dict], as_of: date | None) -> dict:
    if as_of is None:
        return {"key": "sync_data", "label": "先运行盘后管道", "reason": "尚未形成可用的日线快照。", "tone": "warning"}
    if any(item.get("priority") == "risk" for item in watch_rows) or any(
        item.get("severity") in {"critical", "warn"} for item in alerts[:5]
    ):
        return {"key": "review_risk", "label": "先处理风险与异动", "reason": "自选或监控中存在需要优先复核的信号。", "tone": "danger"}
    phase = regime.get("phase") if regime else None
    state = regime.get("state") if regime else None
    if phase in {"rally", "climax", "ignite"} or state == "strong":
        return {"key": "review_mainline", "label": "先核对主线与强势自选", "reason": "市场处于偏强阶段，优先确认主线是否延续。", "tone": "accent"}
    if phase in {"ebb", "ice"} or state in {"weak", "lean_weak"}:
        return {"key": "reduce_risk", "label": "控制仓位，等待确认", "reason": "市场环境偏弱，先观察风险释放和修复信号。", "tone": "warning"}
    return {"key": "review_watchlist", "label": "查看自选与最新告警", "reason": "暂无强制动作，按自选和信号逐项复核。", "tone": "neutral"}


def build_today_actions(request: Request) -> dict:
    repo = request.app.state.repo
    as_of = repo.latest_enriched_date("stock")
    entries = watchlist.list_symbols()
    watch_rows = _watchlist_snapshot(request, entries)
    alerts = alert_store.list_recent(repo.store.data_dir, days=1, limit=20)
    regime = _latest_regime(request)
    mainline = _latest_mainline(request)
    return _json_safe({
        "trade_date": str(as_of or cn_today()),
        "as_of": str(as_of) if as_of else None,
        "generated_at": cn_now().isoformat(),
        "next_action": _next_action(regime, watch_rows, alerts, as_of),
        "market": {
            "state": regime.get("state") if regime else None,
            "state_label": regime.get("state_label") if regime else None,
            "phase": regime.get("phase") if regime else None,
            "phase_label": regime.get("phase_label") if regime else None,
            "score": _finite(regime.get("score")) if regime else None,
            "max_consecutive": regime.get("max_consecutive") if regime else None,
            "mainline": mainline,
        },
        "watchlist": {"count": len(entries), "rows": watch_rows},
        "risks": alerts[:8],
        "readiness": {
            "has_data": as_of is not None,
            "has_regime": regime is not None,
            "watchlist_count": len(entries),
            "alert_count": len(alerts),
        },
    })


@router.get("/actions")
def today_actions(request: Request) -> dict:
    """返回今日执行摘要；失败的单项历史数据会降级为空，不阻断看板。"""
    return build_today_actions(request)
