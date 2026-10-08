import asyncio
import json

import pytest

from app.services.research import ResearchLedger
from app.services.research_jobs import claim_batch, run_batch


def test_batch_claim_prevents_duplicate_request(tmp_path):
    first, reused = claim_batch(tmp_path, ["000001.SZ"], "standard", ["bull_trend"], "request1")
    assert reused is False
    second, reused = claim_batch(tmp_path, ["000001.SZ"], "standard", ["bull_trend"], "request1")
    assert reused is True and first["id"] == second["id"]
    with pytest.raises(ValueError):
        claim_batch(tmp_path, ["600519.SH"], "standard", [], "request1")


@pytest.mark.asyncio
async def test_batch_isolates_failure_persists_progress_and_caps_concurrency(tmp_path):
    batch, _ = claim_batch(tmp_path, ["000001.SZ", "600519.SH"], "standard", [], "request2")
    calls = []

    async def analyze(repo, data_dir, symbol, **kwargs):
        calls.append(symbol)
        if symbol.startswith("000001"):
            yield json.dumps({"type": "error", "message": "failed"})
        else:
            yield json.dumps({"type": "run", "run_id": "run2"})
            await asyncio.sleep(0)
            yield json.dumps({"type": "done"})

    await run_batch(None, tmp_path, batch["id"], analyze=analyze)
    stored = ResearchLedger(tmp_path).get_batch(batch["id"])
    assert stored["status"] == "degraded"
    assert len(stored["items"]) == 2
    assert stored["items"][0]["status"] == "failed"
    assert stored["items"][1]["status"] == "succeeded"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_batch_with_no_completed_reports_is_failed(tmp_path):
    batch, _ = claim_batch(tmp_path, ["000001.SZ"], "standard", [], "failed-request")

    async def analyze(*args, **kwargs):
        yield json.dumps({"type": "error", "message": "capacity unavailable"})

    await run_batch(None, tmp_path, batch["id"], analyze=analyze)
    assert ResearchLedger(tmp_path).get_batch(batch["id"])["status"] == "failed"
