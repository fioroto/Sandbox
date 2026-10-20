"""Dashboard HTML comparativo, self-contained - visao executiva primeiro.

Duas camadas. No topo, a visao executiva: as manchetes (um numero por pergunta,
com o prazo), as leituras da coleta e o placar casa a casa. E HTML estatico,
montado aqui em Python - aparece inteiro mesmo quando o arquivo abre num
visualizador que bloqueia script (a pre-visualizacao de anexo do e-mail, por
exemplo). Abaixo, cada bloco analitico mora num painel recolhido, aberto sob
demanda; o corpo dos paineis continua desenhado pelo JS a partir do JSON
embutido.

Ate 24/09/2026 a pagina abria com todos os blocos expandidos: ~13 mil pixels de
altura, 20 tabelas e ~4.300 palavras antes de qualquer conclusao. Quem abria
para decidir rolava atras do numero; quem queria o detalhe ja o tinha. O
detalhe continua todo aqui - so deixou de ser o caminho obrigatorio.

Tema claro, sempre. O painel seguia o tema do sistema operacional e abria
escuro em maquina configurada assim; a leitura executiva, e a impressao dela, e
em fundo claro.

Os graficos sao SVG desenhado no proprio arquivo: sem dependencia de CDN, o
arquivo abre offline e o commit fica auditavel. Cada grafico tem uma tabela
equivalente (exigida pela regra de contraste da paleta em modo claro).

Linguagem visual (docs/linguagem-visual.md, desde 27/09/2026): figura com
etiqueta vermelha, titulo que afirma a leitura, subtitulo com unidade e prazo e
fonte no rodape, a la The Economist e McKinsey; cor por casa com a cor de marca
de cada uma, a XP com a de destaque (shared/estilo.py). Aqui em Python ficam a
faixa por casa das manchetes (`_faixa`) e a barra fina do placar (`_mini`); os
graficos dos paineis sao do JS.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.models import (
    BLOCO_CNPJ,
    BLOCO_COTISTAS,
    BLOCO_PRODUTO_PROPRIO,
    RunReport,
)
from shelf_monitor.report.aggregate import build_dashboard_data
from shelf_monitor.report.digest import _juntar, _summary_sentence
from shelf_monitor.report.performance import blocos_de_rentabilidade, janela_do_bloco
from shelf_monitor.report.retail_flow import _reais
from shelf_monitor.report.shared import rotulo_da_casa
from shelf_monitor.report.shared.estilo import CORES_DAS_CASAS
from shelf_monitor.report.shared.nomes import nome_de_fundo
from shelf_monitor.report.shared.fontes import (
    CVM_CADASTRO,
    CVM_EXTRATO,
    CVM_INFORME_DIARIO,
    Fonte,
    fontes_do_relatorio,
)
from shelf_monitor.texto import contagem, data_br, milhar, meses_br, periodo_br

# A cor de cada casa (a de marca; a XP com a de destaque) vive em
# `shared/estilo.py` e, para o navegador, nas variaveis CSS --casa-* de
# `_STYLE`; um teste garante que as duas copias batem. O JS le as variaveis.

# CSS e JS moram em `assets/` e nao em strings aqui.
#
# O JS tinha 984 linhas dentro de uma string Python: nenhum editor o destacava,
# nenhum linter o via, e qualquer aspa ou barra invertida dependia de o literal
# ser `r"""`. Como arquivo, ele e um arquivo - e o teste que verifica que o
# dashboard nao quebra passa a poder ler o fonte dele.
#
# Sao embutidos no HTML (e nao referenciados por <link>/<src>) de proposito: o
# dashboard e um arquivo unico que se abre por file:// sem servidor, e essa
# propriedade ja e coberta por teste.
_ASSETS = Path(__file__).parent / "assets"
_STYLE = (_ASSETS / "dashboard.css").read_text(encoding="utf-8")
# Os primitivos de grafico moram em `graficos.js`, comuns ao painel da
# industria; a pagina embute os dois arquivos na mesma tag de script, e segue
# com exatamente dois (o JSON e o codigo).
_GRAFICOS = (_ASSETS / "graficos.js").read_text(encoding="utf-8")
_SCRIPT = _GRAFICOS + "\n" + (_ASSETS / "dashboard.js").read_text(encoding="utf-8")
# A marca da XP na barra preta do topo, na versao para fundo escuro - a mesma
# do material executivo da XP de onde veio o padrao visual.
_LOGO = (_ASSETS / "logo-xp.svg").read_text(encoding="utf-8").strip()

# A Inter do padrao executivo vem do Google Fonts. Sem rede (file:// offline)
# o navegador cai na pilha de --sans do CSS e a pagina segue inteira.
_FONTES_WEB = "\n".join((
    '<link rel="preconnect" href="https://fonts.googleapis.com">',
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
    '&amp;display=swap" rel="stylesheet">',
))

# Celula sem numero no placar: a casa ficou fora do bloco, e o motivo esta no
# painel dele. Traco, e nao zero - zero seria um numero.
_SEM = "–"


def _e(texto: object) -> str:
    return html.escape(str(texto))


# "-R$ 1,3 bi" quebrado no fim de uma linha ("-" numa, "R$ 1,3 bi" na outra, ou
# "bi" sozinho) le como outro numero. O valor em reais, com o sinal, fica
# inteiro na mesma linha - o hifen do sinal e ponto de quebra para o navegador.
_REAIS = re.compile(r"[+-]?R\$ \d[\d.,]* (?:bi|mi|mil)\b")


def _t(texto: object) -> str:
    """Texto da visao executiva: escapado, com os valores em reais inquebraveis."""
    return _REAIS.sub(lambda m: f'<span class="nowrap">{m.group(0)}</span>', _e(texto))


# --- formatos da visao executiva ----------------------------------------------


def _pct(valor: float | None) -> str:
    """"13,27%" - retorno, que o bloco ja guarda em pontos percentuais."""
    return _SEM if valor is None else f"{valor:.2f}%".replace(".", ",")


def _pct_sinal(valor: float | None) -> str:
    """"-0,41%" - fluxo sobre o PL de inicio, que tem direcao."""
    return _SEM if valor is None else f"{valor:+.2f}%".replace(".", ",")


def _indice(valor: float | None) -> str:
    """"1,65x" - custo contra a mediana da categoria."""
    return _SEM if valor is None else f"{valor:.2f}x".replace(".", ",")


# --- o que a visao executiva le do payload ------------------------------------


def _base(data: dict) -> tuple[str, str]:
    """(id, rotulo) da casa contra a qual o painel le as demais - a XP em fundos."""
    for bloco in (data.get("gaps") or {}, data.get("flow") or {}):
        if bloco.get("baseline_id"):
            pid = bloco["baseline_id"]
            return pid, bloco.get("baseline_label") or rotulo_da_casa(pid)
    pid = next((p.platform_id for p in PLATFORMS.values() if p.is_baseline), "xp")
    return pid, rotulo_da_casa(pid)


def _prazo_do_fluxo(flow: dict) -> tuple[str, str] | None:
    """("3 meses", "de 01/07/2026 a 23/09/2026") da captacao, ou None sem o bloco."""
    periodo = flow.get("periodo") or {}
    if not (flow.get("available") and periodo.get("de") and periodo.get("ate")):
        return None
    meses = len(periodo.get("meses_lidos") or [])
    return (meses_br(meses) if meses else "no período", periodo_br(periodo["de"], periodo["ate"]))


def _rentabilidade(data: dict) -> tuple[dict, dict] | None:
    """(bloco, janela) da janela mais longa disponivel - 12 meses antes de 3."""
    blocos = blocos_de_rentabilidade(data.get("performance_12m"), data.get("performance"))
    if not blocos:
        return None
    return blocos[0], janela_do_bloco(blocos[0])


def _por_casa(bloco: dict | None) -> dict[str, dict]:
    """As linhas por casa de um bloco disponivel, indexadas pelo id da casa."""
    if not (bloco and bloco.get("available")):
        return {}
    return {c["platform_id"]: c for c in bloco.get("casas") or []}


def _comparacao(base_valor, outras: list[dict], chave: str, formato, *, maior: bool) -> str:
    """"Maior: BTG Pactual, +R$ 3,0 bi" - a melhor concorrente ao lado da base.

    Quando a base lidera, diz isso e mostra a segunda: o numero sozinho nao
    diz se e bom ou ruim.
    """
    validas = [o for o in outras if o.get(chave) is not None]
    if base_valor is None or not validas:
        return ""
    melhor = (max if maior else min)(validas, key=lambda o: o[chave])
    lidera = base_valor >= melhor[chave] if maior else base_valor <= melhor[chave]
    rotulo = "maior" if maior else "menor"
    if lidera:
        return (
            f"A {rotulo} entre {len(validas) + 1} casas; a seguir, "
            f"{melhor['label']}: {formato(melhor[chave])}"
        )
    return f"{rotulo.capitalize()}: {melhor['label']}, {formato(melhor[chave])}"


# --- manchetes -----------------------------------------------------------------


def _kpi(alvo: str, rotulo: str, valor: str, nota: str, comparacao: str = "",
         prazo: str = "", faixa: str = "") -> str:
    """Um tile de manchete. O tile inteiro e o link para o painel do bloco.

    `faixa` e o SVG de `_faixa`: onde a casa-base cai entre as casas, logo
    abaixo do numero.
    """
    extra = ""
    if comparacao:
        extra += f'<span class="kpi-comp">{_t(comparacao)}</span>'
    if prazo:
        extra += f'<span class="kpi-prazo">{_t(prazo)}</span>'
    return (
        f'<a class="kpi" href="#{alvo}">'
        f'<span class="kpi-label">{_t(rotulo)}</span>'
        f'<span class="kpi-value">{_t(valor)}</span>{faixa}'
        f'<span class="kpi-note">{_t(nota)}</span>{extra}</a>'
    )


def _var_da_casa(platform_id: str) -> str:
    """A cor da casa como variavel CSS (a mesma de estilo.CORES_DAS_CASAS)."""
    return f"var(--casa-{platform_id})" if platform_id in CORES_DAS_CASAS else "var(--casa-outra)"


def _faixa(pontos: list[tuple[str, str, float]], formato, base_id: str) -> str:
    """Um ponto por casa numa linha, da menor a maior: onde a base cai.

    (id, rotulo, valor) por casa. A base sai maior e com anel; as pontas levam
    o menor e o maior valor escritos. Menos de duas casas, ou todas iguais, nao
    e faixa - sai vazio. SVG estatico, desenhado aqui: a visao executiva nao
    depende de script.
    """
    validos = [(pid, rotulo, v) for pid, rotulo, v in pontos if v is not None]
    if len(validos) < 2:
        return ""
    menor = min(v for _, _, v in validos)
    maior = max(v for _, _, v in validos)
    if maior == menor:
        return ""
    esq, dir_, y = 7.0, 193.0, 11.0

    def x(v: float) -> float:
        return esq + (v - menor) / (maior - menor) * (dir_ - esq)

    # A base por ultimo, por cima das outras quando encostam.
    ordem = sorted(validos, key=lambda p: p[0] == base_id)
    pontos_svg = "".join(
        f'<circle cx="{x(v):.1f}" cy="{y}" r="{6.5 if pid == base_id else 4.5}" '
        f'fill="{_var_da_casa(pid)}" stroke="#ffffff" stroke-width="{2 if pid == base_id else 1.5}">'
        f"<title>{_e(rotulo)}: {_e(formato(v))}</title></circle>"
        for pid, rotulo, v in ordem
    )
    descricao = "; ".join(f"{rotulo} {formato(v)}" for _, rotulo, v in validos)
    return (
        '<span class="kpi-faixa">'
        f'<svg viewBox="0 0 200 34" role="img" aria-label="{_e("Entre as casas: " + descricao)}">'
        f'<line x1="{esq}" x2="{dir_}" y1="{y}" y2="{y}" stroke="var(--grid)" stroke-width="2" '
        'stroke-linecap="round"/>'
        f"{pontos_svg}"
        f'<text x="{esq - 4}" y="31" font-size="10.5" fill="var(--muted)">{_e(formato(menor))}</text>'
        f'<text x="{dir_ + 4}" y="31" font-size="10.5" fill="var(--muted)" text-anchor="end">'
        f"{_e(formato(maior))}</text>"
        "</svg></span>"
    )


def _tiles(*tiles: str) -> str:
    """As manchetes na ordem dada; a que nao tem dado (string vazia) sai."""
    return "\n".join(t for t in tiles if t)


# Uma funcao por manchete, e nao um bloco so: o monitor de previdencia monta a
# visao executiva dele com parte destas e com as proprias (ver
# shelf_monitor.previdencia.dashboard). Cada uma devolve "" quando o bloco dela
# nao tem o numero da casa-base.


def _kpi_gaps(data: dict) -> str:
    _, base = _base(data)
    gaps = data.get("gaps") or {}
    if gaps.get("available"):
        funil = gaps.get("funil") or {}
        if (gaps.get("carteira") or {}).get("available"):
            return _kpi(
                "gaps-section", "Fundos que faltam na XP", milhar(gaps.get("total_gaps", 0)),
                _pontos(
                    f"reais, de {milhar(funil.get('aparentes', 0))} aparentes",
                    f"{contagem(gaps.get('total_cobertos', 0), 'coberto', 'cobertos')} "
                    "por fundo da XP",
                    f"{milhar(gaps['gaps_na_lista'])} na lista da casa"
                    if gaps.get("gaps_na_lista") else "",
                    f"{milhar(gaps.get('gaps_liquidez', 0))} em outra liquidez",
                ),
            )
        return _kpi(
            "gaps-section", "Fundos que faltam na XP", milhar(gaps.get("total_gaps", 0)),
            f"em vitrine concorrente, fora da {base} · "
            f"{milhar(gaps.get('multi_house_gaps', 0))} em 2+ casas",
        )
    if not gaps.get("nao_se_aplica"):
        # Previdencia: "fundos que faltam" por CNPJ e o veiculo de cada
        # seguradora, nao oferta que falta - o tile sai, e o motivo mora no
        # painel de gaps. Suprimido por falta de dado, o tile fica e diz por que.
        return _kpi(
            "gaps-section", "Fundos que faltam na XP", "-",
            f"análise suprimida nesta coleta: {gaps.get('reason') or 'dados insuficientes'}",
        )
    return ""


def _kpi_captacao(data: dict) -> str:
    base_id, base = _base(data)
    flow = data.get("flow") or {}
    prazo = _prazo_do_fluxo(flow)
    fluxo = _por_casa(flow)
    if not (prazo and base_id in fluxo):
        return ""
    casa = fluxo[base_id]
    sobre_pl = casa.get("sobre_pl")
    return _kpi(
        "flow-section", f"Captação líquida em {prazo[0]}", _reais(casa["captacao_liquida"]),
        f"{base} · {_pct_sinal(sobre_pl)} do PL de início" if sobre_pl is not None else base,
        _comparacao(casa["captacao_liquida"],
                    [c for pid, c in fluxo.items() if pid != base_id],
                    "captacao_liquida", _reais, maior=True),
        prazo[1],
        _faixa([(pid, c["label"], c.get("captacao_liquida")) for pid, c in fluxo.items()],
               _reais, base_id),
    )


def _kpi_rentabilidade(data: dict) -> str:
    base_id, base = _base(data)
    rent = _rentabilidade(data)
    retorno = _por_casa(rent[0]) if rent else {}
    if not (rent and retorno.get(base_id, {}).get("por_pl") is not None):
        return ""
    janela = rent[1]
    base_calculo = f", base na {janela['base']}" if janela.get("base") else ""
    return _kpi(
        "perf-section", f"Rentabilidade em {janela['rotulo']}",
        _pct(retorno[base_id]["por_pl"]),
        f"{base} · ponderada por PL",
        _comparacao(retorno[base_id]["por_pl"],
                    [c for pid, c in retorno.items() if pid != base_id],
                    "por_pl", _pct, maior=True),
        periodo_br(janela.get("de"), janela.get("ate")) + base_calculo,
        _faixa([(pid, c["label"], c.get("por_pl")) for pid, c in retorno.items()],
               _pct, base_id),
    )


def _kpi_custo(data: dict) -> str:
    base_id, base = _base(data)
    custo = _por_casa(data.get("custo"))
    if custo.get(base_id, {}).get("indice_mediano") is None:
        return ""
    return _kpi(
        "perf-section", "Custo relativo à categoria",
        _indice(custo[base_id]["indice_mediano"]),
        f"{base} · taxa de administração ÷ mediana da categoria",
        _comparacao(custo[base_id]["indice_mediano"],
                    [c for pid, c in custo.items() if pid != base_id],
                    "indice_mediano", _indice, maior=False),
        faixa=_faixa([(pid, c["label"], c.get("indice_mediano")) for pid, c in custo.items()],
                     _indice, base_id),
    )


def _kpi_radar(data: dict) -> str:
    base_id, base = _base(data)
    cvm = data.get("cvm") or {}
    if not cvm.get("available"):
        return ""
    casas = cvm.get("houses") or []
    pre_op = sum(h.get("pre_operational", 0) for h in casas)
    da_base = next((h for h in casas if h.get("house") == base_id), None)
    return _kpi(
        "cvm-section", "Fundos em fase pré-operacional", milhar(pre_op),
        "registrados na CVM, ainda não lançados · todas as casas",
        f"{base}: {milhar(da_base['pre_operational'])}" if da_base else "",
        f"cadastro da CVM de {data_br(cvm.get('collected_at'))}"
        if cvm.get("collected_at") else "",
        _faixa([(h["house"], h.get("label") or rotulo_da_casa(h["house"]), h.get("pre_operational"))
                for h in casas], milhar, base_id),
    )


def _kpis(data: dict) -> str:
    """As manchetes: um numero por pergunta, todos da casa-base, com o prazo.

    Os quatro tiles de antes eram "fundos em vitrine (soma das casas)",
    "fundos que faltam na XP", "registrados na CVM" e "em fase
    pre-operacional" - so o segundo dizia algo sobre a posicao da XP; os
    outros descreviam o universo. Agora cada tile responde uma pergunta de
    decisao (o que falta, quanto capta, quanto rende, quanto custa, o que vem
    por ai), traz a melhor concorrente ao lado e leva ao painel do bloco.
    """
    return _tiles(
        _kpi_gaps(data), _kpi_captacao(data), _kpi_rentabilidade(data),
        _kpi_custo(data), _kpi_radar(data),
    )


# --- leituras ------------------------------------------------------------------


def _desde(report: RunReport) -> str:
    """"desde a coleta de 23/09/2026" - o prazo do movimento de vitrine."""
    datas = sorted({
        d.prev_date for d in report.shelf_diffs if d.prev_date and not d.is_first_run
    })
    if not datas:
        return ""
    if len(datas) == 1:
        return f"desde a coleta de {data_br(datas[0])}"
    return f"desde as coletas de {data_br(datas[0])} a {data_br(datas[-1])}"


# (painel, topico, texto[, prazo]) de uma leitura; None quando o bloco nao tem o
# que dizer. O prazo e opcional: as leituras da previdencia ainda o trazem na
# frase.
Leitura = tuple


def _pontos(*partes: str) -> str:
    """"a · b · c" - numeros lado a lado, sem conjuncao; parte vazia sai."""
    return " · ".join(p for p in partes if p)


def _leitura_gaps(report: RunReport, data: dict) -> Leitura | None:
    """Gaps em numeros: o funil e, com a captacao, o que se transfere.

    Ate 27/09/2026 era a frase inteira do funil (`frase_do_funil`), com cinco
    oracoes; o funil agora e um grafico no painel, e aqui fica a conta.
    """
    _, base = _base(data)
    flow = data.get("flow") or {}
    prazo = _prazo_do_fluxo(flow)
    gaps = data.get("gaps") or {}
    if gaps.get("available"):
        funil = gaps.get("funil") or {}
        if (gaps.get("carteira") or {}).get("available") and funil.get("aparentes"):
            texto = _pontos(
                f"{milhar(funil.get('reais', 0))} reais de {milhar(funil['aparentes'])} aparentes",
                f"{milhar(funil.get('cobertos', 0))} cobertos por fundo da XP"
                if funil.get("cobertos") else "",
                f"{milhar(funil['na_lista'])} na lista da casa" if funil.get("na_lista") else "",
                f"{milhar(funil.get('outra_liquidez', 0))} em outra liquidez"
                if funil.get("outra_liquidez") else "",
            )
        elif gaps.get("total_gaps"):
            texto = _pontos(
                f"{milhar(gaps['total_gaps'])} fora da vitrine da {base}",
                f"{milhar(gaps.get('multi_house_gaps', 0))} em 2+ casas",
            )
        else:
            texto = f"Nenhum: toda vitrine concorrente coletada está na {base}"
        # A mesma conta da "oportunidade de grade" do digest: o que capta varejo
        # fora da vitrine da base, sem o produto proprio do concorrente, que nao
        # se transfere. Cruza CNPJ entre casas, entao some com a supressao.
        grade = flow.get("grade") or {}
        periodo = ""
        if (prazo and grade.get("available") and grade.get("total_transferiveis")
                and not report.suprime(BLOCO_CNPJ)):
            texto = _pontos(
                texto,
                f"{milhar(grade['total_transferiveis'])} transferíveis captam "
                f"{_reais(grade.get('captacao_transferivel') or 0.0)}",
                f"{milhar(grade.get('total_operacionais') or 0)} de gestora que a {base} já distribui",
            )
            periodo = f"captação em {prazo[0]}, {prazo[1]}"
        return ("gaps-section", "Gaps de prateleira", texto, periodo)
    if gaps and not gaps.get("nao_se_aplica"):
        return (
            "gaps-section", "Gaps de prateleira",
            f"Suprimida nesta coleta: {gaps.get('reason') or 'dados insuficientes'}",
        )
    return None


def _diferenca_por_familia(flow: dict, base_id: str, outra_id: str) -> list[tuple[str, float]]:
    """(familia, base - outra) das tres familias que mais explicam a diferenca."""
    diferencas = []
    for linha in flow.get("por_categoria") or []:
        casas = linha.get("casas") or {}
        a = (casas.get(base_id) or {}).get("captacao_liquida") or 0.0
        b = (casas.get(outra_id) or {}).get("captacao_liquida") or 0.0
        if a or b:
            diferencas.append((linha.get("categoria") or "-", a - b))
    return sorted(diferencas, key=lambda d: -abs(d[1]))[:3]


def _leitura_captacao(data: dict) -> Leitura | None:
    """A base contra a que mais capta entre as outras, e de onde vem a diferenca."""
    base_id, base = _base(data)
    flow = data.get("flow") or {}
    prazo = _prazo_do_fluxo(flow)
    fluxo = _por_casa(flow)
    if not prazo:
        return None
    periodo = f"{prazo[0]}, {prazo[1]}"
    outras = [c for pid, c in fluxo.items() if pid != base_id and c.get("captacao_liquida") is not None]
    if base_id not in fluxo or not outras:
        if flow.get("leitura"):
            return ("flow-section", "Captação", flow["leitura"][0], periodo)
        return None
    rival = max(outras, key=lambda c: c["captacao_liquida"])
    texto = (
        f"{base} {_reais(fluxo[base_id]['captacao_liquida'])} vs. "
        f"{rival['label']} {_reais(rival['captacao_liquida'])}"
    )
    familias = _diferenca_por_familia(flow, base_id, rival["platform_id"])
    if familias:
        texto += "; diferença por família: " + _pontos(
            *(f"{nome} {_reais(valor)}" for nome, valor in familias)
        )
    return ("flow-section", "Captação", texto, periodo)


def _leitura_rentabilidade(data: dict) -> Leitura | None:
    base_id, base = _base(data)
    rent = _rentabilidade(data)
    if not rent:
        return None
    bloco, janela = rent
    ranking = sorted(
        (c for c in bloco.get("casas") or [] if c.get("por_pl") is not None),
        key=lambda c: -c["por_pl"],
    )
    if not ranking:
        return None
    periodo = periodo_br(janela.get("de"), janela.get("ate"))
    if janela.get("base"):
        periodo += f", base na {janela['base']}"
    casa = next((c for c in ranking if c["platform_id"] == base_id), None)
    if casa is None:
        texto = _pontos(*(f"{c['label']} {_pct(c['por_pl'])}" for c in ranking))
    else:
        posicao = ranking.index(casa) + 1
        lider = ranking[0]
        sem_cotistas = BLOCO_COTISTAS in (data.get("suprimidos") or {})
        abaixo = (casa.get("alocacao_abaixo_da_mediana") or {}).get("share_pl")
        texto = _pontos(
            f"{base} {_pct(casa['por_pl'])} por PL ({posicao}ª de {len(ranking)})",
            f"{lider['label']} {_pct(lider['por_pl'])}" if lider is not casa else "",
            f"cotista médio {_pct(casa['por_cotista'])}"
            if not sem_cotistas and casa.get("por_cotista") is not None else "",
            f"{round(abaixo * 100)}% do PL abaixo da mediana" if abaixo is not None else "",
        )
    return ("perf-section", f"Rentabilidade em {janela['rotulo']}", texto, periodo)


def _leitura_custo(data: dict) -> Leitura | None:
    # O nivel absoluto do indice carrega a camada de distribuicao do varejo
    # (ver custo.py): a leitura compara as casas entre si, nunca contra 1,00x.
    base_id, base = _base(data)
    custo = data.get("custo") or {}
    ranking = sorted(
        (c for c in custo.get("casas") or [] if c.get("indice_mediano") is not None),
        key=lambda c: c["indice_mediano"],
    ) if custo.get("available") else []
    if not ranking:
        return None
    prazo = "taxa de administração ÷ mediana da categoria"
    casa = next((c for c in ranking if c["platform_id"] == base_id), None)
    if casa is None:
        return ("perf-section", "Custo",
                _pontos(*(f"{c['label']} {_indice(c['indice_mediano'])}" for c in ranking)), prazo)
    barata = ranking[0]

    def parcela(v: float | None) -> str:
        return "-" if v is None else f"{round(v * 100)}%"

    return (
        "perf-section", "Custo",
        _pontos(
            f"{base} {_indice(casa['indice_mediano'])}",
            f"mais barata: {barata['label']} {_indice(barata['indice_mediano'])}"
            if barata is not casa else "a mais barata",
            f"{parcela(casa.get('share_caro'))} dos fundos caros, "
            f"{parcela(casa.get('share_barato'))} baratos",
        ),
        prazo,
    )


def _leitura_movimento(report: RunReport) -> Leitura:
    """Entradas, saidas e alteracoes, com a data da coleta comparada no prazo.

    Os casos que pedem explicacao (nada coletado, primeira coleta, nova linha
    de base) seguem com a frase do digest: ali a conta sozinha engana.
    """
    from shelf_monitor.report.digest import _summary_counts

    frase, alerta = _summary_sentence(report)
    desde = _desde(report)
    rebaseada = any(d.nova_linha_de_base for d in report.shelf_diffs)
    if alerta or not desde or rebaseada:
        frase = frase.replace("`", "")
        if desde and not alerta:
            frase = frase.replace("desde a última coleta", desde)
        return (None, "Movimento na vitrine", frase, desde)
    entradas, saidas, alteracoes, catalogo = _summary_counts(report)
    return (
        None, "Movimento na vitrine",
        _pontos(
            contagem(entradas, "entrada", "entradas"),
            contagem(saidas, "saída", "saídas"),
            contagem(alteracoes, "alteração", "alterações"),
            f"{milhar(catalogo)} só no catálogo" if catalogo else "",
        ),
        desde,
    )


def _leituras(report: RunReport, data: dict) -> list[Leitura]:
    """(painel, topico, texto, prazo) - as leituras da coleta, na ordem em que se decide.

    Numero, nao frase: desde 27/09/2026 cada leitura e uma linha de tabela
    (tema e prazo a esquerda, os numeros a direita, separados por " · "), e
    so traz o que as manchetes e o placar nao mostram - de onde vem a
    diferenca de captacao, o cotista medio, a parcela de fundos caros.
    """
    itens = (
        _leitura_gaps(report, data), _leitura_captacao(data),
        _leitura_rentabilidade(data), _leitura_custo(data), _leitura_movimento(report),
    )
    return [item for item in itens if item]


def _html_leituras(itens: list[Leitura]) -> str:
    """Uma linha de tabela por leitura: tema (link para o painel) e prazo, os numeros."""
    linhas = []
    for item in itens:
        alvo, topico, frase = item[:3]
        prazo = item[3] if len(item) > 3 else ""
        cabeca = (
            f'<a class="topico" href="#{alvo}">{_e(topico)}</a>' if alvo
            else f'<span class="topico">{_e(topico)}</span>'
        )
        if prazo:
            cabeca += f'<small class="prazo">{_t(prazo)}</small>'
        linhas.append(f'<tr><th scope="row">{cabeca}</th><td>{_t(frase)}</td></tr>')
    return "\n".join(linhas)


# --- placar --------------------------------------------------------------------


def _celula(valor: str, sub: str = "", *, melhor: bool = False, titulo: str = "",
            barra: str = "") -> str:
    classe = "num fora" if valor == _SEM else "num"
    title = f' title="{_e(titulo)}"' if titulo else ""
    principal = f'<strong class="melhor">{_e(valor)}</strong>' if melhor else _e(valor)
    extra = f'<span class="sub-val">{_e(sub)}</span>' if sub else ""
    return f'<td class="{classe}"{title}>{principal}{barra}{extra}</td>'


def _mini(valor: float | None, maximo: float, platform_id: str, *, sinal: bool = False) -> str:
    """Barra fina na celula do placar, na cor da casa: o tamanho do numero.

    Com `sinal` (captacao) ela parte do meio - para a direita se entra
    dinheiro, para a esquerda se sai. Sem numero ou sem escala, nada.
    """
    if valor is None or maximo <= 0:
        return ""
    cor = _var_da_casa(platform_id)
    if sinal:
        largura = min(50.0, abs(valor) / maximo * 50)
        lado = "left:50%;right:auto" if valor >= 0 else "right:50%"
        return (f'<span class="mini div" aria-hidden="true">'
                f'<i style="{lado};width:{largura:.1f}%;background:{cor}"></i></span>')
    if valor <= 0:
        return ""
    largura = min(100.0, valor / maximo * 100)
    return (f'<span class="mini" aria-hidden="true">'
            f'<i style="width:{largura:.1f}%;background:{cor}"></i></span>')


def _placar(
    report: RunReport,
    data: dict,
    *,
    sub_vitrine: dict[str, str] | None = None,
    cabeca_vitrine: str = "fundos",
    casas_sem_vitrine: dict[str, str] | None = None,
) -> str:
    """O placar casa a casa: uma linha por casa, as colunas que decidem.

    Vitrine, captacao, rentabilidade e custo lado a lado, a casa-base
    destacada e a melhor casa de cada coluna em negrito. Casa que ficou fora
    de um bloco aparece com traco - o motivo esta no painel do bloco -, nunca
    some da tabela: sumir faria parecer que ela nao existe.

    Os argumentos opcionais existem para a previdencia, e sem eles o placar e
    o de fundos. `sub_vitrine` (id -> texto) troca a linha de baixo da celula
    de vitrine, que em fundos e "N so nela", e `cabeca_vitrine` diz o que a
    coluna traz. `casas_sem_vitrine`
    (id -> motivo) sao as casas que o modulo SABE nao terem vitrine publica:
    entram no placar mesmo sem snapshot (o `report` le so o ultimo snapshot
    util, e elas nao tem nenhum) e saem como "sem vitrine publica", nao como
    "coleta indisponivel" - nao houve falha de coleta.
    """
    base_id, _ = _base(data)
    sem_vitrine = casas_sem_vitrine or {}
    sem_cnpj = report.suprime(BLOCO_CNPJ)
    flow = data.get("flow") or {}
    prazo = _prazo_do_fluxo(flow)
    fluxo = _por_casa(flow) if prazo else {}
    rent = _rentabilidade(data)
    retorno = _por_casa(rent[0]) if rent else {}
    custo = _por_casa(data.get("custo"))

    def lider(valores: dict[str, dict], chave: str, maior: bool = True) -> float | None:
        numeros = [v[chave] for v in valores.values() if v.get(chave) is not None]
        if len(numeros) < 2:
            return None
        return max(numeros) if maior else min(numeros)

    top_fluxo = lider(fluxo, "captacao_liquida")
    top_retorno = lider(retorno, "por_pl")
    top_custo = lider(custo, "indice_mediano", maior=False)

    # Barra fina so onde o tamanho informa: vitrine e captacao. Retorno e
    # custo ficam sem: 13,1% contra 13,7% a partir do zero sao duas barras
    # cheias iguais - verdade, mas nenhuma leitura; a posicao esta na faixa da
    # manchete e no dumbbell do painel. Escala: o maior da coluna, em modulo.
    teto_vitrine = float(max((p.get("listing_funds") or 0 for p in data.get("platforms") or []), default=0))
    teto_fluxo = max((abs(v["captacao_liquida"]) for v in fluxo.values()
                      if v.get("captacao_liquida") is not None), default=0.0)

    cabeca = [
        '<th scope="col">Casa</th>',
        f'<th scope="col" class="num">Vitrine<small>{_e(cabeca_vitrine)}</small></th>',
    ]
    if fluxo:
        cabeca.append(f'<th scope="col" class="num">Captação líquida<small>{_e(prazo[0])}</small></th>')
    if retorno:
        cabeca.append(
            f'<th scope="col" class="num">Rentabilidade<small>{_e(rent[1]["rotulo"])}, por PL</small></th>'
        )
    if custo:
        cabeca.append('<th scope="col" class="num">Custo<small>índice vs. categoria</small></th>')

    plataformas = list(data.get("platforms") or [])
    vistas = {p["platform_id"] for p in plataformas}
    plataformas += [
        {"platform_id": pid, "label": rotulo_da_casa(pid)}
        for pid in sem_vitrine if pid not in vistas
    ]

    linhas = []
    for p in plataformas:
        pid = p["platform_id"]
        vitrine = p.get("listing_funds") or 0
        if pid in sem_vitrine:
            celulas = [_celula(_SEM, "sem vitrine pública", titulo=sem_vitrine[pid])]
        elif p.get("status") == "unavailable":
            celulas = [_celula(_SEM, "coleta indisponível", titulo=p.get("error") or "")]
        elif vitrine:
            if sub_vitrine is not None:
                so_nela = sub_vitrine.get(pid, "")
            else:
                so_nela = "" if sem_cnpj else f"{milhar(p.get('exclusive_count') or 0)} só nela"
            celulas = [_celula(milhar(vitrine), so_nela, barra=_mini(vitrine, teto_vitrine, pid))]
        else:
            celulas = [_celula(_SEM, "sem vitrine pública", titulo=p.get("listing_reason") or "")]
        if fluxo:
            c = fluxo.get(pid)
            celulas.append(
                _celula(_reais(c["captacao_liquida"]),
                        "" if c.get("sobre_pl") is None else f"{_pct_sinal(c['sobre_pl'])} do PL",
                        melhor=c["captacao_liquida"] == top_fluxo,
                        barra=_mini(c["captacao_liquida"], teto_fluxo, pid, sinal=True))
                if c else _celula(_SEM)
            )
        if retorno:
            c = retorno.get(pid)
            celulas.append(
                _celula(_pct(c["por_pl"]), f"mediana {_pct(c.get('mediana'))}",
                        melhor=c["por_pl"] == top_retorno)
                if c and c.get("por_pl") is not None else _celula(_SEM)
            )
        if custo:
            c = custo.get(pid)
            celulas.append(
                _celula(_indice(c["indice_mediano"]), melhor=c["indice_mediano"] == top_custo)
                if c and c.get("indice_mediano") is not None else _celula(_SEM)
            )
        classe = ' class="base"' if pid == base_id else ""
        linhas.append(
            f'<tr{classe}><th scope="row">{_e(p["label"])}</th>{"".join(celulas)}</tr>'
        )

    notas = []
    if fluxo:
        notas.append(
            f"Captação: {_e(prazo[1])}, fundos exibidos, mercado inteiro "
            f"({_a(CVM_INFORME_DIARIO, 'informe diário da CVM')})."
        )
    if retorno:
        janela = rent[1]
        base_calculo = f", base na {janela['base']}" if janela.get("base") else ""
        notas.append(
            f"Rentabilidade: {_e(periodo_br(janela.get('de'), janela.get('ate')) + base_calculo)}; "
            "embaixo, a mediana."
        )
    if custo:
        notas.append(
            f"Custo: taxa ÷ mediana da categoria, menor é mais barato ({_a(CVM_EXTRATO, 'extrato da CVM')})."
        )
    if top_fluxo is not None or top_retorno is not None or top_custo is not None:
        notas.append("Negrito: a melhor da coluna.")
    notas.append("Traço: fora do bloco (motivo no painel).")

    return (
        '<div class="placar-scroll"><table class="placar">'
        f'<thead><tr>{"".join(cabeca)}</tr></thead>'
        f'<tbody>{"".join(linhas)}</tbody></table></div>'
        f'<p class="nota">{" ".join(notas)}</p>'
    )


# --- paineis sob demanda --------------------------------------------------------


def _resumos(report: RunReport, data: dict, config: Config) -> dict[str, str]:
    """A linha de cada painel recolhido: o que ele responde, com o numero-chave.

    E o que o leitor ve sem abrir nada, entao tem de dizer se vale abrir.
    """
    _, base = _base(data)
    sem_cnpj = report.suprime(BLOCO_CNPJ)
    plataformas = data.get("platforms") or []
    com_vitrine = [p for p in plataformas if p.get("status") != "unavailable" and p.get("listing_funds")]
    r: dict[str, str] = {}

    gaps = data.get("gaps") or {}
    if gaps.get("nao_se_aplica"):
        r["gaps"] = "Não se aplica a este monitor."
    elif gaps.get("available"):
        # Os cobertos pelos tres motivos: fundo da XP, lista da casa e outra
        # liquidez (19/10/2026).
        cobertos = sum(
            gaps.get(chave) or 0
            for chave in ("total_cobertos", "gaps_na_lista", "gaps_liquidez")
        )
        r["gaps"] = (
            f"{contagem(gaps.get('total_gaps', 0), 'gap real', 'gaps reais')} na vitrine da {base}"
            + (f" · {contagem(cobertos, 'já coberto', 'já cobertos')}, com o motivo e a evidência."
               if cobertos else ".")
        )
    else:
        r["gaps"] = f"Suprimido nesta coleta: {gaps.get('reason') or 'dados insuficientes'}."

    prazo = _prazo_do_fluxo(data.get("flow") or {})
    r["flow"] = (
        f"{prazo[0].capitalize()}, {prazo[1]}: saldo por casa, família, tendência e fundos."
        if prazo else "Indisponível nesta coleta."
    )

    janelas = [
        janela_do_bloco(b)["rotulo"]
        for b in blocos_de_rentabilidade(data.get("performance_12m"), data.get("performance"))
    ]
    # O resumo so promete o que o painel tem: custo ou mandato indisponivel
    # (a previdencia, sem cobertura de taxa) sai da lista e e dito como tal.
    tem = ["retorno por casa", "onde está o dinheiro"]
    falta = []
    for chave, oque in (("custo", "custo contra a categoria"), ("mandato", "mandato do regulamento")):
        if (data.get(chave) or {}).get("available"):
            tem.append(oque)
        else:
            falta.append(chave)
    fim = "."
    if falta:
        fim = (
            f"; {_juntar(falta)} {'indisponível' if len(falta) == 1 else 'indisponíveis'} "
            "nesta coleta."
        )
    r["perf"] = (
        f"{_juntar(janelas).capitalize()}: {_juntar(tem)}{fim}"
        if janelas else "Indisponível nesta coleta."
    )

    r["vitrine"] = (
        f"{len(com_vitrine)} de {contagem(len(plataformas), 'casa', 'casas')} com vitrine: "
        "classes ANBIMA, arquitetura aberta, ticket."
    )
    r["classes"] = "Quantos fundos cada vitrine exibe em cada classe de ativo."
    r["gestoras"] = (
        "Gestoras distintas na vitrine: "
        + _juntar([f"{p['label']} {milhar(p.get('distinct_managers') or 0)}" for p in com_vitrine])
        + "."
        if com_vitrine else "Sem vitrine coletada."
    )

    ticket = data.get("leitura_ticket")
    if not ticket:
        r["ticket"] = "Distribuição dos fundos por faixa de aplicação mínima."
    else:
        teto = f"R$ {milhar(int(ticket['teto']))}"
        if ticket.get("sem_base"):
            r["ticket"] = f"Sem base para comparar o ticket da {ticket['baseline_label']}."
        elif ticket.get("baseline_varejo") == 0:
            r["ticket"] = f"A vitrine da {ticket['baseline_label']} não tem fundo abaixo de {teto}."
        else:
            r["ticket"] = (
                f"{ticket['baseline_label']}: {milhar(ticket['baseline_varejo'])} fundos "
                f"abaixo de {teto}, contra {milhar(ticket['melhor_varejo'])} da "
                f"{ticket['melhor_label']}."
            )

    r["exclusivos"] = (
        "Não se aplica a este monitor." if sem_cnpj
        else "Fundos de varejo que só uma casa exibe: "
        + _juntar([f"{p['label']} {milhar(p.get('exclusive_count') or 0)}" for p in com_vitrine])
        + "."
        if com_vitrine else "Sem vitrine coletada."
    )

    restritos = data.get("restricted") or {}
    total_restritos = restritos.get("total") or 0
    r["restritos"] = (
        f"{contagem(total_restritos, 'veículo', 'veículos')} fora do varejo e das comparações."
    )

    cvm = data.get("cvm") or {}
    if cvm.get("available"):
        pre_op = sum(h.get("pre_operational", 0) for h in cvm.get("houses") or [])
        r["cvm"] = (
            f"{contagem(cvm.get('total') or 0, 'fundo registrado', 'fundos registrados')} "
            f"nas gestoras ligadas às casas; {milhar(pre_op)} em fase pré-operacional."
        )
        diff = cvm.get("diff") or {}
        if diff and not diff.get("is_first_run") and diff.get("new_registrations"):
            r["cvm"] = r["cvm"][:-1] + (
                f"; {contagem(diff['new_registrations'], 'registro novo', 'registros novos')} "
                "desde a coleta anterior."
            )
    else:
        r["cvm"] = "Cadastro da CVM indisponível nesta coleta."

    por_status: dict[str, int] = {}
    for p in plataformas:
        por_status[p.get("status")] = por_status.get(p.get("status"), 0) + 1
    partes = [
        f"{por_status.get('ok', 0)} de {contagem(len(plataformas), 'casa', 'casas')} "
        "com coleta ok"
    ]
    if por_status.get("partial"):
        partes.append(contagem(por_status["partial"], "parcial", "parciais"))
    if por_status.get("unavailable"):
        partes.append(contagem(por_status["unavailable"], "indisponível", "indisponíveis"))
    sem_vitrine = [p["label"] for p in plataformas
                   if p.get("status") != "unavailable" and not p.get("listing_funds")]
    r["coleta"] = ", ".join(partes) + (
        f"; sem vitrine pública: {_juntar(sem_vitrine)}." if sem_vitrine else "."
    )

    fontes = [f.nome for f in fontes_do_relatorio(report, config)]
    r["fontes"] = (
        f"{contagem(len(fontes), 'fonte', 'fontes')} com link para conferir cada número."
        if fontes else ""
    )
    return r


def _painel(ident: str, titulo: str, resumo: str, corpo: str) -> str:
    """Bloco recolhido: o titulo e a linha de resumo a vista, o corpo sob demanda."""
    return (
        f'<section class="panel" id="{ident}">\n'
        f"    <details>\n"
        f'      <summary><h2>{titulo}</h2><span class="panel-lead">{_e(resumo)}</span></summary>\n'
        f'      <div class="panel-body">\n{corpo}\n      </div>\n'
        f"    </details>\n"
        f"  </section>"
    )


# As chaves do payload que guardam nome de fundo. O JS escreve o que recebe,
# entao o padrao de exibicao (`shared.nomes`, 19/10/2026) entra aqui, uma vez,
# na serializacao: o dicionario de `build_dashboard_data` segue o da fonte.
_CHAVES_DE_NOME = frozenset({
    "name", "nome", "nome_cobre", "nome_master", "familia_nome",
    "nome_possivel_equivalente", "possible_xp_equivalent",
})
# Onde "name" e "nome" nao sao de fundo: a gestora, o CSV da lista, a fonte.
_SEM_NOME_DE_FUNDO = frozenset({"top_managers", "arquivos", "invalidas", "fontes"})


def _nomes_para_ler(no):
    """O payload com os nomes de fundo no padrao de exibicao; o resto igual."""
    if isinstance(no, dict):
        return {
            chave: (
                nome_de_fundo(valor) if chave in _CHAVES_DE_NOME and isinstance(valor, str)
                else valor if chave in _SEM_NOME_DE_FUNDO
                else _nomes_para_ler(valor)
            )
            for chave, valor in no.items()
        }
    if isinstance(no, list):
        return [_nomes_para_ler(valor) for valor in no]
    return no


def _embed_json(data: dict) -> str:
    """Serializa para dentro de <script type="application/json">.

    Um nome de fundo contendo "</script>" encerraria o bloco e o resto viraria
    markup executavel; escapar "<" fecha essa via sem mudar o valor lido pelo
    JSON.parse.
    """
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def _como_ler(*itens: str) -> str:
    """A metodologia do painel, recolhida no fim: uma nota curta por item.

    Ate 27/09/2026 cada painel abria com um paragrafo de 4 a 7 linhas de
    metodologia antes do primeiro numero. A ressalva continua aqui - a que
    evita leitura errada nao se corta -, so deixou de ser o caminho obrigatorio.
    """
    notas = "".join(f"<li>{item}</li>" for item in itens if item)
    if not notas:
        return ""
    return f'\n    <details class="como-ler"><summary>Como ler</summary><ul>{notas}</ul></details>'


# O cotista medio so existe onde a CVM conta cotistas que sao gente; a
# previdencia (cotista = seguradora) troca pela nota da supressao.
_PERF_COMO_LER = (
    "Mediana: o fundo do meio da vitrine. Real médio: ponderado por PL. Cotista médio: "
    "ponderado por cotistas. Gap = real médio − cotista médio: positivo, o dinheiro grande "
    "está nos fundos bons e o varejo nos piores."
)

_EXPOSTOS = """

    <h3>Fundos de muita gente rendendo abaixo da categoria <span class="perf-janela-rotulo"></span></h3>
    <div class="matrix-scroll" id="perf-expostos"></div>"""

_EXCLUSIVOS = """<p class="card-sub">Fundos de varejo (por CNPJ) na vitrine de uma casa só. Não é o
      &ldquo;fundo exclusivo&rdquo; regulatório.</p>
    <div class="chart-scroll" id="chart-exclusive"></div>"""

_FLOW_COMO_LER_EXCLUSIVOS = (
    "&ldquo;Só nesta vitrine&rdquo; é onde a atribuição a uma casa é inequívoca."
)


def _composicao_sub(sem_cnpj: str | None, sem_proprio: str | None) -> str:
    """Nota do saldo: vazia quando os dois cortes valem; o motivo, quando um sai."""
    if sem_proprio and not sem_cnpj:
        return f'<p class="card-sub">Sem o corte próprio × terceiros: {html.escape(sem_proprio)}.</p>'
    return ""


def _a(fonte: Fonte, texto: str | None = None) -> str:
    """Link para a pagina de consulta da fonte."""
    return (
        f'<a href="{html.escape(fonte.url)}" target="_blank" rel="noopener">'
        f"{html.escape(texto or fonte.nome)}</a>"
    )


def _bloco_fontes(report: RunReport, config: Config, resumo: str) -> str:
    """O painel de fontes: cada uma com link, o que fornece e o arquivo lido."""
    linhas = []
    for fonte in fontes_do_relatorio(report, config):
        arquivos = (
            f'<a href="{html.escape(fonte.url_dados)}" target="_blank" rel="noopener">'
            f"{html.escape(fonte.url_dados)}</a>" if fonte.url_dados else "–"
        )
        linhas.append(
            f"<tr><td>{_a(fonte)}</td><td>{html.escape(fonte.uso)}</td>"
            f'<td class="dim">{arquivos}</td></tr>'
        )
    if not linhas:
        return ""
    return _painel(
        "fontes-section", "Fontes", resumo,
        '    <p class="card-sub">Só as fontes que entraram nesta execução.</p>\n'
        '    <div class="table-scroll fontes"><table><thead><tr><th>Fonte</th><th>O que fornece</th>'
        f'<th>Arquivos lidos</th></tr></thead><tbody>{"".join(linhas)}</tbody></table></div>',
    )


def _fragmentos(report: RunReport) -> dict[str, str]:
    """Trechos do HTML fixo que um modulo pode trocar pelo motivo da supressao."""
    sem_cotistas = report.suprime(BLOCO_COTISTAS)
    sem_cnpj = report.suprime(BLOCO_CNPJ)
    return {
        "perf_como_ler": _PERF_COMO_LER if not sem_cotistas else (
            "Mediana: o fundo do meio da vitrine. Real médio: ponderado por PL. "
            f"Sem a leitura por cotistas: {html.escape(sem_cotistas)}."
        ),
        "expostos": "" if sem_cotistas else _EXPOSTOS,
        "exclusivos": _EXCLUSIVOS if not sem_cnpj else (
            f'<div class="note-box">Não se aplica a este monitor: '
            f"{html.escape(sem_cnpj)}.</div>"
        ),
        "flow_como_ler_exclusivos": "" if sem_cnpj else _FLOW_COMO_LER_EXCLUSIVOS,
        "composicao_sub": _composicao_sub(sem_cnpj, report.suprime(BLOCO_PRODUTO_PROPRIO)),
    }


# --- paineis ---------------------------------------------------------------------
# Um painel por funcao. O monitor de previdencia reaproveita os que valem para
# ele (captacao, rentabilidade, aplicacao minima, saude da coleta, fontes) e
# escreve os proprios no lugar dos que nao valem (ver
# shelf_monitor.previdencia.dashboard).
#
# Texto (docs/linguagem-visual.md, secao 5): uma linha de abertura por painel;
# quem afirma e o titulo de cada figura; a metodologia mora em "Como ler".


def _painel_gaps(resumo: dict[str, str]) -> str:
    return _painel("gaps-section", "Gaps de prateleira da XP", resumo["gaps"], """
    <p class="card-sub">Fundos de varejo na vitrine de um concorrente e fora da vitrine da XP, por CNPJ.</p>
    <div class="tiles" id="gaps-tiles"></div>
    <p class="card-sub" id="gaps-funil"></p>

    <h3>Onde a vitrine da XP está rasa</h3>
    <div class="matrix-scroll" id="matrix-gaps"></div>

    <details><summary>Ver os fundos de gap (ordenados pelo esforço de fechar e pela captação)</summary><div id="table-gaps"></div></details>
    <details><summary>Ver os gaps aparentes que a XP já cobre (com o motivo e a evidência)</summary><div id="table-gaps-cobertos"></div></details>""" + _como_ler(
        "Vitrine × vitrine: o catálogo Open Finance não conta em nenhum dos lados.",
        "Coberto: a XP já tem o fundo de outro jeito - o mesmo master (espelho) ou fundo da "
        "mesma gestora com carteira equivalente, pela carteira mensal da CVM; o fundo na "
        "lista da casa (distribui ou pode distribuir); ou a estratégia em outra faixa de "
        "liquidez. Sai da conta e fica listado com o motivo e a prova.",
        "Esforço: inclusão operacional (gestora que a XP já distribui, com fundo na vitrine "
        "ou na lista da casa) ou comercial (abrir gestora).",
    ) + """
    <div class="coverage" id="gaps-coverage"></div>""")


def _painel_captacao(
    resumo: dict[str, str], trecho: dict[str, str], *, composicao: bool = True
) -> str:
    """O painel de captacao. `composicao=False` tira "De onde vem o saldo".

    O saldo e separado por dois cortes, compartilhado x so nesta vitrine e
    produto proprio x terceiros. Com os dois suprimidos (previdencia) ele fica so
    com a captacao e o % do PL, que as manchetes e o placar ja mostram.
    """
    saldo = f"""

    <h3>De onde vem o saldo</h3>{trecho["composicao_sub"]}
    <div class="matrix-scroll" id="flow-composicao"></div>""" if composicao else ""
    return _painel("flow-section", "Captação dos fundos de vitrine", resumo["flow"], f"""
    <p class="card-sub">Captação líquida dos fundos que cada casa exibe
      ({_a(CVM_INFORME_DIARIO, "informe diário da CVM")}).</p>
    <p class="card-sub" id="flow-prazo"></p>
    <div class="tiles" id="flow-tiles"></div>{saldo}

    <h3>Por família de ativo</h3>
    <div class="matrix-scroll" id="flow-familias"></div>

    <h3>Tendência</h3>
    <div class="matrix-scroll" id="flow-trend"></div>

    <h3>Tendência dos fundos, por casa</h3>
    <div class="matrix-scroll" id="flow-tendencia-casas"></div>

    <details><summary>Quais fundos: maiores captações e resgates, o que a vitrine da XP
      perde, o que acelera</summary>
      <div id="flow-lists"></div>
    </details>""" + _como_ler(
        "Fluxo por fundo, nunca por distribuidor: um fundo em três vitrines entra inteiro nas "
        "três somas. " + trecho["flow_como_ler_exclusivos"],
        "Família: a última coluna conta cada fundo uma vez, mesmo exibido por várias casas.",
        "Tendência em média por pregão: o mês corrente tem menos pregões que os cheios.",
        "Ritmo: fundos com PL de ao menos R$ 1 mi no início, cada um num ritmo só - a "
        "linha da tabela soma 100%.",
    ) + """
    <div class="coverage" id="flow-coverage"></div>""")


def _painel_rentabilidade(resumo: dict[str, str], trecho: dict[str, str]) -> str:
    return _painel("perf-section", "Rentabilidade e custo da vitrine", resumo["perf"], f"""
    <p class="card-sub">Retorno dos fundos que a casa exibe, com o peso que eles têm no mercado
      ({_a(CVM_INFORME_DIARIO, "informe diário da CVM")}) - não o retorno dos clientes dela.</p>
    <div class="janelas" id="perf-janelas" role="group" aria-label="Prazo da rentabilidade"></div>
    <p class="card-sub" id="perf-nota"></p>
    <div class="tiles" id="perf-tiles"></div>
    <div class="legend" id="perf-legend"></div>
    <div class="chart-scroll" id="perf-chart"></div>

    <h3>Onde está o dinheiro em relação à mediana da categoria <span class="perf-janela-rotulo"></span></h3>
    <div class="matrix-scroll" id="perf-massa"></div>{trecho["expostos"]}

    <h3>Custo relativo à categoria</h3>
    <div class="matrix-scroll" id="custo-bloco"></div>

    <h3>Mandato autorizado pelo regulamento</h3>
    <div class="matrix-scroll" id="mandato-bloco"></div>""" + _como_ler(
        trecho["perf_como_ler"],
        "A CVM publica cota, PL e cotistas por fundo, somando todos os distribuidores. "
        "Cota ex-provento.",
        "Onde está o dinheiro: índice = % do PL ÷ % dos fundos abaixo da mediana da própria "
        "categoria. Abaixo de 1, o dinheiro evita os piores; metade de qualquer universo fica "
        "abaixo da mediana por construção.",
        "Custo em índice, não em taxa: a taxa bruta mede o mix (renda fixa ~0,2% ao ano, ações "
        f"~2%). 1,00x = mediana da categoria ({_a(CVM_EXTRATO, 'extrato de informações da CVM')}). "
        "Por classe de fundo (nível 1 da ANBIMA), a mesma conta dentro de cada classe. A XP "
        "entra pela base interna (a lista distribuível); as demais casas, pela vitrine do site.",
        "Mandato: o que o regulamento autoriza, nunca o que a carteira carrega (isso exigiria a "
        "carteira mensal). ▲ acima das concorrentes, ▼ abaixo.",
    ) + """
    <div class="coverage" id="perf-coverage"></div>""")


def _painel_vitrine(resumo: dict[str, str]) -> str:
    return _painel("vitrine-section", "Vitrine de varejo &mdash; o que o cliente vê", resumo["vitrine"], """
    <p class="card-sub">Fundos na listagem da própria casa. Link de cada vitrine em
      <a href="#fontes-section">Fontes</a>.</p>
    <div class="tiles" id="vitrine-tiles"></div>

    <h3>Matriz casa &times; classe ANBIMA</h3>
    <div class="matrix-scroll" id="matrix-vitrine"></div>

    <div class="subgrid" style="margin-top:22px">
      <div>
        <h3>Arquitetura aberta</h3>
        <div class="legend" id="legend-arch"></div>
        <div class="chart-scroll" id="chart-arch"></div>
      </div>
      <div>
        <h3>Ticket de entrada por casa</h3>
        <p class="card-sub">Quartis da aplicação mínima.</p>
        <div id="table-costs"></div>
      </div>
    </div>

    <details><summary>Ver a matriz como tabela</summary><div id="table-vitrine"></div></details>""" + _como_ler(
        "Vitrine: site, API do portal ou área logada da casa. O Open Finance é o catálogo "
        "regulatório (tudo que a casa administra): referência, não prateleira.",
        "Classe ANBIMA pelo CNPJ no registro da CVM.",
        "Taxa fora do ticket: quartil de taxa bruta mede o mix da prateleira, não o preço - "
        "ver &ldquo;Custo relativo à categoria&rdquo;.",
    ) + """
    <div class="coverage" id="vitrine-coverage"></div>""")


def _painel_classes(resumo: dict[str, str]) -> str:
    return _painel("classes-section", "Amplitude por classe de ativo", resumo["classes"], """
    <p class="card-sub">Fundos que cada casa <strong>exibe</strong>, por classe; o catálogo não entra.</p>
    <div class="legend" id="legend-classes"></div>
    <div class="chart-scroll" id="chart-classes"></div>
    <details><summary>Ver tabela</summary><div id="table-classes"></div></details>""")


def _painel_gestoras(resumo: dict[str, str]) -> str:
    return _painel("gestoras-section", "Profundidade: gestoras representadas", resumo["gestoras"], """
    <p class="card-sub">Gestoras distintas na <strong>vitrine</strong>.</p>
    <div class="chart-scroll" id="chart-managers"></div>
    <details><summary>Ver as maiores gestoras de cada prateleira</summary>
      <div id="table-top-managers"></div></details>""" + _como_ler(
        "Amplitude sem profundidade é concentração em poucas gestoras.",
        "Contada no catálogo, a casa que administra muito fundo fechado apareceria com o dobro "
        "de gestoras sem pôr uma só a mais na tela do cliente.",
    ))


def _painel_ticket(resumo: dict[str, str]) -> str:
    return _painel("ticket-section", "Aplicação mínima", resumo["ticket"], """
    <p class="card-sub" id="leitura-ticket"></p>
    <div class="legend" id="legend-bands"></div>
    <div class="chart-scroll" id="chart-bands"></div>
    <details><summary>Ver tabela</summary><div id="table-bands"></div></details>""")


def _painel_exclusivos(resumo: dict[str, str], trecho: dict[str, str]) -> str:
    return _painel("exclusivos-section", "Fundos só nesta vitrine", resumo["exclusivos"], f"""
    {trecho["exclusivos"]}""")


def _painel_restritos(resumo: dict[str, str]) -> str:
    return _painel("restritos-section", "Fundos exclusivos e restritos &mdash; auxiliar", resumo["restritos"], """
    <p class="card-sub">Fora do perfil de varejo (exclusivo na CVM, profissional, marcado pela casa
      ou no nome) e fora de toda análise acima.</p>
    <div id="table-restricted"></div>""")


def _painel_cvm(resumo: dict[str, str]) -> str:
    return _painel("cvm-section", "Radar CVM &mdash; visão antecipada", resumo["cvm"], f"""
    <p class="card-sub">Registrados pelas gestoras ligadas a cada casa
      ({_a(CVM_CADASTRO, "cadastro de fundos da CVM")}). Pré-operacional: registrado e ainda não lançado.</p>
    <div class="legend" id="legend-cvm"></div>
    <div class="chart-scroll" id="chart-cvm"></div>
    <details><summary>Ver tabela por casa</summary><div id="table-cvm"></div></details>
    <details open><summary>Fundos em fase pré-operacional</summary><div id="table-pre-op"></div></details>""")


def _painel_coleta(resumo: dict[str, str]) -> str:
    return _painel("coleta-section", "Saúde da coleta", resumo["coleta"], """
    <p class="card-sub">Vitrine pequena pode ser fonte bloqueada: confira o status antes de comparar.</p>
    <div id="table-sources"></div>""")


# --- a pagina ---------------------------------------------------------------------


def _documento(
    config: Config,
    report: RunReport,
    data: dict,
    *,
    manchetes: str,
    leituras: list[Leitura],
    placar: str,
    grupos: list[tuple[str, list[str]]],
) -> str:
    """A pagina inteira: cabecalho, visao executiva e os grupos de paineis.

    Comum aos dois monitores; cada um escolhe as manchetes, as leituras, o
    placar e quais paineis entram em cada grupo. Painel vazio (o de fontes, sem
    fonte nenhuma) sai, e grupo que fica sem painel tambem.
    """
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    grupos_cheios = [(titulo, [p for p in paineis if p]) for titulo, paineis in grupos]
    detalhes = "\n\n".join(
        f'  <p class="grupo">{titulo}</p>\n  ' + "\n\n  ".join(paineis)
        for titulo, paineis in grupos_cheios
        if paineis
    )
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{html.escape(config.titulo_relatorio)}</title>
{_FONTES_WEB}
<style>{_STYLE}</style>
</head>
<body>
<div class="topbar"><div class="wrap">
  <span class="marca" aria-hidden="true">{_LOGO}</span>
  <span class="topbar-t">{html.escape(config.rotulo_assunto)} &middot; <b>{html.escape(generated)}</b></span>
</div></div>
<div class="wrap">
  <header class="page">
    <div class="kicker">{html.escape(config.descricao_relatorio)}</div>
    <h1>{html.escape(config.titulo_relatorio)}</h1>
    <p class="sub">{html.escape(config.descricao_fontes[:1].upper() + config.descricao_fontes[1:])} &middot; gerado em {html.escape(generated)}</p>
  </header>

  <main>
  <section class="exec" id="visao-executiva" aria-label="Visão executiva">
    <div class="kpis">
{manchetes}
    </div>
    <div class="exec-grid">
      <section class="exec-card">
        <h2>Principais leituras</h2>
        <table class="leituras"><tbody>
{_html_leituras(leituras)}
        </tbody></table>
      </section>
      <section class="exec-card">
        <h2>Casa a casa</h2>
        {placar}
      </section>
    </div>
  </section>

  <div class="detalhes-head">
    <div>
      <h2>Detalhes sob demanda</h2>
      <p>Cada bloco abre com um clique, com a metodologia, as tabelas e os fundos.</p>
    </div>
    <button type="button" class="abrir-tudo" id="abrir-tudo" hidden>Abrir todos</button>
  </div>

{detalhes}
  </main>

  <footer>
    Gerado em {html.escape(generated)}. Cada fonte, com o link para consulta, está na
    seção <a href="#fontes-section">Fontes</a>.
  </footer>
</div>

<script type="application/json" id="dashboard-data">{_embed_json(_nomes_para_ler(data))}</script>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def _gravar(config: Config, documento: str) -> Path:
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    path = config.reports_dir / "dashboard.html"
    path.write_text(documento, encoding="utf-8")
    return path


def render_dashboard(config: Config, report: RunReport) -> Path:
    data = build_dashboard_data(report)
    trecho = _fragmentos(report)
    resumo = _resumos(report, data, config)
    return _gravar(config, _documento(
        config, report, data,
        manchetes=_kpis(data),
        leituras=_leituras(report, data),
        placar=_placar(report, data),
        grupos=[
            ("Oportunidade e desempenho", [
                _painel_gaps(resumo),
                _painel_captacao(resumo, trecho),
                _painel_rentabilidade(resumo, trecho),
            ]),
            ("Composição das prateleiras", [
                _painel_vitrine(resumo),
                _painel_classes(resumo),
                _painel_gestoras(resumo),
                _painel_ticket(resumo),
                _painel_exclusivos(resumo, trecho),
                _painel_restritos(resumo),
            ]),
            ("Radar e qualidade dos dados", [
                _painel_cvm(resumo),
                _painel_coleta(resumo),
                _bloco_fontes(report, config, resumo["fontes"]),
            ]),
        ],
    ))
