"""策略进化推荐核心测试 — 候选生成 / 质量分 / 稳定性评分 / 多重门槛。

被测纯函数 (不依赖行情与回测引擎):
- generate_candidates: 单点扰动 + exec 扰动 + 评分 profile + 迭代种子, 去重与上限
- profit_quality_score: 收益质量分单调性
- walk_forward_stability_score / _walk_forward_summary: 稳定性评分口径
- _pick_recommendation: 六重门槛过滤 (收益/胜率/回撤/盈亏比/质量/稳定性)
"""
from __future__ import annotations

from datetime import date

import pytest

from app.backtest.evolution import (
    EvolutionConfig,
    _pick_recommendation,
    _walk_forward_summary,
    generate_candidates,
    load_recommendations,
    profit_quality_score,
    walk_forward_stability_score,
)


def _meta() -> dict:
    return {
        "id": "test_strategy",
        "params": [
            {"id": "vol_ratio_min", "label": "最低量比", "type": "float",
             "default": 1.5, "min": 0.5, "max": 5.0, "step": 0.1},
            {"id": "lookback", "label": "回看天数", "type": "int",
             "default": 20, "min": 5, "max": 60, "step": 5},
            {"id": "require_bullish", "label": "要求收阳", "type": "bool", "default": True},
        ],
        "scoring": {"change_pct": 0.4, "momentum_5d": 0.3, "vol_ratio_5d": 0.3},
    }


def _cfg(**overrides) -> EvolutionConfig:
    base = dict(
        strategy_id="test_strategy",
        max_positions=5,
        holding_days=5,
        max_candidates=20,
    )
    base.update(overrides)
    return EvolutionConfig(**base)


# ── 候选生成 ─────────────────────────────────────────────────

def test_generate_candidates_single_point_perturbation():
    cfg = _cfg()
    candidates = generate_candidates(_meta(), cfg)
    names = [c["name"] for c in candidates]
    # 数值参数扰动 (vol_ratio_min ±1/±2 步长, lookback ±1/±2 步长) + exec + profile
    assert len(candidates) <= cfg.max_candidates
    # 至少包含量比与回看天数的扰动
    assert any("最低量比" in name for name in names)
    assert any("回看天数" in name for name in names)
    # 单点扰动: 每个候选至多一个参数偏离基线
    base_params = {p["id"]: p["default"] for p in _meta()["params"] if p["default"] is not None}
    for c in candidates:
        sp = c["strategy_params"]
        diffs = {k for k in base_params if base_params[k] != sp.get(k)}
        assert len(diffs) <= 1, f"候选 {c['name']} 多参数偏离: {diffs}"
    # 无重复
    sigs = []
    for c in candidates:
        sig = (tuple(sorted(c["strategy_params"].items())), tuple(sorted(c["exec_params"].items())))
        assert sig not in sigs
        sigs.append(sig)


def test_generate_candidates_exec_perturbation():
    cfg = _cfg(max_positions=5, holding_days=5, max_candidates=40)
    candidates = generate_candidates(_meta(), cfg)
    positions = {c["exec_params"].get("max_positions") for c in candidates
                 if c["metadata"].get("type") == "exec" and c["metadata"].get("param") == "max_positions"}
    assert positions <= {4, 6}
    hold_days = {c["exec_params"].get("holding_days") for c in candidates
                 if c["metadata"].get("type") == "exec" and c["metadata"].get("param") == "holding_days"}
    assert hold_days <= {3, 7}


def test_generate_candidates_seeds():
    cfg = _cfg()
    seeds = [
        {"name": "历史推荐1", "strategy_params": {"vol_ratio_min": 1.2}, "exec_params": {"max_positions": 6, "holding_days": 5},
         "metadata": {"run_id": "r1"}},
    ]
    candidates = generate_candidates(_meta(), cfg, seeds)
    seed_candidates = [c for c in candidates if c["metadata"].get("type") == "seed"]
    assert any(c["strategy_params"].get("vol_ratio_min") == 1.2 for c in seed_candidates)


# ── 收益质量分 ───────────────────────────────────────────────

def test_profit_quality_score_monotonic():
    base = {"total_return": 0.1, "win_rate": 0.5, "profit_factor": 1.5,
            "max_drawdown": -0.1, "avg_pnl": 0.02, "avg_win": 0.08, "avg_loss": 0.04}
    low = profit_quality_score(base)
    better = profit_quality_score({**base, "total_return": 0.3, "win_rate": 0.6})
    assert better > low
    worse = profit_quality_score({**base, "max_drawdown": -0.5, "total_return": -0.3})
    assert worse < low
    assert 0.0 <= low <= 100.0


def test_profit_quality_score_handles_none():
    score = profit_quality_score({})
    assert score == 50.0  # 中性基线


# ── walk-forward 稳定性评分 ─────────────────────────────────

def test_stability_score_ranges():
    good = {"positive_window_ratio": 1.0, "avg_total_return": 0.08, "avg_profit_quality_score": 60.0,
            "avg_expectancy": 0.03, "worst_max_drawdown": -0.05, "return_std": 0.02, "worst_total_return": 0.01}
    bad = {"positive_window_ratio": 0.0, "avg_total_return": -0.2, "avg_profit_quality_score": 20.0,
           "avg_expectancy": -0.05, "worst_max_drawdown": -0.4, "return_std": 0.3, "worst_total_return": -0.3}
    assert walk_forward_stability_score(good) > walk_forward_stability_score(bad)
    assert 0.0 <= walk_forward_stability_score(good) <= 100.0
    assert 0.0 <= walk_forward_stability_score(bad) <= 100.0


def test_walk_forward_summary_empty():
    summary = _walk_forward_summary([])
    assert summary["window_count"] == 0
    assert summary["stability_score"] == 0.0


def test_walk_forward_summary_stats():
    windows = [
        {"total_return": 0.1, "win_rate": 0.6, "max_drawdown": -0.05, "profit_factor": 1.5,
         "n_trades": 10, "avg_pnl": 0.02, "avg_win": 0.1, "avg_loss": 0.04},
        {"total_return": 0.05, "win_rate": 0.55, "max_drawdown": -0.08, "profit_factor": 1.2,
         "n_trades": 8, "avg_pnl": 0.01, "avg_win": 0.08, "avg_loss": 0.05},
        {"total_return": -0.02, "win_rate": 0.45, "max_drawdown": -0.12, "profit_factor": 0.9,
         "n_trades": 6, "avg_pnl": -0.005, "avg_win": 0.06, "avg_loss": 0.07},
    ]
    summary = _walk_forward_summary(windows)
    assert summary["window_count"] == 3
    assert summary["positive_windows"] == 2
    assert summary["positive_window_ratio"] == pytest.approx(2 / 3, abs=0.001)
    assert summary["total_trades"] == 24
    assert summary["worst_total_return"] == pytest.approx(-0.02, abs=0.001)


# ── 多重门槛推荐 ─────────────────────────────────────────────

def _candidate(name, total_return=0.15, win_rate=0.55, max_drawdown=-0.10,
               profit_factor=1.6, n_trades=20, quality=70.0, wf_ratio=1.0,
               wf_stability=70.0, wf_positive=3):
    return {
        "name": name,
        "validate": {
            "total_return": total_return, "win_rate": win_rate, "max_drawdown": max_drawdown,
            "profit_factor": profit_factor, "n_trades": n_trades, "avg_pnl": 0.03,
            "profit_quality_score": quality,
        },
        "walk_forward": {
            "positive_window_ratio": wf_ratio, "stability_score": wf_stability,
            "positive_windows": wf_positive,
        },
    }


def _baseline():
    return {
        "validate": {"total_return": 0.10, "win_rate": 0.50, "max_drawdown": -0.12,
                     "profit_factor": 1.4, "n_trades": 18, "avg_pnl": 0.02,
                     "profit_quality_score": 65.0},
        "walk_forward": {"positive_window_ratio": 0.66, "stability_score": 60.0,
                         "positive_windows": 2},
    }


def test_pick_recommendation_accepts_qualified():
    cfg = _cfg(min_validation_trades=3, min_positive_windows=1,
               min_positive_window_ratio=0.5, min_stability_score=55)
    baseline = _baseline()
    candidates = [
        _candidate("不合格-收益低", total_return=0.08),
        _candidate("合格-全面占优", total_return=0.18, win_rate=0.58, max_drawdown=-0.08,
                   profit_factor=1.8, quality=75.0, wf_stability=72.0),
    ]
    rec = _pick_recommendation(baseline, candidates, cfg)
    assert rec is not None
    assert rec["name"] == "合格-全面占优"


def test_pick_recommendation_rejects_each_gate():
    cfg = _cfg(min_validation_trades=3, min_positive_windows=1,
               min_positive_window_ratio=0.5, min_stability_score=55)
    baseline = _baseline()
    gate_violations = [
        ("交易数不足", {"n_trades": 2}),
        ("收益不占优", {"total_return": 0.10}),
        ("回撤更差", {"max_drawdown": -0.15}),
        ("胜率更低", {"win_rate": 0.48}),
        ("盈亏比更低", {"profit_factor": 1.3}),
        ("质量更低", {"quality": 60.0}),
    ]
    for name, patch in gate_violations:
        kwargs = dict(patch)
        candidates = [_candidate(f"{name}", **kwargs)]
        rec = _pick_recommendation(baseline, candidates, cfg)
        assert rec is None, f"门槛 {name} 未生效"

    # walk-forward 稳定性门槛
    candidates = [_candidate("wf稳定性不足", wf_ratio=0.4, wf_stability=50.0, wf_positive=1)]
    assert _pick_recommendation(baseline, candidates, cfg) is None

    # walk-forward 正窗口比例门槛
    candidates = [_candidate("wf窗口不足", wf_ratio=0.3, wf_stability=75.0, wf_positive=1)]
    assert _pick_recommendation(baseline, candidates, cfg) is None


def test_pick_recommendation_picks_best_by_stability():
    cfg = _cfg(min_validation_trades=3, min_positive_windows=1,
               min_positive_window_ratio=0.5, min_stability_score=55)
    baseline = _baseline()
    candidates = [
        _candidate("候选A-更稳", wf_stability=85.0, quality=72.0, total_return=0.15),
        _candidate("候选B-收益高但不稳", wf_stability=70.0, quality=78.0, total_return=0.25),
    ]
    rec = _pick_recommendation(baseline, candidates, cfg)
    assert rec is not None
    assert rec["name"] == "候选A-更稳"  # 稳定性优先于单窗口收益


# ── run_evolution 端到端 (fake service, 不依赖行情) ─────────────

class _FakeStrategy:
    meta = _meta()

    def __init__(self, meta):
        self.meta = meta


class _FakeResult:
    def __init__(self, stats):
        self.error = None
        self.stats = stats


class _FakeService:
    """vol_ratio_min=1.6 时全面优于基线 → 可稳定触发推荐。"""

    _BASE = {"total_return": 0.10, "win_rate": 0.50, "max_drawdown": -0.12,
             "profit_factor": 1.4, "n_trades": 18, "avg_pnl": 0.02,
             "avg_win": 0.08, "avg_loss": 0.04}
    _GOOD = {"total_return": 0.18, "win_rate": 0.58, "max_drawdown": -0.07,
             "profit_factor": 1.9, "n_trades": 22, "avg_pnl": 0.03,
             "avg_win": 0.09, "avg_loss": 0.03}

    def run(self, config):
        params = config.params or {}
        if params.get("vol_ratio_min") == 1.6:
            return _FakeResult(dict(self._GOOD))
        return _FakeResult(dict(self._BASE))


class _FakeEngine:
    def __init__(self, meta):
        self._meta = meta

    def get(self, strategy_id):
        if strategy_id != "test_strategy":
            raise ValueError("unknown strategy")
        return _FakeStrategy(self._meta)


def test_run_evolution_recommends_and_persists(tmp_path):
    from app.backtest.evolution import run_evolution

    cfg = _cfg(max_candidates=20)
    result = run_evolution(_FakeService(), _FakeEngine(_meta()), tmp_path, cfg)
    assert result["status"] == "recommended"
    assert result["recommendation"] is not None
    assert result["recommendation"]["strategy_params"].get("vol_ratio_min") == 1.6
    assert result["baseline"]["validate"]["total_return"] == pytest.approx(0.10)

    # 持久化
    records = load_recommendations(tmp_path)
    assert len(records) == 1
    assert records[0]["run_id"] == result["run_id"]


def test_run_evolution_apply_roundtrip(tmp_path):
    from app.backtest.evolution import (
        apply_recommendation,
        get_applied_overrides,
        run_evolution,
    )

    cfg = _cfg(max_candidates=20)
    result = run_evolution(_FakeService(), _FakeEngine(_meta()), tmp_path, cfg)
    assert result["status"] == "recommended"

    applied = apply_recommendation(tmp_path, result["run_id"])
    assert applied["ok"] is True
    overrides = get_applied_overrides(tmp_path)
    assert "test_strategy" in overrides
    assert overrides["test_strategy"]["params"]["vol_ratio_min"] == 1.6
    assert overrides["test_strategy"]["run_id"] == result["run_id"]


def test_run_evolution_apply_unknown_run(tmp_path):
    from app.backtest.evolution import apply_recommendation

    result = apply_recommendation(tmp_path, "not-exist")
    assert result["ok"] is False


def test_run_evolution_no_improvement(tmp_path):
    from app.backtest.evolution import run_evolution

    class _FlatService:
        def run(self, config):
            return _FakeResult(dict(_FakeService._BASE))

    cfg = _cfg(max_candidates=20)
    result = run_evolution(_FlatService(), _FakeEngine(_meta()), tmp_path, cfg)
    assert result["status"] == "no_improvement"
    assert result["recommendation"] is None


def test_run_evolution_unknown_strategy(tmp_path):
    from app.backtest.evolution import run_evolution

    class _EmptyEngine:
        def get(self, strategy_id):
            raise ValueError("unknown strategy")

    cfg = _cfg(max_candidates=20)
    result = run_evolution(_FakeService(), _EmptyEngine(), tmp_path, cfg)
    assert result["status"] == "error"
