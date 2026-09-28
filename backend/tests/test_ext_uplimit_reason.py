"""涨停原因内置扩展表 (ext_uplimit_reason) 行为回归。

覆盖:
  - 出厂预设: enabled=False (启动不拉取, 与概念/行业 #199 同约定)
  - ensure_uplimit_reason_preset: 只创建 config 不拉数据; 已存在绝不覆盖
  - _dedupe_uplimit_reason_rows: 同股多行取首条 / field_map 前后两种键兼容 /
    代码补零 / 空行剔除 / date 透传
  - sync_uplimit_reason: 显式 day 走按日拉取并写入对应分区; day=None 按接口
    行内 date 分区; 非交易日 (0 行) 返回 0 且不落盘
  - fetch_preset 路由: ext_uplimit_reason 转发到专用同步链路
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from app.services.ext_data import ExtConfigStore
from app.services.ext_uplimit_reason import (
    UP_LIMIT_REASON_ID,
    _dedupe_uplimit_reason_rows,
    _uplimit_reason_preset,
    ensure_uplimit_reason_preset,
    sync_uplimit_reason,
)

# ---------------------------------------------------------------------------
# 预设定义
# ---------------------------------------------------------------------------

def test_preset_ships_disabled() -> None:
    """出厂 pull.enabled 必须为 False, 否则 scheduler 启动即网络拉取 (#199)。"""
    preset = _uplimit_reason_preset()
    assert preset.id == UP_LIMIT_REASON_ID
    assert preset.mode == "timeseries"
    assert preset.pull is not None
    assert preset.pull.enabled is False
    assert preset.pull.url, "禁用归禁用, 手动获取仍需 url 配方"
    assert preset.pull.date_param == "date1"


def test_ensure_preset_writes_config_only(tmp_path: Path) -> None:
    """启动只落 config.json, 不产生任何数据文件。"""
    asyncio.run(ensure_uplimit_reason_preset(tmp_path))

    store = ExtConfigStore(tmp_path)
    config = store.get(UP_LIMIT_REASON_ID)
    assert config is not None
    assert config.pull is not None and config.pull.enabled is False
    cfg_dir = tmp_path / "ext_data" / UP_LIMIT_REASON_ID
    assert (cfg_dir / "config.json").exists(), "配置未创建"
    assert not (cfg_dir / "part.parquet").exists(), "启动不得拉取数据"
    assert not (cfg_dir / "timeseries").exists(), "启动不得拉取数据"


def test_ensure_preset_keeps_existing_user_config(tmp_path: Path) -> None:
    """已存在的配置一律不动: 即使 enabled=True 也不被覆盖。"""
    asyncio.run(ensure_uplimit_reason_preset(tmp_path))
    store = ExtConfigStore(tmp_path)
    config = store.get(UP_LIMIT_REASON_ID)
    assert config is not None and config.pull is not None
    config.pull.enabled = True
    store.upsert(config)

    asyncio.run(ensure_uplimit_reason_preset(tmp_path))

    refreshed = ExtConfigStore(tmp_path).get(UP_LIMIT_REASON_ID)
    assert refreshed is not None and refreshed.pull is not None
    assert refreshed.pull.enabled is True, "已存在配置被静默改写, 违反「绝不覆盖」原则"


# ---------------------------------------------------------------------------
# 行级去重
# ---------------------------------------------------------------------------

def test_dedupe_mapped_rows_keeps_first_reason() -> None:
    """field_map 已应用的行 (中文键): 同股多行取首条, date 透传。"""
    rows = [
        {"股票代码": "000001", "股票简称": "平安银行", "涨停原因": "金融+利好", "date": "2026-09-24"},
        {"股票代码": "000001", "股票简称": "平安银行", "涨停原因": "金融+利好", "date": "2026-09-24"},
        {"股票代码": "600519", "股票简称": "贵州茅台", "涨停原因": "白酒", "date": "2026-09-24"},
    ]
    out = _dedupe_uplimit_reason_rows(rows)
    assert len(out) == 2
    assert out[0]["股票代码"] == "000001"
    assert out[0]["涨停原因"] == "金融+利好"
    assert out[0]["date"] == "2026-09-24"
    assert out[1]["股票代码"] == "600519"


def test_dedupe_raw_rows_pads_code_and_drops_empty() -> None:
    """原始接口行 (英文键): 代码补零; 无代码/无原因行剔除。"""
    rows = [
        {"stock_code": "1", "stock_name": "平安银行", "reason": "利好", "date": "2026-09-24"},
        {"stock_code": "000001", "stock_name": "平安银行", "reason": "利好", "date": "2026-09-24"},
        {"stock_code": "", "reason": "无代码"},
        {"stock_code": "600519", "reason": "  "},
    ]
    out = _dedupe_uplimit_reason_rows(rows)
    assert len(out) == 1
    assert out[0]["股票代码"] == "000001", "「1」应补零为「000001」并与第二行去重"


# ---------------------------------------------------------------------------
# sync_uplimit_reason (mock 通用拉取链, 不出网)
# ---------------------------------------------------------------------------

def _patch_fetch(monkeypatch: pytest.MonkeyPatch, fn) -> None:
    """sync 内部走 _ext_pull.fetch_rows_for_date 属性引用, 打桩到 ext_pull 命名空间。"""
    import app.services.ext_pull as ext_pull_mod

    monkeypatch.setattr(ext_pull_mod, "fetch_rows_for_date", fn, raising=False)


@pytest.fixture()
def fake_sync(monkeypatch: pytest.MonkeyPatch):
    def _install(rows_by_day: dict[str, list[dict]], latest_rows: list[dict] | None = None):
        async def _fake_fetch(config, day):
            return rows_by_day.get(day.isoformat(), [])

        async def _fake_latest(config):
            return latest_rows or []

        _patch_fetch(monkeypatch, _fake_fetch)
        monkeypatch.setattr(
            "app.services.ext_uplimit_reason._fetch_latest_rows", _fake_latest,
            raising=False,
        )
    return _install


def test_sync_explicit_day_writes_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 day: 按 date1=day 拉取, 写入该日分区, 返回去重后行数。"""
    day = date(2026, 9, 24)
    raw = [
        {"date": "2026-09-24", "stock_code": "000001", "stock_name": "平安银行", "reason": "利好A"},
        {"date": "2026-09-24", "stock_code": "000001", "stock_name": "平安银行", "reason": "利好A"},
        {"date": "2026-09-24", "stock_code": "600519", "stock_name": "贵州茅台", "reason": "白酒"},
    ]

    async def _fake_fetch(config, d):
        assert d == day
        return raw

    _patch_fetch(monkeypatch, _fake_fetch)

    n = asyncio.run(sync_uplimit_reason(tmp_path, day=day))
    assert n == 2, "同股 2 行应去重为 1"

    part = tmp_path / "ext_data" / UP_LIMIT_REASON_ID / "timeseries" / "date=2026-09-24" / "part.parquet"
    assert part.exists(), "写入分区路径错误"

    import polars as pl

    df = pl.read_parquet(part)
    assert len(df) == 2
    # symbol 由 symbol_map (股票代码) 生成并 normalize; code 由 computed 派生
    assert set(df["symbol"].to_list()) == {"000001.SZ", "600519.SH"}
    assert set(df["code"].to_list()) == {"000001", "600519"}
    assert "涨停原因" in df.columns


def test_sync_latest_day_uses_row_date_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """day=None: 拉接口最新日, 分区日期取行内 date (而非今天)。"""
    raw = [
        {"date": "2026-09-25", "stock_code": "300001", "stock_name": "特锐德", "reason": "充电桩"},
    ]

    async def _fake_latest(config):
        return raw

    monkeypatch.setattr(
        "app.services.ext_uplimit_reason._fetch_latest_rows", _fake_latest, raising=False,
    )

    n = asyncio.run(sync_uplimit_reason(tmp_path))
    assert n == 1

    part = tmp_path / "ext_data" / UP_LIMIT_REASON_ID / "timeseries" / "date=2026-09-25" / "part.parquet"
    assert part.exists(), "分区日期应为接口行内 date=2026-09-25"


def test_sync_empty_day_returns_zero_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非交易日: 0 行 → 返回 0, 不落盘不建目录。"""

    async def _fake_fetch(config, day):
        return []

    _patch_fetch(monkeypatch, _fake_fetch)

    n = asyncio.run(sync_uplimit_reason(tmp_path, day=date(2026, 9, 26)))
    assert n == 0
    assert not (tmp_path / "ext_data" / UP_LIMIT_REASON_ID / "timeseries").exists()


def test_sync_ensures_config_before_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """config.json 缺失时 sync 自建 (用户可能从未跑过启动钩子)。"""

    async def _fake_fetch(config, day):
        return []

    _patch_fetch(monkeypatch, _fake_fetch)

    asyncio.run(sync_uplimit_reason(tmp_path, day=date(2026, 9, 24)))

    assert ExtConfigStore(tmp_path).get(UP_LIMIT_REASON_ID) is not None


# ---------------------------------------------------------------------------
# fetch_preset 路由
# ---------------------------------------------------------------------------

def test_fetch_preset_routes_uplimit_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """手动拉取端点: ext_uplimit_reason 转发到专用同步链路 (按日分区, 非快照覆盖)。"""
    from app.services import ext_presets

    called: dict = {}

    async def _fake_sync(data_dir, day=None):
        called["data_dir"] = data_dir
        called["day"] = day
        return 7

    monkeypatch.setattr(
        "app.services.ext_uplimit_reason.sync_uplimit_reason", _fake_sync, raising=False,
    )
    n = asyncio.run(ext_presets.fetch_preset(UP_LIMIT_REASON_ID, tmp_path))
    assert n == 7
    assert called["day"] is None, "手动拉取走「接口最新日」路径"
    assert Path(called["data_dir"]) == tmp_path
