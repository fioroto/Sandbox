"""Dashboard do monitor de previdencia: o molde do de fundos, com o que a previdencia mede.

A pagina e a do monitor de fundos (`shelf_monitor.report.dashboard`): tema
claro, visao executiva no topo - manchetes, leituras e placar, em HTML estatico
que aparece mesmo sem script - e cada bloco analitico num painel recolhido,
desenhado pelo mesmo JS a partir do mesmo JSON. O que muda e o conteudo.

Renderizado pelo dashboard de fundos, o da previdencia saia com duas manchetes
(captacao e rentabilidade), tres paineis inteiros de "nao se aplica" - gaps e
exclusivos por CNPJ, e um radar CVM "indisponivel nesta coleta" que a
previdencia nem coleta -, e o grafico de arquitetura aberta publicando o corte
produto proprio x terceiros que o modulo declara sem sentido. Os blocos que a
previdencia suprime continuam suprimidos; o que muda e o que entra no lugar:

- manchetes: a vitrine com a seguradora dos planos, a captacao, a
  rentabilidade em 12 meses e quanto do dinheiro esta em fundo abaixo da
  mediana da categoria (o custo volta sozinho quando a taxa comparavel passar
  do piso de cobertura);
- placar: a seguradora sob o numero de fundos, e Nu e Inter como casas sem
  vitrine publica - elas nao somem, nem no `report`, que nao tem snapshot util
  delas;
- paineis: a seguradora dos planos no lugar da arquitetura aberta, a ressalva
  da gestora registrada no painel de gestoras, e um painel so com o que o
  monitor nao compara, e por que.
"""
from __future__ import annotations

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from shelf_monitor.enrich import KEY_GESTOR_OFICIAL
from shelf_monitor.models import (
    BLOCO_CNPJ,
    BLOCO_COTISTAS,
    BLOCO_PRODUTO_PROPRIO,
    RunReport,
    SnapshotStatus,
)
from shelf_monitor.previdencia.coletores import COLETORES
from shelf_monitor.previdencia.coletores.sem_vitrine import SemVitrineCollector
from shelf_monitor.previdencia.config import (
    MOTIVO_RADAR,
    PLATAFORMAS,
    ConfigPrevidencia,
)
from shelf_monitor.previdencia.eixo import KEY_SEGURADORA
from shelf_monitor.report.aggregate import build_dashboard_data, listing_funds
from shelf_monitor.report.dashboard import (
    Leitura,
    _base,
    _bloco_fontes,
    _comparacao,
    _documento,
    _e,
    _fragmentos,
    _gravar,
    _kpi,
    _kpi_captacao,
    _kpi_custo,
    _kpi_rentabilidade,
    _leitura_custo,
    _leitura_movimento,
    _leitura_rentabilidade,
    _painel,
    _painel_captacao,
    _painel_coleta,
    _painel_rentabilidade,
    _painel_ticket,
    _placar,
    _prazo_do_fluxo,
    _rentabilidade,
    _resumos,
    _tiles,
)
from shelf_monitor.report.digest import _juntar, _share
from shelf_monitor.report.shared import rotulo_da_casa
from shelf_monitor.texto import contagem, data_br, milhar, periodo_br

# Abaixo desta parcela de fundos com gestora, a contagem de gestoras da casa
# nao descreve a vitrine - o Itau Private nao publica a gestora, e os poucos
# nomes que aparecem saem do prefixo do nome comercial.
_COBERTURA_MINIMA_GESTORA = 0.5


def _casas_sem_vitrine() -> dict[str, str]:
    """Casa -> motivo, para as que o modulo sabe nao terem vitrine publica.

    Sai do registro de coletores, nao do status do snapshot: UNAVAILABLE e
    tambem o que uma vitrine que existe devolve quando a coleta falha, e essa
    tem de continuar aparecendo como falha.
    """
    return {
        pid: PLATAFORMAS[pid].notes or "sem vitrine pública de previdência"
        for pid, coletor in COLETORES.items()
        if coletor is SemVitrineCollector
    }


# --- seguradora dos planos ---------------------------------------------------------


def _seguradoras(report: RunReport) -> dict[str, dict]:
    """Casa -> {"total", "sem", "por_seguradora"} sobre os fundos da VITRINE.

    Em previdencia o cliente contrata o plano (PGBL ou VGBL) de uma seguradora e
    escolhe o fundo em que a reserva e aplicada; quem emite o plano e a
    arquitetura aberta que conta aqui. Fundo vendido em planos de mais de uma
    seguradora conta em cada uma (a XP publica a lista); fundo sem seguradora
    publicada entra em "sem".
    """
    por_casa: dict[str, dict] = {}
    for snapshot in report.snapshots:
        if snapshot.status == SnapshotStatus.UNAVAILABLE:
            continue
        vitrine = listing_funds(snapshot)
        if not vitrine:
            continue
        contagem_por_nome: Counter = Counter()
        sem = 0
        for fundo in vitrine:
            nomes = {
                nome.strip()
                for nome in str(fundo.extra.get(KEY_SEGURADORA) or "").split(",")
                if nome.strip()
            }
            contagem_por_nome.update(nomes)
            if not nomes:
                sem += 1
        por_casa[snapshot.platform] = {
            "total": len(vitrine),
            "sem": sem,
            "por_seguradora": sorted(contagem_por_nome.items(), key=lambda kv: (-kv[1], kv[0])),
        }
    return por_casa


def _nomes(info: dict | None) -> list[str]:
    return [nome for nome, _ in (info or {}).get("por_seguradora") or []]


def _sub_vitrine(seguradoras: dict[str, dict]) -> dict[str, str]:
    """A linha de baixo da celula de vitrine no placar: a seguradora dos planos."""
    sub = {}
    for pid, info in seguradoras.items():
        nomes = _nomes(info)
        if not nomes:
            sub[pid] = "seguradora não publicada"
        elif len(nomes) <= 2:
            sub[pid] = _juntar(nomes)
        else:
            sub[pid] = f"{len(nomes)} seguradoras"
    return sub


def _tabela_seguradoras(data: dict, seguradoras: dict[str, dict]) -> str:
    linhas = []
    for p in data.get("platforms") or []:
        info = seguradoras.get(p["platform_id"])
        if not info:
            continue
        itens = list(info["por_seguradora"])
        if info["sem"]:
            itens.append(("não publicada", info["sem"]))
        for i, (nome, n) in enumerate(itens):
            casa = _e(p["label"]) if i == 0 else ""
            parcela = f"{round(100 * n / info['total'])}%"
            linhas.append(
                f"<tr><td>{casa}</td><td>{_e(nome)}</td>"
                f'<td class="num">{milhar(n)}</td><td class="num">{parcela}</td></tr>'
            )
    if not linhas:
        return '<p class="empty">Nenhuma vitrine coletada nesta execução.</p>'
    return (
        '<div class="table-scroll"><table><thead><tr><th>Casa</th><th>Seguradora</th>'
        '<th class="num">Fundos</th><th class="num">% da vitrine</th></tr></thead>'
        f'<tbody>{"".join(linhas)}</tbody></table></div>'
    )


# --- manchetes ---------------------------------------------------------------------


def _com_vitrine(data: dict) -> list[dict]:
    return [
        p for p in data.get("platforms") or []
        if p.get("status") != "unavailable" and p.get("listing_funds")
    ]


def _kpi_vitrine(report: RunReport, data: dict, seguradoras: dict[str, dict]) -> str:
    """Quantos fundos a casa-base exibe, e em planos de quantas seguradoras."""
    base_id, base = _base(data)
    casas = {p["platform_id"]: p for p in _com_vitrine(data)}
    if base_id not in casas:
        return ""
    nomes = _nomes(seguradoras.get(base_id))
    nota = base
    if nomes:
        nota = (
            f"{base}, em planos de {contagem(len(nomes), 'seguradora', 'seguradoras')}: "
            f"{_juntar(nomes)}"
        )
    snapshot = report.snapshot_for(base_id)
    return _kpi(
        "vitrine-section", "Fundos na vitrine", milhar(casas[base_id]["listing_funds"]),
        nota,
        _comparacao(casas[base_id]["listing_funds"],
                    [p for pid, p in casas.items() if pid != base_id],
                    "listing_funds", milhar, maior=True),
        f"coleta de {data_br(snapshot.collected_at)}" if snapshot else "",
    )


def _massas(bloco: dict) -> dict[str, dict]:
    """Casa -> alocacao abaixo da mediana da categoria, com o rotulo da casa."""
    return {
        c["platform_id"]: {"label": c["label"], **(c.get("alocacao_abaixo_da_mediana") or {})}
        for c in bloco.get("casas") or []
        if (c.get("alocacao_abaixo_da_mediana") or {}).get("indice_pl") is not None
    }


def _decimal(valor: float) -> str:
    """"0,48" - com o arredondamento do painel, e nao o do f-string.

    O painel escreve o indice pelo JS (`toLocaleString`), que arredonda o
    decimal que o numero representa: 0.475 vira 0,48. O f-string arredonda o
    binario (0.47499...) e dava 0,47 - a manchete e a tabela do mesmo painel
    diziam numeros diferentes.
    """
    return str(Decimal(repr(valor)).quantize(Decimal("0.01"), ROUND_HALF_UP)).replace(".", ",")


def _kpi_dinheiro(data: dict) -> str:
    """Quanto do PL da vitrine esta em fundo que rende abaixo da propria categoria.

    Sem a ponderacao por cotistas (o cotista do FIE e a seguradora), e esta a
    leitura de onde esta o dinheiro. Compara as casas pelo indice - share do PL
    sobre share de fundos -, porque metade de qualquer universo fica abaixo da
    mediana por construcao e o share do PL sozinho herda isso.
    """
    base_id, base = _base(data)
    rent = _rentabilidade(data)
    if not rent:
        return ""
    bloco, janela = rent
    massas = _massas(bloco)
    if base_id not in massas:
        return ""
    m = massas[base_id]
    return _kpi(
        "perf-section", f"PL abaixo da categoria em {janela['rotulo']}",
        _share(m["share_pl"]),
        f"do PL da {base}, em {_share(m['share_fundos'])} dos fundos: índice "
        f"{_decimal(m['indice_pl'])} (abaixo de 1, o dinheiro evita os piores)",
        _comparacao(m["indice_pl"],
                    [v for pid, v in massas.items() if pid != base_id],
                    "indice_pl", lambda v: f"índice {_decimal(v)}", maior=False),
        periodo_br(janela.get("de"), janela.get("ate")),
    )


# --- leituras ----------------------------------------------------------------------


def _leitura_vitrine(
    data: dict, seguradoras: dict[str, dict], sem_vitrine: dict[str, str]
) -> Leitura | None:
    partes = []
    for p in _com_vitrine(data):
        info = seguradoras.get(p["platform_id"]) or {}
        itens = info.get("por_seguradora") or []
        texto = f"{p['label']} {contagem(p['listing_funds'], 'fundo', 'fundos')}"
        if not itens:
            texto += ", sem seguradora publicada"
        elif len(itens) == 1 and not info.get("sem"):
            texto += f", em planos da {itens[0][0]}"
        else:
            texto += (
                f", em planos de {contagem(len(itens), 'seguradora', 'seguradoras')} ("
                + ", ".join(f"{nome} {milhar(n)}" for nome, n in itens)
                + (f", não publicada {milhar(info['sem'])}" if info.get("sem") else "")
                + ")"
            )
        partes.append(texto)
    if not partes:
        return None
    frase = "Vitrines de previdência: " + "; ".join(partes) + "."
    ausentes = _rotulos(sem_vitrine)
    if ausentes:
        frase += (
            f" {_juntar(ausentes)} não {'tem' if len(ausentes) == 1 else 'têm'} "
            "vitrine pública de previdência."
        )
    return ("vitrine-section", "Vitrines e seguradoras", frase)


def _rotulos(sem_vitrine: dict[str, str]) -> list[str]:
    return [rotulo_da_casa(pid) for pid in sem_vitrine]


def _leitura_fluxo(data: dict) -> Leitura | None:
    """As duas frases do digest: o saldo por casa e de onde vem a diferenca.

    A segunda diz por familia de ativo de onde sai a distancia entre a XP e a
    melhor concorrente - e a frase que explica o numero da manchete.
    """
    flow = data.get("flow") or {}
    prazo = _prazo_do_fluxo(flow)
    if not (prazo and flow.get("leitura")):
        return None
    return ("flow-section", f"Captação em {prazo[0]}", " ".join(flow["leitura"][:2]))


def _leitura_dinheiro(data: dict) -> Leitura | None:
    rent = _rentabilidade(data)
    if not rent:
        return None
    bloco, janela = rent
    ranking = sorted(_massas(bloco).values(), key=lambda m: m["indice_pl"])
    if not ranking:
        return None
    return (
        "perf-section", f"Onde está o dinheiro em {janela['rotulo']}",
        "Parcela do PL da vitrine em fundos que rendem abaixo da mediana da própria "
        f"categoria, {periodo_br(janela.get('de'), janela.get('ate'))}: "
        + _juntar([
            f"{m['label']} {_share(m['share_pl'])} do PL, em {_share(m['share_fundos'])} "
            f"dos fundos (índice {_decimal(m['indice_pl'])})"
            for m in ranking
        ])
        + ". Abaixo de 1, o dinheiro evita os piores da categoria; metade de qualquer "
        "universo fica abaixo da mediana por construção.",
    )


# --- paineis -----------------------------------------------------------------------


# Os blocos suprimidos, na ordem do painel "O que este monitor nao compara".
_LIMITES = (
    (BLOCO_CNPJ, "gaps e exclusividade por CNPJ"),
    (BLOCO_COTISTAS, "ponderação por cotistas"),
    (BLOCO_PRODUTO_PROPRIO, "produto próprio × terceiros"),
)


def _maiuscula(frase: str) -> str:
    """So a primeira letra - `str.capitalize` rebaixaria "CNPJ" e "CVM"."""
    return frase[:1].upper() + frase[1:]


def _cobertura_de_gestora(report: RunReport) -> dict[str, tuple[int, int]]:
    """Casa -> (fundos da vitrine com gestora, fundos da vitrine)."""
    cobertura = {}
    for snapshot in report.snapshots:
        vitrine = listing_funds(snapshot)
        if vitrine:
            com = sum(
                1 for f in vitrine if f.extra.get(KEY_GESTOR_OFICIAL) or f.manager_name
            )
            cobertura[snapshot.platform] = (com, len(vitrine))
    return cobertura


def _resumos_previdencia(
    report: RunReport, data: dict, config: ConfigPrevidencia, sem_vitrine: dict[str, str]
) -> dict[str, str]:
    """As linhas dos paineis: as de fundos, com as que dizem outra coisa aqui."""
    r = _resumos(report, data, config)
    plataformas = data.get("platforms") or []
    com_vitrine = _com_vitrine(data)
    casas = {p["platform_id"] for p in plataformas} | set(sem_vitrine)

    r["vitrine"] = (
        f"{len(com_vitrine)} de {contagem(len(casas), 'casa', 'casas')} com vitrine pública "
        "de previdência: matriz por classe ANBIMA, seguradora dos planos e ticket de entrada."
    )

    cobertura = _cobertura_de_gestora(report)

    def coberta(p: dict) -> bool:
        com, total = cobertura.get(p["platform_id"], (0, 0))
        return bool(total) and com / total >= _COBERTURA_MINIMA_GESTORA

    contadas = [p for p in com_vitrine if coberta(p)]
    if contadas:
        r["gestoras"] = (
            "Gestoras distintas na vitrine, pela gestora registrada: "
            + _juntar([f"{p['label']} {milhar(p.get('distinct_managers') or 0)}" for p in contadas])
            + "."
        )
    else:
        r["gestoras"] = "Nenhuma vitrine publica a gestora dos fundos."
    sem_gestora = [p["label"] for p in com_vitrine if not coberta(p)]
    if contadas and sem_gestora:
        r["gestoras"] = r["gestoras"][:-1] + f"; {_juntar(sem_gestora)} não publica a gestora."

    restritos = (data.get("restricted") or {}).get("total") or 0
    r["restritos"] = (
        f"Inventário de {contagem(restritos, 'fundo', 'fundos')} de público restrito, que "
        f"não {'entra' if restritos == 1 else 'entram'} em nenhuma comparação."
    )

    coletadas = [p for p in plataformas if p["platform_id"] not in sem_vitrine]
    ok = sum(1 for p in coletadas if p.get("status") == "ok")
    partes = [
        f"{ok} de {contagem(len(coletadas), 'vitrine pública', 'vitrines públicas')} "
        "com coleta ok"
    ]
    parciais = sum(1 for p in coletadas if p.get("status") == "partial")
    falhas = sum(1 for p in coletadas if p.get("status") == "unavailable")
    if parciais:
        partes.append(contagem(parciais, "parcial", "parciais"))
    if falhas:
        partes.append(contagem(falhas, "indisponível", "indisponíveis"))
    ausentes = _rotulos(sem_vitrine)
    r["coleta"] = ", ".join(partes) + (
        f"; sem vitrine pública de previdência: {_juntar(ausentes)}." if ausentes else "."
    )

    fora = _juntar([nome for chave, nome in _LIMITES if report.suprime(chave)] + ["radar CVM"])
    r["limites"] = f"{_maiuscula(fora)}: o que não mede nada em previdência, e por quê."
    return r


def _painel_vitrine(resumo: dict[str, str], data: dict, seguradoras: dict[str, dict]) -> str:
    return _painel("vitrine-section", "Vitrine de previdência &mdash; o que o cliente vê", resumo["vitrine"], f"""
    <p class="card-sub">Fundos que a casa lista na página pública de previdência (a API que a
      página chama, ou o HTML servido). Em previdência o cliente contrata um plano (PGBL ou
      VGBL) de uma seguradora e escolhe o fundo em que a reserva é aplicada. O link de cada
      vitrine está em <a href="#fontes-section">Fontes</a>.</p>
    <div class="tiles" id="vitrine-tiles"></div>

    <h3>Matriz casa &times; classe ANBIMA</h3>
    <p class="card-sub">Contagem de fundos da vitrine por classificação ANBIMA oficial (join por
      CNPJ com o registro CVM; a vitrine sem CNPJ, pela categoria resolvida pelo nome). Mais
      escuro = mais fundos.</p>
    <div class="matrix-scroll" id="matrix-vitrine"></div>

    <div class="subgrid" style="margin-top:22px">
      <div>
        <h3>Seguradora dos planos</h3>
        <p class="card-sub">Quem emite o plano em que o fundo é vendido, como a casa publica. É a
          arquitetura aberta da previdência; o corte gestora da casa &times; terceiros não vale
          aqui (ver <a href="#limites-section">O que este monitor não compara</a>).</p>
        {_tabela_seguradoras(data, seguradoras)}
      </div>
      <div>
        <h3>Ticket de entrada por casa</h3>
        <p class="card-sub">Quartis da aplicação mínima que a vitrine mostra &mdash; quartis mostram
          a forma da vitrine; média esconderia.</p>
        <div id="table-costs"></div>
      </div>
    </div>

    <details><summary>Ver a matriz como tabela</summary><div id="table-vitrine"></div></details>
    <div class="coverage" id="vitrine-coverage"></div>""")


def _painel_classes(resumo: dict[str, str]) -> str:
    return _painel("classes-section", "Amplitude por classe de ativo", resumo["classes"], """
    <p class="card-sub">Quantos fundos cada casa <strong>exibe</strong> em cada classe de ativo - a
      leitura mais direta de onde cada vitrine é forte. A classe sai da categoria ANBIMA sem o
      prefixo &ldquo;Previdência&rdquo;: com ele, todo fundo cairia numa barra só.</p>
    <div class="legend" id="legend-classes"></div>
    <div class="chart-scroll" id="chart-classes"></div>
    <details><summary>Ver tabela</summary><div id="table-classes"></div></details>""")


def _painel_gestoras(report: RunReport, resumo: dict[str, str]) -> str:
    motivo = report.suprime(BLOCO_PRODUTO_PROPRIO)
    ressalva = (
        f"""
    <p class="card-sub"><strong>Ressalva da previdência:</strong> {_e(motivo)}. A contagem sai
      menor que o número de estratégias da vitrine: FICs de gestoras diferentes aparecem sob a
      mesma gestora registrada.</p>"""
        if motivo else ""
    )
    return _painel("gestoras-section", "Profundidade: gestoras representadas", resumo["gestoras"], f"""
    <p class="card-sub">Número de gestoras distintas na <strong>vitrine</strong>, pela gestora
      registrada na CVM. Amplitude sem profundidade indica concentração em poucas casas de
      gestão.</p>{ressalva}
    <div class="chart-scroll" id="chart-managers"></div>
    <details><summary>Ver as maiores gestoras de cada vitrine</summary>
      <div id="table-top-managers"></div></details>""")


def _painel_restritos(resumo: dict[str, str]) -> str:
    return _painel("restritos-section", "Fundos de público restrito &mdash; auxiliar", resumo["restritos"], """
    <p class="card-sub">Fundos da vitrine fora do público geral: público profissional declarado
      pela casa, ou &ldquo;exclusivo&rdquo; ou &ldquo;restrito&rdquo; no nome. A marca de exclusivo
      do registro CVM não conta aqui: todo FIE a tem, porque o cotista é a seguradora. Ficam fora
      de toda análise acima; aqui é o inventário deles.</p>
    <div id="table-restricted"></div>""")


def _painel_limites(report: RunReport, resumo: dict[str, str]) -> str:
    """Os blocos do monitor de fundos que a previdencia nao publica, cada um com o motivo.

    Um painel so, no lugar de um "nao se aplica" em cada painel de fundos: o
    leitor ve de uma vez o que nao se compara aqui, e o grupo de desempenho fica
    so com o que mede.
    """
    itens = [
        f"<li><strong>{_e(_maiuscula(nome))}</strong>: {_e(motivo)}.</li>"
        for chave, nome in _LIMITES
        if (motivo := report.suprime(chave))
    ]
    itens.append(f"<li><strong>Radar CVM</strong>: {_e(MOTIVO_RADAR)}.</li>")
    return _painel("limites-section", "O que este monitor não compara", resumo["limites"], f"""
    <p class="card-sub">Blocos do monitor de fundos que, em previdência, mediriam a estrutura da
      seguradora e não a oferta da casa. Saíram da análise &mdash; não viraram zero &mdash; pelos
      motivos abaixo.</p>
    <ul class="leitura">{"".join(itens)}</ul>""")


# --- a pagina ----------------------------------------------------------------------


def render_dashboard_previdencia(config: ConfigPrevidencia, report: RunReport) -> Path:
    """O dashboard da previdencia, em `data/previdencia/reports/dashboard.html`."""
    data = build_dashboard_data(report)
    trecho = _fragmentos(report)
    sem_vitrine = _casas_sem_vitrine()
    seguradoras = _seguradoras(report)
    resumo = _resumos_previdencia(report, data, config, sem_vitrine)
    leituras = (
        _leitura_vitrine(data, seguradoras, sem_vitrine),
        _leitura_fluxo(data),
        _leitura_rentabilidade(data),
        _leitura_dinheiro(data),
        _leitura_custo(data),
        _leitura_movimento(report),
    )
    return _gravar(config, _documento(
        config, report, data,
        manchetes=_tiles(
            _kpi_vitrine(report, data, seguradoras),
            _kpi_captacao(data),
            _kpi_rentabilidade(data),
            _kpi_dinheiro(data),
            _kpi_custo(data),
        ),
        leituras=[item for item in leituras if item],
        placar=_placar(
            report, data,
            sub_vitrine=_sub_vitrine(seguradoras),
            cabeca_vitrine="fundos e seguradora",
            casas_sem_vitrine=sem_vitrine,
        ),
        grupos=[
            ("Captação e desempenho", [
                # Com os dois cortes do saldo suprimidos, "De onde vem o saldo"
                # repetiria a captacao e o % do PL do placar.
                _painel_captacao(resumo, trecho, composicao=not (
                    report.suprime(BLOCO_CNPJ) and report.suprime(BLOCO_PRODUTO_PROPRIO)
                )),
                _painel_rentabilidade(resumo, trecho),
            ]),
            ("Composição das vitrines", [
                _painel_vitrine(resumo, data, seguradoras),
                _painel_classes(resumo),
                _painel_gestoras(report, resumo),
                _painel_ticket(resumo),
                _painel_restritos(resumo),
            ]),
            ("Qualidade dos dados", [
                _painel_coleta(resumo),
                _painel_limites(report, resumo),
                _bloco_fontes(report, config, resumo["fontes"]),
            ]),
        ],
    ))
