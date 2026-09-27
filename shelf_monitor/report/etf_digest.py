"""Relatorio de competicao em ETFs, em markdown e em HTML (CSS inline, para e-mail).

A mesma forma do digest das prateleiras - a leitura primeiro, as tabelas
depois, as fontes com link no fim -, a partir do bloco de
`market.etf_competicao.build_etf_competicao`. Cada numero sai com o prazo dele:
o PL e o do balancete mensal, a comparacao e entre competencias, e cotistas e
fluxo vem da janela de informes do Fundos.NET.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.etf_competicao import (
    curto,
    nome_com_ticker,
    pct,
    pp,
    prazos_etf,
    reais,
    reais_mil,
)
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
from shelf_monitor.report.shared.fontes import CVM_BALANCETE_FIE, CVM_CADASTRO, FUNDOS_NET
from shelf_monitor.texto import competencia_br, contagem, data_br

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


def _tabela_html(cabecalho: list[tuple[str, bool]], linhas: list[list[str]]) -> str:
    """(titulo, alinhar a direita) por coluna; celulas ja escapadas."""
    th = "".join(
        f'<th style="{_TH}{"text-align:right;" if direita else ""}">{_esc(t)}</th>'
        for t, direita in cabecalho
    )
    corpo = "".join(
        "<tr>" + "".join(
            f'<td style="{_TD}{"text-align:right;" if direita else ""}">{celula}</td>'
            for celula, (_, direita) in zip(linha, cabecalho)
        ) + "</tr>"
        for linha in linhas
    )
    return f'<table style="{_TABLE}"><thead><tr>{th}</tr></thead><tbody>{corpo}</tbody></table>'


def _lista_html(itens: list[str]) -> str:
    return (
        '<ul style="margin:0 0 14px;padding-left:18px;">'
        + "".join(f'<li style="{_LI}">{item}</li>' for item in itens)
        + "</ul>"
    )


def render_etf_html(bloco: dict, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    foco = bloco.get("foco") or "casa em foco"
    titulo = f"Competição em ETFs - {foco}"
    if not bloco.get("available"):
        corpo = f'<p style="{_P}">{_esc(bloco.get("reason"))}</p>'
    else:
        corpo = _corpo_html(bloco, foco)
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(titulo)}</title></head>
<body style="{_EMAIL_BODY}">
<div style="{_EMAIL_CARD}">
  <h1 style="font-size:19px;font-weight:600;margin:0 0 4px;">{_esc(titulo)}</h1>
  <p style="font-size:13px;color:#898781;margin:0 0 18px;">
    Gestoras e segmentos de ETF &middot; execução de {gerado_em:%d/%m/%Y %H:%M}</p>
  {corpo}
</div>
</body></html>
"""


def _mensal_html(bloco: dict) -> str:
    mensal = _mensal(bloco)
    if not mensal:
        return ""
    competencias, gestoras = mensal
    tabela = _tabela_html(
        [("Gestora", False), *[(competencia_br(c), True) for c in competencias]],
        [
            [
                f"<strong>{_esc(g['gestora'])}</strong>" if g["foco"] else _esc(g["gestora"]),
                *[_esc(pct(g["share"][c])) for c in competencias],
            ]
            for g in gestoras
        ],
    )
    return f'<p style="{_P}">Participação no PL, competência a competência:</p>{tabela}'


def _corpo_html(bloco: dict, foco: str) -> str:
    def forte(texto: str, sim: bool = True) -> str:
        return f"<strong>{_esc(texto)}</strong>" if sim else _esc(texto)

    partes = [
        f'<p style="{_P}"><strong>Prazos:</strong> {_esc("; ".join(prazos_etf(bloco)))}.</p>',
        _lista_html([_md_para_html(frase) for frase in bloco["leitura"]]),
        f'<h2 style="{_H2}">Gestoras de ETF</h2>',
        _tabela_html(
            [("#", True), ("Gestora", False), ("ETFs", True), ("PL", True),
             ("Participação", True), ("Variação (pp)", True), ("Cotistas", True),
             ("PL por cotista", True), ("Lidera", False)],
            [
                [
                    str(g["posicao"]), forte(g["gestora"], g["foco"]), str(g["etfs"]),
                    _esc(reais(g["pl"])), _esc(pct(g["share"])), _esc(pp(g["delta_share_pp"])),
                    _esc(pct(g["share_cotistas"])), _esc(reais_mil(g["pl_por_cotista"])),
                    _esc(", ".join(g["lidera"]) or "-"),
                ]
                for g in bloco["gestoras"][:_MAX_GESTORAS]
            ],
        ),
        _mensal_html(bloco),
        f'<h2 style="{_H2}">Segmentos</h2>',
        f'<p style="{_P}">O que cada ETF replica, do maior mercado para o menor. A coluna '
        f'{_esc(foco)} é a participação dela no PL do segmento; a variação é da primeira à '
        "última competência.</p>",
        _tabela_html(
            [("Segmento", False), ("PL", True), ("Líder", False), (foco, True),
             ("Posição", True), ("Variação (pp)", True), ("Lançando", False)],
            [
                [
                    _esc(s["segmento"]), _esc(_pl(s["pl"])),
                    _esc(f"{s['lider']} ({pct(s['share_lider'], 0)})" if s["lider"] else "-"),
                    forte(_situacao(s), s["foco_status"] == "lidera"),
                    _esc(s["foco_posicao"] or "-"), _esc(pp(s["foco_delta_pp"])),
                    _md_para_html(_lancando(s, foco)),
                ]
                for s in bloco["segmentos"]
            ],
        ),
    ]
    if bloco["oportunidades"]:
        limiares = bloco.get("limiares") or {}
        partes += [
            f'<h2 style="{_H2}">Oportunidades: onde a {_esc(foco)} é pequena ou não tem ETF</h2>',
            f'<p style="{_P}">Segmentos acima de {_esc(reais(limiares.get("pl_oportunidade")))} '
            f'em que ela tem menos de {_esc(pct(limiares.get("share_pequena"), 0))} do PL, do '
            "maior para o menor:</p>",
            _lista_html([
                f'{forte(s["segmento"])} &mdash; {_esc(reais(s["pl"]))}, líder '
                f'{_esc(s["lider"])} ({_esc(pct(s["share_lider"], 0))}); '
                f"{_esc(_posicao_do_foco(s, foco))}"
                + (
                    f"; concorrentes lançando: {_esc(', '.join(_rivais_lancando(s, foco)))}"
                    if _rivais_lancando(s, foco) else ""
                )
                for s in bloco["oportunidades"]
            ]),
        ]
    if bloco["defesa"]:
        partes += [
            f'<h2 style="{_H2}">Defesa: onde a {_esc(foco)} lidera</h2>',
            _lista_html([
                f'{forte(s["segmento"])} &mdash; {_esc(reais(s["pl"]))}, '
                f'{_esc(pct(s["foco_share"]))} do segmento ({_esc(pp(s["foco_delta_pp"]))} no '
                f'período); atrás: {_esc(s["segundo"] or "-")} ({_esc(pct(s["share_segundo"]))})'
                + (
                    f"; concorrentes lançando: {_esc(', '.join(_rivais_lancando(s, foco)))}"
                    if _rivais_lancando(s, foco) else ""
                )
                for s in bloco["defesa"]
            ]),
        ]
    grupos, estreias, recentes, total_pre = _lancamentos(bloco)
    if grupos or estreias or recentes:
        partes.append(f'<h2 style="{_H2}">Lançamentos</h2>')
        if grupos:
            partes += [
                f'<p style="{_P}"><strong>Em fase pré-operacional: {total_pre} ETFs</strong> '
                "&mdash; registrados na CVM, ainda sem operar. Por segmento:</p>",
                _lista_html([
                    f"{_esc(segmento)}: "
                    + _gestoras_contadas(
                        {k: v for k, v in contagem.items()}, foco,
                        lambda t: f"<strong>{_esc(t)}</strong>",
                    )
                    for segmento, contagem in grupos
                ]),
            ]
        if estreias:
            itens = [
                f'{forte(l["gestora"], l["foco"])} &mdash; {_esc(nome_com_ticker(l["nome"], l.get("ticker")))} '
                f'({_esc(l["segmento"])}): {_esc(reais(l["pl"]))}'
                for l in estreias[:_MAX_ESTREIAS]
            ]
            resto = len(estreias) - _MAX_ESTREIAS
            if resto > 0:
                itens.append(f"... e mais {resto} (planilha, aba Lançamentos)")
            partes += [
                f'<p style="{_P}"><strong>Começaram a operar {_esc(_periodo_das_estreias(bloco))}: '
                f"{contagem(len(estreias), 'ETF', 'ETFs')}</strong>, do maior PL para o menor:</p>",
                _lista_html(itens),
            ]
        if recentes:
            itens = [
                f'{forte(l["gestora"], l["foco"])} &mdash; {_esc(nome_com_ticker(l["nome"], l.get("ticker")))} '
                f'({_esc(l["segmento"])}): {_esc(reais(l["pl"]))} em {data_br(l["data_pl"])}'
                for l in recentes[:_MAX_ESTREIAS]
            ]
            resto = len(recentes) - _MAX_ESTREIAS
            if resto > 0:
                itens.append(f"... e mais {resto} (planilha, aba Lançamentos)")
            partes += [
                f'<p style="{_P}"><strong>Começaram a operar depois do balancete de '
                f"{data_br(bloco.get('data_pl'))}: {contagem(len(recentes), 'ETF', 'ETFs')}"
                "</strong>, com o PL do último informe no Fundos.NET, do maior para o menor:</p>",
                _lista_html(itens),
            ]
    partes += [
        f'<h2 style="{_H2}">Como ler</h2>',
        _lista_html([_esc(nota) for nota in _notas(bloco)]),
        f'<h2 style="{_H2}">Fontes</h2>',
        f'<p style="{_P}">Onde conferir cada número deste relatório:</p>',
        _lista_html([_md_para_html(linha) for linha in _linhas_de_fontes()]),
    ]
    return "\n  ".join(partes)


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
