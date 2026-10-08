"""Turnover-decayed A-share chip distribution metrics.

The calculation is deliberately on-demand: chip histograms are analysis output,
not part of the persisted enriched schema. Prices use the same adjusted OHLC
series as the stock-analysis chart and turnover_rate uses percentage points.
"""
from __future__ import annotations

import math

import polars as pl


def compute_chip_metrics(df: pl.DataFrame, *, days: int = 210, bins: int = 150) -> dict:
    """Return weighted cost, 70/90% chip bands, and profitable-chip ratio.

    Daily volume is distributed over the candle's low/high range with a
    triangular profile peaking at the OHLC4 price. Existing chips decay by
    that day's turnover before new volume is added. Missing turnover makes the
    result unavailable instead of pretending old chips have not changed hands.
    """
    empty = {
        "available": False,
        "reason": "历史数据不足或缺少换手率",
        "as_of": None,
        "trading_days": 0,
        "average_cost": None,
        "cost_70": None,
        "cost_90": None,
        "concentration_70": None,
        "concentration_90": None,
        "profitable_ratio": None,
    }
    required = {"open", "high", "low", "close", "volume", "turnover_rate"}
    if df.is_empty() or not required.issubset(df.columns):
        return empty

    rows = df.tail(max(1, min(int(days), 1000))).select(
        "open", "high", "low", "close", "volume", "turnover_rate",
        *(["date"] if "date" in df.columns else []),
    ).to_dicts()
    if len(rows) < 20:
        return empty
    valid = []
    for row in rows:
        try:
            values = [float(row[key]) for key in ("open", "high", "low", "close", "volume", "turnover_rate")]
        except (TypeError, ValueError):
            continue
        open_price, high, low, close, volume, turnover = values
        if not all(math.isfinite(value) for value in values) or min(open_price, high, low, close) <= 0:
            continue
        if volume < 0 or turnover < 0:
            continue
        valid.append((open_price, high, low, close, volume, min(turnover, 100.0), row.get("date")))
    if len(valid) < 20:
        return empty

    price_min = min(row[2] for row in valid)
    price_max = max(row[1] for row in valid)
    if not price_max > price_min:
        return empty
    bins = max(30, min(int(bins), 500))
    step = max(0.01, (price_max - price_min) / (bins - 1))
    # Recompute the upper edge after enforcing a one-cent minimum resolution.
    bins = max(2, math.ceil((price_max - price_min) / step) + 1)
    chips = [0.0] * bins

    for open_price, high, low, close, volume, turnover_pct, _day in valid:
        decay = 1.0 - turnover_pct / 100.0
        if decay != 1.0:
            chips = [amount * decay for amount in chips]
        if volume <= 0:
            continue
        typical = (open_price + high + low + close) / 4.0
        first = max(0, int((low - price_min) / step))
        last = min(bins - 1, int((high - price_min) / step))
        weights: list[tuple[int, float]] = []
        for index in range(first, last + 1):
            price = price_min + index * step
            if high == low:
                weight = 1.0
            elif price <= typical:
                weight = max(0.0, (price - low) / max(typical - low, 1e-12))
            else:
                weight = max(0.0, (high - price) / max(high - typical, 1e-12))
            weights.append((index, weight))
        total_weight = sum(weight for _, weight in weights)
        if total_weight <= 0:
            index = min(bins - 1, max(0, int((typical - price_min) / step)))
            chips[index] += volume
        else:
            for index, weight in weights:
                chips[index] += volume * weight / total_weight

    total = sum(chips)
    if not math.isfinite(total) or total <= 0:
        return empty

    def quantile_price(q: float) -> float:
        target = total * q
        cumulative = 0.0
        for index, amount in enumerate(chips):
            cumulative += amount
            if cumulative >= target:
                return price_min + index * step
        return price_min + (bins - 1) * step

    def band(percent: float) -> dict:
        lower_q, upper_q = (1.0 - percent) / 2.0, (1.0 + percent) / 2.0
        lower, upper = quantile_price(lower_q), quantile_price(upper_q)
        denominator = lower + upper
        return {
            "lower": round(lower, 4),
            "upper": round(upper, 4),
            "concentration": round((upper - lower) / denominator, 6) if denominator > 0 else None,
        }

    current_close = valid[-1][3]
    profitable = sum(amount for index, amount in enumerate(chips)
                     if price_min + index * step <= current_close)
    average = sum((price_min + index * step) * amount for index, amount in enumerate(chips)) / total
    return {
        "available": True,
        "reason": None,
        "as_of": str(valid[-1][6]) if valid[-1][6] is not None else None,
        "trading_days": len(valid),
        "average_cost": round(average, 4),
        "cost_70": band(0.70),
        "cost_90": band(0.90),
        "concentration_70": band(0.70)["concentration"],
        "concentration_90": band(0.90)["concentration"],
        "profitable_ratio": round(profitable / total, 6),
    }
