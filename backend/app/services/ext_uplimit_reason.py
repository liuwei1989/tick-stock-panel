"""内置涨停原因扩展表 (ext_uplimit_reason) — zzshare 涨停复盘数据。

数据源: https://api.zizizaizai.com/v3/open/review/uplimit/reason?date1=YYYY-MM-DD
  (开放接口, 无需鉴权; 响应 {code,message,data:[{date,stock_code,stock_name,reason}]})
  - 同一股票因关联多个题材会返回多行 (实测 2026-09-24: 604 行 / 53 只), reason 相同
  - 非交易日返回空数组; 无日期参数时返回「最新有数据日」的复盘
  - reason 为自由长文本 (80~400 字), 含换行

与概念/行业预设 (ext_gn_ths/ext_hy_ths) 的差异:
  - timeseries 模式 (按日留存历史, 梯队/监控 JOIN 当日分区, 不受快照"仅当日"限制)
  - 拉取不走 ext_presets._fetch_json 的裸 httpx, 而是复用通用 PullConfig
    配方 (date_param=iso 日期, response_path="data"), 历史回补 / 定时拉取 /
    手动拉取 / 扩展表管理页全自动兼容, 零专用代码路径
  - 行级去重在 fetch 转换层完成: 同 symbol 取首条 reason (接口重复行为冗余)

接入点:
  - app.main.lifespan → ensure_builtin_presets (只创建 config, 不拉数据)
  - app.jobs.daily_pipeline Step 2.9 → sync_uplimit_reason (盘后增量, 软失败)
  - 梯队页 ext_columns=ext_uplimit_reason.涨停原因 动态 JOIN
"""
from __future__ import annotations

import logging
from datetime import date as date_cls
from pathlib import Path

from app.services.ext_data import (
    ExtConfig,
    ExtConfigStore,
    ExtField,
    PullConfig,
    rows_to_parquet,
)

logger = logging.getLogger(__name__)

# 内置涨停原因表 id (全局唯一; 出厂即注册, 用户可在扩展数据页管理)
UP_LIMIT_REASON_ID = "ext_uplimit_reason"

# zzshare 开放涨停复盘接口 (无需鉴权; date1 接受 ISO 日期)
UP_LIMIT_REASON_URL = "https://api.zizizaizai.com/v3/open/review/uplimit/reason"


def _uplimit_reason_preset() -> ExtConfig:
    """涨停原因 (ext_uplimit_reason) — timeseries, 通用拉取配方。

    schema: symbol / code / 股票代码 / 股票简称 / 涨停原因
    symbol_map 走「股票代码」列 (上游字段 stock_code → 股票代码, 见 field_map);
    code 由 rows_to_parquet → apply_config_mapping 按 symbol 派生。
    """
    return ExtConfig(
        id=UP_LIMIT_REASON_ID,
        label="涨停原因",
        mode="timeseries",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("code", "string", "代码"),
            ExtField("股票代码", "string", "股票代码"),
            ExtField("股票简称", "string", "股票简称"),
            ExtField("涨停原因", "string", "涨停原因"),
        ],
        description="zzshare 涨停复盘: 个股当日涨停原因与逻辑 (时序按日留存)",
        symbol_map={"type": "mapped", "col": "股票代码"},
        code_map={"type": "computed", "from": "symbol", "method": "strip_exchange"},
        pull=PullConfig(
            url=UP_LIMIT_REASON_URL,
            method="GET",
            response_path="data",
            # 接口按日期查询: date1=YYYY-MM-DD (iso); 历史回补/盘后管道按日拉取
            date_param="date1",
            date_format="iso",
            field_map={
                "stock_code": "股票代码",
                "stock_name": "股票简称",
                "reason": "涨停原因",
            },
            # 盘后管道已按交易日拉取, 调度器不再重复调度 (与概念/行业同约定 #199)
            schedule_minutes=0,
            enabled=False,
            timeout_seconds=30,
        ),
    )


# ---------------------------------------------------------------------------
# 接口结构 → 本地 schema 转换 (fetch 前应用, 与概念/行业 _flatten_* 同位)
# ---------------------------------------------------------------------------

def _dedupe_uplimit_reason_rows(raw_rows: list[dict]) -> list[dict]:
    """同股多行去重: 接口按关联题材重复返回同一只票, reason 相同, 取首条。

    输入行是 _parse_rows_payload 产物 (field_map 已应用): 键为 股票代码/股票简称/
    涨停原因 + 未映射的 date; 兼容原始键 (stock_code/stock_name/reason) 以便
    单测直接喂接口原始行。
    附带清洗: 代码补零到 6 位 (上游可能给 "1" 而非 "000001"); 剔除无代码/无原因行;
    date 原样透传 (day=None 时 sync 层靠它确定分区日期)。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for r in raw_rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("股票代码") or r.get("stock_code") or "").strip().zfill(6)
        reason = str(r.get("涨停原因") or r.get("reason") or "").strip()
        if not code or code == "000000" or not reason:
            continue
        if code in seen:
            continue
        seen.add(code)
        row: dict = {
            "股票代码": code,
            "股票简称": str(r.get("股票简称") or r.get("stock_name") or "").strip(),
            "涨停原因": reason,
        }
        if r.get("date") is not None:
            row["date"] = r["date"]
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 拉取执行 (复用通用 fetch_and_ingest 链 + 本模块去重转换)
# ---------------------------------------------------------------------------

async def sync_uplimit_reason(data_dir: Path, day: date_cls | None = None) -> int:
    """拉取指定交易日涨停原因并写入时序分区。返回写入行数 (0=该日无数据)。

    day=None 时拉「接口最新有数据日」, 写入按接口行内 date 对应的北京日期 —
    供盘后管道/手动拉取复用; 历史回补显式传 day (走 /backfill 通用端点亦可)。
    写入路径: rows_to_parquet → write_ext_parquet (含扩展帧/策略缓存失效)。
    """
    config = get_uplimit_reason_preset()
    if config is None:
        raise ValueError(f"未知的内置预设: {UP_LIMIT_REASON_ID}")

    # 确保 config.json 存在 (用户可能从未启动过 ensure_builtin_presets)
    store = ExtConfigStore(data_dir)
    if store.get(config.id) is None:
        store.upsert(config)

    # 延迟导入: ext_pull 与 ext_presets 历史上互相延迟引用, 保持同风格。
    # 走模块属性引用 (而非局部绑定), 测试可 monkeypatch 本模块命名空间。
    from app.services import ext_pull as _ext_pull

    rows = (
        await _ext_pull.fetch_rows_for_date(config, day)
        if day else await _fetch_latest_rows(config)
    )
    rows = _dedupe_uplimit_reason_rows(rows)
    if not rows:
        logger.info("ext_uplimit_reason: %s 无涨停复盘数据", day or "latest")
        return 0

    # 分区日期: 显式 day 优先; 否则取去重后首行 date (fetch 链已把接口行内
    # date 原样透传; field_map 不含 date 键所以不会被改名)。
    snap_day = day
    if snap_day is None:
        raw_date = str(rows[0].get("date") or "")[:10] if rows else ""
        try:
            snap_day = date_cls.fromisoformat(raw_date)
        except ValueError:
            logger.warning("ext_uplimit_reason: 接口未返回可解析日期 (%r), 回退今天", raw_date)
            snap_day = date_cls.today()

    n = rows_to_parquet(rows, config, data_dir, snapshot_date=snap_day)
    logger.info("ext_uplimit_reason: %s 写入 %d 只", snap_day.isoformat(), n)
    return n


async def _fetch_latest_rows(config: ExtConfig) -> list[dict]:
    """不带日期参数请求一次 (接口语义=最新有数据日), 供 day=None 路径。"""
    from app.services.ext_pull import _parse_rows_payload, _request_json

    data = await _request_json(config.pull, config.id)
    return _parse_rows_payload(config, config.pull, data)


# ---------------------------------------------------------------------------
# 对外入口 (与概念/行业 get_preset / ensure_builtin_presets 同构)
# ---------------------------------------------------------------------------

def get_uplimit_reason_preset() -> ExtConfig | None:
    """取涨停原因预设定义 (config 恒存在; 保留 None 语义与概念/行业对齐)。"""
    return _uplimit_reason_preset()


async def ensure_uplimit_reason_preset(data_dir: Path) -> None:
    """启动时: 缺失则创建 config.json (不拉数据)。

    与 ensure_builtin_presets 相同的安全约定: 已存在则完全跳过 (绝不覆盖
    用户已有配置, 即使老配置字段不全), 失败只记 warning 不阻断启动。
    """
    config = _uplimit_reason_preset()
    store = ExtConfigStore(data_dir)
    if store.get(config.id) is not None:
        return
    try:
        store.upsert(config)
        logger.info("内置扩展表 %s 配置已就绪 (待盘后管道/手动拉取)", config.id)
    except Exception as e:
        logger.warning("内置扩展表 %s 配置写入失败 (不影响启动): %s", config.id, e)
