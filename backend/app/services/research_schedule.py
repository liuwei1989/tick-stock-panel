"""Opt-in post-close research, driven by the application's existing scheduler."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.market_time import CN_TZ, cn_now
from app.services.research import ResearchLedger
from app.services.research_jobs import claim_batch, start_batch
from app.services.research_skills import load_skills, select_skills
from app.services.trading_day import is_trading_day


class ScheduleConfig(BaseModel):
    enabled: bool = False
    symbols: list[str] = Field(default_factory=list, max_length=20)
    hour: int = Field(default=18, ge=15, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    mode: Literal["quick", "standard", "full"] = "standard"
    skill_ids: list[str] = Field(default_factory=lambda: ["auto"], max_length=3)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.enabled and not self.symbols:
            raise ValueError("启用定时研究前请指定标的")
        if any(not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", s) for s in self.symbols):
            raise ValueError("请输入带交易所后缀的代码, 例如 000001.SZ")
        if self.hour == 15 and self.minute < 35:
            raise ValueError("定时研究应在 15:35 之后运行")
        self.symbols = list(dict.fromkeys(self.symbols))
        return self


def get_config(data_dir):
    return ScheduleConfig.model_validate(ResearchLedger(data_dir).get_setting("schedule") or {})


def save_config(data_dir, config: ScheduleConfig):
    select_skills(load_skills(data_dir)[0], config.skill_ids, "")
    ResearchLedger(data_dir).set_setting("schedule", config.model_dump())
    return config


def _missing_daily_data(repo, symbols, day):
    missing = []
    for symbol in symbols:
        frame = repo.get_daily_asset(repo.resolve_asset_type(symbol), symbol, day, day)
        if frame.is_empty() or not {"date", "close"}.issubset(frame.columns):
            missing.append(symbol)
            continue
        row = frame.tail(1).to_dicts()[0]
        price = row.get("close")
        if (
            str(row["date"])[:10] != day.isoformat()
            or not isinstance(price, (int, float))
            or not math.isfinite(price)
            or price <= 0
        ):
            missing.append(symbol)
    return missing


async def tick(repo, *, now: datetime | None = None):
    data_dir = repo.store.data_dir
    config = await asyncio.to_thread(get_config, data_dir)
    if not config.enabled:
        return {"status": "disabled"}
    now = now or cn_now()
    now = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    ledger = ResearchLedger(data_dir)
    result = {"at": now.isoformat(), "status": "waiting"}
    if (now.hour, now.minute) < (config.hour, config.minute):
        return result
    # Unknown calendar cannot safely authorize an automatic paid analysis.
    verdict = await asyncio.to_thread(is_trading_day, now)
    if verdict is not True:
        result["status"] = "holiday" if verdict is False else "calendar_unknown"
    else:
        try:
            missing = await asyncio.to_thread(_missing_daily_data, repo, config.symbols, now.date())
        except Exception:
            result["status"] = "data_unavailable"
            await asyncio.to_thread(ledger.set_setting, "schedule_status", result)
            return result
        if missing:
            result.update(status="data_pending", missing_symbols=missing)
            await asyncio.to_thread(ledger.set_setting, "schedule_status", result)
            return result
        try:
            batch, reused = await asyncio.to_thread(
                claim_batch,
                data_dir,
                config.symbols,
                config.mode,
                config.skill_ids,
                "scheduled-research:" + now.date().isoformat(),
            )
        except ValueError:
            result["status"] = "busy_or_changed"
        else:
            result.update(status="already_run" if reused else "started", batch_id=batch["id"])
            if not reused:
                # Positional flag: the batch job persists the brief and pushes it once research finishes.
                start_batch(repo, data_dir, batch["id"], True)
    await asyncio.to_thread(ledger.set_setting, "schedule_status", result)
    return result
