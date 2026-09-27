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
from shelf_monitor.report.shared import movimentos_do_run, rotulo_da_casa
from shelf_monitor.texto import contagem


def build_subject(report: RunReport, rotulo: str = Config.rotulo_assunto) -> str:
    """Assunto que ja conta a novidade - o e-mail e lido pela linha de assunto.

    `rotulo` identifica o monitor na caixa de entrada; o de previdencia passa o
    dele (`ConfigPrevidencia.rotulo_assunto`).
    """
    date_label = report.generated_at.strftime("%d/%m")

    launches = len(report.cvm_diff.launches) if report.cvm_diff else 0
    # Na primeira execucao new_registrations traz todos os pre-operacionais que
    # ja existiam: isso e baseline, nao novidade, e nao pode virar manchete.
    new_registrations = (
        len(report.cvm_diff.new_registrations)
        if report.cvm_diff and not report.cvm_diff.is_first_run
        else 0
    )
    # Movimento de vitrine, a mesma conta do corpo do digest: o assunto contava
    # o diff inteiro (catalogo Open Finance e restritos) e prometia mais do que
    # o e-mail mostrava - 236 contra 174 no BTG de 13 para 19/09.
    movimentos = movimentos_do_run(report)
    added = len(movimentos.entradas)
    removed = len(movimentos.saidas)
    changed = len(movimentos.alteracoes)
    rebaseadas = [
        rotulo_da_casa(d.platform) for d in report.shelf_diffs if d.nova_linha_de_base
    ]

    highlights: list[str] = []
    if launches:
        highlights.append(contagem(launches, "fundo em operação", "fundos em operação"))
    if new_registrations:
        highlights.append(
            contagem(new_registrations, "novo registro na CVM", "novos registros na CVM")
        )
    if added or removed:
        highlights.append(
            f"{contagem(added, 'entrada', 'entradas')} / {contagem(removed, 'saída', 'saídas')}"
        )
    if changed:
        highlights.append(contagem(changed, "alteração", "alterações"))
    if rebaseadas:
        # Sem isto o dia em que a coleta passa a ver a vitrine sai como "sem
        # mudancas". Antes da nova linha de base, saia como entrada: em 19/09 o
        # digest publicou "828 fundos entraram".
        highlights.append("nova linha de base: " + ", ".join(rebaseadas))

    if not highlights:
        unavailable = sum(
            1 for s in report.snapshots if s.status == SnapshotStatus.UNAVAILABLE
        )
        if unavailable == len(report.snapshots) and report.snapshots:
            return f"[{rotulo}] {date_label} - nenhuma fonte acessível"
        return f"[{rotulo}] {date_label} - sem mudanças na vitrine"

    return f"[{rotulo}] {date_label} - " + ", ".join(highlights)


def send_digest(config: Config, report: RunReport) -> str:
    """Envia o digest. Devolve uma mensagem de resultado para exibir no CLI."""
    if not config.smtp_configured():
        return (
            "SMTP não configurado (defina SMTP_HOST, SMTP_FROM e SMTP_TO); "
            "digest gravado em disco, sem envio"
        )

    message = EmailMessage()
    message["Subject"] = build_subject(report, config.rotulo_assunto)
    message["From"] = config.smtp_from
    message["To"] = config.smtp_to
    message.set_content(render_markdown(report, config))
    message.add_alternative(render_html(report, config), subtype="html")

    recipients = [r.strip() for r in config.smtp_to.split(",") if r.strip()]
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.http_timeout) as smtp:
        smtp.starttls()
        if config.smtp_user:
            smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(message, to_addrs=recipients)

    return f"digest enviado para {', '.join(recipients)}"
