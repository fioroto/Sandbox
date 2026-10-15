"""As camadas ao lado do universo: previdencia e ETF.

Aparecem no painel para dar contexto ("para onde vai o dinheiro do
investidor"), e nunca entram na soma do universo.

- **Previdencia**: os fundos de previdencia entregam o informe diario (4.395
  em 08/2026, R$ 3,6 tri), entao a camada sai do proprio painel, com a mesma
  camada do investidor: o PL que nao esta em maos de outros fundos, que e o da
  seguradora em nome do participante;
- **ETF**: nao entrega o informe diario da CVM. A captacao vem do monitor de
  ETFs - o repositorio etf-monitor (`python -m etf_monitor etf --export`), que
  grava `data/reports/etf_captacao_mensal.csv` a partir do Fundos.NET; aqui ele
  e lido de `Config.etf_captacao_csv` (variavel SHELF_ETF_CAPTACAO_CSV). Sem o
  arquivo, ou sem a competencia, a camada sai indisponivel com o motivo.
"""
from __future__ import annotations

import csv
from datetime import date

from shelf_monitor.config import Config
from shelf_monitor.industria.base import BaseIndustria
from shelf_monitor.industria.fluxos import total
from shelf_monitor.industria.janelas import ORDEM, Janela
from shelf_monitor.normalize import parse_number
from shelf_monitor.texto import competencia_br


def camada_previdencia(base: BaseIndustria, janelas: dict[str, Janela]) -> dict:
    por_janela = {}
    for chave in ORDEM:
        janela = janelas[chave]
        fluxo = total(base, janela, motivos=("previdencia",))
        por_janela[chave] = {
            "fluxo_investidor": round(fluxo.fluxo_investidor, 2),
            "sobre_pl": None if fluxo.sobre_pl is None else round(fluxo.sobre_pl, 6),
        }
    fim = total(base, janelas["1m"], motivos=("previdencia",))
    return {
        "available": fim.produtos_fim > 0,
        "reason": None if fim.produtos_fim else "nenhum fundo de previdência no informe do mês",
        "fonte": "informe diário da CVM (fundos de previdência)",
        "pl_investidor": round(fim.pl_investidor_fim, 2),
        "produtos": fim.produtos_fim,
        "janelas": por_janela,
    }


def camada_etf(config: Config, janelas: dict[str, Janela]) -> dict:
    caminho = config.etf_captacao_csv
    if not caminho.is_file():
        return {
            "available": False,
            "reason": (
                "sem a captação mensal de ETF: rode `python -m etf_monitor etf --export` no "
                "etf-monitor e aponte SHELF_ETF_CAPTACAO_CSV para o data/reports/etf_captacao_mensal.csv dele"
            ),
        }
    por_mes: dict[str, float] = {}
    pl_por_mes: dict[str, float] = {}
    etfs: set[str] = set()
    with caminho.open(encoding="utf-8-sig", newline="") as handle:
        for linha in csv.DictReader(handle, delimiter=";"):
            mes = (linha.get("competencia") or "").strip()
            if len(mes) != 6:
                continue
            por_mes[mes] = por_mes.get(mes, 0.0) + (parse_number(linha.get("captacao")) or 0.0)
            pl_por_mes[mes] = pl_por_mes.get(mes, 0.0) + (parse_number(linha.get("pl_fim")) or 0.0)
            etfs.add(linha.get("cnpj") or "")
    referencia = janelas["1m"].competencias[-1] if janelas["1m"].competencias else None
    if referencia not in por_mes:
        ultimo = max(por_mes) if por_mes else None
        return {
            "available": False,
            "reason": (
                f"a captação de ETF em disco vai até {competencia_br(ultimo)}, antes de "
                f"{competencia_br(referencia)}: rode `python -m etf_monitor etf --export` no etf-monitor"
                if ultimo else "a captação de ETF em disco está vazia"
            ),
        }
    por_janela = {}
    for chave in ORDEM:
        meses = janelas[chave].competencias
        cobertos = [m for m in meses if m in por_mes]
        por_janela[chave] = {
            "fluxo_investidor": round(sum(por_mes[m] for m in cobertos), 2),
            "meses_cobertos": len(cobertos),
            "meses": len(meses),
        }
    return {
        "available": True,
        "reason": None,
        "fonte": f"monitor de ETFs (Fundos.NET), arquivo de {date.fromtimestamp(caminho.stat().st_mtime).strftime('%d/%m/%Y')}",
        "pl_investidor": round(pl_por_mes.get(referencia, 0.0), 2),
        "produtos": len(etfs),
        "janelas": por_janela,
    }
