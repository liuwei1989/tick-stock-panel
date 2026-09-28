"""驾驶舱 (Cockpit) 聚合服务 — 把数据链路/市场环境/主线/工作流状态浓缩为一屏。

核心目标: "一眼就能发现各种情况"。
- 数据健康: 链路各层 (日线→富化→环境→主线→计划→复盘) 分区数与最新日期, 缺口自动标红
- 主线认证: 基于主线时序 (mainline history) 的持续性认证 (金牌主升/主升/脉冲/轮动) 与龙头身位股
- 环境: regime 最新 (环境分/state/phase) + 门控建议
- 工作流: 计划/复盘/进化 状态
- 提醒: 上述各层缺口聚合为 alerts (info/warn/error), 驾驶舱顶部状态灯
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl

from app.services.market_mainline import load_mainline_history
from app.services.regime_builder import load_regime_history
from app.services import workflow as wf

logger = logging.getLogger(__name__)

_TOP_MAINLINE = 8          # 主线认证候选数
_MIN_AVG_SCORE_GOLD = 60.0  # 金牌主升的 5 日均强度门槛
_MIN_STREAK_GOLD = 3        # 金牌主升需连续上榜天数
_MIN_STREAK_UP = 2          # 主升需连续上榜天数


# ───────────────────────── 数据健康 ─────────────────────────

def _partition_stats(path: Path) -> dict:
    """某目录下的 date= 分区统计; 平铺 part.parquet (regime/mainline) 记为 1 份。"""
    if not path.exists():
        return {"exists": False, "partitions": 0, "latest": None}
    dates = sorted(d.name.split("=", 1)[1] for d in path.glob("date=*"))
    if dates:
        return {"exists": True, "partitions": len(dates), "latest": dates[-1]}
    if (path / "part.parquet").exists():
        return {"exists": True, "partitions": 1, "latest": "part.parquet"}
    return {"exists": True, "partitions": 0, "latest": None}


def data_health(data_dir: Path) -> dict:
    """链路各层健康: 日线 → 富化 → 环境 → 主线 → 计划 → 复盘。"""
    base = data_dir
    layers: list[dict] = []
    specs = [
        ("kline_daily", "日线行情", base / "kline_daily"),
        ("kline_daily_enriched", "富化行情", base / "kline_daily_enriched"),
        ("kline_minute", "分钟行情", base / "kline_minute"),
        ("regime_history", "环境分", base / "regime_history"),
        ("mainline_history", "主线时序", base / "mainline_history"),
        ("plans", "盘前计划", base / "plans"),
        ("reviews", "复盘记录", base / "reviews"),
        ("topic_rank", "题材热度", base / "topic_rank"),
        ("uplimit", "涨停复盘", base / "uplimit"),
        ("lhb", "龙虎榜", base / "lhb"),
        ("sentiment", "情绪K线", base / "sentiment"),
    ]
    for key, label, path in specs:
        if path.is_dir():
            st = _partition_stats(path)
        else:
            st = {"exists": path.exists(), "partitions": 1 if path.exists() else 0,
                  "latest": None}
        st.update({"key": key, "label": label})
        layers.append(st)

    # 富化是否落后日线
    daily = next((l for l in layers if l["key"] == "kline_daily"), {})
    rich = next((l for l in layers if l["key"] == "kline_daily_enriched"), {})
    behind = bool(rich.get("partitions") and daily.get("latest") and
                  rich.get("latest") and rich["latest"] < daily["latest"])
    return {"layers": layers, "enriched_behind_daily": behind}


# ───────────────────────── 主线认证 ─────────────────────────

def mainline_certification(data_dir: Path, top: int = _TOP_MAINLINE) -> dict:
    """题材持续性认证: 连续上榜天数 + 5日均强度 → 金牌主升/主升/脉冲/轮动 + 龙头身位股。"""
    df = load_mainline_history(data_dir, "concept")
    if df.is_empty():
        return {"available": False, "detail": "主线时序未生成 (依赖富化行情, 需先同步日线并构建 enriched)"}
    df = df.sort(["date", "rank"])
    days = sorted(set(df["date"].to_list()))
    recent_days = days[-8:]
    window = df.filter(pl.col("date").is_in(recent_days))
    if window.is_empty():
        return {"available": False, "detail": "主线时序无近 8 日数据"}

    items: list[dict] = []
    by_member: dict[str, list[dict]] = {}
    for row in window.to_dicts():
        by_member.setdefault(str(row["member"]), []).append(row)
    for member, rows in by_member.items():
        rows.sort(key=lambda r: r["date"])
        scores = [float(r["score"]) for r in rows]
        ranks = [int(r["rank"]) for r in rows]
        dates = [str(r["date"]) for r in rows]
        # 连续上榜天数 (按日期顺序, 允许中间缺一天)
        streak = 1
        for i in range(1, len(dates)):
            if (date.fromisoformat(dates[i]) - date.fromisoformat(dates[i - 1])).days <= 4:
                streak += 1
            else:
                streak = 1
        latest = rows[-1]
        avg5 = sum(scores[-5:]) / max(len(scores[-5:]), 1)
        level = "rotate"
        if streak >= _MIN_STREAK_GOLD and avg5 >= _MIN_AVG_SCORE_GOLD:
            level = "gold"
        elif streak >= _MIN_STREAK_UP:
            level = "up"
        elif len(scores) == 1 and scores[0] >= 50:
            level = "pulse"
        items.append({
            "member": member,
            "latest_date": latest["date"],
            "score": float(latest["score"]),
            "rank": int(latest["rank"]),
            "streak_days": streak,
            "avg5_score": round(avg5, 1),
            "limit_up_count": int(latest.get("limit_up_count") or 0),
            "max_boards": int(latest.get("max_boards") or 0),
            "leader_symbol": latest.get("leader_symbol"),
            "level": level,
        })
    items.sort(key=lambda x: (x["streak_days"], x["avg5_score"]), reverse=True)
    top_items = items[:top]

    # 龙头身位股: 取当日 rank1 题材的 leader_symbol 与连板情况
    leaders: list[dict] = []
    rank1 = next((i for i in top_items if i["rank"] == 1), top_items[0] if top_items else None)
    if rank1:
        leaders.append({
            "member": rank1["member"], "symbol": rank1["leader_symbol"],
            "level": rank1["level"], "streak_days": rank1["streak_days"],
            "score": rank1["score"], "max_boards": rank1["max_boards"],
        })

    gold_count = sum(1 for i in top_items if i["level"] == "gold")
    return {"available": True, "as_of": recent_days[-1], "items": top_items,
            "leaders": leaders, "gold_count": gold_count}


# ───────────────────────── 聚合与提醒 ─────────────────────────

def _alerts(health: dict, mainline: dict, regime: dict, wf_overview: dict) -> list[dict]:
    out: list[dict] = []
    layers = {l["key"]: l for l in health["layers"]}

    daily = layers.get("kline_daily", {})
    rich = layers.get("kline_daily_enriched", {})
    if not daily.get("partitions"):
        out.append({"level": "error", "title": "日线数据为空",
                    "detail": "未同步任何日线行情, 需用 zzshare 数据源执行日线同步"})
    if not rich.get("partitions"):
        out.append({"level": "error", "title": "富化行情未生成",
                    "detail": "计划生成/复盘/主线认证均依赖 enriched, 需先构建富化 (可运行 enriched 重建)"})
    elif health.get("enriched_behind_daily"):
        out.append({"level": "warn", "title": "富化落后日线",
                    "detail": f"富化最新 {rich['latest']} < 日线最新 {daily['latest']}, 复盘会缺尾部交易日"})
    if not layers.get("kline_minute", {}).get("partitions"):
        out.append({"level": "info", "title": "无分钟行情",
                    "detail": "分钟级数据未同步 (zzshare 支持), 盘中监控与竞价分析暂不可用"})
    if not regime.get("available"):
        out.append({"level": "warn", "title": "市场环境未生成",
                    "detail": regime.get("detail", "regime 历史为空, 环境分/门控建议不可用")})
    if not mainline.get("available"):
        out.append({"level": "warn", "title": "主线认证不可用",
                    "detail": mainline.get("detail", "主线时序为空")})
    else:
        if mainline["gold_count"] == 0:
            out.append({"level": "info", "title": "暂无金牌主线",
                        "detail": "当前无满足持续性门槛(连续3日+均分60)的主线题材, 注意追高分化风险"})

    ov = wf_overview or {}
    latest_plan = ov.get("latest_plan")
    if latest_plan:
        if latest_plan.get("status") != "reviewed":
            out.append({"level": "warn", "title": f"计划 {latest_plan.get('plan_id')} 待复盘",
                        "detail": f"{latest_plan.get('trade_date')} · {latest_plan.get('entries')} 个标的, 执行后请运行复盘"})
    else:
        out.append({"level": "info", "title": "今日尚无计划",
                    "detail": "可在工作流页生成盘前计划 (进化推荐扫描 + 自选)"})
    if not (ov.get("applied_evolution") or []):
        out.append({"level": "info", "title": "未应用进化参数",
                    "detail": "可在进化页运行 walk-forward 并应用推荐"})
    return out


def cockpit_overview(data_dir: Path) -> dict:
    """驾驶舱总览: 环境 + 数据健康 + 主线认证 + 工作流 + 提醒 + zzshare 扩展。"""
    health = data_health(data_dir)
    mainline = mainline_certification(data_dir)
    regime = _regime_summary(data_dir)
    wf_overview = _safe_wf_overview(data_dir)
    zz = _zzshare_summary(data_dir)
    alerts = _alerts(health, mainline, regime, wf_overview)
    return {
        "as_of": date.today().isoformat(),
        "health": health,
        "mainline": mainline,
        "regime": regime,
        "workflow": wf_overview,
        "zzshare": zz,
        "alerts": alerts,
        "status": ("ok" if not any(a["level"] == "error" for a in alerts)
                   else "attention"),
    }


def _zzshare_summary(data_dir: Path) -> dict:
    """zzshare 扩展数据摘要 (题材/涨停/龙虎榜/情绪), 数据缺失时降级 available=false。"""
    from app.services import zzshare_extra
    return {
        "topics": zzshare_extra.topics_summary(data_dir),
        "uplimit": zzshare_extra.uplimit_summary(data_dir),
        "lhb": zzshare_extra.lhb_summary(data_dir),
        "sentiment": zzshare_extra.sentiment_summary(data_dir),
    }


def _regime_summary(data_dir: Path) -> dict:
    try:
        df = load_regime_history(data_dir)
        if df.is_empty() or "score" not in df.columns:
            return {"available": False, "detail": "regime 历史为空"}
        row = df.sort("date").tail(1).to_dicts()[0]
        return {
            "available": True,
            "date": str(row.get("date", "")),
            "score": row.get("score"),
            "state": row.get("state"),
            "state_label": row.get("state_label"),
            "phase": row.get("phase"),
            "phase_label": row.get("phase_label"),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("cockpit regime read failed: %s", e)
        return {"available": False, "detail": f"regime 读取失败: {e}"}


def _safe_wf_overview(data_dir: Path) -> dict:
    try:
        return wf.workflow_overview(data_dir) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("cockpit workflow overview failed: %s", e)
        return {}
