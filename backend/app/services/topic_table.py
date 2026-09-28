"""题材表格化分析 — 题材列表 / 成分股 / OCR 导入 / 回测入口。

数据源:
- 主线时序 (mainline_history): 题材集合 + 强度/涨停/连板/身位股 → 表格主表
- ext 概念数据 (settings ext config, 若已拉取): 题材→成分股映射 (来源优先)
- 用户维护: data/topics/{topic}.json (OCR 导入/手工保存的成分股, 合并展示)

形态: GET /api/topic-table 表格行 → 点行展开成分股 (GET /api/topic-table/{topic}/members)
      → 成分股可直接进入个股回测 (前端复用 /api/backtest)。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import polars as pl

from app.services.cockpit import mainline_certification

logger = logging.getLogger(__name__)

TOPICS_DIR = "topics"


# ───────────────────────── 表格主表 ─────────────────────────

def get_topic_table(data_dir: Path) -> dict:
    """题材表格: 每题材 强度/涨停/连板/持续性/身位股/认证级别 + 成分股数。"""
    mainline = mainline_certification(data_dir)
    if not mainline.get("available"):
        return {
            "available": False,
            "detail": mainline.get("detail", "主线时序未生成 (需先构建 enriched)"),
            "rows": [],
        }

    rows: list[dict] = []
    for i in mainline.get("items", []):
        members = _saved_members(data_dir, i["member"])
        rows.append({
            "member": i["member"],
            "level": i["level"],
            "score": i["score"],
            "avg5_score": i["avg5_score"],
            "streak_days": i["streak_days"],
            "limit_up_count": i["limit_up_count"],
            "max_boards": i["max_boards"],
            "leader_symbol": i["leader_symbol"],
            "latest_date": i["latest_date"],
            "custom_members": members,
            "member_count": len(members),
        })
    rows.sort(key=lambda r: (r["level"] == "gold", r["streak_days"], r["score"]), reverse=True)
    return {
        "available": True,
        "as_of": mainline.get("as_of"),
        "gold_count": mainline.get("gold_count", 0),
        "rows": rows,
    }


# ───────────────────────── 成分股 ─────────────────────────

def _ext_members(data_dir: Path, topic: str) -> list[str]:
    """尝试从 ext 概念数据取该题材成分股 (kline_ext/{config}/...)。"""
    # ext 数据目录: data/kline_ext/<config_id>/date=YYYY-MM-DD.parquet, 含 concept 列
    ext_root = data_dir / "kline_ext"
    if not ext_root.exists():
        return []
    found: list[str] = []
    for cfg_dir in ext_root.iterdir():
        if not cfg_dir.is_dir():
            continue
        try:
            df = pl.read_parquet(cfg_dir)
        except Exception:  # noqa: BLE001
            continue
        col = next((c for c in ("concept", "concepts", "theme") if c in df.columns), None)
        if col is None:
            continue
        sym_col = next((c for c in ("symbol", "thscode") if c in df.columns), None)
        if sym_col is None:
            continue
        hit = df.filter(pl.col(col).cast(pl.Utf8).str.contains(re.escape(topic), literal=False))
        if not hit.is_empty():
            found.extend(hit[sym_col].to_list())
    # 去重保序
    return list(dict.fromkeys(found))


def _saved_members(data_dir: Path, topic: str) -> list[str]:
    p = data_dir / TOPICS_DIR / f"{_safe_name(topic)}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("symbols", [])
    except Exception:  # noqa: BLE001
        return []


def _safe_name(topic: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]", "_", topic)


def get_topic_members(data_dir: Path, topic: str) -> dict:
    """题材成分股: ext 数据 + 用户维护合并, 标注来源。"""
    ext = _ext_members(data_dir, topic)
    saved = _saved_members(data_dir, topic)
    # 身位股 (来自 mainline) 强制置顶
    mainline = mainline_certification(data_dir)
    leader = None
    if mainline.get("available"):
        for i in mainline.get("items", []):
            if i["member"] == topic:
                leader = i["leader_symbol"]
                break
    merged = list(dict.fromkeys([x for x in ([leader] if leader else []) + ext + saved if x]))
    return {
        "topic": topic,
        "members": [{"symbol": s, "source": _source_of(s, leader, ext, saved)} for s in merged],
        "ext_count": len(ext),
        "custom_count": len(saved),
        "leader_symbol": leader,
    }


def _source_of(symbol: str, leader: str | None, ext: list[str], saved: list[str]) -> str:
    if leader and symbol == leader:
        return "身位股"
    if symbol in saved:
        return "自定义"
    return "ext"


# ───────────────────────── OCR 导入成分股 ─────────────────────────

def import_topic_image(
    data_dir: Path,
    topic: str,
    image_bytes: bytes,
    *,
    existing_symbols: set[str] | None = None,
) -> dict:
    """OCR 识别题材成分股截图 → 候选列表 (不直接写入)。"""
    from app.services.watchlist_ocr import import_watchlist_image

    try:
        return import_watchlist_image(
            image_bytes, data_dir,
            existing_symbols=existing_symbols or set(_saved_members(data_dir, topic)),
        )
    except RuntimeError as e:
        return {"ok": False, "message": str(e)}


def save_topic_members(data_dir: Path, topic: str, symbols: list[str]) -> dict:
    """保存用户确认后的题材成分股 (覆盖写)。"""
    p = data_dir / TOPICS_DIR
    p.mkdir(parents=True, exist_ok=True)
    payload = {"topic": topic, "symbols": list(dict.fromkeys(symbols)),
               "updated_at": _now_iso()}
    (p / f"{_safe_name(topic)}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


from datetime import datetime


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
