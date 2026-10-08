"""Register the research API through the supported backend extension contract."""

from app.api.research import router
from app.extensions import BACKEND_EXTENSION_API_VERSION
from app.services.research import ResearchLedger

EXTENSION_ID = "research.workbench"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar):
    registrar.include_router(router)


def startup(context):
    # A process restart does not pretend interrupted LLM work succeeded.
    ledger = ResearchLedger(context.data_dir)
    for run in ledger.list_runs():
        if run["status"] == "running":
            ledger.finish_run(run["id"], "interrupted")
    for batch in ledger.list_batches():
        if batch["status"] in {"running", "queued"}:
            ledger.update_batch(batch["id"], status="interrupted")
