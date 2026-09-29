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
import re
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path

import polars as pl

from app.services import workflow as wf
from app.services.market_mainline import load_mainline_history
from app.services.regime_builder import load_regime_history

logger = logging.getLogger(__name__)

_TOP_MAINLINE = 8          # 主线认证候选数
_MIN_AVG_SCORE_GOLD = 60.0  # 金牌主升的 5 日均强度门槛
_MIN_STREAK_GOLD = 3        # 金牌主升需连续上榜天数
_MIN_STREAK_UP = 2          # 主升需连续上榜天数

# 提醒对应的可执行处理动作 (前端按 key 派发到既有端点; 后端只声明"能做什么")
ACTION_PIPELINE = "pipeline"                # 运行盘后管道 (日线→富化→环境→主线→zzshare 实时)
ACTION_REBUILD_ENRICHED = "rebuild_enriched"  # 重建富化行情
ACTION_SYNC_MINUTE = "sync_minute"          # 同步分钟行情
ACTION_REGIME = "regime_recompute"          # 重建环境分
ACTION_MAINLINE = "mainline_recompute"      # 重算主线时序
ACTION_GENERATE_PLAN = "generate_plan"      # 生成盘前计划
ACTION_REVIEW_PLAN = "review_plan"          # 运行复盘
ACTION_EVOLUTION = "evolution_run"          # 运行进化扫描


# ───────────────────────── 数据健康 ─────────────────────────

_DATE_IN_NAME = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")


def _date_from_name(name: str) -> date | None:
    """从文件名里抠日期: P20260928-001.json / 20260928.json / date=2026-09-28。"""
    m = _DATE_IN_NAME.search(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parquet_max_date(file: Path, column: str) -> date | None:
    """平铺单文件表的真实数据日期 (取该列 max)。读失败 (并发写/缺列) 返回 None。"""
    try:
        value = pl.scan_parquet(file).select(pl.col(column).max()).collect().item()
    except Exception:  # 读不到就退化为 mtime, 不让健康度整体报错
        return None
    return _parse_date(value)


def _partition_stats(path: Path, date_column: str | None = None) -> dict:
    """某数据目录的「最新数据日期」, 覆盖三种落盘形态:

    1. `date=YYYY-MM-DD` 分区目录 (kline_daily / kline_minute …) → 取最大分区名
    2. 文件名内嵌日期 (plans/P20260928-001.json、reviews/R*.json、topic_rank/20260928.json)
    3. 平铺单文件表 (regime_history / mainline_history 的 part.parquet):
       传 date_column 时读该列 max 得真实数据日期, 否则退化为文件 mtime

    只按目录分区名判断会把形态 2/3 全判成「缺失」, 因此这里统一解析出真实日期,
    并在 freshness_basis 里标明依据 (filename / column:date / mtime) 便于排查。
    """
    if not path.exists():
        return {"exists": False, "partitions": 0, "latest": None}
    dates = sorted(d.name.split("=", 1)[1] for d in path.glob("date=*"))
    if dates:
        return {"exists": True, "partitions": len(dates), "latest": dates[-1]}
    files = [f for f in path.iterdir() if f.is_file() and not f.name.endswith(".bak")]
    named = [d for d in (_date_from_name(f.name) for f in files) if d]
    if named:
        return {"exists": True, "partitions": len(files),
                "latest": max(named).isoformat(), "freshness_basis": "filename"}
    flat = path / "part.parquet"
    if flat.exists():
        real = _parquet_max_date(flat, date_column) if date_column else None
        if real is not None:
            return {"exists": True, "partitions": 1, "latest": real.isoformat(),
                    "freshness_basis": f"column:{date_column}"}
        if files:
            mtime = max(f.stat().st_mtime for f in files)
            return {"exists": True, "partitions": len(files),
                    "latest": datetime.fromtimestamp(mtime).date().isoformat(),
                    "freshness_basis": "mtime"}
    return {"exists": True, "partitions": 0, "latest": None}


# ───────────────────────── 新鲜度判据 ─────────────────────────
# 各数据层"应有多新"取决于粒度:
#  - realtime: 盘中/实时数据 (分钟行情, zzshare 实时模块) 必须在最新交易日当天
#  - daily:    日频数据 (日线/富化/环境/主线/计划/复盘) 容忍 T+1 (上一交易日)
# 周末/节假日用周几近似兜底 (与 trading_day 探针的 fallback 一致), 不在驾驶舱触发网络探测。

def _is_trading_day_heuristic(d: date) -> bool:
    return d.weekday() < 5


def _prev_trading_day(d: date) -> date:
    cur = d
    while True:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            return cur


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _freshness(latest, as_of: date, granularity: str) -> dict:
    """返回 {"status": "fresh"|"stale"|"missing", "lag_days": int|None}。

    - realtime: 最新必须为当天; 交易日却不是当天 → stale; 周末容忍到上一交易日
    - daily:    最新不早于上一交易日即 fresh (容忍 T+1 / 周末)
    """
    latest_d = _parse_date(latest)
    if latest_d is None:
        return {"status": "missing", "lag_days": None}
    if latest_d > as_of:  # 未来日期 (时钟漂移) 视为新鲜而非滞后
        return {"status": "fresh", "lag_days": 0}
    lag = (as_of - latest_d).days
    if granularity == "realtime":
        if latest_d == as_of:
            return {"status": "fresh", "lag_days": 0}
        if _is_trading_day_heuristic(as_of):
            return {"status": "stale", "lag_days": lag}
        prev = _prev_trading_day(as_of)
        return {"status": "fresh" if latest_d >= prev else "stale", "lag_days": lag}
    prev = _prev_trading_day(as_of)
    if latest_d >= prev:
        return {"status": "fresh", "lag_days": lag}
    return {"status": "stale", "lag_days": lag}


# zzshare 实时模块: 数据来自实时接口, 不落内部 parquet 表, 以 *_summary().available 判定健康
_ZZSHARE_LAYER_KEYS: dict[str, str] = {
    "topic_rank": "topics",
    "uplimit": "uplimit",
    "lhb": "lhb",
    "sentiment": "sentiment",
    "ths_hot": "hot",
    "ai_reports": "ai_reports",
    "movement_alerts": "movement",
}

# 各层粒度: realtime 必须当天, daily 容忍 T+1
_LAYER_GRANULARITY: dict[str, str] = {
    "kline_daily": "daily",
    "kline_daily_enriched": "daily",
    "kline_minute": "realtime",
    "regime_history": "daily",
    "mainline_history": "daily",
    "plans": "daily",
    "reviews": "daily",
    "topic_rank": "realtime",
    "uplimit": "realtime",
    "lhb": "realtime",
    "sentiment": "realtime",
    "ths_hot": "realtime",
    "ai_reports": "realtime",
    "movement_alerts": "realtime",
}


def data_health(data_dir: Path, zz_summary: dict | None = None,
                as_of: date | None = None) -> dict:
    """链路各层健康: 日线 → 富化 → 环境 → 主线 → 计划 → 复盘 + zzshare 实时模块。

    zz_summary: 已计算的 zzshare 扩展摘要 (cockpit_overview 传入复用); 缺省时自算。
    as_of:      新鲜度基准日 (默认今天); 各层据此算 fresh/stale/missing。
    """
    if zz_summary is None:
        zz_summary = _zzshare_summary(data_dir)
    as_of = as_of or date.today()
    base = data_dir
    layers: list[dict] = []
    specs = [
        ("kline_daily", "日线行情", base / "kline_daily", None),
        ("kline_daily_enriched", "富化行情", base / "kline_daily_enriched", None),
        ("kline_minute", "分钟行情", base / "kline_minute", None),
        ("regime_history", "环境分", base / "regime_history", "date"),
        ("mainline_history", "主线时序", base / "mainline_history", "date"),
        ("plans", "盘前计划", base / "plans", None),
        ("reviews", "复盘记录", base / "reviews", None),
        ("topic_rank", "题材热度", base / "topic_rank", None),
        ("uplimit", "涨停复盘", base / "uplimit", None),
        ("lhb", "龙虎榜", base / "lhb", None),
        ("sentiment", "情绪K线", base / "sentiment", None),
        ("ths_hot", "人气热搜", base / "ths_hot", None),
        ("ai_reports", "AI研报", base / "ai_reports", None),
        ("movement_alerts", "监管预警", base / "movement_alerts", None),
    ]
    for key, label, path, date_column in specs:
        gran = _LAYER_GRANULARITY.get(key, "daily")
        if key in _ZZSHARE_LAYER_KEYS:
            sub = zz_summary.get(_ZZSHARE_LAYER_KEYS[key]) or {}
            avail = bool(sub.get("available"))
            st = {
                "exists": avail,
                "partitions": 1 if avail else 0,
                "latest": sub.get("date"),
                "source": "zzshare",
            }
        elif path.is_dir():
            st = _partition_stats(path, date_column=date_column)
        else:
            st = {"exists": path.exists(), "partitions": 1 if path.exists() else 0,
                  "latest": None}
        st.update({"key": key, "label": label})
        st["freshness"] = _freshness(st.get("latest"), as_of, gran)
        layers.append(st)

    # 富化是否落后日线
    daily = next((lyr for lyr in layers if lyr["key"] == "kline_daily"), {})
    rich = next((lyr for lyr in layers if lyr["key"] == "kline_daily_enriched"), {})
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
    layers = {lyr["key"]: lyr for lyr in health["layers"]}

    daily = layers.get("kline_daily", {})
    rich = layers.get("kline_daily_enriched", {})
    if not daily.get("partitions"):
        out.append({"level": "error", "title": "日线数据为空",
                    "detail": "未同步任何日线行情, 需用 zzshare 数据源执行日线同步",
                    "action": ACTION_PIPELINE, "action_label": "运行盘后管道"})
    if not rich.get("partitions"):
        out.append({"level": "error", "title": "富化行情未生成",
                    "detail": "计划生成/复盘/主线认证均依赖 enriched, 需先构建富化",
                    "action": ACTION_REBUILD_ENRICHED, "action_label": "重建富化"})
    elif health.get("enriched_behind_daily"):
        out.append({"level": "warn", "title": "富化落后日线",
                    "detail": f"富化最新 {rich['latest']} < 日线最新 {daily['latest']}, 复盘会缺尾部交易日",
                    "action": ACTION_REBUILD_ENRICHED, "action_label": "重建富化"})
    if not layers.get("kline_minute", {}).get("partitions"):
        out.append({"level": "info", "title": "无分钟行情",
                    "detail": "分钟级数据未同步 (zzshare 支持), 盘中监控与竞价分析暂不可用",
                    "action": ACTION_SYNC_MINUTE, "action_label": "同步分钟行情"})
    if not regime.get("available"):
        out.append({"level": "warn", "title": "市场环境未生成",
                    "detail": regime.get("detail", "regime 历史为空, 环境分/门控建议不可用"),
                    "action": ACTION_REGIME, "action_label": "重建环境分"})
    if not mainline.get("available"):
        out.append({"level": "warn", "title": "主线认证不可用",
                    "detail": mainline.get("detail", "主线时序为空"),
                    "action": ACTION_MAINLINE, "action_label": "重算主线"})
    else:
        if mainline["gold_count"] == 0:
            out.append({"level": "info", "title": "暂无金牌主线",
                        "detail": "当前无满足持续性门槛(连续3日+均分60)的主线题材, 注意追高分化风险"})

    ov = wf_overview or {}
    latest_plan = ov.get("latest_plan")
    if latest_plan:
        if latest_plan.get("status") != "reviewed":
            out.append({"level": "warn", "title": f"计划 {latest_plan.get('plan_id')} 待复盘",
                        "detail": f"{latest_plan.get('trade_date')} · {latest_plan.get('entries')} 个标的, 执行后请运行复盘",
                        "action": ACTION_REVIEW_PLAN, "action_label": "运行复盘",
                        "action_payload": {"plan_id": latest_plan.get("plan_id")}})
    else:
        out.append({"level": "info", "title": "今日尚无计划",
                    "detail": "可在工作流页生成盘前计划 (进化推荐扫描 + 自选)",
                    "action": ACTION_GENERATE_PLAN, "action_label": "生成盘前计划"})
    if not (ov.get("applied_evolution") or []):
        out.append({"level": "info", "title": "未应用进化参数",
                    "detail": "可运行 walk-forward 扫描并应用推荐",
                    "action": ACTION_EVOLUTION, "action_label": "去进化页处理"})

    # 数据新鲜度: 滞后 (stale) 的各层聚合为提醒, 区分内部链路与实时源
    stale_internal, stale_rt = [], []
    for lyr in health.get("layers", []):
        if lyr.get("freshness", {}).get("status") == "stale":
            (stale_rt if lyr.get("source") == "zzshare" else stale_internal).append(lyr["label"])
    if stale_internal:
        out.append({"level": "warn", "title": "数据链路滞后",
                    "detail": "以下层级最新日期落后: " + "、".join(stale_internal)
                              + " (详见数据链路卡片)",
                    "action": ACTION_PIPELINE, "action_label": "运行盘后管道"})
    if stale_rt:
        out.append({"level": "warn", "title": "实时数据源滞后",
                    "detail": "zzshare 实时模块落后: " + "、".join(stale_rt),
                    "action": ACTION_PIPELINE, "action_label": "运行盘后管道"})
    return out


def cockpit_overview(data_dir: Path) -> dict:
    """驾驶舱总览: 环境 + 数据健康 + 主线认证 + 工作流 + 提醒 + zzshare 扩展 + 节点时间线。"""
    as_of = date.today()
    zz = _zzshare_summary(data_dir)
    health = data_health(data_dir, zz_summary=zz, as_of=as_of)
    mainline = mainline_certification(data_dir)
    regime = _regime_summary(data_dir)
    wf_overview = _safe_wf_overview(data_dir)
    alerts = _alerts(health, mainline, regime, wf_overview)
    session = cockpit_session(health=health)
    return {
        "as_of": as_of.isoformat(),
        "health": health,
        "mainline": mainline,
        "regime": regime,
        "workflow": wf_overview,
        "zzshare": zz,
        "session": session,
        "alerts": alerts,
        "status": ("ok" if not any(a["level"] == "error" for a in alerts)
                   else "attention"),
    }


# ───────────────────────── 交易节点时间线 ─────────────────────────

# 节点窗口 (北京时间): 盘前 → 竞价 → 早盘 → 午间 → 午盘 → 复盘
_SESSION_NODES: list[tuple[str, str, dt_time, dt_time]] = [
    ("premarket", "盘前", dt_time(0, 0), dt_time(9, 15)),
    ("auction", "竞价", dt_time(9, 15), dt_time(9, 30)),
    ("morning", "早盘", dt_time(9, 30), dt_time(11, 30)),
    ("midday", "午间", dt_time(11, 30), dt_time(13, 0)),
    ("afternoon", "午盘", dt_time(13, 0), dt_time(15, 0)),
    ("review", "复盘", dt_time(15, 0), dt_time(17, 0)),
]


def cockpit_session(health: dict | None = None, now: datetime | None = None) -> dict:
    """交易节点时间线: 按北京时间把一天切成 6 个节点, 标 done/active/pending;
    并交叉引用今日数据存在性 (盘前计划/盘中分钟/复盘记录) 作为 data_ready 提示。

    轻量节点模型: 状态由当前时间推导, 不新增存储; 数据存在性复用 health 各层 latest。
    """
    from app.market_time import cn_now

    now = now or cn_now()
    t = now.time()
    today_str = now.date().isoformat()

    # 今日数据存在性 (由 health 各层 latest 判定)
    plan_today = review_today = intraday_fresh = None
    if health is not None:
        by_key = {lyr["key"]: lyr for lyr in health.get("layers", [])}
        p = by_key.get("plans", {})
        plan_today = (p.get("latest") == today_str)
        r = by_key.get("reviews", {})
        review_today = (r.get("latest") == today_str)
        m = by_key.get("kline_minute", {})
        intraday_fresh = (m.get("freshness", {}).get("status") == "fresh")
    data_ready = {
        "premarket": plan_today,
        "morning": intraday_fresh,
        "afternoon": intraday_fresh,
        "review": review_today,
    }

    nodes: list[dict] = []
    current = None
    for key, label, start, end in _SESSION_NODES:
        if t < start:
            state = "pending"
        elif t < end:
            state = "active"
            current = label
        else:
            state = "done"
        nodes.append({
            "key": key,
            "label": label,
            "window": f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}",
            "state": state,
            "data_ready": data_ready.get(key),
        })
    if not _is_trading_day_heuristic(now.date()):
        # 非交易日: 全天不标进行中/待开始, 当前语境为休市
        for n in nodes:
            n["state"] = "done"
        current = "休市"
    elif current is None:
        # 不在任何 active 窗口 (如盘后 17:00 后): 用最后一个已结束节点作为当前语境
        current = nodes[-1]["label"]
    return {
        "as_of_time": now.strftime("%Y-%m-%d %H:%M"),
        "trading_day": _is_trading_day_heuristic(now.date()),
        "current": current,
        "nodes": nodes,
    }


def _zzshare_summary(data_dir: Path) -> dict:
    """zzshare 扩展数据摘要 (题材/涨停/龙虎榜/情绪), 数据缺失时降级 available=false。"""
    from app.services import zzshare_extra
    return {
        "topics": zzshare_extra.topics_summary(data_dir),
        "uplimit": zzshare_extra.uplimit_summary(data_dir),
        "lhb": zzshare_extra.lhb_summary(data_dir),
        "sentiment": zzshare_extra.sentiment_summary(data_dir),
        "hot": zzshare_extra.hot_summary(data_dir),
        "ai_reports": zzshare_extra.ai_reports_summary(data_dir),
        "movement": zzshare_extra.movement_summary(data_dir),
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
    except Exception as e:
        logger.warning("cockpit regime read failed: %s", e)
        return {"available": False, "detail": f"regime 读取失败: {e}"}


def _safe_wf_overview(data_dir: Path) -> dict:
    try:
        return wf.workflow_overview(data_dir) or {}
    except Exception as e:
        logger.warning("cockpit workflow overview failed: %s", e)
        return {}
