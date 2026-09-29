"""O bloco da industria: o dicionario que todas as superficies desenham.

Segue o contrato dos blocos de mercado (`market/etf_captacao_mensal.py`):
`available`/`reason`, a competencia e as janelas com datas, o que foi medido,
a cobertura e a leitura. Chaves com `_` carregam objetos Python e saem antes do
JSON. Nao ha chave `foco`: o monitor e neutro, nenhuma casa em destaque.
"""
from __future__ import annotations

from shelf_monitor.industria.base import BaseIndustria
from shelf_monitor.industria.cotistas import PF_PJ, SEGMENTOS
from shelf_monitor.industria.painel import (
    Camadas,
    camadas_do_universo,
    funil_do_mes,
    linhas_do_mes,
)
from shelf_monitor.industria.universo import CAMADAS_AO_LADO, ROTULOS, UNIVERSO
from shelf_monitor.report.shared.fontes import fontes_da_industria
from shelf_monitor.report.shared.numeros import pct, reais
from shelf_monitor.texto import competencia_br, data_br, milhar


def _segmentos(camadas: Camadas) -> list[dict]:
    total = camadas.pl_bruto - camadas.sem_perfil
    saida = []
    for segmento in SEGMENTOS:
        valor = camadas.segmentos.get(segmento.chave, 0.0)
        saida.append({
            "chave": segmento.chave,
            "rotulo": segmento.rotulo,
            "grupo": segmento.grupo,
            "pl": round(valor, 2),
            "fatia": round(valor / total, 6) if total else None,
        })
    return saida


def _qualidade(base: BaseIndustria, linhas) -> dict:
    universo = [l for l in linhas if l.motivo == UNIVERSO]
    pl = sum(l.pl for l in universo)
    com_perfil_do_mes = sum(l.pl for l in universo if l.fonte_fatia == "perfil")
    com_perfil_anterior = sum(l.pl for l in universo if l.fonte_fatia == "perfil_anterior")
    sem_cadastro = [l for l in linhas if l.motivo == "sem_cadastro"]
    return {
        "pl_universo": round(pl, 2),
        "pl_com_perfil_do_mes": round(com_perfil_do_mes, 2),
        "pl_com_perfil_anterior": round(com_perfil_anterior, 2),
        "cobertura_perfil": round(com_perfil_do_mes / pl, 4) if pl else None,
        "sem_cadastro": {
            "produtos": len(sem_cadastro),
            "classes": len({l.classe for l in sem_cadastro}),
            "pl": round(sum(l.pl for l in sem_cadastro), 2),
        },
        "retrato_do_cadastro": base.cadastro.competencia,
        "meses_com_perfil": base.meses_com_perfil,
        "meses_com_informe": list(base.serie.meses),
    }


def montar_bloco(base: BaseIndustria) -> dict:
    """O bloco da competencia de referencia da base."""
    competencia = base.competencia
    if competencia not in base.serie.meses:
        return {
            "available": False,
            "reason": (
                f"informe diário de {competencia_br(competencia)} indisponível - "
                "rode com a rede liberada para dados.cvm.gov.br"
            ),
            "competencia": competencia,
            "avisos": base.avisos,
        }
    if not base.perfis.get(competencia):
        return {
            "available": False,
            "reason": (
                f"perfil mensal de {competencia_br(competencia)} indisponível - sem ele "
                "não há como separar o dinheiro de PF/PJ do de outros fundos"
            ),
            "competencia": competencia,
            "avisos": base.avisos,
        }

    linhas = linhas_do_mes(base, competencia)
    funil = funil_do_mes(base, competencia)
    camadas = camadas_do_universo(base, linhas)
    primeiro, ultimo, pregoes = base.serie.calendario(competencia)
    pf_pj = sum(camadas.segmentos.get(c, 0.0) for c in PF_PJ)

    # Camadas ao lado: aparecem, nunca entram na soma do universo. A
    # previdencia entrega o informe (4.395 fundos em 08/2026) e sai das linhas
    # do mes; o ETF nao entrega, e sai do funil, com o PL do cadastro.
    degraus = {d.motivo: d for d in funil}
    ao_lado = {}
    for chave in CAMADAS_AO_LADO:
        grupo = [l for l in linhas if l.motivo == chave]
        if grupo:
            ao_lado[chave] = {
                "rotulo": ROTULOS[chave],
                "produtos": len(grupo),
                "pl": round(sum(l.pl for l in grupo), 2),
                "fonte_do_pl": "informe",
            }
        else:
            ao_lado[chave] = {
                "rotulo": ROTULOS[chave],
                "produtos": degraus[chave].produtos,
                "pl": round(degraus[chave].pl, 2),
                "fonte_do_pl": "cadastro",
            }

    bloco = {
        "available": True,
        "competencia": competencia,
        "rotulo_competencia": competencia_br(competencia),
        "informe": {
            "de": primeiro.isoformat() if primeiro else None,
            "ate": ultimo.isoformat() if ultimo else None,
            "pregoes": pregoes,
        },
        "funil": [d.to_dict() for d in funil],
        "camadas": camadas.to_dict(),
        "dinheiro_pf_pj": round(pf_pj, 2),
        "segmentos": _segmentos(camadas),
        "adjacentes": ao_lado,
        "qualidade": _qualidade(base, linhas),
        "avisos": list(base.avisos),
        "fontes": [f.nome for f in fontes_da_industria()],
        "_base": base,
        "_linhas": linhas,
    }
    bloco["leitura"] = leitura(bloco)
    return bloco


def leitura(bloco: dict) -> list[str]:
    """As frases que abrem o bloco, iguais em toda superficie."""
    if not bloco.get("available"):
        return []
    camadas = bloco["camadas"]
    universo = next(d for d in bloco["funil"] if d["motivo"] == UNIVERSO)
    base = sum(d["pl"] for d in bloco["funil"])
    fim = data_br(bloco["informe"]["ate"])
    frases = [
        (
            f"Em {bloco['rotulo_competencia']}, **{reais(camadas['pl_investidor'])}** estavam "
            f"em fundos disponíveis para PF e PJ, na camada do investidor (sem o dinheiro que "
            f"um fundo aplica em outro), no fechamento de {fim}: {milhar(universo['classes'])} "
            f"classes, de um cadastro que soma {reais(base)} com dupla contagem."
        ),
    ]
    if camadas["pl_bruto"]:
        frases.append(
            f"Outros fundos detêm {pct(1 - camadas['pl_investidor'] / camadas['pl_bruto'])} "
            f"do PL dessas classes: somar sem tirar essa fatia contaria o mesmo dinheiro duas vezes."
        )
    pf_pj = bloco["dinheiro_pf_pj"]
    total = camadas["pl_bruto"] - camadas["sem_perfil"]
    if total:
        conta_e_ordem = next(s for s in bloco["segmentos"] if s["chave"] == "conta_e_ordem")
        frases.append(
            f"O dinheiro de PF e PJ soma {reais(pf_pj)} ({pct(pf_pj / total)} do PL), "
            f"dos quais {reais(conta_e_ordem['pl'])} chegam por conta e ordem de plataformas."
        )
    return frases
