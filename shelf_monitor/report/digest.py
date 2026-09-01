"""Digest de mudancas: o que mudou desde a ultima execucao.

Gera markdown (leitura no repo, diff de git legivel) e HTML com CSS inline,
sem JS e com largura contida - pronto para colar num e-mail.
"""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.models import (
    CvmDiff,
    RunReport,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import format_cnpj
from shelf_monitor.report.aggregate import build_cvm_cross_block, build_overlap_block

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


def _platform_label(platform_id: str) -> str:
    cfg = PLATFORMS.get(platform_id)
    return cfg.label if cfg else platform_id


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


def _summary_counts(report: RunReport) -> tuple[int, int, int]:
    added = sum(len(d.added) for d in report.shelf_diffs if not d.is_first_run)
    removed = sum(len(d.removed) for d in report.shelf_diffs if not d.is_first_run)
    changed = sum(len(d.changed) for d in report.shelf_diffs if not d.is_first_run)
    return added, removed, changed


def _summary_sentence(report: RunReport) -> tuple[str, bool]:
    """Frase de abertura do digest, e se ela e um alerta.

    Distingue tres silencios que se parecem no numero mas nao no significado:
    coleta que falhou inteira, primeira coleta, e prateleira estavel.
    """
    added, removed, changed = _summary_counts(report)
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
        total = sum(len(s.funds) for s in collected)
        return (
            f"Primeira coleta: {total} fundos em {len(collected)} plataforma(s) viram "
            "a linha de base. As comparacoes comecam na proxima execucao.",
            False,
        )

    if not (added or removed or changed):
        return ("Nenhuma mudanca nas prateleiras monitoradas desde a ultima coleta.", False)

    return (
        f"{added} fundos entraram, {removed} sairam e {changed} tiveram condicoes "
        "alteradas nas prateleiras monitoradas.",
        False,
    )


_HIGHLIGHT_ARBITRAGE = 5


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + "%"


def _fmt_pp(value: float) -> str:
    return "+" + f"{value:.2f}".replace(".", ",") + " p.p."


def _overlap_highlights(overlap: dict) -> tuple[str | None, list[tuple[str, str]]]:
    """(frase do nucleo comum, [(texto do fundo, detalhe de precos), ...]).

    Materia-prima dos Destaques competitivos, compartilhada entre o markdown e
    o HTML para as duas superficies nunca divergirem no conteudo.
    """
    if not overlap or len(overlap.get("platforms", [])) < 2:
        return None, []

    labels = overlap.get("labels", {})
    n_houses = len(overlap["platforms"])
    core = overlap.get("core_all", {}).get("count", 0)
    multi = sum(overlap.get("multi_home", {}).values())

    sentence = None
    if multi:
        sentence = (
            f"{multi} fundos de varejo aparecem em 2 ou mais das {n_houses} casas "
            f"coletadas (por CNPJ)"
        )
        if core:
            sentence += f"; {core} estao em todas elas"
        sentence += "."

    items: list[tuple[str, str]] = []
    for item in overlap.get("fee_arbitrage", {}).get("top", [])[:_HIGHLIGHT_ARBITRAGE]:
        fees = item.get("fees", {})
        cheapest = item.get("cheapest")
        priciest = item.get("priciest")
        if cheapest not in fees or priciest not in fees:
            continue
        detail = (
            f"{_fmt_pct(fees[cheapest])} em {labels.get(cheapest, cheapest)} vs "
            f"{_fmt_pct(fees[priciest])} em {labels.get(priciest, priciest)} "
            f"({_fmt_pp(item['spread_pp'])})"
        )
        name = item["name"] + (f" ({item['cnpj']})" if item.get("cnpj") else "")
        items.append((name, detail))
    return sentence, items


def _md_overlap_section(overlap: dict) -> list[str]:
    sentence, items = _overlap_highlights(overlap)
    if not sentence and not items:
        return []

    lines = ["## Destaques competitivos", ""]
    if sentence:
        lines += [sentence, ""]
    if items:
        lines += ["**Maiores diferencas de taxa para o mesmo fundo**", ""]
        for name, detail in items:
            lines.append(f"- {name} - {detail}")
        lines += [
            "",
            "Taxas canonicalizadas para percentual ao ano por casa; uma divergencia "
            "pode refletir classe ou serie diferente sob o mesmo CNPJ.",
            "",
        ]
    return lines


# --- markdown ----------------------------------------------------------------


def _md_shelf_section(diff: ShelfDiff, snapshot: ShelfSnapshot | None) -> list[str]:
    lines = [f"### {_platform_label(diff.platform)}", ""]

    if snapshot and snapshot.status == SnapshotStatus.UNAVAILABLE:
        lines += [f"Coleta indisponivel: {snapshot.error or 'motivo nao informado'}.", ""]
        return lines

    total = len(snapshot.funds) if snapshot else 0
    if diff.is_first_run:
        lines += [
            f"Primeira coleta: {total} fundos registrados como linha de base. "
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

    if not diff.has_changes:
        lines += [f"Sem mudancas. Prateleira estavel em {total} fundos.", ""]
        return lines

    lines += [
        f"{len(diff.added)} entradas, {len(diff.removed)} saidas, "
        f"{len(diff.changed)} alteracoes (prateleira em {total} fundos).",
        "",
    ]

    if diff.added:
        lines.append("**Entraram na prateleira**")
        lines.append("")
        for fund in diff.added[:_MAX_LISTED]:
            cnpj = f" ({format_cnpj(fund.cnpj)})" if fund.cnpj else ""
            manager = f" - {fund.manager_name}" if fund.manager_name else ""
            lines.append(f"- {fund.name}{cnpj}{manager}")
        if len(diff.added) > _MAX_LISTED:
            lines.append(f"- ... e mais {len(diff.added) - _MAX_LISTED} fundos")
        lines.append("")

    if diff.removed:
        lines.append("**Sairam da prateleira**")
        lines.append("")
        for fund in diff.removed[:_MAX_LISTED]:
            cnpj = f" ({format_cnpj(fund.cnpj)})" if fund.cnpj else ""
            lines.append(f"- {fund.name}{cnpj}")
        if len(diff.removed) > _MAX_LISTED:
            lines.append(f"- ... e mais {len(diff.removed) - _MAX_LISTED} fundos")
        lines.append("")

    if diff.changed:
        lines.append("**Mudancas de condicoes**")
        lines.append("")
        for change in diff.changed[:_MAX_LISTED]:
            details = ", ".join(
                f"{_FIELD_LABELS.get(c.field, c.field)}: "
                f"{_format_value(c.old)} -> {_format_value(c.new)}"
                for c in change.changes
            )
            lines.append(f"- {change.name} - {details}")
        if len(diff.changed) > _MAX_LISTED:
            lines.append(f"- ... e mais {len(diff.changed) - _MAX_LISTED} fundos")
        lines.append("")

    return lines


_CROSS_CANCELLED_LISTED = 10


def _md_cvm_cross(cross: dict | None) -> list[str]:
    """Blocos do cruzamento cadastro x prateleira dentro do Radar CVM."""
    if not cross or not cross.get("available"):
        return []
    lines: list[str] = []

    pre = cross.get("pre_op_on_shelf", {})
    if pre.get("count"):
        lines += ["**Ja em prateleira antes do lancamento**", ""]
        for item in pre.get("funds", [])[:_MAX_LISTED]:
            where = ", ".join(item.get("platforms", [])) or "-"
            listing = " - na vitrine" if item.get("on_listing") else ""
            lines.append(
                f"- [{item['house_label']}] {item['name']} ({item['cnpj']}) - "
                f"presente em: {where}{listing}"
            )
        shown = min(pre["count"], len(pre.get("funds", [])), _MAX_LISTED)
        if pre["count"] > shown:
            lines.append(f"- ... e mais {pre['count'] - shown} fundos")
        lines.append("")

    cancelled = cross.get("cancelled_on_shelf", {})
    if cancelled.get("count"):
        by_platform = ", ".join(
            f"{entry['label']} {entry['count']}"
            for entry in cancelled.get("by_platform", [])
        )
        lines += [
            "**Higiene de catalogo: cancelados na CVM ainda no catalogo**",
            "",
            f"{cancelled['count']} fundos com registro cancelado na CVM continuam no "
            f"catalogo coletado ({by_platform}). Um cancelado no catalogo Open Finance "
            "e cadastro administrado desatualizado; um cancelado na vitrine e alerta "
            f"forte ({cancelled.get('on_listing', 0)} na vitrine).",
            "",
        ]
        for item in cancelled.get("funds", [])[:_CROSS_CANCELLED_LISTED]:
            listing = " - NA VITRINE" if item.get("on_listing") else ""
            lines.append(f"- [{item['house_label']}] {item['name']}{listing}")
        if cancelled["count"] > _CROSS_CANCELLED_LISTED:
            lines.append(
                f"- ... e mais {cancelled['count'] - _CROSS_CANCELLED_LISTED} fundos"
            )
        lines.append("")

    return lines


def _md_cvm_section(
    diff: CvmDiff | None, report: RunReport, cross: dict | None = None
) -> list[str]:
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

    lines += _md_cvm_cross(cross)

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


def render_markdown(
    report: RunReport,
    overlap: dict | None = None,
    cvm_cross: dict | None = None,
) -> str:
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    if overlap is None:
        overlap = build_overlap_block(report)
    if cvm_cross is None:
        cvm_cross = build_cvm_cross_block(report)

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

    lines.append("| Plataforma | Fundos | Status | Entradas | Saidas | Alteracoes |")
    lines.append("|---|---:|---|---:|---:|---:|")
    for snapshot in report.snapshots:
        diff = report.diff_for(snapshot.platform)
        lines.append(
            f"| {_platform_label(snapshot.platform)} | {len(snapshot.funds)} | "
            f"{snapshot.status.value} | "
            f"{len(diff.added) if diff and not diff.is_first_run else 0} | "
            f"{len(diff.removed) if diff and not diff.is_first_run else 0} | "
            f"{len(diff.changed) if diff and not diff.is_first_run else 0} |"
        )
    lines.append("")

    lines += _md_overlap_section(overlap)

    lines += _md_cvm_section(report.cvm_diff, report, cvm_cross)

    lines += ["## Prateleiras", ""]
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

    total = len(snapshot.funds) if snapshot else 0
    if diff.is_first_run:
        parts.append(
            f'<p style="{_P}">Primeira coleta: {total} fundos registrados como linha '
            "de base.</p>"
        )
        return "".join(parts)

    if diff.suspect:
        parts.append(
            f'<p style="{_P}background:#fff8e6;border-left:3px solid #fab219;'
            f'padding:8px 10px;border-radius:4px;"><strong>Coleta suspeita</strong> '
            f"({_esc(diff.suspect_reason)}). Saidas omitidas para evitar falso churn.</p>"
        )

    if not diff.has_changes:
        parts.append(f'<p style="{_P}">Sem mudancas. Prateleira estavel em {total} fundos.</p>')
        return "".join(parts)

    parts.append(
        f'<p style="{_P}">{len(diff.added)} entradas, {len(diff.removed)} saidas, '
        f"{len(diff.changed)} alteracoes (prateleira em {total} fundos).</p>"
    )

    if diff.added:
        parts.append(f'<p style="{_P}margin-bottom:6px;"><strong>Entraram</strong></p>')
        parts.append(
            _html_list(
                [
                    _esc(f.name)
                    + (f" <span style='color:#898781;'>{format_cnpj(f.cnpj)}</span>" if f.cnpj else "")
                    for f in diff.added[:_MAX_LISTED]
                ],
                len(diff.added),
            )
        )

    if diff.removed:
        parts.append(f'<p style="{_P}margin-bottom:6px;"><strong>Sairam</strong></p>')
        parts.append(
            _html_list([_esc(f.name) for f in diff.removed[:_MAX_LISTED]], len(diff.removed))
        )

    if diff.changed:
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Mudancas de condicoes</strong></p>'
        )
        items = []
        for change in diff.changed[:_MAX_LISTED]:
            details = ", ".join(
                f"{_esc(_FIELD_LABELS.get(c.field, c.field))}: "
                f"{_esc(_format_value(c.old))} &rarr; <strong>{_esc(_format_value(c.new))}</strong>"
                for c in change.changes
            )
            items.append(f"{_esc(change.name)} &mdash; {details}")
        parts.append(_html_list(items, len(diff.changed)))

    return "".join(parts)


def _html_overlap_section(overlap: dict) -> str:
    sentence, items = _overlap_highlights(overlap)
    if not sentence and not items:
        return ""

    parts = [f'<h2 style="{_H2}">Destaques competitivos</h2>']
    if sentence:
        parts.append(f'<p style="{_P}">{_esc(sentence)}</p>')
    if items:
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Maiores diferencas de taxa '
            "para o mesmo fundo</strong></p>"
        )
        parts.append(
            _html_list(
                [f"{_esc(name)} &mdash; {_esc(detail)}" for name, detail in items],
                len(items),
            )
        )
        parts.append(
            f'<p style="{_P}font-size:12px;color:#898781;">Taxas canonicalizadas para '
            "percentual ao ano por casa; uma divergencia pode refletir classe ou serie "
            "diferente sob o mesmo CNPJ.</p>"
        )
    return "".join(parts)


def _html_cvm_cross(cross: dict | None) -> str:
    """Versao de e-mail do cruzamento cadastro x prateleira - top 5 de cada."""
    if not cross or not cross.get("available"):
        return ""
    parts: list[str] = []

    pre = cross.get("pre_op_on_shelf", {})
    if pre.get("count"):
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Ja em prateleira antes do '
            f"lancamento ({pre['count']})</strong></p>"
        )
        items = [
            f"[{_esc(item['house_label'])}] {_esc(item['name'])}"
            f" <span style='color:#898781;'>presente em "
            f"{_esc(', '.join(item.get('platforms', [])) or '-')}</span>"
            for item in pre.get("funds", [])[:5]
        ]
        parts.append(_html_list(items, pre["count"]))

    cancelled = cross.get("cancelled_on_shelf", {})
    if cancelled.get("count"):
        by_platform = ", ".join(
            f"{entry['label']} {entry['count']}"
            for entry in cancelled.get("by_platform", [])
        )
        parts.append(
            f'<p style="{_P}"><strong>Higiene de catalogo:</strong> '
            f"{cancelled['count']} fundos cancelados na CVM continuam no catalogo "
            f"coletado ({_esc(by_platform)}; {cancelled.get('on_listing', 0)} na "
            "vitrine).</p>"
        )

    return "".join(parts)


def _html_cvm_section(report: RunReport, cross: dict | None = None) -> str:
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

    parts.append(_html_cvm_cross(cross))

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


def render_html(
    report: RunReport,
    overlap: dict | None = None,
    cvm_cross: dict | None = None,
) -> str:
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    if overlap is None:
        overlap = build_overlap_block(report)
    if cvm_cross is None:
        cvm_cross = build_cvm_cross_block(report)
    sentence, is_alert = _summary_sentence(report)
    summary = f"<strong>{_esc(sentence)}</strong>" if is_alert else _esc(sentence)

    rows = ""
    for snapshot in report.snapshots:
        diff = report.diff_for(snapshot.platform)
        counted = diff and not diff.is_first_run
        rows += (
            f'<tr><td style="{_TD}">{_esc(_platform_label(snapshot.platform))}</td>'
            f'<td style="{_TD}text-align:right;">{len(snapshot.funds)}</td>'
            f'<td style="{_TD}">{_esc(snapshot.status.value)}</td>'
            f'<td style="{_TD}text-align:right;">{len(diff.added) if counted else 0}</td>'
            f'<td style="{_TD}text-align:right;">{len(diff.removed) if counted else 0}</td>'
            f'<td style="{_TD}text-align:right;">{len(diff.changed) if counted else 0}</td></tr>'
        )

    shelf_sections = "".join(
        _html_shelf_section(diff, report.snapshot_for(diff.platform))
        for diff in report.shelf_diffs
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
    <th style="{_TH}text-align:right;">Fundos</th>
    <th style="{_TH}">Status</th>
    <th style="{_TH}text-align:right;">Entradas</th>
    <th style="{_TH}text-align:right;">Saidas</th>
    <th style="{_TH}text-align:right;">Alteracoes</th>
  </tr></thead><tbody>{rows}</tbody></table>

  {_html_overlap_section(overlap)}

  {_html_cvm_section(report, cvm_cross)}

  <h2 style="{_H2}">Prateleiras</h2>
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

    # Calculados uma vez e passados aos dois renderers - o conteudo dos blocos
    # nunca diverge entre o markdown e o e-mail.
    overlap = build_overlap_block(report)
    cvm_cross = build_cvm_cross_block(report)

    md_path = config.reports_dir / f"digest-{today.isoformat()}.md"
    md_path.write_text(
        render_markdown(report, overlap=overlap, cvm_cross=cvm_cross),
        encoding="utf-8",
    )

    html_path = config.reports_dir / f"digest-{today.isoformat()}.html"
    html_path.write_text(
        render_html(report, overlap=overlap, cvm_cross=cvm_cross),
        encoding="utf-8",
    )

    return md_path, html_path
