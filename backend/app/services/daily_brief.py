"""每日行动摘要: 复用 Agent 事实, 输出驾驶舱与推送共用的快照。"""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.market_time import CN_TZ, cn_now
from app.services.cockpit import cockpit_overview
from app.services.portfolio_risk import portfolio_risk
from app.services.research import ResearchLedger

logger = logging.getLogger(__name__)
_FILENAME = "daily_action_brief.json"
_PUSH_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daily-brief-push")


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str
    action: str = Field(default="watch", max_length=20)
    reason: str = Field(default="", max_length=500)
    condition: str = Field(default="", max_length=300)
    risk: str = Field(default="", max_length=300)


class DailyBrief(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = 1
    as_of: str
    generated_at: str
    source: str = "rules"
    sentiment: str = "未知"
    sentiment_score: float | None = None
    sentiment_reason: str = ""
    buy: list[ActionItem] = Field(default_factory=list, max_length=10)
    sell: list[ActionItem] = Field(default_factory=list, max_length=10)
    disclaimer: str = "仅供研究参考,不会自动下单。"


def _path(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _FILENAME


def load(data_dir: Path, as_of: date | None = None) -> dict | None:
    path = _path(data_dir)
    if not path.exists():
        return None
    try:
        brief = DailyBrief.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("daily action brief is invalid")
        return None
    if as_of is not None and brief.as_of != as_of.isoformat():
        return None
    return brief.model_dump()


def _save(data_dir: Path, brief: DailyBrief) -> dict:
    path = _path(data_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(brief.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return brief.model_dump()


def _facts(repo, data_dir: Path, as_of: date) -> dict:
    overview = cockpit_overview(data_dir)
    try:
        risk = portfolio_risk(repo)
    except Exception as exc:  # portfolio is optional and must not block the brief
        logger.warning("portfolio facts unavailable: %s", exc)
        risk = {"accounts": []}
    signals = ResearchLedger(data_dir).list_signals()
    signal_facts = []
    for signal in signals[:30]:
        artifact = signal.get("artifact") or {}
        thesis = artifact.get("thesis") or {}
        signal_facts.append({
            "symbol": signal.get("symbol"),
            "status": signal.get("status"),
            "direction": thesis.get("direction"),
            "score": thesis.get("score"),
            "summary": thesis.get("summary", "")[:300],
            "risks": (thesis.get("risks") or [])[:5],
        })
    return {
        "as_of": as_of.isoformat(),
        "market": {
            "regime": overview.get("regime"),
            "mainline": overview.get("mainline"),
            "alerts": overview.get("alerts", [])[:8],
        },
        "portfolio": risk,
        "research": signal_facts,
    }


def _rules(facts: dict, as_of: date) -> DailyBrief:
    regime = facts["market"].get("regime") or {}
    score = regime.get("score") if regime.get("available") else None
    if isinstance(score, (int, float)):
        sentiment = "偏强" if score >= 65 else "中性" if score >= 45 else "偏弱"
        reason = f"市场环境分 {score:.0f}, 阶段为 {regime.get('phase_label') or regime.get('phase') or '未知'}。"
    else:
        sentiment, reason = "未知", "市场环境数据尚未完整生成。"
    buy, sell = [], []
    mainline = facts["market"].get("mainline") or {}
    if sentiment == "偏强":
        for item in (mainline.get("items") or [])[:3]:
            symbol = item.get("leader_symbol")
            if symbol:
                buy.append(ActionItem(symbol=symbol, action="watch", reason=f"主线 {item.get('member', '')} 强度居前", condition="开盘后量价与主线强度继续确认", risk="主线退潮或数据滞后"))
    for account in facts["portfolio"].get("accounts", []):
        for alert in account.get("alerts", []):
            symbol = alert.get("symbol")
            if symbol:
                sell.append(ActionItem(symbol=symbol, action="review", reason=alert.get("message", "持仓风险提醒"), condition="核对实时价格、成本和个人风险承受能力", risk="行情或持仓价格缺失"))
    return DailyBrief(as_of=as_of.isoformat(), generated_at=cn_now().isoformat(), sentiment=sentiment, sentiment_score=score, sentiment_reason=reason, buy=buy, sell=sell)


def _parse_agent(raw: str, facts: dict, as_of: date) -> DailyBrief | None:
    text = raw.strip()
    if "```json" in text:
        text = text.rsplit("```json", 1)[1].split("```", 1)[0].strip()
    try:
        data = json.loads(text)
        data["as_of"] = as_of.isoformat()
        data["generated_at"] = cn_now().isoformat()
        data["source"] = "agent"
        return DailyBrief.model_validate(data)
    except Exception:
        return None


async def generate(repo, data_dir: Path, *, as_of: date | None = None, force: bool = False) -> dict:
    as_of = as_of or cn_now().date()
    if not force:
        cached = load(data_dir, as_of)
        if cached:
            return cached
    facts = await asyncio.to_thread(_facts, repo, data_dir, as_of)
    brief = _rules(facts, as_of)
    try:
        from app.services.ai_provider import ai_configured, generate_ai_text

        if ai_configured():
            prompt = (
                "你是面向金融小白的A股每日行动助手。只依据给定事实,输出严格JSON,不要Markdown。"
                "告诉用户今天关注怎么买、已有持仓怎么卖/继续持有、市场情绪。不会自动下单。"
                "buy 数组每项字段 symbol/action/reason/condition/risk, action 只能 buy/watch; "
                "sell 数组 action 只能 sell/hold/review; 无证据就空数组。不得编造价格、新闻或持仓。"
                "sentiment 使用 偏强/中性/偏弱/未知, sentiment_score 可为空。"
                "JSON字段: sentiment,sentiment_score,sentiment_reason,buy,sell。\n事实:\n"
                + json.dumps(facts, ensure_ascii=False, default=str)
            )
            raw = await generate_ai_text([{"role": "user", "content": prompt}], max_tokens=1800, timeout=120)
            parsed = _parse_agent(raw, facts, as_of)
            if parsed:
                brief = parsed
    except Exception as exc:
        logger.warning("daily action Agent unavailable, use rules: %s", exc)
    return _save(data_dir, brief)


def format_push(brief: dict) -> str:
    lines = [f"市场情绪: {brief.get('sentiment', '未知')} ({brief.get('sentiment_reason', '')})", "", "买入/关注:"]
    lines.extend(f"- {x.get('symbol')}: {x.get('action')}, {x.get('reason')}; 条件: {x.get('condition')}" for x in brief.get("buy", []))
    if not brief.get("buy"):
        lines.append("- 暂无有证据支持的买入标的")
    lines.append("\n卖出/持有:")
    lines.extend(f"- {x.get('symbol')}: {x.get('action')}, {x.get('reason')}" for x in brief.get("sell", []))
    if not brief.get("sell"):
        lines.append("- 暂无持仓卖出提醒")
    lines.append("\n仅供研究参考,不会自动下单。")
    return "\n".join(lines)


def push(brief: dict) -> int:
    """Push the persisted brief through the user's already selected channels.

    Reuses the review push channel list: a non-empty selection is the opt-in.
    ``review_push_mode`` only governs the market recap archive path, so it is
    deliberately not consulted here.
    """
    from app.services import preferences

    channels = preferences.get_review_push_channels()
    if not channels:
        return 0
    body = format_push(brief)
    title = "每日行动摘要"
    sent = 0
    for channel in channels:
        if channel in {"feishu", "wecom", "custom", "email"}:
            try:
                from app import secrets_store
                from app.services import email_adapter, webhook_adapter

                if channel == "feishu":
                    url = preferences.get_feishu_webhook_url()
                    if url and webhook_adapter.send_feishu_card(url, title, brief["as_of"], body, preferences.get_feishu_webhook_secret()):
                        sent += 1
                elif channel == "wecom":
                    url = preferences.get_wecom_webhook_url()
                    if url and webhook_adapter.send_wecom_markdown(url, title, body):
                        sent += 1
                elif channel == "custom":
                    url = preferences.get_custom_webhook_url()
                    if url and webhook_adapter.send_custom(url, title, body, "daily_action_brief", brief, secrets_store.get_custom_webhook_secret()):
                        sent += 1
                else:
                    config = preferences.get_email_smtp_config()
                    if email_adapter.is_configured(config) and email_adapter.send_email(config, secrets_store.get_email_smtp_password(), title, body):
                        sent += 1
            except Exception:  # notification failures never fail research
                logger.warning("daily brief push failed for %s", channel)
        else:
            try:
                from app.services.notification_channels import CHANNELS, dispatch
                from app.services.quote_service import _WEBHOOK_EXECUTOR

                if channel in CHANNELS:
                    sent += int(dispatch(_WEBHOOK_EXECUTOR, channel, title, body, event_key=f"daily_action_brief:{brief['as_of']}"))
            except Exception:
                logger.warning("daily brief push failed for %s", channel)
    return sent


async def scheduled_tick(repo, *, now: datetime | None = None) -> dict:
    """Generate the post-close brief once per trading day.

    The scheduler calls this frequently so a restart can catch up after the
    configured time.  The persisted brief makes generation idempotent and the
    ledger setting prevents duplicate external notifications.
    """
    from app.services.trading_day import is_trading_day

    now = now or cn_now()
    now = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    if (now.hour, now.minute) < (15, 40):
        return {"status": "waiting", "at": now.isoformat()}
    verdict = await asyncio.to_thread(is_trading_day, now)
    if verdict is not True:
        return {
            "status": "holiday" if verdict is False else "calendar_unknown",
            "at": now.isoformat(),
        }

    data_dir = repo.store.data_dir
    as_of = now.date()
    brief = await generate(repo, data_dir, as_of=as_of)
    ledger = ResearchLedger(data_dir)
    marker_key = f"daily_brief_pushed:{as_of.isoformat()}"
    if ledger.get_setting(marker_key):
        return {"status": "already_sent", "as_of": as_of.isoformat(), "brief": brief}

    sent = await asyncio.to_thread(push, brief)
    # Keep retrying on later scheduler ticks when no channel was configured or
    # every configured channel failed.  Dashboard generation remains cached.
    if sent > 0:
        ledger.set_setting(marker_key, {"sent": sent, "at": cn_now().isoformat()})
    return {"status": "sent" if sent else "generated", "sent": sent, "as_of": as_of.isoformat(), "brief": brief}
