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
from shelf_monitor.report.aggregate import build_dashboard_data, frase_do_funil
from shelf_monitor.report.digest import _juntar, _summary_sentence
from shelf_monitor.report.performance import blocos_de_rentabilidade, janela_do_bloco
from shelf_monitor.report.retail_flow import _reais
from shelf_monitor.report.shared import rotulo_da_casa
from shelf_monitor.report.shared.fontes import (
    CVM_CADASTRO,
    CVM_EXTRATO,
    CVM_INFORME_DIARIO,
    Fonte,
    fontes_do_relatorio,
)
from shelf_monitor.texto import contagem, data_br, milhar, meses_br, periodo_br

# A paleta categorica validada (scripts/validate_palette.js) vive nas variaveis
# CSS --s1..5 de `_STYLE`, que e de onde o JS a le. As duas listas Python
# que existiam aqui eram uma terceira copia dos mesmos hexes, nunca referenciada.

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
_SCRIPT = (_ASSETS / "dashboard.js").read_text(encoding="utf-8")

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
         prazo: str = "") -> str:
    """Um tile de manchete. O tile inteiro e o link para o painel do bloco."""
    extra = ""
    if comparacao:
        extra += f'<span class="kpi-comp">{_t(comparacao)}</span>'
    if prazo:
        extra += f'<span class="kpi-prazo">{_t(prazo)}</span>'
    return (
        f'<a class="kpi" href="#{alvo}">'
        f'<span class="kpi-label">{_t(rotulo)}</span>'
        f'<span class="kpi-value">{_t(valor)}</span>'
        f'<span class="kpi-note">{_t(nota)}</span>{extra}</a>'
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
                f"gaps reais, de {milhar(funil.get('aparentes', 0))} aparentes: "
                f"{contagem(gaps.get('total_cobertos', 0), 'já coberto', 'já cobertos')} "
                f"por espelho ou equivalente e {milhar(gaps.get('gaps_liquidez', 0))} só "
                "em outra faixa de liquidez",
            )
        return _kpi(
            "gaps-section", "Fundos que faltam na XP", milhar(gaps.get("total_gaps", 0)),
            f"de varejo, em vitrine concorrente e fora da vitrine da {base}; "
            f"{milhar(gaps.get('multi_house_gaps', 0))} em 2 ou mais casas",
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
        f"{base}, {_pct_sinal(sobre_pl)} do PL de início" if sobre_pl is not None else base,
        _comparacao(casa["captacao_liquida"],
                    [c for pid, c in fluxo.items() if pid != base_id],
                    "captacao_liquida", _reais, maior=True),
        prazo[1],
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
        f"{base}, ponderada pelo PL de cada fundo",
        _comparacao(retorno[base_id]["por_pl"],
                    [c for pid, c in retorno.items() if pid != base_id],
                    "por_pl", _pct, maior=True),
        periodo_br(janela.get("de"), janela.get("ate")) + base_calculo,
    )


def _kpi_custo(data: dict) -> str:
    base_id, base = _base(data)
    custo = _por_casa(data.get("custo"))
    if custo.get(base_id, {}).get("indice_mediano") is None:
        return ""
    return _kpi(
        "perf-section", "Custo relativo à categoria",
        _indice(custo[base_id]["indice_mediano"]),
        f"{base}, índice mediano da taxa de administração (1,00x = mediana)",
        _comparacao(custo[base_id]["indice_mediano"],
                    [c for pid, c in custo.items() if pid != base_id],
                    "indice_mediano", _indice, maior=False),
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
        "registrados na CVM e ainda não lançados, nas gestoras ligadas às casas",
        f"{base}: {milhar(da_base['pre_operational'])}" if da_base else "",
        f"cadastro da CVM de {data_br(cvm.get('collected_at'))}"
        if cvm.get("collected_at") else "",
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


# (painel, topico, frase) de uma leitura; None quando o bloco nao tem o que dizer.
Leitura = tuple[str | None, str, str]


def _leitura_gaps(report: RunReport, data: dict) -> Leitura | None:
    _, base = _base(data)
    flow = data.get("flow") or {}
    prazo = _prazo_do_fluxo(flow)
    gaps = data.get("gaps") or {}
    if gaps.get("available"):
        funil = frase_do_funil(gaps)
        if funil:
            frase = funil
        elif gaps.get("total_gaps"):
            frase = (
                f"{milhar(gaps['total_gaps'])} fundos de varejo estão em vitrines "
                f"concorrentes e fora da vitrine da {base} "
                f"({milhar(gaps.get('multi_house_gaps', 0))} em 2 ou mais casas)."
            )
        else:
            frase = (
                "Nenhum gap: toda vitrine concorrente coletada está contida na "
                f"vitrine da {base}."
            )
        # A mesma conta da "oportunidade de grade" do digest: o que capta varejo
        # fora da vitrine da base, sem o produto proprio do concorrente, que nao
        # se transfere. Cruza CNPJ entre casas, entao some com a supressao.
        grade = flow.get("grade") or {}
        if (prazo and grade.get("available") and grade.get("total_transferiveis")
                and not report.suprime(BLOCO_CNPJ)):
            frase += (
                " Transferíveis e captando varejo: "
                f"{contagem(grade['total_transferiveis'], 'fundo', 'fundos')}, "
                f"{_reais(grade.get('captacao_transferivel') or 0.0)} em {prazo[0]} "
                f"({prazo[1]}); {milhar(grade.get('total_operacionais') or 0)} de gestora "
                f"que a {base} já distribui."
            )
        return ("gaps-section", "Gaps de prateleira", frase)
    if gaps and not gaps.get("nao_se_aplica"):
        return (
            "gaps-section", "Gaps de prateleira",
            f"Análise suprimida nesta coleta: {gaps.get('reason') or 'dados insuficientes'}.",
        )
    return None


def _leitura_captacao(data: dict) -> Leitura | None:
    flow = data.get("flow") or {}
    prazo = _prazo_do_fluxo(flow)
    if prazo and flow.get("leitura"):
        return ("flow-section", f"Captação em {prazo[0]}", flow["leitura"][0])
    return None


def _leitura_rentabilidade(data: dict) -> Leitura | None:
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
    base_calculo = f" (base na {janela['base']})" if janela.get("base") else ""
    return (
        "perf-section", f"Rentabilidade em {janela['rotulo']}",
        "Retorno dos fundos da vitrine ponderado pelo PL de cada um, "
        f"{periodo_br(janela.get('de'), janela.get('ate'))}{base_calculo}: "
        + _juntar([f"{c['label']} {_pct(c['por_pl'])}" for c in ranking]) + ".",
    )


def _leitura_custo(data: dict) -> Leitura | None:
    # O nivel absoluto do indice carrega a camada de distribuicao do varejo
    # (ver custo.py): a frase compara as casas entre si, nunca contra 1,00x.
    custo = data.get("custo") or {}
    ranking = sorted(
        (c for c in custo.get("casas") or [] if c.get("indice_mediano") is not None),
        key=lambda c: c["indice_mediano"],
    ) if custo.get("available") else []
    if not ranking:
        return None
    return (
        "perf-section", "Custo",
        "Taxa de administração contra a mediana da categoria, em índice mediano "
        "da vitrine: "
        + _juntar([f"{c['label']} {_indice(c['indice_mediano'])}" for c in ranking])
        + ". Vale a diferença entre as casas, não a distância de 1,00x.",
    )


def _leitura_movimento(report: RunReport) -> Leitura:
    # A frase de abertura do digest, com a data da coleta de comparacao: sem
    # ela, "5 fundos entraram" nao diz desde quando.
    frase, alerta = _summary_sentence(report)
    frase = frase.replace("`", "")
    desde = _desde(report)
    if desde and not alerta:
        if "desde a última coleta" in frase:
            frase = frase.replace("desde a última coleta", desde)
        else:
            frase = f"{desde[:1].upper()}{desde[1:]}: {frase[:1].lower()}{frase[1:]}"
    return (None, "Movimento na vitrine", frase)


def _leituras(report: RunReport, data: dict) -> list[Leitura]:
    """(painel, topico, frase) - as manchetes da coleta, na ordem em que se decide.

    Frase pronta onde ela ja existe: a de captacao e a de movimento sao as
    mesmas do digest, para as superficies nao dizerem coisas diferentes. O
    topico leva o prazo do numero e o link para o painel com o detalhe.
    """
    itens = (
        _leitura_gaps(report, data), _leitura_captacao(data),
        _leitura_rentabilidade(data), _leitura_custo(data), _leitura_movimento(report),
    )
    return [item for item in itens if item]


def _html_leituras(itens: list[Leitura]) -> str:
    linhas = []
    for alvo, topico, frase in itens:
        cabeca = (
            f'<a class="topico" href="#{alvo}">{_e(topico)}</a>' if alvo
            else f'<span class="topico">{_e(topico)}</span>'
        )
        linhas.append(f"<li>{cabeca}{_t(frase)}</li>")
    return "\n".join(linhas)


# --- placar --------------------------------------------------------------------


def _celula(valor: str, sub: str = "", *, melhor: bool = False, titulo: str = "") -> str:
    classe = "num fora" if valor == _SEM else "num"
    title = f' title="{_e(titulo)}"' if titulo else ""
    principal = f'<strong class="melhor">{_e(valor)}</strong>' if melhor else _e(valor)
    extra = f'<span class="sub-val">{_e(sub)}</span>' if sub else ""
    return f'<td class="{classe}"{title}>{principal}{extra}</td>'


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
            celulas = [_celula(milhar(vitrine), so_nela)]
        else:
            celulas = [_celula(_SEM, "sem vitrine pública", titulo=p.get("listing_reason") or "")]
        if fluxo:
            c = fluxo.get(pid)
            celulas.append(
                _celula(_reais(c["captacao_liquida"]),
                        "" if c.get("sobre_pl") is None else f"{_pct_sinal(c['sobre_pl'])} do PL",
                        melhor=c["captacao_liquida"] == top_fluxo)
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
            f"Captação: {_e(prazo[0])}, {_e(prazo[1])}, fluxo dos fundos que a casa exibe, "
            f"no mercado inteiro ({_a(CVM_INFORME_DIARIO, 'informe diário da CVM')})."
        )
    if retorno:
        janela = rent[1]
        base_calculo = f", base na {janela['base']}" if janela.get("base") else ""
        notas.append(
            f"Rentabilidade: {_e(janela['rotulo'])}, "
            f"{_e(periodo_br(janela.get('de'), janela.get('ate')) + base_calculo)}, "
            "ponderada pelo PL de cada fundo; abaixo, a mediana dos fundos da vitrine."
        )
    if custo:
        notas.append(
            "Custo: índice mediano da taxa de administração contra a mediana da "
            f"categoria; menor é mais barato ({_a(CVM_EXTRATO, 'extrato da CVM')})."
        )
    if top_fluxo is not None or top_retorno is not None or top_custo is not None:
        notas.append("Em negrito, a melhor casa de cada coluna.")
    notas.append("Traço: a casa ficou fora do bloco, e o motivo está no painel dele.")

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
        cobertos = gaps.get("total_cobertos") or 0
        r["gaps"] = (
            f"{contagem(gaps.get('total_gaps', 0), 'gap real', 'gaps reais')} na "
            f"vitrine da {base}, por categoria, com captação e o esforço de fechar cada um"
            + (
                f"; {contagem(cobertos, 'gap aparente já coberto', 'gaps aparentes já cobertos')} "
                "por espelho ou equivalente, com a evidência."
                if cobertos else "."
            )
        )
    else:
        r["gaps"] = f"Suprimido nesta coleta: {gaps.get('reason') or 'dados insuficientes'}."

    prazo = _prazo_do_fluxo(data.get("flow") or {})
    r["flow"] = (
        f"{prazo[0].capitalize()}, {prazo[1]}: saldo por casa, por família de ativo, "
        "tendência e os fundos que mais captam e resgatam."
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
        f"{len(com_vitrine)} de {contagem(len(plataformas), 'casa', 'casas')} com vitrine "
        "alcançável: matriz por classe ANBIMA, arquitetura aberta e ticket de entrada."
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
        f"Inventário de {contagem(total_restritos, 'veículo', 'veículos')} fora do perfil "
        f"de varejo, que não {'entra' if total_restritos == 1 else 'entram'} em nenhuma "
        "comparação."
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


def _embed_json(data: dict) -> str:
    """Serializa para dentro de <script type="application/json">.

    Um nome de fundo contendo "</script>" encerraria o bloco e o resto viraria
    markup executavel; escapar "<" fecha essa via sem mudar o valor lido pelo
    JSON.parse.
    """
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


_PERF_SUB = """Três leituras do mesmo prazo: quanto rende o fundo <strong>mediano</strong>
      da prateleira, quanto rendeu o <strong>real médio</strong> (ponderado por PL) e quanto rendeu o
      <strong>cotista médio</strong> (ponderado por número de cotistas). A diferença entre as duas
      últimas diz onde está o desalinhamento: gap positivo, o dinheiro grande está nos fundos bons e
      o varejo nos piores; negativo, o inverso. É o retorno dos fundos que a casa exibe, com o peso
      que eles têm no <strong>mercado</strong> - não o retorno dos clientes dela: a CVM publica cota,
      PL e cotistas por fundo, somando todos os distribuidores. Cota ex-provento."""

_EXPOSTOS = """

    <h3>Fundos de muita gente rendendo abaixo da categoria <span class="perf-janela-rotulo"></span></h3>
    <p class="card-sub">O recorte com o que fazer: produto específico, com muita gente dentro,
      atrás da mediana da própria categoria. Deduplicado por CNPJ &mdash; a CVM publica cotistas e
      retorno por <strong>fundo</strong>, somando todos os distribuidores, então o mesmo produto em
      três vitrines tem os mesmos números nas três e a coluna Casas lista quem o exibe.</p>
    <div class="matrix-scroll" id="perf-expostos"></div>"""

_EXCLUSIVOS = """<p class="card-sub">Fundos de varejo (por CNPJ) que só uma das casas coletadas <strong>exibe
      na vitrine</strong> - o conceito competitivo, nada a ver com "fundo exclusivo" regulatório,
      que fica fora desta análise. Vitrine dos dois lados: o que a casa apenas administra e
      ninguém vê na tela não é diferencial de prateleira. Considera somente casas com coleta
      bem-sucedida.</p>
    <div class="chart-scroll" id="chart-exclusive"></div>"""

_FLOW_SUB_EXCLUSIVOS = """ A coluna &quot;só ela exibe&quot; é onde a
      atribuição a uma casa é inequívoca."""


def _composicao_sub(sem_cnpj: str | None, sem_proprio: str | None) -> str:
    cortes = []
    if not sem_proprio:
        cortes.append("entre fundo da gestora da própria casa e de terceiros")
    if not sem_cnpj:
        cortes.append("entre os fundos que outra casa também exibe e os que só ela exibe")
    texto = "Captação líquida e % do PL de início de cada vitrine"
    if cortes:
        texto += ", separada " + ", e ".join(cortes)
    texto += "."
    if sem_proprio:
        texto += f" Sem o corte produto próprio x terceiros: {html.escape(sem_proprio)}."
    return texto


def _a(fonte: Fonte, texto: str | None = None) -> str:
    """Link para a pagina de consulta da fonte."""
    return (
        f'<a href="{html.escape(fonte.url)}" target="_blank" rel="noopener">'
        f"{html.escape(texto or fonte.nome)}</a>"
    )


def _bloco_fontes(report: RunReport, config: Config, resumo: str) -> str:
    """O painel de fontes: cada uma com link, o que fornece e o arquivo lido."""
    itens = []
    for fonte in fontes_do_relatorio(report, config):
        item = f"<li>{_a(fonte)}: {html.escape(fonte.uso)}."
        if fonte.url_dados:
            item += (
                f' <span class="dim">Arquivos que o monitor lê: '
                f'<a href="{html.escape(fonte.url_dados)}" target="_blank" rel="noopener">'
                f"{html.escape(fonte.url_dados)}</a></span>"
            )
        itens.append(item + "</li>")
    if not itens:
        return ""
    return _painel(
        "fontes-section", "Fontes", resumo,
        '    <p class="card-sub">Onde conferir cada número deste painel. Só aparece a '
        "fonte que entrou nesta execução.</p>\n"
        f'    <ul class="flow-list fontes">{"".join(itens)}</ul>',
    )


def _fragmentos(report: RunReport) -> dict[str, str]:
    """Trechos do HTML fixo que um modulo pode trocar pelo motivo da supressao.

    Sem supressao (monitor de fundos) sao os trechos de sempre, byte a byte.
    """
    sem_cotistas = report.suprime(BLOCO_COTISTAS)
    sem_cnpj = report.suprime(BLOCO_CNPJ)
    return {
        "perf_sub": _PERF_SUB if not sem_cotistas else (
            "Duas leituras do mesmo prazo: quanto rende o fundo <strong>mediano</strong> "
            "da prateleira e quanto rendeu o <strong>real médio</strong> (ponderado por PL). "
            "É o retorno dos fundos que a casa exibe, com o peso que eles têm no "
            "<strong>mercado</strong>. Sem a leitura por cotistas: "
            f"{html.escape(sem_cotistas)}. Cota ex-provento."
        ),
        "expostos": "" if sem_cotistas else _EXPOSTOS,
        "exclusivos": _EXCLUSIVOS if not sem_cnpj else (
            f'<div class="note-box">Não se aplica a este monitor: '
            f"{html.escape(sem_cnpj)}.</div>"
        ),
        "flow_sub_exclusivos": "" if sem_cnpj else _FLOW_SUB_EXCLUSIVOS,
        "composicao_sub": _composicao_sub(sem_cnpj, report.suprime(BLOCO_PRODUTO_PROPRIO)),
    }


# --- paineis ---------------------------------------------------------------------
# Um painel por funcao, com o HTML de sempre. O monitor de previdencia
# reaproveita os que valem para ele (captacao, rentabilidade, aplicacao minima,
# saude da coleta, fontes) e escreve os proprios no lugar dos que nao valem (ver
# shelf_monitor.previdencia.dashboard).


def _painel_gaps(resumo: dict[str, str]) -> str:
    return _painel("gaps-section", "Gaps de prateleira da XP", resumo["gaps"], """
    <p class="card-sub">Fundos de varejo presentes na vitrine de ao menos um concorrente e
      ausentes da vitrine da XP (por CNPJ). Comparação vitrine &times; vitrine: o catálogo
      Open Finance não conta em nenhum dos lados. Com a carteira mensal da CVM, o gap
      aparente que a XP já cobre por espelho (mesmo master) ou equivalente (mesma gestora,
      carteira equivalente) sai da conta e fica listado com a prova.</p>
    <div class="tiles" id="gaps-tiles"></div>
    <p class="card-sub" id="gaps-funil"></p>

    <h3>Onde a vitrine da XP está rasa</h3>
    <p class="card-sub">Categorias com mais fundos de gap; cada célula conta a vitrine da casa
      na categoria. Mais escuro = mais fundos.</p>
    <div class="matrix-scroll" id="matrix-gaps"></div>

    <details><summary>Ver os fundos de gap (ordenados pelo esforço de fechar e pela captação)</summary><div id="table-gaps"></div></details>
    <details><summary>Ver os gaps aparentes que a XP já cobre (com a evidência)</summary><div id="table-gaps-cobertos"></div></details>
    <div class="coverage" id="gaps-coverage"></div>""")


def _painel_captacao(
    resumo: dict[str, str], trecho: dict[str, str], *, composicao: bool = True
) -> str:
    """O painel de captacao. `composicao=False` tira "De onde vem o saldo".

    A tabela separa o saldo por dois cortes, compartilhado x so nesta vitrine e
    produto proprio x terceiros. Com os dois suprimidos (previdencia) ela fica so
    com a captacao e o % do PL, que as manchetes e o placar ja mostram.
    """
    saldo = f"""

    <h3>De onde vem o saldo</h3>
    <p class="card-sub">{trecho["composicao_sub"]}</p>
    <div class="matrix-scroll" id="flow-composicao"></div>""" if composicao else ""
    return _painel("flow-section", "Captação dos fundos de vitrine", resumo["flow"], f"""
    <p class="card-sub">Fluxo do {_a(CVM_INFORME_DIARIO, "informe diário da CVM")} para os fundos que cada casa exibe.
      A CVM publica captação por <strong>fundo</strong>, nunca por distribuidor: um fundo em
      três vitrines entra inteiro nas três somas.{trecho["flow_sub_exclusivos"]}</p>
    <p class="card-sub" id="flow-prazo"></p>
    <ul class="leitura" id="flow-leitura"></ul>
    <div class="tiles" id="flow-tiles"></div>{saldo}

    <h3>Por família de ativo</h3>
    <p class="card-sub">Captação líquida dos fundos de cada vitrine, por família. A última
      coluna conta cada fundo uma vez, mesmo exibido por várias casas.</p>
    <div class="matrix-scroll" id="flow-familias"></div>

    <h3>Tendência</h3>
    <p class="card-sub">Média diária de captação líquida por competência. A média diária
      existe porque o mês corrente fecha com menos pregões que os meses cheios - comparar
      totais brutos marcaria toda casa como desacelerando, por efeito de calendário.</p>
    <div class="matrix-scroll" id="flow-trend"></div>

    <h3>Tendência dos fundos, por casa</h3>
    <p class="card-sub">Parcela dos fundos de cada vitrine (com PL de ao menos R$ 1 mi no
      início) em cada ritmo. Captando: acelerando, estável, desacelerando ou que virou
      entrada no último mês.</p>
    <div class="matrix-scroll" id="flow-tendencia-casas"></div>

    <details><summary>Quais fundos: maiores captações e resgates, o que a vitrine da XP
      perde, o que acelera</summary>
      <div id="flow-lists"></div>
    </details>
    <div class="coverage" id="flow-coverage"></div>""")


def _painel_rentabilidade(resumo: dict[str, str], trecho: dict[str, str]) -> str:
    return _painel("perf-section", "Rentabilidade e custo da vitrine", resumo["perf"], f"""
    <p class="card-sub">{trecho["perf_sub"]} Fonte: {_a(CVM_INFORME_DIARIO, "informe diário da CVM")}.</p>
    <div class="janelas" id="perf-janelas" role="group" aria-label="Prazo da rentabilidade"></div>
    <p class="card-sub" id="perf-nota"></p>
    <div class="tiles" id="perf-tiles"></div>
    <div class="legend" id="perf-legend"></div>
    <div class="chart-scroll" id="perf-chart"></div>

    <h3>Onde está o dinheiro em relação à mediana da categoria <span class="perf-janela-rotulo"></span></h3>
    <p class="card-sub">Quanto do PL da vitrine está em fundo que rende abaixo da mediana da
      <strong>própria categoria</strong> no universo oficial. O número que decide é o
      <strong>índice</strong> &mdash; share do PL dividido pelo share de fundos: abaixo de 1 o
      dinheiro <strong>evita</strong> os piores da categoria, acima de 1 se concentra neles.
      Cerca de metade de qualquer universo fica abaixo da mediana por construção, então a
      contagem de fundos sozinha não é sinal de nada. Fundo sem categoria com amostra
      suficiente fica fora do denominador.</p>
    <div class="matrix-scroll" id="perf-massa"></div>{trecho["expostos"]}

    <h3>Custo relativo à categoria</h3>
    <p class="card-sub">Índice mediano, não taxa mediana: comparar a taxa bruta entre casas mede o
      <strong>mix</strong> da prateleira (renda fixa custa ~0,2% ao ano, ações ~2%), não o preço dos
      produtos. 1,00x é exatamente a mediana da categoria de cada fundo. Fonte:
      {_a(CVM_EXTRATO, "extrato de informações da CVM")}.</p>
    <div class="matrix-scroll" id="custo-bloco"></div>

    <h3>Mandato autorizado pelo regulamento</h3>
    <p class="card-sub">Share da vitrine cujo regulamento <strong>autoriza</strong> cada coisa - nunca
      o que a carteira carrega. Um fundo pode ter autorização para 100% de crédito privado e carregar
      10%; medir exposição realizada exigiria a carteira mensal (CDA), fora do escopo. Verde acima das
      concorrentes, vermelho abaixo. Fonte: {_a(CVM_EXTRATO, "extrato de informações da CVM")}.</p>
    <div class="matrix-scroll" id="mandato-bloco"></div>
    <div class="coverage" id="perf-coverage"></div>""")


def _painel_vitrine(resumo: dict[str, str]) -> str:
    return _painel("vitrine-section", "Vitrine de varejo &mdash; o que o cliente vê", resumo["vitrine"], """
    <p class="card-sub">Fundos presentes na listagem da própria casa (site, API do portal ou
      área logada). O Open Finance não entra aqui: ele é o catálogo regulatório &mdash; tudo
      que a casa administra &mdash; e serve de referência, não de prateleira. O link de cada
      vitrine está em <a href="#fontes-section">Fontes</a>.</p>
    <div class="tiles" id="vitrine-tiles"></div>

    <h3>Matriz casa &times; classe ANBIMA</h3>
    <p class="card-sub">Contagem de fundos da vitrine por classificação ANBIMA oficial
      (join por CNPJ com o registro CVM). Mais escuro = mais fundos.</p>
    <div class="matrix-scroll" id="matrix-vitrine"></div>

    <div class="subgrid" style="margin-top:22px">
      <div>
        <h3>Arquitetura aberta</h3>
        <p class="card-sub">Produto de gestora da própria casa vs. de terceiros, na vitrine.</p>
        <div class="legend" id="legend-arch"></div>
        <div class="chart-scroll" id="chart-arch"></div>
      </div>
      <div>
        <h3>Ticket de entrada por casa</h3>
        <p class="card-sub">Quartis da aplicação mínima &mdash; quartis mostram a forma da
          prateleira; média esconderia. A taxa saiu daqui: quartil de taxa bruta entre casas
          mede o <strong>mix</strong> da prateleira, não o preço, e a leitura que presta está
          em &ldquo;Custo relativo à categoria&rdquo;.</p>
        <div id="table-costs"></div>
      </div>
    </div>

    <details><summary>Ver a matriz como tabela</summary><div id="table-vitrine"></div></details>
    <div class="coverage" id="vitrine-coverage"></div>""")


def _painel_classes(resumo: dict[str, str]) -> str:
    return _painel("classes-section", "Amplitude por classe de ativo", resumo["classes"], """
    <p class="card-sub">Quantos fundos cada casa <strong>exibe</strong> em cada classe - a leitura
      mais direta de onde cada prateleira é forte. Conta a vitrine; o catálogo Open Finance
      mede o que a casa administra e não entra em nenhum eixo comparativo.</p>
    <div class="legend" id="legend-classes"></div>
    <div class="chart-scroll" id="chart-classes"></div>
    <details><summary>Ver tabela</summary><div id="table-classes"></div></details>""")


def _painel_gestoras(resumo: dict[str, str]) -> str:
    return _painel("gestoras-section", "Profundidade: gestoras representadas", resumo["gestoras"], """
    <p class="card-sub">Número de gestoras distintas na <strong>vitrine</strong>. Amplitude sem
      profundidade indica concentração em poucas casas de gestão. Contada no catálogo, a
      distribuidora que também administra muito fundo fechado aparece com o dobro de
      gestoras sem ter posto uma só a mais na tela do cliente.</p>
    <div class="chart-scroll" id="chart-managers"></div>
    <details><summary>Ver as maiores gestoras de cada prateleira</summary>
      <div id="table-top-managers"></div></details>""")


def _painel_ticket(resumo: dict[str, str]) -> str:
    return _painel("ticket-section", "Aplicação mínima", resumo["ticket"], """
    <p class="card-sub">Distribuição dos fundos da vitrine por faixa de investimento mínimo -
      separa quem mira varejo de quem mira alta renda.</p>
    <p class="card-sub" id="leitura-ticket"></p>
    <div class="legend" id="legend-bands"></div>
    <div class="chart-scroll" id="chart-bands"></div>
    <details><summary>Ver tabela</summary><div id="table-bands"></div></details>""")


def _painel_exclusivos(resumo: dict[str, str], trecho: dict[str, str]) -> str:
    return _painel("exclusivos-section", "Fundos só nesta vitrine", resumo["exclusivos"], f"""
    {trecho["exclusivos"]}""")


def _painel_restritos(resumo: dict[str, str]) -> str:
    return _painel("restritos-section", "Fundos exclusivos e restritos &mdash; auxiliar", resumo["restritos"], """
    <p class="card-sub">Veículos fora do perfil de varejo (exclusivo no registro CVM,
      público profissional, marcados pela casa ou pelo nome). Ficam fora de toda análise
      acima; aqui é o inventário deles.</p>
    <div id="table-restricted"></div>""")


def _painel_cvm(resumo: dict[str, str]) -> str:
    return _painel("cvm-section", "Radar CVM &mdash; visão antecipada", resumo["cvm"], f"""
    <p class="card-sub">Fundos registrados na CVM por gestoras ligadas a cada casa. Um fundo em
      fase pré-operacional foi registrado mas ainda não lançado: é o sinal mais antecipado
      da estratégia de produto da casa. Fonte: {_a(CVM_CADASTRO, "cadastro de fundos da CVM")}.</p>
    <div class="legend" id="legend-cvm"></div>
    <div class="chart-scroll" id="chart-cvm"></div>
    <details><summary>Ver tabela por casa</summary><div id="table-cvm"></div></details>
    <details open><summary>Fundos em fase pré-operacional</summary><div id="table-pre-op"></div></details>""")


def _painel_coleta(resumo: dict[str, str]) -> str:
    return _painel("coleta-section", "Saúde da coleta", resumo["coleta"], """
    <p class="card-sub">Uma prateleira que aparece pequena pode ser fonte bloqueada, não
      prateleira enxuta. Confira o status antes de comparar números.</p>
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
<style>{_STYLE}</style>
</head>
<body>
<div class="wrap">
  <header class="page">
    <h1>{html.escape(config.titulo_relatorio)}</h1>
    <p class="sub">{html.escape(config.descricao_relatorio)} &middot; {html.escape(config.descricao_fontes)}
      &middot; gerado em {html.escape(generated)}</p>
  </header>

  <main>
  <section class="exec" id="visao-executiva" aria-label="Visão executiva">
    <div class="kpis">
{manchetes}
    </div>
    <div class="exec-grid">
      <section class="exec-card">
        <h2>Principais leituras</h2>
        <ul class="leituras">
{_html_leituras(leituras)}
        </ul>
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

<script type="application/json" id="dashboard-data">{_embed_json(data)}</script>
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
