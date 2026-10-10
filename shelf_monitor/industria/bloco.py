"""O bloco da industria: o dicionario que todas as superficies desenham.

Segue o contrato dos blocos de mercado (o da captacao de ETF, hoje no etf-monitor):
`available`/`reason`, a competencia e as janelas com datas, o que foi medido,
a cobertura e a leitura. Chaves com `_` carregam objetos Python e saem antes do
JSON. Nao ha chave `foco`: o monitor e neutro, nenhuma casa em destaque.

Toda soma e da camada do investidor e do universo disponivel, salvo quando a
chave diz outra coisa (`pl` bruto, camadas ao lado).
"""
from __future__ import annotations

from shelf_monitor.config import Config
from shelf_monitor.industria.adjacentes import camada_etf, camada_previdencia
from shelf_monitor.industria.agregados import (
    dimensoes,
    secoes_da_estrategia,
    secoes_da_estrutura,
    secoes_de_gestoras,
    secoes_de_publico,
)
from shelf_monitor.industria.base import BaseIndustria
from shelf_monitor.industria.cotistas import PF_PJ, SEGMENTOS
from shelf_monitor.industria.curadoria import TIPOS_DE_GESTORA
from shelf_monitor.industria.desempenho import rentabilidade_por_peer_group
from shelf_monitor.industria.explorador import linhas_do_explorador
from shelf_monitor.industria.fluxos import (
    fluxos_por,
    historico_mensal,
    lancamentos_e_encerramentos,
    mapa_de_calor,
    segmentos_do_mes,
    total,
)
from shelf_monitor.industria.janelas import ORDEM, janelas as montar_janelas, prazos
from shelf_monitor.industria.painel import (
    Camadas,
    camadas_do_universo,
    funil_do_mes,
    linhas_do_mes,
)
from shelf_monitor.industria.taxonomia import N1_ORDEM
from shelf_monitor.industria.universo import CAMADAS_AO_LADO, ROTULOS, UNIVERSO
from shelf_monitor.report.shared.fontes import fontes_da_industria
from shelf_monitor.report.shared.numeros import pct, pp, reais, reais_com_sinal
from shelf_monitor.texto import competencia_br, data_br, milhar

# Tamanho das listas do bloco (familias, gestoras, parcerias).
TOP = 20


def _segmentos(camadas: Camadas, anterior: dict | None) -> list[dict]:
    total_pl = camadas.pl_bruto - camadas.sem_perfil
    total_anterior = sum(anterior.values()) if anterior else 0.0
    saida = []
    for segmento in SEGMENTOS:
        valor = camadas.segmentos.get(segmento.chave, 0.0)
        fatia = valor / total_pl if total_pl else None
        fatia_anterior = (anterior or {}).get(segmento.chave, 0.0) / total_anterior if total_anterior else None
        saida.append({
            "chave": segmento.chave,
            "rotulo": segmento.rotulo,
            "grupo": segmento.grupo,
            "pl": round(valor, 2),
            "fatia": None if fatia is None else round(fatia, 6),
            "fatia_12m_antes": None if fatia_anterior is None else round(fatia_anterior, 6),
            "variacao_pp": (
                None if fatia is None or fatia_anterior is None else round((fatia - fatia_anterior) * 100, 3)
            ),
        })
    return saida


def _qualidade(base: BaseIndustria, linhas, historico: list[dict]) -> dict:
    universo = [l for l in linhas if l.motivo == UNIVERSO]
    pl = sum(l.pl for l in universo)
    com_perfil_do_mes = sum(l.pl for l in universo if l.fonte_fatia == "perfil")
    com_perfil_anterior = sum(l.pl for l in universo if l.fonte_fatia == "perfil_anterior")
    sem_cadastro = [l for l in linhas if l.motivo == "sem_cadastro"]
    estruturais = [l for l in universo if l.pl_anterior and abs(l.captacao_liquida) > l.pl_anterior]
    residuos = [abs(h["residuo"]) for h in historico if h.get("residuo") is not None]
    variacoes = [
        abs(historico[i]["pl_investidor"] - historico[i - 1]["pl_investidor"])
        for i in range(1, len(historico))
    ]
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
        "fluxos_estruturais": {
            "produtos": len(estruturais),
            "captacao_liquida": round(sum(l.captacao_liquida for l in estruturais), 2),
        },
        "eventos_na_cota": sum(1 for l in universo if l.evento_na_cota),
        "residuo_sobre_variacao": (
            round(sum(residuos) / sum(variacoes), 4) if residuos and sum(variacoes) else None
        ),
        "retrato_do_cadastro": base.cadastro.competencia,
        "meses_com_perfil": base.meses_com_perfil,
        "meses_com_informe": list(base.serie.meses),
    }


def _janelas_de(fluxos: dict, chave) -> dict:
    return {j: fluxos[j][chave].to_dict() if chave in fluxos[j] else None for j in ORDEM}


def _estrategias(base: BaseIndustria, jan, itens, rentabilidade: dict) -> dict:
    secoes = secoes_da_estrategia(itens)
    por_n1 = {j: fluxos_por(base, jan[j], lambda l, d: d.n1) for j in ORDEM}
    por_n2 = {j: fluxos_por(base, jan[j], lambda l, d: (d.n1, d.n2)) for j in ORDEM}
    for item in secoes["n1"]:
        item["janelas"] = _janelas_de(por_n1, item["n1"])
    retorno = {
        j: {(g["n1"], g["n2"]): g for g in rentabilidade[j]["n2"]} for j in ("12m", "3m")
    }
    for item in secoes["n2"]:
        chave = (item["n1"], item["n2"])
        item["janelas"] = _janelas_de(por_n2, chave)
        item["rentabilidade"] = {
            j: {k: v for k, v in retorno[j].get(chave, {}).items() if k not in ("n1", "n2")}
            for j in ("12m", "3m")
        }
    meses = list(jan["12m"].competencias)
    calor = mapa_de_calor(base, lambda l, d: (d.n1, d.n2), meses)
    ordem = {nome: i for i, nome in enumerate(N1_ORDEM)}
    secoes["mapa_de_calor"] = {
        "meses": meses,
        "linhas": [
            {"n1": n1, "n2": n2, "valores": [valores.get(m) for m in meses]}
            for (n1, n2), valores in sorted(calor.items(), key=lambda kv: (ordem.get(kv[0][0], 99), kv[0][1]))
        ],
    }
    return secoes


def _familias(base: BaseIndustria, janela) -> dict:
    fluxos = fluxos_por(base, janela, lambda l, d: d.raiz)
    veiculos: dict[str, set] = {}
    meta: dict[str, tuple] = {}
    for linha in linhas_do_mes(base, base.competencia):
        if linha.motivo != UNIVERSO:
            continue
        dim = dimensoes(base, linha)
        veiculos.setdefault(dim.raiz, set()).add(linha.classe)
        meta.setdefault(dim.raiz, (dim.grupo, dim.n1, dim.n2))

    def nome(raiz: str) -> str:
        produtos = base.cadastro.classes.get(raiz)
        if produtos:
            return produtos[0].nome
        return base.mapa.nome(raiz) if base.mapa else raiz

    def item(raiz: str) -> dict:
        grupo, n1, n2 = meta.get(raiz, ("", "", ""))
        return {
            "raiz": raiz, "nome": nome(raiz), "gestora": grupo, "n1": n1, "n2": n2,
            "veiculos": len(veiculos.get(raiz, ())), **fluxos[raiz].to_dict(),
        }

    ordenadas = sorted(fluxos, key=lambda r: -fluxos[r].fluxo_investidor)
    return {
        "janela": janela.chave,
        "familias_no_universo": len(fluxos),
        "que_mais_captam": [item(r) for r in ordenadas[:TOP] if fluxos[r].fluxo_investidor > 0],
        "que_mais_perdem": [item(r) for r in reversed(ordenadas[-TOP:]) if fluxos[r].fluxo_investidor < 0],
    }


def _gestoras(base: BaseIndustria, jan, itens) -> dict:
    secoes = secoes_de_gestoras(itens)
    doze = jan["12m"]
    por_grupo = fluxos_por(base, doze, lambda l, d: (d.grupo, d.tipo_gestora))
    por_tipo = fluxos_por(base, doze, lambda l, d: d.tipo_gestora)
    total_fim = sum(f.pl_investidor_fim for f in por_grupo.values())
    total_abertura = sum(f.pl_investidor_abertura for f in por_grupo.values())
    total_fluxo_positivo = sum(max(f.fluxo_investidor, 0.0) for f in por_grupo.values())

    def participacao(fluxo) -> dict:
        fim = fluxo.pl_investidor_fim / total_fim if total_fim else None
        abertura = fluxo.pl_investidor_abertura / total_abertura if total_abertura else None
        return {
            "share": None if fim is None else round(fim, 6),
            "share_12m_antes": None if abertura is None else round(abertura, 6),
            "variacao_pp": None if fim is None or abertura is None else round((fim - abertura) * 100, 3),
            "fluxo_investidor_12m": round(fluxo.fluxo_investidor, 2),
            "sobre_pl_12m": None if fluxo.sobre_pl is None else round(fluxo.sobre_pl, 6),
        }

    for item in secoes["grupos"]:
        fluxo = por_grupo.get((item["grupo"], item["tipo"]))
        if fluxo is not None:
            item.update(participacao(fluxo))
    for item in secoes["tipos"]:
        fluxo = por_tipo.get(item["tipo"])
        item["rotulo"] = TIPOS_DE_GESTORA.get(item["tipo"], item["tipo"])
        if fluxo is not None:
            item.update(participacao(fluxo))
    maiores = sorted(por_grupo.items(), key=lambda kv: -kv[1].fluxo_investidor)[:10]
    secoes["concentracao_da_captacao"] = (
        round(sum(max(f.fluxo_investidor, 0.0) for _, f in maiores) / total_fluxo_positivo, 4)
        if total_fluxo_positivo else None
    )
    return secoes


def _estrutura(base: BaseIndustria, jan, itens) -> dict:
    secoes = secoes_da_estrutura(base, itens)
    doze = jan["12m"]
    por_papel = fluxos_por(base, doze, lambda l, d: d.papel)
    por_casa = fluxos_por(base, doze, lambda l, d: d.casa_dedicada)
    for item in secoes["papeis"]:
        fluxo = por_papel.get(item["papel"])
        item["fluxo_investidor_12m"] = round(fluxo.fluxo_investidor, 2) if fluxo else None
    for item in secoes["casas_dedicadas"]:
        fluxo = por_casa.get(item["casa"])
        item["fluxo_investidor_12m"] = round(fluxo.fluxo_investidor, 2) if fluxo else None
    movimentos = lancamentos_e_encerramentos(base, jan["1m"])
    novos = []
    for classe, sub, _ in movimentos["entrou"]:
        dim = base._memo.get(("dimensoes", classe, sub))
        estrutura = base.estrutura.de(classe)
        if estrutura.papel == "espelho" or estrutura.casa_dedicada:
            produto = base.cadastro.produto(classe, sub)
            novos.append({
                "cnpj": classe, "nome": produto.nome if produto else "",
                "papel": estrutura.papel, "casa": estrutura.casa_dedicada,
                "gestora_da_estrategia": estrutura.grupo_estrategia.nome,
                "n2": dim.n2 if dim else None,
            })
    secoes["veiculos_dedicados_novos_no_mes"] = novos
    return secoes


def _plataformizacao(base: BaseIndustria, jan) -> dict:
    """Fatia do PL que chega por conta e ordem, no fim e 12 meses antes."""
    fim, antes = base.competencia, jan["12m"].base

    def fatias(chave) -> dict:
        agora = segmentos_do_mes(base, fim, chave)
        anterior = segmentos_do_mes(base, antes, chave)
        saida = {}
        for grupo, valores in agora.items():
            soma = sum(valores.values())
            soma_antes = sum(anterior.get(grupo, {}).values())
            f = valores["conta_e_ordem"] / soma if soma else None
            f_antes = anterior[grupo]["conta_e_ordem"] / soma_antes if soma_antes else None
            saida[grupo] = {
                "pl": round(soma, 2),
                "conta_e_ordem": None if f is None else round(f, 6),
                "conta_e_ordem_12m_antes": None if f_antes is None else round(f_antes, 6),
                "variacao_pp": None if f is None or f_antes is None else round((f - f_antes) * 100, 3),
            }
        return saida

    total_ = fatias(None).get("total", {})
    por_n1 = fatias(lambda l, d: d.n1)
    por_grupo = fatias(lambda l, d: d.grupo)
    ordem = {nome: i for i, nome in enumerate(N1_ORDEM)}
    return {
        "total": total_,
        "por_n1": [{"n1": k, **v} for k, v in sorted(por_n1.items(), key=lambda kv: ordem.get(kv[0], 99))],
        "por_gestora": [
            {"gestora": k, **v} for k, v in sorted(por_grupo.items(), key=lambda kv: -kv[1]["pl"])[:TOP]
        ],
    }


def _lancamentos(base: BaseIndustria, jan) -> dict:
    saida = {}
    for chave in ("1m", "12m"):
        movimentos = lancamentos_e_encerramentos(base, jan[chave])
        entrou, saiu = [], []
        for destino, lista in ((entrou, movimentos["entrou"]), (saiu, movimentos["saiu"])):
            for classe, sub, mes in lista:
                linha = next(
                    (l for l in linhas_do_mes(base, mes) if l.classe == classe and l.subclasse == sub),
                    None,
                )
                if linha is None:
                    continue
                dim = dimensoes(base, linha)
                produto = base.cadastro.produto(classe, sub)
                destino.append({
                    "cnpj": classe, "subclasse": sub, "nome": produto.nome if produto else "",
                    "competencia": mes, "n1": dim.n1, "n2": dim.n2, "gestora": dim.grupo,
                    "papel": dim.papel, "pl_investidor": round(linha.pl_investidor, 2),
                })
        entrou.sort(key=lambda i: -i["pl_investidor"])
        saiu.sort(key=lambda i: -i["pl_investidor"])
        saida[chave] = {
            "entrou": {"produtos": len(entrou), "pl_investidor": round(sum(i["pl_investidor"] for i in entrou), 2),
                       "maiores": entrou[:TOP]},
            "saiu": {"produtos": len(saiu), "pl_investidor": round(sum(i["pl_investidor"] for i in saiu), 2),
                     "maiores": saiu[:TOP]},
        }
    return saida


def montar_bloco(base: BaseIndustria, config: Config | None = None) -> dict:
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

    jan = montar_janelas(base)
    linhas = linhas_do_mes(base, competencia)
    funil = funil_do_mes(base, competencia)
    camadas = camadas_do_universo(base, linhas)
    primeiro, ultimo, pregoes = base.serie.calendario(competencia)
    pf_pj = sum(camadas.segmentos.get(c, 0.0) for c in PF_PJ)
    itens = [(l, dimensoes(base, l)) for l in linhas if l.motivo == UNIVERSO]
    historico = historico_mensal(base)
    rentabilidade = {j: rentabilidade_por_peer_group(base, jan[j]) for j in ("12m", "3m")}

    degraus = {d.motivo: d for d in funil}
    ao_lado = {}
    for chave in CAMADAS_AO_LADO:
        ao_lado[chave] = {
            "rotulo": ROTULOS[chave],
            "produtos": degraus[chave].produtos,
            "pl": round(degraus[chave].pl, 2),
        }
    ao_lado["previdencia"].update(camada_previdencia(base, jan))
    if config is not None:
        ao_lado["etf"].update(camada_etf(config, jan))

    universo_janelas = {j: total(base, jan[j]).to_dict() for j in ORDEM}
    antes = segmentos_do_mes(base, jan["12m"].base).get("total")
    publico = secoes_de_publico(itens)
    por_publico = {j: fluxos_por(base, jan[j], lambda l, d: d.publico) for j in ORDEM}
    for item in publico:
        item["janelas"] = _janelas_de(por_publico, item["publico"])

    bloco = {
        "available": True,
        "competencia": competencia,
        "rotulo_competencia": competencia_br(competencia),
        "informe": {
            "de": primeiro.isoformat() if primeiro else None,
            "ate": ultimo.isoformat() if ultimo else None,
            "pregoes": pregoes,
        },
        "janelas": {j: jan[j].to_dict() for j in ORDEM},
        "prazos": prazos(jan),
        "funil": [d.to_dict() for d in funil],
        "camadas": camadas.to_dict(),
        "dinheiro_pf_pj": round(pf_pj, 2),
        "segmentos": _segmentos(camadas, antes),
        "mercado": {"historico": historico, "janelas": universo_janelas},
        "adjacentes": ao_lado,
        "publico": publico,
        "estrutura": _estrutura(base, jan, itens),
        "estrategias": _estrategias(base, jan, itens, rentabilidade),
        "familias": _familias(base, jan["12m"]),
        "gestoras": _gestoras(base, jan, itens),
        "plataformizacao": _plataformizacao(base, jan),
        "lancamentos": _lancamentos(base, jan),
        "qualidade": _qualidade(base, linhas, historico),
        "avisos": list(base.avisos),
        "fontes": [f.nome for f in fontes_da_industria(com_carteira=base.mapa is not None)],
        "_base": base,
        "_linhas": linhas,
        "_itens": itens,
        "_janelas": jan,
        "_explorador": linhas_do_explorador(base, jan, rentabilidade),
    }
    pares = leitura_com_topicos(bloco)
    bloco["leitura"] = [frase for _, frase in pares]
    bloco["leitura_topicos"] = [topico for topico, _ in pares]
    return bloco


def _maior(itens: list[dict], chave: str, reverso: bool = True) -> dict | None:
    validos = [i for i in itens if (i.get("janelas") or {}).get("12m")]
    if not validos:
        return None
    return sorted(validos, key=lambda i: i["janelas"]["12m"][chave], reverse=reverso)[0]


# O assunto de cada frase da leitura: o painel e o e-mail apontam cada uma
# para o bloco que a detalha, sem depender da posicao (as frases sao
# condicionais - sem plataformizacao, a de canal nao sai).
TOPICOS_DA_LEITURA = ("tamanho", "captacao", "estrategias", "canal", "gestoras")


def leitura(bloco: dict) -> list[str]:
    """As frases que abrem o bloco, iguais em toda superficie.

    Cada uma com numero, janela e base; o prazo com datas sai uma vez, na
    linha de prazos (`bloco["prazos"]`).
    """
    return [frase for _, frase in leitura_com_topicos(bloco)]


def leitura_com_topicos(bloco: dict) -> list[tuple[str, str]]:
    """(topico, frase) de cada frase da leitura; topico em TOPICOS_DA_LEITURA."""
    if not bloco.get("available"):
        return []
    camadas = bloco["camadas"]
    universo = next(d for d in bloco["funil"] if d["motivo"] == UNIVERSO)
    base_total = sum(d["pl"] for d in bloco["funil"])
    fim = data_br(bloco["informe"]["ate"])
    frases = [
        ("tamanho", (
            f"Em {bloco['rotulo_competencia']}, **{reais(camadas['pl_investidor'])}** estavam "
            f"em fundos disponíveis para PF e PJ, na camada do investidor (sem o dinheiro que "
            f"um fundo aplica em outro), no fechamento de {fim}: {milhar(universo['classes'])} "
            f"classes, de um cadastro que soma {reais(base_total)} com dupla contagem."
        )),
    ]
    doze = (bloco.get("mercado") or {}).get("janelas", {}).get("12m")
    if doze and doze.get("sobre_pl") is not None:
        frases.append(("captacao",
            f"Em 12 meses, o investidor pôs **{reais_com_sinal(doze['fluxo_investidor'])}** "
            f"líquidos nesses fundos ({pct(doze['sobre_pl'])} do PL de abertura); o rendimento "
            f"somou {reais_com_sinal(doze['rendimento_investidor'])}."
        ))
    n2 = (bloco.get("estrategias") or {}).get("n2", [])
    ganha, perde = _maior(n2, "fluxo_investidor"), _maior(n2, "fluxo_investidor", reverso=False)
    if ganha and perde and ganha is not perde:
        frases.append(("estrategias",
            f"O peer group que mais captou em 12 meses foi **{ganha['n2']}** "
            f"({reais_com_sinal(ganha['janelas']['12m']['fluxo_investidor'])}); o que mais perdeu, "
            f"**{perde['n2']}** ({reais_com_sinal(perde['janelas']['12m']['fluxo_investidor'])})."
        ))
    plataforma = (bloco.get("plataformizacao") or {}).get("total") or {}
    if plataforma.get("conta_e_ordem") is not None and plataforma.get("variacao_pp") is not None:
        frases.append(("canal",
            f"A fatia do PL que chega por conta e ordem de plataformas foi de "
            f"**{pct(plataforma['conta_e_ordem'])}** ({pp(plataforma['variacao_pp'])} em 12 meses)."
        ))
    tipos = (bloco.get("gestoras") or {}).get("tipos", [])
    independentes = next((t for t in tipos if t["tipo"] == "independente"), None)
    bancos = next((t for t in tipos if t["tipo"] == "banco"), None)
    if independentes and bancos and independentes.get("share") is not None and bancos.get("share") is not None:
        frases.append(("gestoras",
            f"Gestoras bancárias têm {pct(bancos['share'])} do PL ({pp(bancos.get('variacao_pp'))} em "
            f"12 meses); independentes, {pct(independentes['share'])} ({pp(independentes.get('variacao_pp'))})."
        ))
    return frases
