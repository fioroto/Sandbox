"""Digest semanal: a oportunidade de grade, o ritmo da captacao, o radar e a vitrine.

Gera markdown (leitura no repo, diff de git legivel) e HTML com CSS inline,
sem JS e com largura contida - pronto para ir no corpo do e-mail.

**Versao 2 (14/10/2026), curta e direta**, pelos comentarios da area
(alinhados com o Davi): o digest e semanal e deve trazer pouquissima
informacao. So XP, BTG e Itau (`Config.casas_do_digest`, recorte em
`digest_visao`); a mensagem principal, com mais destaque, e a oportunidade de
grade e os gaps reais. Na ordem:

1. **Oportunidade de grade e gaps** (`report.oportunidade`): os numeros em
   destaque, o resumo por concorrente e os gaps reais que estao captando, com
   a captacao da semana, a de 3 meses, o ritmo e o esforco de trazer cada um;
2. **Ritmo de captacao**: o mapa de ritmo por casa e as maiores captacoes e
   resgates, agora com a semana;
3. **Radar CVM**: o que as gestoras do BTG e do Itau registraram - so os
   concorrentes, e dito que e a gestora, nao a vitrine;
4. **Vitrines**: so o que entrou e o que saiu;
5. **Rentabilidade e custo**, so na edicao mensal (o primeiro digest do mes).

Saiu do semanal - e continua no dashboard e na planilha: o sumario com o
tamanho das vitrines, o funil e os gaps por categoria, os ja cobertos, a
captacao por casa, por familia e mes a mes (a CVM nao atribui fluxo a
distribuidor), o ticket de entrada, as alteracoes de condicao e as listas
"XP perdendo", "Acelerando" e "Viraram saida". Todo fundo da casa sai com o
servico da lista (`distribuivel`): Distribuicao ou FaaS.

Em 15/10/2026, a pedido da area, sairam tambem a frase de abertura (a
oportunidade em uma frase, que repetia os numeros em destaque), a frase do
funil ("504 gaps reais nas vitrines...") e a evolucao semanal da captacao dos
gaps, com as miniaturas por casa. O digest abre direto nas secoes; a unica
frase antes delas e o alerta de coleta que falhou inteira.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.models import (
    BLOCO_COTISTAS,
    CvmDiff,
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
from shelf_monitor.report.retail_flow import nome_da_tendencia
from shelf_monitor.report.shared.semanas import prazos_da_semana
from shelf_monitor.texto import (
    competencia_br,
    contagem,
    data_br,
    meses_br,
    milhar,
    periodo_br,
)
from shelf_monitor.report.shared.estilo import (
    TINTA,
    TINTA_2,
    contorno_da_casa,
    cor_da_casa,
)
from shelf_monitor.report.shared.fontes import (
    CVM_CADASTRO,
    CVM_EXTRATO,
    CVM_INFORME_DIARIO,
    Fonte,
    fontes_do_relatorio,
)
# A casca, a tabela e os estilos do e-mail moram em shared/email_xp.py (os
# outros relatorios usam os mesmos). `_EMAIL_BODY`, `_EMAIL_CARD`, `_H2`, `_LI`,
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
    miudo as _miudo,
    pontuado as _linha,
    secao as _secao,
    tabela as _tabela,
)
from shelf_monitor.report.shared.graficos_email import (
    Celula,
    Linha,
    LinhaDeCalor,
    barras,
    barras_divergentes,
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
from shelf_monitor.report.aggregate import listing_funds, retail_funds
from shelf_monitor.report.digest_visao import (
    VisaoDoDigest,
    edicao_do_digest,
    montar_visao,
)

_MAX_LISTED = 25

# A oportunidade listada no e-mail, por concorrente: os 5 primeiros de cada um
# (o Itau sempre ao lado do BTG). O resto mora na planilha.
_GAP_POR_CASA = 5
# Quantos fundos listar em cada lista de captacao. O digest e leitura de 2
# minutos: a lista inteira mora na planilha.
_FLOW_LISTED = 5

_platform_label = rotulo_da_casa


# --- texto ---------------------------------------------------------------------


def _juntar(itens: list[str]) -> str:
    """"XP, BTG e Itau"."""
    if len(itens) < 2:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _capitalizar(frase: str | None) -> str:
    frase = frase or ""
    return frase[:1].upper() + frase[1:]


def _contagem(valor: int) -> str:
    """Numero absoluto. `_inteiro` poe sinal, porque serve a variacao."""
    return f"{valor:,}".replace(",", ".")


def _mm(valor: float) -> str:
    """Reais em milhoes com sinal - a unidade em que fluxo de fundo se le."""
    return f"{valor / 1_000_000:+,.1f} MM".replace(",", "X").replace(".", ",").replace("X", ".")


def _mm_ou_traco(valor: float | None) -> str:
    return "-" if valor is None else _mm(valor)


def _inteiro(valor: int) -> str:
    """Inteiro com sinal e separador de milhar brasileiro.

    Existe para que o `.replace` fique preso ao numero: aplicado sobre uma
    f-string concatenada, ele trocava as virgulas da frase inteira por pontos.
    """
    return f"{valor:+,}".replace(",", ".")


def _mil(valor: float) -> str:
    """"R$ 101 mil" - o ticket por cotista novo."""
    return f"R$ {valor / 1000:,.0f} mil".replace(",", ".")


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


def _link(fonte: Fonte) -> str:
    return f"[{fonte.nome}]({fonte.url})"


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


# A forma juridica por extenso, como o cadastro da CVM e o Open Finance a
# escrevem, e a sigla dela. O nome de registro do Itau ocupava sete linhas na
# tabela do e-mail ("JGP SELECT FUNDO DE INVESTIMENTO FINANCEIRO DA CLASSE DE
# INVESTIMENTO EM COTAS ... RESPONSABILIDADE LIMITADA"). So a exibicao muda:
# o nome guardado e o CNPJ continuam os da fonte.
_FORMA_JURIDICA = tuple(
    (re.compile(rf"\b{expressao}\b", re.IGNORECASE), sigla)
    for expressao, sigla in (
        (r"FUNDO DE INVESTIMENTO EM COTAS DE FUNDOS DE INVESTIMENTO", "FIC FI"),
        (r"FUNDO INCENTIVADO DE INVESTIMENTO(?: FINANCEIRO)? EM INFRAESTRUTURA", "FI INFRA"),
        (r"FUNDO DE INVESTIMENTO FINANCEIRO", "FIF"),
        (r"FUNDO DE INVESTIMENTO", "FI"),
        (r"(?:DA )?CLASSE DE INVESTIMENTO EM COTAS(?: DE FUNDOS DE INVESTIMENTO)?", "CIC"),
        (r"(?:DA )?CLASSE DE INVESTIMENTO", ""),
        (r"RESPONSABILIDADE LIMITADA", "RL"),
        (r"RESP\.? LIMITADA", "RL"),
    )
)


def _nome_do_fundo(nome: str | None) -> str:
    """O nome para ler: a forma juridica em sigla, o resto como veio."""
    texto = nome or ""
    for padrao, sigla in _FORMA_JURIDICA:
        texto = padrao.sub(sigla, texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip(" -")
    return texto or (nome or "")


def _casas_do_fundo(fundo: dict, visao: VisaoDoDigest) -> str:
    """"XP · FaaS, BTG" - as casas do digest que exibem o fundo, a XP primeiro.

    Fundo que a XP tem - na vitrine ou na lista da casa - sai com o servico que
    ela presta a ele (Distribuição ou FaaS), quando a lista o traz.
    """
    cnpj = fundo.get("cnpj")
    vitrines = [p for p in fundo.get("vitrines") or [] if p in visao.casas]
    partes = []
    if visao.base_id in vitrines or visao.da_casa(cnpj):
        servico = visao.servico(cnpj)
        partes.append(_casas_curtas([visao.base_id]) + (f" · {servico}" if servico else ""))
    outras = [p for p in vitrines if p != visao.base_id]
    if outras:
        partes.append(_casas_curtas(outras))
    return ", ".join(partes) or "-"


def _concorrentes(visao: VisaoDoDigest) -> str:
    """"BTG Pactual e Itaú" - os concorrentes do digest, pelo rotulo."""
    return _juntar([_platform_label(c) for c in visao.concorrentes])


# --- prazos --------------------------------------------------------------------


def _competencia(chave: str) -> str:
    return f"{chave[4:]}/{chave[:4]}"


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


def _janela_da_captacao(periodo: dict) -> str:
    """"3 meses, de 01/07/2026 a 25/09/2026"."""
    meses = periodo.get("meses_lidos") or []
    texto = periodo_br(periodo.get("de"), periodo.get("ate"))
    return f"{meses_br(len(meses))}, {texto}" if meses else texto


def _recorte_semanal(flow: dict | None) -> dict | None:
    return (flow or {}).get("recorte_semanal") if (flow or {}).get("available") else None


def _semana(flow: dict | None) -> dict | None:
    """A semana de referencia (de, ate, dias), ou None sem ela."""
    recorte = _recorte_semanal(flow)
    semana = (recorte or {}).get("semana")
    return semana if semana and semana.get("dias") else None


def _prazos_da_captacao(flow: dict) -> list[str]:
    """Os prazos de toda leitura de captacao: semana, comparacao, 3 meses, o que ficou fora."""
    linhas = []
    recorte = _recorte_semanal(flow)
    if recorte and (recorte.get("semana") or {}).get("dias"):
        linhas += prazos_da_semana(recorte["semana"], recorte["comparacao"])
    linhas.append(_prazo_da_captacao(flow.get("periodo") or {}))
    incompletos = (flow.get("periodo") or {}).get("dias_incompletos") or []
    if incompletos:
        linhas.append(
            "fora da conta, ainda chegando: " + ", ".join(
                f"{data_br(d['dia'])} ({d['cobertura']:.0%} dos fundos)" for d in incompletos
            )
        )
    return linhas


# --- abertura ------------------------------------------------------------------


def _summary_counts(report: RunReport) -> tuple[int, int, int, int]:
    """(entradas, saidas, alteracoes) de vitrine, e o que mudou so no catalogo."""
    total = movimentos_do_run(report)
    return (
        len(total.entradas), len(total.saidas), len(total.alteracoes), total.so_catalogo
    )


def _summary_sentence(report: RunReport) -> tuple[str, bool]:
    """Frase de movimento das vitrines, e se ela e um alerta.

    Distingue tres silencios que se parecem no numero mas nao no significado:
    coleta que falhou inteira, primeira coleta, e prateleira estavel. O
    dashboard abre com ela; o digest so quando nao ha oportunidade de grade.
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


def _alerta(visao: VisaoDoDigest) -> str | None:
    """A unica frase antes das secoes: a coleta que falhou inteira.

    Ate 15/10/2026 o digest abria com a oportunidade em uma frase ("67 fundos
    captam nas vitrines...") ou com o movimento das vitrines - repetiam os
    numeros em destaque e a secao de vitrines, e sairam a pedido da area. Fica
    o alerta, que nenhuma secao da.
    """
    report = visao.report
    coletadas = [s for s in report.snapshots if s.status != SnapshotStatus.UNAVAILABLE]
    if report.snapshots and not coletadas:
        return _summary_sentence(report)[0]
    return None


def _linha_do_cabecalho(visao: VisaoDoDigest, gerado: str) -> str:
    """"BTG Pactual e Itaú · semana de ... · execução de ..." - sem markup.

    E tambem a previa do e-mail quando nao ha alerta.
    """
    semana = _semana(visao.report.retail_flow)
    return _linha(
        _concorrentes(visao),
        f"semana {periodo_br(semana['de'], semana['ate'])}" if semana else "",
        f"execução de {gerado}",
        "edição mensal" if visao.mensal else "",
    )


# --- oportunidade de grade e gaps ----------------------------------------------


def _numeros_da_oportunidade(op: dict) -> list[tuple]:
    """Os numeros em destaque, (valor, rotulo[, destaque]): os mesmos no HTML e no markdown.

    "Gap real" so com a carteira da CVM: sem ela, espelho, equivalente e
    correlato nao foram verificados.
    """
    n = op["total_transferiveis"]
    real = (op.get("carteira") or {}).get("available")
    gaps = ("gap real" if n == 1 else "gaps reais") if real else ("gap" if n == 1 else "gaps")
    deles = "dele" if n == 1 else "deles"
    numeros: list[tuple] = [
        (milhar(n), f"{'fundo' if n == 1 else 'fundos'} para a grade: {gaps} captando", True)
    ]
    if op.get("captacao_semana") is not None:
        numeros.append((_mm(op["captacao_semana"]), f"captação {deles} na semana"))
    numeros.append((_mm(op["captacao_3m"]), f"captação {deles} em 3 meses"))
    if op.get("com_cotistas"):
        numeros.append((_inteiro(op["cotistas_3m"]), "cotistas em 3 meses"))
    abrir = op["total_abrir_gestora"]
    numeros += [
        (milhar(op["total_operacionais"]), "de gestora que a XP já distribui"),
        (milhar(abrir), "exige abrir gestora" if abrir == 1 else "exigem abrir gestora"),
    ]
    if op.get("total_gestora_indefinida"):
        numeros.append((milhar(op["total_gestora_indefinida"]), "sem gestora identificada"))
    return numeros


# Sem o informe diario nao ha numero em destaque, e o leitor tem de saber por que.
_SEM_CAPTACAO = (
    "Sem o informe diário da CVM nesta execução, a captação dos gaps não foi medida; "
    "a lista segue o esforço de trazer, sem o produto próprio do concorrente."
)


def _notas_da_oportunidade(visao: VisaoDoDigest) -> list[str]:
    """O que e gap real, o que ficou fora e de onde vem cada numero - em frases curtas."""
    op = visao.oportunidade
    carteira = op.get("carteira") or {}
    competencias = carteira.get("competencias") or []
    criterio = (
        "Gap real: fundo na vitrine do concorrente que a XP não tem de forma nenhuma - "
        "fora da vitrine e da lista da casa, sem espelho nem equivalente pela carteira "
        "mensal da CVM"
        + (f" (CDA de {competencia_br(competencias[0])})" if competencias else "")
        + ", sem outra versão da mesma estratégia da mesma gestora (Advisory, Seleção, "
        "Access, II, Plus) e sem ser só outra faixa de liquidez."
        if carteira.get("available") else
        "Gap: fundo na vitrine do concorrente e fora da vitrine da XP e da lista da casa, "
        "CNPJ contra CNPJ - sem a carteira da CVM, espelho, equivalente e outra versão da "
        "mesma estratégia não foram verificados."
    )
    notas = [criterio]
    if op.get("total_reais"):
        notas.append(
            "Na planilha: os gaps reais na aba Gaps XP e os cobertos, com o fundo da XP "
            "que cobre cada um, na aba Gaps cobertos."
            if carteira.get("available") else
            "A lista completa dos gaps está na planilha, aba Gaps XP."
        )
    fora = [
        e for e in op.get("excluded") or []
    ]
    if fora:
        notas.append(
            "Fora: " + "; ".join(f"{e['label']}, {_motivo_curto(e['reason'])}" for e in fora)
            + "."
        )
    # A casa cuja coleta falhou entra com a ultima vitrine valida - e o leitor
    # tem de saber de que dia ela e.
    for snapshot in visao.report.snapshots:
        if snapshot.reserva:
            notas.append(f"{_platform_label(snapshot.platform)}: {snapshot.reserva}.")
    return notas


def _celula_da_semana(fundo: dict) -> str:
    """A captacao da semana com o ritmo dela miudo embaixo."""
    valor = fundo.get("captacao_semana")
    if valor is None:
        return "-"
    ritmo = fundo.get("ritmo_semanal")
    if ritmo and ritmo != "sem_serie":
        return _mm(valor) + _miudo(_esc(nome_da_tendencia(ritmo)))
    return _mm(valor)


def _celula_de_cotistas(fundo: dict) -> str:
    """A variacao de cotistas com o ticket por cotista novo miudo, quando curto.

    Acima de R$ 999 mil o ticket e o sinal de fluxo institucional, e o numero
    cheio fica na planilha.
    """
    variacao = fundo.get("variacao_cotistas")
    if variacao is None:
        return "-"
    texto = _inteiro(variacao)
    ticket = fundo.get("ticket_por_cotista")
    if ticket and len(_mil(ticket)) <= len("R$ 999 mil"):
        texto += _miudo(f"{_mil(ticket)} cada")
    return texto


def _linhas_por_casa(op: dict) -> list[tuple[dict, list[dict]]]:
    """Os primeiros gaps de cada concorrente, sem repetir fundo entre eles.

    Fundo exibido pelas duas casas sai no grupo da primeira, com as duas na
    coluna Casas; o grupo seguinte mostra os proximos dele.
    """
    vistos: set[str] = set()
    grupos = []
    for casa in op.get("por_casa") or []:
        linhas = [f for f in casa["linhas"] if f.get("cnpj") not in vistos][:_GAP_POR_CASA]
        vistos.update(f.get("cnpj") for f in linhas)
        grupos.append((casa, linhas))
    return grupos


def _rotulo_dos_gaps(op: dict) -> str:
    """"Gaps reais" com a carteira da CVM; "Gaps" sem ela (ver `_gaps`)."""
    return "Gaps reais" if (op.get("carteira") or {}).get("available") else "Gaps"


def _md_resumo_por_casa(op: dict) -> list[str]:
    """Concorrente a concorrente: gaps, quantos captam e quanto, na semana e em 3 meses."""
    casas = op.get("por_casa") or []
    if not casas:
        return []
    lines = [
        f"| Concorrente | {_rotulo_dos_gaps(op)} | Captando | Semana | 3 meses |",
        "|---|---:|---:|---:|---:|",
    ]
    for casa in casas:
        captando = milhar(casa["total_transferiveis"]) if op.get("com_fluxo") else "-"
        lines.append(
            f"| {casa['label']} | {milhar(casa['total_reais'])} | {captando} | "
            f"{_mm_ou_traco(casa['captacao_semana'])} | {_mm_ou_traco(casa['captacao_3m'])} |"
        )
    return lines + [""]


def _md_tabela_de_gaps(linhas: list[dict], com_cotistas: bool) -> list[str]:
    cabecalho = ["Fundo", "Categoria", "Casas", "Semana", "3 meses"]
    if com_cotistas:
        cabecalho.append("Cotistas")
    cabecalho += ["Ritmo na semana", "Esforço"]
    lines = [
        "| " + " | ".join(cabecalho) + " |",
        "|---|---|---|---:|---:|" + ("---:|" if com_cotistas else "") + "---|---|",
    ]
    for f in linhas:
        celulas = [
            f"{_nome_do_fundo(f['name'])} ({f['cnpj_display']})", f["category"],
            _casas_curtas(f.get("houses") or []),
            _mm_ou_traco(f.get("captacao_semana")), _mm_ou_traco(f.get("captacao_liquida")),
        ]
        if com_cotistas:
            variacao = f.get("variacao_cotistas")
            celulas.append("-" if variacao is None else _inteiro(variacao))
        celulas += [nome_da_tendencia(f.get("ritmo_semanal")) or "-", f["esforco"]]
        lines.append("| " + " | ".join(celulas) + " |")
    return lines


def _md_oportunidade(visao: VisaoDoDigest) -> list[str]:
    op = visao.oportunidade
    lines = ["## Oportunidade de grade e gaps", ""]
    if op.get("nao_se_aplica"):
        return lines + [f"Não se aplica a este monitor: {op.get('reason')}.", ""]
    if not op.get("available"):
        return lines + [f"Análise suprimida nesta execução: {op.get('reason')}.", ""]
    if op.get("baseline_warning"):
        lines += [f"**Atenção:** {op['baseline_warning']}.", ""]
    if op.get("com_fluxo"):
        # Os numeros em destaque do HTML, um por linha.
        lines += [
            f"- **{numero[0]}** {numero[1]}" for numero in _numeros_da_oportunidade(op)
        ] + [""]
    else:
        lines += [_SEM_CAPTACAO, ""]
    lines += _md_resumo_por_casa(op)

    com_cotistas = op.get("com_cotistas")
    mostrados = 0
    for casa, linhas in _linhas_por_casa(op):
        if not linhas:
            continue
        mostrados += len(linhas)
        primeiros = "o primeiro" if len(linhas) == 1 else f"os {len(linhas)} primeiros"
        lines += [
            f"**{casa['label']}** - {primeiros}, por esforço de trazer e captação em 3 meses",
            "",
            *_md_tabela_de_gaps(linhas, com_cotistas),
            "",
        ]
    resto = len(op["linhas"]) - mostrados
    if resto > 0:
        lines += [f"Mais {milhar(resto)} na planilha.", ""]

    for nota in _notas_da_oportunidade(visao):
        lines.append(f"_{nota}_")
    flow = visao.report.retail_flow
    if flow and flow.get("available"):
        lines.append(f"_Prazos: {'; '.join(_prazos_da_captacao(flow))}._")
    lines.append("")
    return lines


def _html_resumo_por_casa(op: dict) -> str:
    casas = op.get("por_casa") or []
    if not casas:
        return ""
    linhas = [
        [
            _esc(casa["label"]), milhar(casa["total_reais"]),
            milhar(casa["total_transferiveis"]) if op.get("com_fluxo") else "-",
            _mm_ou_traco(casa["captacao_semana"]), _mm_ou_traco(casa["captacao_3m"]),
        ]
        for casa in casas
    ]
    return _tabela(
        ["Concorrente", _rotulo_dos_gaps(op), "Captando", "Semana", "3 meses"], linhas,
        direita={1, 2, 3, 4}, sinal={3, 4},
    )


def _html_oportunidade(visao: VisaoDoDigest, omitir: frozenset = frozenset()) -> str:
    op = visao.oportunidade
    parts = [_secao("Oportunidade de grade e gaps")]
    if op.get("nao_se_aplica"):
        parts.append(
            f'<p style="{_CHAMADA}"><strong>Não se aplica a este monitor:</strong> '
            f"{_esc(op.get('reason'))}.</p>"
        )
        return "".join(parts)
    if not op.get("available"):
        parts.append(
            f'<p style="{_CHAMADA}"><strong>Análise suprimida nesta execução:</strong> '
            f"{_esc(op.get('reason'))}.</p>"
        )
        return "".join(parts)
    if op.get("baseline_warning"):
        parts.append(
            f'<p style="{_CHAMADA}"><strong>Atenção:</strong> '
            f"{_esc(op['baseline_warning'])}.</p>"
        )

    com_cotistas = op.get("com_cotistas")
    if op.get("com_fluxo"):
        parts.append(numeros_em_destaque(_numeros_da_oportunidade(op)))
    else:
        parts.append(f'<p style="{_P}">{_esc(_SEM_CAPTACAO)}</p>')
    parts.append(_html_resumo_por_casa(op))

    grupos =[(casa, linhas) for casa, linhas in _linhas_por_casa(op) if linhas]
    if grupos:
        semana = _semana(visao.report.retail_flow)
        parts.append(_legenda(_linha(
            f"**Os {_GAP_POR_CASA} primeiros de cada concorrente**",
            "por esforço de trazer e captação em 3 meses",
            f"semana {periodo_br(semana['de'], semana['ate'])}" if semana else "",
        )))
        cabecalho = ["Fundo", "Casas", "Semana", "3 meses"]
        if com_cotistas:
            cabecalho.append("Cotistas")
        cabecalho.append("Esforço")
        tabela: list = []
        mostrados = 0
        for casa, linhas in grupos:
            # O concorrente e o cabecalho do grupo, como na tabela de vitrines.
            tabela.append(_esc(casa["label"]))
            mostrados += len(linhas)
            for f in linhas:
                linha = [
                    _fundo_html(
                        _nome_do_fundo(f["name"]),
                        _linha(f.get("cnpj_display") or "", f.get("category") or ""),
                    ),
                    _esc(_casas_curtas(f.get("houses") or [])),
                    _celula_da_semana(f),
                    _mm_ou_traco(f.get("captacao_liquida")),
                ]
                if com_cotistas:
                    linha.append(_celula_de_cotistas(f))
                linha.append(_esc(f.get("esforco") or "-"))
                tabela.append(linha)
        numericas = {2, 3, 4} if com_cotistas else {2, 3}
        parts.append(_tabela(
            cabecalho, tabela, direita=numericas, sinal={2, 3}, compacta=True,
            larguras={0: 32, 1: 8, 2: 17, 3: 14},
        ))
        parts.append(_mais(len(op["linhas"]) - mostrados, "na planilha"))

    notas = " ".join(_esc(n) for n in _notas_da_oportunidade(visao))
    flow = visao.report.retail_flow
    if flow and flow.get("available"):
        notas += " " + _esc(_capitalizar("; ".join(_prazos_da_captacao(flow)))) + "."
    parts.append(f'<p style="{_NOTA}">{notas}</p>')
    return "".join(parts)


# --- ritmo de captacao ---------------------------------------------------------

# Tendencias que somam "captando" na tabela por casa: o que o leitor compara
# entre vitrines e quanto dela ainda capta, quanto acelera e quanto perde.
_CAPTANDO = ("acelerando", "estavel", "desacelerando", "virou_entrada")


def _bloco_do_ritmo(flow: dict) -> dict | None:
    """A tabela de ritmo por casa: % dos fundos captando, acelerando, virando saida."""
    por_casa = flow.get("tendencia_por_casa") or {}
    linhas, parcelas, casas = [], [], []
    for casa in flow.get("casas") or []:
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
        casas.append(casa["platform_id"])
    if not linhas:
        return None
    return {
        "cabecalho": ["Casa", "Fundos", "Captando", "Acelerando", "Virou saída",
                      "Saída persistente"],
        "linhas": linhas,
        "parcelas": parcelas,
        "casas": casas,
    }


def _listas_do_ritmo(flow: dict, com_cotistas: bool) -> list[tuple[str, list[dict]]]:
    criterio = "dinheiro e cotistas" if com_cotistas else "dinheiro"
    return [
        (titulo, (flow.get(chave) or [])[:_FLOW_LISTED])
        for titulo, chave in (
            (f"Maiores captações - fundos ganhando {criterio}", "top_captacao"),
            (f"Maiores resgates - fundos perdendo {criterio}", "top_resgate"),
        )
    ]


def _item_de_fundo(fundo: dict, com_cotistas: bool, visao: VisaoDoDigest) -> str:
    """"Nome - +2.422,0 MM em 3 meses, +31,2 MM na semana (acelerando), +23.918 cotistas
    (R$ 101 mil por cotista novo) - XP · FaaS, BTG".

    O ticket por cotista novo entra porque a CVM publica fluxo do fundo inteiro,
    nao so de varejo: em 24/09 os maiores captadores traziam de R$ 88 mil a R$
    433 mil por cotista novo, e so o numero deixa o leitor ver isso.
    """
    detalhe = f"{_mm(fundo['captacao_liquida'])} em 3 meses"
    if fundo.get("captacao_semana") is not None:
        detalhe += f", {_mm(fundo['captacao_semana'])} na semana"
        ritmo = fundo.get("ritmo_semanal")
        if ritmo and ritmo != "sem_serie":
            detalhe += f" ({nome_da_tendencia(ritmo)})"
    if com_cotistas:
        detalhe += f", {_inteiro(fundo['variacao_cotistas'])} cotistas"
        ticket = fundo.get("ticket_por_cotista")
        if ticket:
            detalhe += f" ({_mil(ticket)} por cotista novo)"
    return f"{_nome_do_fundo(fundo['nome'])} - {detalhe} - {_casas_do_fundo(fundo, visao)}"


def _md_ritmo(visao: VisaoDoDigest) -> list[str]:
    flow = visao.report.retail_flow
    if not (flow and flow.get("available")):
        return []
    com_cotistas = not visao.report.suprime(BLOCO_COTISTAS)
    lines = [
        "## Ritmo de captação",
        "",
        f"[Informe diário da CVM]({CVM_INFORME_DIARIO.url}). Prazos: "
        f"{'; '.join(_prazos_da_captacao(flow))}. A CVM publica fluxo por **fundo**, nunca "
        "por distribuidor: o ritmo de uma casa é o dos fundos que ela exibe, no mercado "
        "inteiro.",
        "",
    ]
    ritmo = _bloco_do_ritmo(flow)
    if ritmo:
        lines += [
            "**Tendência dos fundos, por casa** - parcela dos fundos de cada vitrine "
            "(com PL de ao menos R$ 1 mi no início) em cada ritmo, mês contra os meses "
            "anteriores. Captando: acelerando, estável, desacelerando ou que virou "
            "entrada no último mês.",
            "",
            "| " + " | ".join(ritmo["cabecalho"]) + " |",
            "|---|" + "---:|" * (len(ritmo["cabecalho"]) - 1),
            *("| " + " | ".join(linha) + " |" for linha in ritmo["linhas"]),
            "",
        ]
    for titulo, fundos in _listas_do_ritmo(flow, com_cotistas):
        if fundos:
            lines += [f"**{titulo}**", ""]
            lines += [f"- {_item_de_fundo(f, com_cotistas, visao)}" for f in fundos]
            lines.append("")
    if flow.get("total_estruturais"):
        lines += [
            f"_{contagem(flow['total_estruturais'], 'fundo', 'fundos')} com movimento "
            "estrutural (fluxo maior que o próprio patrimônio, ou fundo esvaziado) ficam "
            "fora das listas: é dinheiro trocando de veículo, não demanda._",
            "",
        ]
    return lines


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


def _tabela_de_fundos_da_captacao(
    fundos: list[dict], com_cotistas: bool, visao: VisaoDoDigest
) -> str:
    """Uma lista de fundos da captacao como tabela: Fundo | Semana | 3 meses |
    Cotistas | Casas. O ritmo da semana vai miudo sob ela, o ticket por cotista
    novo sob os cotistas."""
    cabecalho = ["Fundo", "Semana", "3 meses"]
    if com_cotistas:
        cabecalho.append("Cotistas")
    cabecalho.append("Casas")
    linhas = []
    for fundo in fundos:
        # Sem o CNPJ: nas listas de mercado o nome basta, e o CNPJ esta na
        # planilha - cada byte conta no orcamento do e-mail.
        linha = [
            _esc(_nome_do_fundo(fundo["nome"])), _celula_da_semana(fundo),
            _mm(fundo["captacao_liquida"]),
        ]
        if com_cotistas:
            linha.append(_celula_de_cotistas(fundo))
        linha.append(_esc(_casas_do_fundo(fundo, visao)))
        linhas.append(linha)
    # A semana com folga para o ritmo miudo ("saída persistente") caber numa linha.
    larguras = {0: 36, 1: 18, 2: 15}
    if com_cotistas:
        larguras[3] = 13
    return _tabela(
        cabecalho, linhas, direita={1, 2, 3} if com_cotistas else {1, 2}, sinal={1, 2},
        larguras=larguras,
    )


def _html_ritmo(visao: VisaoDoDigest, omitir: frozenset = frozenset()) -> str:
    flow = visao.report.retail_flow
    if not (flow and flow.get("available")):
        return ""
    com_cotistas = not visao.report.suprime(BLOCO_COTISTAS)
    parts = [_secao("Ritmo de captação")]
    ritmo = _bloco_do_ritmo(flow)
    if ritmo:
        desenho = "" if "ritmo" in omitir else _figura_do_ritmo(ritmo, flow)
        parts.append(desenho or _tabela(
            ritmo["cabecalho"], [[_esc(c) for c in linha] for linha in ritmo["linhas"]],
            direita=range(1, len(ritmo["cabecalho"])),
            base=[_e_base(pid) for pid in ritmo["casas"]],
        ))
    for titulo, fundos in _listas_do_ritmo(flow, com_cotistas):
        if not fundos:
            continue
        destaque, _, resto = titulo.partition(" - ")
        parts.append(
            f'<p style="{_LEGENDA}"><strong style="color:{TINTA};">'
            f"{_esc(destaque)}</strong>{' · ' + _esc(resto) if resto else ''}</p>"
        )
        parts.append(_tabela_de_fundos_da_captacao(fundos, com_cotistas, visao))
    notas = [
        f"Fluxo por fundo, no mercado inteiro, não por distribuidor. "
        f"{_capitalizar('; '.join(_prazos_da_captacao(flow)))}."
    ]
    if flow.get("total_estruturais"):
        notas.append(
            f"{contagem(flow['total_estruturais'], 'fundo', 'fundos')} com movimento "
            "estrutural (fluxo maior que o PL, ou fundo esvaziado) fora das listas: troca "
            "de veículo, não demanda."
        )
    parts.append(
        f'<p style="{_NOTA}">{_esc(" ".join(notas))} {_fonte_html(CVM_INFORME_DIARIO)}</p>'
    )
    return "".join(parts)


# --- radar CVM -----------------------------------------------------------------

_SUFIXOS_DE_EMPRESA = re.compile(r"\s+(LTDA|S/?A|S\.A|DTVM|EIRELI)\b\.?", re.I)
_SIGLAS = {"BTG", "WM", "XP", "DTVM", "CCTVM"}
_MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "em"}


def _nome_da_gestora(nome: str) -> str:
    """"BTG PACTUAL WM GESTÃO DE RECURSOS LTDA" -> "BTG Pactual WM Gestão de Recursos"."""
    texto = _SUFIXOS_DE_EMPRESA.sub("", nome or "").strip(" .-")
    palavras = []
    for palavra in texto.split():
        if palavra.upper() in _SIGLAS:
            palavras.append(palavra.upper())
        elif palavra.lower() in _MINUSCULAS and palavras:
            palavras.append(palavra.lower())
        else:
            palavras.append(palavra.capitalize())
    return " ".join(palavras)


def _gestoras_do_radar(report: RunReport) -> list[str]:
    """"BTG: BTG Pactual Gestão..., Empiricus Asset Management e mais 5" por grupo."""
    snapshot = report.cvm_snapshot
    if not snapshot:
        return []
    saida = []
    for casa, fundos in sorted(snapshot.by_house().items()):
        contagem_por_gestora: dict[str, int] = {}
        for fundo in fundos:
            if fundo.manager_name:
                contagem_por_gestora[fundo.manager_name] = (
                    contagem_por_gestora.get(fundo.manager_name, 0) + 1
                )
        nomes = [
            _nome_da_gestora(n)
            for n, _ in sorted(contagem_por_gestora.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        if not nomes:
            continue
        mostrados = ", ".join(nomes[:2])
        resto = f" e mais {len(nomes) - 2}" if len(nomes) > 2 else ""
        saida.append(f"{_casas_curtas([casa])}: {mostrados}{resto}")
    return saida


def _titulo_do_radar(report: RunReport) -> str:
    casas = sorted({f.house for f in report.cvm_snapshot.funds}) if report.cvm_snapshot else []
    grupos = _juntar([_casas_curtas([c]) for c in casas])
    return f"Radar CVM — gestoras de {grupos}" if grupos else "Radar CVM — gestoras dos concorrentes"


def _nota_do_radar(report: RunReport) -> str:
    gestoras = _gestoras_do_radar(report)
    return (
        "Fundos registrados na CVM pelas gestoras dos grupos concorrentes, pelo gestor do "
        "cadastro: é o produto que a gestora prepara, não a vitrine da casa. A XP e os "
        "fundos cancelados ficam de fora."
        + (f" Gestoras: {'; '.join(gestoras)}." if gestoras else "")
    )


def _md_cvm_section(diff: CvmDiff | None, report: RunReport) -> list[str]:
    lines = [f"## {_titulo_do_radar(report)}", ""]

    snapshot = report.cvm_snapshot
    if not snapshot or snapshot.status == SnapshotStatus.UNAVAILABLE:
        error = snapshot.error if snapshot else "extrato não coletado"
        lines += [f"Extrato da CVM indisponível: {error}.", ""]
        return lines

    by_house = snapshot.by_house()
    lines.append(_nota_do_radar(report))
    lines.append("")
    lines.append(
        f"{contagem(len(snapshot.funds), 'fundo ativo', 'fundos ativos')} no cadastro, "
        f"{_contagem(sum(1 for f in snapshot.funds if f.is_pre_operational))} em fase "
        f"pré-operacional. Fonte: {_link(CVM_CADASTRO)}."
    )
    lines.append("")
    lines.append("| Grupo | Ativos | Pré-operacionais |")
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
                    f"- [{_platform_label(fund.house)}] {_nome_do_fundo(fund.name)} "
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
                f"- [{_platform_label(fund.house)}] {_nome_do_fundo(fund.name)} "
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
                f"- [{_platform_label(change.fund.house)}] {_nome_do_fundo(change.fund.name)} "
                f"({format_cnpj(change.fund.cnpj)})"
            )
        lines.append("")

    others = [c for c in diff.situation_changes if not c.is_launch]
    if others:
        lines += ["**Outras mudanças de situação**", ""]
        for change in others[:_MAX_LISTED]:
            lines.append(
                f"- [{_platform_label(change.fund.house)}] {_nome_do_fundo(change.fund.name)}: "
                f"{change.old_situation or '-'} -> {change.new_situation or '-'}"
            )
        lines.append("")

    if diff.cancelled:
        lines += ["**Saíram do cadastro**", ""]
        for fund in diff.cancelled[:_MAX_LISTED]:
            lines.append(f"- [{_platform_label(fund.house)}] {_nome_do_fundo(fund.name)}")
        lines.append("")

    return lines


def _figura_do_radar(report: RunReport) -> str:
    """Fundos registrados e ainda nao lancados, por grupo gestor."""
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
    if len(linhas) > 1 and base is None and len(empate) == 1:
        titulo += f"; {linhas[1].rotulo}, {linhas[1].texto}"
    return figura(
        titulo,
        _linha("Registrados na CVM pelas gestoras do grupo e ainda não lançados",
               f"cadastro de {data_br(snapshot.collected_at.date())}"),
        barras(linhas),
        _fonte_html(CVM_CADASTRO),
    )


def _html_cvm_section(report: RunReport, omitir: frozenset = frozenset()) -> str:
    snapshot = report.cvm_snapshot
    parts = [_secao(_esc(_titulo_do_radar(report)))]

    if not snapshot or snapshot.status == SnapshotStatus.UNAVAILABLE:
        error = snapshot.error if snapshot else "extrato não coletado"
        parts.append(f'<p style="{_P}">Extrato da CVM indisponível: {_esc(error)}.</p>')
        return "".join(parts)

    figura_do_radar = "" if "radar" in omitir else _figura_do_radar(report)
    parts.append(figura_do_radar or (
        f'<p style="{_LEGENDA}">Fundos das gestoras dos grupos · '
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
    if len(por_casa) > 1:
        linhas.append([
            "Total", _contagem(len(snapshot.funds)),
            _contagem(sum(1 for f in snapshot.funds if f.is_pre_operational)),
        ])
    parts.append(_tabela(
        ["Grupo", "Ativos", "Pré-operacionais"], linhas, direita={1, 2},
        total=len(por_casa) > 1,
    ))
    parts.append(f'<p style="{_NOTA}">{_esc(_nota_do_radar(report))}</p>')

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
            ["Grupo", "Fundo", "Movimento"],
            [
                [
                    _esc(_casas_curtas([casa])),
                    _fundo_html(_nome_do_fundo(fundo.name), format_cnpj(fundo.cnpj)),
                    _esc(movimento)
                    + (f'<br><small style="{_MIUDO_CINZA}">{data_br(data)}</small>'
                       if data else ""),
                ]
                for casa, fundo, movimento, data in movimentos
            ],
            larguras={0: 10, 2: 30},
        ))
        parts.append(_mais(sobra, "no dashboard e na planilha"))
    return "".join(parts)


# --- vitrines --------------------------------------------------------------------


def _tamanhos(snapshot: ShelfSnapshot | None) -> tuple[int, int]:
    """(vitrine, catalogo de varejo) da casa nesta coleta."""
    if snapshot is None:
        return 0, 0
    return len(listing_funds(snapshot)), len(retail_funds(snapshot))


def _frase_de_tamanho(snapshot: ShelfSnapshot | None) -> str:
    """"634 fundos na vitrine" - ou o motivo, quando a vitrine nao foi alcancada.

    Casa sem vitrine alcancavel nao vira "0 fundos na vitrine": zero e um numero
    e diz que a prateleira esta vazia, quando o que houve foi o monitor nao
    alcancar a listagem.
    """
    vitrine, _ = _tamanhos(snapshot)
    if not vitrine:
        return "sem vitrine pública coletada"
    return f"{contagem(vitrine, 'fundo', 'fundos')} na vitrine"


def _entradas_e_saidas(entradas: list, saidas: list) -> str:
    """"1 entrada, 0 saídas" - cada contagem concorda com o numero."""
    return (
        f"{contagem(len(entradas), 'entrada', 'entradas')}, "
        f"{contagem(len(saidas), 'saída', 'saídas')}"
    )


def _mudancas_de_vitrine(
    diff: ShelfDiff, snapshot: ShelfSnapshot | None
) -> tuple[list[FundOffer], list[FundOffer]]:
    """(entradas, saidas) na vitrine - a regra de `movimentos_de_vitrine`.

    Alteracao de condicao (taxa, aplicacao minima, fechado para captacao) saiu
    do digest em 14/10/2026: so o que entrou e o que saiu. Continua no
    dashboard e na planilha, como o que mudou so no catalogo Open Finance.
    """
    casa = movimentos_de_vitrine(diff, snapshot)
    return casa.entradas, casa.saidas


def _servico_do_fundo(fund: FundOffer, diff: ShelfDiff, visao: VisaoDoDigest | None) -> str:
    """" · FaaS" para fundo da casa com servico na lista; "" nos outros."""
    if visao is None or diff.platform != visao.base_id:
        return ""
    servico = visao.servico(fund.cnpj)
    return f" · {servico}" if servico else ""


def _md_shelf_section(
    diff: ShelfDiff,
    snapshot: ShelfSnapshot | None,
    url: str | None = None,
    visao: VisaoDoDigest | None = None,
) -> list[str]:
    rotulo = _platform_label(diff.platform)
    lines = [f"### [{rotulo}]({url})" if url else f"### {rotulo}", ""]

    if snapshot and snapshot.reserva:
        # A coleta do dia falhou: as analises usam a vitrine de reserva, e o
        # movimento nao foi medido - comparar a reserva com ela mesma daria zero.
        lines += [
            f"{_capitalizar(snapshot.reserva)}. Entradas e saídas não medidas nesta execução.",
            "",
        ]
        return lines
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

    entradas, saidas = _mudancas_de_vitrine(diff, snapshot)
    if not (entradas or saidas):
        lines += [f"Nenhum fundo entrou nem saiu: {tamanho}.", ""]
        return lines

    lines += [f"{_entradas_e_saidas(entradas, saidas)} na vitrine - {tamanho}.", ""]

    if entradas:
        lines.append("**Entraram na vitrine**")
        lines.append("")
        for fund in entradas[:_MAX_LISTED]:
            cnpj = f" ({format_cnpj(fund.cnpj)})" if fund.cnpj else ""
            manager = f" - {fund.manager_name}" if fund.manager_name else ""
            lines.append(
                f"- {_nome_do_fundo(fund.name)}{cnpj}{_servico_do_fundo(fund, diff, visao)}{manager}"
            )
        if len(entradas) > _MAX_LISTED:
            lines.append(f"- ... e mais {contagem(len(entradas) - _MAX_LISTED, 'fundo', 'fundos')}")
        lines.append("")

    if saidas:
        lines.append("**Saíram da vitrine**")
        lines.append("")
        for fund in saidas[:_MAX_LISTED]:
            cnpj = f" ({format_cnpj(fund.cnpj)})" if fund.cnpj else ""
            manager = f" - {fund.manager_name}" if fund.manager_name else ""
            lines.append(
                f"- {_nome_do_fundo(fund.name)}{cnpj}{_servico_do_fundo(fund, diff, visao)}{manager}"
            )
        if len(saidas) > _MAX_LISTED:
            lines.append(f"- ... e mais {contagem(len(saidas) - _MAX_LISTED, 'fundo', 'fundos')}")
        lines.append("")

    return lines


def _html_vitrines(
    report: RunReport, urls: dict[str, str], visao: VisaoDoDigest | None = None
) -> str:
    """Entradas e saidas de cada vitrine numa tabela: Movimento | Fundo | Gestora.

    So o que entrou e o que saiu (pedido de 14/10/2026), sem taxa, aplicacao
    nem outro dado comparado. O estado de cada casa (primeira coleta, nova
    linha de base, coleta suspeita, sem mudanca) vira uma nota curta embaixo.
    """
    linhas: list = []
    notas: list[str] = []
    alertas: list[str] = []
    primeira, sem_mudanca, sobra = [], [], 0
    for diff in report.shelf_diffs:
        snapshot = report.snapshot_for(diff.platform)
        rotulo = _platform_label(diff.platform)
        if snapshot and snapshot.reserva:
            alertas.append(
                f"{_esc(rotulo)}: {_esc(snapshot.reserva)}; entradas e saídas não medidas."
            )
            continue
        if snapshot and snapshot.status == SnapshotStatus.UNAVAILABLE:
            alertas.append(
                f"{_esc(rotulo)}: coleta indisponível "
                f"({_esc(_motivo_curto(snapshot.error))})."
            )
            continue
        if diff.nova_linha_de_base:
            alertas.append(
                f"Nova linha de base em {_esc(rotulo)}: "
                f"{_esc(_motivo_curto(diff.suspect_reason) or 'a coleta passou a ver a vitrine')}."
            )
            continue
        if diff.is_first_run:
            primeira.append(rotulo)
            continue
        if diff.suspect:
            alertas.append(
                f"Coleta suspeita em {_esc(rotulo)} ({_esc(_motivo_curto(diff.suspect_reason))}): "
                "saídas omitidas para evitar falso churn."
            )
        entradas, saidas = _mudancas_de_vitrine(diff, snapshot)
        if not (entradas or saidas):
            sem_mudanca.append(rotulo)
            continue
        casa = (
            f'<a href="{_esc(urls[diff.platform])}" style="{_LINK}">{_esc(rotulo)}</a>'
            if urls.get(diff.platform) else _esc(rotulo)
        )

        def fundo(f: FundOffer) -> str:
            cnpj = format_cnpj(f.cnpj) if f.cnpj else ""
            return _fundo_html(
                _nome_do_fundo(f.name), (cnpj + _servico_do_fundo(f, diff, visao)).strip(" ·")
            )

        movimentos = [
            ["Entrou", fundo(f), _esc(f.manager_name or "-")] for f in entradas[:_MAX_LISTED]
        ] + [
            ["Saiu", fundo(f), _esc(f.manager_name or "-")] for f in saidas[:_MAX_LISTED]
        ]
        sobra += sum(max(0, len(lista) - _MAX_LISTED) for lista in (entradas, saidas))
        # A casa e o cabecalho do grupo, e nao uma coluna: no telefone a coluna
        # a mais espremia o fundo e a gestora.
        linhas.append(casa)
        linhas += movimentos

    if primeira:
        notas.insert(0, f"Primeira coleta, linha de base: {_esc(_juntar(primeira))}.")
    if sem_mudanca:
        notas.append(f"Nenhum fundo entrou nem saiu: {_esc(_juntar(sem_mudanca))}.")
    partes = [f'<p style="{_CHAMADA}">{" ".join(alertas)}</p>'] if alertas else []
    if linhas:
        partes.append(_tabela(["Movimento", "Fundo", "Gestora"], linhas, larguras={0: 14, 1: 52}))
        partes.append(_mais(sobra, "movimentos no dashboard e na planilha"))
    if notas:
        partes.append(f'<p style="{_NOTA}">{" ".join(notas)}</p>')
    return "".join(partes)


def _urls_das_vitrines(config: Config, report: RunReport) -> dict[str, str]:
    return {
        s.platform: url
        for s in report.snapshots
        if (url := config.platform(s.platform).shelf_url)
    }


# --- rentabilidade e custo (edicao mensal) ----------------------------------------


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


_TITULO_MENSAL = "Rentabilidade e custo · edição mensal"


def _md_performance_section(report: RunReport) -> list[str]:
    """Rentabilidade ponderada e custo relativo - so na edicao mensal.

    A manchete nao e o retorno medio: e o gap entre a ponderada por PL e a
    ponderada por cotistas, mais quanto do dinheiro e da gente esta em fundo
    que rende abaixo da propria categoria.

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
        f"## {_TITULO_MENSAL}",
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
                f"- {_contagem(fundo['cotistas'])} cotistas - {_nome_do_fundo(fundo['nome'])} "
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
    partes = [_secao(_esc(_TITULO_MENSAL))]

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
                    _fundo_html(
                        _nome_do_fundo(f["nome"]),
                        format_cnpj(f["cnpj"]) if f.get("cnpj") else None,
                    ),
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


# --- figuras do e-mail -------------------------------------------------------
#
# Uma funcao por figura: monta as linhas a partir do bloco, escreve o titulo que
# afirma a leitura (e nao o nome do grafico) e o subtitulo com unidade e prazo.
# Sem dado, devolvem "" - e a secao segue so com a tabela, que e a versao em
# tabela de cada figura e continua sempre logo abaixo dela.


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


# --- fontes --------------------------------------------------------------------


def _linhas_de_fontes(report: RunReport, config: Config) -> list[str]:
    """Uma linha por fonte usada, em markdown: nome com link, e o que ela fornece."""
    linhas = []
    for fonte in fontes_do_relatorio(report, config):
        linha = f"{_link(fonte)}: {fonte.uso}."
        if fonte.url_dados:
            linha += f" Arquivos que o monitor lê: [{fonte.url_dados}]({fonte.url_dados})."
        linhas.append(linha)
    return linhas


def _html_fontes(report: RunReport, config: Config) -> str:
    """As fontes como tabela: nome com link | o que fornece (e os arquivos lidos)."""
    return _fontes_html(fontes_do_relatorio(report, config))


# --- markdown --------------------------------------------------------------------


def render_markdown(
    report: RunReport, config: Config | None = None, edicao: str | None = None
) -> str:
    """O digest em markdown - a parte em texto do e-mail e o arquivo do repositorio."""
    visao = montar_visao(report, config, edicao)
    recortado = visao.report
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    # Sem config, a identidade padrao do Config - a do monitor de fundos.
    identidade = config if config is not None else Config
    fontes_config = config if config is not None else Config()

    lines = [
        f"# {identidade.titulo_relatorio}",
        "",
        f"{identidade.descricao_relatorio}: {_linha_do_cabecalho(visao, generated).replace(' · ', ' - ')}",
        "",
    ]
    alerta = _alerta(visao)
    if alerta:
        lines += [f"**{alerta}**", ""]

    lines += _md_oportunidade(visao)
    lines += _md_ritmo(visao)
    lines += _md_cvm_section(recortado.cvm_diff, recortado)

    lines += ["## Vitrines: entradas e saídas", ""]
    urls = _urls_das_vitrines(fontes_config, recortado)
    for diff in recortado.shelf_diffs:
        lines += _md_shelf_section(
            diff, recortado.snapshot_for(diff.platform), urls.get(diff.platform), visao
        )

    if visao.mensal:
        lines += _md_performance_section(recortado)

    unavailable = [s for s in recortado.snapshots if s.status == SnapshotStatus.UNAVAILABLE]
    if unavailable:
        lines += ["## Fontes indisponíveis", ""]
        for snapshot in unavailable:
            lines.append(
                f"- {_platform_label(snapshot.platform)}: "
                f"{snapshot.error or 'motivo não informado'}"
            )
        lines.append("")

    fontes = _linhas_de_fontes(recortado, fontes_config)
    if fontes:
        lines += ["## Fontes", "", "Onde conferir cada número deste relatório:", ""]
        lines += [f"- {linha}" for linha in fontes]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --- HTML para e-mail --------------------------------------------------------

# Orcamento do e-mail. O Gmail corta a mensagem com mais de ~102 KB de HTML: o
# leitor ve o comeco e um link "Exibir mensagem inteira", e o resto some. As
# figuras sao a parte que cresce (tabela de celulas coloridas), e sao todas a
# versao grafica de uma tabela que fica no e-mail - entao, passado o orcamento,
# elas saem uma a uma, da que menos acrescenta a que mais, e o rodape avisa.
# O digest v2 (14/10/2026) e bem menor que o antigo (~98 KB com todas as
# figuras); o corte fica para a edicao mensal com tudo.
_ORCAMENTO_DO_EMAIL = 100_000
_FIGURAS_POR_CORTE = (
    ("pl_abaixo", "PL abaixo da mediana"),
    ("radar", "radar CVM"),
    ("custo", "custo"),
    ("rentabilidade", "rentabilidade"),
    ("ritmo", "ritmo dos fundos"),
)


def render_html(
    report: RunReport, config: Config | None = None, edicao: str | None = None
) -> str:
    """O digest em HTML de e-mail, dentro do orcamento de tamanho do Gmail."""
    montar = _montador_do_html(report, config, edicao)
    html = montar(())
    cortes = [chave for chave, _ in _FIGURAS_POR_CORTE]
    for n in range(1, len(cortes) + 1):
        if len(html.encode("utf-8")) <= _ORCAMENTO_DO_EMAIL:
            break
        html = montar(tuple(cortes[:n]))
    return html


def _montador_do_html(report: RunReport, config: Config | None, edicao: str | None = None):
    """Monta as partes fixas uma vez; devolve `montar(omitidas) -> html`."""
    visao = montar_visao(report, config, edicao)
    recortado = visao.report
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")
    identidade = config if config is not None else Config
    fontes_config = config if config is not None else Config()
    alerta = _alerta(visao)
    # A previa do e-mail e texto puro: o alerta, ou as casas e a semana do
    # cabecalho - o assunto ja traz a oportunidade.
    previa = alerta or _linha_do_cabecalho(visao, generated)

    urls = _urls_das_vitrines(fontes_config, recortado)
    vitrines = _html_vitrines(recortado, urls, visao)
    bloco_fontes = _html_fontes(recortado, fontes_config)

    # A faixa preta do topo, como a barra de navegacao do deck: o rotulo do
    # monitor em caixa alta a esquerda, a data da execucao a direita.
    faixa = _faixa(identidade.rotulo_assunto, report.generated_at.strftime("%d/%m/%Y"))
    lede = (
        f'<p style="{_CHAMADA}font-size:15px;"><strong>{_md_para_html(alerta)}</strong></p>'
        if alerta else ""
    )
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
        mensal = _html_performance_section(recortado, omitir) if visao.mensal else ""
        corpo = f"""
  <p style="{_RETRANCA}font-weight:700;margin:0 0 14px;padding:0;border:0;">{_TRACO}{_esc(identidade.descricao_relatorio)}</p>
  <h1 style="font-size:28px;line-height:32px;font-weight:800;letter-spacing:-0.02em;color:{TINTA};margin:0 0 8px;">{_esc(identidade.titulo_relatorio)}</h1>
  <p style="font-size:14px;line-height:20px;color:{TINTA_2};margin:0 0 18px;">{_esc(_linha_do_cabecalho(visao, generated))}</p>
  {lede}

  {_html_oportunidade(visao, omitir)}

  {_html_ritmo(visao, omitir)}

  {_html_cvm_section(recortado, omitir)}

  {_secao("Vitrines: entradas e saídas")}
  {vitrines}

  {mensal}

  {bloco_fontes}
  {nota_de_corte}
"""
        return _casca_do_email(
            _esc(identidade.titulo_relatorio), _esc(previa), corpo, faixa
        )

    return montar


def render_digest(config: Config, report: RunReport) -> tuple[Path, Path]:
    """Grava digest markdown e HTML. Devolve (path_md, path_html).

    A edicao (semanal ou mensal) e decidida uma vez, antes de gravar: o arquivo
    de hoje nao pode virar o "digest anterior do mes" do HTML gravado depois.
    """
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    today: date = report.generated_at.date()
    edicao = edicao_do_digest(config, today)

    md_path = config.reports_dir / f"digest-{today.isoformat()}.md"
    md_path.write_text(render_markdown(report, config, edicao), encoding="utf-8")

    html_path = config.reports_dir / f"digest-{today.isoformat()}.html"
    html_path.write_text(render_html(report, config, edicao), encoding="utf-8")

    return md_path, html_path
