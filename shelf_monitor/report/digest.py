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
from shelf_monitor.report.aggregate import (
    build_flows_block,
    build_investors_block,
    build_performance_block,
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


def _format_brl(value: float | None) -> str:
    """R$ compacto (bi/mi/mil): captacao se le em ordem de grandeza."""
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"{sign}R$ {magnitude / 1e9:.2f} bi".replace(".", ",")
    if magnitude >= 1e6:
        return f"{sign}R$ {magnitude / 1e6:.1f} mi".replace(".", ",")
    if magnitude >= 1e3:
        return f"{sign}R$ {magnitude / 1e3:.0f} mil"
    return f"{sign}R$ {magnitude:.0f}"


def _flows_intro(block: dict) -> str:
    """A frase que rotula a janela - nenhum numero de captacao sai sem ela."""
    intro = "Captacao liquida (aplicacoes menos resgates) do informe diario da CVM"
    if block["window_months"]:
        intro += f", em ate {block['window_months']} meses fechados"
    if block["as_of"]:
        intro += f", ate {block['as_of']}"
    return intro + ". O mes corrente e parcial e fica fora das somas."


def _md_flows_section(report: RunReport) -> list[str]:
    block = build_flows_block(report)
    lines = ["## Captacao (CVM)", ""]

    if not block["available"]:
        lines += [
            "Captacao indisponivel: importe o informe diario com "
            "`python -m shelf_monitor market`.",
            "",
        ]
        return lines

    lines += [_flows_intro(block), ""]

    platforms = [p for p in block["platforms"] if p["total"]]
    if any(p["matched"] for p in platforms):
        lines.append("| Casa | Liquido 3m | Liquido 12m | Cobertura |")
        lines.append("|---|---:|---:|---:|")
        for p in platforms:
            lines.append(
                f"| {p['label']} | {_format_brl(p['net_3m'])} | "
                f"{_format_brl(p['net_12m'])} | {p['matched']}/{p['total']} |"
            )
        lines.append("")

    for title, movers in (
        ("**Maiores captadores da prateleira (12m)**", block["top_inflow"][:5]),
        ("**Maiores resgates da prateleira (12m)**", block["top_outflow"][:5]),
    ):
        if not movers:
            continue
        lines += [title, ""]
        for item in movers:
            manager = f" ({item['manager']})" if item.get("manager") else ""
            houses = ", ".join(item["platforms"])
            lines.append(
                f"- {item['name']}{manager}: {_format_brl(item['net_12m'])} - {houses}"
            )
        lines.append("")

    managers = block["managers"]
    if managers and managers.get("houses"):
        lines += ["**Gestoras da watchlist - mercado inteiro**", ""]
        lines.append("| Casa | Liquido 3m | Liquido 12m | PL | Classes |")
        lines.append("|---|---:|---:|---:|---:|")
        for house in managers["houses"]:
            lines.append(
                f"| {house['label']} | {_format_brl(house['net_3m'])} | "
                f"{_format_brl(house['net_12m'])} | {_format_brl(house['pl'])} | "
                f"{house['classes']} |"
            )
        lines.append("")
        market = managers.get("market") or {}
        coverage = managers.get("coverage") or {}
        if market:
            lines.append(
                f"Mercado inteiro: {_format_brl(market.get('net_12m'))} liquidos em "
                f"12m, PL de {_format_brl(market.get('pl'))} em "
                f"{market.get('classes', 0)} classes com informe"
                + (
                    f"; gestora identificada em {coverage.get('classes_com_gestora')} "
                    f"delas."
                    if coverage
                    else "."
                )
            )
            lines.append("")

    return lines


def _format_pct(value: float | None, digits: int = 1) -> str:
    """Fracao -> percentual pt-BR: 0.1234 -> '12,3%'."""
    if value is None:
        return "-"
    return f"{value * 100:.{digits}f}%".replace(".", ",")


def _format_int(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{int(round(value)):,}".replace(",", ".")


def _performance_intro(block: dict) -> str:
    intro = "Retorno pela cota do informe diario, em meses fechados"
    if block.get("window_end"):
        intro += f" ate {block['window_end']}"
    if block.get("window_months"):
        intro += f" (ate {block['window_months']} meses de cota)"
    cdi = block.get("cdi") or {}
    cdi_12m = (cdi.get("windows") or {}).get("12m")
    if cdi_12m is not None:
        intro += f". CDI de 12 meses na mesma janela: {_format_pct(cdi_12m, 2)} (BCB)."
    else:
        intro += ". CDI indisponivel: sem '% do CDI' nesta execucao."
    return intro


def _investors_intro(block: dict) -> str:
    intro = "Cotistas e PL por cotista (ticket medio) do informe diario"
    if block.get("as_of"):
        intro += f", ate {block['as_of']}"
    if block.get("window_months"):
        intro += f"; variacao de cotistas sobre ate {block['window_months']} meses fechados"
    return intro + "."


def _mover_line(item: dict, value: str, extra: str = "") -> str:
    manager = f" ({item['manager']})" if item.get("manager") else ""
    houses = ", ".join(item.get("platforms") or [])
    return f"- {item['name']}{manager}: {value}{extra} - {houses}"


def _md_performance_section(report: RunReport) -> list[str]:
    block = build_performance_block(report)
    lines = ["## Rentabilidade e risco (CVM)", ""]

    if not block["available"]:
        lines += [
            "Rentabilidade indisponivel: importe o informe diario com "
            "`python -m shelf_monitor market` (uma base importada antes da cota "
            "esta incompleta - o doctor avisa).",
            "",
        ]
        return lines

    lines += [_performance_intro(block), ""]
    has_cdi = any(p.get("above_cdi") is not None for p in block["platforms"])
    platforms = [p for p in block["platforms"] if p["matched"]]
    if platforms:
        header = "| Casa | Retorno 12m (mediana) | Vol. 12m (mediana) |"
        divider = "|---|---:|---:|"
        if has_cdi:
            header += " Acima do CDI |"
            divider += "---:|"
        lines.append(header + " Cobertura |")
        lines.append(divider + "---:|")
        for p in platforms:
            row = (
                f"| {p['label']} | {_format_pct(p['return_median'])} | "
                f"{_format_pct(p['vol_median'])} |"
            )
            if has_cdi:
                row += f" {p['above_cdi']} de {p['matched']} |"
            lines.append(row + f" {p['matched']}/{p['total']} |")
        lines.append("")

    for title, movers in (
        ("**Maiores retornos da prateleira (12m)**", block["top"][:5]),
        ("**Menores retornos da prateleira (12m)**", block["bottom"][:5]),
    ):
        if not movers:
            continue
        lines += [title, ""]
        for item in movers:
            extra = ""
            if item.get("vol_12m") is not None:
                extra += f", vol. {_format_pct(item['vol_12m'])}"
            if item.get("pct_cdi") is not None:
                extra += f", {_format_pct(item['pct_cdi'], 0)} do CDI"
            lines.append(_mover_line(item, _format_pct(item["return_12m"]), extra))
        lines.append("")

    return lines


def _md_investors_section(report: RunReport) -> list[str]:
    block = build_investors_block(report)
    lines = ["## Cotistas e ticket (CVM)", ""]

    if not block["available"]:
        lines += [
            "Cotistas indisponiveis: importe o informe diario com "
            "`python -m shelf_monitor market`.",
            "",
        ]
        return lines

    lines += [_investors_intro(block), ""]
    platforms = [p for p in block["platforms"] if p["matched"]]
    if platforms:
        lines.append(
            "| Casa | Cotistas | Ticket mediano | Ticket < R$ 10 mil | "
            "Var. cotistas 12m | Cobertura |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|")
        for p in platforms:
            share = (
                _format_pct(p["small_ticket_share"], 0)
                if p["small_ticket_share"] is not None
                else "-"
            )
            delta = "-"
            if p["delta_12m"] is not None:
                delta = _format_int(p["delta_12m"])
                if p["delta_12m_pct"] is not None:
                    delta += f" ({_format_pct(p['delta_12m_pct'])})"
            lines.append(
                f"| {p['label']} | {_format_int(p['shareholders'])} | "
                f"{_format_brl(p['ticket_median'])} | {share} | {delta} | "
                f"{p['matched']}/{p['total']} |"
            )
        lines.append("")

    for title, movers in (
        ("**Maiores ganhos de cotistas (12m)**", block["top_gain"][:5]),
        ("**Maiores perdas de cotistas (12m)**", block["top_loss"][:5]),
    ):
        if not movers:
            continue
        lines += [title, ""]
        for item in movers:
            value = f"{'+' if item['delta_12m'] > 0 else ''}{_format_int(item['delta_12m'])} cotistas"
            extra = ""
            if item.get("delta_pct_12m") is not None:
                extra += f" ({_format_pct(item['delta_pct_12m'])})"
            if item.get("ticket") is not None:
                extra += f", ticket {_format_brl(item['ticket'])}"
            lines.append(_mover_line(item, value, extra))
        lines.append("")

    managers = block["managers"]
    if managers and managers.get("houses"):
        lines += ["**Gestoras da watchlist - cotistas no mercado inteiro**", ""]
        lines.append("| Casa | Cotistas | Var. 12m | Classes com base |")
        lines.append("|---|---:|---:|---:|")
        for house in managers["houses"]:
            lines.append(
                f"| {house['label']} | {_format_int(house.get('shareholders'))} | "
                f"{_format_int(house.get('shareholders_delta_12m'))} | "
                f"{house.get('classes_with_delta', 0)}/{house['classes']} |"
            )
        lines.append("")

    return lines


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

    lines += _md_cvm_section(report.cvm_diff, report)

    lines += _md_flows_section(report)
    lines += _md_performance_section(report)
    lines += _md_investors_section(report)

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


def _html_flows_section(report: RunReport) -> str:
    block = build_flows_block(report)
    parts = [f'<h2 style="{_H2}">Captacao (CVM)</h2>']

    if not block["available"]:
        parts.append(
            f'<p style="{_P}">Captacao indisponivel: importe o informe diario com '
            "<code>python -m shelf_monitor market</code>.</p>"
        )
        return "".join(parts)

    parts.append(f'<p style="{_P}">{_esc(_flows_intro(block))}</p>')

    platforms = [p for p in block["platforms"] if p["total"]]
    if any(p["matched"] for p in platforms):
        rows = "".join(
            f'<tr><td style="{_TD}">{_esc(p["label"])}</td>'
            f'<td style="{_TD}text-align:right;">{_esc(_format_brl(p["net_3m"]))}</td>'
            f'<td style="{_TD}text-align:right;">{_esc(_format_brl(p["net_12m"]))}</td>'
            f'<td style="{_TD}text-align:right;">{p["matched"]}/{p["total"]}</td></tr>'
            for p in platforms
        )
        parts.append(
            f'<table style="{_TABLE}"><thead><tr>'
            f'<th style="{_TH}">Casa</th>'
            f'<th style="{_TH}text-align:right;">Liquido 3m</th>'
            f'<th style="{_TH}text-align:right;">Liquido 12m</th>'
            f'<th style="{_TH}text-align:right;">Cobertura</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )

    for title, movers in (
        ("Maiores captadores da prateleira (12m)", block["top_inflow"][:5]),
        ("Maiores resgates da prateleira (12m)", block["top_outflow"][:5]),
    ):
        if not movers:
            continue
        parts.append(f'<p style="{_P}margin-bottom:6px;"><strong>{title}</strong></p>')
        parts.append(
            _html_list(
                [
                    _esc(item["name"])
                    + (f" ({_esc(item['manager'])})" if item.get("manager") else "")
                    + f": <strong>{_esc(_format_brl(item['net_12m']))}</strong>"
                    + f" <span style='color:#898781;'>{_esc(', '.join(item['platforms']))}</span>"
                    for item in movers
                ],
                len(movers),
            )
        )

    managers = block["managers"]
    if managers and managers.get("houses"):
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Gestoras da watchlist &mdash; '
            "mercado inteiro</strong></p>"
        )
        rows = "".join(
            f'<tr><td style="{_TD}">{_esc(h["label"])}</td>'
            f'<td style="{_TD}text-align:right;">{_esc(_format_brl(h["net_3m"]))}</td>'
            f'<td style="{_TD}text-align:right;">{_esc(_format_brl(h["net_12m"]))}</td>'
            f'<td style="{_TD}text-align:right;">{_esc(_format_brl(h["pl"]))}</td>'
            f'<td style="{_TD}text-align:right;">{h["classes"]}</td></tr>'
            for h in managers["houses"]
        )
        parts.append(
            f'<table style="{_TABLE}"><thead><tr>'
            f'<th style="{_TH}">Casa</th>'
            f'<th style="{_TH}text-align:right;">Liquido 3m</th>'
            f'<th style="{_TH}text-align:right;">Liquido 12m</th>'
            f'<th style="{_TH}text-align:right;">PL</th>'
            f'<th style="{_TH}text-align:right;">Classes</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
        market = managers.get("market") or {}
        if market:
            parts.append(
                f'<p style="{_P}">Mercado inteiro: '
                f"{_esc(_format_brl(market.get('net_12m')))} liquidos em 12m, PL de "
                f"{_esc(_format_brl(market.get('pl')))} em "
                f"{market.get('classes', 0)} classes com informe.</p>"
            )

    return "".join(parts)


def _html_table(headers: list[tuple[str, bool]], rows: list[list[str]]) -> str:
    """Tabela de e-mail: (rotulo, numerico) por coluna; celulas ja escapadas."""
    head = "".join(
        f'<th style="{_TH}{"text-align:right;" if numeric else ""}">{_esc(label)}</th>'
        for label, numeric in headers
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td style="{_TD}{"text-align:right;" if headers[i][1] else ""}">{cell}</td>'
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f'<table style="{_TABLE}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _html_movers(title: str, items: list[str]) -> str:
    if not items:
        return ""
    return (
        f'<p style="{_P}margin-bottom:6px;"><strong>{title}</strong></p>'
        + _html_list(items, len(items))
    )


def _html_performance_section(report: RunReport) -> str:
    block = build_performance_block(report)
    parts = [f'<h2 style="{_H2}">Rentabilidade e risco (CVM)</h2>']
    if not block["available"]:
        parts.append(
            f'<p style="{_P}">Rentabilidade indisponivel: importe o informe diario '
            "com <code>python -m shelf_monitor market</code>.</p>"
        )
        return "".join(parts)

    parts.append(f'<p style="{_P}">{_esc(_performance_intro(block))}</p>')
    has_cdi = any(p.get("above_cdi") is not None for p in block["platforms"])
    platforms = [p for p in block["platforms"] if p["matched"]]
    if platforms:
        headers = [("Casa", False), ("Retorno 12m (mediana)", True), ("Vol. 12m", True)]
        if has_cdi:
            headers.append(("Acima do CDI", True))
        headers.append(("Cobertura", True))
        rows = []
        for p in platforms:
            row = [_esc(p["label"]), _esc(_format_pct(p["return_median"])),
                   _esc(_format_pct(p["vol_median"]))]
            if has_cdi:
                row.append(f"{p['above_cdi']} de {p['matched']}")
            row.append(f"{p['matched']}/{p['total']}")
            rows.append(row)
        parts.append(_html_table(headers, rows))

    for title, movers in (
        ("Maiores retornos da prateleira (12m)", block["top"][:5]),
        ("Menores retornos da prateleira (12m)", block["bottom"][:5]),
    ):
        items = []
        for item in movers:
            extra = ""
            if item.get("vol_12m") is not None:
                extra += f", vol. {_format_pct(item['vol_12m'])}"
            if item.get("pct_cdi") is not None:
                extra += f", {_format_pct(item['pct_cdi'], 0)} do CDI"
            items.append(
                _esc(item["name"])
                + (f" ({_esc(item['manager'])})" if item.get("manager") else "")
                + f": <strong>{_esc(_format_pct(item['return_12m']))}</strong>{_esc(extra)}"
                + f" <span style='color:#898781;'>{_esc(', '.join(item['platforms']))}</span>"
            )
        parts.append(_html_movers(title, items))
    return "".join(parts)


def _html_investors_section(report: RunReport) -> str:
    block = build_investors_block(report)
    parts = [f'<h2 style="{_H2}">Cotistas e ticket (CVM)</h2>']
    if not block["available"]:
        parts.append(
            f'<p style="{_P}">Cotistas indisponiveis: importe o informe diario '
            "com <code>python -m shelf_monitor market</code>.</p>"
        )
        return "".join(parts)

    parts.append(f'<p style="{_P}">{_esc(_investors_intro(block))}</p>')
    platforms = [p for p in block["platforms"] if p["matched"]]
    if platforms:
        rows = []
        for p in platforms:
            delta = "-"
            if p["delta_12m"] is not None:
                delta = _format_int(p["delta_12m"])
                if p["delta_12m_pct"] is not None:
                    delta += f" ({_format_pct(p['delta_12m_pct'])})"
            rows.append([
                _esc(p["label"]), _format_int(p["shareholders"]),
                _esc(_format_brl(p["ticket_median"])),
                _format_pct(p["small_ticket_share"], 0) if p["small_ticket_share"] is not None else "-",
                _esc(delta), f"{p['matched']}/{p['total']}",
            ])
        parts.append(_html_table(
            [("Casa", False), ("Cotistas", True), ("Ticket mediano", True),
             ("Ticket < R$ 10 mil", True), ("Var. cotistas 12m", True), ("Cobertura", True)],
            rows,
        ))

    for title, movers in (
        ("Maiores ganhos de cotistas (12m)", block["top_gain"][:5]),
        ("Maiores perdas de cotistas (12m)", block["top_loss"][:5]),
    ):
        items = []
        for item in movers:
            sign = "+" if item["delta_12m"] > 0 else ""
            extra = ""
            if item.get("delta_pct_12m") is not None:
                extra += f" ({_format_pct(item['delta_pct_12m'])})"
            if item.get("ticket") is not None:
                extra += f", ticket {_format_brl(item['ticket'])}"
            items.append(
                _esc(item["name"])
                + (f" ({_esc(item['manager'])})" if item.get("manager") else "")
                + f": <strong>{sign}{_format_int(item['delta_12m'])} cotistas</strong>{_esc(extra)}"
                + f" <span style='color:#898781;'>{_esc(', '.join(item['platforms']))}</span>"
            )
        parts.append(_html_movers(title, items))

    managers = block["managers"]
    if managers and managers.get("houses"):
        parts.append(
            f'<p style="{_P}margin-bottom:6px;"><strong>Gestoras da watchlist &mdash; '
            "cotistas no mercado inteiro</strong></p>"
        )
        parts.append(_html_table(
            [("Casa", False), ("Cotistas", True), ("Var. 12m", True), ("Classes com base", True)],
            [[_esc(h["label"]), _format_int(h.get("shareholders")),
              _format_int(h.get("shareholders_delta_12m")),
              f"{h.get('classes_with_delta', 0)}/{h['classes']}"] for h in managers["houses"]],
        ))
    return "".join(parts)


def render_html(report: RunReport) -> str:
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
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

  {_html_cvm_section(report)}

  {_html_flows_section(report)}

  {_html_performance_section(report)}

  {_html_investors_section(report)}

  <h2 style="{_H2}">Prateleiras</h2>
  {shelf_sections}

  <p style="font-size:12px;color:#898781;margin-top:24px;border-top:1px solid #e1e0d9;padding-top:14px;">
    Fontes publicas: sites das plataformas, API de Dados Abertos de Investimentos do Open
    Finance Brasil, cadastro e informe diario de fundos da CVM.
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
