"""Validated research Skill catalog, deterministic routing and bounded execution."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ResearchSkill(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    display_name: str = Field(min_length=1, max_length=100)
    category: Literal["trend", "pattern", "reversal", "framework"]
    description: str = Field(default="", max_length=1000)
    instructions: str = Field(min_length=1, max_length=20000)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    default_priority: int = 100
    core_rules: list[int] = Field(default_factory=list, max_length=30)
    required_tools: list[str] = Field(default_factory=list, max_length=30)
    allowed_tools: list[str] = Field(default_factory=list, max_length=30)
    default_active: bool = False
    default_router: bool = False
    market_regimes: list[str] = Field(default_factory=list, max_length=30)
    user_invocable: bool = True
    disable_model_invocation: bool = False
    source: Literal["builtin", "custom"] = "custom"


def load_skills(data_dir: Path) -> tuple[dict[str, ResearchSkill], list[str]]:
    catalog: dict[str, ResearchSkill] = {}
    errors: list[str] = []
    builtin_folder = Path(__file__).parents[1] / "research_skills"
    for folder in (builtin_folder, data_dir / "research_skills"):
        for path in sorted(folder.glob("*.yaml"))[:100]:
            try:
                if path.stat().st_size > 65536:
                    raise ValueError("file too large")
                skill = ResearchSkill.model_validate(yaml.safe_load(path.read_text("utf-8")))
                # Source is assigned by the loader, never trusted from custom YAML.
                skill.source = "builtin" if folder == builtin_folder else "custom"
                if skill.name in catalog:
                    raise ValueError("duplicate ID")
                catalog[skill.name] = skill
            except (OSError, ValueError, ValidationError, yaml.YAMLError):
                # No file contents/absolute paths in user-visible diagnostics.
                errors.append(f"{path.name}: 无效研究 Skill 或重复 ID")
    return dict(
        sorted(catalog.items(), key=lambda item: (item[1].default_priority, item[0]))
    ), errors


def select_skills(
    catalog: dict[str, ResearchSkill], requested: list[str], focus: str
) -> list[ResearchSkill]:
    if not requested:
        return []
    if requested == ["auto"]:
        candidates = sorted(
            (s for s in catalog.values() if s.user_invocable and not s.disable_model_invocation),
            key=lambda s: (s.default_priority, s.name),
        )
        matches = [
            s
            for s in candidates
            if any(
                a and a.casefold() in focus.casefold() for a in [s.name, s.display_name, *s.aliases]
            )
        ]
        defaults = [s for s in candidates if s.default_active]
        requested = [s.name for s in (matches[:3] or defaults[:1] or candidates[:1])]
    if len(requested) > 3:
        raise ValueError("每次最多运行 3 个研究 Skill")
    if any(key not in catalog for key in requested):
        raise ValueError("未知研究 Skill")
    if any(not catalog[key].user_invocable for key in requested):
        raise ValueError("研究 Skill 不允许用户调用")
    return [catalog[key] for key in dict.fromkeys(requested)]


async def run_skills(
    skills: list[ResearchSkill], evidence: str, invoke, *, concurrency=2, timeout=90.0
) -> list[dict]:
    semaphore = asyncio.Semaphore(max(1, min(3, concurrency)))

    async def run(skill):
        async with semaphore:
            started = time.monotonic()
            result = {
                "stage": f"skill:{skill.name}",
                "label": skill.display_name,
                "status": "completed",
                "content": "",
            }
            try:
                system = (
                    "你是研究 Skill 分析员。仅分析给定证据,不执行任何代码或交易。"
                    "下面的框架提到的工具名称只是上游文档,不是可调用工具;不要声称已调用。"
                    "数据缺失时明确列出缺口。所有建议改写为待观察条件。"
                    "输出结论、支持与反对证据、失效条件和缺失数据;不得虚构数值或来源。\n"
                    + skill.display_name
                    + "\n"
                    + skill.instructions
                )
                result["content"] = (
                    await asyncio.wait_for(invoke(system, evidence), timeout=timeout)
                )[:10000]
                if not result["content"].strip():
                    raise ValueError("empty result")
            except Exception as exc:
                result.update(
                    status="degraded",
                    failure_code="timeout" if isinstance(exc, TimeoutError) else "provider_error",
                )
            result["duration_ms"] = round((time.monotonic() - started) * 1000)
            return result

    return await asyncio.gather(*(run(skill) for skill in skills))
