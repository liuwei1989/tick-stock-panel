"""Provider registry."""
from __future__ import annotations

from app.data_providers.tickflow_provider import TickFlowProvider
from app.data_providers.zzshare_provider import ZzshareProvider

_PROVIDERS = {
    "tickflow": TickFlowProvider,
    "zzshare": ZzshareProvider,
}


def get_provider(name: str = "tickflow"):
    provider_cls = _PROVIDERS.get((name or "tickflow").lower())
    if provider_cls is None:
        raise ValueError(f"Unsupported data provider: {name}")
    return provider_cls()


def builtin_sources() -> dict[str, type]:
    """非 TickFlow 的内置源 (类引用, 不实例化)。

    供运行时路由 (kline_sync 分钟解析 / policy 能力增广) 与能力矩阵
    读取类属性 capabilities, 与 custom loader 的插件/自定义源并列。
    """
    return {name: cls for name, cls in _PROVIDERS.items() if name != "tickflow"}
