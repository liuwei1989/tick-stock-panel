"""盘前结构化研报 — 概念 → 核心逻辑 → 催化事件 → 核心股票。

数据源 (按依赖顺序, 全部就绪才生成完整研报):
- 主线时序 (mainline_history): 题材强度/涨停数/连板高度/身位股 → 概念与核心逻辑
- regime 环境分: 盘前环境定性
- workflow 最新计划: 若已生成, 纳入"今日计划"段落
- AI (ai_provider): 可选流式增强, 输出自然语言研报正文; 未配置 AI 时降级为规则版

产出两种形态:
- structured: 机器可读 (as_of / environment / mainline_top / plan / summary)
- markdown: 人工可读研报正文 (规则版或 AI 版)

存储: data/premarket_reports/{YYYY-MM-DD}.json (每日一份, 可覆盖当天)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from app.services.cockpit import mainline_certification, _regime_summary
from app.services import workflow as wf

logger = logging.getLogger(__name__)

REPORTS_DIR = "premarket_reports"


# ───────────────────────── 规则版研报 ─────────────────────────

def _level_label(level: str) -> str:
    return {"gold": "金牌主升", "up": "主升", "pulse": "日内脉冲", "rotate": "轮动"}.get(level, level)


def build_structured(data_dir: Path, as_of: date | None = None) -> dict:
    """规则版结构化研报: 环境 + 主线 Top + 今日计划 + 摘要。"""
    as_of = as_of or date.today()
    mainline = mainline_certification(data_dir)
    regime = _regime_summary(data_dir)
    ov = {}
    try:
        ov = wf.workflow_overview(data_dir) or {}
    except Exception:  # noqa: BLE001
        pass

    top = mainline.get("items", [])[:5] if mainline.get("available") else []
    leaders = mainline.get("leaders", []) if mainline.get("available") else []

    environment = "数据未生成"
    if regime.get("available"):
        environment = (f"环境分 {regime.get('score')} ({regime.get('state_label')} · "
                       f"{regime.get('phase_label')})")
    elif mainline.get("available"):
        environment = "环境分未生成, 以主线强度为参考"

    mainline_top = [
        {
            "member": i["member"],
            "level": i["level"],
            "level_label": _level_label(i["level"]),
            "score": i["score"],
            "streak_days": i["streak_days"],
            "limit_up_count": i["limit_up_count"],
            "max_boards": i["max_boards"],
            "leader_symbol": i["leader_symbol"],
        }
        for i in top
    ]

    # 催化事件: 规则版以"连板结构 + 身位股"给出可证伪的催化锚点
    catalysts: list[dict] = []
    for i in mainline_top:
        if i["level"] in ("gold", "up") and i["max_boards"] and i["max_boards"] >= 2:
            catalysts.append({
                "member": i["member"],
                "event": f"{i['member']} 出现 {i['max_boards']} 连板身位股, 持续{i['streak_days']}日居前",
                "watch": i["leader_symbol"],
            })

    plan = None
    lp = ov.get("latest_plan")
    if lp:
        plan = {
            "plan_id": lp.get("plan_id"),
            "trade_date": lp.get("trade_date"),
            "status": lp.get("status"),
            "entries": lp.get("entries"),
        }

    summary = _build_summary(environment, mainline_top, leaders, plan)

    return {
        "as_of": as_of.isoformat(),
        "environment": environment,
        "mainline_top": mainline_top,
        "leaders": leaders,
        "catalysts": catalysts[:6],
        "plan": plan,
        "summary": summary,
        "ai": False,
        "available": bool(mainline_top or plan),
    }


def _build_summary(environment: str, top: list[dict], leaders: list[dict], plan: dict | None) -> str:
    if not top:
        return "主线时序未生成, 暂无法给出盘前结构化研报 (需先同步日线并构建 enriched)。"
    lines = [f"盘前要点: {environment}。"]
    for i in top[:3]:
        lines.append(
            f"· {i['member']} ({i['level_label']}, 强度{i['score']:.0f}, 连{i['streak_days']}日居前, "
            f"涨停{i['limit_up_count']}家, 最高{i['max_boards']}板) — 身位股 {i['leader_symbol']}"
        )
    if plan:
        lines.append(f"· 今日计划 {plan['plan_id']} 已生成 ({plan['entries']} 标的), 状态 {plan['status']}。")
    return "\n".join(lines)


def build_markdown(data_dir: Path, as_of: date | None = None) -> str:
    """规则版研报正文 (无 AI 时的降级输出)。"""
    s = build_structured(data_dir, as_of)
    lines = [f"# 盘前结构化研报 ({s['as_of']})", "", f"**环境**: {s['environment']}", ""]
    if s["mainline_top"]:
        lines.append("## 主线认证")
        for i in s["mainline_top"]:
            lines.append(
                f"- **{i['member']}** [{i['level_label']}] 强度 {i['score']:.0f} · "
                f"连{i['streak_days']}日居前 · 涨停 {i['limit_up_count']} · 最高 {i['max_boards']} 板 · "
                f"身位股 {i['leader_symbol']}"
            )
        lines.append("")
    if s["catalysts"]:
        lines.append("## 催化事件 (连板结构锚点)")
        for c in s["catalysts"]:
            lines.append(f"- {c['event']} — 观察 {c['watch']}")
        lines.append("")
    if s["plan"]:
        lines.append(f"## 今日计划\n- {s['plan']['plan_id']} ({s['plan']['trade_date']}) "
                     f"{s['plan']['entries']} 个标的, 状态 {s['plan']['status']}")
        lines.append("")
    lines.append("## 核心股票")
    seen: set[str] = set()
    for i in s["mainline_top"]:
        sym = i["leader_symbol"]
        if sym and sym not in seen:
            seen.add(sym)
            lines.append(f"- {sym} — {i['member']} 身位股 ({i['level_label']})")
    if not s["available"]:
        lines.append(s["summary"])
    return "\n".join(lines)


# ───────────────────────── 存储 ─────────────────────────

def reports_dir(data_dir: Path) -> Path:
    p = data_dir / REPORTS_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_reports(data_dir: Path) -> list[dict]:
    """历史研报列表 (降序, 后 60 份)。"""
    out: list[dict] = []
    if not (data_dir / REPORTS_DIR).exists():
        return out
    for p in sorted((data_dir / REPORTS_DIR).glob("*.json"), reverse=True)[:60]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def save_report(data_dir: Path, payload: dict) -> dict:
    p = reports_dir(data_dir) / f"{payload['as_of']}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


# ───────────────────────── AI 流式研报 ─────────────────────────

def build_ai_prompt(data_dir: Path, as_of: date | None = None, focus: str = "",
                    news: list[dict] | None = None) -> str:
    """盘前研报 AI 提示词 (基于规则版素材)。"""
    s = build_structured(data_dir, as_of)
    if not s["available"]:
        return ""
    prompt = (
        f"你是A股短线交易研究助手。今天是 {s['as_of']} 盘前。请基于以下结构化素材, "
        "输出一份盘前研报, 严格按五段结构:"
        "\n1. 市场环境 (一句话定性, 基于环境分与主线状态)"
        "\n2. 核心主线 (Top 题材: 认证级别/强度/连板结构/身位股)"
        "\n3. 催化事件 (可从连板结构推出的潜在催化锚点, 标注为'待验证')"
        "\n4. 下一交易日板块方向 (给出偏强观察/中性轮动/偏弱规避的证据判断、优先观察板块及开盘后验证条件;不作确定性预测)"
        "\n5. 核心股票 (身位股 + 连板梯队中的关键标的, 附风险提示)"
        "\n语言简洁、可执行, 不编造素材之外的数据;新闻催化与量价/板块结构相冲突时明确指出。\n\n"
        f"素材:\n{json.dumps({'environment': s['environment'], 'mainline_top': s['mainline_top'], 'catalysts': s['catalysts'], 'plan': s['plan'], 'news_and_stock_catalysts': (news or [])[:10]}, ensure_ascii=False, indent=1)}"
    )
    if focus:
        prompt += f"\n\n用户特别关注: {focus}"
    return prompt
