"""Envio do digest por e-mail via SMTP.

Opcional: sem as variaveis SMTP configuradas o monitor apenas grava os arquivos,
e o envio fica a cargo de quem executa (um cliente de e-mail, um conector, um
job externo). O digest HTML ja e gerado com CSS inline e sem JS justamente para
sobreviver a qualquer um desses caminhos.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from shelf_monitor.config import Config
from shelf_monitor.models import RunReport, SnapshotStatus
from shelf_monitor.report.digest import render_html, render_markdown


def build_subject(report: RunReport) -> str:
    """Assunto que ja conta a novidade - o e-mail e lido pela linha de assunto."""
    date_label = report.generated_at.strftime("%d/%m")

    launches = len(report.cvm_diff.launches) if report.cvm_diff else 0
    new_registrations = (
        len(report.cvm_diff.new_registrations) if report.cvm_diff else 0
    )
    added = sum(len(d.added) for d in report.shelf_diffs if not d.is_first_run)
    removed = sum(len(d.removed) for d in report.shelf_diffs if not d.is_first_run)
    changed = sum(len(d.changed) for d in report.shelf_diffs if not d.is_first_run)

    highlights: list[str] = []
    if launches:
        highlights.append(f"{launches} fundo(s) em operacao")
    if new_registrations:
        highlights.append(f"{new_registrations} novo(s) registro(s) na CVM")
    if added or removed:
        highlights.append(f"{added} entradas / {removed} saidas")
    if changed:
        highlights.append(f"{changed} alteracoes")

    if not highlights:
        unavailable = sum(
            1 for s in report.snapshots if s.status == SnapshotStatus.UNAVAILABLE
        )
        if unavailable == len(report.snapshots) and report.snapshots:
            return f"[Monitor de fundos] {date_label} - nenhuma fonte acessivel"
        return f"[Monitor de fundos] {date_label} - sem mudancas"

    return f"[Monitor de fundos] {date_label} - " + ", ".join(highlights)


def send_digest(config: Config, report: RunReport) -> str:
    """Envia o digest. Devolve uma mensagem de resultado para exibir no CLI."""
    if not config.smtp_configured():
        return (
            "SMTP nao configurado (defina SMTP_HOST, SMTP_FROM e SMTP_TO); "
            "digest gravado em disco, sem envio"
        )

    message = EmailMessage()
    message["Subject"] = build_subject(report)
    message["From"] = config.smtp_from
    message["To"] = config.smtp_to
    message.set_content(render_markdown(report))
    message.add_alternative(render_html(report), subtype="html")

    recipients = [r.strip() for r in config.smtp_to.split(",") if r.strip()]
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.http_timeout) as smtp:
        smtp.starttls()
        if config.smtp_user:
            smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(message, to_addrs=recipients)

    return f"digest enviado para {', '.join(recipients)}"
