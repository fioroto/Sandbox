"""Registry de coletores por plataforma."""
from __future__ import annotations

from shelf_monitor.collectors.base import AuthenticatedBrowserCollector, BaseCollector
from shelf_monitor.collectors.btg import BtgCollector
from shelf_monitor.collectors.inter import InterCollector
from shelf_monitor.collectors.itau import ItauCollector
from shelf_monitor.collectors.nu import NuCollector
from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.collectors.xp import XpCollector
from shelf_monitor.config import Config

COLLECTORS: dict[str, type[PlatformCollector]] = {
    "xp": XpCollector,
    "btg": BtgCollector,
    "itau": ItauCollector,
    "nu": NuCollector,
    "inter": InterCollector,
}


def build_collector(
    config: Config, platform_id: str, stop_at_first_source: bool = False
) -> BaseCollector:
    if platform_id not in COLLECTORS:
        raise KeyError(
            f"plataforma desconhecida: {platform_id} "
            f"(disponíveis: {', '.join(sorted(COLLECTORS))})"
        )
    return COLLECTORS[platform_id](
        config, config.platform(platform_id), stop_at_first_source=stop_at_first_source
    )


__all__ = [
    "COLLECTORS",
    "AuthenticatedBrowserCollector",
    "BaseCollector",
    "PlatformCollector",
    "build_collector",
]
