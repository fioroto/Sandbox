"""O explorador: uma linha por produto do universo, com todas as dimensoes.

E o que a planilha e o painel abrem para filtrar e ordenar: estrategia, papel,
gestora, publico, casa dedicada, liquidez, taxa, PL, fluxo em cada janela,
retorno e o quartil no peer group. As somas por dimensao do bloco fecham com a
soma destas linhas (travado em teste).
"""
from __future__ import annotations

from shelf_monitor.familias import faixa_de_liquidez
from shelf_monitor.industria.agregados import dimensoes
from shelf_monitor.industria.base import BaseIndustria
from shelf_monitor.industria.fluxos import fluxos_por
from shelf_monitor.industria.janelas import ORDEM, Janela
from shelf_monitor.industria.painel import linhas_do_mes
from shelf_monitor.industria.universo import UNIVERSO
from shelf_monitor.normalize import format_cnpj


def _nome_da_raiz(base: BaseIndustria, raiz: str) -> str:
    produtos = base.cadastro.classes.get(raiz)
    if produtos:
        return produtos[0].nome
    return base.mapa.nome(raiz) if base.mapa else ""


def linhas_do_explorador(
    base: BaseIndustria, janelas: dict[str, Janela], rentabilidade: dict[str, dict]
) -> list[dict]:
    competencia = base.competencia
    por_janela = {
        chave: fluxos_por(base, janelas[chave], lambda l, d: (l.classe, l.subclasse))
        for chave in ORDEM
    }
    retornos_12m = rentabilidade.get("12m", {}).get("por_classe", {})
    retornos_3m = rentabilidade.get("3m", {}).get("por_classe", {})
    linhas = []
    for linha in linhas_do_mes(base, competencia):
        if linha.motivo != UNIVERSO:
            continue
        dim = dimensoes(base, linha)
        produto = base.cadastro.produto(linha.classe, linha.subclasse)
        perfil = base.perfil_do_mes(linha.classe, competencia)
        chave = (linha.classe, linha.subclasse)
        taxa = produto.taxa_adm if produto and produto.taxa_adm_unidade == "percentual" else None
        r12 = retornos_12m.get(linha.classe, {})
        r3 = retornos_3m.get(linha.classe, {})
        fluxos = {
            f"fluxo_{c}": round(por_janela[c][chave].fluxo_investidor, 2) if chave in por_janela[c] else None
            for c in ORDEM
        }
        doze = por_janela["12m"].get(chave)
        linhas.append({
            "cnpj": format_cnpj(linha.classe),
            "subclasse": linha.subclasse,
            "nome": produto.nome if produto else "",
            "nome_subclasse": produto.nome_subclasse if produto else "",
            "n1": dim.n1,
            "n2": dim.n2,
            "origem": dim.origem,
            "confianca": dim.confianca,
            "papel": dim.papel,
            "raiz": format_cnpj(dim.raiz),
            "raiz_nome": _nome_da_raiz(base, dim.raiz) if dim.raiz != linha.classe else "",
            "gestora": dim.grupo,
            "tipo_gestora": dim.tipo_gestora,
            "gestora_do_veiculo": dim.grupo_veiculo,
            "casa_dedicada": dim.casa_dedicada or "",
            "publico": dim.publico,
            "condominio": produto.condominio if produto else "",
            "dmais": produto.dmais if produto else None,
            "faixa_liquidez": faixa_de_liquidez(produto.dmais) if produto else None,
            "aplicacao_minima": produto.aplicacao_minima if produto else None,
            "taxa_adm": taxa,
            "pl": round(linha.pl, 2),
            "pl_investidor": round(linha.pl_investidor, 2),
            "fatia_fundos": round(linha.fatia_fundos, 4),
            "cotistas": linha.cotistas,
            **fluxos,
            "sobre_pl_12m": None if doze is None or doze.sobre_pl is None else round(doze.sobre_pl, 6),
            "retorno_12m": r12.get("retorno"),
            "quartil_12m": r12.get("quartil"),
            "retorno_3m": r3.get("retorno"),
            "quartil_3m": r3.get("quartil"),
            "conta_e_ordem": round(perfil.fatia("conta_e_ordem"), 4) if perfil else None,
            "pf_pj": round(perfil.pf_pj, 4) if perfil else None,
        })
    linhas.sort(key=lambda l: -l["pl_investidor"])
    return linhas
