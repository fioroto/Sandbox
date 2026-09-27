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
    BLOCO_CNPJ,
    BLOCO_COTISTAS,
    BLOCO_PRODUTO_PROPRIO,
    CvmDiff,
    FundChange,
    FundOffer,
    RunReport,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import format_cnpj
from shelf_monitor.report.shared import (
    movimentos_de_vitrine,
    movimentos_do_run,
    rotulo_da_casa,
)
from shelf_monitor.report.retail_flow import leitura_da_captacao
from shelf_monitor.texto import competencia_br, contagem, meses_br, milhar, periodo_br
from shelf_monitor.report.shared.fontes import (
    CVM_CADASTRO,
    CVM_INFORME_DIARIO,
    Fonte,
    fontes_do_relatorio,
)
from shelf_monitor.report.performance import (
    _MAX_EXPOSTOS,
    blocos_de_rentabilidade,
    fundos_abaixo_da_categoria,
    janela_do_bloco,
)
from shelf_monitor.report.aggregate import (
    bloco_de_gaps,
    frase_do_funil,
    listing_funds,
    notas_da_cobertura,
    ranquear_gaps,
    retail_funds,
)

_FIELD_LABELS = {
    "name": "nome",
    "min_initial": "aplicação mínima",
    "admin_fee_pct": "taxa de administração",
    "performance_fee_pct": "taxa de performance",
    "closed_for_investment": "fechado para captação",
    "redemption_quotation_days": "cotização do resgate (dias)",
    "redemption_payment_days": "líquidação do resgate (dias)",
    "grace_days": "carência (dias)",
}

_MAX_LISTED = 25

# Gaps listados no e-mail: o topo cabe numa tela; o resto mora no dashboard e
# na planilha.
_GAP_LISTED = 10
_GAP_CATEGORY_ROWS = 8
_GAP_COBERTOS_LISTED = 5


def _gap_footnotes(gaps: dict) -> list[str]:
    """Proveniencia do bloco de gaps - as frases sao as mesmas em md e html."""
    notes = [
        "Comparação vitrine x vitrine por CNPJ - o catálogo Open Finance não "
        "conta em nenhum dos lados."
    ]
    if gaps.get("baseline_warning"):
        notes.append(f"Atenção: {gaps['baseline_warning']}.")
    if gaps.get("excluded"):
        out = "; ".join(f"{e['label']} ({e['reason']})" for e in gaps["excluded"])
        notes.append(f"Fora do universo: {out}.")
    notes.extend(notas_da_cobertura(gaps))
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
        return (
            "sem vitrine pública coletada; "
            f"{contagem(catalogo, 'fundo', 'fundos')} no catálogo Open Finance"
        )
    if catalogo > vitrine:
        return (
            f"{contagem(vitrine, 'fundo', 'fundos')} na vitrine "
            f"({_contagem(catalogo)} no catálogo)"
        )
    return f"{contagem(vitrine, 'fundo', 'fundos')} na vitrine"


def _movimento(entradas: list, saidas: list, alteracoes: list) -> str:
    """"1 entrada, 0 saídas, 3 alterações" - cada contagem concorda com o número."""
    return (
        f"{contagem(len(entradas), 'entrada', 'entradas')}, "
        f"{contagem(len(saidas), 'saída', 'saídas')}, "
        f"{contagem(len(alteracoes), 'alteração', 'alterações')}"
    )


def _juntar(itens: list[str]) -> str:
    """"XP, BTG e Itau"."""
    if len(itens) < 2:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _capitalizar(frase: str | None) -> str:
    frase = frase or ""
    return frase[:1].upper() + frase[1:]


def _mudancas_de_vitrine(
    diff: ShelfDiff, snapshot: ShelfSnapshot | None
) -> tuple[list[FundOffer], list[FundOffer], list[FundChange], int]:
    """(entradas, saidas, alteracoes) na vitrine, e quantas ficaram no catalogo.

    O diff roda sobre tudo que foi coletado, e tem de continuar rodando: fundo
    que aparece so no Open Finance de uma casa e sinal de registro novo. Mas no
    digest ele nao pode entrar em "entraram na prateleira" - num dia de churn do
    catalogo do BTG, a secao dele afogaria as mudancas reais de todas as casas.
    Entao a vitrine vai listada e o catalogo vai contado, em uma linha. A regra
    e a de `movimentos_de_vitrine`, a mesma do assunto, do console e do deck.
    """
    casa = movimentos_de_vitrine(diff, snapshot)
    return casa.entradas, casa.saidas, casa.alteracoes, casa.so_catalogo


def _format_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "sim" if value else "não"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}".replace(",", ".")
        return f"{value:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return str(value)


def _summary_counts(report: RunReport) -> tuple[int, int, int, int]:
    """(entradas, saidas, alteracoes) de vitrine, e o que mudou so no catalogo."""
    total = movimentos_do_run(report)
    return (
        len(total.entradas), len(total.saidas), len(total.alteracoes), total.so_catalogo
    )


def _summary_sentence(report: RunReport) -> tuple[str, bool]:
    """Frase de abertura do digest, e se ela e um alerta.

    Distingue tres silencios que se parecem no numero mas nao no significado:
    coleta que falhou inteira, primeira coleta, e prateleira estavel.
    """
    added, removed, changed, catalogo = _summary_counts(report)
    collected = [s for s in report.snapshots if s.status != SnapshotStatus.UNAVAILABLE]

    if report.snapshots and not collected:
        return (
            "Nenhuma fonte foi acessível nesta execução: nada foi coletado e nenhuma "
            "comparação pode ser feita. Cheque o acesso às fontes com "
            "`python -m shelf_monitor doctor`.",
            True,
        )

    first_run = all(d.is_first_run for d in report.shelf_diffs) if report.shelf_diffs else True
    rebaseadas = [
        _platform_label(d.platform) for d in report.shelf_diffs if d.nova_linha_de_base
    ]
    if first_run:
        vitrine = sum(len(listing_funds(s)) for s in collected)
        catalogo_total = sum(len(retail_funds(s)) for s in collected)
        if rebaseadas:
            return (
                f"Nova linha de base: {contagem(vitrine, 'fundo', 'fundos')} de vitrine em "
                f"{contagem(len(collected), 'plataforma', 'plataformas')} "
                f"({_contagem(catalogo_total)} no catálogo de varejo). A coleta "
                f"passou a ver a vitrine de {_juntar(rebaseadas)}: ganho de cobertura, "
                "não fundos que entraram. As comparações recomeçam na próxima execução.",
                False,
            )
        return (
            f"Primeira coleta: {contagem(vitrine, 'fundo', 'fundos')} de vitrine em "
            f"{contagem(len(collected), 'plataforma', 'plataformas')} "
            f"{'vira' if vitrine == 1 else 'viram'} a linha de base "
            f"({_contagem(catalogo_total)} no catálogo de varejo). As comparações "
            "começam na próxima execução.",
            False,
        )

    cauda = (
        (
            " Outra mudança ficou" if catalogo == 1
            else f" Outras {contagem(catalogo, 'mudança', 'mudanças')} ficaram"
        )
        + " só no catálogo Open Finance, fora da vitrine."
        if catalogo
        else ""
    )
    if rebaseadas:
        cauda += (
            f" {_juntar(rebaseadas)} {'virou' if len(rebaseadas) == 1 else 'viraram'} "
            "nova linha de base - a coleta passou a ver a vitrine - e "
            f"{'fica' if len(rebaseadas) == 1 else 'ficam'} fora da contagem."
        )

    if not (added or removed or changed):
        return (
            "Nenhuma mudança nas vitrines monitoradas desde a última coleta." + cauda,
            False,
        )

    return (
        f"{contagem(added, 'fundo entrou', 'fundos entraram')}, "
        f"{contagem(removed, 'saiu', 'saíram')} e "
        f"{contagem(changed, 'teve', 'tiveram')} condições alteradas nas vitrines "
        "monitoradas." + cauda,
        False,
    )


# --- markdown ----------------------------------------------------------------


def _md_shelf_section(
    diff: ShelfDiff, snapshot: ShelfSnapshot | None, url: str | None = None
) -> list[str]:
    rotulo = _platform_label(diff.platform)
    lines = [f"### [{rotulo}]({url})" if url else f"### {rotulo}", ""]

    if snapshot and snapshot.status == SnapshotStatus.UNAVAILABLE:
        lines += [f"Coleta indisponível: {snapshot.error or 'motivo não informado'}.", ""]
        return lines

    tamanho = _frase_de_tamanho(snapshot)
    if diff.nova_linha_de_base:
        lines += [
            f"Nova linha de base: {tamanho}. {_capitalizar(diff.suspect_reason)}. "
            "Comparações recomeçam na próxima execução.",
            "",
        ]
        return lines
    if diff.is_first_run:
        lines += [
            f"Primeira coleta: {tamanho}, como linha de base. "
            "Comparações começam na próxima execução.",
            "",
        ]
        return lines

    if diff.suspect:
        lines += [
            f"**Coleta suspeita** ({diff.suspect_reason}). Saídas não reportadas para "
            "evitar falso churn.",
            "",
        ]

    entradas, saidas, alteracoes, so_catalogo = _mudancas_de_vitrine(diff, snapshot)
    cauda = (
        f" Mais {contagem(so_catalogo, 'mudança', 'mudanças')} só no catálogo Open Finance, "
        "fora da vitrine."
        if so_catalogo
        else ""
    )

    if not (entradas or saidas or alteracoes):
        lines += [f"Sem mudanças na vitrine: {tamanho}.{cauda}", ""]
        return lines

    lines += [
        f"{_movimento(entradas, saidas, alteracoes)} na vitrine - {tamanho}.{cauda}",
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
            lines.append(f"- ... e mais {contagem(len(entradas) - _MAX_LISTED, 'fundo', 'fundos')}")
        lines.append("")

    if saidas:
        lines.append("**Saíram da vitrine**")
        lines.append("")
        for fund in saidas[:_MAX_LISTED]:
            cnpj = f" ({format_cnpj(fund.cnpj)})" if fund.cnpj else ""
            lines.append(f"- {fund.name}{cnpj}")
        if len(saidas) > _MAX_LISTED:
            lines.append(f"- ... e mais {contagem(len(saidas) - _MAX_LISTED, 'fundo', 'fundos')}")
        lines.append("")

    if alteracoes:
        lines.append("**Mudanças de condições**")
        lines.append("")
        for change in alteracoes[:_MAX_LISTED]:
            details = ", ".join(
                f"{_FIELD_LABELS.get(c.field, c.field)}: "
                f"{_format_value(c.old)} -> {_format_value(c.new)}"
                for c in change.changes
            )
            lines.append(f"- {change.name} - {details}")
        if len(alteracoes) > _MAX_LISTED:
            lines.append(f"- ... e mais {contagem(len(alteracoes) - _MAX_LISTED, 'fundo', 'fundos')}")
        lines.append("")

    return lines


def _md_cvm_section(diff: CvmDiff | None, report: RunReport) -> list[str]:
    lines = ["## Radar CVM", ""]

    snapshot = report.cvm_snapshot
    if not snapshot or snapshot.status == SnapshotStatus.UNAVAILABLE:
        error = snapshot.error if snapshot else "extrato não coletado"
        lines += [f"Extrato da CVM indisponível: {error}.", ""]
        return lines

    by_house = snapshot.by_house()
    lines.append(
        f"{contagem(len(snapshot.funds), 'fundo', 'fundos')} na watchlist, "
        f"{_contagem(sum(1 for f in snapshot.funds if f.is_pre_operational))} em fase "
        "pré-operacional. "
        f"Fonte: {_link(CVM_CADASTRO)}."
    )
    lines.append("")
    lines.append("| Casa | Registrados | Pré-operacionais |")
    lines.append("|---|---:|---:|")
    for house, funds in sorted(by_house.items()):
        pre_op = sum(1 for f in funds if f.is_pre_operational)
        lines.append(f"| {_platform_label(house)} | {_contagem(len(funds))} | {_contagem(pre_op)} |")
    lines.append("")

    if not diff:
        return lines

    if diff.is_first_run:
        pre_op = diff.new_registrations
        if pre_op:
            lines += ["**Fundos registrados e ainda não lançados**", ""]
            for fund in pre_op[:_MAX_LISTED]:
                lines.append(
                    f"- [{_platform_label(fund.house)}] {fund.name} "
                    f"({format_cnpj(fund.cnpj)}) - registrado em "
                    f"{fund.registered_at or 'data não informada'}"
                )
            if len(pre_op) > _MAX_LISTED:
                lines.append(f"- ... e mais {contagem(len(pre_op) - _MAX_LISTED, 'fundo', 'fundos')}")
            lines.append("")
        return lines

    if diff.new_registrations:
        lines += ["**Novos registros na CVM**", ""]
        for fund in diff.new_registrations[:_MAX_LISTED]:
            status = " (pré-operacional)" if fund.is_pre_operational else ""
            lines.append(
                f"- [{_platform_label(fund.house)}] {fund.name} "
                f"({format_cnpj(fund.cnpj)}) - gestor {fund.manager_name or '-'}{status}"
            )
        if len(diff.new_registrations) > _MAX_LISTED:
            lines.append(f"- ... e mais {len(diff.new_registrations) - _MAX_LISTED}")
        lines.append("")

    launches = diff.launches
    if launches:
        lines += ["**Fundos que entraram em operação**", ""]
        for change in launches[:_MAX_LISTED]:
            lines.append(
                f"- [{_platform_label(change.fund.house)}] {change.fund.name} "
                f"({format_cnpj(change.fund.cnpj)})"
            )
        lines.append("")

    others = [c for c in diff.situation_changes if not c.is_launch]
    if others:
        lines += ["**Outras mudanças de situação**", ""]
        for change in others[:_MAX_LISTED]:
            lines.append(
                f"- [{_platform_label(change.fund.house)}] {change.fund.name}: "
                f"{change.old_situation or '-'} -> {change.new_situation or '-'}"
            )
        lines.append("")

    if diff.cancelled:
        lines += ["**Saíram do cadastro**", ""]
        for fund in diff.cancelled[:_MAX_LISTED]:
            lines.append(f"- [{_platform_label(fund.house)}] {fund.name}")
        lines.append("")

    return lines


def _negrito_html(texto: str) -> str:
    import re

    partes = re.split(r"\*\*(.+?)\*\*", texto)
    return "".join(
        f"<strong>{_esc(parte)}</strong>" if i % 2 else _esc(parte)
        for i, parte in enumerate(partes)
    )


_LINK_MD = r"\[([^\]]+)\]\((https?://[^)\s]+)\)"


def _md_para_html(texto: str) -> str:
    """Converte o `**negrito**` e os `[links](url)` de uma frase, escapando o resto.

    Existe porque as frases de veredito sao escritas uma vez, em markdown, e
    saem nos dois formatos: duplicar o texto para ter uma versao com <strong>
    seria a mesma divergencia que o resto desta revisao veio apagar. Os links
    sao as fontes citadas no texto (ver report/shared/fontes.py).
    """
    import re

    saida = []
    pos = 0
    for m in re.finditer(_LINK_MD, texto):
        saida.append(_negrito_html(texto[pos:m.start()]))
        saida.append(
            f'<a href="{_esc(m.group(2))}" style="color:#1a5fb4;">{_esc(m.group(1))}</a>'
        )
        pos = m.end()
    saida.append(_negrito_html(texto[pos:]))
    return "".join(saida)


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
    if leitura.get("sem_base"):
        # Falta de dado da coleta, nunca "a casa nao tem fundo barato".
        return (
            f"Sem base para comparar o ticket da {leitura['baseline_label']}: a "
            f"coleta trouxe o mínimo da própria vitrine em "
            f"{leitura['baseline_amostra']} fundos, abaixo dos "
            f"{leitura['amostra_minima']} necessários. A melhor concorrente nessa "
            f"faixa, {leitura['melhor_label']}, tem {leitura['melhor_varejo']} "
            f"fundos abaixo de {teto}."
        )
    if leitura["baseline_varejo"] == 0:
        return (
            f"**A vitrine da {leitura['baseline_label']} não tem nenhum fundo "
            f"abaixo de {teto}.** A melhor concorrente nessa faixa, "
            f"{leitura['melhor_label']}, tem {leitura['melhor_varejo']}. Não é "
            "diferença de composição: é a ausência de uma porta de entrada de "
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
    gaps = bloco_de_gaps(report)
    if not gaps.get("available"):
        return gaps
    return {**gaps, "funds": ranquear_gaps(
        gaps.get("funds") or [], report.retail_flow, report.grade_opportunities
    )}


def _abertura_dos_gaps(gaps: dict) -> str:
    """A frase que abre a secao - a mesma em md e html.

    Com o mapa de familias, e o funil (aparentes -> cobertos -> reais); sem
    ele, a contagem de antes, CNPJ contra CNPJ.
    """
    funil = frase_do_funil(gaps)
    ordem = (
        "Ordenados pelo esforço de fechar e, dentro de cada faixa, pela captação "
        "que estão levando no mercado. Produto próprio do concorrente não se "
        "transfere e vai para o fim, sem sair da lista."
    )
    if funil:
        return f"{funil} {ordem}"
    return (
        f"{gaps['total_gaps']} fundos de varejo estão em vitrines concorrentes e "
        f"fora da vitrine da XP ({gaps['multi_house_gaps']} em 2 ou mais casas). "
        + ordem
    )


def _linha_do_coberto(fund: dict) -> str:
    """"Fundo (CNPJ) - coberto por espelho: Fundo XP (CNPJ), D+61 contra D+60"."""
    v = fund.get("veredito") or {}
    cobre = v.get("nome_cobre") or ""
    cnpj_cobre = format_cnpj(v.get("cobre")) if v.get("cobre") else ""
    liquidez = ""
    if v.get("dmais") is not None and v.get("dmais_cobre") is not None:
        liquidez = f", D+{v['dmais']} contra D+{v['dmais_cobre']} na XP"
    return (
        f"{fund['name']} ({fund['cnpj_display']}) - {v.get('rotulo')}: "
        f"{cobre} ({cnpj_cobre}){liquidez}"
    )


def _md_gaps_section(gaps: dict) -> list[str]:
    lines = ["## Gaps de prateleira da XP", ""]
    if gaps.get("nao_se_aplica"):
        lines += [f"Não se aplica a este monitor: {gaps.get('reason')}.", ""]
        return lines
    if not gaps.get("available"):
        lines += [f"Análise suprimida nesta execução: {gaps.get('reason')}.", ""]
        return lines

    if not gaps["total_gaps"] and not gaps.get("gaps_liquidez"):
        lines += [
            frase_do_funil(gaps)
            or "Nenhum gap: toda vitrine concorrente coletada está contida na "
            "vitrine da XP.",
            "",
        ]
    else:
        lines += [_abertura_dos_gaps(gaps), ""]
        for fund in gaps["funds"][:_GAP_LISTED]:
            captacao = fund.get("captacao_liquida")
            fluxo = f" - {_mm(captacao)} de captação" if captacao is not None else ""
            esforco = f" - {fund['esforco']}" if fund.get("esforco") else ""
            lines.append(
                f"- {fund['name']} ({fund['cnpj_display']}) - "
                f"{fund['category']} - casas: {fund['houses_label']}{fluxo}{esforco}"
            )
        remaining = len(gaps["funds"]) - min(_GAP_LISTED, len(gaps["funds"]))
        if remaining > 0:
            lines.append(f"- ... e mais {remaining} (dashboard e planilha)")
        lines.append("")

        categories = gaps["categories"][:_GAP_CATEGORY_ROWS]
        if categories:
            lines.append("| Categoria | XP | Melhor concorrente | Gaps reais | Cobertos |")
            lines.append("|---|---:|---|---:|---:|")
            for entry in categories:
                best = entry["best_competitor"]
                best_label = f"{best['label']} ({best['count']})" if best else "-"
                lines.append(
                    f"| {entry['category']} | {entry['xp']} | "
                    f"{best_label} | {entry['gap_count']} | {entry.get('cobertos', 0)} |"
                )
            lines.append("")

    cobertos = gaps.get("cobertos") or []
    if cobertos:
        lines.append(
            f"**Já cobertos** ({milhar(len(cobertos))}, os primeiros; a lista inteira "
            "com a evidência está na aba 'Gaps cobertos' da planilha):"
        )
        for fund in cobertos[:_GAP_COBERTOS_LISTED]:
            lines.append(f"- {_linha_do_coberto(fund)}")
        lines.append("")

    for note in _gap_footnotes(gaps):
        lines.append(f"_{note}_")
    lines.append("")
    return lines


def _pct(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:.2f}%".replace(".", ",")


def _pp(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:+.2f}pp".replace(".", ",")


def _share(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:.0%}"


def _indice(massa: dict) -> str:
    """Share do PL sobre share dos fundos. Abaixo de 1, o dinheiro evita os piores."""
    valor = massa.get("indice_pl")
    return "-" if valor is None else f"{valor:.2f}".replace(".", ",")


def _contagem(valor: int) -> str:
    """Numero absoluto. `_inteiro` poe sinal, porque serve a variacao."""
    return f"{valor:,}".replace(",", ".")


def _prazo(bloco: dict, destaque=lambda texto: f"**{texto}**") -> str:
    """"12 meses, de 30/09/2025 a 23/09/2026 (243 pregões, base na ...)".

    O prazo sai inteiro junto do numero, e em data brasileira: em 2026-09-24 o
    digest dizia "2026-07-01 a 2026-09-23 (61 pregões)" e o leitor tinha de
    deduzir que eram tres meses e de onde partia a conta.
    """
    janela = janela_do_bloco(bloco)
    partes = []
    if janela.get("pregoes"):
        partes.append(f"{_contagem(janela['pregoes'])} pregões")
    if janela.get("base"):
        partes.append(f"base na {janela['base']}")
    detalhe = f" ({', '.join(partes)})" if partes else ""
    return (
        f"{destaque(janela['rotulo'])}, "
        f"{periodo_br(janela.get('de'), janela.get('ate'))}{detalhe}"
    )


def _excluidos(blocos: list[dict]) -> list[dict]:
    """Quem ficou de fora de alguma janela, sem repetir o mesmo motivo."""
    vistos: set[tuple[str, str]] = set()
    saida = []
    for bloco in blocos:
        for item in bloco.get("excluded", []):
            chave = (item.get("label", ""), item.get("reason", ""))
            if chave not in vistos:
                vistos.add(chave)
                saida.append(item)
    return saida


def _md_tabela_rentabilidade(bloco: dict, sem_cotistas: str | None) -> list[str]:
    if sem_cotistas:
        lines = [
            "| Casa | Mediana dos fundos | Ponderada por PL | Abaixo da mediana da categoria |",
            "|---|---:|---:|---|",
        ]
    else:
        lines = [
            "| Casa | Mediana dos fundos | Ponderada por PL | Ponderada por cotistas | PL menos cotistas (pp) | Abaixo da mediana da categoria |",
            "|---|---:|---:|---:|---:|---|",
        ]
    for casa in bloco["casas"]:
        massa = casa["alocacao_abaixo_da_mediana"]
        por_cotista = (
            "" if sem_cotistas
            else f"{_pct(casa['por_cotista'])} | {_pp(casa['gap'])} | "
        )
        lines.append(
            f"| {casa['label']} | {_pct(casa['mediana'])} | {_pct(casa['por_pl'])} | "
            f"{por_cotista}"
            f"{massa['fundos']} de {massa['de']} ({_share(massa['share_fundos'])}) "
            f"carregam {_share(massa['share_pl'])} do PL - índice {_indice(massa)} |"
        )
    return lines


def _md_performance_section(report: RunReport) -> list[str]:
    """Rentabilidade ponderada e custo relativo, na leitura curta do digest.

    A manchete nao e o retorno medio: e o gap entre a ponderada por PL e a
    ponderada por cotistas, mais quanto do dinheiro e da gente esta em fundo
    que rende abaixo da propria categoria. Sao as duas linhas que nenhuma outra
    secao responde.

    Duas janelas, a mais longa primeiro: 12 meses, que separa quem ficou para
    tras, e 3 meses, a leitura recente. Cada tabela sai com o proprio prazo.
    """
    blocos = blocos_de_rentabilidade(report.performance_12m, report.performance)
    if not blocos:
        return []
    principal = blocos[0]

    # Sem cotistas (previdencia): sai a ponderada por cotistas, o gap contra a
    # ponderada por PL e a lista de "muita gente" - as tres contam cotistas.
    sem_cotistas = report.suprime(BLOCO_COTISTAS)
    lines = [
        "## Rentabilidade da vitrine",
        "",
        f"Prazos: {' e '.join(_prazo(b) for b in blocos)}. Cota patrimonial do "
        f"[informe diário da CVM]({CVM_INFORME_DIARIO.url}), ex-provento, contra a "
        "mediana da mesma categoria no universo oficial, no mesmo prazo. É o "
        "retorno dos fundos que a casa exibe, com o peso que eles têm no "
        "**mercado** - não o retorno dos clientes dela.",
        "",
    ]
    for bloco in blocos:
        janela = janela_do_bloco(bloco)
        lines += [
            f"**Em {janela['rotulo']}** ({periodo_br(janela.get('de'), janela.get('ate'))})",
            "",
            *_md_tabela_rentabilidade(bloco, sem_cotistas),
            "",
        ]
    if sem_cotistas:
        lines.append(f"_Sem ponderação por cotistas: {sem_cotistas}._")
    else:
        lines.append(
            "Gap positivo: o dinheiro grande está nos fundos melhores e o varejo nos "
            "piores. Negativo: o inverso. O sinal do gap **muda conforme a janela** - "
            "ele descreve que categorias performaram no período, não a base de clientes."
        )
    lines.append("")
    lines.append(
        "Índice abaixo de 1 significa que o dinheiro EVITA os fundos piores da "
        "categoria. Cerca de metade de qualquer universo fica abaixo da mediana por "
        "construção, então a contagem de fundos sozinha não é sinal de nada."
    )
    lines.append("")

    # Os fundos de muita gente que rendem abaixo dos pares - o recorte acionavel,
    # no prazo mais longo: em tres meses a distancia e de centesimos.
    abaixo = [] if sem_cotistas else fundos_abaixo_da_categoria(principal)
    if abaixo:
        rotulo = janela_do_bloco(principal)["rotulo"]
        lines.append(f"**Fundos de muita gente rendendo abaixo da categoria em {rotulo}**")
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
        lines.append("**Custo relativo à categoria**")
        lines.append("")
        lines.append(
            "Índice mediano, não taxa mediana: a taxa bruta entre casas mede o mix "
            "da prateleira, não o preço dos produtos."
        )
        lines.append("")
        lines.append("| Casa | Índice mediano | Caros | Baratos | Resgate até D+1 |")
        lines.append("|---|---:|---:|---:|---:|")
        for casa in custo["casas"]:
            lines.append(
                f"| {casa['label']} | {_indice_mediano(casa)} | "
                f"{_share(casa['share_caro'])} | {_share(casa['share_barato'])} | "
                f"{_share(casa['share_liquidez_imediata'])} |"
            )
        lines.append("")

    fora = _excluidos(blocos)
    for excluido in fora:
        lines.append(f"> Fora: {excluido['label']} - {excluido['reason']}")
    if fora:
        lines.append("")
    return lines


def _indice_mediano(casa: dict) -> str:
    """"1,65x", ou "-" quando a casa nao tem amostra comparavel."""
    indice = casa.get("indice_mediano")
    return "-" if indice is None else f"{indice:.2f}x".replace(".", ",")


def _html_tabela_rentabilidade(bloco: dict, sem_cotistas: str | None) -> str:
    linhas = ""
    for casa in bloco["casas"]:
        massa = casa["alocacao_abaixo_da_mediana"]
        cor = "#0ca30c" if (casa["gap"] or 0) > 0 else "#d03b3b"
        por_cotista = "" if sem_cotistas else (
            f'<td style="{_TD}text-align:right;"><strong>{_pct(casa["por_cotista"])}</strong></td>'
            f'<td style="{_TD}text-align:right;color:{cor};">{_pp(casa["gap"])}</td>'
        )
        linhas += (
            f'<tr><td style="{_TD}">{_esc(casa["label"])}</td>'
            f'<td style="{_TD}text-align:right;">{_pct(casa["mediana"])}</td>'
            f'<td style="{_TD}text-align:right;">{_pct(casa["por_pl"])}</td>'
            f"{por_cotista}"
            f'<td style="{_TD}">{massa["fundos"]} de {massa["de"]} '
            f'({_share(massa["share_fundos"])}) carregam '
            f'{_share(massa["share_pl"])} do PL &mdash; índice {_indice(massa)}</td></tr>'
        )
    cabecalho_cotistas = "" if sem_cotistas else (
        f'<th style="{_TH}text-align:right;">Ponderada por cotistas</th>'
        f'<th style="{_TH}text-align:right;">Gap</th>'
    )
    return f"""<table style="{_TABLE}"><thead><tr>
    <th style="{_TH}">Casa</th>
    <th style="{_TH}text-align:right;">Mediana dos fundos</th>
    <th style="{_TH}text-align:right;">Ponderada por PL</th>
    {cabecalho_cotistas}
    <th style="{_TH}">Abaixo da mediana da categoria</th>
  </tr></thead><tbody>{linhas}</tbody></table>"""


def _html_performance_section(report: RunReport) -> str:
    blocos = blocos_de_rentabilidade(report.performance_12m, report.performance)
    if not blocos:
        return ""
    principal = blocos[0]
    sem_cotistas = report.suprime(BLOCO_COTISTAS)

    prazos = " e ".join(
        _prazo(b, lambda texto: f"<strong>{_esc(texto)}</strong>") for b in blocos
    )
    tabelas = ""
    for bloco in blocos:
        janela = janela_do_bloco(bloco)
        tabelas += (
            f'<h3 style="{_H2}font-size:14px;">Em {_esc(janela["rotulo"])} '
            f'<span style="font-weight:400;color:#898781;">'
            f'({periodo_br(janela.get("de"), janela.get("ate"))})</span></h3>'
            f"{_html_tabela_rentabilidade(bloco, sem_cotistas)}"
        )

    fora = "".join(
        f'<p style="{_P}font-size:12.5px;color:#898781;">Fora: {_esc(e["label"])} &mdash; {_esc(e["reason"])}</p>'
        for e in _excluidos(blocos)
    )

    # O recorte acionavel do bloco. Ele existia so no markdown - quem recebia o
    # digest por e-mail via as tres medias da vitrine e nenhum fundo para agir.
    expostos = [] if sem_cotistas else fundos_abaixo_da_categoria(principal)
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
            f"abaixo da categoria em {_esc(janela_do_bloco(principal)['rotulo'])}</h3>"
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
  <h3 style="{_H2}font-size:14px;">Custo relativo à categoria</h3>
  <p style="{_P}font-size:12.5px;color:#898781;">Índice mediano, não taxa mediana: a taxa
    bruta entre casas mede o mix da prateleira, não o preço dos produtos.</p>
  <table style="{_TABLE}"><thead><tr>
    <th style="{_TH}">Casa</th>
    <th style="{_TH}text-align:right;">Índice mediano</th>
    <th style="{_TH}text-align:right;">Caros</th>
    <th style="{_TH}text-align:right;">Baratos</th>
    <th style="{_TH}text-align:right;">Resgate até D+1</th>
  </tr></thead><tbody>{linhas_custo}</tbody></table>"""

    nota_cotistas = (
        f"Sem ponderação por cotistas: {_esc(sem_cotistas)}." if sem_cotistas
        else "Gap positivo: o dinheiro grande está nos fundos melhores e o "
        "varejo nos piores. Negativo: o inverso. O sinal do gap muda conforme a "
        "janela."
    )
    return f"""
  <h2 style="{_H2}">Rentabilidade da vitrine</h2>
  <p style="{_P}">Prazos: {prazos}. Cota patrimonial do {_md_para_html(f"[informe diário da CVM]({CVM_INFORME_DIARIO.url})")},
    ex-provento, contra a mediana da mesma categoria no universo oficial, no mesmo prazo.
    É o retorno dos fundos que a casa exibe, com o peso que eles têm no
    <strong>mercado</strong> &mdash; não o retorno dos clientes dela.</p>
  {tabelas}
  <p style="{_P}font-size:12.5px;color:#898781;">{nota_cotistas}</p>
  {bloco_expostos}
  {bloco_custo}
  {fora}
"""


def _link(fonte: Fonte) -> str:
    return f"[{fonte.nome}]({fonte.url})"


def _linhas_de_fontes(report: RunReport, config: Config) -> list[str]:
    """Uma linha por fonte usada, em markdown: nome com link, e o que ela fornece."""
    linhas = []
    for fonte in fontes_do_relatorio(report, config):
        linha = f"{_link(fonte)}: {fonte.uso}."
        if fonte.url_dados:
            linha += f" Arquivos que o monitor lê: [{fonte.url_dados}]({fonte.url_dados})."
        linhas.append(linha)
    return linhas


def _urls_das_vitrines(config: Config, report: RunReport) -> dict[str, str]:
    return {
        s.platform: url
        for s in report.snapshots
        if (url := config.platform(s.platform).shelf_url)
    }


def render_markdown(report: RunReport, config: Config | None = None) -> str:
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    # Sem config, a identidade padrao do Config - a do monitor de fundos.
    identidade = config if config is not None else Config
    fontes_config = config if config is not None else Config()

    lines = [
        f"# {identidade.titulo_relatorio}",
        "",
        f"{identidade.descricao_relatorio} - execução de {generated}",
        "",
        "## Sumário",
        "",
    ]

    sentence, is_alert = _summary_sentence(report)
    lines.append(f"**{sentence}**" if is_alert else sentence)
    lines.append("")

    lines.append(
        "| Plataforma | Vitrine | Catálogo | Status | Entradas | Saídas | Alterações |"
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
            f"{_contagem(vitrine) if vitrine else 'sem vitrine'} | {_contagem(catalogo)} | "
            f"{snapshot.status.value} | "
            f"{len(entradas)} | {len(saidas)} | {len(alteracoes)} |"
        )
    lines.append("")
    if tem_catalogo_maior:
        lines.append(
            "_Vitrine: fundos de varejo na listagem da própria casa - o que o cliente "
            "vê, é a única coluna comparável entre casas. Catálogo: o que a casa "
            "administra, somando o Open Finance. Entradas, saídas e alterações contam "
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
    urls = _urls_das_vitrines(fontes_config, report)
    for diff in report.shelf_diffs:
        lines += _md_shelf_section(
            diff, report.snapshot_for(diff.platform), urls.get(diff.platform)
        )

    unavailable = [s for s in report.snapshots if s.status == SnapshotStatus.UNAVAILABLE]
    if unavailable:
        lines += ["## Fontes indisponíveis", ""]
        for snapshot in unavailable:
            lines.append(
                f"- {_platform_label(snapshot.platform)}: "
                f"{snapshot.error or 'motivo não informado'}"
            )
        lines.append("")

    fontes = _linhas_de_fontes(report, fontes_config)
    if fontes:
        lines += ["## Fontes", "", "Onde conferir cada número deste relatório:", ""]
        lines += [f"- {linha}" for linha in fontes]
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


def _mil(valor: float) -> str:
    """"R$ 101 mil" - o ticket por cotista novo."""
    return f"R$ {valor / 1000:,.0f} mil".replace(",", ".")


def _pct_pl(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:+.2f}%".replace(".", ",")


# Tendencias que somam "captando" na tabela por casa: o que o leitor compara
# entre vitrines e quanto dela ainda capta, quanto acelera e quanto perde.
_CAPTANDO = ("acelerando", "estavel", "desacelerando", "virou_entrada")


def _item_de_fundo(fundo: dict, com_cotistas: bool) -> str:
    """"Nome - +2.422,0 MM, +23.918 cotistas (R$ 101 mil por cotista novo) - XP, BTG".

    O ticket por cotista novo entra porque a CVM publica fluxo do fundo inteiro,
    nao so de varejo: em 24/09 os maiores captadores traziam de R$ 88 mil a R$
    433 mil por cotista novo, e so o numero deixa o leitor ver isso.
    """
    casas = ", ".join(_platform_label(p) for p in fundo["vitrines"])
    detalhe = ""
    if com_cotistas:
        detalhe = f", {_inteiro(fundo['variacao_cotistas'])} cotistas"
        ticket = fundo.get("ticket_por_cotista")
        if ticket:
            detalhe += f" ({_mil(ticket)} por cotista novo)"
    return f"{fundo['nome']} - {_mm(fundo['captacao_liquida'])}{detalhe} - {casas}"


def _prazo_da_captacao(periodo: dict) -> str:
    """"3 meses, de 01/07/2026 a 23/09/2026 (competências jul/2026, ...)"."""
    meses = periodo.get("meses_lidos") or []
    texto = periodo_br(periodo.get("de"), periodo.get("ate"))
    if meses:
        texto = (
            f"{meses_br(len(meses))}, {texto} (competências "
            f"{', '.join(competencia_br(m) for m in meses)})"
        )
    return texto


def _captacao_em_blocos(report: RunReport) -> list[dict]:
    """A secao de captacao como estrutura: markdown e HTML so a desenham.

    As duas versoes eram escritas a mao, lado a lado, e divergiam - a
    oportunidade de grade saia com os fundos no markdown e sem eles no HTML.
    Montada uma vez, a divergencia deixa de ser possivel.
    """
    flow = report.retail_flow
    if not (flow and flow.get("available")):
        return []
    sem_cnpj = report.suprime(BLOCO_CNPJ)
    com_cotistas = not report.suprime(BLOCO_COTISTAS)
    sem_proprio = report.suprime(BLOCO_PRODUTO_PROPRIO)
    periodo = flow["periodo"]
    blocos: list[dict] = [{
        "tipo": "p",
        "texto": (
            f"[Informe diário da CVM]({CVM_INFORME_DIARIO.url}). Prazo: "
            f"{_prazo_da_captacao(periodo)}. A CVM "
            "publica fluxo por **fundo**, nunca por distribuidor: os números por casa "
            "são o fluxo dos fundos que ela exibe, no mercado inteiro."
        ),
    }]

    leitura = leitura_da_captacao(flow, report.suprimidos)
    if leitura:
        blocos.append({"tipo": "lista", "titulo": None, "itens": leitura})

    # Composicao do saldo de cada casa.
    cabecalho = ["Casa", "Fundos com informe", "Captação líquida", "% do PL"]
    if not sem_cnpj:
        cabecalho += ["Compartilhados", "Só nesta vitrine"]
    if not sem_proprio:
        cabecalho += ["Produto próprio", "Terceiros"]
    linhas = []
    sem_gestora = 0
    for casa in flow["casas"]:
        linha = [
            casa["label"],
            f"{casa['com_informe']} de {casa['vitrine']} ({casa['cobertura']:.0%})",
            _mm(casa["captacao_liquida"]),
            _pct_pl(casa.get("sobre_pl")),
        ]
        if not sem_cnpj:
            linha += [
                _mm(casa.get("captacao_compartilhados", 0.0)),
                _mm(casa["captacao_exclusivos"]),
            ]
        if not sem_proprio:
            linha += [
                _mm(casa.get("captacao_propria", 0.0)),
                _mm(casa.get("captacao_terceiros", 0.0)),
            ]
        linhas.append(linha)
        sem_gestora += casa.get("sem_gestora", 0)
    blocos.append({"tipo": "tabela", "cabecalho": cabecalho, "linhas": linhas})
    blocos.append({"tipo": "nota", "texto": (
        ("Compartilhados e só nesta vitrine somam a captação líquida. " if not sem_cnpj else "")
        + "% do PL: captação líquida sobre o PL de início dos mesmos fundos."
        + (
            f" Sem o corte produto próprio x terceiros: {sem_proprio}." if sem_proprio
            else " Produto próprio é fundo da gestora da própria casa, pela mesma "
            "watchlist do radar CVM"
            + (f"; {sem_gestora} fundos sem gestora conhecida ficam fora das duas "
               "colunas" if sem_gestora else "")
            + "."
        )
    )})

    categorias = flow.get("por_categoria") or []
    if categorias:
        ids = [c["platform_id"] for c in flow["casas"]]
        blocos.append({"tipo": "p", "texto": (
            "**Por família de ativo** - captação líquida dos fundos de cada vitrine. "
            "Na última coluna cada fundo conta uma vez, mesmo exibido por várias casas."
        )})
        blocos.append({
            "tipo": "tabela",
            "cabecalho": ["Família", *[c["label"] for c in flow["casas"]], "Todas as vitrines"],
            "linhas": [
                [
                    linha["categoria"],
                    *[
                        _mm(linha["casas"][pid]["captacao_liquida"])
                        if linha["casas"].get(pid, {}).get("fundos") else "-"
                        for pid in ids
                    ],
                    f"{_mm(linha['universo']['captacao_liquida'])} "
                    f"({linha['universo']['fundos']} fundos)",
                ]
                for linha in categorias
            ],
        })

    competencias = flow.get("competencias") or []
    if competencias:
        blocos.append({"tipo": "p", "texto": (
            "**Tendência** - média diária de captação por competência. A média diária "
            "existe porque o mês corrente tem menos pregões que os fechados; comparar "
            "totais seria comparar calendários."
        )})
        linhas = []
        for platform_id, serie in (flow.get("serie_por_casa") or {}).items():
            por_competencia = {m["competencia"]: m for m in serie}
            celulas = []
            for competencia in competencias:
                mes = por_competencia.get(competencia)
                celulas.append(_mm(mes["media_diaria"]) + "/dia" if mes else "-")
            linhas.append([_platform_label(platform_id), *celulas])
        blocos.append({
            "tipo": "tabela",
            "cabecalho": ["Casa", *[_competencia(c) for c in competencias]],
            "linhas": linhas,
        })

    por_casa = flow.get("tendencia_por_casa") or {}
    linhas = []
    for casa in flow["casas"]:
        por_estado = por_casa.get(casa["platform_id"]) or {}
        total = sum(por_estado.values())
        if not total:
            continue
        parcela = lambda n: f"{n / total:.0%}"  # noqa: E731
        linhas.append([
            casa["label"], str(total),
            parcela(sum(por_estado.get(k, 0) for k in _CAPTANDO)),
            parcela(por_estado.get("acelerando", 0)),
            parcela(por_estado.get("virou_saida", 0)),
            parcela(por_estado.get("saida_persistente", 0)),
        ])
    if linhas:
        blocos.append({"tipo": "p", "texto": (
            "**Tendência dos fundos, por casa** - parcela dos fundos de cada vitrine "
            "(com PL de ao menos R$ 1 mi no início) em cada ritmo. Captando: "
            "acelerando, estável, desacelerando ou que virou entrada no último mês."
        )})
        blocos.append({
            "tipo": "tabela",
            "cabecalho": ["Casa", "Fundos", "Captando", "Acelerando", "Virou saída",
                          "Saída persistente"],
            "linhas": linhas,
        })

    criterio = "dinheiro e cotistas" if com_cotistas else "dinheiro"
    base = _platform_label(flow.get("baseline_id") or "xp")
    for titulo, chave in (
        (f"Maiores captações - fundos ganhando {criterio}", "top_captacao"),
        (f"Maiores resgates - fundos perdendo {criterio}", "top_resgate"),
        (f"Na vitrine da {base}, perdendo {criterio}", "baseline_perdendo"),
        ("Acelerando - captando mais por dia que antes", "acelerando"),
        ("Viraram saída - captavam e passaram a perder", "virou_saida"),
    ):
        fundos = flow.get(chave) or []
        if fundos:
            blocos.append({
                "tipo": "lista", "titulo": titulo,
                "itens": [_item_de_fundo(f, com_cotistas) for f in fundos[:_FLOW_LISTED]],
            })

    if flow.get("total_estruturais"):
        blocos.append({"tipo": "nota", "texto": (
            f"{contagem(flow['total_estruturais'], 'fundo', 'fundos')} com movimento "
            "estrutural (fluxo maior "
            "que o próprio patrimônio, ou fundo esvaziado) ficam fora de todas as "
            "somas: é dinheiro trocando de veículo, não demanda."
        )})

    # A grade e "fundo que capta fora da vitrine da XP", por CNPJ: sai junto.
    grade = None if sem_cnpj else report.grade_opportunities
    if grade and grade.get("available"):
        cotistas = (
            f" e {_inteiro(grade['cotistas_transferivel'])} cotistas" if com_cotistas else ""
        )
        blocos.append({"tipo": "h3", "texto": "Oportunidade de grade"})
        blocos.append({"tipo": "p", "texto": (
            f"{grade['total']} fundos captam varejo fora da vitrine da XP, mas "
            f"{grade['total_produto_do_concorrente']} são produto próprio da casa "
            f"que os exibe e não se transferem. Restam **{grade['total_transferiveis']} "
            f"fundos, {_mm(grade['captacao_transferivel'])}**{cotistas} - "
            f"{grade['total_operacionais']} de gestora que a XP já distribui, "
            f"{grade['total_comerciais']} exigem abrir gestora."
        )})
        itens = []
        for fundo in (grade.get("mais_varejo") or [])[:_FLOW_LISTED]:
            esforco = (
                "gestora já distribuída" if fundo["gestora_ja_distribuida"]
                else "abrir gestora"
            )
            detalhe = (
                f", {_inteiro(fundo['variacao_cotistas'])} cotistas" if com_cotistas else ""
            )
            itens.append(
                f"{fundo['nome']} - {_mm(fundo['captacao_liquida'])}{detalhe} - {esforco}"
            )
        if itens:
            blocos.append({"tipo": "lista", "titulo": None, "itens": itens})
    return blocos


def _md_flow_section(report: RunReport) -> list[str]:
    blocos = _captacao_em_blocos(report)
    if not blocos:
        return []
    lines = ["## Captação dos fundos de vitrine", ""]
    for bloco in blocos:
        tipo = bloco["tipo"]
        if tipo == "p":
            lines += [bloco["texto"], ""]
        elif tipo == "nota":
            lines += [f"_{bloco['texto']}_", ""]
        elif tipo == "h3":
            lines += [f"### {bloco['texto']}", ""]
        elif tipo == "lista":
            if bloco["titulo"]:
                lines += [f"**{bloco['titulo']}**", ""]
            lines += [f"- {item}" for item in bloco["itens"]] + [""]
        elif tipo == "tabela":
            colunas = len(bloco["cabecalho"])
            lines.append("| " + " | ".join(bloco["cabecalho"]) + " |")
            lines.append("|---|" + "---:|" * (colunas - 1))
            lines += ["| " + " | ".join(linha) + " |" for linha in bloco["linhas"]]
            lines.append("")
    return lines


def _cor_do_valor(texto: str) -> str:
    """Verde para saldo positivo, vermelho para negativo - so em celula de valor."""
    if texto.startswith("+"):
        return "color:#2c6e4b;"
    if texto.startswith("-") and len(texto) > 1:
        return "color:#a3402b;"
    return ""


def _html_flow_section(report: RunReport) -> str:
    blocos = _captacao_em_blocos(report)
    if not blocos:
        return ""
    parts = [f'<h2 style="{_H2}">Captação dos fundos de vitrine</h2>']
    for bloco in blocos:
        tipo = bloco["tipo"]
        if tipo == "p":
            parts.append(f'<p style="{_P}">{_md_para_html(bloco["texto"])}</p>')
        elif tipo == "nota":
            parts.append(
                f'<p style="{_P}font-size:12.5px;color:#898781;">'
                f'{_md_para_html(bloco["texto"])}</p>'
            )
        elif tipo == "h3":
            parts.append(f'<h3 style="{_H3}">{_esc(bloco["texto"])}</h3>')
        elif tipo == "lista":
            if bloco["titulo"]:
                parts.append(
                    f'<p style="{_P}margin-bottom:6px;"><strong>{_esc(bloco["titulo"])}'
                    "</strong></p>"
                )
            itens = "".join(
                f'<li style="{_LI}">{_md_para_html(item)}</li>' for item in bloco["itens"]
            )
            parts.append(f'<ul style="margin:0 0 14px;padding-left:18px;">{itens}</ul>')
        elif tipo == "tabela":
            cabecalho = "".join(
                f'<th style="{_TH}{"" if i == 0 else "text-align:right;"}">{_esc(t)}</th>'
                for i, t in enumerate(bloco["cabecalho"])
            )
            corpo = "".join(
                "<tr>" + "".join(
                    f'<td style="{_TD}{"" if i == 0 else "text-align:right;" + _cor_do_valor(c)}">'
                    f"{_esc(c)}</td>"
                    for i, c in enumerate(linha)
                ) + "</tr>"
                for linha in bloco["linhas"]
            )
            parts.append(
                f'<table style="{_TABLE}"><thead><tr>{cabecalho}</tr></thead>'
                f"<tbody>{corpo}</tbody></table>"
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
        f'<li style="{_LI}color:#898781;">... e mais '
        f'{contagem(total - len(items), "fundo", "fundos")}</li>'
        if total > len(items)
        else ""
    )
    return f'<ul style="margin:0 0 14px;padding-left:20px;">{rows}{more}</ul>'


def _html_shelf_section(
    diff: ShelfDiff, snapshot: ShelfSnapshot | None, url: str | None = None
) -> str:
    rotulo = _esc(_platform_label(diff.platform))
    titulo = (
        f'<a href="{_esc(url)}" style="color:#0b0b0b;">{rotulo}</a>' if url else rotulo
    )
    parts = [f'<h3 style="{_H3}">{titulo}</h3>']

    if snapshot and snapshot.status == SnapshotStatus.UNAVAILABLE:
        parts.append(
            f'<p style="{_P}">Coleta indisponível: '
            f'{_esc(snapshot.error or "motivo não informado")}.</p>'
        )
        return "".join(parts)

    tamanho = _frase_de_tamanho(snapshot)
    if diff.nova_linha_de_base:
        parts.append(
            f'<p style="{_P}">Nova linha de base: {tamanho}. '
            f"{_esc(_capitalizar(diff.suspect_reason))}.</p>"
        )
        return "".join(parts)
    if diff.is_first_run:
        parts.append(
            f'<p style="{_P}">Primeira coleta: {tamanho}, como linha de base.</p>'
        )
        return "".join(parts)

    if diff.suspect:
        parts.append(
            f'<p style="{_P}background:#fff8e6;border-left:3px solid #fab219;'
            f'padding:8px 10px;border-radius:4px;"><strong>Coleta suspeita</strong> '
            f"({_esc(diff.suspect_reason)}). Saídas omitidas para evitar falso churn.</p>"
        )

    entradas, saidas, alteracoes, so_catalogo = _mudancas_de_vitrine(diff, snapshot)
    cauda = (
        f" Mais {contagem(so_catalogo, 'mudança', 'mudanças')} só no catálogo Open Finance, "
        "fora da vitrine."
        if so_catalogo
        else ""
    )

    if not (entradas or saidas or alteracoes):
        parts.append(
            f'<p style="{_P}">Sem mudanças na vitrine: {tamanho}.{cauda}</p>'
        )
        return "".join(parts)

    parts.append(
        f'<p style="{_P}">{_movimento(entradas, saidas, alteracoes)} na vitrine - '
        f"{tamanho}.{cauda}</p>"
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
            f'<p style="{_P}margin-bottom:6px;"><strong>Saíram da vitrine</strong></p>'
        )
        parts.append(
            _html_list([_esc(f.name) for f in saidas[:_MAX_LISTED]], len(saidas))
        )

    if alteracoes:
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Mudanças de condições</strong></p>'
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
    parts = [f'<h2 style="{_H2}">Radar CVM &mdash; visão antecipada</h2>']

    if not snapshot or snapshot.status == SnapshotStatus.UNAVAILABLE:
        error = snapshot.error if snapshot else "extrato não coletado"
        parts.append(f'<p style="{_P}">Extrato da CVM indisponível: {_esc(error)}.</p>')
        return "".join(parts)

    pre_op_total = sum(1 for f in snapshot.funds if f.is_pre_operational)
    parts.append(
        f'<p style="{_P}">{contagem(len(snapshot.funds), "fundo", "fundos")} de gestoras '
        f"ligadas às casas monitoradas, {_contagem(pre_op_total)} em fase pré-operacional "
        "(registrados e ainda não "
        f"lançados). Fonte: {_md_para_html(_link(CVM_CADASTRO))}.</p>"
    )

    rows = "".join(
        f'<tr><td style="{_TD}">{_esc(_platform_label(house))}</td>'
        f'<td style="{_TD}text-align:right;">{_contagem(len(funds))}</td>'
        f'<td style="{_TD}text-align:right;">'
        f'{_contagem(sum(1 for f in funds if f.is_pre_operational))}</td></tr>'
        for house, funds in sorted(snapshot.by_house().items())
    )
    parts.append(
        f'<table style="{_TABLE}"><thead><tr>'
        f'<th style="{_TH}">Casa</th>'
        f'<th style="{_TH}text-align:right;">Registrados</th>'
        f'<th style="{_TH}text-align:right;">Pré-operacionais</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )

    diff = report.cvm_diff
    if not diff:
        return "".join(parts)

    if diff.new_registrations:
        title = (
            "Fundos registrados e ainda não lançados"
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
            f'<p style="{_P}margin-bottom:6px;"><strong>Entraram em operação</strong></p>'
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
    if gaps.get("nao_se_aplica"):
        parts.append(
            f'<p style="{_P}"><strong>Não se aplica a este monitor:</strong> '
            f"{_esc(gaps.get('reason'))}.</p>"
        )
        return "".join(parts)
    if not gaps.get("available"):
        parts.append(
            f'<p style="{_P}"><strong>Análise suprimida nesta execução:</strong> '
            f"{_esc(gaps.get('reason'))}.</p>"
        )
        return "".join(parts)

    if not gaps["total_gaps"] and not gaps.get("gaps_liquidez"):
        parts.append(
            f'<p style="{_P}">'
            + _esc(
                frase_do_funil(gaps)
                or "Nenhum gap: toda vitrine concorrente coletada está contida na "
                "vitrine da XP."
            )
            + "</p>"
        )
    else:
        parts.append(f'<p style="{_P}">{_esc(_abertura_dos_gaps(gaps))}</p>')
        items = ""
        for fund in gaps["funds"][:_GAP_LISTED]:
            captacao = fund.get("captacao_liquida")
            fluxo = f" - {_mm(captacao)} de captação" if captacao is not None else ""
            esforco = f" - {fund['esforco']}" if fund.get("esforco") else ""
            items += (
                f'<li style="{_LI}">{_esc(fund["name"])} ({_esc(fund["cnpj_display"])}) '
                f"- {_esc(fund['category'])} - casas: {_esc(fund['houses_label'])}"
                f"{_esc(fluxo)}{_esc(esforco)}</li>"
            )
        remaining = len(gaps["funds"]) - min(_GAP_LISTED, len(gaps["funds"]))
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
                    f'<td style="{_TD}text-align:right;">{entry["gap_count"]}</td>'
                    f'<td style="{_TD}text-align:right;">{entry.get("cobertos", 0)}</td></tr>'
                )
            parts.append(
                f'<table style="{_TABLE}"><thead><tr>'
                f'<th style="{_TH}">Categoria</th>'
                f'<th style="{_TH}text-align:right;">XP</th>'
                f'<th style="{_TH}">Melhor concorrente</th>'
                f'<th style="{_TH}text-align:right;">Gaps reais</th>'
                f'<th style="{_TH}text-align:right;">Cobertos</th>'
                f"</tr></thead><tbody>{rows}</tbody></table>"
            )

    cobertos = gaps.get("cobertos") or []
    if cobertos:
        parts.append(
            f'<p style="{_P}"><strong>Já cobertos</strong> ({milhar(len(cobertos))}, '
            "os primeiros; a lista inteira com a evidência está na aba 'Gaps "
            "cobertos' da planilha):</p>"
        )
        items = "".join(
            f'<li style="{_LI}">{_esc(_linha_do_coberto(fund))}</li>'
            for fund in cobertos[:_GAP_COBERTOS_LISTED]
        )
        parts.append(f'<ul style="margin:0 0 12px;padding-left:20px;">{items}</ul>')

    notes = " ".join(_esc(note) for note in _gap_footnotes(gaps))
    parts.append(f'<p style="font-size:12px;color:#898781;margin:0 0 12px;">{notes}</p>')
    return "".join(parts)


def render_html(report: RunReport, config: Config | None = None) -> str:
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    identidade = config if config is not None else Config
    fontes_config = config if config is not None else Config()
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
            f'<td style="{_TD}text-align:right;">'
            f'{_contagem(vitrine) if vitrine else "sem vitrine"}</td>'
            f'<td style="{_TD}text-align:right;color:#898781;">{_contagem(catalogo)}</td>'
            f'<td style="{_TD}">{_esc(snapshot.status.value)}</td>'
            f'<td style="{_TD}text-align:right;">{len(entradas)}</td>'
            f'<td style="{_TD}text-align:right;">{len(saidas)}</td>'
            f'<td style="{_TD}text-align:right;">{len(alteracoes)}</td></tr>'
        )

    nota_base = (
        f'<p style="font-size:12px;color:#898781;margin:-6px 0 18px;">'
        "Vitrine: fundos de varejo na listagem da própria casa &mdash; o que o cliente vê, "
        "é a única coluna comparável entre casas. Catálogo: o que a casa administra, "
        "somando o Open Finance. Entradas, saídas e alterações contam a vitrine.</p>"
        if tem_catalogo_maior
        else ""
    )

    urls = _urls_das_vitrines(fontes_config, report)
    shelf_sections = "".join(
        _html_shelf_section(diff, report.snapshot_for(diff.platform), urls.get(diff.platform))
        for diff in report.shelf_diffs
    )
    itens_fontes = "".join(
        f'<li style="{_LI}">{_md_para_html(linha)}</li>'
        for linha in _linhas_de_fontes(report, fontes_config)
    )
    bloco_fontes = (
        f'<h2 style="{_H2}">Fontes</h2>'
        f'<p style="{_P}">Onde conferir cada número deste relatório:</p>'
        f'<ul style="margin:0 0 14px;padding-left:18px;">{itens_fontes}</ul>'
        if itens_fontes else ""
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
<title>{_esc(identidade.titulo_relatorio)}</title></head>
<body style="{_EMAIL_BODY}">
<div style="{_EMAIL_CARD}">
  <h1 style="font-size:19px;font-weight:600;margin:0 0 4px;">{_esc(identidade.titulo_relatorio)}</h1>
  <p style="font-size:13px;color:#898781;margin:0 0 18px;">
    {_esc(identidade.descricao_relatorio)} &middot; execução de {_esc(generated)}</p>

  <p style="{_P}">{summary}</p>

  <table style="{_TABLE}"><thead><tr>
    <th style="{_TH}">Plataforma</th>
    <th style="{_TH}text-align:right;">Vitrine</th>
    <th style="{_TH}text-align:right;">Catálogo</th>
    <th style="{_TH}">Status</th>
    <th style="{_TH}text-align:right;">Entradas</th>
    <th style="{_TH}text-align:right;">Saídas</th>
    <th style="{_TH}text-align:right;">Alterações</th>
  </tr></thead><tbody>{rows}</tbody></table>
  {nota_base}

  {_html_gaps_section(_gaps_ranqueados(report))}

  {bloco_ticket}

  {_html_flow_section(report)}
  {_html_performance_section(report)}

  {_html_cvm_section(report)}

  <h2 style="{_H2}">Vitrines</h2>
  {shelf_sections}

  {bloco_fontes}
</div>
</body></html>
"""


def render_digest(config: Config, report: RunReport) -> tuple[Path, Path]:
    """Grava digest markdown e HTML. Devolve (path_md, path_html)."""
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    today: date = report.generated_at.date()

    md_path = config.reports_dir / f"digest-{today.isoformat()}.md"
    md_path.write_text(render_markdown(report, config), encoding="utf-8")

    html_path = config.reports_dir / f"digest-{today.isoformat()}.html"
    html_path.write_text(render_html(report, config), encoding="utf-8")

    return md_path, html_path
