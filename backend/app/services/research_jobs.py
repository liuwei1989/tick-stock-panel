"""Durable, bounded batch research using the same stock analysis service."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from app.services.research import ResearchLedger

_TASKS: set[asyncio.Task] = set()


def claim_batch(
    data_dir: Path, symbols: list[str], mode: str, skill_ids: list[str], request_id: str
):
    ledger = ResearchLedger(data_dir)
    payload = {"symbols": list(dict.fromkeys(symbols)), "mode": mode, "skill_ids": skill_ids}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    batch_id = "batch_" + hashlib.sha256(request_id.encode()).hexdigest()[:32]
    return ledger.claim_batch(batch_id, payload, fingerprint)


async def run_batch(repo, data_dir: Path, batch_id: str, *, analyze=None, push_brief: bool = False):
    from app.services.stock_analyzer import analyze_stock_stream

    analyze = analyze or analyze_stock_stream
    ledger = ResearchLedger(data_dir)
    batch = ledger.get_batch(batch_id)
    items = []
    try:
        ledger.update_batch(batch_id, status="running")
        # One symbol per batch at a time. The shared batch claim admits one active batch.
        for symbol in batch["symbols"]:
            item = {"symbol": symbol, "status": "failed"}
            try:
                async for raw in analyze(
                    repo, data_dir, symbol, mode=batch["mode"], skill_ids=batch["skill_ids"]
                ):
                    event = json.loads(raw)
                    if event["type"] == "run":
                        item["run_id"] = event["run_id"]
                    if event["type"] == "agent_stage" and event.get("status") == "degraded":
                        item["partial"] = True
                    if event["type"] == "done":
                        item["status"] = (
                            "degraded"
                            if item.get("partial") or event.get("archive_error")
                            else "succeeded"
                        )
            except Exception:
                item["status"] = "failed"
            items.append(item)
            ledger.update_batch(batch_id, items=items)
        if all(i["status"] == "succeeded" for i in items):
            status = "succeeded"
        elif any(i["status"] in {"succeeded", "degraded"} for i in items):
            status = "degraded"
        else:
            status = "failed"
        ledger.update_batch(batch_id, status=status)
        if push_brief and status in {"succeeded", "degraded"}:
            from app.services.daily_brief import generate, push

            brief = await generate(repo, data_dir, force=True)
            await asyncio.to_thread(push, brief)
    except asyncio.CancelledError:
        ledger.update_batch(batch_id, status="interrupted", items=items)
        raise
    except Exception:
        ledger.update_batch(batch_id, status="failed", items=items)


def start_batch(repo, data_dir, batch_id, push_brief: bool = False):
    task = asyncio.create_task(run_batch(repo, data_dir, batch_id, push_brief=push_brief))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def shutdown_batches():
    tasks = tuple(_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
