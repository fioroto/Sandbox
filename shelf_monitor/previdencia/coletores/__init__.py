"""Registro dos coletores de previdencia - o equivalente de `collectors.COLLECTORS`."""
from __future__ import annotations

from shelf_monitor.collectors.base import BaseCollector
from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.config import Config
from shelf_monitor.previdencia.coletores.btg import BtgPrevidenciaCollector
from shelf_monitor.previdencia.coletores.itau import ItauPrivateCollector
from shelf_monitor.previdencia.coletores.sem_vitrine import SemVitrineCollector
from shelf_monitor.previdencia.coletores.xp import XpPrevidenciaCollector

COLETORES: dict[str, type[PlatformCollector]] = {
    "xp": XpPrevidenciaCollector,
    "btg": BtgPrevidenciaCollector,
    "itau": ItauPrivateCollector,
    "nu": SemVitrineCollector,
    "inter": SemVitrineCollector,
}


def construir_coletor(
    config: Config, platform_id: str, stop_at_first_source: bool = False
) -> BaseCollector:
    """Mesma assinatura de `collectors.build_collector`, para `collect_platforms`."""
    if platform_id not in COLETORES:
        raise KeyError(
            f"casa sem coletor de previdência: {platform_id} "
            f"(disponíveis: {', '.join(sorted(COLETORES))})"
        )
    return COLETORES[platform_id](
        config, config.platform(platform_id), stop_at_first_source=stop_at_first_source
    )


__all__ = ["COLETORES", "construir_coletor"]
