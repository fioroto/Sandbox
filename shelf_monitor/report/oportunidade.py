"""Oportunidade de grade e gaps: a mensagem principal do digest semanal.

Pedido em 14/10/2026: o digest abre com os gaps REAIS que estao captando e com
o esforco de trazer cada um. Ate ali ele publicava duas listas separadas -
"Gaps de prateleira" (por esforco) e "Oportunidade de grade" (fundos que
captam fora da XP) -, com regras de gestora diferentes: a grade comparava o
nome da gestora do veiculo, os gaps a raiz da gestora da estrategia (o master).
Aqui as duas viram uma, com a regra dos gaps (`aggregate._esforco_do_gap`).

O recorte, sobre o bloco de gaps ja ranqueado das casas do digest:

- **gap real**: fora da vitrine da XP, sem espelho nem equivalente pela
  carteira, sem correlato da mesma gestora (outra versao da mesma estrategia,
  como o Advisory da XP contra o Seleção do Itau - `familias`), sem ser so
  outra faixa de liquidez e fora da lista da casa (`distribuivel`; esforco
  "expor na vitrine" ou "ativar distribuição");
- **captando**: o criterio da grade (`FundFlow.entrou`) - captacao liquida em 3
  meses positiva, com cotistas novos onde cotista mede gente, PL de ao menos
  R$ 1 mi no inicio e sem movimento estrutural;
- **transferivel**: produto proprio do concorrente fica fora, contado.

A ordem e a dos gaps: esforco, depois captacao em 3 meses (`chave_do_gap`). A
semana entra como leitura - captacao da semana e o ritmo dela -, nao como
ordem: uma semana de um fundo e ruidosa demais para decidir o topo.
"""
from __future__ import annotations

from shelf_monitor.report.aggregate import (
    ESFORCO_ABRIR_GESTORA,
    ESFORCO_ATIVAR,
    ESFORCO_EXPOR,
    ESFORCO_INDEFINIDO,
    ESFORCO_LIQUIDEZ,
    ESFORCO_NAO_TRANSFERIVEL,
    ESFORCO_OPERACIONAL,
    chave_do_gap,
)
from shelf_monitor.report.retail_flow import _MIN_PL_RANKING

# O que a lista da casa ja tem (fora da vitrine) e o que so falta em outra
# faixa de liquidez nao e gap real.
_NAO_REAIS = frozenset({ESFORCO_EXPOR, ESFORCO_ATIVAR, ESFORCO_LIQUIDEZ})


def oportunidade_de_grade(
    gaps: dict, retail_flow: dict | None, com_cotistas: bool = True
) -> dict:
    """Os gaps reais que estao captando, com os numeros que abrem o digest.

    `gaps` e o bloco de gaps ja ranqueado (`ranquear_gaps`), montado so com as
    casas do digest. Sem o informe diario (a CVM fora do ar) nao ha como saber
    quem esta captando: a lista sai com os gaps reais transferiveis pela ordem
    de esforco, sem os numeros de captacao, e `com_fluxo` diz isso.
    """
    if not gaps.get("available"):
        return {
            "available": False,
            "reason": gaps.get("reason"),
            "nao_se_aplica": bool(gaps.get("nao_se_aplica")),
        }
    flow = retail_flow if (retail_flow or {}).get("available") else None
    fluxos = {f.cnpj: f for f in (flow or {}).get("_fluxos") or []}

    reais = [f for f in gaps.get("funds") or [] if f.get("esforco") not in _NAO_REAIS]
    if flow is None:
        return _sem_informe(gaps, reais, com_cotistas)
    captando = []
    sem_informe = 0
    for fundo in reais:
        fluxo = fluxos.get(fundo.get("cnpj"))
        if fluxo is None:
            sem_informe += 1
            continue
        if fluxo.entrou(com_cotistas) and fluxo.pl_inicio >= _MIN_PL_RANKING:
            captando.append((fundo, fluxo))
    proprios = [par for par in captando if par[0]["esforco"] == ESFORCO_NAO_TRANSFERIVEL]
    transferiveis = sorted(
        (par for par in captando if par[0]["esforco"] != ESFORCO_NAO_TRANSFERIVEL),
        key=lambda par: chave_do_gap(par[0]),
    )
    linhas = [fundo for fundo, _ in transferiveis]

    # Sem recorte semanal (informe sem semana completa) a captacao da semana
    # nao existe - e "-", nunca zero.
    recorte = flow.get("_recorte")
    com_semana = bool(recorte is not None and recorte.todas())
    return {
        **_cabecalho(gaps, com_cotistas),
        "por_casa": [
            _da_casa(concorrente, reais, transferiveis, com_semana)
            for concorrente in gaps.get("competitors") or []
        ],
        "com_fluxo": True,
        "total_reais": len(reais),
        "sem_informe": sem_informe,
        "total_captando": len(captando),
        "total_produto_do_concorrente": len(proprios),
        "total_transferiveis": len(linhas),
        "total_operacionais": sum(1 for f in linhas if f["esforco"] == ESFORCO_OPERACIONAL),
        "total_abrir_gestora": sum(1 for f in linhas if f["esforco"] == ESFORCO_ABRIR_GESTORA),
        "total_gestora_indefinida": sum(1 for f in linhas if f["esforco"] == ESFORCO_INDEFINIDO),
        "captacao_3m": round(sum(f.captacao_liquida for _, f in transferiveis), 2),
        "captacao_semana": (
            round(sum(f.captacao_semana or 0.0 for _, f in transferiveis), 2)
            if com_semana else None
        ),
        "cotistas_3m": sum(f.variacao_cotistas for _, f in transferiveis),
        "linhas": linhas,
    }


def _da_casa(
    concorrente: dict, reais: list[dict], transferiveis: list[tuple], com_semana: bool
) -> dict:
    """A oportunidade de um concorrente: os gaps reais da vitrine dele.

    O Itau tem de aparecer em toda analise (pedido de 14/10/2026), e na lista
    unica o BTG - vitrine maior, mais fundos captando - ocupava o topo inteiro.
    Fundo exibido pelas duas casas conta nas duas.
    """
    pid = concorrente["platform_id"]
    da_casa = [(f, fluxo) for f, fluxo in transferiveis if pid in (f.get("houses") or [])]
    return {
        "platform_id": pid,
        "label": concorrente["label"],
        "total_reais": sum(1 for f in reais if pid in (f.get("houses") or [])),
        "total_transferiveis": len(da_casa),
        "captacao_semana": (
            round(sum(fluxo.captacao_semana or 0.0 for _, fluxo in da_casa), 2)
            if com_semana else None
        ),
        "captacao_3m": round(sum(fluxo.captacao_liquida for _, fluxo in da_casa), 2),
        "linhas": [f for f, _ in da_casa],
    }


def _cabecalho(gaps: dict, com_cotistas: bool) -> dict:
    """O que a oportunidade carrega do bloco de gaps, com ou sem informe."""
    return {
        "available": True,
        "com_cotistas": com_cotistas,
        "baseline_label": gaps.get("baseline_label"),
        "concorrentes": [c["label"] for c in gaps.get("competitors") or []],
        "excluded": gaps.get("excluded") or [],
        "baseline_warning": gaps.get("baseline_warning"),
        "carteira": gaps.get("carteira") or {},
    }


def _sem_informe(gaps: dict, reais: list[dict], com_cotistas: bool) -> dict:
    """Sem o informe diario: os gaps reais transferiveis, pelo esforco, sem captacao."""
    linhas = sorted(
        (f for f in reais if f["esforco"] != ESFORCO_NAO_TRANSFERIVEL), key=chave_do_gap
    )
    por_casa = []
    for concorrente in gaps.get("competitors") or []:
        pid = concorrente["platform_id"]
        da_casa = [f for f in linhas if pid in (f.get("houses") or [])]
        por_casa.append({
            "platform_id": pid,
            "label": concorrente["label"],
            "total_reais": sum(1 for f in reais if pid in (f.get("houses") or [])),
            "total_transferiveis": len(da_casa),
            "captacao_semana": None,
            "captacao_3m": None,
            "linhas": da_casa,
        })
    return {
        **_cabecalho(gaps, com_cotistas),
        "por_casa": por_casa,
        "com_fluxo": False,
        "total_reais": len(reais),
        "sem_informe": len(reais),
        "total_captando": None,
        "total_produto_do_concorrente": len(reais) - len(linhas),
        "total_transferiveis": len(linhas),
        "total_operacionais": sum(1 for f in linhas if f["esforco"] == ESFORCO_OPERACIONAL),
        "total_abrir_gestora": sum(1 for f in linhas if f["esforco"] == ESFORCO_ABRIR_GESTORA),
        "total_gestora_indefinida": sum(1 for f in linhas if f["esforco"] == ESFORCO_INDEFINIDO),
        "captacao_3m": None,
        "captacao_semana": None,
        "cotistas_3m": None,
        "linhas": linhas,
    }
