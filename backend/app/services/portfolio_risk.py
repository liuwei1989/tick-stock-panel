"""Read-only paper portfolio risk, using unadjusted prices and explicit gaps."""

from __future__ import annotations

import math

from app.market_time import cn_today
from app.services.research import ResearchLedger
from app.strategy import paper


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def evaluate_portfolio(
    account, positions, prices, nav, *, concentration=0.35, loss=0.10, drawdown=0.15
):
    held = {s: p for s, p in positions.items() if p.get("qty", 0) > 0}
    missing = [s for s in held if not _number(prices.get(s)) or prices[s] <= 0]
    cash = account.get("cash")
    available = not missing and _number(cash)
    market_value = sum(p["qty"] * prices[s] for s, p in held.items()) if available else None
    equity = cash + market_value if available else None
    holdings, alerts = [], []
    for symbol, position in held.items():
        price = prices.get(symbol) if symbol not in missing else None
        value = position["qty"] * price if price is not None else None
        cost = position.get("avg_cost")
        pnl = price / cost - 1 if price is not None and _number(cost) and cost > 0 else None
        weight = value / equity if value is not None and equity is not None and equity > 0 else None
        holdings.append(
            {"symbol": symbol, "price": price, "pnl_ratio": pnl, "weight_ratio": weight}
        )
        if weight is not None and weight >= concentration:
            alerts.append(
                {
                    "kind": "concentration",
                    "symbol": symbol,
                    "message": f"{symbol} 持仓占比 {weight:.1%}",
                }
            )
        if pnl is not None and pnl <= -loss:
            alerts.append(
                {
                    "kind": "loss",
                    "symbol": symbol,
                    "message": f"{symbol} 相对持仓成本下跌 {abs(pnl):.1%}",
                }
            )
    values = [
        row["nav"]
        for row in sorted(nav, key=lambda r: r.get("date", ""))
        if _number(row.get("nav")) and row["nav"] > 0
    ]
    current_drawdown = 1 - values[-1] / max(values) if values else None
    if current_drawdown is not None and current_drawdown >= drawdown:
        alerts.append(
            {"kind": "drawdown", "message": f"定版净值相对历史高点回撤 {current_drawdown:.1%}"}
        )
    return {
        "available": available,
        "missing_prices": missing,
        "equity": equity,
        "exposure_ratio": market_value / equity if equity and equity > 0 else None,
        "drawdown_ratio": current_drawdown,
        "holdings": holdings,
        "alerts": alerts,
        "thresholds": {
            "concentration_ratio": concentration,
            "loss_ratio": loss,
            "drawdown_ratio": drawdown,
        },
        "price_basis": "raw_close",
    }


def portfolio_risk(repo):
    data_dir = repo.store.data_dir
    # Historical repository access remains inside the existing paper domain.
    latest = repo.latest_daily_date()
    as_of = min(latest, cn_today()).isoformat() if latest else None
    signals = ResearchLedger(data_dir).list_signals()
    reports = []
    for account_id in paper.list_account_ids(data_dir):
        with paper.PAPER_LOCK:
            account = paper.get_account(data_dir, account_id)
            positions = paper.load_positions(data_dir, account_id)
            nav = paper.load_nav(data_dir, account_id)
        if account is None:
            continue
        prices = {}
        if as_of:
            for symbol, position in positions.items():
                if position.get("qty", 0) <= 0:
                    continue
                bar = paper.read_daily_bar(
                    data_dir, symbol, position.get("asset_type", "stock"), as_of
                )
                if bar is not None:
                    prices[symbol] = bar["close"]
        report = evaluate_portfolio(account, positions, prices, nav)
        # Only the newest signal per held symbol should influence the snapshot.
        seen = set()
        for signal in signals:
            symbol = signal["symbol"]
            if symbol in seen or symbol not in positions or positions[symbol].get("qty", 0) <= 0:
                continue
            seen.add(symbol)
            if signal["status"] == "review_required":
                report["alerts"].append(
                    {
                        "kind": "research",
                        "symbol": symbol,
                        "message": f"{symbol} 的最新研究需要复评",
                    }
                )
        reports.append(
            {
                "account_id": account_id,
                "name": account.get("name", account_id),
                "as_of": as_of,
                **report,
            }
        )
    return {"accounts": reports}
