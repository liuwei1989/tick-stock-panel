"""题材表格化分析 API。

- GET  /api/topic-table                    题材表格主表
- GET  /api/topic-table/{topic}/members    题材成分股 (ext + 自定义合并)
- POST /api/topic-table/{topic}/ocr-import 上传截图 OCR 识别候选 (multipart)
- PUT  /api/topic-table/{topic}/members    保存成分股 (覆盖写, 用户确认后)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, UploadFile

from app.services import topic_table as tt

router = APIRouter(prefix="/api/topic-table", tags=["topic-table"])


def _data_dir(request: Request):
    return request.app.state.repo.store.data_dir


@router.get("")
def topic_table(request: Request):
    return tt.get_topic_table(_data_dir(request))


@router.get("/{topic}/members")
def members(request: Request, topic: str):
    if not topic.strip():
        raise HTTPException(400, "topic 不能为空")
    return tt.get_topic_members(_data_dir(request), topic.strip())


@router.post("/{topic}/ocr-import")
async def ocr_import(request: Request, topic: str, file: UploadFile):
    """OCR 识别题材成分股截图 → 候选列表 (不写入; 前端展示后 PUT 保存)。"""
    if not topic.strip():
        raise HTTPException(400, "topic 不能为空")
    data = await file.read()
    if not data:
        raise HTTPException(400, "图片为空")
    result = tt.import_topic_image(_data_dir(request), topic.strip(), data)
    if not result.get("ok") and "message" in result:
        raise HTTPException(400, result["message"])
    return result


@router.put("/{topic}/members")
async def save_members(request: Request, topic: str, payload: dict):
    from pydantic import BaseModel

    class In(BaseModel):
        symbols: list[str]

    body = In(**payload)
    return tt.save_topic_members(_data_dir(request), topic.strip(), body.symbols)
