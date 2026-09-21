"""Digest de mudancas: o que mudou desde a ultima execucao.

Gera markdown (leitura no repo, diff de git legivel) e HTML com CSS inline,
sem JS e com largura contida - pronto para colar num e-mail.
"""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.models import (
    CvmDiff,
    FundChange,
    FundOffer,
    RunReport,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import format_cnpj
from shelf_monitor.profile import restriction_reason
from shelf_monitor.report.shared import rotulo_da_casa
from shelf_monitor.report.performance import (
    _MAX_EXPOSTOS,
    fundos_abaixo_da_categoria,
)
from shelf_monitor.report.aggregate import (
    build_gap_block,
    listing_funds,
    ranquear_gaps,
    retail_funds,
)

_FIELD_LABELS = {
    "name": "nome",
    "min_initial": "aplicacao minima",
    "admin_fee_pct": "taxa de administracao",
    "performance_fee_pct": "taxa de performance",
    "closed_for_investment": "fechado para captacao",
    "redemption_quotation_days": "cotizacao do resgate (dias)",
    "redemption_payment_days": "liquidacao do resgate (dias)",
    "grace_days": "carencia (dias)",
}

_MAX_LISTED = 25

# Gaps listados no e-mail: o topo cabe numa tela; o resto mora no dashboard e
# na planilha.
_GAP_LISTED = 10
_GAP_CATEGORY_ROWS = 8


def _gap_footnotes(gaps: dict) -> list[str]:
    """Proveniencia do bloco de gaps - as frases sao as mesmas em md e html."""
    notes = [
        "Comparacao vitrine x vitrine por CNPJ - o catalogo Open Finance nao "
        "conta em nenhum dos lados."
    ]
    if gaps.get("baseline_warning"):
        notes.append(f"Atencao: {gaps['baseline_warning']}.")
    if gaps.get("excluded"):
        out = "; ".join(f"{e['label']} ({e['reason']})" for e in gaps["excluded"])
        notes.append(f"Fora do universo: {out}.")
    return notes


_platform_label = rotulo_da_casa


# Vitrine x catalogo, e por que o digest inteiro fala em vitrine.
#
# `len(snapshot.funds)` conta tudo que a coleta trouxe, e para as casas que
# publicam Open Finance isso e o que elas ADMINISTRAM, nao o que exibem. Medido
# em 2026-09-20, a tabela de sumario saia assim: XP 781, BTG 10.073, Itau 2.179.
# Lido como prateleira - que e como o titulo "Fundos" pedia para ser lido - o
# BTG tinha treze vezes a XP. Na vitrine sao 634 contra 664, ou seja, empate.
# Os 9.413 de diferenca sao fundos que nenhum cliente do BTG ve numa tela.
#
# O catalogo continua na tabela, em coluna propria e com nome proprio: ele
# responde outra pergunta (o tamanho da casa como administradora) e some-lo a
# vitrine nao responde nenhuma.


def _na_vitrine(fund: FundOffer) -> bool:
    """O cliente ve esse fundo na tela da casa - a pertinencia do digest.

    Mesma regra do resto do monitor: varejo, e vindo do canal da propria casa.
    """
    return fund.on_listing and restriction_reason(fund) is None


def _tamanhos(snapshot: ShelfSnapshot | None) -> tuple[int, int]:
    """(vitrine, catalogo de varejo) da casa nesta coleta."""
    if snapshot is None:
        return 0, 0
    return len(listing_funds(snapshot)), len(retail_funds(snapshot))


def _frase_de_tamanho(snapshot: ShelfSnapshot | None) -> str:
    """"634 fundos na vitrine (6.618 no catalogo)" - com o catalogo so quando difere.

    Casa sem vitrine alcancavel nao vira "0 fundos na vitrine": zero e um numero
    e diz que a prateleira esta vazia, quando o que houve foi o monitor nao
    alcancar a listagem. O motivo entra no lugar do numero.
    """
    vitrine, catalogo = _tamanhos(snapshot)
    if not vitrine:
        return f"sem vitrine publica coletada; {catalogo} fundos no catalogo Open Finance"
    if catalogo > vitrine:
        return f"{vitrine} fundos na vitrine ({catalogo} no catalogo)"
    return f"{vitrine} fundos na vitrine"


def _mudancas_de_vitrine(
    diff: ShelfDiff, snapshot: ShelfSnapshot | None
) -> tuple[list[FundOffer], list[FundOffer], list[FundChange], int]:
    """(entradas, saidas, alteracoes) na vitrine, e quantas ficaram no catalogo.

    O diff roda sobre tudo que foi coletado, e tem de continuar rodando: fundo
    que aparece so no Open Finance de uma casa e sinal de registro novo. Mas no
    digest ele nao pode entrar em "entraram na prateleira" - num dia de churn do
    catalogo do BTG, a secao dele afogaria as mudancas reais de todas as casas.
    Entao a vitrine vai listada e o catalogo vai contado, em uma linha.

    `changed` so carrega identity e nome, entao a pertinencia dele vem do
    snapshot atual - as identidades que estao na vitrine de hoje.
    """
    vitrine_ids = {f.identity for f in listing_funds(snapshot)} if snapshot else set()
    entradas = [f for f in diff.added if _na_vitrine(f)]
    saidas = [f for f in diff.removed if _na_vitrine(f)]
    alteracoes = [c for c in diff.changed if c.identity in vitrine_ids]
    so_catalogo = (
        (len(diff.added) - len(entradas))
        + (len(diff.removed) - len(saidas))
        + (len(diff.changed) - len(alteracoes))
    )
    return entradas, saidas, alteracoes, so_catalogo


def _format_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "sim" if value else "nao"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}".replace(",", ".")
        return f"{value:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return str(value)


def _summary_counts(report: RunReport) -> tuple[int, int, int, int]:
    """(entradas, saidas, alteracoes) de vitrine, e o que mudou so no catalogo."""
    added = removed = changed = catalogo = 0
    for diff in report.shelf_diffs:
        if diff.is_first_run:
            continue
        entradas, saidas, alteracoes, so_catalogo = _mudancas_de_vitrine(
            diff, report.snapshot_for(diff.platform)
        )
        added += len(entradas)
        removed += len(saidas)
        changed += len(alteracoes)
        catalogo += so_catalogo
    return added, removed, changed, catalogo


def _summary_sentence(report: RunReport) -> tuple[str, bool]:
    """Frase de abertura do digest, e se ela e um alerta.

    Distingue tres silencios que se parecem no numero mas nao no significado:
    coleta que falhou inteira, primeira coleta, e prateleira estavel.
    """
    added, removed, changed, catalogo = _summary_counts(report)
    collected = [s for s in report.snapshots if s.status != SnapshotStatus.UNAVAILABLE]

    if report.snapshots and not collected:
        return (
            "Nenhuma fonte foi acessivel nesta execucao: nada foi coletado e nenhuma "
            "comparacao pode ser feita. Cheque o acesso as fontes com "
            "`python -m shelf_monitor doctor`.",
            True,
        )

    first_run = all(d.is_first_run for d in report.shelf_diffs) if report.shelf_diffs else True
    if first_run:
        vitrine = sum(len(listing_funds(s)) for s in collected)
        catalogo_total = sum(len(retail_funds(s)) for s in collected)
        return (
            f"Primeira coleta: {vitrine} fundos de vitrine em {len(collected)} "
            f"plataforma(s) viram a linha de base ({catalogo_total} no catalogo de "
            "varejo). As comparacoes comecam na proxima execucao.",
            False,
        )

    cauda = (
        f" Outras {catalogo} mudancas ficaram so no catalogo Open Finance, fora da vitrine."
        if catalogo
        else ""
    )

    if not (added or removed or changed):
        return (
            "Nenhuma mudanca nas vitrines monitoradas desde a ultima coleta." + cauda,
            False,
        )

    return (
        f"{added} fundos entraram, {removed} sairam e {changed} tiveram condicoes "
        "alteradas nas vitrines monitoradas." + cauda,
        False,
    )


# --- markdown ----------------------------------------------------------------


def _md_shelf_section(diff: ShelfDiff, snapshot: ShelfSnapshot | None) -> list[str]:
    lines = [f"### {_platform_label(diff.platform)}", ""]

    if snapshot and snapshot.status == SnapshotStatus.UNAVAILABLE:
        lines += [f"Coleta indisponivel: {snapshot.error or 'motivo nao informado'}.", ""]
        return lines

    tamanho = _frase_de_tamanho(snapshot)
    if diff.is_first_run:
        lines += [
            f"Primeira coleta: {tamanho}, como linha de base. "
            "Comparacoes comecam na proxima execucao.",
            "",
        ]
        return lines

    if diff.suspect:
        lines += [
            f"**Coleta suspeita** ({diff.suspect_reason}). Saidas nao reportadas para "
            "evitar falso churn.",
            "",
        ]

    entradas, saidas, alteracoes, so_catalogo = _mudancas_de_vitrine(diff, snapshot)
    cauda = (
        f" Mais {so_catalogo} mudancas so no catalogo Open Finance, fora da vitrine."
        if so_catalogo
        else ""
    )

    if not (entradas or saidas or alteracoes):
        lines += [f"Sem mudancas na vitrine: {tamanho}.{cauda}", ""]
        return lines

    lines += [
        f"{len(entradas)} entradas, {len(saidas)} saidas, "
        f"{len(alteracoes)} alteracoes na vitrine - {tamanho}.{cauda}",
        "",
    ]

    if entradas:
        lines.append("**Entraram na vitrine**")
        lines.append("")
        for fund in entradas[:_MAX_LISTED]:
            cnpj = f" ({format_cnpj(fund.cnpj)})" if fund.cnpj else ""
            manager = f" - {fund.manager_name}" if fund.manager_name else ""
            lines.append(f"- {fund.name}{cnpj}{manager}")
        if len(entradas) > _MAX_LISTED:
            lines.append(f"- ... e mais {len(entradas) - _MAX_LISTED} fundos")
        lines.append("")

    if saidas:
        lines.append("**Sairam da vitrine**")
        lines.append("")
        for fund in saidas[:_MAX_LISTED]:
            cnpj = f" ({format_cnpj(fund.cnpj)})" if fund.cnpj else ""
            lines.append(f"- {fund.name}{cnpj}")
        if len(saidas) > _MAX_LISTED:
            lines.append(f"- ... e mais {len(saidas) - _MAX_LISTED} fundos")
        lines.append("")

    if alteracoes:
        lines.append("**Mudancas de condicoes**")
        lines.append("")
        for change in alteracoes[:_MAX_LISTED]:
            details = ", ".join(
                f"{_FIELD_LABELS.get(c.field, c.field)}: "
                f"{_format_value(c.old)} -> {_format_value(c.new)}"
                for c in change.changes
            )
            lines.append(f"- {change.name} - {details}")
        if len(alteracoes) > _MAX_LISTED:
            lines.append(f"- ... e mais {len(alteracoes) - _MAX_LISTED} fundos")
        lines.append("")

    return lines


def _md_cvm_section(diff: CvmDiff | None, report: RunReport) -> list[str]:
    lines = ["## Radar CVM", ""]

    snapshot = report.cvm_snapshot
    if not snapshot or snapshot.status == SnapshotStatus.UNAVAILABLE:
        error = snapshot.error if snapshot else "extrato nao coletado"
        lines += [f"Extrato da CVM indisponivel: {error}.", ""]
        return lines

    by_house = snapshot.by_house()
    lines.append(
        f"{len(snapshot.funds)} fundos na watchlist, "
        f"{sum(1 for f in snapshot.funds if f.is_pre_operational)} em fase pre-operacional."
    )
    lines.append("")
    lines.append("| Casa | Registrados | Pre-operacionais |")
    lines.append("|---|---:|---:|")
    for house, funds in sorted(by_house.items()):
        pre_op = sum(1 for f in funds if f.is_pre_operational)
        lines.append(f"| {_platform_label(house)} | {len(funds)} | {pre_op} |")
    lines.append("")

    if not diff:
        return lines

    if diff.is_first_run:
        pre_op = diff.new_registrations
        if pre_op:
            lines += ["**Fundos registrados e ainda nao lancados**", ""]
            for fund in pre_op[:_MAX_LISTED]:
                lines.append(
                    f"- [{_platform_label(fund.house)}] {fund.name} "
                    f"({format_cnpj(fund.cnpj)}) - registrado em "
                    f"{fund.registered_at or 'data nao informada'}"
                )
            if len(pre_op) > _MAX_LISTED:
                lines.append(f"- ... e mais {len(pre_op) - _MAX_LISTED} fundos")
            lines.append("")
        return lines

    if diff.new_registrations:
        lines += ["**Novos registros na CVM**", ""]
        for fund in diff.new_registrations[:_MAX_LISTED]:
            status = " (pre-operacional)" if fund.is_pre_operational else ""
            lines.append(
                f"- [{_platform_label(fund.house)}] {fund.name} "
                f"({format_cnpj(fund.cnpj)}) - gestor {fund.manager_name or '-'}{status}"
            )
        if len(diff.new_registrations) > _MAX_LISTED:
            lines.append(f"- ... e mais {len(diff.new_registrations) - _MAX_LISTED}")
        lines.append("")

    launches = diff.launches
    if launches:
        lines += ["**Fundos que entraram em operacao**", ""]
        for change in launches[:_MAX_LISTED]:
            lines.append(
                f"- [{_platform_label(change.fund.house)}] {change.fund.name} "
                f"({format_cnpj(change.fund.cnpj)})"
            )
        lines.append("")

    others = [c for c in diff.situation_changes if not c.is_launch]
    if others:
        lines += ["**Outras mudancas de situacao**", ""]
        for change in others[:_MAX_LISTED]:
            lines.append(
                f"- [{_platform_label(change.fund.house)}] {change.fund.name}: "
                f"{change.old_situation or '-'} -> {change.new_situation or '-'}"
            )
        lines.append("")

    if diff.cancelled:
        lines += ["**Sairam do cadastro**", ""]
        for fund in diff.cancelled[:_MAX_LISTED]:
            lines.append(f"- [{_platform_label(fund.house)}] {fund.name}")
        lines.append("")

    return lines


def _md_para_html(texto: str) -> str:
    """Converte o `**negrito**` de uma frase curta, escapando o resto.

    Existe porque as frases de veredito sao escritas uma vez, em markdown, e
    saem nos dois formatos: duplicar o texto para ter uma versao com <strong>
    seria a mesma divergencia que o resto desta revisao veio apagar.
    """
    import re

    partes = re.split(r"\*\*(.+?)\*\*", texto)
    return "".join(
        f"<strong>{_esc(parte)}</strong>" if i % 2 else _esc(parte)
        for i, parte in enumerate(partes)
    )


def _frase_do_ticket(report: RunReport) -> str | None:
    """O veredito do ticket de entrada, ou None quando nao ha o que concluir.

    Calculado em `aggregate.leitura_do_ticket` para as quatro superficies
    dizerem a mesma coisa. A distribuicao de aplicacao minima so aparecia no
    dashboard, como barras sem conclusao - e e onde mora um dos achados mais
    diretos da coleta.
    """
    from shelf_monitor.report.aggregate import leitura_do_ticket, summarize_platform

    exclusive = _exclusive_por_casa(report)
    resumos = [
        summarize_platform(s, exclusive.get(s.platform, set())).to_dict()
        for s in report.snapshots
    ]
    leitura = leitura_do_ticket(resumos)
    if not leitura:
        return None
    teto = f"R$ {leitura['teto']:,.0f}".replace(",", ".")
    if leitura["baseline_varejo"] == 0:
        return (
            f"**A vitrine da {leitura['baseline_label']} nao tem nenhum fundo "
            f"abaixo de {teto}.** A melhor concorrente nessa faixa, "
            f"{leitura['melhor_label']}, tem {leitura['melhor_varejo']}. Nao e "
            "diferenca de composicao: e a ausencia de uma porta de entrada de "
            "varejo."
        )
    return (
        f"{leitura['baseline_label']}: {leitura['baseline_varejo']} fundos "
        f"abaixo de {teto}, contra {leitura['melhor_varejo']} da "
        f"{leitura['melhor_label']}."
    )


def _exclusive_por_casa(report: RunReport) -> dict:
    from shelf_monitor.report.aggregate import _exclusive_cnpjs

    return _exclusive_cnpjs(report.snapshots)


def _gaps_ranqueados(report: RunReport) -> dict:
    """Bloco de gaps com a lista ja fundida com a captacao e o esforco.

    O digest listava os dez primeiros gaps por presenca e PL, e mais abaixo
    repetia uma segunda lista ("Oportunidade de grade") com outros criterios.
    Sao a mesma pergunta; agora a ordem e uma so.
    """
    gaps = build_gap_block(report.snapshots)
    return {**gaps, "funds": ranquear_gaps(
        gaps.get("funds") or [], report.retail_flow, report.grade_opportunities
    )}


def _md_gaps_section(gaps: dict) -> list[str]:
    lines = ["## Gaps de prateleira da XP", ""]
    if not gaps.get("available"):
        lines += [f"Analise suprimida nesta execucao: {gaps.get('reason')}.", ""]
        return lines

    if not gaps["total_gaps"]:
        lines += [
            "Nenhum gap: toda vitrine concorrente coletada esta contida na "
            "vitrine da XP.",
            "",
        ]
    else:
        lines += [
            f"{gaps['total_gaps']} fundos de varejo estao em vitrines "
            f"concorrentes e fora da vitrine da XP "
            f"({gaps['multi_house_gaps']} em 2 ou mais casas). "
            "Ordenados pelo esforco de incluir e, dentro de cada faixa, pela "
            "captacao que estao levando no mercado. Produto proprio do "
            "concorrente nao se transfere e vai para o fim, sem sair da lista.",
            "",
        ]
        for fund in gaps["funds"][:_GAP_LISTED]:
            captacao = fund.get("captacao_liquida")
            fluxo = f" - {_mm(captacao)} de captacao" if captacao is not None else ""
            esforco = f" - {fund['esforco']}" if fund.get("esforco") else ""
            lines.append(
                f"- {fund['name']} ({fund['cnpj_display']}) - "
                f"{fund['category']} - casas: {fund['houses_label']}{fluxo}{esforco}"
            )
        remaining = gaps["total_gaps"] - min(_GAP_LISTED, len(gaps["funds"]))
        if remaining > 0:
            lines.append(f"- ... e mais {remaining} (dashboard e planilha)")
        lines.append("")

        categories = gaps["categories"][:_GAP_CATEGORY_ROWS]
        if categories:
            lines.append("| Categoria | XP | Melhor concorrente | Gaps |")
            lines.append("|---|---:|---|---:|")
            for entry in categories:
                best = entry["best_competitor"]
                best_label = f"{best['label']} ({best['count']})" if best else "-"
                lines.append(
                    f"| {entry['category']} | {entry['xp']} | "
                    f"{best_label} | {entry['gap_count']} |"
                )
            lines.append("")

    for note in _gap_footnotes(gaps):
        lines.append(f"_{note}_")
    lines.append("")
    return lines


def _pct(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:.2f}%"


def _pp(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:+.2f}pp"


def _share(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:.0%}"


def _indice(massa: dict) -> str:
    """Share do PL sobre share dos fundos. Abaixo de 1, o dinheiro evita os piores."""
    valor = massa.get("indice_pl")
    return "-" if valor is None else f"{valor:.2f}"


def _contagem(valor: int) -> str:
    """Numero absoluto. `_inteiro` poe sinal, porque serve a variacao."""
    return f"{valor:,}".replace(",", ".")


def _md_performance_section(report: RunReport) -> list[str]:
    """Rentabilidade ponderada e custo relativo, na leitura curta do digest.

    A manchete nao e o retorno medio: e o gap entre a ponderada por PL e a
    ponderada por cotistas, mais quanto do dinheiro e da gente esta em fundo
    que rende abaixo da propria categoria. Sao as duas linhas que nenhuma outra
    secao responde.
    """
    performance = report.performance
    if not (performance and performance.get("available")):
        return []

    periodo = performance["periodo"]
    lines = [
        "## Rentabilidade da vitrine",
        "",
        f"Cota patrimonial do informe diario, {periodo['de']} a {periodo['ate']} "
        f"({periodo['pregoes']} pregoes), contra a mediana da mesma categoria no "
        "universo oficial. E o retorno dos fundos que a casa exibe, com o peso que "
        "eles tem no **mercado** - nao o retorno dos clientes dela.",
        "",
        "| Casa | Mediana dos fundos | Ponderada por PL | Ponderada por cotistas | PL menos cotistas (pp) | Abaixo da mediana da categoria |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for casa in performance["casas"]:
        massa = casa["alocacao_abaixo_da_mediana"]
        lines.append(
            f"| {casa['label']} | {_pct(casa['mediana'])} | {_pct(casa['por_pl'])} | "
            f"{_pct(casa['por_cotista'])} | {_pp(casa['gap'])} | "
            f"{massa['fundos']} de {massa['de']} ({_share(massa['share_fundos'])}) "
            f"carregam {_share(massa['share_pl'])} do PL - indice {_indice(massa)} |"
        )
    lines.append("")
    lines.append(
        "Gap positivo: o dinheiro grande esta nos fundos melhores e o varejo nos "
        "piores. Negativo: o inverso. O sinal do gap **muda conforme a janela** - "
        "ele descreve que categorias performaram no periodo, nao a base de clientes."
    )
    lines.append("")
    lines.append(
        "Indice abaixo de 1 significa que o dinheiro EVITA os fundos piores da "
        "categoria. Cerca de metade de qualquer universo fica abaixo da mediana por "
        "construcao, entao a contagem de fundos sozinha nao e sinal de nada."
    )
    lines.append("")

    # Os fundos de muita gente que rendem abaixo dos pares - o recorte acionavel.
    abaixo = fundos_abaixo_da_categoria(performance)
    if abaixo:
        lines.append("**Fundos de muita gente rendendo abaixo da categoria**")
        lines.append("")
        for fundo in abaixo[:_MAX_EXPOSTOS]:
            lines.append(
                f"- {_contagem(fundo['cotistas'])} cotistas - {fundo['nome']} "
                f"({', '.join(fundo['casas'])}): {_pct(fundo['retorno'])} contra "
                f"{_pct(fundo['mediana_categoria'])} da categoria "
                f"({_pp(fundo['excesso'])})"
            )
        lines.append("")

    custo = report.custo
    if custo and custo.get("available"):
        lines.append("**Custo relativo a categoria**")
        lines.append("")
        lines.append(
            "Indice mediano, nao taxa mediana: a taxa bruta entre casas mede o mix "
            "da prateleira, nao o preco dos produtos."
        )
        lines.append("")
        lines.append("| Casa | Indice mediano | Caros | Baratos | Resgate ate D+1 |")
        lines.append("|---|---:|---:|---:|---:|")
        for casa in custo["casas"]:
            indice = casa["indice_mediano"]
            lines.append(
                f"| {casa['label']} | "
                f"{'-' if indice is None else f'{indice:.2f}x'} | "
                f"{_share(casa['share_caro'])} | {_share(casa['share_barato'])} | "
                f"{_share(casa['share_liquidez_imediata'])} |"
            )
        lines.append("")

    for excluido in performance.get("excluded", []):
        lines.append(f"> Fora: {excluido['label']} - {excluido['reason']}")
    if performance.get("excluded"):
        lines.append("")
    return lines


def _indice_mediano(casa: dict) -> str:
    """"1,65x", ou "-" quando a casa nao tem amostra comparavel."""
    indice = casa.get("indice_mediano")
    return "-" if indice is None else f"{indice:.2f}x"


def _html_performance_section(report: RunReport) -> str:
    performance = report.performance
    if not (performance and performance.get("available")):
        return ""

    periodo = performance["periodo"]
    linhas = ""
    for casa in performance["casas"]:
        massa = casa["alocacao_abaixo_da_mediana"]
        cor = "#0ca30c" if (casa["gap"] or 0) > 0 else "#d03b3b"
        linhas += (
            f'<tr><td style="{_TD}">{_esc(casa["label"])}</td>'
            f'<td style="{_TD}text-align:right;">{_pct(casa["mediana"])}</td>'
            f'<td style="{_TD}text-align:right;">{_pct(casa["por_pl"])}</td>'
            f'<td style="{_TD}text-align:right;"><strong>{_pct(casa["por_cotista"])}</strong></td>'
            f'<td style="{_TD}text-align:right;color:{cor};">{_pp(casa["gap"])}</td>'
            f'<td style="{_TD}">{massa["fundos"]} de {massa["de"]} '
            f'({_share(massa["share_fundos"])}) carregam '
            f'{_share(massa["share_pl"])} do PL &mdash; indice {_indice(massa)}</td></tr>'
        )

    fora = "".join(
        f'<p style="{_P}font-size:12.5px;color:#898781;">Fora: {_esc(e["label"])} &mdash; {_esc(e["reason"])}</p>'
        for e in performance.get("excluded", [])
    )

    # O recorte acionavel do bloco. Ele existia so no markdown - quem recebia o
    # digest por e-mail via as tres medias da vitrine e nenhum fundo para agir.
    expostos = fundos_abaixo_da_categoria(performance)
    bloco_expostos = ""
    if expostos:
        itens = "".join(
            f"<li>{_contagem(f['cotistas'])} cotistas &mdash; {_esc(f['nome'])} "
            f"({_esc(', '.join(f['casas']))}): {_pct(f['retorno'])} contra "
            f"{_pct(f['mediana_categoria'])} da categoria ({_pp(f['excesso'])})</li>"
            for f in expostos[:_MAX_EXPOSTOS]
        )
        bloco_expostos = (
            f'<h3 style="{_H2}font-size:14px;">Fundos de muita gente rendendo '
            f"abaixo da categoria</h3>"
            f'<ul style="{_P}padding-left:18px;">{itens}</ul>'
        )

    # Custo relativo tambem so saia no markdown.
    custo = report.custo
    bloco_custo = ""
    if custo and custo.get("available"):
        linhas_custo = "".join(
            f'<tr><td style="{_TD}">{_esc(casa["label"])}</td>'
            f'<td style="{_TD}text-align:right;">{_indice_mediano(casa)}</td>'
            f'<td style="{_TD}text-align:right;">{_share(casa["share_caro"])}</td>'
            f'<td style="{_TD}text-align:right;">{_share(casa["share_barato"])}</td>'
            f'<td style="{_TD}text-align:right;">{_share(casa["share_liquidez_imediata"])}</td></tr>'
            for casa in custo["casas"]
        )
        bloco_custo = f"""
  <h3 style="{_H2}font-size:14px;">Custo relativo a categoria</h3>
  <p style="{_P}font-size:12.5px;color:#898781;">Indice mediano, nao taxa mediana: a taxa
    bruta entre casas mede o mix da prateleira, nao o preco dos produtos.</p>
  <table style="{_TABLE}"><thead><tr>
    <th style="{_TH}">Casa</th>
    <th style="{_TH}text-align:right;">Indice mediano</th>
    <th style="{_TH}text-align:right;">Caros</th>
    <th style="{_TH}text-align:right;">Baratos</th>
    <th style="{_TH}text-align:right;">Resgate ate D+1</th>
  </tr></thead><tbody>{linhas_custo}</tbody></table>"""

    return f"""
  <h2 style="{_H2}">Rentabilidade da vitrine</h2>
  <p style="{_P}">Cota patrimonial do informe diario, {_esc(periodo['de'])} a
    {_esc(periodo['ate'])} ({periodo['pregoes']} pregoes), contra a mediana da mesma
    categoria no universo oficial. E o retorno dos fundos que a casa exibe, com o peso
    que eles tem no <strong>mercado</strong> &mdash; nao o retorno dos clientes dela.</p>
  <table style="{_TABLE}"><thead><tr>
    <th style="{_TH}">Casa</th>
    <th style="{_TH}text-align:right;">Mediana dos fundos</th>
    <th style="{_TH}text-align:right;">Ponderada por PL</th>
    <th style="{_TH}text-align:right;">Ponderada por cotistas</th>
    <th style="{_TH}text-align:right;">Gap</th>
    <th style="{_TH}">Abaixo da mediana da categoria</th>
  </tr></thead><tbody>{linhas}</tbody></table>
  <p style="{_P}font-size:12.5px;color:#898781;">Gap positivo: o dinheiro grande esta nos fundos melhores e o
    varejo nos piores. Negativo: o inverso.</p>
  {bloco_expostos}
  {bloco_custo}
  {fora}
"""


def render_markdown(report: RunReport) -> str:
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")

    lines = [
        "# Monitor de Prateleiras de Fundos",
        "",
        f"Concorrentes da XP - execucao de {generated}",
        "",
        "## Sumario",
        "",
    ]

    sentence, is_alert = _summary_sentence(report)
    lines.append(f"**{sentence}**" if is_alert else sentence)
    lines.append("")

    lines.append(
        "| Plataforma | Vitrine | Catalogo | Status | Entradas | Saidas | Alteracoes |"
    )
    lines.append("|---|---:|---:|---|---:|---:|---:|")
    tem_catalogo_maior = False
    for snapshot in report.snapshots:
        diff = report.diff_for(snapshot.platform)
        vitrine, catalogo = _tamanhos(snapshot)
        tem_catalogo_maior = tem_catalogo_maior or catalogo > vitrine
        if diff and not diff.is_first_run:
            entradas, saidas, alteracoes, _ = _mudancas_de_vitrine(diff, snapshot)
        else:
            entradas = saidas = alteracoes = []
        lines.append(
            f"| {_platform_label(snapshot.platform)} | "
            f"{vitrine if vitrine else 'sem vitrine'} | {catalogo} | "
            f"{snapshot.status.value} | "
            f"{len(entradas)} | {len(saidas)} | {len(alteracoes)} |"
        )
    lines.append("")
    if tem_catalogo_maior:
        lines.append(
            "_Vitrine: fundos de varejo na listagem da propria casa - o que o cliente "
            "ve, e a unica coluna comparavel entre casas. Catalogo: o que a casa "
            "administra, somando o Open Finance. Entradas, saidas e alteracoes contam "
            "a vitrine._"
        )
        lines.append("")

    # O insight-manchete vem antes do radar: e a leitura XP-centrica da coleta.
    lines += _md_gaps_section(_gaps_ranqueados(report))

    ticket = _frase_do_ticket(report)
    if ticket:
        lines += ["## Ticket de entrada", "", ticket, ""]

    lines += _md_flow_section(report)
    lines += _md_performance_section(report)

    lines += _md_cvm_section(report.cvm_diff, report)

    lines += ["## Vitrines", ""]
    for diff in report.shelf_diffs:
        lines += _md_shelf_section(diff, report.snapshot_for(diff.platform))

    unavailable = [s for s in report.snapshots if s.status == SnapshotStatus.UNAVAILABLE]
    if unavailable:
        lines += ["## Fontes indisponiveis", ""]
        for snapshot in unavailable:
            lines.append(
                f"- {_platform_label(snapshot.platform)}: "
                f"{snapshot.error or 'motivo nao informado'}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --- captacao ----------------------------------------------------------------

# Quantos fundos listar em cada recorte do digest. O digest e leitura de 2
# minutos: a lista inteira mora na planilha.
_FLOW_LISTED = 5


def _mm(valor: float) -> str:
    """Reais em milhoes com sinal - a unidade em que fluxo de fundo se le."""
    return f"{valor / 1_000_000:+,.1f} MM".replace(",", "X").replace(".", ",").replace("X", ".")


def _inteiro(valor: int) -> str:
    """Inteiro com separador de milhar brasileiro.

    Existe para que o `.replace` fique preso ao numero: aplicado sobre uma
    f-string concatenada, ele trocava as virgulas da frase inteira por pontos.
    """
    return f"{valor:+,}".replace(",", ".")


def _competencia(chave: str) -> str:
    return f"{chave[4:]}/{chave[:4]}"


def _md_flow_section(report: RunReport) -> list[str]:
    flow = report.retail_flow
    if not (flow and flow.get("available")):
        return []

    periodo = flow["periodo"]
    lines = [
        "## Captacao dos fundos de vitrine",
        "",
        f"Informe diario da CVM, {periodo['de']} a {periodo['ate']}. "
        "A CVM publica fluxo por **fundo**, nunca por distribuidor: os numeros "
        "por casa sao o fluxo dos fundos que ela exibe, no mercado inteiro.",
        "",
        "| Casa | Fundos com informe | Captacao no mercado | So nesta vitrine |",
        "|---|---:|---:|---:|",
    ]
    for casa in flow["casas"]:
        lines.append(
            f"| {casa['label']} | {casa['com_informe']} de {casa['vitrine']} "
            f"({casa['cobertura']:.0%}) | {_mm(casa['captacao_liquida'])} | "
            f"{casa['exclusivos']} fundos, {_mm(casa['captacao_exclusivos'])} |"
        )
    lines.append("")

    competencias = flow.get("competencias") or []
    if competencias:
        lines += [
            "**Tendencia** - media diaria de captacao por competencia. A media "
            "diaria existe porque o mes corrente tem menos pregoes que os "
            "fechados; comparar totais seria comparar calendarios.",
            "",
            "| Casa | " + " | ".join(_competencia(c) for c in competencias) + " |",
            "|---|" + "---:|" * len(competencias),
        ]
        for platform_id, serie in (flow.get("serie_por_casa") or {}).items():
            por_competencia = {m["competencia"]: m for m in serie}
            celulas = []
            for competencia in competencias:
                mes = por_competencia.get(competencia)
                celulas.append(_mm(mes["media_diaria"]) + "/dia" if mes else "-")
            lines.append(f"| {_platform_label(platform_id)} | " + " | ".join(celulas) + " |")
        lines.append("")

    resumo = flow.get("tendencias") or {}
    rotulos = flow.get("tendencia_labels") or {}
    if resumo:
        lines += ["**Fundos de varejo por tendencia**", ""]
        for chave, quantidade in resumo.items():
            descricao = rotulos.get(chave)
            sufixo = f" - {descricao}" if descricao else ""
            lines.append(f"- **{quantidade}** {chave.replace('_', ' ')}{sufixo}")
        lines.append("")

    for titulo, chave in (
        ("Maiores captadores de varejo", "top_captacao"),
        ("Maiores perdas de varejo", "top_resgate"),
        ("Acelerando - captando mais por dia que antes", "acelerando"),
        ("Viraram saida - captavam e passaram a perder", "virou_saida"),
    ):
        fundos = flow.get(chave) or []
        if not fundos:
            continue
        lines += [f"**{titulo}**", ""]
        for fundo in fundos[:_FLOW_LISTED]:
            casas = ", ".join(fundo["vitrines"])
            cotistas = _inteiro(fundo["variacao_cotistas"])
            lines.append(
                f"- {fundo['nome']} - {_mm(fundo['captacao_liquida'])}, "
                f"{cotistas} cotistas - {casas}"
            )
        lines.append("")

    if flow.get("total_estruturais"):
        lines += [
            f"_{flow['total_estruturais']} fundos com movimento estrutural "
            "(fluxo maior que o proprio patrimonio, ou fundo esvaziado) ficam "
            "fora de todas as somas: e dinheiro trocando de veiculo, nao demanda._",
            "",
        ]

    grade = report.grade_opportunities
    if grade and grade.get("available"):
        lines += [
            "### Oportunidade de grade",
            "",
            f"{grade['total']} fundos captam varejo fora da vitrine da XP, mas "
            f"{grade['total_produto_do_concorrente']} sao produto proprio da casa "
            f"que os exibe e nao se transferem. Restam **{grade['total_transferiveis']} "
            f"fundos, {_mm(grade['captacao_transferivel'])}** e "
            f"{_inteiro(grade['cotistas_transferivel'])} cotistas - "
            f"{grade['total_operacionais']} de gestora que a XP ja distribui, "
            f"{grade['total_comerciais']} exigem abrir gestora.",
            "",
        ]
        for fundo in (grade.get("mais_varejo") or [])[:_FLOW_LISTED]:
            cotistas = _inteiro(fundo["variacao_cotistas"])
            esforco = (
                "gestora ja distribuida" if fundo["gestora_ja_distribuida"]
                else "abrir gestora"
            )
            lines.append(
                f"- {fundo['nome']} - {_mm(fundo['captacao_liquida'])}, "
                f"{cotistas} cotistas - {esforco}"
            )
        lines.append("")

    return lines


def _html_flow_section(report: RunReport) -> str:
    flow = report.retail_flow
    if not (flow and flow.get("available")):
        return ""

    periodo = flow["periodo"]
    parts = [
        f'<h2 style="{_H2}">Captacao dos fundos de vitrine</h2>',
        f'<p style="{_P}">Informe diario da CVM, {_esc(periodo["de"])} a '
        f'{_esc(periodo["ate"])}. A CVM publica fluxo por <strong>fundo</strong>, '
        "nunca por distribuidor: os numeros por casa sao o fluxo dos fundos que "
        "ela exibe, no mercado inteiro.</p>",
    ]

    linhas = ""
    for casa in flow["casas"]:
        cor = "#2c6e4b" if casa["captacao_liquida"] > 0 else "#a3402b"
        linhas += (
            f'<tr><td style="{_TD}">{_esc(casa["label"])}</td>'
            f'<td style="{_TD}text-align:right;">{casa["com_informe"]} de {casa["vitrine"]}</td>'
            f'<td style="{_TD}text-align:right;color:{cor};font-weight:600;">'
            f'{_esc(_mm(casa["captacao_liquida"]))}</td>'
            f'<td style="{_TD}text-align:right;">{casa["exclusivos"]} / '
            f'{_esc(_mm(casa["captacao_exclusivos"]))}</td></tr>'
        )
    parts.append(
        f'<table style="{_TABLE}"><thead><tr>'
        f'<th style="{_TH}">Casa</th>'
        f'<th style="{_TH}text-align:right;">Com informe</th>'
        f'<th style="{_TH}text-align:right;">Captacao no mercado</th>'
        f'<th style="{_TH}text-align:right;">So nesta vitrine</th>'
        f"</tr></thead><tbody>{linhas}</tbody></table>"
    )

    competencias = flow.get("competencias") or []
    if competencias:
        cabecalho = "".join(
            f'<th style="{_TH}text-align:right;">{_esc(_competencia(c))}</th>'
            for c in competencias
        )
        corpo = ""
        for platform_id, serie in (flow.get("serie_por_casa") or {}).items():
            por_competencia = {m["competencia"]: m for m in serie}
            celulas = ""
            for competencia in competencias:
                mes = por_competencia.get(competencia)
                texto = _mm(mes["media_diaria"]) if mes else "-"
                celulas += f'<td style="{_TD}text-align:right;">{_esc(texto)}</td>'
            corpo += (
                f'<tr><td style="{_TD}">{_esc(_platform_label(platform_id))}</td>'
                f"{celulas}</tr>"
            )
        parts.append(
            f'<p style="{_P}"><strong>Tendencia</strong> - media diaria de captacao '
            "por competencia (o mes corrente tem menos pregoes que os fechados).</p>"
            f'<table style="{_TABLE}"><thead><tr><th style="{_TH}">Casa</th>'
            f"{cabecalho}</tr></thead><tbody>{corpo}</tbody></table>"
        )

    resumo = flow.get("tendencias") or {}
    rotulos = flow.get("tendencia_labels") or {}
    if resumo:
        itens = "".join(
            f'<li style="{_LI}"><strong>{quantidade}</strong> '
            f"{_esc(chave.replace('_', ' '))}"
            + (f" - {_esc(rotulos[chave])}" if chave in rotulos else "")
            + "</li>"
            for chave, quantidade in resumo.items()
        )
        parts.append(
            f'<p style="{_P}"><strong>Fundos de varejo por tendencia</strong></p>'
            f'<ul style="margin:0 0 14px;padding-left:18px;">{itens}</ul>'
        )

    for titulo, chave in (
        ("Maiores captadores de varejo", "top_captacao"),
        ("Maiores perdas de varejo", "top_resgate"),
        ("Acelerando", "acelerando"),
        ("Viraram saida", "virou_saida"),
    ):
        fundos = flow.get(chave) or []
        if not fundos:
            continue
        itens = "".join(
            f'<li style="{_LI}">{_esc(fundo["nome"])} - '
            f'{_esc(_mm(fundo["captacao_liquida"]))}, '
            f'{_inteiro(fundo["variacao_cotistas"])} cotistas - '
            f'{_esc(", ".join(fundo["vitrines"]))}</li>'
            for fundo in fundos[:_FLOW_LISTED]
        )
        parts.append(
            f'<p style="{_P}"><strong>{_esc(titulo)}</strong></p>'
            f'<ul style="margin:0 0 14px;padding-left:18px;">{itens}</ul>'
        )

    grade = report.grade_opportunities
    if grade and grade.get("available"):
        parts.append(
            f'<p style="{_P}"><strong>Oportunidade de grade:</strong> '
            f'{grade["total"]} fundos captam varejo fora da vitrine da XP, mas '
            f'{grade["total_produto_do_concorrente"]} sao produto proprio da casa '
            f"que os exibe. Restam <strong>{grade['total_transferiveis']} fundos, "
            f"{_esc(_mm(grade['captacao_transferivel']))}</strong> - "
            f'{grade["total_operacionais"]} de gestora que a XP ja distribui, '
            f'{grade["total_comerciais"]} exigem abrir gestora.</p>'
        )

    return "".join(parts)


# --- HTML para e-mail --------------------------------------------------------

_EMAIL_BODY = "margin:0;padding:24px 12px;background:#f4f4f2;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0b0b0b;"
_EMAIL_CARD = "max-width:600px;margin:0 auto;background:#ffffff;border:1px solid #e1e0d9;border-radius:10px;padding:24px;"
_H2 = "font-size:16px;font-weight:600;margin:26px 0 10px;padding-bottom:6px;border-bottom:1px solid #e1e0d9;"
_H3 = "font-size:14px;font-weight:600;margin:18px 0 6px;"
_P = "font-size:14px;line-height:1.55;margin:0 0 12px;color:#52514e;"
_LI = "font-size:13.5px;line-height:1.5;margin-bottom:5px;color:#0b0b0b;"
_TABLE = "border-collapse:collapse;width:100%;font-size:13px;margin-bottom:14px;"
_TH = "text-align:left;padding:7px 8px;background:#f9f9f7;border-bottom:1px solid #e1e0d9;font-size:12px;color:#52514e;"
_TD = "padding:7px 8px;border-bottom:1px solid #f0efec;"


def _esc(value: object) -> str:
    return html.escape(str(value))


def _html_list(items: list[str], total: int) -> str:
    rows = "".join(f'<li style="{_LI}">{item}</li>' for item in items)
    more = (
        f'<li style="{_LI}color:#898781;">... e mais {total - len(items)} fundos</li>'
        if total > len(items)
        else ""
    )
    return f'<ul style="margin:0 0 14px;padding-left:20px;">{rows}{more}</ul>'


def _html_shelf_section(diff: ShelfDiff, snapshot: ShelfSnapshot | None) -> str:
    parts = [f'<h3 style="{_H3}">{_esc(_platform_label(diff.platform))}</h3>']

    if snapshot and snapshot.status == SnapshotStatus.UNAVAILABLE:
        parts.append(
            f'<p style="{_P}">Coleta indisponivel: '
            f'{_esc(snapshot.error or "motivo nao informado")}.</p>'
        )
        return "".join(parts)

    tamanho = _frase_de_tamanho(snapshot)
    if diff.is_first_run:
        parts.append(
            f'<p style="{_P}">Primeira coleta: {tamanho}, como linha de base.</p>'
        )
        return "".join(parts)

    if diff.suspect:
        parts.append(
            f'<p style="{_P}background:#fff8e6;border-left:3px solid #fab219;'
            f'padding:8px 10px;border-radius:4px;"><strong>Coleta suspeita</strong> '
            f"({_esc(diff.suspect_reason)}). Saidas omitidas para evitar falso churn.</p>"
        )

    entradas, saidas, alteracoes, so_catalogo = _mudancas_de_vitrine(diff, snapshot)
    cauda = (
        f" Mais {so_catalogo} mudancas so no catalogo Open Finance, fora da vitrine."
        if so_catalogo
        else ""
    )

    if not (entradas or saidas or alteracoes):
        parts.append(
            f'<p style="{_P}">Sem mudancas na vitrine: {tamanho}.{cauda}</p>'
        )
        return "".join(parts)

    parts.append(
        f'<p style="{_P}">{len(entradas)} entradas, {len(saidas)} saidas, '
        f"{len(alteracoes)} alteracoes na vitrine - {tamanho}.{cauda}</p>"
    )

    if entradas:
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Entraram na vitrine</strong></p>'
        )
        parts.append(
            _html_list(
                [
                    _esc(f.name)
                    + (f" <span style='color:#898781;'>{format_cnpj(f.cnpj)}</span>" if f.cnpj else "")
                    for f in entradas[:_MAX_LISTED]
                ],
                len(entradas),
            )
        )

    if saidas:
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Sairam da vitrine</strong></p>'
        )
        parts.append(
            _html_list([_esc(f.name) for f in saidas[:_MAX_LISTED]], len(saidas))
        )

    if alteracoes:
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Mudancas de condicoes</strong></p>'
        )
        items = []
        for change in alteracoes[:_MAX_LISTED]:
            details = ", ".join(
                f"{_esc(_FIELD_LABELS.get(c.field, c.field))}: "
                f"{_esc(_format_value(c.old))} &rarr; <strong>{_esc(_format_value(c.new))}</strong>"
                for c in change.changes
            )
            items.append(f"{_esc(change.name)} &mdash; {details}")
        parts.append(_html_list(items, len(alteracoes)))

    return "".join(parts)


def _html_cvm_section(report: RunReport) -> str:
    snapshot = report.cvm_snapshot
    parts = [f'<h2 style="{_H2}">Radar CVM &mdash; visao antecipada</h2>']

    if not snapshot or snapshot.status == SnapshotStatus.UNAVAILABLE:
        error = snapshot.error if snapshot else "extrato nao coletado"
        parts.append(f'<p style="{_P}">Extrato da CVM indisponivel: {_esc(error)}.</p>')
        return "".join(parts)

    pre_op_total = sum(1 for f in snapshot.funds if f.is_pre_operational)
    parts.append(
        f'<p style="{_P}">{len(snapshot.funds)} fundos de gestoras ligadas as casas '
        f"monitoradas, {pre_op_total} em fase pre-operacional (registrados e ainda nao "
        "lancados).</p>"
    )

    rows = "".join(
        f'<tr><td style="{_TD}">{_esc(_platform_label(house))}</td>'
        f'<td style="{_TD}text-align:right;">{len(funds)}</td>'
        f'<td style="{_TD}text-align:right;">'
        f'{sum(1 for f in funds if f.is_pre_operational)}</td></tr>'
        for house, funds in sorted(snapshot.by_house().items())
    )
    parts.append(
        f'<table style="{_TABLE}"><thead><tr>'
        f'<th style="{_TH}">Casa</th>'
        f'<th style="{_TH}text-align:right;">Registrados</th>'
        f'<th style="{_TH}text-align:right;">Pre-operacionais</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )

    diff = report.cvm_diff
    if not diff:
        return "".join(parts)

    if diff.new_registrations:
        title = (
            "Fundos registrados e ainda nao lancados"
            if diff.is_first_run
            else "Novos registros na CVM"
        )
        parts.append(f'<p style="{_P}margin-bottom:6px;"><strong>{title}</strong></p>')
        items = [
            f"[{_esc(_platform_label(f.house))}] {_esc(f.name)}"
            f" <span style='color:#898781;'>{format_cnpj(f.cnpj)}</span>"
            + (f" &middot; registrado em {_esc(f.registered_at)}" if f.registered_at else "")
            for f in diff.new_registrations[:_MAX_LISTED]
        ]
        parts.append(_html_list(items, len(diff.new_registrations)))

    if diff.launches:
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Entraram em operacao</strong></p>'
        )
        parts.append(
            _html_list(
                [
                    f"[{_esc(_platform_label(c.fund.house))}] {_esc(c.fund.name)}"
                    for c in diff.launches[:_MAX_LISTED]
                ],
                len(diff.launches),
            )
        )

    return "".join(parts)


def _html_gaps_section(gaps: dict) -> str:
    parts = [f'<h2 style="{_H2}">Gaps de prateleira da XP</h2>']
    if not gaps.get("available"):
        parts.append(
            f'<p style="{_P}"><strong>Analise suprimida nesta execucao:</strong> '
            f"{_esc(gaps.get('reason'))}.</p>"
        )
        return "".join(parts)

    if not gaps["total_gaps"]:
        parts.append(
            f'<p style="{_P}">Nenhum gap: toda vitrine concorrente coletada '
            "esta contida na vitrine da XP.</p>"
        )
    else:
        parts.append(
            f'<p style="{_P}"><strong>{gaps["total_gaps"]}</strong> fundos de '
            "varejo estao em vitrines concorrentes e fora da vitrine da XP "
            f"({gaps['multi_house_gaps']} em 2 ou mais casas).</p>"
        )
        items = "".join(
            f'<li style="{_LI}">{_esc(fund["name"])} ({_esc(fund["cnpj_display"])}) '
            f"- {_esc(fund['category'])} - casas: {_esc(fund['houses_label'])}</li>"
            for fund in gaps["funds"][:_GAP_LISTED]
        )
        remaining = gaps["total_gaps"] - min(_GAP_LISTED, len(gaps["funds"]))
        if remaining > 0:
            items += (
                f'<li style="{_LI}">... e mais {remaining} '
                "(dashboard e planilha)</li>"
            )
        parts.append(f'<ul style="margin:0 0 12px;padding-left:20px;">{items}</ul>')

        categories = gaps["categories"][:_GAP_CATEGORY_ROWS]
        if categories:
            rows = ""
            for entry in categories:
                best = entry["best_competitor"]
                best_label = f"{best['label']} ({best['count']})" if best else "-"
                rows += (
                    f'<tr><td style="{_TD}">{_esc(entry["category"])}</td>'
                    f'<td style="{_TD}text-align:right;">{entry["xp"]}</td>'
                    f'<td style="{_TD}">{_esc(best_label)}</td>'
                    f'<td style="{_TD}text-align:right;">{entry["gap_count"]}</td></tr>'
                )
            parts.append(
                f'<table style="{_TABLE}"><thead><tr>'
                f'<th style="{_TH}">Categoria</th>'
                f'<th style="{_TH}text-align:right;">XP</th>'
                f'<th style="{_TH}">Melhor concorrente</th>'
                f'<th style="{_TH}text-align:right;">Gaps</th>'
                f"</tr></thead><tbody>{rows}</tbody></table>"
            )

    notes = " ".join(_esc(note) for note in _gap_footnotes(gaps))
    parts.append(f'<p style="font-size:12px;color:#898781;margin:0 0 12px;">{notes}</p>')
    return "".join(parts)


def render_html(report: RunReport) -> str:
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    sentence, is_alert = _summary_sentence(report)
    summary = f"<strong>{_esc(sentence)}</strong>" if is_alert else _esc(sentence)

    rows = ""
    tem_catalogo_maior = False
    for snapshot in report.snapshots:
        diff = report.diff_for(snapshot.platform)
        vitrine, catalogo = _tamanhos(snapshot)
        tem_catalogo_maior = tem_catalogo_maior or catalogo > vitrine
        if diff and not diff.is_first_run:
            entradas, saidas, alteracoes, _ = _mudancas_de_vitrine(diff, snapshot)
        else:
            entradas = saidas = alteracoes = []
        rows += (
            f'<tr><td style="{_TD}">{_esc(_platform_label(snapshot.platform))}</td>'
            f'<td style="{_TD}text-align:right;">{vitrine if vitrine else "sem vitrine"}</td>'
            f'<td style="{_TD}text-align:right;color:#898781;">{catalogo}</td>'
            f'<td style="{_TD}">{_esc(snapshot.status.value)}</td>'
            f'<td style="{_TD}text-align:right;">{len(entradas)}</td>'
            f'<td style="{_TD}text-align:right;">{len(saidas)}</td>'
            f'<td style="{_TD}text-align:right;">{len(alteracoes)}</td></tr>'
        )

    nota_base = (
        f'<p style="font-size:12px;color:#898781;margin:-6px 0 18px;">'
        "Vitrine: fundos de varejo na listagem da propria casa &mdash; o que o cliente ve, "
        "e a unica coluna comparavel entre casas. Catalogo: o que a casa administra, "
        "somando o Open Finance. Entradas, saidas e alteracoes contam a vitrine.</p>"
        if tem_catalogo_maior
        else ""
    )

    shelf_sections = "".join(
        _html_shelf_section(diff, report.snapshot_for(diff.platform))
        for diff in report.shelf_diffs
    )

    ticket = _frase_do_ticket(report)
    bloco_ticket = (
        f'<h2 style="{_H2}">Ticket de entrada</h2>'
        f'<p style="{_P}">{_md_para_html(ticket)}</p>'
        if ticket else ""
    )

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor de Prateleiras de Fundos</title></head>
<body style="{_EMAIL_BODY}">
<div style="{_EMAIL_CARD}">
  <h1 style="font-size:19px;font-weight:600;margin:0 0 4px;">Monitor de Prateleiras de Fundos</h1>
  <p style="font-size:13px;color:#898781;margin:0 0 18px;">
    Concorrentes da XP &middot; execucao de {_esc(generated)}</p>

  <p style="{_P}">{summary}</p>

  <table style="{_TABLE}"><thead><tr>
    <th style="{_TH}">Plataforma</th>
    <th style="{_TH}text-align:right;">Vitrine</th>
    <th style="{_TH}text-align:right;">Catalogo</th>
    <th style="{_TH}">Status</th>
    <th style="{_TH}text-align:right;">Entradas</th>
    <th style="{_TH}text-align:right;">Saidas</th>
    <th style="{_TH}text-align:right;">Alteracoes</th>
  </tr></thead><tbody>{rows}</tbody></table>
  {nota_base}

  {_html_gaps_section(_gaps_ranqueados(report))}

  {bloco_ticket}

  {_html_flow_section(report)}
  {_html_performance_section(report)}

  {_html_cvm_section(report)}

  <h2 style="{_H2}">Vitrines</h2>
  {shelf_sections}

  <p style="font-size:12px;color:#898781;margin-top:24px;border-top:1px solid #e1e0d9;padding-top:14px;">
    Fontes publicas: sites das plataformas, API de Dados Abertos de Investimentos do Open
    Finance Brasil e cadastro de fundos da CVM.
  </p>
</div>
</body></html>
"""


def render_digest(config: Config, report: RunReport) -> tuple[Path, Path]:
    """Grava digest markdown e HTML. Devolve (path_md, path_html)."""
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    today: date = report.generated_at.date()

    md_path = config.reports_dir / f"digest-{today.isoformat()}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")

    html_path = config.reports_dir / f"digest-{today.isoformat()}.html"
    html_path.write_text(render_html(report), encoding="utf-8")

    return md_path, html_path
