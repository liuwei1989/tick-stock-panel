"""自动跟单规则域 — 监控事件触发模拟盘自动下单 (V2)。

规则匹配: 事件 source=="strategy" 且 strategy_id 相等 (跟策略), 或 rule_id 相等
(跟任意监控规则, 含信号/价格规则)。仓位: 固定金额 或 账户权益百分比。
冷却: 同规则同 symbol 最近一次自动下单后 N 个交易日内不再触发 (按订单
created_at 日期判断, 无独立状态文件 — 可由订单列表重放推导)。

规则按账户存放 (accounts/{account_id}/auto_rules/), 账户间隔离;
on_rule_events 由钩子对每个账户各调一次。

触发链路: quote_service._evaluate_monitors 产出的 rule_events →
paper_auto.on_rule_events → paper.create_order(source=f"auto:{rule_id}")。
所有下单走 paper 域的同一把锁与校验, 账户冻结/资金/涨跌停等约束自动生效。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date as _date
from datetime import datetime
from pathlib import Path

from app.market_time import cn_now
from app.services.fs_utils import atomic_write_text
from app.strategy import paper

logger = logging.getLogger(__name__)

STRATEGY_EVENT_TYPES = {"buy_signal", "sell_signal", "pool_entry", "pool_exit"}
DEFAULT_STRATEGY_EVENT_SIDES = {"buy_signal": "buy", "sell_signal": "sell"}
DEFAULT_STRATEGY_EVENTS = ["buy_signal", "sell_signal"]


def _dir(data_dir: Path, account_id: str) -> Path:
    d = paper._root(data_dir, account_id) / "auto_rules"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_iso() -> str:
    return cn_now().isoformat()


def _new_id() -> str:
    return f"arule_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"


def validate_rule(rule: dict) -> None:
    """校验规则字段, 非法抛 ValueError (中文信息)。"""
    if (rule.get("name") or "").strip() == "":
        raise ValueError("规则名称不能为空")
    if rule.get("match_kind") not in ("strategy", "rule"):
        raise ValueError(f"match_kind 非法: {rule.get('match_kind')!r} (应为 strategy / rule)")
    if not (rule.get("match_id") or "").strip():
        raise ValueError("match_id 不能为空")
    if rule.get("side") not in ("buy", "sell"):
        raise ValueError(f"side 非法: {rule.get('side')!r}")
    event_types = rule.get("event_types")
    if event_types is not None:
        if rule.get("match_kind") != "strategy":
            raise ValueError("event_types 仅适用于策略规则")
        if not isinstance(event_types, list) or not event_types:
            raise ValueError("策略规则至少选择一个触发事件")
        if set(event_types) - STRATEGY_EVENT_TYPES or len(set(event_types)) != len(event_types):
            raise ValueError("event_types 包含非法策略事件")
    event_sides = rule.get("event_sides")
    if event_sides is not None:
        if rule.get("match_kind") != "strategy" or not isinstance(event_sides, dict):
            raise ValueError("event_sides 仅适用于策略规则")
        if set(event_sides) - STRATEGY_EVENT_TYPES or (
            event_types is not None and set(event_sides) != set(event_types)
        ) or any(
            side not in ("buy", "sell") for side in event_sides.values()
        ):
            raise ValueError("策略事件方向配置非法")
        if not event_sides:
            raise ValueError("策略规则至少配置一个事件方向")
    if rule.get("size_mode") not in ("fixed_amount", "pct_equity"):
        raise ValueError(f"size_mode 非法: {rule.get('size_mode')!r}")
    value = rule.get("size_value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("size_value 必须是正数")
    if rule.get("size_mode") == "pct_equity" and value > 100:
        raise ValueError("pct_equity 的 size_value 不能超过 100 (%)")
    if rule.get("order_type") not in ("market", "next_open", "close"):
        raise ValueError(f"order_type 非法: {rule.get('order_type')!r}")
    cooldown = rule.get("cooldown_days", 0)
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
        raise ValueError("cooldown_days 必须是非负整数")


def normalize_rule(rule: dict) -> dict:
    d = dict(rule)
    d["name"] = (d.get("name") or "").strip()
    d["match_id"] = (d.get("match_id") or "").strip()
    d.setdefault("side", "buy")
    d.setdefault("size_mode", "fixed_amount")
    d.setdefault("order_type", "next_open")
    d.setdefault("cooldown_days", 5)
    d.setdefault("enabled", True)
    if d.get("match_kind") == "strategy":
        if d.get("event_types") is None:
            d["event_types"] = list(DEFAULT_STRATEGY_EVENTS)
        if d.get("event_sides") is None:
            d["event_sides"] = dict(DEFAULT_STRATEGY_EVENT_SIDES)
    d.setdefault("created_at", _now_iso())
    return d


def load_auto_rules(data_dir: Path, account_id: str = paper.DEFAULT_ACCOUNT_ID, enabled_only: bool = False) -> list[dict]:
    out: list[dict] = []
    for f in sorted(_dir(data_dir, account_id).glob("arule_*.json")):
        try:
            r = normalize_rule(json.loads(f.read_text(encoding="utf-8")))
            if enabled_only and not r.get("enabled"):
                continue
            out.append(r)
        except Exception as e:
            logger.warning("paper auto rule load failed %s: %s", f.name, e)
    return out


def save_auto_rule(data_dir: Path, rule: dict, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> dict:
    validate_rule(rule)
    atomic_write_text(_dir(data_dir, account_id) / f"{rule['id']}.json", json.dumps(rule, ensure_ascii=False, indent=2))
    return rule


def create_auto_rule(data_dir: Path, rule: dict, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> dict:
    rule = normalize_rule({**rule, "id": _new_id()})
    validate_rule(rule)
    return save_auto_rule(data_dir, rule, account_id)


def delete_auto_rule(data_dir: Path, rule_id: str, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> bool:
    p = _dir(data_dir, account_id) / f"{rule_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False


def set_enabled(data_dir: Path, rule_id: str, enabled: bool, account_id: str = paper.DEFAULT_ACCOUNT_ID) -> dict | None:
    p = _dir(data_dir, account_id) / f"{rule_id}.json"
    if not p.exists():
        return None
    rule = normalize_rule(json.loads(p.read_text(encoding="utf-8")))
    rule["enabled"] = enabled
    return save_auto_rule(data_dir, rule, account_id)


def _matches(rule: dict, ev: dict) -> bool:
    if rule["match_kind"] == "strategy":
        event_type = ev.get("type")
        return (
            ev.get("source") == "strategy"
            and ev.get("strategy_id") == rule["match_id"]
            # Historical callers may send strategy events without a type. Keep their
            # configured static side behavior; the monitor engine always supplies type.
            and (not event_type or not rule.get("event_types") or event_type in rule["event_types"])
            and (not event_type or not rule.get("event_sides") or event_type in rule["event_sides"])
        )
    return ev.get("rule_id") == rule["match_id"]


def _expand_event(ev: dict) -> list[dict]:
    """展开策略监控的批量事件,保留每个标的的价格后才能逐笔模拟下单。"""
    items = ev.get("items")
    if ev.get("source") != "strategy" or not isinstance(items, list):
        return [ev]
    return [
        {**ev, **item, "type": ev.get("type"), "source": "strategy",
         "strategy_id": ev.get("strategy_id"), "rule_id": ev.get("rule_id")}
        for item in items
        if isinstance(item, dict)
    ]


def strategy_monitor_requirements(data_dir: Path) -> dict[str, set[str]]:
    """返回启用的模拟盘策略规则需要监听的事件类型。"""
    required: dict[str, set[str]] = {}
    for account_id in paper.list_account_ids(data_dir):
        for rule in load_auto_rules(data_dir, account_id, enabled_only=True):
            if rule.get("match_kind") != "strategy":
                continue
            sid = str(rule.get("match_id", ""))
            events = rule.get("event_types") or STRATEGY_EVENT_TYPES
            required.setdefault(sid, set()).update(events)
    return required


def sync_strategy_monitors(
    data_dir: Path,
    strategy_engine,
    monitor_engine,
    *,
    preference_enabled: bool,
    preference_ids: list[str],
) -> None:
    """把普通策略监控与启用的模拟盘跟单策略合并到监控引擎。"""
    from app.strategy import monitor_rules as mr_store

    auto_requirements = strategy_monitor_requirements(data_dir)
    requested_ids = set(preference_ids if preference_enabled else []) | set(auto_requirements)
    strategies = strategy_engine.list_strategies()
    known_ids = {str(s.get("id", "")) for s in strategies}
    requested_ids &= known_ids
    names = {str(s.get("id", "")): str(s.get("name") or s.get("id", "")) for s in strategies}
    mr_store.migrate_strategy_monitors(data_dir, sorted(requested_ids), names)

    for sid, event_types in auto_requirements.items():
        if sid not in requested_ids:
            continue
        rule = mr_store.load_one(data_dir, mr_store.strategy_rule_id(sid))
        if rule is None:
            continue
        base_events = set(rule.get("_paper_auto_notify_events_base") or rule.get("notify_events") or [])
        rule["_paper_auto_notify_events_base"] = sorted(base_events)
        rule["notify_events"] = sorted(base_events | event_types)
        mr_store.save_one(data_dir, rule)
    for rule in mr_store.load_all(data_dir):
        sid = str(rule.get("strategy_id") or "")
        if (rule.get("type") == "strategy" and sid not in auto_requirements
                and "_paper_auto_notify_events_base" in rule):
            rule["notify_events"] = rule.pop("_paper_auto_notify_events_base")
            mr_store.save_one(data_dir, rule)
    if monitor_engine is not None:
        monitor_engine.set_rules(mr_store.load_all(data_dir))


def _in_cooldown(data_dir: Path, rule: dict, symbol: str, cooldown_days: int, account_id: str) -> bool:
    """同规则同 symbol 最近一次自动下单是否仍在冷却期 (按日历日, 含当日)。"""
    if cooldown_days <= 0:
        return False
    prefix = f"auto:{rule['id']}"
    today = cn_now().date()
    for order in paper.load_orders(data_dir, account_id):
        if order.get("source") != prefix or order.get("symbol") != symbol:
            continue
        try:
            created = _date.fromisoformat(order["created_at"][:10])
        except (KeyError, ValueError):
            continue
        if (today - created).days < cooldown_days:
            return True
    return False


def _sizing_qty(data_dir: Path, rule: dict, ref_price: float, account_id: str) -> int:
    if ref_price <= 0:
        return 0
    if rule["size_mode"] == "fixed_amount":
        amount = float(rule["size_value"])
    else:  # pct_equity: 按账户总权益 (现金 + 最新定版持仓市值)
        acc = paper.get_account(data_dir, account_id)
        if acc is None:
            return 0
        nav_rows = paper.load_nav(data_dir, account_id)
        equity = nav_rows[-1]["nav"] if nav_rows else float(acc["cash"])
        amount = equity * float(rule["size_value"]) / 100.0
    return paper.qty_from_amount(amount, ref_price)


def on_rule_events(data_dir: Path, events: list[dict], account_id: str = paper.DEFAULT_ACCOUNT_ID) -> list[dict]:
    """监控事件 → 自动下单。返回本次创建的订单列表 (被拒订单只记日志)。

    触发条件: 事件带 symbol 与价格、有规则匹配、未冷却; 下单复用 paper.create_order
    (冻结/资金/T+1/涨跌停等校验自动生效), ref_price 用事件价格 (当前快照价)。
    """
    created: list[dict] = []
    if not events:
        return created
    rules = load_auto_rules(data_dir, account_id, enabled_only=True)
    if not rules:
        return created
    with paper.PAPER_LOCK:
        for raw_event in events:
            for ev in _expand_event(raw_event):
                symbol = (ev.get("symbol") or "").strip()
                price = ev.get("raw_price") or ev.get("price")
                if not symbol or isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
                    continue
                for rule in rules:
                    if not _matches(rule, ev):
                        continue
                    side = (rule.get("event_sides") or {}).get(ev.get("type"), rule["side"])
                    if rule["match_kind"] == "strategy" and ev.get("type") in STRATEGY_EVENT_TYPES:
                        # Pool membership is not an execution signal. Require an explicit side mapping.
                        if ev.get("type") not in (rule.get("event_sides") or {}):
                            continue
                    if _in_cooldown(data_dir, rule, symbol, int(rule.get("cooldown_days", 0)), account_id):
                        continue
                    qty = _sizing_qty(data_dir, rule, float(price), account_id)
                    if qty <= 0:
                        logger.info("paper auto %s: %s 金额不足以一手 (价 %s)", rule["name"], symbol, price)
                        continue
                    order, err = paper.create_order(
                        data_dir, symbol,
                        side,
                        account_id=account_id,
                        qty=qty,
                        order_type=rule["order_type"],
                        ref_price=float(price),
                        source=f"auto:{rule['id']}",
                    )
                    if err:
                        logger.info("paper auto %s: %s 下单被拒: %s", rule["name"], symbol, err)
                        continue
                    created.append(order)
                    logger.info("paper auto %s: %s 触发 %s %d 股 (%s)", rule["name"], symbol, side, qty, order["id"])
    return created


_ORDER_TYPE_LABEL = {"market": "即时", "next_open": "次日开盘", "close": "当日收盘"}


def auto_order_events(created: list[dict], account_id: str = paper.DEFAULT_ACCOUNT_ID) -> list[dict]:
    """自动跟单创建的订单 → 推送事件。与成交事件同构 (source=paper 对齐
    AlertEvent), 走同一推送通道; rule_id 从 order.source (auto:{rule_id}) 还原。
    """
    events: list[dict] = []
    for o in created:
        source = str(o.get("source", ""))
        rule_id = source.removeprefix("auto:") if source.startswith("auto:") else ""
        side_label = "买入" if o["side"] == "buy" else "卖出"
        type_label = _ORDER_TYPE_LABEL.get(o["order_type"], o["order_type"])
        events.append({
            "source": "paper",
            "type": "auto_order",
            "severity": "info",
            "ts": int(cn_now().timestamp() * 1000),
            "symbol": o["symbol"],
            "rule_id": rule_id,
            "side": o["side"],
            "qty": o["qty"],
            "account_id": account_id,
            "message": f"自动跟单触发{side_label}下单 ({type_label}) {o['qty']}股",
        })
    return events
