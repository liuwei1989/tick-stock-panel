"""驾驶舱 API — 一屏聚合数据健康/主线认证/环境/工作流/提醒。

- GET /api/cockpit/overview  驾驶舱总览 (前端 Cockpit 页消费)
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.services.cockpit import cockpit_overview

router = APIRouter(prefix="/api/cockpit", tags=["cockpit"])


def _data_dir(request: Request):
    return request.app.state.repo.store.data_dir


@router.get("/overview")
def overview(request: Request):
    return cockpit_overview(_data_dir(request))
