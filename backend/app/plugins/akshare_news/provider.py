"""AKShare-backed global financial headlines for the market recap."""
from __future__ import annotations

import importlib.util
import logging
import math

logger = logging.getLogger(__name__)


def availability() -> tuple[bool, str]:
    """Keep the optional plugin isolated when AKShare is not installed."""
    if importlib.util.find_spec("akshare") is None:
        return False, "AKShare 依赖未安装"
    return True, "ok"


class AkshareNewsProvider:
    """Fetch normalized global financial headlines from AKShare."""

    name = "akshare_news"
    builtin = True

    def __init__(self) -> None:
        from app.data_providers.custom.loader import plugin_manifest

        manifest = plugin_manifest(self.name) or {}
        self.config = type("PluginConfig", (), {
            "name": self.name,
            "display_name": manifest.get("display_name", "AKShare 财经快讯"),
            "datasets": {dataset: None for dataset in manifest.get("datasets", [])},
            "path": None,
            "builtin": True,
        })()

    def close(self) -> None:
        pass

    def get_market_news(self, limit: int = 8) -> list[dict]:
        """Return title/snippet/source/date/link records; tolerate upstream schema drift."""
        import akshare as ak

        frame = ak.stock_info_global_em()
        if frame is None or frame.empty:
            return []

        rows: list[dict] = []
        for raw in frame.head(max(1, min(int(limit), 30))).to_dict(orient="records"):
            title = _first(raw, "标题", "新闻标题", "title")
            if not title:
                continue
            rows.append({
                "title": str(title).strip(),
                "snippet": _text(_first(raw, "内容", "新闻内容", "摘要", "snippet")),
                "source": _text(_first(raw, "文章来源", "来源", "source")),
                "published_date": _text(_first(raw, "发布时间", "时间", "published_date")),
                "url": _text(_first(raw, "链接", "新闻链接", "url")),
            })
        return rows

    def get_stock_news(self, symbol: str, limit: int = 8) -> list[dict]:
        """Fetch normalized company headlines for a six-digit mainland ticker."""
        code = str(symbol).split(".", maxsplit=1)[0]
        if len(code) != 6 or not code.isdigit():
            return []
        import akshare as ak

        frame = ak.stock_news_em(symbol=code)
        if frame is None or frame.empty:
            return []
        rows: list[dict] = []
        for raw in frame.head(max(1, min(int(limit), 20))).to_dict(orient="records"):
            title = _first(raw, "新闻标题", "标题", "title")
            if not title:
                continue
            rows.append({
                "title": str(title).strip()[:300],
                "snippet": _text(_first(raw, "新闻内容", "内容", "摘要", "snippet"))[:1000],
                "source": _text(_first(raw, "文章来源", "来源", "source"))[:100],
                "published_date": _text(_first(raw, "发布时间", "时间", "published_date"))[:80],
                "url": _text(_first(raw, "新闻链接", "链接", "url"))[:1000],
            })
        return rows


def _first(row: dict, *keys: str):
    for key in keys:
        value = row.get(key)
        if value is not None and not _is_nan(value) and str(value).strip():
            return value
    return None


def _text(value) -> str:
    if value is None:
        return ""
    if _is_nan(value):
        return ""
    return str(value).strip()


def _is_nan(value) -> bool:
    try:
        return math.isnan(value)
    except (TypeError, ValueError):
        return False
