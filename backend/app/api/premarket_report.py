"""盘前结构化研报 API。

- GET  /api/premarket-report           最新一份 (当日优先, 否则最近一份)
- GET  /api/premarket-report/list      历史列表
- GET  /api/premarket-report/context   规则版上下文 (结构化, 生成前预览)
- POST /api/premarket-report/generate  流式 AI 研报 (NDJSON; AI 未配置降级规则版)
"""
from __future__ import annotations

import json as _json
from datetime import date as date_cls, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services import premarket_report as pr

router = APIRouter(prefix="/api/premarket-report", tags=["premarket-report"])


def _data_dir(request: Request):
    return request.app.state.repo.store.data_dir


def _j(o) -> str:
    return _json.dumps(o, ensure_ascii=False)


@router.get("")
def latest(request: Request, date: str | None = Query(None)):
    """最新一份研报 (指定 date 取当日, 否则当日优先/最近一份)。"""
    reports = pr.list_reports(_data_dir(request))
    if date:
        for r in reports:
            if r.get("as_of") == date:
                return r
        raise HTTPException(404, f"未找到 {date} 的研报")
    return reports[0] if reports else pr.build_structured(_data_dir(request))


@router.get("/list")
def report_list(request: Request, limit: int = Query(30, ge=1, le=60)):
    return {"reports": pr.list_reports(_data_dir(request))[:limit]}


@router.get("/context")
def context(request: Request, date: str | None = Query(None)):
    """规则版结构化上下文 (前端生成前预览)。"""
    as_of = None
    if date:
        try:
            as_of = date_cls.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, f"date 格式应为 YYYY-MM-DD, 收到: {date}")
    return pr.build_structured(_data_dir(request), as_of)


class GenerateIn(BaseModel):
    date: str | None = None
    focus: str = ""


@router.post("/generate")
async def generate(request: Request, req: GenerateIn):
    """流式 AI 盘前研报 (NDJSON 协议, 与大盘复盘一致)。

    协议行: {"type":"meta","as_of","summary","fallback"} /
             {"type":"delta","content"} / {"type":"error","message"} / {"type":"done","report"}
    AI 未配置或素材不可用 → 直接返回规则版 (fallback)。
    """
    data_dir = _data_dir(request)
    as_of = None
    if req.date:
        try:
            as_of = date_cls.fromisoformat(req.date)
        except ValueError:
            raise HTTPException(400, f"date 格式应为 YYYY-MM-DD, 收到: {req.date}")

    structured = pr.build_structured(data_dir, as_of)
    if not structured["available"]:
        return {"ok": False, "message": structured["summary"]}

    from app.services.ai_provider import ai_configured
    if not ai_configured():
        pr.save_report(data_dir, structured)
        return {"ok": True, "fallback": True, "report": structured}

    prompt = pr.build_ai_prompt(data_dir, as_of, req.focus)
    if not prompt:
        return {"ok": False, "message": "素材不足, 无法生成 AI 研报"}

    async def stream_gen():
        from app.services.ai_provider import stream_ai_text
        meta = {"type": "meta", "as_of": structured["as_of"],
                "summary": structured["summary"], "fallback": False}
        yield _j(meta) + "\n"
        try:
            chunks: list[str] = []
            async for delta in stream_ai_text(prompt, max_tokens=2400):
                chunks.append(delta)
                yield _j({"type": "delta", "content": delta}) + "\n"
            content = "".join(chunks)
            report = dict(structured)
            report.update({"ai": True, "content": content,
                           "created_at": datetime.now().isoformat(timespec="seconds")})
            pr.save_report(data_dir, report)
            yield _j({"type": "done", "report": report}) + "\n"
        except Exception as e:  # noqa: BLE001
            yield _j({"type": "error", "message": str(e)}) + "\n"

    return StreamingResponse(stream_gen(), media_type="application/x-ndjson")
