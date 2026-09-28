"""工作流闭环 API — 盘前计划生成/查询 + 程序化复盘/查询 + 闭环总览。

配套: app/services/workflow.py (计划/复盘存储与计算), evolution.py (策略发现)。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services import workflow as wf

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


class GeneratePlanRequest(BaseModel):
    trade_date: str | None = None
    max_entries: int = 20
    max_per_strategy: int = 8
    use_evolution: bool = True


class ReviewRequest(BaseModel):
    trade_date: str | None = None
    track_days: int = 1


@router.post("/plan/generate")
def generate_plan(req: GeneratePlanRequest, request: Request) -> dict:
    """盘前计划: 进化推荐策略扫描 + 自选补充池 → 固化当日计划。"""
    data_dir = _data_dir(request)
    engine = request.app.state.strategy_engine
    return wf.generate_plan(
        data_dir,
        trade_date=req.trade_date,
        max_entries=req.max_entries,
        max_per_strategy=req.max_per_strategy,
        engine=engine,
        use_evolution=req.use_evolution,
    )


@router.get("/plans")
def list_plans(request: Request, date: str | None = None) -> dict:
    """计划列表 (可按交易日过滤)。"""
    data_dir = _data_dir(request)
    plans = wf.list_plans(data_dir, date)
    return {"plans": plans, "total": len(plans)}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, request: Request) -> dict:
    data_dir = _data_dir(request)
    plan = wf.get_plan(data_dir, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"计划 {plan_id} 不存在")
    return plan


@router.post("/plan/{plan_id}/review")
def review_plan(plan_id: str, req: ReviewRequest, request: Request) -> dict:
    """程序化复盘: 对照计划逐标的算触发/收益/胜率 + 策略反馈回写。"""
    data_dir = _data_dir(request)
    return wf.review_plan(data_dir, plan_id, trade_date=req.trade_date, track_days=req.track_days)


@router.get("/reviews")
def list_reviews(request: Request, date: str | None = None) -> dict:
    data_dir = _data_dir(request)
    reviews = wf.list_reviews(data_dir, date)
    return {"reviews": reviews, "total": len(reviews)}


@router.get("/reviews/{review_id}")
def get_review(review_id: str, request: Request) -> dict:
    data_dir = _data_dir(request)
    review = wf.get_review(data_dir, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"复盘 {review_id} 不存在")
    return review


@router.get("/overview")
def overview(request: Request) -> dict:
    """闭环总览: 最新计划/复盘 + 进化推荐/反馈状态。"""
    return wf.workflow_overview(_data_dir(request))
