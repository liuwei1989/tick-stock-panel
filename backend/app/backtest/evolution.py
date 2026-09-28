"""策略进化推荐 — 候选参数生成 + 训练/验证分离 + walk-forward 稳定性评估 + 门槛推荐。

移植自 a_stock_trader_system (astock_selector/evolution.py) 的设计思路, 按 TSP 技术栈重写:

- 候选生成: 对策略 META.params 的数值参数做 ±1/±2 步长扰动 + 评分权重 profile 变体
  + max_positions / holding_days 单点扰动; 支持以历史优秀推荐为迭代种子。
- 评估: 训练/验证分离 + walk-forward 多窗口稳定性 (复用 backtest/walkforward.generate_folds)。
- 稳定性评分: 正窗口比例 / 平均收益 / 收益质量 / 期望 / 最差回撤 / 收益波动 → 0-100。
- 推荐门槛: 验证期收益/胜率/回撤/盈亏比/质量/交易数 + walk-forward 稳定性全部不低于基线
  才生成推荐 (多重门槛, 防单参数点过拟合)。
- 推荐只落盘 (JSONL, 数据目录内), 人工确认后经 API 应用 (写 evolution_applied.json,
  单策略扫描时并入运行参数)。

与 walkforward.py 的分工: walkforward.py 负责"给定参数网格 → 每折训练区优化 + OOS 验证";
本模块负责"生成候选参数集 → 整体评估 → 按多重门槛推荐一个候选"。两者互补, 本模块不重复
实现网格优化, 直接以现有策略参数为锚做局部扰动, 数据需求低 (180 训练 + 30 验证即可)。
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ───────────────────────── 配置 ─────────────────────────

@dataclass
class EvolutionConfig:
    strategy_id: str
    train_days: int = 180
    validate_days: int = 30
    walk_forward_windows: int = 3
    max_candidates: int = 20
    min_validation_trades: int = 3
    min_positive_windows: int = 1
    min_positive_window_ratio: float = 0.5
    min_stability_score: float = 55.0
    symbols: list[str] | None = None
    start: date | None = None
    end: date | None = None
    max_positions: int = 5
    holding_days: int = 5
    max_positions_range: tuple[int, int] = (3, 8)
    holding_days_delta: int = 2
    iterative_seeds: bool = True
    seed_runs: int = 3


# ───────────────────────── 参数与候选生成 ─────────────────────────

def _numeric_param_defs(meta: dict) -> list[dict]:
    """提取 META.params 中可扰动的数值参数 (float/int, 有 default)。"""
    out: list[dict] = []
    for pdef in meta.get("params") or []:
        if pdef.get("type") not in ("float", "int"):
            continue
        if pdef.get("default") is None:
            continue
        out.append(pdef)
    return out


def _perturb_values(pdef: dict) -> list:
    """数值参数在 default 附近 ±1/±2 步长展开 (min/max 夹取), 含 default。"""
    ptype = pdef["type"]
    default = pdef["default"]
    lo = pdef.get("min")
    hi = pdef.get("max")
    step = pdef.get("step")
    values = {default}
    if step and float(step) > 0:
        for k in (1, 2):
            v_lo = default - k * step
            v_hi = default + k * step
            if lo is not None and v_lo < lo:
                v_lo = lo
            if hi is not None and v_hi > hi:
                v_hi = hi
            values.add(v_lo)
            values.add(v_hi)
    ordered = sorted(values)
    if ptype == "int":
        return [int(v) for v in ordered]
    return [round(float(v), 4) for v in ordered]


def _scoring_profiles(scoring: dict | None) -> list[tuple[str, dict | None]]:
    """评分权重变体: base + 反转/防守倾斜。动量/趋势类关键词权重下调, 其余上调。"""
    scoring = dict(scoring or {})
    profiles: list[tuple[str, dict | None]] = [("base", None)]
    if scoring:
        def _tilt(factor: float) -> dict:
            momentum_keys = ("momentum", "trend", "breakout", "ret_20", "ret_60", "break_")
            tilted = {}
            for key, weight in scoring.items():
                k_low = key.lower()
                if any(tag in k_low for tag in momentum_keys):
                    tilted[key] = round(float(weight) * factor, 4)
                else:
                    tilted[key] = round(float(weight) * (2.0 - factor), 4)
            return tilted
        profiles.append(("defensive_tilt", _tilt(0.6)))
        profiles.append(("momentum_tilt", _tilt(1.4)))
    return profiles


def _base_strategy_params(strategy_meta: dict) -> dict:
    """从 META.params 默认值构建基础参数 dict。"""
    return {pdef["id"]: pdef.get("default") for pdef in strategy_meta.get("params") or [] if pdef.get("default") is not None}


def generate_candidates(
    strategy_meta: dict,
    cfg: EvolutionConfig,
    seeds: list[dict] | None = None,
) -> list[dict]:
    """生成候选策略参数集: 单参数扰动 + exec 参数扰动 + 评分 profile + 迭代种子。

    每个候选只偏离基线一个维度 (单点扰动, 便于归因); 候选上限 cfg.max_candidates。
    """
    base_params = _base_strategy_params(strategy_meta)
    candidates: list[dict] = []
    seen: set[tuple] = set()

    def _signature(strategy_params: dict, exec_params: dict) -> tuple:
        sp = tuple(sorted((k, repr(v)) for k, v in strategy_params.items()))
        ep = tuple(sorted((k, repr(v)) for k, v in exec_params.items()))
        return (sp, ep)

    def _add(name: str, strategy_params: dict, exec_params: dict, metadata: dict) -> None:
        if len(candidates) >= cfg.max_candidates:
            return
        sig = _signature(strategy_params, exec_params)
        if sig in seen:
            return
        seen.add(sig)
        candidates.append({
            "name": name,
            "strategy_params": dict(strategy_params),
            "exec_params": dict(exec_params),
            "metadata": metadata,
        })

    base_exec = {"max_positions": cfg.max_positions, "holding_days": cfg.holding_days}

    # 1) 数值参数单点扰动
    for pdef in _numeric_param_defs(strategy_meta):
        pid = pdef["id"]
        for value in _perturb_values(pdef):
            if value == pdef["default"]:
                continue
            sp = dict(base_params)
            sp[pid] = value
            _add(
                f"参数 {pdef.get('label', pid)}={value}",
                sp,
                base_exec,
                {"type": "param", "param": pid, "value": value},
            )

    # 2) exec 参数扰动 (max_positions / holding_days)
    lo_p, hi_p = cfg.max_positions_range
    for mp in (cfg.max_positions - 1, cfg.max_positions + 1):
        if lo_p <= mp <= hi_p and mp != cfg.max_positions:
            _add(
                f"买入数量={mp}",
                base_params,
                {**base_exec, "max_positions": mp},
                {"type": "exec", "param": "max_positions", "value": mp},
            )
    for hd in (cfg.holding_days - cfg.holding_days_delta, cfg.holding_days + cfg.holding_days_delta):
        if hd >= 2 and hd != cfg.holding_days:
            _add(
                f"持有天数={hd}",
                base_params,
                {**base_exec, "holding_days": hd},
                {"type": "exec", "param": "holding_days", "value": hd},
            )

    # 3) 评分权重 profile 变体
    for profile_name, tilted in _scoring_profiles(strategy_meta.get("scoring")):
        if tilted is None:
            continue
        _add(
            f"评分档={profile_name}",
            base_params,
            base_exec,
            {"type": "profile", "profile": profile_name},
        )

    # 4) 迭代种子: 历史推荐参数作为整体候选
    for seed in seeds or []:
        sp = dict(seed.get("strategy_params") or {})
        ep = dict(seed.get("exec_params") or base_exec)
        if sp == base_params and ep == base_exec:
            continue
        _add(
            f"种子: {seed.get('name', '历史推荐')}",
            sp,
            ep,
            {"type": "seed", "source": seed.get("metadata", {}).get("run_id", "")},
        )

    return candidates


# ───────────────────────── 统计与评分 ─────────────────────────

def _f(v: Any) -> float:
    try:
        value = float(v)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def profit_quality_score(stats: dict) -> float:
    """收益质量分 (0-100), 对齐 a_stock_trader backtest._profit_quality_score 口径。

    输入为 TSP stats 字段: total_return(小数) / win_rate(小数) / profit_factor /
    max_drawdown(负小数) / avg_pnl(小数, 即期望) / payoff(avg_win/avg_loss)。
    字段缺失时跳过对应项 (不按 0 惩罚), 保证空 stats 回到中性基线。
    """
    score = 50.0

    def _add(delta: float, lo: float, hi: float) -> None:
        nonlocal score
        score += max(lo, min(hi, delta))

    total_return = stats.get("total_return")
    if total_return is not None:
        _add(_f(total_return) * 120.0, -25.0, 25.0)

    win_rate = stats.get("win_rate")
    if win_rate is not None:
        _add((_f(win_rate) - 0.5) * 50.0, -15.0, 15.0)

    profit_factor = stats.get("profit_factor")
    if profit_factor is not None:
        _add((_f(profit_factor) - 1.0) * 15.0, -15.0, 20.0)

    max_drawdown = stats.get("max_drawdown")
    if max_drawdown is not None:
        _add(_f(max_drawdown) * 110.0, -25.0, 15.0)

    avg_pnl = stats.get("avg_pnl")
    if avg_pnl is not None:
        _add(_f(avg_pnl) * 400.0, -15.0, 15.0)

    avg_win = _f(stats.get("avg_win"))
    avg_loss = abs(_f(stats.get("avg_loss")))
    if avg_win or avg_loss:
        payoff = (avg_win / avg_loss) if avg_loss > 0 else (8.0 if avg_win > 0 else 0.0)
        _add((payoff - 1.0) * 8.0, -10.0, 10.0)

    return round(max(0.0, min(100.0, score)), 1)


def walk_forward_stability_score(summary: dict) -> float:
    """walk-forward 稳定性评分 (0-100)。

    输入 summary 字段 (全部用 TSP 小数口径): positive_window_ratio / avg_total_return /
    avg_profit_quality_score / avg_expectancy / worst_max_drawdown / return_std /
    worst_total_return。
    """
    positive_ratio = _f(summary.get("positive_window_ratio"))
    avg_return = _f(summary.get("avg_total_return"))
    avg_quality = _f(summary.get("avg_profit_quality_score"))
    avg_expectancy = _f(summary.get("avg_expectancy"))
    worst_drawdown = _f(summary.get("worst_max_drawdown"))
    return_std = _f(summary.get("return_std"))
    worst_return = _f(summary.get("worst_total_return"))

    score = 45.0
    score += min(28.0, max(-18.0, positive_ratio * 28.0))
    score += min(18.0, max(-18.0, avg_return * 110.0))
    score += min(10.0, max(-12.0, (avg_quality - 50.0) * 0.35))
    score += min(10.0, max(-12.0, avg_expectancy * 250.0))
    score += min(10.0, max(-18.0, worst_drawdown * 80.0))
    score -= min(18.0, return_std * 150.0)
    if worst_return < 0:
        score -= min(12.0, abs(worst_return) * 70.0)
    return round(max(0.0, min(100.0, score)), 2)


def _walk_forward_summary(windows: list[dict]) -> dict:
    if not windows:
        return {
            "window_count": 0,
            "active_windows": 0,
            "positive_windows": 0,
            "positive_window_ratio": 0.0,
            "avg_total_return": 0.0,
            "worst_total_return": 0.0,
            "avg_max_drawdown": 0.0,
            "worst_max_drawdown": 0.0,
            "avg_profit_quality_score": 0.0,
            "avg_expectancy": 0.0,
            "avg_win_rate": 0.0,
            "total_trades": 0,
            "return_std": 0.0,
            "stability_score": 0.0,
        }
    returns = [_f(w.get("total_return")) for w in windows]
    drawdowns = [_f(w.get("max_drawdown")) for w in windows]
    qualities = [_f(w.get("profit_quality_score")) for w in windows]
    expectancies = [_f(w.get("avg_pnl")) for w in windows]
    win_rates = [_f(w.get("win_rate")) for w in windows]
    trades = [int(_f(w.get("n_trades"))) for w in windows]
    positive = sum(1 for v in returns if v > 0)
    active = sum(1 for v in trades if v > 0)
    avg_return = sum(returns) / len(returns)
    variance = sum((v - avg_return) ** 2 for v in returns) / len(returns)
    summary = {
        "window_count": len(windows),
        "active_windows": active,
        "positive_windows": positive,
        "positive_window_ratio": round(positive / len(windows), 4),
        "avg_total_return": round(avg_return, 4),
        "worst_total_return": round(min(returns), 4),
        "avg_max_drawdown": round(sum(drawdowns) / len(drawdowns), 4),
        "worst_max_drawdown": round(min(drawdowns), 4),
        "avg_profit_quality_score": round(sum(qualities) / len(qualities), 2),
        "avg_expectancy": round(sum(expectancies) / len(expectancies), 4),
        "avg_win_rate": round(sum(win_rates) / len(win_rates), 4),
        "total_trades": sum(trades),
        "return_std": round(variance ** 0.5, 4),
    }
    summary["stability_score"] = walk_forward_stability_score(summary)
    return summary


# ───────────────────────── 评估与推荐 ─────────────────────────

def _backtest_stats(service, cfg: EvolutionConfig, strategy_params: dict, exec_params: dict, start: date, end: date) -> dict | None:
    """单区间策略回测, 返回 stats; 失败返回 None。"""
    from app.backtest.strategy import StrategyBacktestConfig

    config = StrategyBacktestConfig(
        strategy_id=cfg.strategy_id,
        symbols=cfg.symbols,
        start=start,
        end=end,
        params=strategy_params,
        mode="position",
        max_positions=int(exec_params.get("max_positions", cfg.max_positions)),
        holding_days=int(exec_params.get("holding_days", cfg.holding_days)),
    )
    result = service.run(config)
    if result.error:
        logger.warning("evolution backtest failed %s [%s,%s]: %s", cfg.strategy_id, start, end, result.error)
        return None
    stats = dict(result.stats or {})
    stats["profit_quality_score"] = profit_quality_score(stats)
    return stats


def _evaluate_candidate(
    service,
    cfg: EvolutionConfig,
    name: str,
    strategy_params: dict,
    exec_params: dict,
    windows: list[tuple[date, date]],
    progress: Callable[[str], None] | None = None,
) -> dict | None:
    """在 windows 上回测候选; 主窗口 (最后一个) 作为 validate, 全部窗口做 walk-forward 汇总。"""
    window_results: list[dict] = []
    for idx, (w_start, w_end) in enumerate(windows, start=1):
        stats = _backtest_stats(service, cfg, strategy_params, exec_params, w_start, w_end)
        if stats is None:
            window_results.append({"error": True})
            continue
        window_results.append(stats)
        if progress:
            progress(f"{name} 窗口{idx}/{len(windows)}: 收益 {stats.get('total_return', 0):+.2%}, "
                     f"胜率 {stats.get('win_rate', 0):.0%}, 回撤 {stats.get('max_drawdown', 0):.1%}")
    if not window_results:
        return None
    validate = window_results[-1]
    if validate.get("error"):
        return None
    wf = _walk_forward_summary([w for w in window_results if not w.get("error")])
    return {
        "name": name,
        "strategy_params": dict(strategy_params),
        "exec_params": dict(exec_params),
        "validate": validate,
        "walk_forward": wf,
        "score": round(wf.get("stability_score", 0) * 0.5 + float(validate.get("profit_quality_score", 0)) * 0.5, 2),
    }


def _pick_recommendation(
    baseline: dict,
    candidates: list[dict],
    cfg: EvolutionConfig,
) -> dict | None:
    """多重门槛推荐: 收益/胜率/回撤/盈亏比/质量/交易数 + walk-forward 稳定性全部不低于基线。"""
    bv = baseline["validate"]
    bw = baseline["walk_forward"]
    base_pf = _f(bv.get("profit_factor"))
    qualified: list[dict] = []
    for candidate in candidates:
        v = candidate["validate"]
        w = candidate["walk_forward"]
        pf = _f(v.get("profit_factor"))
        if (
            int(_f(v.get("n_trades"))) < cfg.min_validation_trades
            or _f(v.get("total_return")) <= _f(bv.get("total_return"))
            or _f(v.get("max_drawdown")) < _f(bv.get("max_drawdown"))
            or _f(v.get("win_rate")) < _f(bv.get("win_rate"))
            or pf < base_pf
            or float(v.get("profit_quality_score", 0)) <= float(bv.get("profit_quality_score", 0))
            or int(w.get("positive_windows", 0)) < cfg.min_positive_windows
            or float(w.get("positive_window_ratio", 0)) < cfg.min_positive_window_ratio
            or float(w.get("stability_score", 0)) < max(cfg.min_stability_score, float(bw.get("stability_score", 0)))
        ):
            continue
        qualified.append(candidate)
    if not qualified:
        return None
    return sorted(
        qualified,
        key=lambda c: (
            float(c["walk_forward"].get("stability_score", 0)),
            float(c["validate"].get("profit_quality_score", 0)),
            _f(c["validate"].get("total_return")),
        ),
        reverse=True,
    )[0]


# ───────────────────────── 主流程 ─────────────────────────

def _evaluation_windows(cfg: EvolutionConfig, end: date) -> list[tuple[date, date]]:
    """切出评估窗口: 前 train_days 为训练(锚), 其后按 walk_forward_windows 个验证窗口滚动。"""
    from app.backtest.walkforward import generate_folds

    total = cfg.train_days + cfg.walk_forward_windows * cfg.validate_days + cfg.validate_days
    start = end - timedelta(days=total + 20)
    folds = generate_folds(start, end, cfg.train_days, cfg.validate_days, cfg.validate_days)
    return [(f.test_start, f.test_end) for f in folds][-cfg.walk_forward_windows:]


def _load_seed_records(data_dir: Path, cfg: EvolutionConfig, limit: int) -> list[dict]:
    """读取历史推荐作为迭代种子 (仅同策略)。"""
    if not cfg.iterative_seeds:
        return []
    path = data_dir / "evolution_recommendations.jsonl"
    if not path.exists():
        return []
    seeds: list[dict] = []
    try:
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            record = json.loads(line)
            if record.get("strategy_id") != cfg.strategy_id:
                continue
            rec = record.get("recommendation")
            if not rec:
                continue
            seeds.append({
                "name": rec.get("name", f"历史推荐 {record.get('run_id', '')}"),
                "strategy_params": rec.get("strategy_params") or {},
                "exec_params": rec.get("exec_params") or {},
                "metadata": {"run_id": record.get("run_id", "")},
            })
            if len(seeds) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        logger.warning("load evolution seeds failed: %s", e)
    return seeds


def run_evolution(
    service,
    strategy_engine,
    data_dir: Path,
    cfg: EvolutionConfig,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """运行一轮策略进化, 持久化推荐记录, 返回完整结果。"""
    try:
        strategy = strategy_engine.get(cfg.strategy_id)
        strategy_meta = dict(strategy.meta or {})
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    end = cfg.end or date.today()
    windows = _evaluation_windows(cfg, end)
    if not windows:
        return {"status": "error", "error": "数据区间不足以切出评估窗口, 请扩大 train_days/validate_days 或检查行情覆盖"}

    base_params = _base_strategy_params(strategy_meta)
    base_exec = {"max_positions": cfg.max_positions, "holding_days": cfg.holding_days}
    if progress:
        progress(f"评估基线 {cfg.strategy_id} ({len(windows)} 个验证窗口)")
    baseline = _evaluate_candidate(service, cfg, "当前策略", base_params, base_exec, windows, progress)
    if baseline is None:
        return {"status": "error", "error": "基线回测失败 (行情覆盖不足或策略报错)"}

    seeds = _load_seed_records(data_dir, cfg, cfg.seed_runs)
    candidates = generate_candidates(strategy_meta, cfg, seeds)
    if progress:
        progress(f"生成 {len(candidates)} 个候选 ({len(seeds)} 个迭代种子)")

    evaluated: list[dict] = []
    for candidate in candidates:
        result = _evaluate_candidate(
            service, cfg, candidate["name"], candidate["strategy_params"], candidate["exec_params"], windows, progress
        )
        if result is not None:
            result["metadata"] = candidate.get("metadata", {})
            evaluated.append(result)

    recommendation = _pick_recommendation(baseline, evaluated, cfg)
    status = "recommended" if recommendation else "no_improvement"

    run_id = f"{cfg.strategy_id}-{uuid.uuid4().hex[:8]}"
    record = {
        "run_id": run_id,
        "strategy_id": cfg.strategy_id,
        "status": status,
        "created_at": end.isoformat(),
        "windows": [
            {"validate_start": str(ws), "validate_end": str(we)} for ws, we in windows
        ],
        "config": {
            "train_days": cfg.train_days,
            "validate_days": cfg.validate_days,
            "walk_forward_windows": cfg.walk_forward_windows,
            "max_candidates": cfg.max_candidates,
            "min_validation_trades": cfg.min_validation_trades,
            "min_positive_windows": cfg.min_positive_windows,
            "min_positive_window_ratio": cfg.min_positive_window_ratio,
            "min_stability_score": cfg.min_stability_score,
        },
        "baseline": baseline,
        "candidates": evaluated,
        "recommendation": recommendation,
    }
    _append_recommendation(data_dir, record)
    if progress:
        progress(f"完成: {status}" + (f" → 推荐 {recommendation['name']}" if recommendation else ""))
    return {
        "run_id": run_id,
        "strategy_id": cfg.strategy_id,
        "status": status,
        "created_at": record["created_at"],
        "windows": record["windows"],
        "config": record["config"],
        "baseline": baseline,
        "candidates": evaluated,
        "recommendation": recommendation,
    }


# ───────────────────────── 持久化与应用 ─────────────────────────

def _recommendations_path(data_dir: Path) -> Path:
    return data_dir / "evolution_recommendations.jsonl"


def _applied_path(data_dir: Path) -> Path:
    return data_dir / "evolution_applied.json"


def _append_recommendation(data_dir: Path, record: dict) -> None:
    path = _recommendations_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def load_recommendations(data_dir: Path, limit: int = 20) -> list[dict]:
    path = _recommendations_path(data_dir)
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))
    except Exception as e:  # noqa: BLE001
        logger.warning("load evolution recommendations failed: %s", e)
        return records
    return list(reversed(records))[:limit]


def apply_recommendation(data_dir: Path, run_id: str) -> dict:
    """人工确认后应用推荐: 写入 evolution_applied.json (单策略扫描时并入运行参数)。"""
    records = load_recommendations(data_dir, limit=100)
    target = next((r for r in records if r.get("run_id") == run_id), None)
    if target is None:
        return {"ok": False, "error": f"run_id {run_id} 不存在"}
    rec = target.get("recommendation")
    if not rec:
        return {"ok": False, "error": f"run_id {run_id} 无推荐 (status={target.get('status')})"}
    applied = get_applied_overrides(data_dir)
    applied[target["strategy_id"]] = {
        "run_id": run_id,
        "params": rec.get("strategy_params") or {},
        "exec_params": rec.get("exec_params") or {},
        "applied_at": target.get("created_at", ""),
        "name": rec.get("name", ""),
    }
    path = _applied_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(applied, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "strategy_id": target["strategy_id"], "applied": applied[target["strategy_id"]]}


def get_applied_overrides(data_dir: Path) -> dict[str, dict]:
    path = _applied_path(data_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("load applied evolution overrides failed: %s", e)
        return {}


def clear_applied(data_dir: Path, strategy_id: str) -> dict:
    applied = get_applied_overrides(data_dir)
    if strategy_id not in applied:
        return {"ok": False, "error": f"{strategy_id} 无已应用参数"}
    del applied[strategy_id]
    path = _applied_path(data_dir)
    path.write_text(json.dumps(applied, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "strategy_id": strategy_id}
