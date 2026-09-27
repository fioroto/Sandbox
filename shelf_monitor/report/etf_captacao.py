"""Captacao de ETF mes a mes, em markdown e em HTML.

As quatro visoes do bloco de competicao - gestoras, segmentos, oportunidades e
defesa, e cada ETF com o seu segmento - pela captacao de cada mes, com o estado
do mes (acelerando, captando, virou saida, saida persistente). Sai do bloco de
`market.etf_captacao_mensal.build_captacao_mensal`; cada numero traz o prazo
dele, e as fontes fecham o relatorio com link.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.etf_captacao_mensal import (
    DETALHE,
    ORDEM_DA_LEGENDA,
    ROTULOS,
    SIMBOLOS,
    legenda,
    prazos_captacao,
    reais_com_sinal,
    rotulo,
)
from shelf_monitor.market.etf_competicao import curto, pct, reais, reais_mil
from shelf_monitor.report.digest import (
    _EMAIL_BODY,
    _EMAIL_CARD,
    _H2,
    _LI,
    _P,
    _TABLE,
    _TD,
    _TH,
    _esc,
    _md_para_html,
)
from shelf_monitor.report.shared.fontes import (
    ANBIMA_BOLETIM,
    B3_FUNDOS_LISTADOS,
    CVM_BALANCETE_FIE,
    CVM_CADASTRO,
    FUNDOS_NET,
)
from shelf_monitor.texto import competencia_br, contagem

_FONTES = (FUNDOS_NET, CVM_BALANCETE_FIE, CVM_CADASTRO, B3_FUNDOS_LISTADOS)
_MAX_GESTORAS = 12
_MAX_ETFS = 15
_MAX_SAIDAS = 10
# (fundo, texto) de cada estado - o mesmo codigo de cor no HTML e na planilha.
CORES = {
    "acelerando": ("1E7B34", "FFFFFF"),
    "captando": ("C6EFCE", "0B3D17"),
    "sem_movimento": ("EDEDEA", "52514E"),
    "virou_saida": ("F8CBAD", "7A3500"),
    "saida_persistente": ("C00000", "FFFFFF"),
    "sem_dado": ("FFF2CC", "6B5500"),
}
# A grade de 12 meses nao cabe nos 600 px do cartao de e-mail do digest.
_CARTAO_LARGO = _EMAIL_CARD.replace("max-width:600px", "max-width:1180px")


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
            + "; ".join(f"{_nome_curto(nome)}, {competencia_br(m)}, {o}" for nome, m, o in eventos)
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
            f"{e['ticker'] or _nome_curto(e['nome'])} ({reais(e['valor'])})" for e in d["etfs_em_estreia"][:3]
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


def _conferencia_html(conferencia: dict | None) -> list[str]:
    if not conferencia:
        return []
    cabecalho = [("Mês", False), ("Monitor", True), ("Das quais estreias", True), ("ANBIMA", True),
                 ("Diferença", True), ("PL: ANBIMA / monitor", True)]
    corpo = [
        [competencia_br(l["mes"]), reais_com_sinal(l["monitor"]), _estreias_texto(l["estreias"]),
         reais_com_sinal(l["anbima"]), reais_com_sinal(l["diferenca"]), pct(l["cobertura"])]
        for l in conferencia["linhas"]
    ]
    for rotulo_, soma in (("12 meses", conferencia["doze_meses"]), ("No ano", conferencia.get("no_ano"))):
        if soma:
            corpo.append([rotulo_, reais_com_sinal(soma["monitor"]), reais_com_sinal(soma["estreias"]),
                          reais_com_sinal(soma["anbima"]), reais_com_sinal(soma["monitor"] - soma["anbima"]), ""])
    ths = "".join(
        f'<th style="{_TH}{"text-align:right;" if direita else ""}">{_esc(t)}</th>' for t, direita in cabecalho
    )
    trs = "".join(
        "<tr>" + "".join(
            f'<td style="{_TD}{"text-align:right;white-space:nowrap;" if direita else ""}">{_esc(c)}</td>'
            for c, (_, direita) in zip(linha, cabecalho)
        ) + "</tr>"
        for linha in corpo
    )
    partes = [
        f'<h2 style="{_H2}">Conferência com a ANBIMA</h2>',
        f'<p style="{_P}">Boletim de Fundos de Investimento da ANBIMA (anexo '
        f"{_esc(conferencia['arquivo'])}), na janela dele. A ANBIMA soma o que os administradores "
        "associados informam; o monitor, todos os ETFs da CVM, e conta o PL de estreia como "
        "captação.</p>",
        f'<div style="overflow-x:auto;"><table style="{_TABLE}"><thead><tr>{ths}</tr></thead>'
        f"<tbody>{trs}</tbody></table></div>",
    ]
    destaques = _destaques_da_conferencia(conferencia)
    if destaques:
        partes += [f'<p style="{_P}">Onde a diferença passou de R$ 1 bi:</p>', _lista_html([_esc(d) for d in destaques])]
    return partes


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


def _nome_curto(nome: str) -> str:
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
                f"| {ticker} | {_nome_curto(e['nome'])} | {curto(e['gestora'])} | {e['segmento']} | "
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


def _celula_de_mes(fluxo: float | None, estado: str) -> str:
    """Celula colorida pelo estado, com o valor compacto e o estado no title."""
    if estado == "fora":
        return f'<td style="{_TD}text-align:center;color:#c9c8c2;"></td>'
    fundo, texto = CORES.get(estado, ("FFFFFF", "0B0B0B"))
    conteudo = valor_compacto(fluxo) if fluxo is not None else SIMBOLOS.get(estado, "")
    return (
        f'<td title="{_esc(rotulo(estado))}" style="{_TD}text-align:center;white-space:nowrap;'
        f'font-size:12px;background:#{fundo};color:#{texto};">{_esc(conteudo)}</td>'
    )


def _grade_html(cabecalho: list[tuple[str, bool]], meses: list[str], linhas: list[tuple[list[str], dict, dict, list[str]]]) -> str:
    """Tabela com colunas fixas, a grade de meses colorida e colunas finais.

    Cada linha: (celulas antes da grade, fluxo por mes, estado por mes, celulas depois).
    """
    posicao = next(i for i, c in enumerate(cabecalho) if c[0] == "__meses__")
    ths = [
        f'<th style="{_TH}{"text-align:right;" if direita else ""}">{_esc(t)}</th>'
        for t, direita in cabecalho[:posicao]
    ]
    ths += [f'<th style="{_TH}text-align:center;">{_esc(mes_curto(m))}</th>' for m in meses]
    ths += [
        f'<th style="{_TH}{"text-align:right;" if direita else ""}">{_esc(t)}</th>'
        for t, direita in cabecalho[posicao + 1:]
    ]
    # Numero nao quebra linha: "+R$ 15,3 bi" em tres linhas nao se le.
    direita_css = "text-align:right;white-space:nowrap;"
    corpo = []
    for iniciais, fluxo, estado, finais in linhas:
        tds = [
            f'<td style="{_TD}{direita_css if direita else ""}">{celula}</td>'
            for celula, (_, direita) in zip(iniciais, cabecalho[:posicao])
        ]
        tds += [_celula_de_mes(fluxo.get(m), estado.get(m, "fora")) for m in meses]
        tds += [
            f'<td style="{_TD}{direita_css if direita else ""}">{celula}</td>'
            for celula, (_, direita) in zip(finais, cabecalho[posicao + 1:])
        ]
        corpo.append("<tr>" + "".join(tds) + "</tr>")
    return (
        f'<div style="overflow-x:auto;"><table style="{_TABLE}"><thead><tr>{"".join(ths)}</tr>'
        f'</thead><tbody>{"".join(corpo)}</tbody></table></div>'
    )


def _lista_html(itens: list[str]) -> str:
    return (
        '<ul style="margin:0 0 14px;padding-left:18px;">'
        + "".join(f'<li style="{_LI}">{item}</li>' for item in itens)
        + "</ul>"
    )


def _legenda_html() -> str:
    chips = []
    for estado in ORDEM_DA_LEGENDA:
        fundo, texto = CORES[estado]
        chips.append(
            f'<span style="display:inline-block;padding:2px 8px;margin:0 6px 6px 0;border-radius:4px;'
            f'font-size:12px;background:#{fundo};color:#{texto};">{_esc(ROTULOS[estado])}</span>'
        )
    chips.append('<span style="font-size:12px;color:#898781;">em branco: antes da estreia</span>')
    return f'<p style="{_P}">{"".join(chips)}</p>'


def render_captacao_html(bloco: dict, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    foco = bloco.get("foco") or "casa em foco"
    titulo = f"Captação de ETF mês a mês - {foco}"
    corpo = (
        f'<p style="{_P}">{_esc(bloco.get("reason"))}</p>'
        if not bloco.get("available") else _corpo_html(bloco, foco)
    )
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(titulo)}</title></head>
<body style="{_EMAIL_BODY}">
<div style="{_CARTAO_LARGO}">
  <h1 style="font-size:19px;font-weight:600;margin:0 0 4px;">{_esc(titulo)}</h1>
  <p style="font-size:13px;color:#898781;margin:0 0 18px;">
    Captação líquida mês a mês, no ano e em 12 meses &middot; execução de {gerado_em:%d/%m/%Y %H:%M}</p>
  {corpo}
</div>
</body></html>
"""


def _corpo_html(bloco: dict, foco: str) -> str:
    def forte(texto: str, sim: bool = True) -> str:
        return f"<strong>{_esc(texto)}</strong>" if sim else _esc(texto)

    meses = bloco["meses"]
    ultimo = meses[-1]
    mercado = bloco["mercado"]
    limiares = bloco.get("limiares") or {}
    informe = _com_informe(bloco)
    neutro = {m: "sem_movimento" for m in meses}
    linhas_do_mercado = [
        (["<strong>Captação líquida</strong>" if informe else "<strong>Todos os ETFs</strong>",
          _esc(reais_com_sinal(mercado["doze_meses"])), _esc(reais_com_sinal(mercado["no_ano"]))],
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
                [_esc(titulo), _esc(reais_com_sinal(assinado(detalhe["doze_meses"][coluna]))),
                 _esc(reais_com_sinal(assinado(detalhe["no_ano"][coluna])))],
                {m: assinado(detalhe[coluna][m]) for m in meses}, neutro, [],
            ))
    linhas_do_mercado.append(
        (["Das quais estreias", _esc(reais_com_sinal(_estreias_em(mercado.get("historico") or {}, meses))),
          _esc(reais_com_sinal(_estreias_em(mercado.get("historico") or {}, bloco["meses_ano"])))],
         {m: ((mercado.get("historico") or {}).get(m) or {}).get("estreias") for m in meses}, neutro, []),
    )
    cab_gestora = [("Captação", True), ("Resgates", True), ("Ajuste pela diferença de PL", True)] if informe else []

    def colunas_do_informe(item: dict, terceira: str = "ajuste") -> list[str]:
        if not informe:
            return []
        return [
            _esc(_bruto(_doze(item, "captacao"))), _esc(_resgate(_doze(item, "resgates"))),
            _esc(_variacao(_doze(item, terceira))),
        ]

    partes = [
        f'<p style="{_P}"><strong>Prazos:</strong> {_esc("; ".join(prazos_captacao(bloco)))}.</p>',
        _legenda_html(),
        _lista_html([_md_para_html(frase) for frase in bloco["leitura"]]),
        f'<h2 style="{_H2}">Mercado mês a mês</h2>',
        *(
            [f'<p style="{_P}">Captação e resgates declarados no informe diário do Fundos.NET; o '
             "ajuste é o que a diferença de PL corrigiu nos dias em que o declarado não fechou com "
             "a variação de cotas. Captação líquida = captação - resgates + ajuste; a diferença de "
             "PL tem também a valorização. Só a linha da captação líquida leva a cor do estado.</p>"]
            if informe else []
        ),
        _grade_html(
            [("", False), ("12 meses", True), ("No ano", True), ("__meses__", False)],
            meses,
            linhas_do_mercado,
        ),
        *_conferencia_html(bloco.get("anbima")),
        f'<h2 style="{_H2}">Competição: captação por gestora</h2>',
        f'<p style="{_P}">Da que mais captou em 12 meses para a que menos; cada mês traz a '
        "captação dele, na cor do estado."
        + (" Captação, resgates e ajuste são os de 12 meses." if informe else "") + "</p>",
        _grade_html(
            [("#", True), ("Gestora", False), ("12 meses", True), ("% da captação", True),
             ("% do PL", True), ("No ano", True), *cab_gestora, ("__meses__", False),
             (f"Em {competencia_br(ultimo)}", False)],
            meses,
            [
                (
                    [str(g["posicao"]), forte(g["gestora"], g["foco"]),
                     _esc(reais_com_sinal(g["doze_meses"])), _esc(pct(g["share_doze_meses"])),
                     _esc(pct(g["share_pl"])), _esc(reais_com_sinal(g["no_ano"])),
                     *colunas_do_informe(g)],
                    g["fluxo"], g["estado"], [_esc(rotulo(g["estado_atual"]))],
                )
                for g in _gestoras_da_tabela(bloco)
            ],
        ),
        f'<h2 style="{_H2}">Segmentos</h2>',
        f'<p style="{_P}">O que cada ETF replica, do segmento que mais captou em 12 meses para o '
        f"que menos. A coluna {_esc(foco)} é a captação dela no segmento em 12 meses.</p>",
        _grade_html(
            [("Segmento", False), ("12 meses", True), ("No ano", True), ("__meses__", False),
             ("Quem mais captou", False), (foco, True), (f"{foco} em {competencia_br(ultimo)}", False)],
            meses,
            [
                (
                    [_esc(s["segmento"]), _esc(reais_com_sinal(s["doze_meses"])), _esc(reais_com_sinal(s["no_ano"]))],
                    s["fluxo"], s["estado"],
                    [
                        _esc(f"{curto(s['lider_captacao'])} ({reais_com_sinal(s['lider_captacao_valor'])})"
                             if s["lider_captacao"] else "-"),
                        _esc(f"{reais_com_sinal(s['foco_doze_meses'])} ({pct(s['foco_share'])})"
                             if s["foco_doze_meses"] is not None else "sem ETF"),
                        _esc(rotulo(s["foco_estado_atual"]) if s["foco_estado_atual"] else "-"),
                    ],
                )
                for s in bloco["segmentos"]
            ],
        ),
        f'<h2 style="{_H2}">Oportunidades e defesa</h2>',
    ]
    if bloco["oportunidades"]:
        partes.append(
            f'<p style="{_P}"><strong>Oportunidades</strong> &mdash; segmentos que captaram mais de '
            f"{_esc(reais(limiares.get('captacao_oportunidade')))} em 12 meses e em que a "
            f"{_esc(foco)} ficou de fora ou com menos de {_esc(pct(limiares.get('share_pequena'), 0))} "
            "da captação:</p>"
        )
        partes.append(_grade_html(
            [("Segmento", False), ("12 meses", True), ("__meses__", False), ("Quem captou", False), (foco, True)],
            meses,
            [
                (
                    [forte(s["segmento"]), _esc(reais_com_sinal(s["doze_meses"]))], s["fluxo"], s["estado"],
                    [
                        _esc(", ".join(f"{curto(r['gestora'])} ({reais_com_sinal(r['doze_meses'])})"
                                       for r in s["rivais_captando"]) or "-"),
                        _esc(f"{reais_com_sinal(s['foco_doze_meses'])} ({pct(s['foco_share'])})"
                             if s["foco_tem_etf"] else "sem ETF"),
                    ],
                )
                for s in bloco["oportunidades"]
            ],
        ))
    if bloco["defesa"]:
        partes.append(
            f'<p style="{_P}"><strong>Defesa</strong> &mdash; segmentos que a {_esc(foco)} lidera '
            "em PL: a grade é a captação dela no segmento, mês a mês.</p>"
        )
        partes.append(_grade_html(
            [("Segmento", False), ("PL", True), (f"{foco}: 12 meses", True), ("__meses__", False),
             ("Quem mais captou além dela", False), ("Alerta", False)],
            meses,
            [
                (
                    [forte(s["segmento"]), _esc(reais(s["pl"])), _esc(reais_com_sinal(s["foco_doze_meses"]))],
                    s["foco_fluxo"] or {}, s["foco_estado"] or {},
                    [
                        _esc(f"{curto(s['rivais_captando'][0]['gestora'])} "
                             f"({reais_com_sinal(s['rivais_captando'][0]['doze_meses'])})"
                             if s["rivais_captando"] else "-"),
                        _esc("; ".join(s.get("alertas") or []) or "-"),
                    ],
                )
                for s in bloco["defesa"]
            ],
        ))

    captacoes, saidas, da_casa = _destaques_de_etf(bloco)
    partes += [
        f'<h2 style="{_H2}">Por ETF</h2>',
        f'<p style="{_P}">A lista completa, com todos os meses, está na planilha (aba Captação - '
        "por ETF).</p>",
    ]
    for titulo, lista in (
        ("Maiores captações em 12 meses", captacoes),
        ("Maiores saídas em 12 meses", saidas),
        (f"ETFs da {foco}", da_casa),
    ):
        if not lista:
            continue
        partes.append(f'<p style="{_P}"><strong>{_esc(titulo)}</strong></p>')
        partes.append(_grade_html(
            [("Ticker", False), ("ETF", False), ("Gestora", False), ("Segmento", False),
             ("12 meses", True),
             *([("Captação", True), ("Resgates", True), ("Diferença de PL", True)] if informe else []),
             ("__meses__", False)],
            meses,
            [
                (
                    [f"<strong>{_esc(e['ticker'])}</strong>" if e.get("ticker") else "-",
                     forte(_nome_curto(e["nome"]), e["foco"]), _esc(curto(e["gestora"])),
                     _esc(e["segmento"]), _esc(reais_com_sinal(e["doze_meses"])),
                     *colunas_do_informe(e, "diferenca_pl")],
                    e["fluxo"], e["estado"], [],
                )
                for e in lista
            ],
        ))
    partes += [
        f'<h2 style="{_H2}">Como ler</h2>',
        _lista_html([_esc(nota) for nota in _notas(bloco)]),
        f'<h2 style="{_H2}">Fontes</h2>',
        f'<p style="{_P}">Onde conferir cada número deste relatório:</p>',
        _lista_html([_md_para_html(linha) for linha in _linhas_de_fontes(bloco)]),
    ]
    return "\n  ".join(partes)


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
