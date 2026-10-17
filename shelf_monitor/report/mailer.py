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
from shelf_monitor.report.digest import _mm, render_html, render_markdown
from shelf_monitor.report.digest_visao import EDICAO_SEMANAL, montar_visao
from shelf_monitor.report.shared import movimentos_do_run, rotulo_da_casa
from shelf_monitor.texto import contagem


def build_subject(
    report: RunReport, rotulo: str = Config.rotulo_assunto, config: Config | None = None
) -> str:
    """Assunto que ja conta a novidade - o e-mail e lido pela linha de assunto.

    Abre com a mensagem principal do digest - quantos gaps reais aceleram e
    quanto captam na semana - e segue com o que entrou e saiu das vitrines, so
    das casas do digest (`Config.casas_do_digest`). Alteracao de condicao nao
    conta mais desde 14/10/2026, e o que as gestoras registraram na CVM desde
    17/10/2026, quando o radar saiu do digest.

    `rotulo` identifica o monitor na caixa de entrada; o de previdencia passa o
    dele (`ConfigPrevidencia.rotulo_assunto`).
    """
    date_label = report.generated_at.strftime("%d/%m")
    visao = montar_visao(report, config, EDICAO_SEMANAL)
    recortado = visao.report

    # Movimento de vitrine, a mesma conta do corpo do digest: o assunto contava
    # o diff inteiro (catalogo Open Finance e restritos) e prometia mais do que
    # o e-mail mostrava - 236 contra 174 no BTG de 13 para 19/09.
    movimentos = movimentos_do_run(recortado)
    added = len(movimentos.entradas)
    removed = len(movimentos.saidas)
    rebaseadas = [
        rotulo_da_casa(d.platform) for d in recortado.shelf_diffs if d.nova_linha_de_base
    ]

    highlights: list[str] = []
    oportunidade = visao.oportunidade
    if oportunidade.get("com_fluxo") and oportunidade.get("total_transferiveis"):
        grade = contagem(
            oportunidade["total_transferiveis"], "fundo para a grade", "fundos para a grade"
        )
        if oportunidade.get("captacao_semana") is not None:
            grade += f" ({_mm(oportunidade['captacao_semana'])} na semana)"
        highlights.append(grade)
    if added or removed:
        highlights.append(
            f"{contagem(added, 'entrada', 'entradas')} / {contagem(removed, 'saída', 'saídas')}"
        )
    if rebaseadas:
        # Sem isto o dia em que a coleta passa a ver a vitrine sai como "sem
        # mudancas". Antes da nova linha de base, saia como entrada: em 19/09 o
        # digest publicou "828 fundos entraram".
        highlights.append("nova linha de base: " + ", ".join(rebaseadas))

    if not highlights:
        snapshots = recortado.snapshots
        unavailable = sum(1 for s in snapshots if s.status == SnapshotStatus.UNAVAILABLE)
        if unavailable == len(snapshots) and snapshots:
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
    message["Subject"] = build_subject(report, config.rotulo_assunto, config)
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
