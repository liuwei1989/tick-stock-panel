"""驾驶舱 API — 一屏聚合数据健康/主线认证/环境/工作流/提醒。

- GET /api/cockpit/overview  驾驶舱总览 (前端 Cockpit 页消费)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.services.cockpit import cockpit_overview

router = APIRouter(prefix="/api/cockpit", tags=["cockpit"])


def _data_dir(request: Request):
    return request.app.state.repo.store.data_dir


@router.get("/overview")
def overview(request: Request):
    result = cockpit_overview(_data_dir(request))
    from app.services.daily_brief import load
    result["daily_brief"] = load(_data_dir(request))
    return result


@router.post("/daily-brief")
async def daily_brief(request: Request):
    from app.services.daily_brief import generate

    return await generate(request.app.state.repo, _data_dir(request), force=True)


@router.get("/ai-report/{post_id}")
def ai_report_detail(request: Request, post_id: int):
    """AI 盘前/收盘报告正文 (zzshare, 带磁盘缓存)。"""
    from app.services.zzshare_extra import fetch_ai_report_detail
    try:
        return fetch_ai_report_detail(post_id, _data_dir(request))
    except Exception as e:
        raise HTTPException(502, f"获取 AI 报告正文失败: {e}") from e
