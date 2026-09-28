"""环境门控测试 — 环境 → 选股参数自适应。

被测:
- classify_mode: 环境分/state/情绪阶段 → 攻/守/均衡, 冲突取保守
- apply_env_gate: min_score 调整 + max_results 系数
"""
from __future__ import annotations

from app.services.env_gate import (
    apply_env_gate,
    classify_mode,
)


# ── classify_mode ────────────────────────────────────────────

def test_score_only():
    assert classify_mode(regime_score=75) == ("强势", "进攻")
    assert classify_mode(regime_score=35) == ("弱势", "防守")
    assert classify_mode(regime_score=55) == ("中性", "均衡")


def test_phase_only():
    assert classify_mode(phase="ebb") == ("弱势", "防守")
    assert classify_mode(phase="ice") == ("弱势", "防守")
    assert classify_mode(phase="repair") == ("弱势", "防守")
    assert classify_mode(phase="rally") == ("强势", "进攻")
    assert classify_mode(phase="climax") == ("强势", "进攻")


def test_state_only():
    assert classify_mode(state="strong") == ("强势", "进攻")
    assert classify_mode(state="weak") == ("弱势", "防守")
    assert classify_mode(state="range") == ("中性", "均衡")


def test_conflict_takes_defensive():
    # 环境分偏强但情绪阶段退潮 → 防守优先
    assert classify_mode(regime_score=75, phase="ebb") == ("弱势", "防守")
    # 环境分偏弱但 state 偏强 → 防守优先
    assert classify_mode(regime_score=30, state="strong") == ("弱势", "防守")
    # 全部未知 → 中性
    assert classify_mode() == ("中性", "均衡")


# ── apply_env_gate ───────────────────────────────────────────

def test_apply_defensive():
    min_score, max_results, suggestion = apply_env_gate(90, 20, regime_score=35)
    assert min_score == 100
    assert max_results == 13
    assert suggestion["mode"] == "防守"


def test_apply_offensive():
    min_score, max_results, suggestion = apply_env_gate(90, 20, regime_score=72)
    assert min_score == 85
    assert max_results == 24
    assert suggestion["mode"] == "进攻"


def test_apply_neutral():
    min_score, max_results, suggestion = apply_env_gate(90, 20, regime_score=55)
    assert (min_score, max_results) == (90, 20)
    assert suggestion["mode"] == "均衡"


def test_apply_no_op_when_disabled():
    min_score, max_results, suggestion = apply_env_gate(
        90, 20, regime_score=35, apply_to_selection=False
    )
    assert (min_score, max_results) == (90, 20)
    assert suggestion["min_score_delta"] == 10


def test_apply_bounds():
    min_score, max_results, _ = apply_env_gate(5, 1, regime_score=35)
    assert min_score == 15
    assert max_results == 1  # 不低于 1
    min_score, max_results, _ = apply_env_gate(295, 300, regime_score=72)
    assert min_score == 290  # 不高于 300
    assert max_results == 200  # 不高于 200
