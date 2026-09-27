"""Captacao de ETF mes a mes, em markdown e em HTML.

As quatro visoes do bloco de competicao - gestoras, segmentos, oportunidades e
defesa, e cada ETF com o seu segmento - pela captacao de cada mes, com o estado
do mes (acelerando, captando, virou saida, saida persistente). Sai do bloco de
`market.etf_captacao_mensal.build_captacao_mensal`; cada numero traz o prazo
dele, e as fontes fecham o relatorio com link.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.etf_captacao_mensal import (
    DETALHE,
    SIMBOLOS,
    legenda,
    prazos_captacao,
    reais_com_sinal,
    rotulo,
)
from shelf_monitor.market.etf_competicao import casa_da_gestora, curto, pct, reais, reais_mil
from shelf_monitor.report.shared.email_xp import (
    cabecalho,
    casca,
    chamada,
    esc,
    faixa,
    fontes_html,
    legenda as titulo_de_tabela,
    md_para_html,
    miudo,
    nota,
    pontuado,
    secao,
    tabela,
)
from shelf_monitor.report.shared.estilo import (
    COMPONENTE_1,
    SUPERFICIE_2,
    TINTA,
    contorno_da_casa,
    cor_da_casa,
)
from shelf_monitor.report.shared.fontes import (
    ANBIMA_BOLETIM,
    B3_FUNDOS_LISTADOS,
    CVM_BALANCETE_FIE,
    CVM_CADASTRO,
    FUNDOS_NET,
)
from shelf_monitor.report.shared.graficos_email import (
    Linha,
    Par,
    Serie,
    barras_divergentes,
    barras_pareadas,
    colunas_por_casa,
    figura,
    numeros_em_destaque,
    passo_divergente,
)
from shelf_monitor.texto import competencia_br, contagem, periodo_br

_FONTES = (FUNDOS_NET, CVM_BALANCETE_FIE, CVM_CADASTRO, B3_FUNDOS_LISTADOS)
_MAX_GESTORAS = 12
_MAX_ETFS = 15
_MAX_SAIDAS = 10
# (fundo, texto) de cada estado, na planilha (etf_report). O HTML pinta a grade
# pelo valor, na escala divergente dos tokens de status (`passo_divergente`).
CORES = {
    "acelerando": ("1E7B34", "FFFFFF"),
    "captando": ("C6EFCE", "0B3D17"),
    "sem_movimento": ("EDEDEA", "52514E"),
    "virou_saida": ("F8CBAD", "7A3500"),
    "saida_persistente": ("C00000", "FFFFFF"),
    "sem_dado": ("FFF2CC", "6B5500"),
}
def mes_curto(competencia: str) -> str:
    """"202609" -> "set/26" - cabecalho de coluna de mes."""
    texto = competencia_br(competencia)
    return texto[:-4] + texto[-2:]


def valor_compacto(valor: float | None) -> str:
    """"+2,5 bi", "+302 mi", "-9 mi", "+40 mil" - celula de mes."""
    if valor is None:
        return ""
    if abs(valor) >= 1e9:
        texto = f"{valor / 1e9:+.1f} bi"
    elif abs(valor) >= 1e6:
        texto = f"{valor / 1e6:+.0f} mi"
    else:
        texto = f"{valor / 1e3:+.0f} mil"
    return texto.replace(".", ",")


def _simbolo(estado: str) -> str:
    return SIMBOLOS.get(estado, "")


def _atual(item: dict, ultimo: str) -> str:
    """"+R$ 302 mi (captando)" - o ultimo mes de uma linha."""
    return f"{reais_com_sinal(item['fluxo'].get(ultimo))} ({rotulo(item['estado_atual'])})"


def _notas(bloco: dict) -> list[str]:
    limiares = bloco.get("limiares") or {}
    cobertura = bloco.get("cobertura") or {}
    if _com_informe(bloco):
        metodo = [
            "Captação vem do informe diário de cada ETF no Fundos.NET: a soma, dia a dia, da "
            "captação menos o resgate que o administrador declarou. Cada dia é conferido contra "
            "a diferença de PL sem a valorização - a variação da quantidade de cotas, que só muda "
            "quando o agente autorizado cria ou resgata cotas. Quando os dois não fecham (folga "
            "de 5%), vale a diferença de PL, e a correção aparece como ajuste.",
            "Diferença de PL é o PL do último informe do mês menos o do mês anterior: captação "
            "líquida mais valorização (e menos rendimento distribuído).",
        ]
        ajustes = cobertura.get("ajuste_por_gestora") or []
        if ajustes:
            metodo.append(
                "Onde o ajuste pesa em 12 meses: "
                + "; ".join(
                    f"{a['gestora']}, {reais_com_sinal(a['ajuste'])} (declarou captação de "
                    f"{reais(a['captacao'] or 0)} e resgates de {reais(a['resgates'] or 0)})"
                    for a in ajustes[:4]
                )
                + ". Ajuste do tamanho da captação é gestora que não preenche captação e resgate "
                "no informe; ajuste positivo com resgate declarado é resgate sem mudança de cotas "
                "(o padrão de pagamento de rendimento)."
            )
        if cobertura.get("sem_informe_do_mes"):
            metodo.append(
                f"{contagem(cobertura['sem_informe_do_mes'], 'mês de ETF ficou', 'meses de ETF ficaram')} "
                "sem o informe do mês (página que não veio do Fundos.NET): nesses, vale a variação "
                "de cotas entre os fechamentos, vezes a cota média do mês."
            )
    else:
        metodo = [
            "Captação é a variação da quantidade de cotas entre dois fechamentos, vezes a cota "
            "média do mês. Cota só muda de quantidade quando o agente autorizado cria ou resgata "
            "cotas; valorização e distribuição mexem na cota, não na quantidade. Num mês de "
            "mercado muito volátil o valor em reais é aproximado; o sinal, não.",
        ]
    notas = [
        *metodo,
        f"Estados: acelerando é captar mais que no mês anterior, que também captou; virou "
        f"saída é o primeiro mês de saída; saída persistente, o segundo seguido ou mais; "
        f"sem movimento é captação abaixo de {pct(limiares.get('materialidade'))} do PL do "
        f"início do mês, e nunca menos de {reais_mil(limiares.get('fluxo_minimo'))}, para mais "
        "ou para menos.",
        (
            "No mês de estreia, a captação é o que o informe declara desde o primeiro dia, "
            "conferido contra o PL; o monitor conta a estreia como captação."
            if _com_informe(bloco) else "No mês de estreia, a captação é o PL do fechamento."
        ) + " ETF cancelado sai do cadastro da CVM e fica fora da conta.",
        f"% da captação é a parte da gestora na captação líquida de todos os ETFs em 12 "
        f"meses; a oportunidade é o segmento que captou mais de "
        f"{reais(limiares.get('captacao_oportunidade'))} em 12 meses e onde a casa em foco "
        f"ficou com menos de {pct(limiares.get('share_pequena'), 0)} disso.",
    ]
    eventos = cobertura.get("eventos_na_cota") or []
    if eventos:
        notas.append(
            "Desdobramento ou grupamento de cotas: o fator vem do retorno dos pares do "
            "segmento no mês - "
            + "; ".join(f"{nome_curto(nome)}, {competencia_br(m)}, {o}" for nome, m, o in eventos)
            + "."
        )
    if cobertura.get("sem_dado"):
        notas.append(
            f"{contagem(cobertura['sem_dado'], 'mês de ETF ficou', 'meses de ETF ficaram')} "
            "sem dado (fechamento faltando no Fundos.NET); o total do mês soma os ETFs que "
            "têm os dois fechamentos."
        )
    return notas


def _com_informe(bloco: dict) -> bool:
    """A captacao saiu do informe diario do mes (e nao so dos fechamentos)."""
    return bloco.get("metodo") == "informe"


def _doze(item: dict, coluna: str) -> float | None:
    """Captacao, resgates, ajuste ou diferenca de PL de uma linha, em 12 meses."""
    return ((item.get("detalhe") or {}).get("doze_meses") or {}).get(coluna)


def _saida(valor: float | None) -> float | None:
    """Resgate com o sinal de saida: "-R$ 4,7 bi" soma com a captacao a olho."""
    return -valor if valor else valor


def _bruto(valor: float | None) -> str:
    """Captacao declarada: um valor sem direcao."""
    return reais(valor) if valor is not None else "-"


def _resgate(valor: float | None) -> str:
    """"-R$ 4,7 bi"; zero sem sinal."""
    if valor is None:
        return "-"
    return reais_com_sinal(-valor) if valor else reais(0)


def _variacao(valor: float | None) -> str:
    """Ajuste e diferenca de PL: com direcao, e zero sem sinal."""
    if valor is None:
        return "-"
    return reais_com_sinal(valor) if valor else reais(0)


def _tem_sem_informe(item: dict) -> bool:
    """Algum mes saiu so dos fechamentos (pagina do informe que nao veio)."""
    return any(v for v in ((item.get("detalhe") or {}).get("sem_informe") or {}).values())


def _estreias_texto(valor: float | None) -> str:
    """Estreia zero vira traco: "+R$ 0 mi" em quase todo mes so faz ruido."""
    return reais_com_sinal(valor) if valor else "-"


def _estreias_em(historico: dict, meses: list[str]) -> float | None:
    valores = [(historico.get(m) or {}).get("estreias") for m in meses]
    valores = [v for v in valores if v is not None]
    return sum(valores) if valores else None


def _destaques_da_conferencia(conferencia: dict) -> list[str]:
    """"out/2025: +R$ 2,5 bi de diferenca, com estreias de ..." - os meses que pedem explicacao."""
    frases = []
    for d in conferencia.get("destaques") or []:
        etfs = ", ".join(
            f"{e['ticker'] or nome_curto(e['nome'])} ({reais(e['valor'])})" for e in d["etfs_em_estreia"][:3]
        )
        frases.append(
            f"{competencia_br(d['mes'])}: diferença de {reais_com_sinal(d['diferenca'])}, com "
            f"{reais_com_sinal(d['estreias'])} de estreias no mês"
            + (f" ({etfs})" if etfs else "")
        )
    return frases


def _conferencia_markdown(conferencia: dict | None) -> list[str]:
    if not conferencia:
        return []
    linhas = [
        "## Conferência com a ANBIMA",
        "",
        f"Boletim de Fundos de Investimento da ANBIMA (anexo `{conferencia['arquivo']}`), na janela "
        "dele. A ANBIMA soma o que os administradores associados informam; o monitor, todos os ETFs "
        "da CVM, e conta o PL de estreia como captação.",
        "",
        "| Mês | Monitor | Das quais estreias | ANBIMA | Diferença | PL: ANBIMA / monitor |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for l in conferencia["linhas"]:
        linhas.append(
            f"| {competencia_br(l['mes'])} | {reais_com_sinal(l['monitor'])} | "
            f"{_estreias_texto(l['estreias'])} | {reais_com_sinal(l['anbima'])} | "
            f"{reais_com_sinal(l['diferenca'])} | {pct(l['cobertura'])} |"
        )
    for rotulo_, soma in (("12 meses", conferencia["doze_meses"]), ("No ano", conferencia.get("no_ano"))):
        if soma:
            linhas.append(
                f"| **{rotulo_}** | **{reais_com_sinal(soma['monitor'])}** | "
                f"{reais_com_sinal(soma['estreias'])} | **{reais_com_sinal(soma['anbima'])}** | "
                f"{reais_com_sinal(soma['monitor'] - soma['anbima'])} | |"
            )
    linhas.append("")
    destaques = _destaques_da_conferencia(conferencia)
    if destaques:
        linhas += ["Onde a diferença passou de R$ 1 bi:", ""] + [f"- {d}" for d in destaques] + [""]
    return linhas


def _linhas_de_fontes(bloco: dict | None = None) -> list[str]:
    linhas = []
    fontes = (*_FONTES, ANBIMA_BOLETIM) if (bloco or {}).get("anbima") else _FONTES
    for fonte in fontes:
        linha = f"[{fonte.nome}]({fonte.url}): {fonte.uso}."
        if fonte.url_dados:
            linha += f" Arquivos que o monitor lê: [{fonte.url_dados}]({fonte.url_dados})."
        linhas.append(linha)
    return linhas


def _gestoras_da_tabela(bloco: dict) -> list[dict]:
    gestoras = bloco["gestoras"][:_MAX_GESTORAS]
    if not any(g["foco"] for g in gestoras):
        gestoras += [g for g in bloco["gestoras"] if g["foco"]]
    return gestoras


def _destaques_de_etf(bloco: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(maiores captacoes, maiores saidas, ETFs da casa em foco), em 12 meses."""
    etfs = [e for e in bloco["etfs"] if e["doze_meses"] is not None]
    captacoes = sorted((e for e in etfs if e["doze_meses"] > 0), key=lambda e: -e["doze_meses"])
    saidas = sorted((e for e in etfs if e["doze_meses"] < 0), key=lambda e: e["doze_meses"])
    da_casa = sorted((e for e in bloco["etfs"] if e["foco"]), key=lambda e: -(e["doze_meses"] or 0))
    return captacoes[:_MAX_ETFS], saidas[:_MAX_SAIDAS], da_casa


def nome_curto(nome: str) -> str:
    """O nome do ETF sem o sufixo juridico ("FUNDO DE INDICE ...", "CLASSE ...")."""
    for corte in (" FUNDO DE ÍNDICE", " FUNDO DE INDICE", " CLASSE DE", " CLASSE ÚNICA", " - RESP"):
        posicao = nome.upper().find(corte)
        if posicao > 0:
            return nome[:posicao].strip()
    return nome


# --- markdown ---------------------------------------------------------------


def render_captacao_markdown(bloco: dict, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    foco = bloco.get("foco") or "casa em foco"
    linhas = [
        f"# Captação de ETF mês a mês - {foco}",
        "",
        f"Execução de {gerado_em:%d/%m/%Y %H:%M}.",
        "",
    ]
    if not bloco.get("available"):
        return "\n".join(linhas + [f"_{bloco.get('reason')}_", ""])

    meses = bloco["meses"]
    ultimo = meses[-1]
    cabecalho_meses = " | ".join(mes_curto(m) for m in meses)
    alinhamento_meses = ":---:|" * len(meses)
    linhas += [
        "Prazos: " + "; ".join(prazos_captacao(bloco)) + ".",
        "",
        f"Legenda do mês: {legenda()}.",
        "",
    ]
    linhas += [f"- {frase}" for frase in bloco["leitura"]] + [""]

    mercado = bloco["mercado"]
    historico = mercado.get("historico") or {}
    if _com_informe(bloco):
        detalhe = mercado["detalhe"]
        sem_informe = _tem_sem_informe(mercado)
        linhas += [
            "## Mercado mês a mês",
            "",
            "Captação e resgates declarados no informe diário do Fundos.NET; o ajuste é o que a "
            "diferença de PL corrigiu nos dias em que o declarado não fechou com a variação de "
            "cotas. Captação líquida = captação - resgates + ajuste"
            + (" + a dos ETFs sem o informe do mês, pelos fechamentos" if sem_informe else "")
            + "; a diferença de PL tem também a valorização.",
            "",
            "| Mês | Captação | Resgates | Ajuste pela diferença de PL | "
            + ("Sem o informe do mês | " if sem_informe else "")
            + "Captação líquida | Diferença de PL | Das quais estreias | Estado | ETFs com informe |",
            "|---|---:|---:|---:|" + ("---:|" if sem_informe else "") + "---:|---:|---:|---|---:|",
        ]

        def colunas(valores: dict, liquida: str) -> str:
            return (
                f"{_bruto(valores['captacao'])} | {_resgate(valores['resgates'])} | "
                f"{_variacao(valores['ajuste'])} | "
                + (f"{_variacao(valores['sem_informe'])} | " if sem_informe else "")
                + f"{liquida} | {_variacao(valores['diferenca_pl'])}"
            )

        for m in meses:
            linhas.append(
                f"| {competencia_br(m)} | "
                + colunas({c: detalhe[c][m] for c in DETALHE}, reais_com_sinal(mercado["fluxo"][m]))
                + f" | {_estreias_texto((historico.get(m) or {}).get('estreias'))} | "
                f"{_simbolo(mercado['estado'][m])} {rotulo(mercado['estado'][m])} | "
                f"{bloco['cobertura']['com_informe'].get(m, 0)} |"
            )
        for titulo, janela, liquida, meses_da_janela in (
            ("12 meses", "doze_meses", mercado["doze_meses"], meses),
            ("No ano", "no_ano", mercado["no_ano"], bloco["meses_ano"]),
        ):
            linhas.append(
                f"| **{titulo}** | " + colunas(detalhe[janela], f"**{reais_com_sinal(liquida)}**")
                + f" | {_estreias_texto(_estreias_em(historico, meses_da_janela))} | | |"
            )
        linhas.append("")
    else:
        linhas += [
            "## Mercado mês a mês",
            "",
            "| Mês | Captação | Das quais estreias | Estado | ETFs com os dois fechamentos |",
            "|---|---:|---:|---|---:|",
        ]
        for m in meses:
            linhas.append(
                f"| {competencia_br(m)} | {reais_com_sinal(mercado['fluxo'][m])} | "
                f"{_estreias_texto((historico.get(m) or {}).get('estreias'))} | "
                f"{_simbolo(mercado['estado'][m])} {rotulo(mercado['estado'][m])} | "
                f"{bloco['cobertura']['com_dado'].get(m, 0)} |"
            )
        linhas += [
            f"| **12 meses** | **{reais_com_sinal(mercado['doze_meses'])}** | "
            f"{_estreias_texto(_estreias_em(historico, meses))} | | |",
            f"| **No ano** | **{reais_com_sinal(mercado['no_ano'])}** | "
            f"{_estreias_texto(_estreias_em(historico, bloco['meses_ano']))} | | |",
            "",
        ]
    linhas += _conferencia_markdown(bloco.get("anbima"))

    informe = _com_informe(bloco)
    extra_cab = " Captação | Resgates | Ajuste pela diferença de PL |" if informe else ""
    extra_alinha = "---:|---:|---:|" if informe else ""

    def extra(item: dict) -> str:
        if not informe:
            return ""
        return (
            f" {_bruto(_doze(item, 'captacao'))} | {_resgate(_doze(item, 'resgates'))} | "
            f"{_variacao(_doze(item, 'ajuste'))} |"
        )

    linhas += [
        "## Competição: captação por gestora",
        "",
        "Da que mais captou em 12 meses para a que menos; cada mês traz o estado dele."
        + (" Captação, resgates e ajuste são os de 12 meses." if informe else ""),
        "",
        f"| # | Gestora | 12 meses | % da captação | % do PL | No ano |{extra_cab} {cabecalho_meses} | Em {competencia_br(ultimo)} |",
        f"|---:|---|---:|---:|---:|---:|{extra_alinha}{alinhamento_meses}---|",
    ]
    for g in _gestoras_da_tabela(bloco):
        nome = f"**{g['gestora']}**" if g["foco"] else g["gestora"]
        linhas.append(
            f"| {g['posicao']} | {nome} | {reais_com_sinal(g['doze_meses'])} | "
            f"{pct(g['share_doze_meses'])} | {pct(g['share_pl'])} | {reais_com_sinal(g['no_ano'])} |"
            + extra(g) + " "
            + " | ".join(_simbolo(g["estado"][m]) for m in meses)
            + f" | {_atual(g, ultimo)} |"
        )
    linhas.append("")

    linhas += [
        "## Segmentos",
        "",
        f"O que cada ETF replica, do segmento que mais captou em 12 meses para o que menos. "
        f"A coluna {foco} é a captação dela no segmento em 12 meses e a parte dela no total.",
        "",
        f"| Segmento | 12 meses | No ano | {cabecalho_meses} | Quem mais captou | {foco} | {foco} em {competencia_br(ultimo)} |",
        f"|---|---:|---:|{alinhamento_meses}---|---:|---|",
    ]
    for s in bloco["segmentos"]:
        lider = (
            f"{curto(s['lider_captacao'])} ({reais_com_sinal(s['lider_captacao_valor'])})"
            if s["lider_captacao"] else "-"
        )
        do_foco = (
            f"{reais_com_sinal(s['foco_doze_meses'])} ({pct(s['foco_share'])})"
            if s["foco_doze_meses"] is not None else "sem ETF"
        )
        linhas.append(
            f"| {s['segmento']} | {reais_com_sinal(s['doze_meses'])} | {reais_com_sinal(s['no_ano'])} | "
            + " | ".join(_simbolo(s["estado"][m]) for m in meses)
            + f" | {lider} | {do_foco} | {rotulo(s['foco_estado_atual']) if s['foco_estado_atual'] else '-'} |"
        )
    linhas.append("")

    limiares = bloco.get("limiares") or {}
    linhas += ["## Oportunidades e defesa", ""]
    if bloco["oportunidades"]:
        linhas += [
            f"**Oportunidades** - segmentos que captaram mais de "
            f"{reais(limiares.get('captacao_oportunidade'))} em 12 meses e em que a {foco} ficou "
            f"de fora ou com menos de {pct(limiares.get('share_pequena'), 0)} da captação:",
            "",
        ]
        for s in bloco["oportunidades"]:
            ultimos = " ".join(_simbolo(s["estado"][m]) for m in meses[-3:])
            rivais = ", ".join(
                f"{curto(r['gestora'])} ({reais_com_sinal(r['doze_meses'])})" for r in s["rivais_captando"]
            ) or "-"
            do_foco = (
                f"{reais_com_sinal(s['foco_doze_meses'])} ({pct(s['foco_share'])} do segmento)"
                if s["foco_tem_etf"] else "sem ETF"
            )
            linhas.append(
                f"- **{s['segmento']}** - {reais_com_sinal(s['doze_meses'])} em 12 meses, "
                f"{reais_com_sinal(s['no_ano'])} no ano; últimos três meses {ultimos} "
                f"({rotulo(s['estado_atual'])} em {competencia_br(ultimo)}); quem captou: {rivais}; "
                f"{foco}: {do_foco}"
            )
        linhas.append("")
    if bloco["defesa"]:
        linhas += [
            f"**Defesa** - segmentos que a {foco} lidera em PL, com a captação dela e de quem "
            "vem atrás:",
            "",
        ]
        for s in bloco["defesa"]:
            rival = s["rivais_captando"][0] if s["rivais_captando"] else None
            texto = (
                f"- **{s['segmento']}** - PL {reais(s['pl'])}; {foco}: "
                f"{reais_com_sinal(s['foco_doze_meses'])} em 12 meses, "
                f"{rotulo(s['foco_estado_atual'])} em {competencia_br(ultimo)}; "
                + (
                    f"quem mais captou além dela: {curto(rival['gestora'])} ({reais_com_sinal(rival['doze_meses'])})"
                    if rival else "nenhuma concorrente captou em 12 meses"
                )
            )
            if s.get("alertas"):
                texto += f". **Alerta:** {'; '.join(s['alertas'])}"
            linhas.append(texto)
        linhas.append("")

    captacoes, saidas, da_casa = _destaques_de_etf(bloco)
    linhas += [
        "## Por ETF",
        "",
        "A lista completa, com todos os meses, está na planilha (aba Captação - por ETF).",
        "",
    ]
    for titulo, lista in (
        ("Maiores captações em 12 meses", captacoes),
        ("Maiores saídas em 12 meses", saidas),
        (f"ETFs da {foco}", da_casa),
    ):
        if not lista:
            continue
        cab_etf = " Captação | Resgates | Diferença de PL |" if informe else ""
        linhas += [
            f"**{titulo}**",
            "",
            f"| Ticker | ETF | Gestora | Segmento | 12 meses | No ano |{cab_etf} {cabecalho_meses} | Em {competencia_br(ultimo)} |",
            f"|---|---|---|---|---:|---:|{extra_alinha}{alinhamento_meses}---|",
        ]
        for e in lista:
            ticker = f"**{e['ticker']}**" if e.get("ticker") else "-"
            do_informe = (
                f" {_bruto(_doze(e, 'captacao'))} | {_resgate(_doze(e, 'resgates'))} | "
                f"{_variacao(_doze(e, 'diferenca_pl'))} |" if informe else ""
            )
            linhas.append(
                f"| {ticker} | {nome_curto(e['nome'])} | {curto(e['gestora'])} | {e['segmento']} | "
                f"{reais_com_sinal(e['doze_meses'])} | {reais_com_sinal(e['no_ano'])} |"
                + do_informe + " "
                + " | ".join(_simbolo(e["estado"][m]) for m in meses)
                + f" | {_atual(e, ultimo)} |"
            )
        linhas.append("")

    linhas += ["## Como ler", ""] + [f"- {nota}" for nota in _notas(bloco)] + [""]
    linhas += ["## Fontes", "", "Onde conferir cada número deste relatório:", ""]
    linhas += [f"- {linha}" for linha in _linhas_de_fontes(bloco)] + [""]
    return "\n".join(linhas)


# --- HTML -------------------------------------------------------------------
#
# O padrao executivo da XP (shared/email_xp.py e shared/graficos_email.py) e a
# secao 5 da linguagem visual: as leituras viram numeros e uma tabela de
# "tema e prazo | numeros"; cada bloco abre por uma figura com titulo que
# afirma, calculado dos dados; a grade de 12 meses e um mapa de calor na
# escala divergente dos tokens de status (verde entrou, vermelho saiu, mais
# cheio quanto maior), com o sinal escrito em cada celula; a metodologia fica
# numa linha cada, no fim. E uma pagina de relatorio - larga (1180 px) e fora
# do orcamento de e-mail -, mas feita das mesmas pecas de e-mail, para ter a
# mesma cara em qualquer cliente. O markdown continua com as frases inteiras.

_ROTULO_DO_MONITOR = "Monitor de ETFs"
_LARGURA = 1180
_MAX_BARRAS = 12
_MAX_PARES = 10


def _marca(gestora: str | None, foco: bool) -> dict:
    """Cor da gestora: a da casa monitorada a que ela pertence (XP, BTG, Itau,
    Nu, Inter), cinza para as demais - nunca um matiz novo."""
    casa = casa_da_gestora(gestora) or ""
    return {"cor": cor_da_casa(casa), "contorno": contorno_da_casa(casa), "destaque": foco}


def _fonte(*fontes) -> str:
    return "Fonte: " + "; ".join(md_para_html(f"[{f.nome}]({f.url})") for f in fontes) + "."


def _rotulo_html(texto: str, detalhe: str = "") -> str:
    """Celula de tema: o tema em negrito e o prazo (ou a unidade) miudo embaixo."""
    return f"<strong>{esc(texto)}</strong>" + (miudo(esc(detalhe)) if detalhe else "")


def _doze_meses(bloco: dict) -> str:
    """"12 meses, de 31/08/2025 a 31/08/2026" - o prazo das colunas de 12 meses."""
    janela = bloco.get("janela_12m") or {}
    return f"12 meses, {periodo_br(janela.get('de'), janela.get('ate'))}"


def _celula_do_mes(fluxo: float | None, estado: str) -> str:
    """O valor compacto do mes, com o estado no `title` (a dica do cursor)."""
    if estado == "fora":
        return ""
    conteudo = valor_compacto(fluxo) if fluxo is not None else SIMBOLOS.get(estado, "?")
    return f'<span title="{esc(rotulo(estado))}">{esc(conteudo)}</span>'


def _grade(
    cabecalho: list[str],
    meses: list[str],
    linhas: list[tuple[list[str], dict, dict, list[str]]],
    finais: list[str] = (),
    *,
    base: list[bool] | None = None,
    pintar_linha: list[bool] | None = None,
    direita=(),
    sinal=(),
) -> str:
    """Tabela com colunas fixas, a grade de meses pintada e colunas finais.

    Cada linha: (celulas antes da grade, fluxo por mes, estado por mes, celulas
    depois). A escala da cor e uma so por tabela - o maior |fluxo| dela - para
    a cor comparar linhas entre si. "Sem movimento" e "sem dado" ficam no cinza
    neutro; antes da estreia, a celula fica em branco. `pintar_linha` diz quais
    linhas levam cor (no mercado, so a da captacao liquida).
    """
    antes = len(cabecalho)
    pintar_linha = pintar_linha or [True] * len(linhas)
    maximo = max(
        (
            abs(fluxo.get(m) or 0) for (_, fluxo, estado, _), pinta in zip(linhas, pintar_linha)
            if pinta for m in meses if estado.get(m, "fora") not in ("fora", "sem_movimento")
        ),
        default=0,
    )

    def pintar(n: int, i: int) -> tuple[str, str] | None:
        if not antes <= i < antes + len(meses) or not pintar_linha[n]:
            return None
        _, fluxo, estado, _ = linhas[n]
        m = meses[i - antes]
        situacao = estado.get(m, "fora")
        if situacao == "fora":
            return None
        if situacao in ("sem_movimento", "sem_dado"):
            return SUPERFICIE_2, TINTA
        return passo_divergente(fluxo.get(m), maximo) or (SUPERFICIE_2, TINTA)

    return tabela(
        [*cabecalho, *(mes_curto(m) for m in meses), *finais],
        [
            [*iniciais, *(_celula_do_mes(fluxo.get(m), estado.get(m, "fora")) for m in meses),
             *depois]
            for iniciais, fluxo, estado, depois in linhas
        ],
        direita=set(direita), sinal=set(sinal),
        centro=range(antes, antes + len(meses)),
        base=base, compacta=True, larga=True, pintar=pintar,
    )


def _legenda_da_grade() -> str:
    """A escala da grade numa linha: amostras da saida forte a entrada forte."""
    amostras = []
    for valor, texto in ((-1.0, "saída forte"), (-0.1, "saída"), (0.0, "sem movimento"),
                         (0.1, "entrada"), (1.0, "entrada forte")):
        fundo, tinta = passo_divergente(valor, 1.0) or (SUPERFICIE_2, TINTA)
        amostras.append(
            f'<span style="background:{fundo};color:{tinta};padding:1px 6px;margin-right:4px;">'
            f"{esc(texto)}</span>"
        )
    return nota(
        "Célula: captação líquida do mês, com o sinal · "
        + "".join(amostras)
        + " · cor na escala da tabela · em branco: antes da estreia · ?: sem dado · o "
        "estado do mês (acelerando, virou saída...) na dica do cursor e na última coluna."
    )


def _leituras(bloco: dict, foco: str) -> str:
    """Os numeros-chave e a tabela "tema e prazo | numeros", no lugar das
    frases de leitura (que seguem no markdown)."""
    mercado = bloco["mercado"]
    ultimo = bloco["meses"][-1]
    base = next((g for g in bloco["gestoras"] if g["foco"]), None)
    numeros = [
        (reais_com_sinal(mercado["doze_meses"]), "captação líquida dos ETFs em 12 meses"),
        (reais_com_sinal(mercado["no_ano"]), f"no ano de {bloco['ano']}"),
    ]
    if base:
        numeros += [
            (reais_com_sinal(base["doze_meses"]), f"da {foco} em 12 meses", True),
            (pct(base["share_doze_meses"]), f"da captação, contra {pct(base['share_pl'])} do PL"),
            (f"{base['posicao']}ª", f"em captação, de {len(bloco['gestoras'])} gestoras"),
        ]
    numeros.append((
        reais_com_sinal(mercado["fluxo"][ultimo]),
        f"em {competencia_br(ultimo)} ({rotulo(mercado['estado'][ultimo])})",
    ))

    linhas = [[
        _rotulo_html("Mercado", _doze_meses(bloco)),
        esc(pontuado(
            reais_com_sinal(mercado["doze_meses"]),
            f"no ano {reais_com_sinal(mercado['no_ano'])}",
            f"{competencia_br(ultimo)} {reais_com_sinal(mercado['fluxo'][ultimo])} "
            f"({rotulo(mercado['estado'][ultimo])})",
        )),
    ]]
    if _com_informe(bloco):
        doze = mercado["detalhe"]["doze_meses"]
        linhas.append([
            _rotulo_html("Informe diário", "12 meses, declarado pelo administrador"),
            esc(pontuado(
                f"captação {_bruto(doze['captacao'])}", f"resgates {_bruto(doze['resgates'])}",
                f"ajuste {_variacao(doze['ajuste'])}",
                f"diferença de PL {_variacao(doze['diferenca_pl'])}",
            )),
        ])
    topo = bloco["gestoras"][:3]
    if topo:
        linhas.append([
            _rotulo_html("Quem mais captou", "12 meses, % da captação"),
            esc(pontuado(*(
                f"{curto(g['gestora'])} {reais_com_sinal(g['doze_meses'])} "
                f"({pct(g['share_doze_meses'])})" for g in topo
            ))),
        ])
    if base:
        linhas.append([
            _rotulo_html(foco, "12 meses"),
            esc(pontuado(
                reais_com_sinal(base["doze_meses"]),
                f"{pct(base['share_doze_meses'])} da captação, {pct(base['share_pl'])} do PL",
                f"{base['posicao']}ª em captação",
                f"{competencia_br(ultimo)} {reais_com_sinal(base['fluxo'].get(ultimo))} "
                f"({rotulo(base['estado_atual'])})",
            )),
        ])
    por_estado: dict[str, list[str]] = {}
    for s in bloco["segmentos"]:
        por_estado.setdefault(s["estado_atual"], []).append(s["segmento"])
    def nomes(lista: list[str]) -> str:
        return ", ".join(lista[:3]) + (f" e mais {len(lista) - 3}" if len(lista) > 3 else "")

    ritmo = [
        f"{rotulo(estado)}: {nomes(por_estado[estado])}"
        for estado in ("acelerando", "virou_saida", "saida_persistente") if por_estado.get(estado)
    ]
    if ritmo:
        linhas.append([_rotulo_html("Segmentos", f"em {competencia_br(ultimo)}"), esc(pontuado(*ritmo))])
    if bloco["oportunidades"]:
        linhas.append([
            _rotulo_html("Oportunidades", f"12 meses, {foco} abaixo de "
                         f"{pct((bloco.get('limiares') or {}).get('share_pequena'), 0)}"),
            esc(pontuado(*(
                f"{s['segmento']} {reais_com_sinal(s['doze_meses'])} ({curto(s['lider_captacao'])})"
                for s in bloco["oportunidades"][:3]
            ))),
        ])
    alertas = [s for s in bloco["defesa"] if s.get("alertas")]
    if alertas:
        linhas.append([
            _rotulo_html("Defesa com alerta", f"segmentos que a {foco} lidera em PL"),
            esc(pontuado(*(s["segmento"] for s in alertas))),
        ])
    return (
        secao("Principais leituras")
        + numeros_em_destaque(numeros)
        + tabela(["Tema e prazo", "Números"], linhas, larguras={0: 26})
    )


def _figura_do_mercado(bloco: dict) -> str:
    """A captacao liquida de todos os ETFs, mes a mes."""
    meses = bloco["meses"]
    mercado = bloco["mercado"]
    valores = tuple(mercado["fluxo"].get(m) for m in meses)
    ultimo = meses[-1]
    corpo = colunas_por_casa(
        [Serie("Todos os ETFs", valores, COMPONENTE_1,
               nota=f"{competencia_br(ultimo)}: {reais_com_sinal(mercado['fluxo'][ultimo])}")],
        # So o mes ("set", "out"): com o ano, 12 rotulos passam da largura do
        # telefone; o ano esta nas datas do subtitulo.
        [competencia_br(m)[:3] for m in meses], altura=96, por_linha=1,
    )
    captando = sum(1 for v in valores if v and v > 0)
    titulo = (
        f"Os ETFs captaram em todos os {len(meses)} meses: "
        if captando == len(meses) else
        f"Os ETFs captaram em {captando} de {len(meses)} meses: "
    ) + f"{reais_com_sinal(mercado['doze_meses'])} no total"
    return figura(
        titulo,
        pontuado("Captação líquida mensal de todos os ETFs, R$", _doze_meses(bloco)),
        corpo,
        _fonte(FUNDOS_NET),
    )


def _figura_das_gestoras(bloco: dict, foco: str) -> str:
    """Captacao em 12 meses por gestora, do zero para cada lado."""
    gestoras = _gestoras_da_tabela(bloco)[:_MAX_BARRAS]
    if not any(g["foco"] for g in gestoras):
        gestoras += [g for g in bloco["gestoras"] if g["foco"]]
    linhas = [
        Linha(curto(g["gestora"]), g["doze_meses"], reais_com_sinal(g["doze_meses"]),
              **_marca(g["gestora"], g["foco"]))
        for g in gestoras if g.get("doze_meses") is not None
    ]
    corpo = barras_divergentes(linhas)
    if not corpo:
        return ""
    lider = bloco["gestoras"][0]
    base = next((g for g in bloco["gestoras"] if g["foco"]), None)
    titulo = (
        f"{curto(lider['gestora'])} lidera a captação em 12 meses "
        f"({reais_com_sinal(lider['doze_meses'])})"
    )
    if base and base is not lider:
        titulo += f"; {foco} é a {base['posicao']}ª, com {reais_com_sinal(base['doze_meses'])}"
    elif base:
        titulo = f"{foco} lidera a captação em 12 meses ({reais_com_sinal(base['doze_meses'])})"
    return figura(
        titulo,
        pontuado("Captação líquida por gestora, R$", _doze_meses(bloco)),
        corpo,
        _fonte(FUNDOS_NET),
    )


def _figura_lider_e_foco(bloco: dict, foco: str) -> str:
    """Nos segmentos que mais captaram, a lider da captacao e a casa em foco."""
    segmentos = [s for s in bloco["segmentos"] if s["lider_captacao"]][:_MAX_PARES]
    if not segmentos:
        return ""
    def da_lider(s: dict) -> Linha:
        # Onde a propria casa em foco lidera, a outra barra e a concorrente
        # que mais captou depois dela.
        if s["lider_captacao"] == foco and s["rivais_captando"]:
            rival = s["rivais_captando"][0]
            return Linha(curto(rival["gestora"]), rival["doze_meses"],
                         reais_com_sinal(rival["doze_meses"]), **_marca(rival["gestora"], False))
        return Linha(curto(s["lider_captacao"]), s["lider_captacao_valor"],
                     reais_com_sinal(s["lider_captacao_valor"]),
                     **_marca(s["lider_captacao"], False))

    pares = [
        Par(
            s["segmento"] + (" (lidera)" if s["lider_captacao"] == foco else ""),
            da_lider(s),
            Linha(curto(foco), s["foco_doze_meses"] or 0,
                  reais_com_sinal(s["foco_doze_meses"]) if s["foco_doze_meses"] is not None
                  else "sem ETF",
                  **_marca(foco, True)),
        )
        for s in segmentos
    ]
    lidera = sum(1 for s in segmentos if s["lider_captacao"] == foco)
    titulo = (
        f"Nos {len(segmentos)} segmentos que mais captaram, a {foco} lidera a captação em "
        f"{lidera}" if lidera else
        f"Nos {len(segmentos)} segmentos que mais captaram, a {foco} não lidera a captação "
        "em nenhum"
    )
    return figura(
        titulo,
        pontuado(f"Captação líquida da líder do segmento e da {foco}, R$", _doze_meses(bloco)),
        barras_pareadas(pares),
        _fonte(FUNDOS_NET),
    )


def render_captacao_html(bloco: dict, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    foco = bloco.get("foco") or "casa em foco"
    titulo = f"Captação de ETF mês a mês - {foco}"
    if not bloco.get("available"):
        corpo = chamada(esc(bloco.get("reason")))
        previa = esc(bloco.get("reason"))
    else:
        corpo = _corpo_html(bloco, foco)
        previa = esc(re.sub(r"\*\*|\[|\]\([^)]*\)", "", bloco["leitura"][0])) if bloco["leitura"] else ""
    corpo = cabecalho(
        "Captação líquida mês a mês, no ano e em 12 meses", titulo,
        f"Execução de {gerado_em:%d/%m/%Y %H:%M}",
    ) + corpo
    return casca(
        esc(titulo), previa, corpo, faixa(_ROTULO_DO_MONITOR, f"{gerado_em:%d/%m/%Y}"),
        largura=_LARGURA,
    )


def _conferencia_html(conferencia: dict | None) -> list[str]:
    if not conferencia:
        return []
    linhas = [
        [esc(competencia_br(l["mes"])), esc(reais_com_sinal(l["monitor"])),
         esc(_estreias_texto(l["estreias"])), esc(reais_com_sinal(l["anbima"])),
         esc(reais_com_sinal(l["diferenca"])), esc(pct(l["cobertura"]))]
        for l in conferencia["linhas"]
    ]
    for rotulo_, soma in (("12 meses", conferencia["doze_meses"]), ("No ano", conferencia.get("no_ano"))):
        if soma:
            linhas.append([
                f"<strong>{rotulo_}</strong>", f"<strong>{esc(reais_com_sinal(soma['monitor']))}</strong>",
                esc(reais_com_sinal(soma["estreias"])),
                f"<strong>{esc(reais_com_sinal(soma['anbima']))}</strong>",
                esc(reais_com_sinal(soma["monitor"] - soma["anbima"])), "",
            ])
    partes = [
        secao("Conferência com a ANBIMA"),
        nota(esc(pontuado(
            f"Boletim de Fundos de Investimento da ANBIMA (anexo {conferencia['arquivo']})",
            "a ANBIMA soma os associados; o monitor, todos os ETFs da CVM, e conta a estreia "
            "como captação",
        )) + "."),
        tabela(
            ["Mês", "Monitor", "Das quais estreias", "ANBIMA", "Diferença", "PL: ANBIMA / monitor"],
            linhas, direita={1, 2, 3, 4, 5}, sinal={4}, compacta=True,
        ),
    ]
    destaques = conferencia.get("destaques") or []
    if destaques:
        partes += [
            titulo_de_tabela("**Onde a diferença passou de R$ 1 bi**"),
            tabela(
                ["Mês", "Diferença", "Estreias", "ETFs em estreia"],
                [
                    [
                        esc(competencia_br(d["mes"])), esc(reais_com_sinal(d["diferenca"])),
                        esc(reais_com_sinal(d["estreias"])),
                        esc(", ".join(
                            f"{e['ticker'] or nome_curto(e['nome'])} ({reais(e['valor'])})"
                            for e in d["etfs_em_estreia"][:3]
                        ) or "-"),
                    ]
                    for d in destaques
                ],
                direita={1, 2}, sinal={1}, compacta=True,
            ),
        ]
    return partes


def _notas_curtas(bloco: dict) -> list[str]:
    """"Como ler" em uma linha cada; as frases inteiras ficam no markdown."""
    limiares = bloco.get("limiares") or {}
    cobertura = bloco.get("cobertura") or {}
    if _com_informe(bloco):
        notas = [
            "Captação: informe diário de cada ETF no Fundos.NET (captação menos resgate), "
            "conferido contra a variação de cotas; se não fecha (folga de 5%), vale a "
            "diferença de PL, e a correção é o ajuste.",
            "Diferença de PL: captação líquida mais valorização, menos rendimento distribuído.",
        ]
        ajustes = cobertura.get("ajuste_por_gestora") or []
        if ajustes:
            notas.append(
                "Maior ajuste em 12 meses: "
                + pontuado(*(f"{curto(a['gestora'])} {reais_com_sinal(a['ajuste'])}" for a in ajustes[:4]))
                + " (gestora que não declara captação e resgate, ou resgate sem mudança de cotas)."
            )
        if cobertura.get("sem_informe_do_mes"):
            notas.append(
                f"{contagem(cobertura['sem_informe_do_mes'], 'mês de ETF', 'meses de ETF')} "
                "sem o informe do mês: vale a variação de cotas vezes a cota média."
            )
    else:
        notas = [
            "Captação: variação da quantidade de cotas entre dois fechamentos, vezes a cota "
            "média do mês; em mês muito volátil o valor é aproximado, o sinal não.",
        ]
    notas += [
        f"Estados: acelerando capta mais que no mês anterior; virou saída é o primeiro mês "
        f"de saída; saída persistente, o segundo ou mais; sem movimento, abaixo de "
        f"{pct(limiares.get('materialidade'))} do PL e de {reais_mil(limiares.get('fluxo_minimo'))}.",
        "Estreia conta como captação; ETF cancelado fica fora.",
        f"% da captação: parte na captação líquida de todos os ETFs em 12 meses. Oportunidade: "
        f"segmento com mais de {reais(limiares.get('captacao_oportunidade'))} em 12 meses e a "
        f"casa em foco abaixo de {pct(limiares.get('share_pequena'), 0)} disso.",
    ]
    eventos = cobertura.get("eventos_na_cota") or []
    if eventos:
        notas.append(
            "Desdobramento ou grupamento de cotas: "
            + pontuado(*(f"{nome_curto(nome)}, {competencia_br(m)}, {o}" for nome, m, o in eventos))
            + "."
        )
    if cobertura.get("sem_dado"):
        notas.append(
            f"{contagem(cobertura['sem_dado'], 'mês de ETF', 'meses de ETF')} sem dado "
            "(fechamento faltando); o total soma os ETFs com os dois fechamentos."
        )
    return notas


def _corpo_html(bloco: dict, foco: str) -> str:
    meses = bloco["meses"]
    ultimo = meses[-1]
    mercado = bloco["mercado"]
    limiares = bloco.get("limiares") or {}
    informe = _com_informe(bloco)
    neutro = {m: "sem_movimento" for m in meses}
    historico = mercado.get("historico") or {}

    linhas_do_mercado = [
        (["<strong>Captação líquida</strong>" if informe else "<strong>Todos os ETFs</strong>",
          esc(reais_com_sinal(mercado["doze_meses"])), esc(reais_com_sinal(mercado["no_ano"]))],
         mercado["fluxo"], mercado["estado"], []),
    ]
    if informe:
        detalhe = mercado["detalhe"]
        for titulo, coluna, sinal in (
            ("Captação declarada", "captacao", 1), ("Resgates declarados", "resgates", -1),
            ("Ajuste pela diferença de PL", "ajuste", 1),
            *([("Sem o informe do mês (fechamentos)", "sem_informe", 1)] if _tem_sem_informe(mercado) else []),
            ("Diferença de PL", "diferenca_pl", 1),
        ):
            def assinado(valor, s=sinal):
                return valor * s if valor is not None else None

            linhas_do_mercado.append((
                [esc(titulo), esc(reais_com_sinal(assinado(detalhe["doze_meses"][coluna]))),
                 esc(reais_com_sinal(assinado(detalhe["no_ano"][coluna])))],
                {m: assinado(detalhe[coluna][m]) for m in meses}, neutro, [],
            ))
    linhas_do_mercado.append((
        ["Das quais estreias", esc(reais_com_sinal(_estreias_em(historico, meses))),
         esc(reais_com_sinal(_estreias_em(historico, bloco["meses_ano"])))],
        {m: (historico.get(m) or {}).get("estreias") for m in meses}, neutro, [],
    ))

    gestoras = _gestoras_da_tabela(bloco)
    partes = [
        # So os prazos; o metodo de cada numero vai no "Como ler".
        nota(esc("Prazos: " + " · ".join(
            prazo for prazo in prazos_captacao(bloco)
            if not prazo.startswith(("captação:", "diferença de PL:"))
        ) + ".")),
        _leituras(bloco, foco),
        secao("Mercado mês a mês"),
        _figura_do_mercado(bloco),
        _legenda_da_grade(),
        *([nota("Captação e resgates declarados no informe diário do Fundos.NET; ajuste: o que a "
                "diferença de PL corrigiu. Captação líquida = captação − resgates + ajuste. Só "
                "essa linha leva cor.")] if informe else []),
        _grade(["", "12 meses", "No ano"], meses, linhas_do_mercado,
               pintar_linha=[True] + [False] * (len(linhas_do_mercado) - 1),
               direita={1, 2}, sinal={1, 2}),
        *_conferencia_html(bloco.get("anbima")),
        secao("Competição: captação por gestora"),
        _figura_das_gestoras(bloco, foco),
        # Captacao, resgates e ajuste de cada gestora ficam no markdown e na
        # planilha: com eles a grade passava da largura da pagina.
        titulo_de_tabela(f"**Todas as gestoras** · da que mais captou para a que menos · "
                         f"{_doze_meses(bloco)}"),
        _grade(
            ["#", "Gestora", "12 meses", "% capt.", "% PL", "No ano"],
            meses,
            [
                (
                    [str(g["posicao"]), esc(g["gestora"]), esc(reais_com_sinal(g["doze_meses"])),
                     esc(pct(g["share_doze_meses"])), esc(pct(g["share_pl"])),
                     esc(reais_com_sinal(g["no_ano"]))],
                    g["fluxo"], g["estado"], [esc(rotulo(g["estado_atual"]))],
                )
                for g in gestoras
            ],
            [f"Em {mes_curto(ultimo)}"],
            base=[g["foco"] for g in gestoras],
            direita={0, 2, 3, 4, 5}, sinal={2, 5},
        ),
        secao("Segmentos"),
        _figura_lider_e_foco(bloco, foco),
        titulo_de_tabela(f"**Todos os segmentos** · do que mais captou para o que menos · {foco}: "
                f"captação e parte no segmento em {_doze_meses(bloco)}"),
        _grade(
            ["Segmento", "12 meses", "No ano"],
            meses,
            [
                (
                    [esc(s["segmento"]), esc(reais_com_sinal(s["doze_meses"])),
                     esc(reais_com_sinal(s["no_ano"]))],
                    s["fluxo"], s["estado"],
                    [
                        esc(curto(s["lider_captacao"]) if s["lider_captacao"] else "-")
                        + (miudo(esc(reais_com_sinal(s["lider_captacao_valor"])))
                           if s["lider_captacao"] else ""),
                        esc(reais_com_sinal(s["foco_doze_meses"]) if s["foco_doze_meses"] is not None
                            else "sem ETF")
                        + (miudo(esc(pct(s["foco_share"]))) if s["foco_doze_meses"] is not None else ""),
                        esc(rotulo(s["foco_estado_atual"]) if s["foco_estado_atual"] else "-"),
                    ],
                )
                for s in bloco["segmentos"]
            ],
            ["Quem mais captou", foco, f"{curto(foco)} em {mes_curto(ultimo)}"],
            base=[s["lider_captacao"] == foco for s in bloco["segmentos"]],
            direita={1, 2, 3 + len(meses) + 1}, sinal={1, 2},
        ),
        secao("Oportunidades e defesa"),
    ]
    if bloco["oportunidades"]:
        partes += [
            titulo_de_tabela(f"**Oportunidades** · segmentos com mais de "
                    f"{reais(limiares.get('captacao_oportunidade'))} em 12 meses e a {foco} "
                    f"fora ou abaixo de {pct(limiares.get('share_pequena'), 0)} da captação"),
            _grade(
                ["Segmento", "12 meses"],
                meses,
                [
                    (
                        [f"<strong>{esc(s['segmento'])}</strong>", esc(reais_com_sinal(s["doze_meses"]))],
                        s["fluxo"], s["estado"],
                        [
                            esc(pontuado(*(
                                f"{curto(r['gestora'])} {reais_com_sinal(r['doze_meses'])}"
                                for r in s["rivais_captando"][:2]
                            )) or "-"),
                            esc(f"{reais_com_sinal(s['foco_doze_meses'])} ({pct(s['foco_share'])})"
                                if s["foco_tem_etf"] else "sem ETF"),
                        ],
                    )
                    for s in bloco["oportunidades"]
                ],
                ["Quem captou", foco],
                direita={1, 2 + len(meses) + 1}, sinal={1},
            ),
        ]
    if bloco["defesa"]:
        alertas = [
            f"<strong>{esc(s['segmento'])}</strong>: {esc('; '.join(s['alertas']))}"
            for s in bloco["defesa"] if s.get("alertas")
        ]
        partes += [
            titulo_de_tabela(f"**Defesa** · segmentos que a {foco} lidera em PL · a grade é a captação "
                    "dela no segmento"),
            *([chamada("<strong>Alerta</strong> · " + " · ".join(alertas))] if alertas else []),
            _grade(
                ["Segmento", "PL", f"{curto(foco)}: 12 meses"],
                meses,
                [
                    (
                        [f"<strong>{esc(s['segmento'])}</strong>", esc(reais(s["pl"])),
                         esc(reais_com_sinal(s["foco_doze_meses"]))],
                        s["foco_fluxo"] or {}, s["foco_estado"] or {},
                        [
                            esc(f"{curto(s['rivais_captando'][0]['gestora'])} "
                                f"{reais_com_sinal(s['rivais_captando'][0]['doze_meses'])}"
                                if s["rivais_captando"] else "-"),
                        ],
                    )
                    for s in bloco["defesa"]
                ],
                ["Quem mais captou além dela"],
                direita={1, 2}, sinal={2},
            ),
        ]

    captacoes, saidas, da_casa = _destaques_de_etf(bloco)
    partes += [secao("Por ETF")]
    for titulo, lista in (
        ("Maiores captações", captacoes),
        ("Maiores saídas", saidas),
        (f"ETFs da {foco}", da_casa),
    ):
        if not lista:
            continue
        partes += [
            titulo_de_tabela(f"**{titulo}** · {_doze_meses(bloco)} · a lista inteira está na "
                    "planilha (aba Captação - por ETF)"),
            _grade(
                ["Ticker", "ETF", "Gestora", "Segmento", "12 meses"],
                meses,
                [
                    (
                        [f"<strong>{esc(e['ticker'])}</strong>" if e.get("ticker") else "-",
                         esc(nome_curto(e["nome"])), esc(curto(e["gestora"])),
                         esc(e["segmento"]), esc(reais_com_sinal(e["doze_meses"]))],
                        e["fluxo"], e["estado"], [],
                    )
                    for e in lista
                ],
                base=[e["foco"] for e in lista],
                direita={4}, sinal={4},
            ),
        ]
    partes += [
        secao("Como ler"),
        *(nota(esc(linha)) for linha in _notas_curtas(bloco)),
        fontes_html((*_FONTES, ANBIMA_BOLETIM) if bloco.get("anbima") else _FONTES),
    ]
    return "\n".join(p for p in partes if p)


def write_etf_captacao(
    config: Config, bloco: dict, gerado_em: datetime | None = None
) -> tuple[Path, Path]:
    """Grava `etf-captacao-<data>.md` e `.html` na pasta de relatorios."""
    gerado_em = gerado_em or datetime.now()
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    base = config.reports_dir / f"etf-captacao-{gerado_em:%Y-%m-%d}"
    md = base.with_suffix(".md")
    html = base.with_suffix(".html")
    md.write_text(render_captacao_markdown(bloco, gerado_em), encoding="utf-8")
    html.write_text(render_captacao_html(bloco, gerado_em), encoding="utf-8")
    return md, html
