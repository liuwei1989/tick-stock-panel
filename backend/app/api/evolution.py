"""策略进化推荐 API — 运行进化、查看/应用推荐、环境门控建议。

- POST /api/evolution/run         运行一轮策略进化 (候选生成 + walk-forward 评估 + 门槛推荐)
- GET  /api/evolution/recommendations  最近推荐记录
- POST /api/evolution/recommendations/{run_id}/apply  人工确认后应用推荐 (写 evolution_applied.json)
- DELETE /api/evolution/recommendations/applied/{strategy_id}  撤销已应用参数
- GET  /api/evolution/env-gate/suggestion  当前市场环境下的选股参数建议 (环境门控)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.backtest.evolution import (
    apply_recommendation,
    clear_applied,
    get_applied_overrides,
    load_recommendations,
    run_evolution as run_evolution_core,
)
from app.config import settings
from app.services.env_gate import apply_env_gate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


def _data_dir(request: Request):
    return request.app.state.repo.store.data_dir


def _services(request: Request):
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestService

    engine = BacktestEngine(request.app.state.repo)
    strategy_engine = request.app.state.strategy_engine
    service = StrategyBacktestService(engine, strategy_engine)
    return service, strategy_engine


class EvolutionRunRequest(BaseModel):
    strategy_id: str
    train_days: int = Field(default=180, ge=60, le=1000)
    validate_days: int = Field(default=30, ge=10, le=300)
    walk_forward_windows: int = Field(default=3, ge=1, le=10)
    max_candidates: int = Field(default=12, ge=2, le=40)
    min_validation_trades: int = Field(default=3, ge=1, le=50)
    min_positive_windows: int = Field(default=1, ge=0, le=10)
    min_positive_window_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    min_stability_score: float = Field(default=55.0, ge=0.0, le=100.0)
    max_positions: int = Field(default=5, ge=1, le=20)
    holding_days: int = Field(default=5, ge=1, le=60)
    symbols: list[str] | None = None
    iterative_seeds: bool = True


@router.post("/run")
def run(request: EvolutionRunRequest, req: Request):
    """运行一轮策略进化; 结果持久化为 JSONL 记录, 有合格候选时给出推荐。"""
    from app.backtest.evolution import EvolutionConfig

    service, strategy_engine = _services(req)
    cfg = EvolutionConfig(
        strategy_id=request.strategy_id,
        train_days=request.train_days,
        validate_days=request.validate_days,
        walk_forward_windows=request.walk_forward_windows,
        max_candidates=request.max_candidates,
        min_validation_trades=request.min_validation_trades,
        min_positive_windows=request.min_positive_windows,
        min_positive_window_ratio=request.min_positive_window_ratio,
        min_stability_score=request.min_stability_score,
        max_positions=request.max_positions,
        holding_days=request.holding_days,
        symbols=request.symbols,
        iterative_seeds=request.iterative_seeds,
    )
    progress_log: list[str] = []

    def _progress(message: str) -> None:
        progress_log.append(message)
        logger.info("evolution[%s]: %s", request.strategy_id, message)

    result = run_evolution_core(service, strategy_engine, _data_dir(req), cfg, _progress)
    result["progress"] = progress_log
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "进化失败"))
    return result


@router.get("/recommendations")
def recommendations(req: Request, limit: int = 20):
    """最近策略进化推荐记录 (倒序)。"""
    return {"count": limit, "records": load_recommendations(_data_dir(req), limit=limit)}


@router.post("/recommendations/{run_id}/apply")
def apply_rec(run_id: str, req: Request):
    """人工确认后应用推荐参数 (写 evolution_applied.json, 单策略扫描时并入)。"""
    result = apply_recommendation(_data_dir(req), run_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "应用失败"))
    return result


@router.delete("/recommendations/applied/{strategy_id}")
def clear_applied_rec(strategy_id: str, req: Request):
    """撤销某策略已应用的进化参数, 恢复默认。"""
    result = clear_applied(_data_dir(req), strategy_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "无可撤销参数"))
    return result


@router.get("/applied")
def applied_overrides(req: Request):
    """各策略已应用的进化参数 (evolution_applied.json)。"""
    return {"applied": get_applied_overrides(_data_dir(req))}


@router.get("/env-gate/suggestion")
def env_gate_suggestion(req: Request):
    """当前市场环境下的选股参数建议 (环境门控)。

    读取最新 regime (环境分/state/情绪阶段), 给出防守/均衡/进攻三档下的
    min_score 调整与 max_results 系数。默认基线取 scan 常用值 (min_score=90, max_results=20),
    前端可按实际参数传入覆盖。
    """
    data_dir = _data_dir(req)
    latest: dict | None = None
    try:
        from app.services.regime_builder import load_regime_history

        df = load_regime_history(data_dir)
        if not df.is_empty() and "score" in df.columns:
            row = df.sort("date").tail(1).to_dicts()[0]
            latest = {
                "date": str(row.get("date", "")),
                "score": row.get("score"),
                "state": row.get("state"),
                "state_label": row.get("state_label"),
                "phase": row.get("phase"),
                "phase_label": row.get("phase_label"),
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("env-gate suggestion regime read failed: %s", e)

    if latest is None:
        return {"available": False, "detail": "无 regime 数据, 无法给出环境建议"}

    new_min_score, new_max_results, suggestion = apply_env_gate(
        90, 20,
        regime_score=latest.get("score"),
        phase=latest.get("phase"),
        state=latest.get("state"),
    )
    return {
        "available": True,
        "date": latest.get("date"),
        "regime": latest,
        "suggestion": suggestion,
        "default_params": {"min_score": 90, "max_results": 20},
        "applied_params": {"min_score": new_min_score, "max_results": new_max_results},
    }
