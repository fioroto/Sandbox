"""Gera `blocos_2026-09-23.json`: os blocos analiticos reais da previdencia, aparados.

Os blocos de captacao e rentabilidade saem do informe diario da CVM, que nao
cabe num teste. Este script roda o relatorio de previdencia sobre os snapshots
gravados e guarda so a estrutura, com cada lista cortada a poucos itens - o
bastante para exercitar todas as secoes das superficies, com as chaves que os
blocos reais tem.

Uso (com o informe e a base de mercado em disco):
    python tests/fixtures/previdencia/make_blocos.py
"""
from __future__ import annotations

import json
from pathlib import Path

from shelf_monitor.main import carregar_snapshots
from shelf_monitor.previdencia.cli import construir_relatorio
from shelf_monitor.previdencia.config import (
    PLATAFORMAS,
    ROTULOS_PROPRIOS,
    ConfigPrevidencia,
)
from shelf_monitor.report.shared import rotulos_sobrepostos

_ITENS = 3
_DESTINO = Path(__file__).parent / "blocos_2026-09-23.json"


def _aparar(valor, profundidade: int = 0):
    """Corta toda lista a `_ITENS` itens, recursivamente; chaves `_*` saem."""
    if isinstance(valor, dict):
        return {
            k: _aparar(v, profundidade + 1)
            for k, v in valor.items()
            if not str(k).startswith("_")
        }
    if isinstance(valor, list):
        return [_aparar(v, profundidade + 1) for v in valor[:_ITENS]]
    return valor


def main() -> None:
    config = ConfigPrevidencia()
    with rotulos_sobrepostos(ROTULOS_PROPRIOS):
        snapshots = carregar_snapshots(config, list(PLATAFORMAS))
        report = construir_relatorio(config, snapshots)
    blocos = {
        "performance": _aparar(report.performance),
        "retail_flow": _aparar(report.retail_flow),
        "grade_opportunities": _aparar(report.grade_opportunities),
    }
    _DESTINO.write_text(
        json.dumps(blocos, ensure_ascii=True, indent=1, default=str), encoding="utf-8"
    )
    print(f"gravado {_DESTINO} ({_DESTINO.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
