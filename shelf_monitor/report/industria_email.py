"""E-mails do monitor da industria: o mensal e o flash semanal, em md e html.

O mesmo bloco que desenha o painel e a planilha (`industria/bloco.py`) vira
aqui o e-mail do ciclo mensal, `industria-AAAA-MM-DD.md/.html`, e o bloco do
flash (`industria/flash.py`) vira `industria-flash-AAAA-MM-DD.md/.html`.

HTML de e-mail no padrao executivo (`shared/email_xp.py` e
`shared/graficos_email.py`): 640 px, so tabela e estilo em linha, sem
`<svg>`, `<style>`, `<script>` nem `<img>`. O Gmail corta a mensagem acima de
~102 KB: passado o orcamento, as figuras saem uma a uma - todas desenham uma
tabela que fica no e-mail - e o rodape diz quais.

O markdown traz os mesmos fatos (a leitura, os destaques, as tabelas e os
prazos), em texto: e a parte que sobrevive a qualquer cliente de e-mail.
Neutro como o painel: nenhuma casa em foco, nenhuma marca amarela de casa.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.report.industria_painel import ARQUIVO as ARQUIVO_DO_PAINEL
from shelf_monitor.report.industria_painel import contas, nome_curto, ticket
from shelf_monitor.report.shared.email_xp import (
    UL,
    cabecalho,
    casca,
    chamada,
    esc,
    faixa,
    fontes_html,
    md_para_html,
    miudo,
    nota,
    secao,
    tabela,
)
from shelf_monitor.report.shared.fontes import (
    CVM_CADASTRO_INDUSTRIA,
    CVM_INFORME_DIARIO_INDUSTRIA,
    CVM_PERFIL_MENSAL,
    fontes_da_industria,
)
from shelf_monitor.report.shared.graficos_email import Linha, barras, barras_divergentes, figura
from shelf_monitor.report.shared.numeros import pct, pp, reais, reais_com_sinal
from shelf_monitor.texto import competencia_br, data_br, milhar, periodo_br

_ROTULO_DO_MONITOR = "Monitor da indústria de fundos"
_FONTE_DAS_FIGURAS = (
    "Fonte: CVM (informe diário, perfil mensal, cadastro e carteira); estratégia, papel e "
    "grupo gestor pela classificação do monitor."
)
_TOP_PEER_GROUPS = 6
_TOP_FAMILIAS = 5
_TOP_GRUPOS = 10
_TOP_CASAS = 5

# Orcamento do e-mail (ver `digest._ORCAMENTO_DO_EMAIL`): acima dele as figuras
# saem, da que menos acrescenta a que mais. Todas desenham numeros que uma
# tabela do e-mail ja traz, menos o mes a mes, que tambem esta no markdown.
_ORCAMENTO_DO_EMAIL = 100_000
_FIGURAS_POR_CORTE = (
    ("tipos", "tipo de gestora"),
    ("segmentos", "dono do dinheiro"),
    ("peer_groups", "captação por peer group"),
    ("mensal", "captação mês a mês"),
    ("macroclasses", "captação por macroclasse"),
)


def _sem_marcas(texto: str) -> str:
    """A frase da leitura sem o `**` do markdown - a previa e o texto puro."""
    return re.sub(r"\*\*", "", texto)


def _com_sinal(fracao: float | None) -> str:
    if fracao is None:
        return "-"
    if abs(fracao) < 0.0005:
        return "0,0%"
    return ("+" if fracao > 0 else "") + pct(fracao)


def _nome_n2(itens: list[dict]) -> dict[tuple[str, str], str]:
    """Peer group com o nome repetido em outra macroclasse leva a macroclasse."""
    macros: dict[str, set[str]] = {}
    for item in itens:
        macros.setdefault(item["n2"], set()).add(item["n1"])
    return {
        (i["n1"], i["n2"]): f"{i['n2']} ({i['n1']})" if len(macros[i["n2"]]) > 1 else i["n2"]
        for i in itens
    }


# --- os fatos do mes, uma vez para as duas superficies ------------------------------


def _janela(bloco: dict, chave: str) -> dict:
    return (bloco.get("janelas") or {}).get(chave) or {}


def _prazo(bloco: dict, chave: str) -> str:
    janela = _janela(bloco, chave)
    if not janela.get("de"):
        return ""
    texto = f"{janela['rotulo']}, {periodo_br(janela['de'], janela['ate'])}"
    if janela.get("data_base"):
        texto += f"; base no fechamento de {data_br(janela['data_base'])}"
    return texto


def _destaques(bloco: dict) -> list[tuple[str, str, str]]:
    """(rotulo, valor, prazo) dos cinco destaques - os do painel."""
    camadas = bloco["camadas"]
    universo = bloco["mercado"]["janelas"]
    plataforma = (bloco.get("plataformizacao") or {}).get("total") or {}
    fim = data_br(bloco["informe"]["ate"])
    base_12m = data_br(_janela(bloco, "12m").get("data_base"))
    cotistas = camadas.get("cotistas") or 0
    variacao = plataforma.get("variacao_pp")
    return [
        ("PL disponível para PF e PJ, na camada do investidor", reais(camadas["pl_investidor"]),
         f"fechamento de {fim}; {milhar(camadas['classes'])} classes com informe"),
        ("Captação do investidor em 12 meses", reais_com_sinal(universo["12m"]["fluxo_investidor"]),
         f"{_com_sinal(universo['12m']['sobre_pl'])} do PL de abertura; {_prazo(bloco, '12m')}"),
        (f"Captação do investidor em {bloco['rotulo_competencia']}", reais_com_sinal(universo["1m"]["fluxo_investidor"]),
         f"{_com_sinal(universo['1m']['sobre_pl'])} do PL de abertura; {_prazo(bloco, '1m')}"),
        ("Fatia do PL via conta e ordem", pct(plataforma.get("conta_e_ordem")),
         (f"{pp(variacao)} em 12 meses; " if variacao is not None else "") + f"{fim} contra {base_12m}"),
        ("Contas (posições de cotistas, não CPFs)", contas(cotistas),
         f"{ticket(camadas['pl_investidor'] / cotistas if cotistas else None)} por conta; fechamento de {fim}"),
    ]


def _linhas_n1(bloco: dict) -> list[list[str]]:
    """Macroclasse | PL | Fatia | Captacao 12m | % do PL | No mes, com o total."""
    linhas = []
    for item in bloco["estrategias"]["n1"]:
        doze = (item.get("janelas") or {}).get("12m") or {}
        um = (item.get("janelas") or {}).get("1m") or {}
        linhas.append([
            item["n1"], reais(item["pl_investidor"]), pct(item.get("fatia")),
            reais_com_sinal(doze.get("fluxo_investidor")), _com_sinal(doze.get("sobre_pl")),
            reais_com_sinal(um.get("fluxo_investidor")),
        ])
    universo = bloco["mercado"]["janelas"]
    linhas.append([
        "Total", reais(bloco["camadas"]["pl_investidor"]), "100%",
        reais_com_sinal(universo["12m"]["fluxo_investidor"]), _com_sinal(universo["12m"]["sobre_pl"]),
        reais_com_sinal(universo["1m"]["fluxo_investidor"]),
    ])
    return linhas


def _peer_groups(bloco: dict) -> list[dict]:
    """Os que mais captaram e os que mais perderam em 12 meses, sem repetir."""
    itens = [i for i in bloco["estrategias"]["n2"] if ((i.get("janelas") or {}).get("12m") or {})]
    ordenados = sorted(itens, key=lambda i: -i["janelas"]["12m"]["fluxo_investidor"])
    topo = [i for i in ordenados[:_TOP_PEER_GROUPS] if i["janelas"]["12m"]["fluxo_investidor"] > 0]
    fundo = [i for i in ordenados[-_TOP_PEER_GROUPS:] if i["janelas"]["12m"]["fluxo_investidor"] < 0 and i not in topo]
    return topo + fundo


def _meses(bloco: dict) -> list[dict]:
    meses = set(_janela(bloco, "12m").get("competencias") or [])
    return [h for h in bloco["mercado"]["historico"] if h["competencia"] in meses]


def _familias(bloco: dict) -> tuple[list[dict], list[dict]]:
    familias = bloco.get("familias") or {}
    return (familias.get("que_mais_captam") or [])[:_TOP_FAMILIAS], (familias.get("que_mais_perdem") or [])[:_TOP_FAMILIAS]


def _tipos(bloco: dict) -> list[dict]:
    return [t for t in (bloco.get("gestoras") or {}).get("tipos", []) if t.get("share")]


def _segmentos(bloco: dict) -> list[dict]:
    return [s for s in bloco.get("segmentos") or [] if s.get("fatia") is not None]


def _casas(bloco: dict) -> list[dict]:
    return [c for c in (bloco.get("estrutura") or {}).get("casas_dedicadas", []) if c.get("casa")][:_TOP_CASAS]


def _como_ler() -> list[str]:
    return [
        "Universo disponível: fundos líquidos que um cliente PF ou PJ pode comprar; saem previdência, "
        "exclusivos, masters, institucionais, não residentes e restritos.",
        "Camada do investidor: PL e captação sem o dinheiro que um fundo aplica em outro (perfil mensal "
        "da CVM) - o FoF e o espelho não contam duas vezes.",
        "Captação é por fundo, nunca por distribuidor: a CVM não publica quem vendeu.",
        "Estratégia, gestora e retorno vêm da raiz da cadeia master/feeder/espelho.",
    ]


# --- markdown do mes ---------------------------------------------------------------


def _md_tabela(cabecalho_: list[str], linhas: list[list[str]], direita: set[int] = frozenset()) -> list[str]:
    alinhamento = "|".join("---:" if i in direita else "---" for i in range(len(cabecalho_)))
    saida = ["| " + " | ".join(cabecalho_) + " |", f"|{alinhamento}|"]
    saida += ["| " + " | ".join(str(c).replace("|", "/") for c in linha) + " |" for linha in linhas]
    return saida + [""]


def render_email_markdown(bloco: dict, config: Config | None = None, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    if not bloco.get("available"):
        return "\n".join([
            "# Monitor da indústria de fundos", "",
            f"Execução de {gerado_em:%d/%m/%Y %H:%M}.", "", f"_{bloco.get('reason')}_", "",
        ])
    rotulo = bloco["rotulo_competencia"]
    linhas = [
        f"# A indústria de fundos em {rotulo}",
        "",
        f"{_ROTULO_DO_MONITOR} · visão de mercado para a distribuição, sem casa em foco. "
        f"Execução de {gerado_em:%d/%m/%Y %H:%M}.",
        "",
        "Prazos: " + "; ".join(bloco["prazos"]) + ".",
        "",
        "## Leitura do mês",
        "",
        *[f"- {frase}" for frase in bloco["leitura"]],
        "",
        "## Destaques",
        "",
        *_md_tabela(["Destaque", "Valor", "Prazo e base"], [list(d) for d in _destaques(bloco)], {1}),
        "## Para onde vai o dinheiro",
        "",
        f"### Captação por macroclasse ({_prazo(bloco, '12m')}; no mês: {_prazo(bloco, '1m')})",
        "",
        *_md_tabela(["Macroclasse", "PL", "Fatia", "Captação 12m", "% do PL", "No mês"],
                    _linhas_n1(bloco), {1, 2, 3, 4, 5}),
        f"### Peer groups que mais captaram e mais perderam ({_prazo(bloco, '12m')})",
        "",
    ]
    nomes = _nome_n2(bloco["estrategias"]["n2"])
    linhas += _md_tabela(
        ["Peer group", "PL", "Captação 12m", "% do PL"],
        [[nomes[(i["n1"], i["n2"])], reais(i["pl_investidor"]),
          reais_com_sinal(i["janelas"]["12m"]["fluxo_investidor"]), _com_sinal(i["janelas"]["12m"]["sobre_pl"])]
         for i in _peer_groups(bloco)],
        {1, 2, 3},
    )
    linhas += [f"### Mês a mês ({periodo_br(_janela(bloco, '12m').get('de'), _janela(bloco, '12m').get('ate'))})", ""]
    linhas += _md_tabela(
        ["Competência", "Captação do investidor", "Rendimento", "Resíduo", "PL do investidor"],
        [[competencia_br(h["competencia"]), reais_com_sinal(h["fluxo_investidor"]),
          reais_com_sinal(h["rendimento_investidor"]), reais_com_sinal(h["residuo"]), reais(h["pl_investidor"])]
         for h in _meses(bloco)],
        {1, 2, 3, 4},
    )
    captam, perdem = _familias(bloco)
    linhas += [f"### Estratégias que mais captaram e mais perderam ({_prazo(bloco, '12m')})", ""]
    linhas += _md_tabela(
        ["", "Estratégia", "Gestora", "Peer group", "Captação 12m", "% do PL"],
        [["mais captou", nome_curto(f["nome"]), f["gestora"], f["n2"], reais_com_sinal(f["fluxo_investidor"]),
          _com_sinal(f.get("sobre_pl"))] for f in captam]
        + [["mais perdeu", nome_curto(f["nome"]), f["gestora"], f["n2"], reais_com_sinal(f["fluxo_investidor"]),
            _com_sinal(f.get("sobre_pl"))] for f in perdem],
        {4, 5},
    )
    fim = data_br(bloco["informe"]["ate"])
    base_12m = data_br(_janela(bloco, "12m").get("data_base"))
    linhas += ["## Quem ganha e por onde chega", "", f"### Tipo de gestora (fatia do PL em {fim} e variação contra {base_12m})", ""]
    linhas += _md_tabela(["Tipo de gestora", "Fatia do PL", "Em 12 meses", "Captação 12m"],
                         [[t.get("rotulo") or t["tipo"], pct(t["share"]), pp(t.get("variacao_pp")),
                           reais_com_sinal(t.get("fluxo_investidor_12m"))] for t in _tipos(bloco)], {1, 2, 3})
    linhas += [f"### Grupos gestores ({_TOP_GRUPOS} maiores por PL do investidor)", ""]
    linhas += _md_tabela(
        ["Grupo gestor", "Tipo", "Fatia do PL", "Em 12 meses", "Captação 12m"],
        [[g["grupo"], _rotulo_do_tipo(bloco, g["tipo"]), pct(g.get("share")), pp(g.get("variacao_pp")),
          reais_com_sinal(g.get("fluxo_investidor_12m"))] for g in (bloco["gestoras"].get("grupos") or [])[:_TOP_GRUPOS]],
        {2, 3, 4},
    )
    linhas += [f"### Dono do dinheiro (perfil mensal de {bloco['rotulo_competencia']}, variação contra {base_12m})", ""]
    linhas += _md_tabela(["Cotista", "Fatia do PL", "Em 12 meses"],
                         [[s["rotulo"], pct(s["fatia"]), pp(s.get("variacao_pp"))] for s in _segmentos(bloco)], {1, 2})
    linhas += [f"### Casas dedicadas (veículos feitos para um distribuidor, {fim})", ""]
    linhas += _md_tabela(["Casa", "PL do investidor", "Captação 12m"],
                         [[c["casa"], reais(c["pl_investidor"]), reais_com_sinal(c.get("fluxo_investidor_12m"))]
                          for c in _casas(bloco)], {1, 2})
    linhas += ["## O que mudou", ""]
    linhas += _md_tabela(["Janela", "Entraram", "PL ao entrar", "Saíram", "PL ao sair"],
                         _linhas_de_lancamentos(bloco), {1, 2, 3, 4})
    linhas += ["## Camadas ao lado (fora do total do universo)", ""]
    linhas += _md_tabela(["Camada", "PL", "Captação 12m", "Fonte"], _linhas_adjacentes(bloco), {1, 2})
    linhas += ["## Qualidade e como ler", ""]
    linhas += [f"- {item}" for item in _qualidade(bloco) + _como_ler()] + [""]
    linhas += ["## Onde ver mais", "", _onde_ver_mais(bloco), ""]
    linhas += ["## Fontes", ""]
    linhas += [f"- [{f.nome}]({f.url}): {f.uso}" for f in _fontes(bloco)] + [""]
    return "\n".join(linhas)


def _rotulo_do_tipo(bloco: dict, tipo: str) -> str:
    for item in (bloco.get("gestoras") or {}).get("tipos", []):
        if item["tipo"] == tipo:
            return item.get("rotulo") or tipo
    return tipo


def _linhas_de_lancamentos(bloco: dict) -> list[list[str]]:
    linhas = []
    for chave in ("1m", "12m"):
        item = (bloco.get("lancamentos") or {}).get(chave)
        if not item:
            continue
        janela = _janela(bloco, chave)
        linhas.append([
            f"{janela.get('rotulo', chave)} ({periodo_br(janela.get('de'), janela.get('ate'))})",
            milhar(item["entrou"]["produtos"]), reais(item["entrou"]["pl_investidor"]),
            milhar(item["saiu"]["produtos"]), reais(item["saiu"]["pl_investidor"]),
        ])
    return linhas


def _linhas_adjacentes(bloco: dict) -> list[list[str]]:
    linhas = []
    for chave, rotulo in (("previdencia", "Previdência"), ("etf", "ETF")):
        camada = (bloco.get("adjacentes") or {}).get(chave) or {}
        if camada.get("available") is False:
            linhas.append([rotulo, reais(camada.get("pl")), "-", camada.get("reason") or "indisponível"])
            continue
        doze = ((camada.get("janelas") or {}).get("12m") or {}).get("fluxo_investidor")
        linhas.append([rotulo, reais(camada.get("pl_investidor", camada.get("pl"))), reais_com_sinal(doze),
                       camada.get("fonte") or ""])
    return linhas


def _qualidade(bloco: dict) -> list[str]:
    q = bloco.get("qualidade") or {}
    itens = []
    if q.get("cobertura_perfil") is not None:
        itens.append(f"O perfil mensal do próprio mês cobre {pct(q['cobertura_perfil'])} do PL do universo.")
    if q.get("residuo_sobre_variacao") is not None:
        itens.append(
            f"O resíduo da decomposição (variação do PL menos captação e rendimento) é "
            f"{pct(q['residuo_sobre_variacao'])} da variação do PL em 12 meses."
        )
    sem = q.get("sem_cadastro") or {}
    if sem.get("produtos"):
        itens.append(f"{milhar(sem['produtos'])} produtos do informe sem cadastro na CVM ({reais(sem.get('pl'))}) ficam fora.")
    return itens


def _onde_ver_mais(bloco: dict) -> str:
    return (
        f"Painel interativo, com filtros por público, macroclasse, papel e tipo de gestora e o explorador "
        f"de produtos: `data/reports/{ARQUIVO_DO_PAINEL}`. Planilha com todas as abas: "
        f"`data/reports/industria-{bloco['competencia'][:4]}-{bloco['competencia'][4:]}.xlsx`."
    )


def _fontes(bloco: dict):
    return fontes_da_industria(com_carteira=bool((bloco.get("estrutura") or {}).get("com_carteira", True)))


# --- html do mes ---------------------------------------------------------------------


def _lista(frases: list[str]) -> str:
    return f'<ul style="{UL}">' + "".join(f"<li>{md_para_html(f)}</li>" for f in frases) + "</ul>"


def _figura_macroclasses(bloco: dict) -> str:
    itens = [i for i in bloco["estrategias"]["n1"] if ((i.get("janelas") or {}).get("12m") or {})]
    if not itens:
        return ""
    fluxo = {i["n1"]: i["janelas"]["12m"]["fluxo_investidor"] for i in itens}
    maior = max(fluxo, key=fluxo.get)
    menor = min(fluxo, key=fluxo.get)
    titulo = f"{maior} captou mais em 12 meses" + (f"; {menor} perdeu mais" if fluxo[menor] < 0 else "")
    corpo = barras_divergentes([Linha(n1, v, reais_com_sinal(v)) for n1, v in fluxo.items()])
    return figura(titulo, f"Captação líquida do investidor por macroclasse, {_prazo(bloco, '12m')}", corpo,
                  esc(_FONTE_DAS_FIGURAS))


def _figura_peer_groups(bloco: dict) -> str:
    itens = _peer_groups(bloco)
    if not itens:
        return ""
    nomes = _nome_n2(bloco["estrategias"]["n2"])
    corpo = barras_divergentes([
        Linha(nomes[(i["n1"], i["n2"])], i["janelas"]["12m"]["fluxo_investidor"],
              reais_com_sinal(i["janelas"]["12m"]["fluxo_investidor"]))
        for i in itens
    ])
    return figura(f"{nomes[(itens[0]['n1'], itens[0]['n2'])]} foi o peer group que mais captou",
                  f"Os que mais captaram e os que mais perderam, captação líquida do investidor, {_prazo(bloco, '12m')}",
                  corpo, esc(_FONTE_DAS_FIGURAS))


def _figura_mensal(bloco: dict) -> str:
    meses = _meses(bloco)
    if not meses:
        return ""
    positivos = sum(1 for h in meses if h["fluxo_investidor"] > 0)
    corpo = barras_divergentes([
        Linha(competencia_br(h["competencia"]), h["fluxo_investidor"], reais_com_sinal(h["fluxo_investidor"]))
        for h in meses
    ])
    janela = _janela(bloco, "12m")
    return figura(f"A captação do investidor foi positiva em {positivos} de {len(meses)} meses",
                  f"Captação líquida do investidor por competência, {periodo_br(janela.get('de'), janela.get('ate'))}",
                  corpo, esc(_FONTE_DAS_FIGURAS))


def _figura_tipos(bloco: dict) -> str:
    tipos = _tipos(bloco)
    if not tipos:
        return ""
    base_12m = data_br(_janela(bloco, "12m").get("data_base"))
    corpo = barras([
        Linha(t.get("rotulo") or t["tipo"], t["share"], f"{pct(t['share'])} ({pp(t.get('variacao_pp'))})")
        for t in tipos
    ])
    return figura(f"Gestoras do tipo {(tipos[0].get('rotulo') or tipos[0]['tipo']).lower()} têm {pct(tipos[0]['share'])} do PL",
                  f"Fatia do PL do investidor por tipo de gestora da estratégia em {data_br(bloco['informe']['ate'])}, "
                  f"e a variação em pontos percentuais contra {base_12m}", corpo, esc(_FONTE_DAS_FIGURAS))


def _figura_segmentos(bloco: dict) -> str:
    segmentos = _segmentos(bloco)
    if not segmentos:
        return ""
    pf_pj = sum(s["fatia"] for s in segmentos if s.get("grupo") == "pf_pj")
    corpo = barras([Linha(s["rotulo"], s["fatia"], f"{pct(s['fatia'])} ({pp(s.get('variacao_pp'))})") for s in segmentos])
    base_12m = data_br(_janela(bloco, "12m").get("data_base"))
    return figura(f"PF e PJ têm {pct(pf_pj)} do PL disponível",
                  f"Fatia do PL do universo por tipo de cotista, perfil mensal de {bloco['rotulo_competencia']}, "
                  f"e a variação em pontos percentuais contra {base_12m}", corpo,
                  esc("Fonte: CVM, perfil mensal (composição do PL por tipo de cotista); PL do informe diário."))


def _montador_do_html(bloco: dict, config: Config | None, gerado_em: datetime):
    rotulo = bloco["rotulo_competencia"]
    fim = data_br(bloco["informe"]["ate"])
    base_12m = data_br(_janela(bloco, "12m").get("data_base"))
    figuras = {
        "macroclasses": _figura_macroclasses(bloco),
        "peer_groups": _figura_peer_groups(bloco),
        "mensal": _figura_mensal(bloco),
        "tipos": _figura_tipos(bloco),
        "segmentos": _figura_segmentos(bloco),
    }
    destaques = tabela(["Destaque", "Valor"], [
        [f"{esc(rotulo_)}{miudo(esc(prazo))}", f"<strong>{esc(valor)}</strong>"]
        for rotulo_, valor, prazo in _destaques(bloco)
    ], direita={1})
    tabela_n1 = tabela(["Macroclasse", "PL", "Fatia", "Captação 12m", "% do PL", "No mês"],
                       [[esc(c) for c in linha] for linha in _linhas_n1(bloco)],
                       direita={1, 2, 3, 4, 5}, sinal={3, 4, 5}, total=True)
    captam, perdem = _familias(bloco)

    def linha_de_familia(f: dict) -> list[str]:
        return [f"{esc(nome_curto(f['nome']))}{miudo(esc(f['gestora']))}", esc(f["n2"]),
                esc(reais_com_sinal(f["fluxo_investidor"])), esc(_com_sinal(f.get("sobre_pl")))]

    familias = tabela(["Estratégia e gestora", "Peer group", "Captação 12m", "% do PL"],
                      (["Mais captaram"] + [linha_de_familia(f) for f in captam] if captam else [])
                      + (["Mais perderam"] + [linha_de_familia(f) for f in perdem] if perdem else []),
                      direita={2, 3}, sinal={2, 3})
    grupos = tabela(["Grupo gestor", "Tipo", "Fatia", "Em 12 meses", "Captação 12m"], [
        [esc(g["grupo"]), esc(_rotulo_do_tipo(bloco, g["tipo"])), esc(pct(g.get("share"))),
         esc(pp(g.get("variacao_pp"))), esc(reais_com_sinal(g.get("fluxo_investidor_12m")))]
        for g in (bloco["gestoras"].get("grupos") or [])[:_TOP_GRUPOS]
    ], direita={2, 3, 4}, sinal={3, 4})
    casas = tabela(["Casa dedicada", "PL do investidor", "Captação 12m"], [
        [esc(c["casa"]), esc(reais(c["pl_investidor"])), esc(reais_com_sinal(c.get("fluxo_investidor_12m")))]
        for c in _casas(bloco)
    ], direita={1, 2}, sinal={2})
    lancamentos = tabela(["Janela", "Entraram", "PL ao entrar", "Saíram", "PL ao sair"],
                         [[esc(c) for c in linha] for linha in _linhas_de_lancamentos(bloco)], direita={1, 2, 3, 4})
    adjacentes = tabela(["Camada", "PL", "Captação 12m", "Fonte"],
                        [[esc(c) for c in linha] for linha in _linhas_adjacentes(bloco)], direita={1, 2}, sinal={2})
    previa = esc(_sem_marcas(bloco["leitura"][0])) if bloco.get("leitura") else ""
    titulo = f"A indústria de fundos em {rotulo}"

    def montar(omitidas: tuple[str, ...]) -> str:
        fig = {chave: ("" if chave in omitidas else html) for chave, html in figuras.items()}
        cortadas = [nome for chave, nome in _FIGURAS_POR_CORTE if chave in omitidas and figuras[chave]]
        aviso = ""
        if cortadas:
            aviso = nota(esc(
                f"{len(cortadas)} gráfico{'s ficaram' if len(cortadas) > 1 else ' ficou'} de fora pelo limite de "
                f"tamanho do Gmail ({', '.join(cortadas)}); as tabelas e o markdown trazem os mesmos números."
            ))
        corpo = (
            cabecalho("Visão de mercado para a distribuição · sem casa em foco", titulo,
                      f"Execução de {gerado_em:%d/%m/%Y %H:%M} · competência {rotulo}")
            + nota(esc("Prazos: " + "; ".join(bloco["prazos"]) + "."))
            + secao("Leitura do mês") + _lista(bloco["leitura"])
            + secao("Destaques") + destaques
            + secao("Para onde vai o dinheiro")
            + fig["macroclasses"] + tabela_n1
            + fig["peer_groups"] + fig["mensal"]
            + nota(esc(f"Estratégias que mais captaram e mais perderam, {_prazo(bloco, '12m')}.")) + familias
            + secao("Quem ganha e por onde chega")
            + fig["tipos"]
            + nota(esc(f"Grupos gestores: os {_TOP_GRUPOS} maiores por PL do investidor em {fim}; fatia e "
                       f"variação contra {base_12m}; captação {_prazo(bloco, '12m')}.")) + grupos
            + fig["segmentos"]
            + nota(esc(f"Casas dedicadas: veículos feitos para um distribuidor, PL em {fim}.")) + casas
            + secao("O que mudou") + lancamentos
            + secao("Camadas ao lado") + nota(esc("Previdência e ETF nunca entram no total do universo.")) + adjacentes
            + secao("Qualidade e como ler") + _lista(_qualidade(bloco) + _como_ler())
            + chamada(md_para_html(_onde_ver_mais(bloco)).replace("`", ""))
            + aviso
            + fontes_html(_fontes(bloco))
        )
        return casca(esc(titulo), previa, corpo, faixa(_ROTULO_DO_MONITOR, f"{gerado_em:%d/%m/%Y}"))

    return montar


def render_email_html(bloco: dict, config: Config | None = None, gerado_em: datetime | None = None) -> str:
    """O e-mail do mes em HTML, dentro do orcamento de tamanho do Gmail."""
    gerado_em = gerado_em or datetime.now()
    if not bloco.get("available"):
        titulo = "Monitor da indústria de fundos"
        corpo = cabecalho("Visão de mercado para a distribuição", titulo,
                          f"Execução de {gerado_em:%d/%m/%Y %H:%M}") + chamada(esc(bloco.get("reason")))
        return casca(esc(titulo), esc(bloco.get("reason")), corpo, faixa(_ROTULO_DO_MONITOR, f"{gerado_em:%d/%m/%Y}"))
    montar = _montador_do_html(bloco, config, gerado_em)
    html = montar(())
    cortes = [chave for chave, _ in _FIGURAS_POR_CORTE]
    for n in range(1, len(cortes) + 1):
        if len(html.encode("utf-8")) <= _ORCAMENTO_DO_EMAIL:
            break
        html = montar(tuple(cortes[:n]))
    return html


def gravar_email(config: Config, bloco: dict, gerado_em: datetime | None = None) -> Path:
    """Grava `industria-AAAA-MM-DD.md` e `.html`; devolve o caminho do html."""
    gerado_em = gerado_em or datetime.now()
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    base = config.reports_dir / f"industria-{gerado_em:%Y-%m-%d}"
    base.with_suffix(".md").write_text(render_email_markdown(bloco, config, gerado_em), encoding="utf-8")
    html = base.with_suffix(".html")
    html.write_text(render_email_html(bloco, config, gerado_em), encoding="utf-8")
    return html


# --- o flash semanal -------------------------------------------------------------------

_FONTES_DO_FLASH = (CVM_INFORME_DIARIO_INDUSTRIA, CVM_PERFIL_MENSAL, CVM_CADASTRO_INDUSTRIA)


def _fino(valor: float | None) -> str:
    """"+R$ 2,1 mi" abaixo de R$ 10 mi: a semana de uma macroclasse pequena nao
    vira "+R$ 0 mi"; acima, o formato de sempre."""
    if valor is None:
        return "-"
    if abs(valor) >= 1e7:
        return reais_com_sinal(valor)
    if abs(valor) < 5e4:
        return "R$ 0,0 mi"
    return ("-" if valor < 0 else "+") + f"R$ {abs(valor) / 1e6:.1f} mi".replace(".", ",")


def _linhas_do_flash(flash: dict) -> list[list[str]]:
    linhas = [
        [i["n1"], _fino(i["semana"]), _fino(i["por_dia"]), _fino(i["por_dia_12_semanas"]),
         _fino(i["diferenca_por_dia"])]
        for i in flash["por_n1"]
    ]
    total = flash["total"]
    linhas.append(["Total", _fino(total["semana"]), _fino(total["por_dia"]),
                   _fino(total["por_dia_12_semanas"]), _fino(total["diferenca_por_dia"])])
    return linhas


def _maiores_do_flash(flash: dict) -> list[tuple[str, dict]]:
    return ([("entrada", p) for p in flash.get("maiores_entradas") or []]
            + [("saída", p) for p in flash.get("maiores_saidas") or []])


def _notas_do_flash(flash: dict) -> list[str]:
    itens = [
        "Captação líquida declarada no informe diário, tirada a fatia do PL de cada produto que é de outros "
        "fundos no perfil mensal do ciclo - uma estimativa da camada do investidor.",
        "Por dia útil: a semana com feriado não parece fraca, e a comparação é com a média diária das 12 "
        "semanas anteriores.",
    ]
    if flash.get("dias_incompletos"):
        fora = ", ".join(f"{data_br(d['dia'])} ({pct(d['cobertura'], 0)} dos produtos)" for d in flash["dias_incompletos"])
        itens.append(f"Fora do flash, porque ainda estão chegando: {fora}.")
    cobertura = flash.get("cobertura") or {}
    if cobertura:
        itens.append(
            f"{milhar(cobertura['produtos_com_informe_na_semana'])} de {milhar(cobertura['produtos_no_retrato'])} "
            "produtos do universo entregaram o informe na semana."
        )
    return itens


def render_flash_markdown(flash: dict, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    if not flash.get("available"):
        return "\n".join(["# Flash semanal da indústria de fundos", "",
                          f"Execução de {gerado_em:%d/%m/%Y %H:%M}.", "", f"_{flash.get('reason')}_", ""])
    semana = flash["semana"]
    linhas = [
        f"# Flash semanal da indústria de fundos: semana {periodo_br(semana['de'], semana['ate'])}",
        "",
        f"{_ROTULO_DO_MONITOR} · captação na camada do investidor. Execução de {gerado_em:%d/%m/%Y %H:%M}.",
        "",
        "Prazos: " + "; ".join(flash["prazos"]) + ".",
        "",
        *[f"- {frase}" for frase in flash["leitura"]],
        "",
        "## Captação por macroclasse",
        "",
        *_md_tabela(["Macroclasse", "Semana", "Por dia útil", "Por dia, 12 semanas", "Diferença por dia"],
                    _linhas_do_flash(flash), {1, 2, 3, 4}),
        "## Maiores saldos da semana",
        "",
        *_md_tabela(["", "Produto", "Peer group", "Gestora", "Semana"],
                    [[lado, nome_curto(p["nome"]), p["n2"], p["gestora"], reais_com_sinal(p["fluxo"])]
                     for lado, p in _maiores_do_flash(flash)], {4}),
        "## Como ler",
        "",
        *[f"- {item}" for item in _notas_do_flash(flash)],
        "",
        "## Fontes",
        "",
        *[f"- [{f.nome}]({f.url}): {f.uso}" for f in _FONTES_DO_FLASH],
        "",
    ]
    return "\n".join(linhas)


def render_flash_html(flash: dict, gerado_em: datetime | None = None) -> str:
    gerado_em = gerado_em or datetime.now()
    titulo = "Flash semanal da indústria de fundos"
    if not flash.get("available"):
        corpo = cabecalho("Captação na camada do investidor", titulo,
                          f"Execução de {gerado_em:%d/%m/%Y %H:%M}") + chamada(esc(flash.get("reason")))
        return casca(esc(titulo), esc(flash.get("reason")), corpo, faixa(_ROTULO_DO_MONITOR, f"{gerado_em:%d/%m/%Y}"))
    semana = flash["semana"]
    por_n1 = flash["por_n1"]
    corpo_da_figura = barras_divergentes([Linha(i["n1"], i["semana"], _fino(i["semana"])) for i in por_n1])
    titulo_da_figura = (f"{por_n1[0]['n1']} liderou a captação da semana" if por_n1 and por_n1[0]["semana"] > 0
                        else "Captação por macroclasse na semana")
    maiores = tabela(["", "Produto e gestora", "Peer group", "Semana"], [
        [esc(lado), f"{esc(nome_curto(p['nome']))}{miudo(esc(p['gestora']))}", esc(p["n2"]), esc(reais_com_sinal(p["fluxo"]))]
        for lado, p in _maiores_do_flash(flash)
    ], direita={3}, sinal={3})
    corpo = (
        cabecalho("Flash semanal · captação na camada do investidor",
                  f"Semana {periodo_br(semana['de'], semana['ate'])}",
                  f"Execução de {gerado_em:%d/%m/%Y %H:%M} · universo do ciclo de "
                  f"{competencia_br(flash['retrato']['competencia'])}")
        + nota(esc("Prazos: " + "; ".join(flash["prazos"]) + "."))
        + _lista(flash["leitura"])
        + secao("Captação por macroclasse")
        + figura(titulo_da_figura, f"Captação líquida do investidor na semana {periodo_br(semana['de'], semana['ate'])}",
                 corpo_da_figura, esc("Fonte: CVM, informe diário; fatia de fundos do perfil mensal."))
        + tabela(["Macroclasse", "Semana", "Por dia útil", "Por dia, 12 semanas", "Diferença por dia"],
                 [[esc(c) for c in linha] for linha in _linhas_do_flash(flash)],
                 direita={1, 2, 3, 4}, sinal={1, 2, 3, 4}, total=True)
        + secao("Maiores saldos da semana") + maiores
        + secao("Como ler") + _lista(_notas_do_flash(flash))
        + fontes_html(_FONTES_DO_FLASH)
    )
    previa = esc(_sem_marcas(flash["leitura"][0])) if flash.get("leitura") else ""
    return casca(esc(titulo), previa, corpo, faixa(_ROTULO_DO_MONITOR, f"{gerado_em:%d/%m/%Y}"))


def gravar_flash(config: Config, flash: dict, gerado_em: datetime | None = None) -> Path:
    """Grava `industria-flash-AAAA-MM-DD.md` e `.html`; devolve o caminho do html."""
    gerado_em = gerado_em or datetime.now()
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    base = config.reports_dir / f"industria-flash-{gerado_em:%Y-%m-%d}"
    base.with_suffix(".md").write_text(render_flash_markdown(flash, gerado_em), encoding="utf-8")
    html = base.with_suffix(".html")
    html.write_text(render_flash_html(flash, gerado_em), encoding="utf-8")
    return html
