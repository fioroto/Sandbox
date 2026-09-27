"""Coletor do Nu (Nubank).

O app NuInvest foi encerrado e a prateleira migrou para dentro do app Nubank,
sem equivalente web deslogado. Sobra o Open Finance; se a Nu DTVM nao publicar
o endpoint publico, o snapshot fica UNAVAILABLE de forma transparente - e este
e justamente o caso que justifica o coletor autenticado (v2).
"""
from __future__ import annotations

from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer


class NuCollector(PlatformCollector):
    def _collect_web(self) -> list[FundOffer]:
        raise SourceUnavailable(
            "Nu não expõe prateleira deslogada (app NuInvest encerrado); "
            "requer coletor autenticado"
        )
