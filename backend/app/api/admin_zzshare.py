"""zzshare 数据源后台管理 API — token 池热配置。

GET  /api/admin/zzshare-tokens   读取当前 token 池 (脱敏)
PUT  /api/admin/zzshare-tokens   更新 token 池, 立即热生效 (不重启)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.zzshare_sync import get_tokens, set_tokens

router = APIRouter(prefix="/api/admin/zzshare-tokens", tags=["admin"])


class TokenList(BaseModel):
    tokens: list[str] = Field(min_length=1)


def _mask(tok: str) -> str:
    if len(tok) <= 12:
        return "***"
    return f"{tok[:6]}...{tok[-4:]}"


@router.get("")
def read_tokens() -> dict:
    tokens = get_tokens()
    return {
        "count": len(tokens),
        "tokens": [_mask(t) for t in tokens],
    }


@router.put("")
def update_tokens(body: TokenList) -> dict:
    cleaned = [t.strip() for t in body.tokens if t and t.strip()]
    if not cleaned:
        raise HTTPException(status_code=422, detail="tokens 不能为空")
    try:
        n = set_tokens(cleaned)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "count": n, "tokens": [_mask(t) for t in cleaned]}
