"""Write-only notification credentials and redacted delivery history."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import notification_channels as channels

router = APIRouter(prefix="/api/notification-channels", tags=["notifications"])


@router.get("")
def status():
    return {"channels": channels.channel_status()}


class ChannelConfig(BaseModel):
    values: dict[str, str] = Field(default_factory=dict, max_length=10)


@router.put("/{channel}")
def save(channel: str, body: ChannelConfig):
    try:
        channels.save_config(channel, body.values)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"channels": channels.channel_status()}


@router.get("/deliveries/history")
def history():
    return {"deliveries": channels.delivery_history()}
