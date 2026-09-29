"""zzshare 竞价兜底: fuyao 未配置时涨停竞价名单可用。"""
from app.config import settings
from app.services.auction_benchmark import get_auction_benchmark


def test_zzshare_auction_fallback():
    """本地有 uplimit 数据时, 竞价定盘返回真实名单而非降级。"""
    ul = settings.data_dir / "uplimit"
    if not ul.exists() or not list(ul.glob("*.json")):
        assert True  # 无本地数据跳过 (不强制网络)
        return
    r = get_auction_benchmark(settings.data_dir)
    assert r["state"] in ("ok", "fallback_prev")
    items = r.get("items") or []
    assert items
    for i in items[:3]:
        assert i["thscode"]
        assert i["name"]
    # 去重
    codes = [i["thscode"] for i in items]
    assert len(codes) == len(set(codes))
