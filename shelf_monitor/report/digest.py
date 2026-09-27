"""Digest de mudancas: o que mudou desde a ultima execucao.

Gera markdown (leitura no repo, diff de git legivel) e HTML com CSS inline,
sem JS e com largura contida - pronto para colar num e-mail.
"""
from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path

from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.familias import (
    LIMIAR_DISTANCIA_CARTEIRA,
    LIMIAR_ESPELHO,
    LIMIAR_SOBREPOSICAO,
)
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
from shelf_monitor.texto import (
    competencia_br,
    contagem,
    data_br,
    meses_br,
    milhar,
    periodo_br,
)
from shelf_monitor.report.shared.estilo import (
    COMPONENTE_1,
    RAMPA,
    TINTA,
    TINTA_2,
    contorno_da_casa,
    cor_da_casa,
)
from shelf_monitor.report.shared.fontes import (
    CVM_CADASTRO,
    CVM_CDA,
    CVM_EXTRATO,
    CVM_INFORME_DIARIO,
    Fonte,
    fontes_do_relatorio,
)
# A casca, a tabela e os estilos do e-mail moram em shared/email_xp.py (os
# relatorios de ETF usam os mesmos). `_EMAIL_BODY`, `_EMAIL_CARD`, `_H2`, `_LI`,
# `_P`, `_TABLE`, `_TD`, `_TH`, `_esc`, `_md_para_html` e `_cor_do_valor` seguem
# importaveis daqui pelos nomes de antes.
from shelf_monitor.report.shared.email_xp import (  # noqa: F401
    CHAMADA as _CHAMADA,
    CINZA as _CINZA,
    EMAIL_BODY as _EMAIL_BODY,
    EMAIL_CARD as _EMAIL_CARD,
    H2 as _H2,
    H3 as _H3,
    LEGENDA as _LEGENDA,
    LI as _LI,
    LINK as _LINK,
    MIUDO_CINZA as _MIUDO_CINZA,
    NOTA as _NOTA,
    P as _P,
    RETRANCA as _RETRANCA,
    TABLE as _TABLE,
    TD as _TD,
    TH as _TH,
    TRACO as _TRACO,
    UL as _UL,
    casca as _casca_do_email,
    cor_do_valor as _cor_do_valor,
    esc as _esc,
    faixa as _faixa,
    fontes_html as _fontes_html,
    fundo_html as _fundo_html,
    legenda as _legenda,
    mais as _mais,
    md_para_html as _md_para_html,
    pontuado as _linha,
    tabela as _tabela,
)
from shelf_monitor.report.shared.graficos_email import (
    Celula,
    Linha,
    LinhaDeCalor,
    Oposta,
    Segmento,
    Serie,
    barra_segmentada,
    barras,
    barras_divergentes,
    barras_opostas,
    colunas_por_casa,
    figura,
    grades_por_casa,
    mapa_de_calor,
    numeros_em_destaque,
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
    """Uma linha por casa, so numero: o texto "258 de 552 (47%) carregam 23% do
    PL - indice 0,48" virou quatro colunas."""
    cabecalho = ["Casa", "Mediana dos fundos", "Ponderada por PL"]
    if not sem_cotistas:
        cabecalho += ["Ponderada por cotistas", "Gap"]
    cabecalho += ["Fundos abaixo", "% fundos", "% PL", "Índice"]
    linhas, negrito = [], []
    for casa in bloco["casas"]:
        massa = casa["alocacao_abaixo_da_mediana"]
        linha = [_esc(casa["label"]), _pct(casa["mediana"]), _pct(casa["por_pl"])]
        if not sem_cotistas:
            linha += [f"<strong>{_pct(casa['por_cotista'])}</strong>", _pp(casa["gap"])]
        linha += [
            f"{massa['fundos']} de {massa['de']}", _share(massa["share_fundos"]),
            _share(massa["share_pl"]), _indice(massa),
        ]
        linhas.append(linha)
        negrito.append(_e_base(casa.get("platform_id")))
    colunas = range(1, len(cabecalho))
    return _tabela(
        cabecalho, linhas, direita=colunas, base=negrito, compacta=True,
        sinal={4} if not sem_cotistas else (),
    )


def _html_performance_section(report: RunReport, omitir: frozenset = frozenset()) -> str:
    blocos = blocos_de_rentabilidade(report.performance_12m, report.performance)
    if not blocos:
        return ""
    principal = blocos[0]
    sem_cotistas = report.suprime(BLOCO_COTISTAS)
    partes = [f'<h2 style="{_RETRANCA}">{_TRACO}Rentabilidade da vitrine</h2>']

    # Sem a figura (orcamento do e-mail), a ressalva do rodape dela sobe para
    # uma linha de abertura - e o que impede ler o numero como retorno do cliente.
    if "rentabilidade" in omitir:
        partes.append(
            f'<p style="{_P}">Cota ex-provento do '
            f'{_md_para_html(f"[informe diário da CVM]({CVM_INFORME_DIARIO.url})")}, '
            "com o peso de mercado: não é o retorno dos clientes da casa.</p>"
        )
    for i, bloco in enumerate(blocos):
        janela = janela_do_bloco(bloco)
        partes.append(
            f'<h3 style="{_H3}">Em {_esc(janela["rotulo"])} '
            f'<span style="font-weight:400;{_CINZA}">'
            f'({periodo_br(janela.get("de"), janela.get("ate"))})</span></h3>'
        )
        if "rentabilidade" not in omitir:
            partes.append(_figura_da_rentabilidade(bloco, primeira=i == 0))
        partes.append(_html_tabela_rentabilidade(bloco, sem_cotistas))

    indice = (
        "Índice = % do PL ÷ % dos fundos abaixo da mediana da categoria; abaixo de 1, "
        "o dinheiro evita os piores."
    )
    gap = (
        f"Sem ponderação por cotistas: {_esc(sem_cotistas)}." if sem_cotistas
        else "Gap = ponderada por PL − por cotistas; positivo: o dinheiro grande está "
        "nos fundos melhores. O sinal muda com a janela."
    )
    partes.append(f'<p style="{_NOTA}">{gap} {indice}</p>')
    if "pl_abaixo" not in omitir:
        partes.append(_figura_do_pl_abaixo_da_mediana(principal))

    # O recorte acionavel do bloco: fundos com muita gente atras dos pares.
    expostos = [] if sem_cotistas else fundos_abaixo_da_categoria(principal)
    if expostos:
        partes.append(
            f'<h3 style="{_H3}">Fundos de muita gente rendendo '
            f"abaixo da categoria em {_esc(janela_do_bloco(principal)['rotulo'])}</h3>"
        )
        partes.append(_tabela(
            ["Fundo", "Cotistas", "Retorno", "Mediana cat.", "Atrás", "Casas"],
            [
                [
                    _fundo_html(f["nome"], format_cnpj(f["cnpj"]) if f.get("cnpj") else None),
                    _contagem(f["cotistas"]), _pct(f["retorno"]),
                    _pct(f["mediana_categoria"]), _pp(f["excesso"]),
                    _esc(_casas_curtas(f["casas"])),
                ]
                for f in expostos[:_MAX_EXPOSTOS]
            ],
            direita={1, 2, 3, 4},
        ))

    custo = report.custo
    if custo and custo.get("available"):
        partes.append(f'<h3 style="{_H3}">Custo relativo à categoria</h3>')
        figura_do_custo = "" if "custo" in omitir else _figura_do_custo(report)
        partes.append(figura_do_custo or (
            f'<p style="{_NOTA}">Índice, não taxa: a taxa bruta mede o mix da '
            "prateleira. 1,00x = mediana da categoria.</p>"
        ))
        partes.append(_tabela(
            ["Casa", "Índice mediano", "Caros", "Baratos", "Resgate até D+1"],
            [
                [
                    _esc(casa["label"]), _indice_mediano(casa), _share(casa["share_caro"]),
                    _share(casa["share_barato"]), _share(casa["share_liquidez_imediata"]),
                ]
                for casa in custo["casas"]
            ],
            direita={1, 2, 3, 4},
            base=[_e_base(casa.get("platform_id")) for casa in custo["casas"]],
        ))

    fora = _excluidos(blocos)
    if fora:
        partes.append(
            f'<p style="{_NOTA}">Fora: '
            + "; ".join(
                f"{_esc(e['label'])}, {_esc(_motivo_curto(e['reason']))}" for e in fora
            )
            + ".</p>"
        )
    return "".join(partes)


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

    O e-mail le menos texto que o markdown (secao 5 da linguagem visual), entao
    alguns blocos carregam chaves so do HTML, que o markdown ignora:
    `curto` (a versao de uma linha; "" = o HTML omite o bloco), `figura` (a
    figura que ocupa o lugar do paragrafo), `fundos` (a lista como linhas de
    tabela) e `numeros` (a frase de contagem como numeros em destaque).
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
        "figura": "captacao",
        "curto": (
            f"[Informe diário da CVM]({CVM_INFORME_DIARIO.url}) · "
            f"{_janela_da_captacao(periodo)} · fluxo por fundo, não por distribuidor."
        ),
    }]

    # A leitura em frases narra o grafico e a tabela de composicao: no e-mail,
    # que tem os dois, ela sai.
    leitura = leitura_da_captacao(flow, report.suprimidos)
    if leitura:
        blocos.append({"tipo": "lista", "titulo": None, "itens": leitura, "curto": ""})

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
    blocos.append({
        "tipo": "tabela", "id": "composicao", "cabecalho": cabecalho, "linhas": linhas,
    })
    curto = _linha(
        "Compartilhados + só nesta vitrine = captação líquida" if not sem_cnpj else "",
        "% do PL: sobre o PL de início",
        "Produto próprio: gestora da própria casa (watchlist do radar CVM)"
        + (f", {sem_gestora} sem gestora conhecida fora" if sem_gestora else "")
        if not sem_proprio else "",
    ) + "." + (f" Sem o corte produto próprio x terceiros: {sem_proprio}." if sem_proprio else "")
    blocos.append({"curto": curto, "tipo": "nota", "texto": (
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
        ), "curto": (
            "**Por família de ativo** · na última coluna, cada fundo conta uma vez"
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
        ), "figura": "mensal", "curto": "**Tendência** · média diária por competência"})
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
            "id": "mensal",
            "cabecalho": ["Casa", *[_competencia(c) for c in competencias]],
            "linhas": linhas,
        })

    por_casa = flow.get("tendencia_por_casa") or {}
    linhas = []
    # As mesmas parcelas em numero, para o HTML pintar o mapa de calor sem
    # reler o texto da celula; e de quem e cada linha, para a casa-base.
    parcelas: list[list[float]] = []
    casas_do_ritmo: list[str] = []
    for casa in flow["casas"]:
        por_estado = por_casa.get(casa["platform_id"]) or {}
        total = sum(por_estado.values())
        if not total:
            continue
        numeros = [
            sum(por_estado.get(k, 0) for k in _CAPTANDO) / total,
            por_estado.get("acelerando", 0) / total,
            por_estado.get("virou_saida", 0) / total,
            por_estado.get("saida_persistente", 0) / total,
        ]
        linhas.append([casa["label"], str(total), *(f"{n:.0%}" for n in numeros)])
        parcelas.append(numeros)
        casas_do_ritmo.append(casa["platform_id"])
    if linhas:
        blocos.append({"tipo": "p", "texto": (
            "**Tendência dos fundos, por casa** - parcela dos fundos de cada vitrine "
            "(com PL de ao menos R$ 1 mi no início) em cada ritmo. Captando: "
            "acelerando, estável, desacelerando ou que virou entrada no último mês."
        ), "figura": "ritmo", "curto": (
            "**Tendência dos fundos, por casa** · % dos fundos em cada ritmo"
        )})
        blocos.append({
            "tipo": "tabela",
            "id": "ritmo",
            "cabecalho": ["Casa", "Fundos", "Captando", "Acelerando", "Virou saída",
                          "Saída persistente"],
            "linhas": linhas,
            "parcelas": parcelas,
            "casas": casas_do_ritmo,
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
                "fundos": fundos[:_FLOW_LISTED], "com_cotistas": com_cotistas,
            })

    if flow.get("total_estruturais"):
        blocos.append({"tipo": "nota", "texto": (
            f"{contagem(flow['total_estruturais'], 'fundo', 'fundos')} com movimento "
            "estrutural (fluxo maior "
            "que o próprio patrimônio, ou fundo esvaziado) ficam fora de todas as "
            "somas: é dinheiro trocando de veículo, não demanda."
        ), "curto": (
            f"{contagem(flow['total_estruturais'], 'fundo', 'fundos')} com movimento "
            "estrutural (fluxo maior que o PL, ou fundo esvaziado) fora das somas: "
            "troca de veículo, não demanda."
        )})

    # A grade e "fundo que capta fora da vitrine da XP", por CNPJ: sai junto.
    grade = None if sem_cnpj else report.grade_opportunities
    if grade and grade.get("available"):
        cotistas = (
            f" e {_inteiro(grade['cotistas_transferivel'])} cotistas" if com_cotistas else ""
        )
        blocos.append({"tipo": "h3", "texto": "Oportunidade de grade"})
        numeros = [
            (milhar(grade["total_transferiveis"]), "fundos transferíveis para a XP", True),
            (_mm(grade["captacao_transferivel"]), "captação deles"),
        ]
        if com_cotistas:
            numeros.append((_inteiro(grade["cotistas_transferivel"]), "cotistas deles"))
        numeros += [
            (milhar(grade["total_operacionais"]), "de gestora que a XP já distribui"),
            (milhar(grade["total_comerciais"]), "exigem abrir gestora"),
            (f"{milhar(grade['total_produto_do_concorrente'])} de {milhar(grade['total'])}",
             "produto próprio do concorrente: não se transfere"),
        ]
        blocos.append({"tipo": "p", "texto": (
            f"{grade['total']} fundos captam varejo fora da vitrine da XP, mas "
            f"{grade['total_produto_do_concorrente']} são produto próprio da casa "
            f"que os exibe e não se transferem. Restam **{grade['total_transferiveis']} "
            f"fundos, {_mm(grade['captacao_transferivel'])}**{cotistas} - "
            f"{grade['total_operacionais']} de gestora que a XP já distribui, "
            f"{grade['total_comerciais']} exigem abrir gestora."
        ), "numeros": numeros})
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
            blocos.append({
                "tipo": "lista", "titulo": None, "itens": itens,
                "fundos": (grade.get("mais_varejo") or [])[:_FLOW_LISTED],
                "com_cotistas": com_cotistas, "com_esforco": True,
            })
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


def _html_tabela_de_bloco(bloco: dict, base: str = "") -> str:
    """A tabela de um bloco de captacao: primeira coluna texto, o resto numero,
    com a cor do sinal. Mais de cinco colunas sai compacta, para caber nos 600 px
    do e-mail no computador; no telefone ela rola dentro da propria caixa.

    Na composicao, a cobertura ("606 de 636 (95%)") desce para baixo do nome da
    casa, miuda: como coluna, ela quebrava em tres linhas e alongava a tabela.
    """
    cabecalho = list(bloco["cabecalho"])
    linhas = [[_esc(c) for c in linha] for linha in bloco["linhas"]]
    if bloco.get("id") == "composicao":
        del cabecalho[1]
        linhas = [
            [f'{casa}<br><small style="{_MIUDO_CINZA}">{cobertura} com informe</small>',
             *resto]
            for casa, cobertura, *resto in linhas
        ]
    # "+3.489,5 MM (345 fundos)": a contagem desce para baixo do valor, miuda,
    # e o valor fica numa linha so.
    contagem_miuda = rf'\1<br><small style="{_MIUDO_CINZA}">\2</small>'
    linhas = [
        [re.sub(r"^(.+?) \(([\d.]+ fundos?)\)$", contagem_miuda, c) for c in linha]
        for linha in linhas
    ]
    colunas = range(1, len(cabecalho))
    # A linha da casa-base: o rotulo dela abre a linha (antes da cobertura).
    marcadas = [bool(base) and linha[0].split("<br>")[0] == _esc(base) for linha in linhas]
    return _tabela(
        cabecalho, linhas, direita=colunas, sinal=colunas, base=marcadas,
        compacta=len(cabecalho) > 5,
    )


def _tabela_de_fundos_da_captacao(bloco: dict, janela: str) -> str:
    """Uma lista de fundos da captacao como tabela: Fundo | Captacao | Cotistas | Casas.

    O ticket por cotista novo vai miudo sob os cotistas, quando e curto: acima
    de R$ 999 mil ele e o sinal de fluxo institucional, e o numero cheio fica na
    planilha.
    """
    com_cotistas = bloco.get("com_cotistas")
    com_esforco = bloco.get("com_esforco")
    cabecalho = ["Fundo", f"Captação {janela}".strip()]
    if com_cotistas:
        cabecalho.append("Cotistas")
    cabecalho.append("Casas")
    if com_esforco:
        cabecalho.append("Esforço")
    linhas = []
    for fundo in bloco["fundos"]:
        # Sem o CNPJ: nas listas de mercado o nome basta, e o CNPJ esta na
        # planilha - cada byte conta no orcamento do e-mail.
        linha = [_esc(fundo["nome"]), _mm(fundo["captacao_liquida"])]
        if com_cotistas:
            cotistas = _inteiro(fundo["variacao_cotistas"])
            ticket = fundo.get("ticket_por_cotista")
            if ticket and len(_mil(ticket)) <= len("R$ 999 mil"):
                cotistas += f'<br><small style="{_MIUDO_CINZA}">{_mil(ticket)} cada</small>'
            linha.append(cotistas)
        linha.append(_esc(_casas_curtas(fundo.get("vitrines") or [])))
        if com_esforco:
            linha.append(
                "gestora já distribuída" if fundo.get("gestora_ja_distribuida")
                else "abrir gestora"
            )
        linhas.append(linha)
    larguras = {0: 46, 1: 17}
    if com_cotistas:
        larguras[2] = 14
    return _tabela(
        cabecalho, linhas, direita={1, 2} if com_cotistas else {1}, sinal={1},
        larguras=larguras,
    )


def _html_flow_section(report: RunReport, omitir: frozenset = frozenset()) -> str:
    """A captacao no e-mail: figura no lugar do paragrafo, tabela no lugar da lista.

    Os blocos sao os mesmos do markdown (`_captacao_em_blocos`); o HTML le as
    chaves so dele (`figura`, `curto`, `fundos`, `numeros`) e desenha menos
    texto: a leitura em frases sai, porque o grafico e a tabela de composicao
    mostram os mesmos numeros.
    """
    blocos = _captacao_em_blocos(report)
    if not blocos:
        return ""
    flow = report.retail_flow
    janela = meses_br(len((flow.get("periodo") or {}).get("meses_lidos") or []))
    ritmo = next((b for b in blocos if b.get("id") == "ritmo"), None)
    figuras = {
        "captacao": lambda: _figura_da_captacao(flow),
        "mensal": lambda: _figura_mensal(flow),
        "ritmo": lambda: _figura_do_ritmo(ritmo, flow) if ritmo else "",
    }
    parts = [f'<h2 style="{_RETRANCA}">{_TRACO}Captação dos fundos de vitrine</h2>']
    ritmo_desenhado = False
    for bloco in blocos:
        tipo = bloco["tipo"]
        curto = bloco.get("curto")
        if curto == "":
            continue
        if tipo == "p":
            nome = bloco.get("figura")
            desenho = figuras[nome]() if nome and nome not in omitir else ""
            if desenho:
                parts.append(desenho)
                ritmo_desenhado = ritmo_desenhado or nome == "ritmo"
            elif bloco.get("numeros"):
                parts.append(numeros_em_destaque(bloco["numeros"]))
            elif curto is not None:
                parts.append(f'<p style="{_LEGENDA}">{_md_para_html(curto)}</p>')
            else:
                parts.append(f'<p style="{_P}">{_md_para_html(bloco["texto"])}</p>')
        elif tipo == "nota":
            parts.append(f'<p style="{_NOTA}">{_md_para_html(curto or bloco["texto"])}</p>')
        elif tipo == "h3":
            parts.append(f'<h3 style="{_H3}">{_esc(bloco["texto"])}</h3>')
        elif tipo == "lista":
            if bloco["titulo"]:
                destaque, _, resto = bloco["titulo"].partition(" - ")
                parts.append(
                    f'<p style="{_LEGENDA}"><strong style="color:{TINTA};">'
                    f"{_esc(destaque)}</strong>{' · ' + _esc(resto) if resto else ''}</p>"
                )
            if bloco.get("fundos"):
                parts.append(_tabela_de_fundos_da_captacao(bloco, janela))
            else:
                itens = "".join(f"<li>{_md_para_html(item)}</li>" for item in bloco["itens"])
                parts.append(f'<ul style="{_UL}">{itens}</ul>')
        elif tipo == "tabela":
            # O mapa de calor E a tabela de ritmo: mesmas celulas, pintadas.
            if bloco is ritmo and ritmo_desenhado:
                continue
            parts.append(_html_tabela_de_bloco(bloco, _platform_label(flow.get("baseline_id") or "")))
    return "".join(parts)


# --- HTML para e-mail --------------------------------------------------------


def _e_base(platform_id: str | None) -> bool:
    """A casa-base de toda leitura (a XP no monitor de fundos)."""
    cfg = PLATFORMS.get(platform_id or "")
    return bool(cfg and cfg.is_baseline)


def _marca(platform_id: str | None) -> dict:
    """Cor, destaque e contorno da marca da casa numa figura.

    O contorno preto e o que faz o amarelo da XP aparecer no branco
    (`estilo.contorno_da_casa`); as outras casas ficam sem ele.
    """
    return {
        "cor": cor_da_casa(platform_id or ""),
        "destaque": _e_base(platform_id),
        "contorno": contorno_da_casa(platform_id or ""),
    }


def _fonte_html(*fontes: Fonte) -> str:
    """"Fonte: <link>." - o rodape de figura, com o endereco publico."""
    return "Fonte: " + "; ".join(_md_para_html(_link(f)) for f in fontes) + "."


def _data_das_coletas(datas) -> str:
    """"coleta de 26/09/2026", ou "coletas de 25/09/2026 a 26/09/2026"."""
    dias = sorted({d for d in datas if d})
    if not dias:
        return ""
    if len(dias) == 1:
        return f"coleta de {data_br(dias[0])}"
    return f"coletas {periodo_br(dias[0], dias[-1])}"


def _quando_do_relatorio(report: RunReport) -> str:
    return _data_das_coletas(
        s.snapshot_date for s in report.snapshots if s.status != SnapshotStatus.UNAVAILABLE
    )


def _lider_e_base(linhas: list) -> tuple:
    """A primeira linha (a lista ja chega ordenada) e a da casa-base, se houver."""
    return linhas[0], next((linha for linha in linhas if linha.destaque), None)


def _empatadas(linhas: list) -> list:
    """As linhas empatadas com a primeira - contagem empata, e "a maior" mente."""
    return [linha for linha in linhas if linha.valor == linhas[0].valor]


# --- figuras do e-mail -------------------------------------------------------
#
# Uma funcao por figura: monta as linhas a partir do bloco, escreve o titulo que
# afirma a leitura (e nao o nome do grafico) e o subtitulo com unidade e prazo.
# Sem dado, devolvem "" - e a secao segue so com a tabela, que e a versao em
# tabela de cada figura e continua sempre logo abaixo dela.


def _figura_do_sumario(report: RunReport) -> str:
    """Tamanho da vitrine de cada casa - a unica coluna comparavel entre casas."""
    linhas, sem_vitrine, datas = [], [], []
    for snapshot in report.snapshots:
        if snapshot.status == SnapshotStatus.UNAVAILABLE:
            continue
        vitrine, _ = _tamanhos(snapshot)
        rotulo = _platform_label(snapshot.platform)
        if not vitrine:
            sem_vitrine.append(rotulo)
            continue
        datas.append(snapshot.snapshot_date)
        linhas.append(Linha(rotulo, vitrine, milhar(vitrine), **_marca(snapshot.platform)))
    if not linhas:
        return ""
    linhas.sort(key=lambda linha: (-linha.valor, not linha.destaque))
    lider, base = _lider_e_base(linhas)
    fundos = contagem(int(lider.valor), "fundo", "fundos")
    empate = _empatadas(linhas)
    if len(linhas) == 1:
        titulo = f"{lider.rotulo} tem {fundos} na vitrine"
    elif len(empate) > 1:
        titulo = (
            f"{_juntar([linha.rotulo for linha in empate])} empatam na maior vitrine: "
            f"{fundos}"
        )
    elif base is None or base is lider:
        segunda = linhas[1]
        titulo = f"{lider.rotulo} tem a maior vitrine: {fundos}; {segunda.rotulo}, {segunda.texto}"
    else:
        titulo = (
            f"{lider.rotulo} tem a maior vitrine: {fundos}; {base.rotulo} fica em "
            f"{linhas.index(base) + 1}º, com {base.texto}"
        )
    fonte = f"{_esc(_juntar(sem_vitrine))}: sem vitrine pública coletada." if sem_vitrine else ""
    return figura(
        titulo,
        _linha("Fundos de varejo na vitrine", _data_das_coletas(datas)),
        barras(linhas),
        fonte,
    )


def _figura_do_funil(gaps: dict, quando: str) -> str:
    """De aparente a real: a barra unica segmentada pelo veredito da carteira."""
    funil = gaps.get("funil") or {}
    aparentes = funil.get("aparentes") or 0
    if not (gaps.get("carteira") or {}).get("available") or not aparentes:
        return ""
    reais = funil.get("reais") or 0

    def parte(rotulo: str, chave: str, cor: str) -> Segmento:
        return Segmento(rotulo, funil.get(chave) or 0, milhar(funil.get(chave) or 0), cor)

    # Da esquerda para a direita, como o funil se le: o que ja esta coberto, o
    # que so falta em outra liquidez e os gaps reais, do mais ao menos claro.
    segmentos = [
        parte("Já cobertos", "cobertos", RAMPA[1]),
        parte("Outra liquidez", "outra_liquidez", RAMPA[2]),
        parte("Reais: estratégia", "estrategia", RAMPA[6]),
        parte("Reais: gestora", "gestora", RAMPA[5]),
        parte("Reais: sem carteira", "sem_dado", RAMPA[4]),
    ]
    if reais == aparentes:
        titulo = (
            "O único gap aparente é real" if aparentes == 1
            else f"Todos os {milhar(aparentes)} gaps aparentes são reais"
        )
    elif not reais:
        titulo = (
            "O único gap aparente já está coberto" if aparentes == 1
            else f"Nenhum dos {milhar(aparentes)} gaps aparentes é real"
        )
    else:
        titulo = (
            f"{milhar(reais)} dos {milhar(aparentes)} gaps aparentes "
            f"{'é real' if reais == 1 else 'são reais'}"
        )
    base = gaps.get("baseline_label") or "XP"
    return figura(
        titulo,
        _linha(f"Fundos em vitrine concorrente e fora da {base}", "veredito pela carteira", quando),
        barra_segmentada(segmentos),
        f"{_esc(_nota_da_carteira(gaps))} {_fonte_html(CVM_CDA)}",
    )


def _nota_da_carteira(gaps: dict) -> str:
    """A metodologia do veredito em uma linha: carteira usada, cobertura, limiares.

    A frase inteira (`notas_da_cobertura`) segue no markdown; no e-mail ela vira
    a nota do rodape do funil, com os mesmos limiares de `familias`.
    """
    carteira = gaps.get("carteira") or {}
    competencias = carteira.get("competencias") or []
    recuo = (
        f" ({', '.join(competencia_br(c) for c in competencias[1:])} se faltar)"
        if len(competencias) > 1 else ""
    )
    return _linha(
        f"Carteira CDA de {competencia_br(competencias[0]) if competencias else '-'}{recuo}",
        f"{milhar(carteira.get('com_carteira', 0))} de "
        f"{milhar(gaps.get('gaps_aparentes', 0))} com carteira",
        f"espelho: ≥ {LIMIAR_ESPELHO:.0%} do PL num só fundo",
        f"equivalente: mesma gestora e categoria, ≤ {LIMIAR_DISTANCIA_CARTEIRA:.0%} da "
        f"carteira em outra faixa e ≥ {LIMIAR_SOBREPOSICAO:.0%} em comum",
    ) + "."


def _figura_das_categorias(gaps: dict, quando: str) -> str:
    """Onde os gaps reais se concentram, por categoria ANBIMA."""
    categorias = [
        c for c in gaps.get("categories", [])[:_GAP_CATEGORY_ROWS] if c.get("gap_count")
    ]
    # Uma barra so nao compara nada: a tabela logo abaixo ja diz o mesmo.
    if len(categorias) < 2:
        return ""
    reais = "gaps reais" if (gaps.get("carteira") or {}).get("available") else "gaps"
    linhas = sorted(
        (Linha(c["category"], c["gap_count"], milhar(c["gap_count"]), COMPONENTE_1)
         for c in categorias),
        key=lambda linha: -linha.valor,
    )
    empate = _empatadas(linhas)
    verbo = "têm" if len(empate) > 1 else "tem"
    return figura(
        f"{_juntar([linha.rotulo for linha in empate])} {verbo} mais {reais}: "
        f"{contagem(int(linhas[0].valor), 'fundo', 'fundos')}",
        _linha(
            f"{_capitalizar(reais)} por categoria ANBIMA",
            f"{contagem(len(linhas), 'categoria', 'categorias')} com mais", quando,
        ),
        barras(linhas, rotulo_em_cima=True),
    )


def _janela_da_captacao(periodo: dict) -> str:
    """"3 meses, de 01/07/2026 a 25/09/2026"."""
    meses = periodo.get("meses_lidos") or []
    texto = periodo_br(periodo.get("de"), periodo.get("ate"))
    return f"{meses_br(len(meses))}, {texto}" if meses else texto


def _figura_da_captacao(flow: dict) -> str:
    """Quem capta e quem perde: barras divergentes a partir do zero vermelho."""
    base_id = flow.get("baseline_id")
    linhas = sorted(
        (
            Linha(c["label"], c["captacao_liquida"], _mm(c["captacao_liquida"]),
                  **_marca(c["platform_id"]))
            for c in flow.get("casas") or []
        ),
        key=lambda linha: -linha.valor,
    )
    if not linhas:
        return ""
    lider, base = _lider_e_base(linhas)
    if lider.valor < 0:
        titulo = f"Todas as casas têm saída líquida; {lider.rotulo} perde menos: {lider.texto}"
    elif base is None or base is lider or len(linhas) == 1:
        titulo = f"{lider.rotulo} lidera a captação: {lider.texto}"
    else:
        titulo = (
            f"{lider.rotulo} lidera a captação; {base.rotulo} fica em "
            f"{linhas.index(base) + 1}º" + (", com saída líquida" if base.valor < 0 else "")
        )
    return figura(
        titulo,
        _linha("Captação líquida por vitrine, R$ milhões (MM)",
               _janela_da_captacao(flow.get("periodo") or {})),
        barras_divergentes(linhas),
        "Fluxo por fundo, no mercado inteiro, não por distribuidor. "
        + _fonte_html(CVM_INFORME_DIARIO),
    )


def _figura_mensal(flow: dict) -> str:
    """Ritmo por competencia: colunas de ganho e perda, uma miniatura por casa."""
    competencias = flow.get("competencias") or []
    serie_por_casa = flow.get("serie_por_casa") or {}
    if not competencias or not serie_por_casa:
        return ""
    base_id = flow.get("baseline_id")
    ordem = [
        c["platform_id"] for c in flow.get("casas") or [] if c["platform_id"] in serie_por_casa
    ]
    ordem += [pid for pid in serie_por_casa if pid not in ordem]
    # A casa-base abre a fileira; as outras na ordem da tabela.
    ordem.sort(key=lambda pid: pid != base_id)

    series, medias = [], {}
    for pid in ordem:
        por_mes = {m["competencia"]: m["media_diaria"] for m in serie_por_casa[pid]}
        medias[pid] = por_mes
        ultimo = next((c for c in reversed(competencias) if por_mes.get(c) is not None), None)
        series.append(Serie(
            _platform_label(pid),
            tuple(por_mes.get(c) for c in competencias),
            nota=f"{competencia_br(ultimo)}: {_mm(por_mes[ultimo])}/dia" if ultimo else "",
            **_marca(pid),
        ))
    corpo = colunas_por_casa(series, [competencia_br(c).split("/")[0] for c in competencias])
    if not corpo:
        return ""

    alvo = base_id if base_id in medias else ordem[0]
    valores = [(c, medias[alvo][c]) for c in competencias if medias[alvo].get(c) is not None]
    if not valores:
        return ""
    competencia, ultimo = valores[-1]
    mes = competencia_br(competencia)
    if len(valores) < 2:
        leitura = f"{_mm(ultimo)}/dia em {mes}"
    else:
        anterior = valores[-2][1]
        if anterior < 0 <= ultimo:
            verbo = "volta a captar"
        elif anterior >= 0 > ultimo:
            verbo = "passa a perder dinheiro"
        elif ultimo < 0:
            verbo = "saída se aprofunda" if ultimo < anterior else "saída diminui"
        else:
            verbo = "captação acelera" if ultimo > anterior else "captação desacelera"
        leitura = f"{verbo} em {mes}, com {_mm(ultimo)}/dia"
    periodo = flow.get("periodo") or {}
    return figura(
        f"{_platform_label(alvo)}: {leitura}",
        _linha("Média diária por competência, MM/dia",
               periodo_br(periodo.get("de"), periodo.get("ate")), "mesma escala"),
        corpo,
        f"Média diária porque {_esc(competencia_br(competencias[-1]))} vai só até "
        f"{_esc(data_br(periodo.get('ate')))}. " + _fonte_html(CVM_INFORME_DIARIO),
    )


def _figura_do_ritmo(bloco: dict, flow: dict) -> str:
    """A tabela de ritmo dos fundos como mapa de calor - as mesmas celulas."""
    # A contagem de fundos vai sob o nome da casa, e nao numa coluna: com ela,
    # o mapa passava da largura do telefone.
    linhas = [
        LinhaDeCalor(
            texto[0],
            tuple(Celula(n * 100, t) for n, t in zip(numeros, texto[2:])),
            _e_base(pid),
            contagem(int(texto[1]), "fundo", "fundos"),
        )
        for pid, texto, numeros in zip(bloco["casas"], bloco["linhas"], bloco["parcelas"])
    ]
    corpo = mapa_de_calor(bloco["cabecalho"][2:], linhas)
    if not corpo:
        return ""
    persistente = {pid: numeros[3] for pid, numeros in zip(bloco["casas"], bloco["parcelas"])}
    base_id = flow.get("baseline_id")
    if base_id in persistente and len(persistente) > 1:
        outras = {pid: v for pid, v in persistente.items() if pid != base_id}
        melhor = min(outras, key=outras.get)
        if persistente[base_id] <= outras[melhor]:
            titulo = (
                f"{_platform_label(base_id)} tem a menor parcela de fundos em saída "
                f"persistente: {persistente[base_id]:.0%}"
            )
        else:
            titulo = (
                f"{_platform_label(base_id)} tem {persistente[base_id]:.0%} dos fundos em "
                f"saída persistente; {_platform_label(melhor)}, {outras[melhor]:.0%}"
            )
    else:
        pior = max(persistente, key=persistente.get)
        titulo = (
            f"{_platform_label(pior)} tem a maior parcela de fundos em saída "
            f"persistente: {persistente[pior]:.0%}"
        )
    return figura(
        titulo,
        _linha("% dos fundos de cada vitrine por ritmo de captação",
               _janela_da_captacao(flow.get("periodo") or {})),
        corpo,
        "Captando inclui acelerando · fundos com PL de ao menos R$ 1 mi no início.",
    )


def _figura_da_rentabilidade(bloco: dict, primeira: bool = True) -> str:
    """Retorno ponderado por PL por casa, numa janela; com negativo, divergente.

    A ressalva que evita a leitura errada (e o peso do mercado, nao o retorno
    dos clientes) vai so no rodape da primeira janela.
    """
    janela = janela_do_bloco(bloco)
    linhas = sorted(
        (
            Linha(c["label"], c["por_pl"], _pct(c["por_pl"]), **_marca(c.get("platform_id")))
            for c in bloco.get("casas", []) if c.get("por_pl") is not None
        ),
        key=lambda linha: -linha.valor,
    )
    if not linhas:
        return ""
    corpo = (
        barras_divergentes(linhas) if any(linha.valor < 0 for linha in linhas)
        else barras(linhas)
    )
    lider, base = _lider_e_base(linhas)
    rotulo = janela["rotulo"]
    titulo = f"{lider.rotulo} tem a maior rentabilidade ponderada por PL em {rotulo}: {lider.texto}"
    if base is not None and base is not lider:
        titulo += f"; {base.rotulo}, {base.texto}"
    nota = _linha(
        f"Base: {janela['base']}" if janela.get("base") else "",
        f"{_contagem(janela['pregoes'])} pregões" if janela.get("pregoes") else "",
        "cota ex-provento, com o peso de mercado: não é o retorno dos clientes da casa"
        if primeira else "",
    )
    return figura(
        titulo,
        _linha("Retorno ponderado pelo PL, em %",
               f"{rotulo}, {periodo_br(janela.get('de'), janela.get('ate'))}"),
        corpo,
        (f"{_esc(nota)}. " if nota else "") + _fonte_html(CVM_INFORME_DIARIO),
    )


def _figura_do_pl_abaixo_da_mediana(bloco: dict) -> str:
    """Quanto do dinheiro esta em fundo abaixo da propria categoria: 100 pontos por casa."""
    janela = janela_do_bloco(bloco)
    linhas = [
        Linha(c["label"], massa["share_pl"], f"{_share(massa['share_pl'])} do PL",
              **_marca(c.get("platform_id")))
        for c in bloco.get("casas", [])
        if (massa := c.get("alocacao_abaixo_da_mediana") or {}).get("share_pl") is not None
    ]
    corpo = grades_por_casa(linhas)
    if not corpo:
        return ""
    ordenadas = sorted(linhas, key=lambda linha: linha.valor)
    melhor, base = _lider_e_base(ordenadas)
    rotulo = janela["rotulo"]
    if base is None or base is melhor:
        titulo = (
            f"{melhor.rotulo} tem a menor parcela do PL em fundos abaixo da mediana da "
            f"categoria em {rotulo}: {_share(melhor.valor)}"
        )
    else:
        titulo = (
            f"{base.rotulo} tem {_share(base.valor)} do PL em fundos abaixo da mediana "
            f"da categoria em {rotulo}; {melhor.rotulo}, {_share(melhor.valor)}"
        )
    return figura(
        titulo,
        _linha("% do PL em fundos abaixo da mediana da categoria",
               f"{rotulo}, {periodo_br(janela.get('de'), janela.get('ate'))}",
               "1 quadrado = 1%"),
        corpo,
        _fonte_html(CVM_INFORME_DIARIO),
    )


def _figura_do_custo(report: RunReport) -> str:
    """Indice mediano de custo por casa, contra a mediana da categoria."""
    custo = report.custo or {}
    linhas = sorted(
        (
            Linha(c["label"], c["indice_mediano"], _indice_mediano(c),
                  **_marca(c.get("platform_id")))
            for c in custo.get("casas", []) if c.get("indice_mediano") is not None
        ),
        key=lambda linha: linha.valor,
    )
    if not linhas:
        return ""
    barata, base = _lider_e_base(linhas)
    if len(linhas) == 1:
        titulo = f"{barata.rotulo} cobra {barata.texto} a mediana da categoria"
    elif base is None or base is barata:
        titulo = f"{barata.rotulo} é a casa mais barata contra a categoria: {barata.texto}"
    elif base is linhas[-1]:
        titulo = (
            f"{base.rotulo} é a casa mais cara contra a categoria: {base.texto}; "
            f"{barata.rotulo}, {barata.texto}"
        )
    else:
        titulo = (
            f"{barata.rotulo} é a casa mais barata contra a categoria: {barata.texto}; "
            f"{base.rotulo}, {base.texto}"
        )
    return figura(
        titulo,
        _linha("Taxa de administração ÷ mediana da categoria (1,00x = mediana)",
               _quando_do_relatorio(report)),
        barras(linhas),
        "Índice, não taxa: a taxa bruta mede o mix da prateleira. O nível carrega a "
        "camada de distribuição; decide a diferença entre casas. "
        + _fonte_html(CVM_EXTRATO),
    )


def _figura_do_radar(report: RunReport) -> str:
    """Fundos registrados e ainda nao lancados, por casa."""
    snapshot = report.cvm_snapshot
    linhas = []
    for house, funds in snapshot.by_house().items():
        pre_operacionais = sum(1 for f in funds if f.is_pre_operational)
        if pre_operacionais:
            linhas.append(Linha(
                _platform_label(house), pre_operacionais, milhar(pre_operacionais),
                **_marca(house),
            ))
    if not linhas:
        return ""
    linhas.sort(key=lambda linha: (-linha.valor, not linha.destaque))
    lider, base = _lider_e_base(linhas)
    empate = _empatadas(linhas)
    verbo = "têm" if len(empate) > 1 else "tem"
    titulo = (
        f"{_juntar([linha.rotulo for linha in empate])} {verbo} mais fundos "
        f"pré-operacionais: {lider.texto}"
    )
    if base is not None and base not in empate:
        titulo += f"; {base.rotulo}, {base.texto}"
    return figura(
        titulo,
        _linha("Registrados na CVM e ainda não lançados, por casa",
               f"cadastro de {data_br(snapshot.collected_at.date())}"),
        barras(linhas),
        _fonte_html(CVM_CADASTRO),
    )


def _figura_das_vitrines(report: RunReport) -> str:
    """Entradas a direita e saidas a esquerda do zero, por casa, desde a coleta anterior."""
    linhas, antes, depois, suspeitas = [], [], [], []
    alteracoes = 0
    for diff in report.shelf_diffs:
        snapshot = report.snapshot_for(diff.platform)
        if diff.is_first_run or (snapshot and snapshot.status == SnapshotStatus.UNAVAILABLE):
            continue
        entradas, saidas, alteradas, _ = _mudancas_de_vitrine(diff, snapshot)
        alteracoes += len(alteradas)
        if diff.suspect:
            suspeitas.append(_platform_label(diff.platform))
        antes.append(diff.prev_date)
        depois.append(diff.curr_date)
        linhas.append(Oposta(
            _platform_label(diff.platform), len(saidas), len(entradas),
            f"-{len(saidas)}" if saidas else "0",
            f"+{len(entradas)}" if entradas else "0",
            **_marca(diff.platform),
        ))
    total_entradas = sum(linha.direita for linha in linhas)
    total_saidas = sum(linha.esquerda for linha in linhas)
    if not (total_entradas or total_saidas):
        return ""
    if total_entradas and total_saidas:
        titulo = (
            f"As vitrines ganham {contagem(total_entradas, 'fundo', 'fundos')} e "
            f"perdem {milhar(total_saidas)}"
        )
    elif total_entradas:
        titulo = (
            f"As vitrines ganham {contagem(total_entradas, 'fundo', 'fundos')} e não "
            "perdem nenhum"
        )
    else:
        titulo = (
            f"As vitrines perdem {contagem(total_saidas, 'fundo', 'fundos')} e não "
            "ganham nenhum"
        )
    notas = []
    if alteracoes:
        notas.append(contagem(
            alteracoes,
            "alteração de condições, na tabela abaixo.",
            "alterações de condições, na tabela abaixo.",
        ))
    if suspeitas:
        notas.append(f"Coleta suspeita em {_juntar(suspeitas)}: saídas omitidas.")
    return figura(
        titulo,
        _linha("Entradas (à direita) e saídas (à esquerda) da vitrine",
               periodo_br(min(antes), max(depois))),
        barras_opostas(linhas),
        _esc(" ".join(notas)),
    )


# --- secoes em HTML ----------------------------------------------------------
#
# Secao 5 da linguagem visual: lista de fundos vira tabela, introducao cabe numa
# linha (ou some, quando a figura logo abaixo ja diz o que o bloco mede e o
# prazo), metodologia vai para a nota miuda. O markdown continua com as frases
# inteiras - e a parte em texto do e-mail.

_CASA_CURTA = {"xp": "XP", "btg": "BTG", "itau": "Itaú", "nu": "Nu", "inter": "Inter"}


def _casas_curtas(casas: list[str]) -> str:
    """"XP, BTG, Inter": id ou rotulo de casa, no nome curto das colunas Casas.

    Casa com rotulo trocado pelo modulo (o Itau Private da previdencia) ou fora
    do registro fica com o rotulo inteiro - o nome curto e do rotulo padrao.
    """
    por_rotulo = {cfg.label: pid for pid, cfg in PLATFORMS.items()}
    saida = []
    for casa in casas:
        pid = casa if casa in PLATFORMS else por_rotulo.get(casa)
        rotulo = _platform_label(pid) if pid else casa
        padrao = pid is not None and rotulo == PLATFORMS[pid].label
        saida.append(_CASA_CURTA.get(pid, rotulo) if padrao else rotulo)
    return ", ".join(saida)


def _motivo_curto(motivo: str | None) -> str:
    """A primeira oracao do motivo de exclusao; o detalhe fica no markdown."""
    texto = motivo or "motivo não informado"
    for corte in (" (", ";", " - "):
        texto = texto.split(corte)[0]
    return texto.strip()


# --- vitrines ------------------------------------------------------------------


_DETALHE_DO_CAMPO = " → "


def _html_vitrines(report: RunReport, urls: dict[str, str]) -> str:
    """Todas as mudancas de vitrine numa tabela: Casa | Movimento | Fundo | Detalhe.

    O estado de cada casa (primeira coleta, nova linha de base, coleta suspeita,
    sem mudanca, o que mudou so no catalogo) vira uma nota curta cada, embaixo.
    O tamanho de cada vitrine ja esta no sumario e nao se repete aqui.
    """
    linhas: list[list[str]] = []
    notas: list[str] = []
    alertas: list[str] = []
    primeira, sem_mudanca, so_catalogo, sobra = [], [], [], 0
    for diff in report.shelf_diffs:
        snapshot = report.snapshot_for(diff.platform)
        rotulo = _platform_label(diff.platform)
        if snapshot and snapshot.status == SnapshotStatus.UNAVAILABLE:
            alertas.append(
                f"{_esc(rotulo)}: coleta indisponível "
                f"({_esc(_motivo_curto(snapshot.error))})."
            )
            continue
        if diff.nova_linha_de_base:
            alertas.append(
                f"Nova linha de base em {_esc(rotulo)}: "
                f"{_esc(diff.suspect_reason or 'a coleta passou a ver a vitrine')}."
            )
            continue
        if diff.is_first_run:
            primeira.append(rotulo)
            continue
        if diff.suspect:
            alertas.append(
                f"Coleta suspeita em {_esc(rotulo)} ({_esc(diff.suspect_reason)}): "
                "saídas omitidas para evitar falso churn."
            )
        entradas, saidas, alteracoes, catalogo = _mudancas_de_vitrine(diff, snapshot)
        if catalogo:
            so_catalogo.append(f"{rotulo} {milhar(catalogo)}")
        if not (entradas or saidas or alteracoes):
            sem_mudanca.append(rotulo)
            continue
        casa = (
            f'<a href="{_esc(urls[diff.platform])}" style="{_LINK}">{_esc(rotulo)}</a>'
            if urls.get(diff.platform) else _esc(rotulo)
        )
        movimentos = [
            ("Entrou", _fundo_html(f.name, format_cnpj(f.cnpj) if f.cnpj else None),
             f"gestora {_esc(f.manager_name)}" if f.manager_name else "")
            for f in entradas[:_MAX_LISTED]
        ] + [
            ("Saiu", _fundo_html(f.name, format_cnpj(f.cnpj) if f.cnpj else None), "")
            for f in saidas[:_MAX_LISTED]
        ] + [
            ("Alterou", _esc(c.name), "; ".join(
                f"{_esc(_FIELD_LABELS.get(m.field, m.field))}: {_esc(_format_value(m.old))}"
                f"{_DETALHE_DO_CAMPO}<strong>{_esc(_format_value(m.new))}</strong>"
                for m in c.changes
            ))
            for c in alteracoes[:_MAX_LISTED]
        ]
        sobra += sum(max(0, len(lista) - _MAX_LISTED) for lista in (entradas, saidas, alteracoes))
        # A casa e o cabecalho do grupo, e nao uma coluna: no telefone a coluna
        # a mais espremia o fundo e o detalhe.
        linhas.append(casa)
        linhas += [list(movimento) for movimento in movimentos]

    if primeira:
        notas.insert(0, f"Primeira coleta, linha de base: {_esc(_juntar(primeira))}.")
    if sem_mudanca:
        notas.append(f"Sem mudança na vitrine: {_esc(_juntar(sem_mudanca))}.")
    if so_catalogo:
        notas.append(
            f"Só no catálogo Open Finance, fora da vitrine: {_esc(', '.join(so_catalogo))}."
        )
    partes = [f'<p style="{_CHAMADA}">{" ".join(alertas)}</p>'] if alertas else []
    if linhas:
        com_detalhe = any(linha[2] for linha in linhas if not isinstance(linha, str))
        partes.append(_tabela(
            ["Movimento", "Fundo"] + (["Detalhe"] if com_detalhe else []),
            [
                linha if isinstance(linha, str) or com_detalhe else linha[:2]
                for linha in linhas
            ],
            larguras={0: 16},
        ))
        partes.append(_mais(sobra, "mudanças no dashboard e na planilha"))
    if notas:
        partes.append(f'<p style="{_NOTA}">{" ".join(notas)}</p>')
    return "".join(partes)


# --- radar CVM -----------------------------------------------------------------


def _html_cvm_section(report: RunReport, omitir: frozenset = frozenset()) -> str:
    snapshot = report.cvm_snapshot
    parts = [f'<h2 style="{_RETRANCA}">{_TRACO}Radar CVM &mdash; visão antecipada</h2>']

    if not snapshot or snapshot.status == SnapshotStatus.UNAVAILABLE:
        error = snapshot.error if snapshot else "extrato não coletado"
        parts.append(f'<p style="{_P}">Extrato da CVM indisponível: {_esc(error)}.</p>')
        return "".join(parts)

    figura_do_radar = "" if "radar" in omitir else _figura_do_radar(report)
    parts.append(figura_do_radar or (
        f'<p style="{_LEGENDA}">Fundos das gestoras ligadas às casas · '
        f"cadastro de {data_br(snapshot.collected_at.date())} · "
        f"Fonte: {_md_para_html(_link(CVM_CADASTRO))}.</p>"
    ))

    por_casa = sorted(snapshot.by_house().items())
    linhas = [
        [
            _esc(_platform_label(house)), _contagem(len(funds)),
            _contagem(sum(1 for f in funds if f.is_pre_operational)),
        ]
        for house, funds in por_casa
    ]
    linhas.append([
        "Total", _contagem(len(snapshot.funds)),
        _contagem(sum(1 for f in snapshot.funds if f.is_pre_operational)),
    ])
    parts.append(_tabela(
        ["Casa", "Registrados", "Pré-operacionais"], linhas, direita={1, 2},
        base=[_e_base(house) for house, _ in por_casa] + [False], total=True,
    ))

    diff = report.cvm_diff
    if not diff:
        return "".join(parts)

    movimentos: list[tuple[str, object, str, str | None]] = []
    sobra = 0

    def juntar(fundos: list, rotulo, data) -> None:
        nonlocal sobra
        sobra += max(0, len(fundos) - _MAX_LISTED)
        for item in fundos[:_MAX_LISTED]:
            fundo = getattr(item, "fund", item)
            movimentos.append((fundo.house, fundo, rotulo(item), data(item)))

    if diff.is_first_run:
        juntar(diff.new_registrations, lambda f: "Registrado, não lançado",
               lambda f: f.registered_at)
        legenda = "**Registrados e ainda não lançados**"
    else:
        juntar(
            diff.new_registrations,
            lambda f: "Novo registro" + (" · pré-operacional" if f.is_pre_operational else ""),
            lambda f: f.registered_at,
        )
        juntar(diff.launches, lambda c: "Entrou em operação",
               lambda c: c.fund.situation_started_at)
        juntar(
            [c for c in diff.situation_changes if not c.is_launch],
            lambda c: f"{c.old_situation or '-'} → {c.new_situation or '-'}",
            lambda c: c.fund.situation_started_at,
        )
        juntar(diff.cancelled, lambda f: "Saiu do cadastro", lambda f: None)
        legenda = (
            f"**Movimentos no cadastro** · desde {data_br(diff.prev_date)}"
            if diff.prev_date else "**Movimentos no cadastro**"
        )
    if movimentos:
        # A data vai sob o movimento, miuda: quatro colunas nao cabem no telefone.
        parts.append(_legenda(legenda))
        parts.append(_tabela(
            ["Casa", "Fundo", "Movimento"],
            [
                [
                    _esc(_casas_curtas([casa])),
                    _fundo_html(fundo.name, format_cnpj(fundo.cnpj)),
                    _esc(movimento)
                    + (f'<br><small style="{_MIUDO_CINZA}">{data_br(data)}</small>'
                       if data else ""),
                ]
                for casa, fundo, movimento, data in movimentos
            ],
            larguras={0: 10, 2: 30},
            base=[_e_base(casa) for casa, *_ in movimentos],
        ))
        parts.append(_mais(sobra, "no dashboard e na planilha"))
    return "".join(parts)


# --- gaps ----------------------------------------------------------------------


def _notas_curtas_dos_gaps(gaps: dict, com_funil: bool) -> list[str]:
    """As notas do bloco de gaps em uma linha cada; a metodologia da carteira
    fica no rodape do funil quando ele sai."""
    notas = ["Vitrine contra vitrine, por CNPJ; o catálogo Open Finance não conta."]
    if gaps.get("excluded"):
        notas.append("Fora: " + "; ".join(
            f"{e['label']}, {_motivo_curto(e['reason'])}" for e in gaps["excluded"]
        ) + ".")
    for nota in notas_da_cobertura(gaps):
        if nota.startswith("Espelho e equivalência"):
            if not com_funil:
                notas.append(_nota_da_carteira(gaps))
            continue
        notas.append(nota)
    return notas


def _html_gaps_section(
    gaps: dict, quando: str = "", omitir: frozenset = frozenset(), janela: str = ""
) -> str:
    parts = [f'<h2 style="{_RETRANCA}">{_TRACO}Gaps de prateleira da XP</h2>']
    if gaps.get("nao_se_aplica"):
        parts.append(
            f'<p style="{_CHAMADA}"><strong>Não se aplica a este monitor:</strong> '
            f"{_esc(gaps.get('reason'))}.</p>"
        )
        return "".join(parts)
    if not gaps.get("available"):
        parts.append(
            f'<p style="{_CHAMADA}"><strong>Análise suprimida nesta execução:</strong> '
            f"{_esc(gaps.get('reason'))}.</p>"
        )
        return "".join(parts)

    # O funil desenhado diz o que a frase de abertura dizia; sem ele (sem
    # carteira, ou fora do orcamento), a frase volta.
    funil = "" if "funil" in omitir else _figura_do_funil(gaps, quando)
    tem_gaps = gaps["total_gaps"] or gaps.get("gaps_liquidez")
    if funil:
        parts.append(funil)
    elif not tem_gaps:
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
        parts.append(f'<p style="{_P}">{_esc(frase_do_funil(gaps) or _abertura_curta(gaps))}</p>')

    if tem_gaps:
        reais = "Gaps reais" if (gaps.get("carteira") or {}).get("available") else "Gaps"
        fundos = gaps["funds"][:_GAP_LISTED]
        if fundos:
            parts.append(_legenda(
                f"**{reais}, os {len(fundos)} primeiros** · por esforço de fechar e "
                "captação; produto próprio do concorrente no fim"
            ))
            parts.append(_tabela(
                ["Fundo", "Categoria", "Casas", f"Captação {janela}".strip(), "Esforço"],
                [
                    [
                        _fundo_html(f["name"], f.get("cnpj_display")),
                        _esc(f["category"]),
                        _esc(_casas_curtas(f.get("houses") or [])),
                        _mm(f["captacao_liquida"]) if f.get("captacao_liquida") is not None
                        else "-",
                        _esc(f.get("esforco") or "-"),
                    ]
                    for f in fundos
                ],
                direita={3}, sinal={3}, compacta=True,
                larguras={0: 34, 1: 24, 2: 9, 3: 15},
            ))
            parts.append(_mais(len(gaps["funds"]) - len(fundos), "no dashboard e na planilha"))

        categories = gaps["categories"][:_GAP_CATEGORY_ROWS]
        if categories:
            if "categorias" not in omitir:
                parts.append(_figura_das_categorias(gaps, quando))
            parts.append(_tabela(
                ["Categoria", "XP", "Melhor concorrente", "Gaps reais", "Cobertos"],
                [
                    [
                        _esc(entry["category"]), str(entry["xp"]),
                        _esc(
                            f"{entry['best_competitor']['label']} "
                            f"({entry['best_competitor']['count']})"
                            if entry["best_competitor"] else "-"
                        ),
                        str(entry["gap_count"]), str(entry.get("cobertos", 0)),
                    ]
                    for entry in categories
                ],
                direita={1, 3, 4},
            ))

    cobertos = gaps.get("cobertos") or []
    if cobertos:
        mostrados = cobertos[:_GAP_COBERTOS_LISTED]
        parts.append(_legenda(
            f"**Já cobertos** · {milhar(len(cobertos))}; os {len(mostrados)} primeiros "
            "(lista completa na aba 'Gaps cobertos' da planilha)"
        ))
        linhas = []
        for fund in mostrados:
            v = fund.get("veredito") or {}
            resgate = (
                f"D+{v['dmais']} / D+{v['dmais_cobre']}"
                if v.get("dmais") is not None and v.get("dmais_cobre") is not None else "-"
            )
            como = (v.get("rotulo") or "-").removeprefix("coberto por ")
            linhas.append([
                _fundo_html(fund["name"], fund.get("cnpj_display")),
                _fundo_html(v.get("nome_cobre") or "-",
                            format_cnpj(v["cobre"]) if v.get("cobre") else None),
                _esc(como), resgate,
            ])
        parts.append(_tabela(
            ["Fundo concorrente", "Coberto por", "Como", "Resgate gap / XP"], linhas,
            direita={3}, larga=True,
        ))

    notes = " ".join(_esc(note) for note in _notas_curtas_dos_gaps(gaps, bool(funil)))
    parts.append(f'<p style="{_NOTA}">{notes}</p>')
    if gaps.get("baseline_warning"):
        parts.insert(1, f'<p style="{_CHAMADA}"><strong>Atenção:</strong> '
                        f"{_esc(gaps['baseline_warning'])}.</p>")
    return "".join(parts)


def _abertura_curta(gaps: dict) -> str:
    """Sem o mapa de familias: a contagem CNPJ contra CNPJ, sem a frase de ordem."""
    return (
        f"{gaps['total_gaps']} fundos de varejo em vitrines concorrentes e fora da "
        f"vitrine da XP ({gaps['multi_house_gaps']} em 2 ou mais casas)."
    )


# --- fontes --------------------------------------------------------------------


def _html_fontes(report: RunReport, config: Config) -> str:
    """As fontes como tabela: nome com link | o que fornece (e os arquivos lidos)."""
    return _fontes_html(fontes_do_relatorio(report, config))


def _html_tabela_do_sumario(report: RunReport) -> tuple[str, bool]:
    """A tabela do sumario, e se alguma casa tem catalogo maior que a vitrine."""
    linhas, negrito = [], []
    tem_catalogo_maior = False
    for snapshot in report.snapshots:
        diff = report.diff_for(snapshot.platform)
        vitrine, catalogo = _tamanhos(snapshot)
        tem_catalogo_maior = tem_catalogo_maior or catalogo > vitrine
        if diff and not diff.is_first_run:
            entradas, saidas, alteracoes, _ = _mudancas_de_vitrine(diff, snapshot)
        else:
            entradas = saidas = alteracoes = []
        linhas.append([
            _esc(_platform_label(snapshot.platform)),
            _contagem(vitrine) if vitrine else "sem vitrine",
            f'<span style="{_CINZA}">{_contagem(catalogo)}</span>',
            _esc(snapshot.status.value),
            str(len(entradas)), str(len(saidas)), str(len(alteracoes)),
        ])
        negrito.append(_e_base(snapshot.platform))
    tabela = _tabela(
        ["Plataforma", "Vitrine", "Catálogo", "Status", "Entradas", "Saídas", "Alterações"],
        linhas, direita={1, 2, 4, 5, 6}, base=negrito,
    )
    return tabela, tem_catalogo_maior


# Orcamento do e-mail. O Gmail corta a mensagem com mais de ~102 KB de HTML: o
# leitor ve o comeco e um link "Exibir mensagem inteira", e o resto some. As
# figuras sao a parte que cresce (tabela de celulas coloridas), e sao todas a
# versao grafica de uma tabela que fica no e-mail - entao, passado o orcamento,
# elas saem uma a uma, da que menos acrescenta a que mais, e o rodape avisa.
# Medido em 2026-09-27 com os dados reais: ~97 KB com todas as figuras.
_ORCAMENTO_DO_EMAIL = 100_000
_FIGURAS_POR_CORTE = (
    ("pl_abaixo", "PL abaixo da mediana"),
    ("mensal", "captação mês a mês"),
    ("categorias", "gaps por categoria"),
    ("radar", "radar CVM"),
    ("vitrines", "entradas e saídas"),
    ("sumario", "tamanho das vitrines"),
    ("custo", "custo"),
    ("ritmo", "ritmo dos fundos"),
    ("rentabilidade", "rentabilidade"),
    ("funil", "funil de gaps"),
    ("captacao", "captação por casa"),
)


def render_html(report: RunReport, config: Config | None = None) -> str:
    """O digest em HTML de e-mail, dentro do orcamento de tamanho do Gmail."""
    montar = _montador_do_html(report, config)
    html = montar(())
    cortes = [chave for chave, _ in _FIGURAS_POR_CORTE]
    for n in range(1, len(cortes) + 1):
        if len(html.encode("utf-8")) <= _ORCAMENTO_DO_EMAIL:
            break
        html = montar(tuple(cortes[:n]))
    return html


def _montador_do_html(report: RunReport, config: Config | None):
    """Monta as partes fixas uma vez; devolve `montar(omitidas) -> html`."""
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    identidade = config if config is not None else Config
    fontes_config = config if config is not None else Config()
    sentence, is_alert = _summary_sentence(report)
    summary = f"<strong>{_esc(sentence)}</strong>" if is_alert else _esc(sentence)

    tabela_do_sumario, tem_catalogo_maior = _html_tabela_do_sumario(report)
    nota_base = (
        f'<p style="{_NOTA}">Vitrine: o que o cliente vê na listagem da casa, a única '
        "coluna comparável · Catálogo: o que ela administra, com o Open Finance · "
        "entradas, saídas e alterações contam a vitrine.</p>"
        if tem_catalogo_maior
        else ""
    )

    urls = _urls_das_vitrines(fontes_config, report)
    vitrines = _html_vitrines(report, urls)
    bloco_fontes = _html_fontes(report, fontes_config)

    ticket = _frase_do_ticket(report)
    bloco_ticket = (
        f'<h2 style="{_RETRANCA}">{_TRACO}Ticket de entrada</h2>'
        f'<p style="{_P}">{_md_para_html(ticket)}</p>'
        if ticket else ""
    )

    # A faixa preta do topo, como a barra de navegacao do deck: o rotulo do
    # monitor em caixa alta a esquerda, a data da execucao a direita.
    faixa = _faixa(identidade.rotulo_assunto, report.generated_at.strftime("%d/%m/%Y"))
    lede = (
        f'<p style="{_CHAMADA}font-size:15px;">{summary}</p>' if is_alert
        else f'<p style="font-size:16px;line-height:1.5;color:{TINTA};margin:0 0 16px;">'
        f"{summary}</p>"
    )
    gaps = _gaps_ranqueados(report)
    periodo = (report.retail_flow or {}).get("periodo") or {}
    janela = meses_br(len(periodo["meses_lidos"])) if periodo.get("meses_lidos") else ""
    quando = _quando_do_relatorio(report)
    nomes = dict(_FIGURAS_POR_CORTE)

    def montar(omitidas: tuple[str, ...]) -> str:
        omitir = frozenset(omitidas)
        nota_de_corte = (
            f'<p style="{_NOTA}">Para caber no limite de tamanho do Gmail, '
            f"{contagem(len(omitidas), 'gráfico ficou', 'gráficos ficaram')} de fora "
            f"({_esc(_juntar([nomes[c] for c in omitidas]))}); as tabelas trazem os "
            "mesmos números.</p>"
            if omitidas else ""
        )
        corpo = f"""
  <p style="{_RETRANCA}font-weight:700;margin:0 0 14px;padding:0;border:0;">{_TRACO}{_esc(identidade.descricao_relatorio)}</p>
  <h1 style="font-size:28px;line-height:32px;font-weight:800;letter-spacing:-0.02em;color:{TINTA};margin:0 0 8px;">{_esc(identidade.titulo_relatorio)}</h1>
  <p style="font-size:14px;line-height:20px;color:{TINTA_2};margin:0;">Execução de {_esc(generated)}</p>

  <h2 style="{_RETRANCA}">{_TRACO}Sumário</h2>
  {lede}
  {"" if "sumario" in omitir else _figura_do_sumario(report)}
  {tabela_do_sumario}
  {nota_base}

  {_html_gaps_section(gaps, quando, omitir, janela)}

  {bloco_ticket}

  {_html_flow_section(report, omitir)}
  {_html_performance_section(report, omitir)}

  {_html_cvm_section(report, omitir)}

  <h2 style="{_RETRANCA}">{_TRACO}Vitrines</h2>
  {"" if "vitrines" in omitir else _figura_das_vitrines(report)}
  {vitrines}

  {bloco_fontes}
  {nota_de_corte}
"""
        return _casca_do_email(
            _esc(identidade.titulo_relatorio), _esc(sentence), corpo, faixa
        )

    return montar


def render_digest(config: Config, report: RunReport) -> tuple[Path, Path]:
    """Grava digest markdown e HTML. Devolve (path_md, path_html)."""
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    today: date = report.generated_at.date()

    md_path = config.reports_dir / f"digest-{today.isoformat()}.md"
    md_path.write_text(render_markdown(report, config), encoding="utf-8")

    html_path = config.reports_dir / f"digest-{today.isoformat()}.html"
    html_path.write_text(render_html(report, config), encoding="utf-8")

    return md_path, html_path
