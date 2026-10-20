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
  outra faixa de liquidez e fora da lista da casa (`distribuivel`). Desde
  19/10/2026 o bloco de gaps ja manda a lista e a liquidez para os cobertos
  (`aggregate._cobertura`);
- **captando**: o criterio da grade (`FundFlow.entrou`) - captacao liquida em 3
  meses positiva, com cotistas novos onde cotista mede gente, PL de ao menos
  R$ 1 mi no inicio e sem movimento estrutural;
- **acelerando na semana** (pedidos de 16 e 19/10/2026): o ritmo da semana e
  "acelerando" (`RITMOS_DA_LISTA`) - a captacao por dia da semana de
  referencia mais de 25% acima da das 12 semanas anteriores, que ja era
  positiva. Estavel, desacelerando, virou saida e, desde 19/10/2026, virou
  entrada ficam fora: no digest de 14/10/2026, 3 dos 5 primeiros do BTG ja nao
  aceleravam, e um deles tinha virado saida;
- **mais de R$ 10 MM na semana** (pedido de 19/10/2026): a captacao liquida
  da semana de referencia passa de `CAPTACAO_MINIMA_NA_SEMANA`. O fundo
  pequeno que acelera de +0,1 MM para +1 MM por dia acelera, mas nao muda a
  grade. Os numeros de cima e as contagens do resumo por concorrente ("Na
  lista", "Para trazer") contam a mesma lista.

A captacao do resumo por concorrente (colunas Semana e 3 meses) e outra conta
(pedido de 20/10/2026): soma todos os gaps reais da vitrine da casa - na lista
ou nao, para trazer ou produto proprio, captando ou resgatando -, com o recorte
da tabela por categoria (`por_categoria`): informe, PL de ao menos R$ 1 mi no
inicio e sem movimento estrutural. Ate ali somava so o que dava para trazer da
lista, e repetia os numeros de cima casa a casa.

A semana filtra e os 3 meses ordenam (`chave_da_lista`). A semana de um fundo e
ruidosa demais para decidir o topo sozinha, mas basta para separar quem ganha
forca de quem perde. Sem semana completa no informe nao ha ritmo: a lista fica
com todos os que captam, e `com_ritmo` diz isso.

O produto proprio do concorrente entra na lista, marcado pelo esforco (pedido
de 16/10/2026): mostra onde o concorrente ganha com produto dele. Os numeros de
cima - quantos para a grade, captacao, cotistas e esforco - contam so o que da
para trazer, e o produto proprio sai contado a parte. Ate essa data a ordem era
esforco e depois captacao, com o produto proprio fora e 5 fundos por casa.
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
# faixa de liquidez nao e gap real. O bloco de gaps ja os tira (19/10/2026);
# o filtro fica para o relatorio salvo antes disso.
_NAO_REAIS = frozenset({ESFORCO_EXPOR, ESFORCO_ATIVAR, ESFORCO_LIQUIDEZ})

# O ritmo da semana que poe o gap na lista: capta mais por dia do que nas 12
# semanas anteriores (ver retail_flow.classificar_ritmo). "Virou entrada" saiu
# em 19/10/2026, a pedido: a visao semanal e so de quem acelera.
RITMOS_DA_LISTA = frozenset({"acelerando"})

# A captacao da semana que, alem do ritmo, poe o gap na lista (pedido de
# 19/10/2026): mais de R$ 10 MM liquidos na semana de referencia.
CAPTACAO_MINIMA_NA_SEMANA = 10_000_000.0


def chave_da_lista(fundo: dict) -> tuple:
    """A ordem da lista do digest: captacao em 3 meses, depois casas e PL."""
    return (
        -(fundo.get("captacao_liquida") or 0.0),
        -(fundo.get("houses_count") or 0),
        -(fundo.get("pl_cvm") or 0.0),
    )


def da_para_trazer(fundo: dict) -> bool:
    """Tudo menos o produto proprio do concorrente, que a XP nao traz."""
    return fundo.get("esforco") != ESFORCO_NAO_TRANSFERIVEL


def oportunidade_de_grade(
    gaps: dict, retail_flow: dict | None, com_cotistas: bool = True
) -> dict:
    """Os gaps reais que captam e aceleram, com os numeros que abrem o digest.

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
    # Os gaps reais que a captacao do resumo por concorrente soma, entrando ou
    # saindo (20/10/2026).
    medidos = []
    sem_informe = 0
    for fundo in reais:
        fluxo = fluxos.get(fundo.get("cnpj"))
        if fluxo is None:
            sem_informe += 1
            continue
        if fluxo.pl_inicio < _MIN_PL_RANKING:
            continue
        if not fluxo.estrutural:
            medidos.append((fundo, fluxo))
        if fluxo.entrou(com_cotistas):
            captando.append((fundo, fluxo))

    # Sem recorte semanal (informe sem semana completa) nao ha captacao da
    # semana nem ritmo dela: a captacao e "-", nunca zero, e a lista nao filtra.
    recorte = flow.get("_recorte")
    com_semana = bool(recorte is not None and recorte.todas())
    na_lista = sorted(
        (
            par for par in captando
            if not com_semana or (
                par[1].ritmo_semanal in RITMOS_DA_LISTA
                and (par[1].captacao_semana or 0.0) > CAPTACAO_MINIMA_NA_SEMANA
            )
        ),
        key=lambda par: chave_da_lista(par[0]),
    )
    trazer = [par for par in na_lista if da_para_trazer(par[0])]
    linhas = [fundo for fundo, _ in na_lista]
    para_trazer = [fundo for fundo, _ in trazer]
    return {
        **_cabecalho(gaps, com_cotistas),
        "por_casa": [
            _da_casa(concorrente, reais, medidos, na_lista, com_semana)
            for concorrente in gaps.get("competitors") or []
        ],
        "com_fluxo": True,
        "com_ritmo": com_semana,
        "total_reais": len(reais),
        "sem_informe": sem_informe,
        "total_captando": len(captando),
        "total_na_lista": len(linhas),
        "total_produto_do_concorrente": len(linhas) - len(para_trazer),
        "total_transferiveis": len(para_trazer),
        "total_operacionais": sum(1 for f in para_trazer if f["esforco"] == ESFORCO_OPERACIONAL),
        "total_abrir_gestora": sum(1 for f in para_trazer if f["esforco"] == ESFORCO_ABRIR_GESTORA),
        "total_gestora_indefinida": sum(
            1 for f in para_trazer if f["esforco"] == ESFORCO_INDEFINIDO
        ),
        "captacao_3m": round(sum(f.captacao_liquida for _, f in trazer), 2),
        "captacao_semana": (
            round(sum(f.captacao_semana or 0.0 for _, f in trazer), 2)
            if com_semana else None
        ),
        "cotistas_3m": sum(f.variacao_cotistas for _, f in trazer),
        "linhas": linhas,
    }


def _da_casa(
    concorrente: dict, reais: list[dict], medidos: list[tuple], na_lista: list[tuple],
    com_semana: bool,
) -> dict:
    """A lista de um concorrente: os gaps da vitrine dele que entram na lista.

    O Itau tem de aparecer em toda analise (pedido de 14/10/2026), e na lista
    unica o BTG - vitrine maior, mais fundos captando - ocupava o topo inteiro.
    Fundo exibido pelas duas casas conta nas duas. A captacao somada e a de
    todos os gaps reais da casa com informe (`medidos`, 20/10/2026), e nao so a
    da lista.
    """
    pid = concorrente["platform_id"]
    da_casa = [(f, fluxo) for f, fluxo in na_lista if pid in (f.get("houses") or [])]
    trazer = [(f, fluxo) for f, fluxo in da_casa if da_para_trazer(f)]
    medidos_da_casa = [fluxo for f, fluxo in medidos if pid in (f.get("houses") or [])]
    return {
        "platform_id": pid,
        "label": concorrente["label"],
        "total_reais": sum(1 for f in reais if pid in (f.get("houses") or [])),
        "total_na_lista": len(da_casa),
        "total_transferiveis": len(trazer),
        "captacao_semana": (
            round(sum(fluxo.captacao_semana or 0.0 for fluxo in medidos_da_casa), 2)
            if com_semana else None
        ),
        "captacao_3m": round(sum(fluxo.captacao_liquida for fluxo in medidos_da_casa), 2),
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
    linhas = sorted((f for f in reais if da_para_trazer(f)), key=chave_do_gap)
    por_casa = []
    for concorrente in gaps.get("competitors") or []:
        pid = concorrente["platform_id"]
        da_casa = [f for f in linhas if pid in (f.get("houses") or [])]
        por_casa.append({
            "platform_id": pid,
            "label": concorrente["label"],
            "total_reais": sum(1 for f in reais if pid in (f.get("houses") or [])),
            "total_na_lista": len(da_casa),
            "total_transferiveis": len(da_casa),
            "captacao_semana": None,
            "captacao_3m": None,
            "linhas": da_casa,
        })
    return {
        **_cabecalho(gaps, com_cotistas),
        "por_casa": por_casa,
        "com_fluxo": False,
        "com_ritmo": False,
        "total_reais": len(reais),
        "sem_informe": len(reais),
        "total_captando": None,
        "total_na_lista": len(linhas),
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
