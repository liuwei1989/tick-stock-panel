"""Research contracts; work stays in services and reuses the existing repository."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.research import ResearchLedger, evaluate_outcome, outcome_cutoff
from app.services.research_schedule import ScheduleConfig, get_config, save_config
from app.services.research_skills import load_skills

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/schedule")
def schedule(request: Request):
    return {
        "config": get_config(request.app.state.repo.store.data_dir).model_dump(),
        "last_check": _ledger(request).get_setting("schedule_status"),
        "scheduler_available": getattr(request.app.state, "scheduler", None) is not None,
    }


@router.put("/schedule")
def update_schedule(request: Request, body: ScheduleConfig):
    try:
        return save_config(request.app.state.repo.store.data_dir, body).model_dump()
    except ValueError as exc:
        raise HTTPException(422, "研究框架无效, 请重新选择") from exc


def _ledger(request):
    return ResearchLedger(request.app.state.repo.store.data_dir)


@router.get("/skills")
def skills(request: Request):
    catalog, errors = load_skills(request.app.state.repo.store.data_dir)
    return {
        "skills": [s.model_dump(exclude={"instructions"}) for s in catalog.values()],
        "errors": errors,
        "builtin_count": sum(s.source == "builtin" for s in catalog.values()),
        "custom_count": sum(s.source == "custom" for s in catalog.values()),
    }


@router.get("/skills/{skill_id}")
def skill_detail(request: Request, skill_id: str):
    catalog, _errors = load_skills(request.app.state.repo.store.data_dir)
    skill = catalog.get(skill_id)
    if skill is None:
        raise HTTPException(404, "研究 Skill 不存在")
    return skill.model_dump()


@router.get("/runs")
def runs(request: Request):
    return {"runs": _ledger(request).list_runs()}


@router.get("/runs/{run_id}")
def run(request: Request, run_id: str):
    result = _ledger(request).get_run(run_id)
    if result is None:
        raise HTTPException(404, "研究任务不存在")
    return result


@router.get("/signals")
def signals(request: Request):
    return {"signals": _ledger(request).list_signals()}


class Transition(BaseModel):
    status: Literal["watching", "review_required", "dismissed"]
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/signals/{signal_id}/status")
def transition(request: Request, signal_id: str, body: Transition):
    try:
        return _ledger(request).transition_signal(
            signal_id, body.status, expected_version=body.expected_version, reason=body.reason
        )
    except KeyError as exc:
        raise HTTPException(404, "研究信号不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, "记录已更新或不允许该状态变化,请刷新") from exc


class OutcomeRequest(BaseModel):
    expected_version: int = Field(ge=1)
    horizon: int = Field(default=5, ge=1, le=60)


@router.post("/signals/{signal_id}/outcome")
def outcome(request: Request, signal_id: str, body: OutcomeRequest):
    ledger = _ledger(request)
    signal = ledger.get_signal(signal_id)
    if signal is None:
        raise HTTPException(404, "研究信号不存在")
    repo = request.app.state.repo
    start = date.fromisoformat(signal["signal_date"])
    cutoff = outcome_cutoff()
    bars = repo.get_daily_asset(
        signal["asset_type"], signal["symbol"], start - timedelta(days=60), cutoff
    )
    result = evaluate_outcome(bars, signal["signal_date"], cutoff, body.horizon)
    if result["status"] == "pending":
        return {"signal": signal, "outcome": result}
    try:
        updated = ledger.transition_signal(
            signal_id,
            "evaluated",
            expected_version=body.expected_version,
            reason=f"完成 {body.horizon} 根日线观察",
            outcome=result,
        )
    except ValueError as exc:
        raise HTTPException(409, "记录已更新或不允许后验评估,请刷新") from exc
    return {"signal": updated, "outcome": result}


@router.get("/reports/compare")
def compare(request: Request, first: str, second: str):
    from app.services import stock_reports

    reports = {r["id"]: r for r in stock_reports.list_reports()}
    a, b = reports.get(first), reports.get(second)
    if not a or not b:
        raise HTTPException(404, "报告不存在或已过保留期")
    if a["symbol"] != b["symbol"]:
        raise HTTPException(422, "请选择同一标的的两份报告")

    # Older Markdown-only reports remain comparable; no fake score for missing schema.
    def view(report):
        artifact = report.get("artifact") or {}
        return {
            "id": report["id"],
            "created_at": report.get("created_at"),
            "content": report["content"],
            "thesis": artifact.get("thesis"),
            "data_quality": artifact.get("data_quality"),
        }

    return {"symbol": a["symbol"], "first": view(a), "second": view(b)}


class BatchRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["quick", "standard", "full"] = "standard"
    skill_ids: list[str] = Field(default_factory=list, max_length=3)
    request_id: str = Field(min_length=1, max_length=100)


@router.get("/batches")
def batches(request: Request):
    return {"batches": _ledger(request).list_batches()}


@router.post("/batches", status_code=202)
async def batch(request: Request, body: BatchRequest):
    import re

    from app.services.research_jobs import claim_batch, start_batch
    from app.services.research_skills import select_skills

    if any(not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", symbol) for symbol in body.symbols):
        raise HTTPException(422, "请输入带交易所后缀的代码, 例如 000001.SZ")
    repo = request.app.state.repo
    try:
        select_skills(load_skills(repo.store.data_dir)[0], body.skill_ids, "")
        result, reused = claim_batch(
            repo.store.data_dir, body.symbols, body.mode, body.skill_ids, body.request_id
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not reused:
        start_batch(repo, repo.store.data_dir, result["id"])
    return {"batch": result, "reused": reused}


@router.get("/portfolio-risk")
def portfolio_risk(request: Request):
    from app.services.portfolio_risk import portfolio_risk

    return portfolio_risk(request.app.state.repo)


@router.get("/trajectory-metrics")
def trajectory_metrics(request: Request):
    runs = _ledger(request).list_runs()
    terminal = [r for r in runs if r["status"] != "running"]
    durations = [
        e["duration_ms"]
        for r in runs
        for e in r.get("trajectory", [])
        if e.get("status") != "started" and isinstance(e.get("duration_ms"), (int, float))
    ]
    return {
        "runs": len(runs),
        "terminal_runs": len(terminal),
        "success_ratio": sum(r["status"] == "succeeded" for r in terminal) / len(terminal)
        if terminal
        else None,
        "degraded_runs": sum(r["status"] == "degraded" for r in terminal),
        "timeout_stages": sum(
            e.get("failure_code") == "timeout" for r in runs for e in r.get("trajectory", [])
        ),
        "average_stage_ms": sum(durations) / len(durations) if durations else None,
        "scope": "最近 200 条任务的运行质量, 不代表预测准确率",
    }
