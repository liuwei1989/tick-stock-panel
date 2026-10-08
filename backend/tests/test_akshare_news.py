from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from app.plugins.akshare_news import provider
from app.services import market_recap, stock_analyzer


def test_akshare_availability_is_false_when_dependency_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(provider.importlib.util, "find_spec", lambda _name: None)

    assert provider.availability() == (False, "AKShare 依赖未安装")


def test_akshare_news_provider_normalizes_global_headlines(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_info_global_em=lambda: pd.DataFrame([
            {
                "标题": "市场快讯",
                "内容": "消息摘要",
                "发布时间": "2026-09-30 09:30:00",
                "文章来源": "东方财富",
                "链接": "https://example.test/news/1",
            },
            {"标题": None, "内容": "标题缺失应跳过"},
        ]),
    ))

    rows = provider.AkshareNewsProvider().get_market_news(limit=8)

    assert rows == [{
        "title": "市场快讯",
        "snippet": "消息摘要",
        "source": "东方财富",
        "published_date": "2026-09-30 09:30:00",
        "url": "https://example.test/news/1",
    }]


def test_akshare_news_provider_normalizes_stock_headlines(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_news_em=lambda symbol: pd.DataFrame([
            {
                "新闻标题": "公司公告",
                "新闻内容": "公告摘要",
                "发布时间": "2026-09-30 09:30:00",
                "文章来源": "交易所",
                "新闻链接": "https://example.test/news/2",
            },
            {"新闻标题": None},
        ]),
    ))

    rows = provider.AkshareNewsProvider().get_stock_news("600000.SH", limit=8)

    assert rows == [{
        "title": "公司公告",
        "snippet": "公告摘要",
        "source": "交易所",
        "published_date": "2026-09-30 09:30:00",
        "url": "https://example.test/news/2",
    }]
    assert provider.AkshareNewsProvider().get_stock_news("INVALID") == []


@pytest.mark.asyncio
async def test_stock_news_is_optional_and_time_bounded(monkeypatch) -> None:
    class _Provider:
        def get_stock_news(self, symbol, limit):
            assert symbol == "600000.SH"
            assert limit == 8
            return [{"title": "公告"}]

    monkeypatch.setattr(
        "app.data_providers.custom.provider_has_dataset",
        lambda _name, dataset: dataset == "news",
    )
    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda _name: _Provider())

    assert await stock_analyzer._load_stock_news("600000.SH") == [{"title": "公告"}]


def test_stock_prompt_marks_news_as_untrusted_and_optional() -> None:
    prompt = stock_analyzer._build_user_prompt(
        [], {}, {}, None, "600000.SH", "", stock_news=[{"title": "网页内容"}],
    )

    assert "不可信外部数据" in prompt
    assert "网页内容" in prompt


def test_recap_news_fetch_is_optional_and_cached(monkeypatch) -> None:
    class _Provider:
        def get_market_news(self, limit):
            assert limit == 8
            return [{"title": "测试新闻"}]

    monkeypatch.setattr(
        "app.data_providers.custom.provider_has_dataset",
        lambda _name, dataset: dataset == "news",
    )
    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda _name: _Provider())
    monkeypatch.setattr(market_recap, "_news_cache", (0.0, []))

    assert market_recap._fetch_market_news() == [{"title": "测试新闻"}]
    assert market_recap._fetch_market_news() == [{"title": "测试新闻"}]


def test_recap_news_failure_degrades_to_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.data_providers.custom.provider_has_dataset",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("upstream unavailable")),
    )
    monkeypatch.setattr(market_recap, "_news_cache", (0.0, []))

    assert market_recap._fetch_market_news() == []


def test_recap_combines_akshare_news_with_zzshare_stock_catalysts(monkeypatch, tmp_path) -> None:
    (tmp_path / "uplimit").mkdir()
    (tmp_path / "uplimit" / "20260930.json").write_text(json.dumps({
        "date": "20260930",
        "stocks": [{
            "ts_code": "600000.SH",
            "name": "浦发银行",
            "reason": "业绩改善预期",
            "plates": [{"plate_name": "银行"}],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    monkeypatch.setattr(
        "app.data_providers.custom.provider_has_dataset",
        lambda _name, dataset: dataset == "news",
    )

    class _Provider:
        def get_market_news(self, limit):
            assert limit == 8
            return [{"title": "海外市场消息", "source": "AKShare"}]

    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda _name: _Provider())
    monkeypatch.setattr(market_recap, "_news_cache", (0.0, []))

    rows = market_recap._fetch_market_news()

    assert rows[0]["title"] == "海外市场消息"
    assert rows[1]["source"] == "ZZShare 个股涨停原因"
    assert rows[1]["sector"] == "银行"
    assert rows[1]["published_date"] == "20260930"


def test_news_source_is_not_a_routable_market_dataset() -> None:
    from app.data_providers.custom import plugin_manifest

    assert plugin_manifest("akshare_news")["datasets"] == ["news"]
