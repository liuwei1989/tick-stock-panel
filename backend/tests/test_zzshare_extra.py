"""zzshare 扩展数据 (题材/涨停/龙虎榜/情绪) 落盘与驾驶舱聚合测试。"""
from pathlib import Path

from app.config import settings
from app.services import zzshare_extra
from app.services.cockpit import cockpit_overview


def _has_data() -> bool:
    d = settings.data_dir
    return (d / "topic_rank").exists() and (d / "uplimit").exists()


def test_zzshare_summaries_available():
    """摘要函数: 数据落盘后 available=True 且有真实内容。"""
    if not _has_data():
        # 无本地数据时降级为不可用 (不强制网络)
        assert not zzshare_extra.topics_summary(settings.data_dir)["available"]
        return
    t = zzshare_extra.topics_summary(settings.data_dir)
    assert t["available"] is True
    assert t["date"]
    assert isinstance(t["top"], list)
    if t["top"]:
        p = t["top"][0]
        assert p["plate_name"]
        assert "score" in p

    u = zzshare_extra.uplimit_summary(settings.data_dir)
    assert u["available"] is True
    assert u["date"]
    assert u["count"] >= 0

    l = zzshare_extra.lhb_summary(settings.data_dir)
    assert l["available"] is True
    assert l["count"] >= 0
    for b in l["top_net_buy"]:
        assert b["name"]
        assert b["net"] > 0

    s = zzshare_extra.sentiment_summary(settings.data_dir)
    assert s["available"] is True
    assert s["date"]
    assert s["p_close"] is not None


def test_cockpit_overview_has_zzshare_block():
    """驾驶舱聚合: overview 包含 zzshare 四区块且结构完整。"""
    r = cockpit_overview(settings.data_dir)
    assert "zzshare" in r
    zz = r["zzshare"]
    for key in ("topics", "uplimit", "lhb", "sentiment"):
        assert key in zz
        assert "available" in zz[key]
    # 数据健康层包含新四层
    keys = [l["key"] for l in r["health"]["layers"]]
    for key in ("topic_rank", "uplimit", "lhb", "sentiment"):
        assert key in keys
    assert r["status"] in ("ok", "attention")


def test_concept_ext_data_loaded():
    """概念成分 ext_data/zzshare_concept: config + timeseries parquet 存在。"""
    d = settings.data_dir / "ext_data" / "zzshare_concept"
    cfg = d / "config.json"
    if not cfg.exists():
        # 无本地数据时跳过 (不强制网络)
        assert True
        return
    import json
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    assert raw["mode"] == "timeseries"
    assert any(f.get("name") == "concept" for f in raw["fields"])
    files = list((d / "timeseries").glob("*.parquet"))
    assert files
    import polars as pl
    df = pl.read_parquet(files[-1])
    assert "symbol" in df.columns and "concept" in df.columns
    assert df.height > 0


def test_sentiment_crosscheck_with_regime():
    """情绪交叉核验: sentiment 有数据时返回最近 5 日与 regime 对照。"""
    s = zzshare_extra.sentiment_summary(settings.data_dir)
    if not s["available"]:
        assert True
        return
    rows = s.get("crosscheck") or []
    assert rows
    r = rows[-1]
    assert "date" in r and "sentiment_pct" in r and "limit_up" in r


def test_topic_rank_files_are_json():
    """落盘文件: topic_rank/uplimit/lhb 均为合法 JSON 且结构完整。"""
    d = settings.data_dir
    for sub in ("topic_rank", "uplimit", "lhb"):
        files = sorted((d / sub).glob("*.json")) if (d / sub).exists() else []
        if not files:
            continue
        import json
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        assert "date" in data
        assert isinstance(data["date"], str) and len(data["date"]) == 8
        if sub == "topic_rank":
            assert isinstance(data["plates"], list)
        elif sub == "uplimit":
            assert isinstance(data["stocks"], list)
        elif sub == "lhb":
            assert isinstance(data["list"], list)
