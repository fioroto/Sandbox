"""Casa sem vitrine publica de previdencia: sai UNAVAILABLE, com o motivo.

Uma casa sem vitrine nao e uma casa com vitrine vazia. O snapshot UNAVAILABLE e
o que faz o relatorio dizer "o Nu nao tem vitrine de previdencia" em vez de "o
Nu nao oferta renda fixa" - `competitive.vitrine_snapshots` exclui a casa com o
motivo. Nenhuma requisicao e feita: nao ha URL para consultar.
"""
from __future__ import annotations

from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer


class SemVitrineCollector(PlatformCollector):
    def _collect_impl(self) -> list[FundOffer]:
        raise SourceUnavailable(
            self.platform.notes or "sem vitrine pública de previdência"
        )
