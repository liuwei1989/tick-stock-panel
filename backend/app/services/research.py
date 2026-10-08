"""Versioned research artifacts and an atomic, restart-safe decision ledger.

Financial observations use the repository's adjusted close series. A signal is
an item for research/reassessment, never an order or an execution instruction.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.market_time import CN_TZ, cn_now


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    summary: str = Field(default="", max_length=4000)
    score: float | None = Field(default=None, ge=0, le=100)
    direction: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=30)
    catalysts: list[str] = Field(default_factory=list, max_length=30)
    invalidation: list[str] = Field(default_factory=list, max_length=30)
    checklist: list[str] = Field(default_factory=list, max_length=30)
    disagreement: list[str] = Field(default_factory=list, max_length=30)


class ContextBlock(BaseModel):
    id: str
    label: str
    status: Literal["available", "missing", "unknown", "not_applicable"]
    as_of: str | None = None
    count: int = 0


class AnalysisContextPack(BaseModel):
    schema_version: Literal[1] = 1
    symbol: str
    asset_type: Literal["stock", "etf", "index"]
    as_of: str
    blocks: list[ContextBlock]
    completeness_score: (
        int  # percentage of applicable input blocks present, NOT predictive confidence
    )


class DataQuality(BaseModel):
    completeness_score: int
    level: Literal["usable", "limited"] = "limited"
    limitations: list[str] = Field(default_factory=list)


class ResearchArtifact(BaseModel):
    schema_version: Literal[1] = 1
    artifact_id: str
    symbol: str
    asset_type: str
    created_at: str
    thesis: Decision
    evidence: list[ContextBlock]
    invalidation_conditions: list[str]
    next_actions: list[str]
    data_quality: DataQuality
    key_levels: dict
    parse_status: Literal["validated", "unstructured", "invalid"]


DECISION_INSTRUCTION = """\n报告正文使用 Markdown。正文后另起一行输出一个 ```json 代码块,严格包含下列字段:
{"summary":"核心结论", "score":null, "direction":"unknown", "evidence_ids":[],
"risks":[], "catalysts":[], "invalidation":[], "checklist":[], "disagreement":[]}
score 是有证据支持时的 0-100 研究评分,否则 null;不是盈利概率。
direction 仅允许 bullish/bearish/neutral/unknown。
evidence_ids 只能引用上下文质量表中的可用 id,不得编造证据;缺少支持时保留 unknown/null。
invalidation 是推翻当前结论的观察条件,checklist 是待核验事项,不是下单指令。
"""


def parse_decision(text: str) -> tuple[Decision, str]:
    candidate = text.strip()
    if "```json" in candidate:
        candidate = candidate.rsplit("```json", 1)[1].split("```", 1)[0].strip()
    elif not candidate.startswith("{"):
        return Decision(), "unstructured"
    try:
        parsed = Decision.model_validate_json(candidate)
        return parsed, "validated"
    except (ValueError, ValidationError):
        return Decision(), "invalid"


def eligible_news(news: list[dict], as_of: date) -> list[dict]:
    # Undated entries remain leads, with unknown quality. Future dated entries
    # must be excluded from both the prompt and the quality summary.
    return [
        row
        for row in news
        if str(row.get("published_date") or row.get("published_at") or "")[:10] <= as_of.isoformat()
    ]


def outcome_cutoff(now: datetime | None = None) -> date:
    now = now or cn_now()
    now = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    # Allow settlement time; a current intraday bar is not a completed observation.
    return now.date() if now.time() >= time(15, 30) else now.date() - timedelta(days=1)


def build_context(
    symbol: str,
    asset_type: str,
    rows: list[dict],
    financials: dict,
    news: list[dict],
    *,
    as_of: date,
) -> AnalysisContextPack:
    dates = [
        str(row.get("date", ""))[:10]
        for row in rows
        if row.get("date") and str(row["date"])[:10] <= as_of.isoformat()
    ]
    latest = max(dates, default=None)
    financial_count = sum(len(v) for v in financials.values() if isinstance(v, list))
    news_rows = eligible_news(news, as_of)
    blocks = [
        ContextBlock(
            id="kline",
            label="日线与技术指标",
            status="available"
            if latest == as_of.isoformat()
            else "unknown"
            if latest
            else "missing",
            as_of=latest,
            count=len(dates),
        ),
        ContextBlock(
            id="financials",
            label="财务",
            status="not_applicable"
            if asset_type != "stock"
            else "unknown"
            if financial_count
            else "missing",
            count=financial_count,
        ),
        ContextBlock(
            id="news",
            label="新闻线索",
            status="unknown" if news_rows else "missing",
            count=len(news_rows),
        ),
    ]
    applicable = [b for b in blocks if b.status != "not_applicable"]
    completeness = round(sum(b.count > 0 for b in applicable) / len(applicable) * 100)
    return AnalysisContextPack(
        symbol=symbol,
        asset_type=asset_type,
        as_of=as_of.isoformat(),
        blocks=blocks,
        completeness_score=completeness,
    )


def build_artifact(
    run_id: str,
    context: AnalysisContextPack,
    decision: Decision,
    parse_status: str,
    trajectory: list[dict],
    levels: dict,
) -> ResearchArtifact:
    decision = decision.model_copy(deep=True)
    known = {b.id for b in context.blocks if b.count > 0}
    limitations = [
        f"{b.id}:{b.status}"
        for b in context.blocks
        if b.status not in {"available", "not_applicable"}
    ]
    if parse_status != "validated":
        limitations.append(f"output:{parse_status}")
    if any(e not in known for e in decision.evidence_ids) or not decision.evidence_ids:
        limitations.append("unverified_evidence")
        decision.score = None
        decision.direction = "unknown"
        decision.evidence_ids = [e for e in decision.evidence_ids if e in known]
    if any(s.get("status") == "degraded" for s in trajectory):
        limitations.append(
            "analysis_timeout_partial"
            if any(s.get("failure_code") == "timeout" for s in trajectory)
            else "agent_partial"
        )
    return ResearchArtifact(
        artifact_id=run_id,
        symbol=context.symbol,
        asset_type=context.asset_type,
        created_at=cn_now().isoformat(),
        thesis=decision,
        evidence=context.blocks,
        invalidation_conditions=decision.invalidation or ["新数据或相反证据出现时重新研究"],
        next_actions=decision.checklist or ["核验数据日期与研究证据"],
        data_quality=DataQuality(
            completeness_score=context.completeness_score,
            level="limited" if limitations else "usable",
            limitations=limitations,
        ),
        key_levels=levels,
        parse_status=parse_status,
    )


def evaluate_outcome(bars: pl.DataFrame, signal_date: str, as_of: date, horizon: int) -> dict:
    """Observe close-to-close performance; do not represent this as a fill/backtest."""
    pending = {
        "status": "pending",
        "basis": "adjusted_close",
        "return_ratio": None,
        "observations": 0,
    }
    if bars.is_empty() or not {"date", "close"}.issubset(bars.columns):
        return pending
    rows = bars.select("date", "close").unique(subset=["date"], keep="last").sort("date").to_dicts()
    # Both prices read in the same call, so a later adjustment cannot mix price bases.
    base = next((r for r in reversed(rows) if str(r["date"])[:10] <= signal_date), None)
    future = [r for r in rows if signal_date < str(r["date"])[:10] <= as_of.isoformat()]

    def valid(v):
        return (
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
        )

    if base is None or not valid(base["close"]) or len(future) < horizon:
        return {**pending, "observations": len(future)}
    target = future[horizon - 1]
    if not valid(target["close"]):
        return {**pending, "reason": "invalid_price"}
    return {
        "status": "observed",
        "basis": "adjusted_close",
        "return_ratio": target["close"] / base["close"] - 1,
        "observations": horizon,
        "signal_date": signal_date,
        "start_date": str(base["date"])[:10],
        "end_date": str(target["date"])[:10],
        "description": "信号日或之前最近收盘至信号后第 N 根日线收盘的观察收益,非成交收益",
    }


class ResearchLedger:
    """Small SQLite ledger: atomic compare-and-swap prevents duplicate state transitions."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "user_data" / "research.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,id))"
            )

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _get(self, conn, kind, key):
        row = conn.execute(
            "SELECT payload FROM records WHERE kind=? AND id=?", (kind, key)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, conn, kind, key, value):
        conn.execute(
            "INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
            (kind, key, json.dumps(value, ensure_ascii=False, allow_nan=False)),
        )

    def _list(self, kind):
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM records WHERE kind=? ORDER BY rowid DESC LIMIT 200", (kind,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def create_run(self, run_id, symbol, mode):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._get(conn, "run", run_id):
                raise ValueError("duplicate run")
            self._put(
                conn,
                "run",
                run_id,
                {
                    "id": run_id,
                    "symbol": symbol,
                    "mode": mode,
                    "status": "running",
                    "created_at": cn_now().isoformat(),
                    "trajectory": [],
                },
            )

    def append_stage(self, run_id, stage):
        # Explicit allowlist excludes model prompts, credentials, raw exceptions.
        event = {
            k: v
            for k, v in stage.items()
            if k in {"stage", "label", "status", "duration_ms", "failure_code"}
        }
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._get(conn, "run", run_id)
            if run:
                run["trajectory"] = [*run["trajectory"], event][-80:]
                self._put(conn, "run", run_id, run)

    def finish_run(self, run_id, status, **details):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._get(conn, "run", run_id)
            if run:
                run.update(status=status, finished_at=cn_now().isoformat(), **details)
                self._put(conn, "run", run_id, run)

    def get_run(self, run_id):
        with self.connection() as conn:
            return self._get(conn, "run", run_id)

    def list_runs(self):
        return self._list("run")

    def get_setting(self, key):
        with self.connection() as conn:
            return self._get(conn, "setting", key)

    def set_setting(self, key, value):
        with self.connection() as conn:
            self._put(conn, "setting", key, value)

    def upsert_signal(self, signal_id, payload):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._get(conn, "signal", signal_id)
            if existing:
                return existing
            payload = {
                **payload,
                "id": signal_id,
                "version": 1,
                "history": [],
                "created_at": cn_now().isoformat(),
            }
            self._put(conn, "signal", signal_id, payload)
            return payload

    def list_signals(self):
        return self._list("signal")

    def get_signal(self, signal_id):
        with self.connection() as conn:
            return self._get(conn, "signal", signal_id)

    def transition_signal(self, signal_id, status, *, expected_version, reason, outcome=None):
        allowed = {
            "watching": {"review_required", "dismissed", "evaluated"},
            "review_required": {"watching", "dismissed", "evaluated"},
            "evaluated": {"review_required", "dismissed"},
            "dismissed": set(),
        }
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            signal = self._get(conn, "signal", signal_id)
            if signal is None:
                raise KeyError(signal_id)
            if signal["version"] != expected_version:
                raise ValueError("signal version conflict")
            if status not in allowed.get(signal["status"], set()):
                raise ValueError("invalid signal transition")
            signal["history"] = [
                *signal["history"],
                {
                    "from": signal["status"],
                    "to": status,
                    "reason": reason[:1000],
                    "at": cn_now().isoformat(),
                },
            ][-100:]
            signal.update(status=status, version=expected_version + 1)
            if outcome is not None:
                signal["outcome"] = outcome
            self._put(conn, "signal", signal_id, signal)
            return signal

    def claim_batch(self, batch_id, payload, fingerprint):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._get(conn, "batch", batch_id)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ValueError("request ID already used for a different batch")
                return existing, True
            active = conn.execute("SELECT payload FROM records WHERE kind='batch'").fetchall()
            if any(json.loads(row[0])["status"] in {"queued", "running"} for row in active):
                raise ValueError("已有批量研究运行, 请等待完成")
            batch = {
                **payload,
                "id": batch_id,
                "fingerprint": fingerprint,
                "status": "queued",
                "items": [],
                "created_at": cn_now().isoformat(),
            }
            self._put(conn, "batch", batch_id, batch)
            return batch, False

    def get_batch(self, batch_id):
        with self.connection() as conn:
            return self._get(conn, "batch", batch_id)

    def list_batches(self):
        return self._list("batch")

    def update_batch(self, batch_id, **fields):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            batch = self._get(conn, "batch", batch_id)
            if batch:
                batch.update(fields)
                self._put(conn, "batch", batch_id, batch)
