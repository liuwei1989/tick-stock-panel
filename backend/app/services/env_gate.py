"""环境门控 — 市场环境(regime score + 情绪阶段) → 选股参数自适应建议。

移植自 a_stock_trader_system (astock_selector/market_env.py) 的 apply_market_environment 思路:

- 强势(进攻): 放宽选股 → min_score -5, max_results ×1.2
- 弱势(防守): 收紧选股 → min_score +10, max_results ×0.65
- 中性(均衡): 不变

输入用 TSP 已有数据: regime_builder 的 0-100 环境分与 5 档 state (强势/偏强/震荡/偏弱/弱势),
可选叠加 market_phase 的 6 阶段情绪周期 (主升/高潮 → 进攻倾向; 退潮/冰点/修复 → 防守倾向)。
多信号冲突时取更保守档 (防守优先), 避免逆势放宽。

与 TSP 已有 regime_filter 的分工: regime_filter 是回测期按 state 过滤个股入场;
本模块是"环境 → 扫描参数(min_score/max_results) 自适应", 服务选股扫描与前端建议。
"""
from __future__ import annotations

from typing import Any

# 环境分档位 (与 regime_builder 的 5 档 state 阈值对应)
STRONG_SCORE = 68.0
WEAK_SCORE = 42.0

# 情绪阶段 → 倾向 (market_phase 6 阶段)
_DEFENSIVE_PHASES = frozenset({"ebb", "ice", "repair"})      # 退潮/冰点/修复
_OFFENSIVE_PHASES = frozenset({"rally", "climax", "ignite"})  # 主升/高潮/启动

# state 档位 (regime_builder)
_OFFENSIVE_STATES = frozenset({"strong", "lean_strong"})
_DEFENSIVE_STATES = frozenset({"weak", "lean_weak"})


def classify_mode(regime_score: float | None = None, phase: str | None = None, state: str | None = None) -> tuple[str, str]:
    """返回 (status, mode): 强势/进攻、弱势/防守、中性/均衡。

    多信号冲突时取更保守档 (防守 > 均衡 > 进攻)。
    """
    score_mode = None
    if regime_score is not None:
        if regime_score >= STRONG_SCORE:
            score_mode = "进攻"
        elif regime_score <= WEAK_SCORE:
            score_mode = "防守"
        else:
            score_mode = "均衡"

    state_mode = None
    if state:
        if state in _OFFENSIVE_STATES:
            state_mode = "进攻"
        elif state in _DEFENSIVE_STATES:
            state_mode = "防守"
        else:
            state_mode = "均衡"

    phase_mode = None
    if phase:
        if phase in _DEFENSIVE_PHASES:
            phase_mode = "防守"
        elif phase in _OFFENSIVE_PHASES:
            phase_mode = "进攻"
        else:
            phase_mode = "均衡"

    candidates = [m for m in (score_mode, state_mode, phase_mode) if m is not None]
    if not candidates:
        return "中性", "均衡"
    # 取最保守档
    rank = {"防守": 0, "均衡": 1, "进攻": 2}
    mode = min(candidates, key=lambda m: rank[m])
    status = {"防守": "弱势", "均衡": "中性", "进攻": "强势"}[mode]
    return status, mode


def apply_env_gate(
    min_score: int,
    max_results: int,
    regime_score: float | None = None,
    phase: str | None = None,
    state: str | None = None,
    *,
    strong_score: float = STRONG_SCORE,
    weak_score: float = WEAK_SCORE,
    apply_to_selection: bool = True,
) -> tuple[int, int, dict[str, Any]]:
    """把环境信号应用到选股参数。

    返回 (new_min_score, new_max_results, suggestion)。suggestion 含 mode/min_score_delta/
    max_results_multiplier 供前端展示。apply_to_selection=False 时只返回建议不改参数。
    """
    status, mode = classify_mode(regime_score, phase, state)

    if mode == "进攻":
        min_score_delta = -5
        max_results_multiplier = 1.2
    elif mode == "防守":
        min_score_delta = 10
        max_results_multiplier = 0.65
    else:
        min_score_delta = 0
        max_results_multiplier = 1.0

    suggestion = {
        "mode": mode,
        "status": status,
        "min_score_delta": min_score_delta,
        "max_results_multiplier": max_results_multiplier,
    }
    if not apply_to_selection:
        return min_score, max_results, suggestion

    next_score = max(0, min(300, int(min_score) + min_score_delta))
    next_results = max(1, min(200, round(int(max_results) * max_results_multiplier)))
    return next_score, next_results, suggestion
