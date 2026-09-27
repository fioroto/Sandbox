"""Relatorio de competicao em ETFs, em markdown e em HTML (CSS inline, para e-mail).

A mesma forma do digest das prateleiras - a leitura primeiro, as tabelas
depois, as fontes com link no fim -, a partir do bloco de
`market.etf_competicao.build_etf_competicao`. Cada numero sai com o prazo dele:
o PL e o do balancete mensal, a comparacao e entre competencias, e cotistas e
fluxo vem da janela de informes do Fundos.NET.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.etf_competicao import (
    casa_da_gestora,
    curto,
    nome_com_ticker,
    pct,
    pp,
    prazos_etf,
    reais,
    reais_mil,
)
from shelf_monitor.report.etf_captacao import nome_curto
from shelf_monitor.report.shared.email_xp import (
    cabecalho,
    casca,
    chamada,
    esc,
    faixa,
    fontes_html,
    legenda,
    mais,
    md_para_html,
    miudo,
    nota,
    pontuado,
    secao,
    tabela,
)
from shelf_monitor.report.shared.estilo import contorno_da_casa, cor_da_casa
from shelf_monitor.report.shared.fontes import CVM_BALANCETE_FIE, CVM_CADASTRO, FUNDOS_NET
from shelf_monitor.report.shared.graficos_email import (
    Linha,
    barras,
    figura,
    numeros_em_destaque,
)
from shelf_monitor.texto import competencia_br, contagem, data_br, milhar

_FONTES = (CVM_BALANCETE_FIE, CVM_CADASTRO, FUNDOS_NET)
_MAX_GESTORAS = 12
_MAX_ESTREIAS = 8
_MAX_MENSAL = 8

STATUS = {"lidera": "lidera", "presente": "presente", "entrando": "lançando", "ausente": "sem ETF"}


def _notas(bloco: dict) -> list[str]:
    return [
        "O segmento é lido do nome do ETF: a CVM deixa vazios o benchmark e as "
        f"classificações dos {bloco['etfs_catalogo']} ETFs (classes FIIM) do cadastro.",
        "Dentro de um segmento os ETFs replicam o mesmo índice e têm a mesma "
        "valorização, então a variação de participação no segmento é fluxo - menos "
        "exata onde o segmento junta índices diferentes (Inflação mistura IMA-B e "
        "NTN-B de prazos distintos).",
        "Cotistas são somados por ETF: quem tem dois ETFs conta duas vezes. Vêm do "
        "maior valor de cada ETF na janela de informes, porque o Fundos.NET traz erro "
        "de preenchimento que derruba o número num dia.",
        "Pré-operacional é ETF registrado e ainda sem operar; estreia é ETF sem PL na "
        "primeira competência da comparação e com PL na última. ETF que começou a operar "
        "depois do último balancete aparece com o PL e a data do último informe do "
        "Fundos.NET, e fica fora das somas de PL, que são todas da data do balancete.",
    ]


def _linhas_de_fontes() -> list[str]:
    linhas = []
    for fonte in _FONTES:
        linha = f"[{fonte.nome}]({fonte.url}): {fonte.uso}."
        if fonte.url_dados:
            linha += f" Arquivos que o monitor lê: [{fonte.url_dados}]({fonte.url_dados})."
        linhas.append(linha)
    return linhas


def _pl(valor: float) -> str:
    return reais(valor) if valor else "-"


def _situacao(segmento: dict) -> str:
    if segmento["foco_share"] is not None:
        return pct(segmento["foco_share"])
    return STATUS[segmento["foco_status"]]


def _lancando(segmento: dict, foco: str | None) -> str:
    """Quem tem ETF pre-operacional no segmento, com a casa em foco em negrito."""
    gestoras = sorted({p["gestora"] for p in segmento["pre_operacionais"]})
    return ", ".join(
        f"**{curto(g)}**" if g == foco else curto(g) for g in gestoras
    ) or "-"


def _posicao_do_foco(segmento: dict, foco: str) -> str:
    """"5,1%, 5ª de 8" / "sem ETF", mais os lancamentos da propria casa."""
    if segmento["foco_share"] is not None:
        texto = (
            f"{pct(segmento['foco_share'])}, {segmento['foco_posicao']}ª de "
            f"{segmento['gestoras']}"
        )
    else:
        texto = "sem ETF operando"
    proprios = len(segmento["foco_pre_operacionais"]) + len(segmento["foco_recentes"])
    if proprios:
        texto += f" ({proprios} em lançamento)"
    return f"{foco}: {texto}"


def _rivais_lancando(segmento: dict, foco: str | None) -> list[str]:
    return sorted({curto(p["gestora"]) for p in segmento["lancando"] if p["gestora"] != foco})


def _lancamentos(
    bloco: dict,
) -> tuple[list[tuple[str, dict[str, int]]], list[dict], list[dict], int]:
    """(pre-operacionais por segmento, estreias na comparacao e depois do
    balancete, ambas do maior PL ao menor, total pre)."""
    pre = [l for l in bloco["lancamentos"] if l["chave"] == "pre_operacional"]
    por_segmento: dict[str, dict[str, int]] = {}
    for lancamento in pre:
        contagem = por_segmento.setdefault(lancamento["segmento"], {})
        contagem[lancamento["gestora"]] = contagem.get(lancamento["gestora"], 0) + 1
    grupos = sorted(por_segmento.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    estreias = sorted(
        (l for l in bloco["lancamentos"] if l["chave"] == "estreia"), key=lambda l: -l["pl"]
    )
    recentes = sorted(
        (l for l in bloco["lancamentos"] if l["chave"] == "recente"), key=lambda l: -l["pl"]
    )
    return grupos, estreias, recentes, len(pre)


def _gestoras_contadas(contagem: dict[str, int], foco: str | None, negrito) -> str:
    partes = []
    for gestora, n in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0])):
        texto = f"{curto(gestora)} ({n})" if n > 1 else curto(gestora)
        partes.append(negrito(texto) if gestora == foco else texto)
    return ", ".join(partes)


def _mensal(bloco: dict) -> tuple[list[str], list[dict]] | None:
    """As maiores gestoras, e a casa em foco se ela ficou de fora, mes a mes."""
    mensal = bloco.get("participacao_mensal") or {}
    competencias = mensal.get("competencias") or []
    if len(competencias) < 2:
        return None
    gestoras = mensal["gestoras"][:_MAX_MENSAL]
    if not any(g["foco"] for g in gestoras):
        gestoras += [g for g in mensal["gestoras"] if g["foco"]]
    return competencias, gestoras


def _periodo_das_estreias(bloco: dict) -> str:
    """"entre os balancetes de 30/06/2026 e 31/08/2026": sem PL no primeiro, com
    PL no segundo. So o mes ("de jun/2026 a ago/2026") nao dizia que a janela
    comeca depois da data-base de junho."""
    if bloco.get("data_pl_inicio") and bloco.get("data_pl"):
        return (
            f"entre os balancetes de {data_br(bloco['data_pl_inicio'])} e "
            f"{data_br(bloco['data_pl'])}"
        )
    return (
        f"de {competencia_br(bloco['competencia_inicio'])} a "
        f"{competencia_br(bloco['competencia'])}"
    )


# --- markdown ---------------------------------------------------------------


def render_etf_markdown(bloco: dict, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    foco = bloco.get("foco") or "casa em foco"
    linhas = [
        f"# Competição em ETFs - {foco}",
        "",
        f"Execução de {gerado_em:%d/%m/%Y %H:%M}.",
        "",
    ]
    if not bloco.get("available"):
        return "\n".join(linhas + [f"_{bloco.get('reason')}_", ""])

    linhas += ["Prazos: " + "; ".join(prazos_etf(bloco)) + ".", ""]
    linhas += [f"- {frase}" for frase in bloco["leitura"]] + [""]

    linhas += [
        "## Gestoras de ETF",
        "",
        "| # | Gestora | ETFs | PL | Participação | Variação (pp) | Cotistas | PL por cotista | Lidera |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for g in bloco["gestoras"][:_MAX_GESTORAS]:
        nome = f"**{g['gestora']}**" if g["foco"] else g["gestora"]
        linhas.append(
            f"| {g['posicao']} | {nome} | {g['etfs']} | {reais(g['pl'])} | {pct(g['share'])} | "
            f"{pp(g['delta_share_pp'])} | {pct(g['share_cotistas'])} | "
            f"{reais_mil(g['pl_por_cotista'])} | {', '.join(g['lidera']) or '-'} |"
        )
    linhas.append("")

    mensal = _mensal(bloco)
    if mensal:
        competencias, gestoras = mensal
        linhas += [
            "Participação no PL, competência a competência:",
            "",
            "| Gestora | " + " | ".join(competencia_br(c) for c in competencias) + " |",
            "|---|" + "---:|" * len(competencias),
        ]
        for g in gestoras:
            nome = f"**{g['gestora']}**" if g["foco"] else g["gestora"]
            linhas.append(
                f"| {nome} | " + " | ".join(pct(g["share"][c]) for c in competencias) + " |"
            )
        linhas.append("")

    linhas += [
        "## Segmentos",
        "",
        f"O que cada ETF replica, do maior mercado para o menor. \"{foco}\" é a participação "
        "dela no PL do segmento; a variação é da primeira à última competência.",
        "",
        f"| Segmento | PL | Líder | {foco} | Posição | Variação (pp) | Lançando |",
        "|---|---:|---|---:|---:|---:|---|",
    ]
    for s in bloco["segmentos"]:
        lider = f"{s['lider']} ({pct(s['share_lider'], 0)})" if s["lider"] else "-"
        linhas.append(
            f"| {s['segmento']} | {_pl(s['pl'])} | {lider} | {_situacao(s)} | "
            f"{s['foco_posicao'] or '-'} | {pp(s['foco_delta_pp'])} | {_lancando(s, foco)} |"
        )
    linhas.append("")

    if bloco["oportunidades"]:
        limiares = bloco.get("limiares") or {}
        linhas += [
            f"## Oportunidades: onde a {foco} é pequena ou não tem ETF",
            "",
            f"Segmentos acima de {reais(limiares.get('pl_oportunidade'))} em que ela tem "
            f"menos de {pct(limiares.get('share_pequena'), 0)} do PL, do maior para o menor:",
            "",
        ]
        for s in bloco["oportunidades"]:
            rivais = _rivais_lancando(s, foco)
            linhas.append(
                f"- **{s['segmento']}** - {reais(s['pl'])}, líder {s['lider']} "
                f"({pct(s['share_lider'], 0)}); {_posicao_do_foco(s, foco)}"
                + (f"; concorrentes lançando: {', '.join(rivais)}" if rivais else "")
            )
        linhas.append("")

    if bloco["defesa"]:
        linhas += [f"## Defesa: onde a {foco} lidera", ""]
        for s in bloco["defesa"]:
            rivais = _rivais_lancando(s, foco)
            linhas.append(
                f"- **{s['segmento']}** - {reais(s['pl'])}, {pct(s['foco_share'])} do segmento "
                f"({pp(s['foco_delta_pp'])} no período); atrás: {s['segundo'] or '-'} "
                f"({pct(s['share_segundo'])})"
                + (f"; concorrentes lançando: {', '.join(rivais)}" if rivais else "")
            )
        linhas.append("")

    grupos, estreias, recentes, total_pre = _lancamentos(bloco)
    if grupos or estreias or recentes:
        linhas += ["## Lançamentos", ""]
        if grupos:
            linhas += [
                f"**Em fase pré-operacional: {total_pre} ETFs** - registrados na CVM, ainda "
                "sem operar. Por segmento:",
                "",
            ]
            linhas += [
                f"- {segmento}: {_gestoras_contadas(contagem, foco, lambda t: f'**{t}**')}"
                for segmento, contagem in grupos
            ]
            linhas.append("")
        if estreias:
            linhas += [
                f"**Começaram a operar {_periodo_das_estreias(bloco)}: "
                f"{contagem(len(estreias), 'ETF', 'ETFs')}**, do maior PL para o menor:",
                "",
            ]
            for lancamento in estreias[:_MAX_ESTREIAS]:
                gestora = f"**{lancamento['gestora']}**" if lancamento["foco"] else lancamento["gestora"]
                linhas.append(
                    f"- {gestora} - {nome_com_ticker(lancamento['nome'], lancamento.get('ticker'))} "
                    f"({lancamento['segmento']}): "
                    f"{reais(lancamento['pl'])}"
                )
            resto = len(estreias) - _MAX_ESTREIAS
            if resto > 0:
                linhas.append(f"- ... e mais {resto} (planilha, aba Lançamentos)")
            linhas.append("")
        if recentes:
            linhas += [
                f"**Começaram a operar depois do balancete de {data_br(bloco.get('data_pl'))}: "
                f"{contagem(len(recentes), 'ETF', 'ETFs')}**, com o PL do último informe no "
                "Fundos.NET, do maior para o menor:",
                "",
            ]
            for lancamento in recentes[:_MAX_ESTREIAS]:
                gestora = f"**{lancamento['gestora']}**" if lancamento["foco"] else lancamento["gestora"]
                linhas.append(
                    f"- {gestora} - {nome_com_ticker(lancamento['nome'], lancamento.get('ticker'))} "
                    f"({lancamento['segmento']}): "
                    f"{reais(lancamento['pl'])} em {data_br(lancamento['data_pl'])}"
                )
            resto = len(recentes) - _MAX_ESTREIAS
            if resto > 0:
                linhas.append(f"- ... e mais {resto} (planilha, aba Lançamentos)")
            linhas.append("")

    linhas += ["## Como ler", ""] + [f"- {nota}" for nota in _notas(bloco)] + [""]
    linhas += ["## Fontes", "", "Onde conferir cada número deste relatório:", ""]
    linhas += [f"- {linha}" for linha in _linhas_de_fontes()] + [""]
    return "\n".join(linhas)


# --- HTML -------------------------------------------------------------------
#
# O padrao executivo da XP (shared/email_xp.py e shared/graficos_email.py) e a
# secao 5 da linguagem visual: as leituras viram numeros e uma tabela de
# "tema e prazo | numeros"; cada bloco abre por uma figura com titulo que
# afirma, calculado dos dados; toda lista de ETF ou de segmento e tabela; a
# metodologia fica numa linha cada, no fim. O markdown continua com as frases
# inteiras - e o que se le no texto puro.

_ROTULO_DO_MONITOR = "Monitor de ETFs"
_MAX_BARRAS = 10


def _marca(gestora: str | None, foco: bool) -> dict:
    """Cor da gestora: a da casa monitorada a que ela pertence (XP, BTG, Itau,
    Nu, Inter), cinza para as demais - nunca um matiz novo."""
    casa = casa_da_gestora(gestora) or ""
    return {"cor": cor_da_casa(casa), "contorno": contorno_da_casa(casa), "destaque": foco}


def _prazo_do_pl(bloco: dict) -> str:
    """"balancete de ago/2026 (data-base 31/08/2026)"."""
    return (
        f"balancete de {competencia_br(bloco['competencia'])} "
        f"(data-base {data_br(bloco.get('data_pl'))})"
    )


def _comparacao(bloco: dict) -> str:
    return f"de {competencia_br(bloco['competencia_inicio'])} a {competencia_br(bloco['competencia'])}"


def _fonte(*fontes) -> str:
    return "Fonte: " + "; ".join(md_para_html(f"[{f.nome}]({f.url})") for f in fontes) + "."


def _rotulo(texto: str, detalhe: str = "") -> str:
    """Celula de tema: o tema em negrito e o prazo (ou a unidade) miudo embaixo."""
    return f"<strong>{esc(texto)}</strong>" + (miudo(esc(detalhe)) if detalhe else "")


def _leituras(bloco: dict, foco: str) -> str:
    """Os numeros-chave da casa em foco e a tabela "tema e prazo | numeros".

    Tomam o lugar das frases de leitura (que seguem no markdown): cada linha
    diz o tema e o prazo a esquerda e so numero a direita, com o ponto medio no lugar
    de conjuncao.
    """
    base = next((g for g in bloco["gestoras"] if g["foco"]), None)
    lider = bloco["gestoras"][0] if bloco["gestoras"] else None
    lidera = [s for s in bloco["segmentos"] if s["foco_status"] == "lidera"]
    corte = reais((bloco.get("limiares") or {}).get("pl_oportunidade"))
    sem_etf = [s for s in bloco["oportunidades"] if s["foco_share"] is None]
    pequena = [s for s in bloco["oportunidades"] if s["foco_share"] is not None]
    grupos, _estreias, recentes, total_pre = _lancamentos(bloco)
    pre_do_foco = sum(contagem_.get(foco, 0) for _, contagem_ in grupos)
    cotistas = bloco.get("cotistas") or {}

    numeros = []
    if base:
        numeros += [
            (f"{base['posicao']}ª", f"gestora em PL de ETF, de {len(bloco['gestoras'])}", True),
            (pct(base["share"]), f"do PL de ETFs ({reais(base['pl'])})"),
            (str(len(lidera)), "segmentos liderados" if len(lidera) != 1 else "segmento liderado"),
            (str(len(sem_etf)), f"segmentos sem ETF acima de {corte}"),
            (f"{pre_do_foco} de {total_pre}", "ETFs em fase pré-operacional"),
        ]
        if base.get("share_cotistas") is not None:
            numeros.append((pct(base["share_cotistas"]), "dos cotistas de ETF"))

    linhas = []
    if base and lider:
        linhas.append([
            _rotulo("PL de ETFs", _prazo_do_pl(bloco)),
            esc(pontuado(
                f"{reais(bloco['pl_total'])} em {bloco['etfs_com_pl']} ETFs",
                f"{foco} {base['posicao']}ª, {reais(base['pl'])} ({pct(base['share'])})",
                f"líder {curto(lider['gestora'])}, {pct(lider['share'])}",
            )),
        ])
    if lidera:
        linhas.append([
            _rotulo(f"Onde a {foco} lidera", "% do PL do segmento"),
            esc(pontuado(*(f"{s['segmento']} {pct(s['foco_share'], 0)}" for s in lidera))),
        ])
    if sem_etf:
        linhas.append([
            _rotulo(f"Sem ETF da {foco}", f"segmentos acima de {corte}"),
            esc(pontuado(
                f"{contagem(len(sem_etf), 'segmento', 'segmentos')}, "
                f"{reais(sum(s['pl'] for s in sem_etf))}",
                *(f"{s['segmento']} {reais(s['pl'])} ({curto(s['lider'])})" for s in sem_etf[:4]),
            )),
        ])
    if pequena:
        linhas.append([
            _rotulo("Onde é pequena", "participação e posição no segmento"),
            esc(pontuado(*(
                f"{s['segmento']} {pct(s['foco_share'])} ({s['foco_posicao']}ª de {s['gestoras']})"
                for s in pequena[:4]
            ))),
        ])
    if base and bloco["competencia_inicio"] != bloco["competencia"]:
        movimentos = sorted(
            (s for s in bloco["segmentos"] if s.get("foco_delta_pp") is not None),
            key=lambda s: -abs(s["foco_delta_pp"]),
        )[:3]
        linhas.append([
            _rotulo(f"Participação da {foco}", _comparacao(bloco)),
            esc(pontuado(
                f"{pct(base['share_inicio'])} → {pct(base['share'])}",
                *(f"{s['segmento']} {pp(s['foco_delta_pp'])}" for s in movimentos),
            )),
        ])
    if total_pre or recentes:
        do_foco = sum(1 for l in recentes if l["foco"])
        linhas.append([
            _rotulo("Lançamentos"),
            esc(pontuado(
                f"{total_pre} pré-operacionais, {pre_do_foco} da {foco}" if total_pre else "",
                f"{contagem(len(recentes), 'estreia', 'estreias')} depois de "
                f"{data_br(bloco.get('data_pl'))} ({reais(sum(l['pl'] for l in recentes))}), "
                f"{do_foco} da {foco}" if recentes else "",
            )),
        ])
    if base and cotistas.get("total"):
        janela = f"informes de {data_br(cotistas.get('de'))} a {data_br(cotistas.get('ate'))}"
        linhas.append([
            _rotulo("Cotistas", janela),
            esc(pontuado(
                f"{foco} {milhar(base['cotistas'])} ({pct(base['share_cotistas'])} dos cotistas)",
                f"{reais_mil(base['pl_por_cotista'])} por cotista, contra "
                f"{reais_mil(bloco['pl_total'] / cotistas['total'])} no mercado",
            )),
        ])
    return (
        secao("Principais leituras")
        + numeros_em_destaque(numeros)
        + tabela(["Tema e prazo", "Números"], linhas, larguras={0: 32})
    )


def _figura_das_gestoras(bloco: dict, foco: str) -> str:
    """Participacao no PL por gestora, a casa em foco destacada."""
    gestoras = bloco["gestoras"][:_MAX_BARRAS]
    if not any(g["foco"] for g in gestoras):
        gestoras += [g for g in bloco["gestoras"] if g["foco"]]
    linhas = [
        Linha(g["gestora"], g["share"], pct(g["share"]), **_marca(g["gestora"], g["foco"]))
        for g in gestoras if g.get("share")
    ]
    if not linhas:
        return ""
    lider = bloco["gestoras"][0]
    base = next((g for g in bloco["gestoras"] if g["foco"]), None)
    titulo = f"{curto(lider['gestora'])} lidera o PL de ETFs, com {pct(lider['share'])}"
    if base and base is not lider:
        titulo += f"; {foco} é a {base['posicao']}ª, com {pct(base['share'])}"
    elif base:
        titulo = f"{foco} lidera o PL de ETFs, com {pct(base['share'])}"
    return figura(
        titulo,
        pontuado("Participação no PL de ETFs, %", _prazo_do_pl(bloco)),
        barras(linhas),
        _fonte(CVM_BALANCETE_FIE),
    )


def _figura_dos_segmentos(bloco: dict, foco: str) -> str:
    """Participacao da casa em foco em cada segmento em que tem ETF."""
    presentes = sorted(
        (s for s in bloco["segmentos"] if s["foco_share"]), key=lambda s: -s["foco_share"]
    )
    # Abaixo de 1% a barra nao se ve; esses segmentos vao por nome no rodape.
    miudos = [s for s in presentes if s["foco_share"] < 0.01]
    presentes = [s for s in presentes if s["foco_share"] >= 0.01]
    if not presentes:
        return ""
    linhas = [
        Linha(s["segmento"], s["foco_share"], pct(s["foco_share"], 0), **_marca(foco, False))
        for s in presentes
    ]
    lidera = sum(1 for s in bloco["segmentos"] if s["foco_status"] == "lidera")
    corte = (bloco.get("limiares") or {}).get("pl_oportunidade")
    sem_etf = [s for s in bloco["oportunidades"] if s["foco_share"] is None]
    titulo = (
        f"{foco} lidera {contagem(lidera, 'segmento', 'segmentos')} de "
        f"{len(bloco['segmentos'])}"
        + (f"; não tem ETF em {len(sem_etf)} acima de {reais(corte)}" if sem_etf else "")
    )
    return figura(
        titulo,
        pontuado(f"Participação da {foco} no PL de cada segmento em que tem ETF, %",
                 _prazo_do_pl(bloco)),
        barras(linhas),
        (f"Abaixo de 1%: {esc(', '.join(s['segmento'] for s in miudos))}. " if miudos else "")
        + _fonte(CVM_BALANCETE_FIE),
    )


def _figura_dos_lancamentos(bloco: dict, foco: str, gerado_em: datetime) -> str:
    """ETFs em fase pre-operacional por gestora."""
    pre = [l for l in bloco["lancamentos"] if l["chave"] == "pre_operacional"]
    if not pre:
        return ""
    por_gestora: dict[str, int] = {}
    for lancamento in pre:
        por_gestora[lancamento["gestora"]] = por_gestora.get(lancamento["gestora"], 0) + 1
    ordem = sorted(por_gestora.items(), key=lambda kv: (-kv[1], kv[0] != foco, kv[0]))
    linhas = [
        Linha(curto(g), n, str(n), **_marca(g, g == foco)) for g, n in ordem
    ]
    do_foco = por_gestora.get(foco, 0)
    lider, maior = ordem[0]
    if lider == foco:
        titulo = f"{foco} prepara mais lançamentos: {do_foco} dos {len(pre)} ETFs pré-operacionais"
    else:
        titulo = (
            f"{curto(lider)} prepara mais lançamentos ({maior}); {foco} tem {do_foco} dos "
            f"{len(pre)} ETFs pré-operacionais"
        )
    return figura(
        titulo,
        pontuado("ETFs registrados na CVM e ainda sem operar, por gestora",
                 f"cadastro lido em {gerado_em:%d/%m/%Y}"),
        barras(linhas),
        _fonte(CVM_CADASTRO),
    )


def render_etf_html(bloco: dict, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    foco = bloco.get("foco") or "casa em foco"
    titulo = f"Competição em ETFs - {foco}"
    if not bloco.get("available"):
        corpo = chamada(esc(bloco.get("reason")))
        previa = esc(bloco.get("reason"))
    else:
        corpo = _corpo_html(bloco, foco, gerado_em)
        previa = esc(re.sub(r"\*\*|\[|\]\([^)]*\)", "", bloco["leitura"][0])) if bloco["leitura"] else ""
    corpo = (
        cabecalho("Gestoras e segmentos de ETF", titulo, f"Execução de {gerado_em:%d/%m/%Y %H:%M}")
        + corpo
    )
    return casca(
        esc(titulo), previa, corpo, faixa(_ROTULO_DO_MONITOR, f"{gerado_em:%d/%m/%Y}")
    )


def _tabela_de_gestoras(bloco: dict) -> str:
    gestoras = bloco["gestoras"][:_MAX_GESTORAS]
    return tabela(
        ["#", "Gestora", "ETFs", "PL", "Part.", "Var. (pp)", "Cotistas", "PL/cotista", "Lidera"],
        [
            [
                str(g["posicao"]), esc(g["gestora"]), str(g["etfs"]), esc(reais(g["pl"])),
                esc(pct(g["share"])), esc(pp(g["delta_share_pp"])), esc(pct(g["share_cotistas"])),
                esc(reais_mil(g["pl_por_cotista"])),
                esc(contagem(len(g["lidera"]), "segmento", "segmentos") if g["lidera"] else "-"),
            ]
            for g in gestoras
        ],
        direita={0, 2, 3, 4, 5, 6, 7, 8}, sinal={5}, compacta=True,
        base=[g["foco"] for g in gestoras],
    )


def _tabela_mensal(bloco: dict) -> str:
    mensal = _mensal(bloco)
    if not mensal:
        return ""
    competencias, gestoras = mensal
    return legenda(
        f"**Participação no PL, competência a competência** · {_comparacao(bloco)}"
    ) + tabela(
        ["Gestora", *[competencia_br(c) for c in competencias]],
        [[esc(g["gestora"]), *[esc(pct(g["share"][c])) for c in competencias]] for g in gestoras],
        direita=range(1, len(competencias) + 1), base=[g["foco"] for g in gestoras],
    )


def _tabela_de_segmentos(bloco: dict, foco: str) -> str:
    segmentos = bloco["segmentos"]
    return tabela(
        ["Segmento", "PL", "Líder", foco, "Posição", "Var. (pp)", "Lançando"],
        [
            [
                esc(s["segmento"]), esc(_pl(s["pl"])),
                esc(f"{curto(s['lider'])} {pct(s['share_lider'], 0)}" if s["lider"] else "-"),
                esc(_situacao(s)), esc(s["foco_posicao"] or "-"), esc(pp(s["foco_delta_pp"])),
                md_para_html(_lancando(s, foco)),
            ]
            for s in segmentos
        ],
        direita={1, 3, 4, 5}, sinal={5}, compacta=True,
        base=[s["foco_status"] == "lidera" for s in segmentos],
    )


def _lista_de_etfs(lancamentos: list[dict], com_data: bool) -> str:
    """Gestora | Ticker | ETF | Segmento | PL (com a data embaixo, se houver)."""
    mostrados = lancamentos[:_MAX_ESTREIAS]
    return tabela(
        ["Gestora", "Ticker", "ETF", "Segmento", "PL"],
        [
            [
                esc(curto(l["gestora"])), f"<strong>{esc(l['ticker'])}</strong>" if l.get("ticker") else "-",
                esc(nome_curto(l["nome"])), esc(l["segmento"]),
                esc(reais(l["pl"])) + (miudo(f"em {data_br(l['data_pl'])}") if com_data else ""),
            ]
            for l in mostrados
        ],
        direita={4}, compacta=True, base=[l["foco"] for l in mostrados],
    ) + mais(len(lancamentos) - len(mostrados), "na planilha, aba Lançamentos")


def _notas_curtas(bloco: dict) -> list[str]:
    """"Como ler" em uma linha cada; as frases inteiras ficam no markdown."""
    return [
        f"Segmento lido do nome do ETF: a CVM deixa vazios benchmark e classificação dos "
        f"{bloco['etfs_catalogo']} ETFs do cadastro.",
        "Dentro do segmento, variação de participação é fluxo (mesmo índice, mesma "
        "valorização); menos exata onde o segmento junta índices diferentes.",
        "Cotista contado uma vez por ETF (quem tem dois conta duas vezes), no maior valor "
        "da janela de informes.",
        "Pré-operacional: registrado e sem operar. Estreia: sem PL na primeira competência e "
        "com PL na última. Quem estreou depois do balancete sai com o PL do último informe e "
        "fica fora das somas.",
    ]


def _corpo_html(bloco: dict, foco: str, gerado_em: datetime) -> str:
    limiares = bloco.get("limiares") or {}
    corte = reais(limiares.get("pl_oportunidade"))
    pequena = pct(limiares.get("share_pequena"), 0)
    partes = [
        nota(esc("Prazos: " + " · ".join(prazos_etf(bloco)) + ".")),
        _leituras(bloco, foco),
        secao("Gestoras de ETF"),
        _figura_das_gestoras(bloco, foco),
        _tabela_de_gestoras(bloco),
        _tabela_mensal(bloco),
        secao("Segmentos"),
        _figura_dos_segmentos(bloco, foco),
        legenda(f"**Todos os segmentos** · do maior PL ao menor · {foco}: participação no "
                f"PL do segmento · variação {_comparacao(bloco)}"),
        _tabela_de_segmentos(bloco, foco),
    ]
    if bloco["oportunidades"]:
        partes += [
            secao(f"Oportunidades: onde a {esc(foco)} é pequena ou não tem ETF"),
            legenda(f"**Segmentos acima de {corte}** com menos de {pequena} da {foco} · "
                    "do maior PL ao menor"),
            tabela(
                ["Segmento", "PL", "Líder", foco, "Lançando"],
                [
                    [
                        f"<strong>{esc(s['segmento'])}</strong>", esc(reais(s["pl"])),
                        esc(f"{curto(s['lider'])} {pct(s['share_lider'], 0)}"),
                        esc(
                            f"{pct(s['foco_share'])}, {s['foco_posicao']}ª de {s['gestoras']}"
                            if s["foco_share"] is not None else "sem ETF"
                        ) + (
                            miudo(f"{_em_lancamento(s)} em lançamento")
                            if s["foco_pre_operacionais"] or s["foco_recentes"] else ""
                        ),
                        esc(", ".join(_rivais_lancando(s, foco)) or "-"),
                    ]
                    for s in bloco["oportunidades"]
                ],
                direita={1}, compacta=True, larga=True,
            ),
        ]
    if bloco["defesa"]:
        partes += [
            secao(f"Defesa: onde a {esc(foco)} lidera"),
            tabela(
                ["Segmento", "PL", foco, "Var. (pp)", "2º", "Lançando"],
                [
                    [
                        f"<strong>{esc(s['segmento'])}</strong>", esc(reais(s["pl"])),
                        esc(pct(s["foco_share"])), esc(pp(s["foco_delta_pp"])),
                        esc(f"{curto(s['segundo'])} {pct(s['share_segundo'])}" if s["segundo"] else "-"),
                        esc(", ".join(_rivais_lancando(s, foco)) or "-"),
                    ]
                    for s in bloco["defesa"]
                ],
                direita={1, 2, 3}, sinal={3}, compacta=True,
            ),
        ]
    grupos, estreias, recentes, total_pre = _lancamentos(bloco)
    if grupos or estreias or recentes:
        partes += [secao("Lançamentos"), _figura_dos_lancamentos(bloco, foco, gerado_em)]
        if grupos:
            partes += [
                legenda(f"**Em fase pré-operacional: {total_pre} ETFs** · registrados na CVM, "
                        "ainda sem operar"),
                tabela(
                    ["Segmento", "ETFs", "Gestoras"],
                    [
                        [
                            esc(segmento), str(sum(contagem_.values())),
                            _gestoras_contadas_html(contagem_, foco),
                        ]
                        for segmento, contagem_ in grupos
                    ],
                    direita={1}, compacta=True,
                ),
            ]
        if estreias:
            partes += [
                legenda(f"**Começaram a operar {_periodo_das_estreias(bloco)}: "
                        f"{contagem(len(estreias), 'ETF', 'ETFs')}** · do maior PL ao menor"),
                _lista_de_etfs(estreias, com_data=False),
            ]
        if recentes:
            partes += [
                legenda(f"**Começaram a operar depois do balancete de "
                        f"{data_br(bloco.get('data_pl'))}: {contagem(len(recentes), 'ETF', 'ETFs')}** "
                        "· PL do último informe no Fundos.NET"),
                _lista_de_etfs(recentes, com_data=True),
            ]
    partes += [
        secao("Como ler"),
        *(nota(esc(linha)) for linha in _notas_curtas(bloco)),
        fontes_html(_FONTES),
    ]
    return "\n".join(p for p in partes if p)


def _em_lancamento(segmento: dict) -> int:
    """ETFs da casa em foco no segmento que ainda nao entraram no PL do balancete."""
    return len(segmento["foco_pre_operacionais"]) + len(segmento["foco_recentes"])


def _gestoras_contadas_html(contagem_: dict[str, int], foco: str | None) -> str:
    """"BTG (2), Itau, **XP**" - como `_gestoras_contadas`, com cada nome escapado."""
    partes = []
    for gestora, n in sorted(contagem_.items(), key=lambda kv: (-kv[1], kv[0])):
        texto = esc(f"{curto(gestora)} ({n})" if n > 1 else curto(gestora))
        partes.append(f"<strong>{texto}</strong>" if gestora == foco else texto)
    return ", ".join(partes)

def write_etf_digest(
    config: Config, bloco: dict, gerado_em: datetime | None = None
) -> tuple[Path, Path]:
    """Grava `etf-competicao-<data>.md` e `.html` na pasta de relatorios."""
    gerado_em = gerado_em or datetime.now()
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    base = config.reports_dir / f"etf-competicao-{gerado_em:%Y-%m-%d}"
    md = base.with_suffix(".md")
    html = base.with_suffix(".html")
    md.write_text(render_etf_markdown(bloco, gerado_em), encoding="utf-8")
    html.write_text(render_etf_html(bloco, gerado_em), encoding="utf-8")
    return md, html
