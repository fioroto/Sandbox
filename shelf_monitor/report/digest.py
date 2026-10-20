"""Digest semanal: a oportunidade de grade, o ritmo da captacao e a vitrine.

Gera markdown (leitura no repo, diff de git legivel) e HTML com CSS inline,
sem JS e com largura contida - pronto para ir no corpo do e-mail.

**Versao 2 (14/10/2026), curta e direta**, pelos comentarios da area
(alinhados com o Davi): o digest e semanal e deve trazer pouquissima
informacao. So XP, BTG e Itau (`Config.casas_do_digest`, recorte em
`digest_visao`); a mensagem principal, com mais destaque, e a oportunidade de
grade e os gaps reais. Na ordem:

1. **Oportunidade de grade e gaps** (`report.oportunidade`): os numeros em
   destaque, a legenda do ritmo num bloco que abre no clique (17/10/2026), o
   resumo por concorrente e os gaps reais que captam e aceleram na semana, pela
   captacao em 3 meses, com o produto proprio do concorrente marcado
   (16/10/2026), a captacao da semana, o ritmo e o esforco de trazer;
2. **Ritmo de captacao**: o mapa de ritmo por casa, as maiores captacoes e
   resgates, com a semana e, ao lado, a captacao do fundo na XP pelas bases
   internas (20/10/2026, `report.captacao_na_xp`), e abaixo deles a captacao
   das vitrines por categoria ANBIMA, a visao de mercado (19/10/2026);
3. **Vitrines**: so o que entrou e o que saiu;
4. **Rentabilidade e custo**, so na edicao mensal (o primeiro digest do mes).

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
frase antes delas e o alerta de coleta que falhou inteira. Em 17/10/2026
saiu o radar CVM (o que as gestoras do BTG e do Itau registraram), que segue
no dashboard, na planilha e no deck.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from shelf_monitor.caixa import nota_caixa
from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.models import (
    BLOCO_COTISTAS,
    FundOffer,
    RunReport,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import format_cnpj
from shelf_monitor.previdencia_fora import nota_previdencia
from shelf_monitor.report.shared import (
    movimentos_de_vitrine,
    movimentos_do_run,
    rotulo_da_casa,
)
from shelf_monitor.report.retail_flow import _TREND_BAND, nome_da_tendencia
from shelf_monitor.report.shared.nomes import nome_de_fundo
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
    como_ler as _como_ler,
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
    prazo_da_rentabilidade,
)
from shelf_monitor.report.aggregate import _offer_manager, listing_funds, retail_funds
from shelf_monitor.report import captacao_na_xp as na_xp
from shelf_monitor.report.custo import classes_com_indice, nota_das_classes
from shelf_monitor.report.digest_visao import (
    VisaoDoDigest,
    edicao_do_digest,
    montar_visao,
)
from shelf_monitor.report.oportunidade import CAPTACAO_MINIMA_NA_SEMANA, da_para_trazer

# O corte de captacao da semana da lista de gaps, por extenso ("R$ 10 MM").
_MINIMO_NA_SEMANA = f"R$ {CAPTACAO_MINIMA_NA_SEMANA / 1e6:.0f} MM"

_MAX_LISTED = 25

# A oportunidade listada no e-mail, por concorrente: os 10 primeiros de cada um
# (o Itau sempre ao lado do BTG; eram 5 ate 16/10/2026). O resto mora na planilha.
_GAP_POR_CASA = 10
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


def _nome_do_fundo(nome: str | None) -> str:
    """O nome para ler, no padrao das vitrines (`shared.nomes`): a forma
    juridica em sigla e, em caixa alta, caixa de titulo (19/10/2026)."""
    return nome_de_fundo(nome)


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
    correlato nao foram verificados. Contam o que da para trazer; o produto
    proprio do concorrente, que a lista mostra marcado, sai contado a parte.
    """
    n = op["total_transferiveis"]
    real = (op.get("carteira") or {}).get("available")
    gaps = ("gap real" if n == 1 else "gaps reais") if real else ("gap" if n == 1 else "gaps")
    deles = "dele" if n == 1 else "deles"
    ritmo = (
        f"acelerando, com mais de {_MINIMO_NA_SEMANA} na semana"
        if op.get("com_ritmo") else "captando"
    )
    numeros: list[tuple] = [
        (milhar(n), f"{'fundo' if n == 1 else 'fundos'} para a grade: {gaps} {ritmo}", True)
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
    proprios = op.get("total_produto_do_concorrente") or 0
    if proprios:
        numeros.append((
            milhar(proprios),
            "produto próprio do concorrente, marcado na lista" if proprios == 1
            else "produtos próprios do concorrente, marcados na lista",
        ))
    return numeros


# Sem o informe diario nao ha numero em destaque, e o leitor tem de saber por que.
_SEM_CAPTACAO = (
    "Sem o informe diário da CVM nesta execução, a captação dos gaps não foi medida; "
    "a lista segue o esforço de trazer, sem o produto próprio do concorrente."
)


def _notas_da_oportunidade(visao: VisaoDoDigest) -> list[str]:
    """O que e gap real e de onde vem cada numero - em frases curtas.

    No HTML ficam no bloco que abre no clique (17/10/2026); o que muda a leitura
    dos numeros - casa fora, vitrine de reserva - e aviso, e fica a mostra
    (`_avisos_da_oportunidade`).
    """
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
    notas = [
        criterio,
        # A taxa global (20/10/2026): a do site, e nao a do Open Finance.
        "Taxa global: a que o site da casa mostra para o fundo, em % ao ano - "
        "administração, gestão e distribuição -; com o fundo em duas casas, a menor. "
        "\"-\": o site não a publica.",
        # A regra da gestora (19/10/2026): a lista da casa conta, nao so a vitrine.
        "Gestora que a XP já distribui: a da estratégia tem algum fundo na vitrine da "
        "XP ou na lista da casa; sem nenhum, o gap exige abrir gestora.",
    ]
    if op.get("com_fluxo"):
        notas.append(
            ("Na lista: os que captaram em 3 meses, estão acelerando na semana - captação "
             "por dia mais de 25% acima da média das 12 semanas anteriores, que já era "
             f"positiva - e captaram mais de {_MINIMO_NA_SEMANA} na semana, pela captação "
             "em 3 meses."
             if op.get("com_ritmo") else
             "Na lista: os que captaram em 3 meses, pela captação - sem semana completa no "
             "informe, o ritmo da semana não filtra.")
            + " O produto próprio do concorrente entra marcado e fica fora dos números de "
            "cima."
        )
        # A captacao da tabela por concorrente e de todos os gaps reais, e nao da
        # lista (20/10/2026): sem a nota, o leitor a le como a do "Para trazer".
        gaps = _rotulo_dos_gaps(op).lower()
        notas.append(
            f"Por concorrente: Semana e 3 meses somam todos os {gaps} da vitrine - na "
            "lista ou não, para trazer ou produto próprio, captando ou resgatando -, com "
            "informe, PL de ao menos R$ 1 mi no início e sem movimento estrutural. A "
            "captação é a do fundo no mercado inteiro (informe da CVM), não a que cada "
            "casa fez."
        )
    if op.get("total_reais"):
        notas.append(
            "Na planilha: os gaps reais na aba Gaps XP e os cobertos - por fundo da XP, "
            "pela lista da casa ou em outra faixa de liquidez -, com o motivo e a prova, "
            "na aba Gaps cobertos."
            if carteira.get("available") else
            "A lista completa dos gaps está na planilha, aba Gaps XP."
        )
    notas += [f"Gaps: {nota}." for nota in _notas_de_fora(visao.gaps)]
    prazo = _prazo_da_rentabilidade(visao.report)
    if prazo and op.get("linhas"):
        notas.append(prazo)
    return notas


def _avisos_da_oportunidade(visao: VisaoDoDigest) -> list[str]:
    """A casa que ficou fora e a vitrine de reserva: mudam a leitura dos numeros."""
    avisos = []
    fora = visao.oportunidade.get("excluded") or []
    if fora:
        avisos.append(
            "Fora: " + "; ".join(f"{e['label']}, {_motivo_curto(e['reason'])}" for e in fora)
            + "."
        )
    # A casa cuja coleta falhou entra com a ultima vitrine valida - e o leitor
    # tem de saber de que dia ela e.
    for snapshot in visao.report.snapshots:
        if snapshot.reserva:
            avisos.append(f"{_platform_label(snapshot.platform)}: {snapshot.reserva}.")
    return avisos


# --- rentabilidade de referencia -------------------------------------------------
#
# Pedido de 16/10/2026: a rentabilidade de cada fundo ao lado dele nas tabelas
# de gaps e de captacoes, como referencia. 12 meses primeiro (a convencao da
# lamina); o fundo sem 12 meses de cota mostra 3 meses, com o prazo escrito.
# O "pp" compara com a mediana da propria categoria no universo da CVM - o
# mesmo criterio da secao mensal de rentabilidade.


def _rentabilidade_do_fundo(fundo: dict) -> tuple[float, str, float | None] | None:
    """(retorno, prazo, excesso sobre a mediana da categoria), ou None sem serie."""
    for prazo, rotulo in (("12m", "12 meses"), ("3m", "3 meses")):
        retorno = fundo.get(f"retorno_{prazo}")
        if retorno is None:
            continue
        mediana = fundo.get(f"mediana_{prazo}")
        return retorno, rotulo, None if mediana is None else retorno - mediana
    return None


def _celula_de_rentabilidade(fundo: dict) -> str:
    """HTML: o retorno, com a comparacao (e o prazo, quando nao e 12 meses) miuda."""
    dados = _rentabilidade_do_fundo(fundo)
    if dados is None:
        return "-"
    retorno, prazo, excesso = dados
    miudo = _linha(
        "" if prazo == "12 meses" else f"em {prazo}",
        "" if excesso is None else f"{_pp(excesso)} vs categoria",
    )
    return _pct(retorno) + (_miudo(_esc(miudo)) if miudo else "")


def _texto_de_rentabilidade(fundo: dict) -> str:
    """Markdown, celula de tabela: "13,45% (+0,31pp)" ou "4,12% em 3 meses (+0,10pp)"."""
    dados = _rentabilidade_do_fundo(fundo)
    if dados is None:
        return "-"
    retorno, prazo, excesso = dados
    texto = _pct(retorno) + ("" if prazo == "12 meses" else f" em {prazo}")
    return texto + ("" if excesso is None else f" ({_pp(excesso)})")


def _frase_de_rentabilidade(fundo: dict) -> str:
    """Markdown, item de lista: "rentabilidade de 13,45% em 12 meses (+0,31pp vs categoria)"."""
    dados = _rentabilidade_do_fundo(fundo)
    if dados is None:
        return ""
    retorno, prazo, excesso = dados
    comparacao = "" if excesso is None else f" ({_pp(excesso)} vs categoria)"
    return f"rentabilidade de {_pct(retorno)} em {prazo}{comparacao}"


def _prazo_da_rentabilidade(report: RunReport) -> str:
    """A janela de cada prazo de rentabilidade, para a nota de cada secao."""
    return prazo_da_rentabilidade(report.performance_12m, report.performance)


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


def _coluna_da_lista(op: dict) -> str:
    """Quantos gaps entram na lista. Com a semana, "Na lista" - acelerar e
    captar mais de R$ 10 MM nela (19/10/2026), como diz a nota; sem semana
    completa, so captar."""
    return "Na lista" if op.get("com_ritmo") else "Captando"


def _ordem_da_lista(op: dict) -> str:
    """Com o informe a lista vai pela captacao; sem ele, pelo esforco de trazer."""
    return "por captação em 3 meses" if op.get("com_fluxo") else "por esforço de trazer"


def _esforco_na_tabela(fundo: dict) -> str:
    """O esforco na tabela, com "produto próprio" curto: e a etiqueta dele na lista."""
    if not da_para_trazer(fundo):
        return "produto próprio"
    return fundo.get("esforco") or "-"


def _resumo_da_casa(op: dict, casa: dict) -> list[str]:
    """Gaps, quantos entram na lista, quantos dá para trazer e a captação de todos os gaps."""
    com_fluxo = op.get("com_fluxo")
    return [
        milhar(casa["total_reais"]),
        milhar(casa["total_na_lista"]) if com_fluxo else "-",
        milhar(casa["total_transferiveis"]),
        _mm_ou_traco(casa["captacao_semana"]), _mm_ou_traco(casa["captacao_3m"]),
    ]


def _md_resumo_por_casa(op: dict) -> list[str]:
    """Concorrente a concorrente: gaps, quantos entram na lista e a captacao de todos os gaps."""
    casas = op.get("por_casa") or []
    if not casas:
        return []
    lines = [
        (f"| Concorrente | {_rotulo_dos_gaps(op)} | {_coluna_da_lista(op)} | Para trazer | "
         "Semana | 3 meses |"),
        "|---|---:|---:|---:|---:|---:|",
    ]
    for casa in casas:
        lines.append(f"| {casa['label']} | " + " | ".join(_resumo_da_casa(op, casa)) + " |")
    return lines + [""]


# A tabela por categoria (pedidos de 17 e 19/10/2026), no Ritmo de captacao,
# abaixo dos maiores resgates: a visao de mercado das vitrines em foco - todos os
# fundos, cada um uma vez, com a captacao no mercado inteiro -, pela categoria
# ANBIMA (`report/por_categoria.py`). Sao ~30 categorias (14/10/2026); o e-mail
# mostra a que mexe ao menos R$ 50 mi em 3 meses, para entrar ou para sair, e
# conta as outras numa linha.
_POR_CATEGORIA = (
    "todos os fundos das vitrines, cada um uma vez, com a captação no mercado inteiro, "
    "pela categoria ANBIMA"
)
_CABECALHO_POR_CATEGORIA = ["Categoria", "Fundos", "Semana", "3 meses", "Ritmo na semana"]
_MIN_CATEGORIA_NO_DIGEST = 50_000_000.0


def _nota_por_categoria(visao: VisaoDoDigest) -> str | None:
    """Como a tabela por categoria e montada: a nota dela, na secao de captacao."""
    if not visao.por_categoria:
        return None
    return (
        f"Por categoria: os fundos das vitrines de {_juntar(_rotulos_do_foco(visao))}, "
        "cada um uma vez, com informe, PL de ao menos R$ 1 mi no início e sem movimento "
        "estrutural, somados pela categoria ANBIMA. A captação é a do fundo no mercado "
        "inteiro (informe da CVM), não a que cada casa fez; o ritmo é o da soma de cada "
        "categoria. Aparece a categoria com ao menos "
        f"{_mm(_MIN_CATEGORIA_NO_DIGEST).lstrip('+')} de captação ou resgate em 3 meses."
    )


def _rotulos_do_foco(visao: VisaoDoDigest) -> list[str]:
    return [_platform_label(pid) for pid in visao.casas]


def _categorias_no_digest(visao: VisaoDoDigest) -> tuple[list[dict], int]:
    """As categorias da tabela, e quantas ficaram de fora por mexer pouco."""
    todas = visao.por_categoria or []
    mostradas = [c for c in todas if abs(c["captacao_3m"]) >= _MIN_CATEGORIA_NO_DIGEST]
    return mostradas, len(todas) - len(mostradas)


def _resto_das_categorias(fora: int) -> str:
    return (
        f"Mais {contagem(fora, 'categoria', 'categorias')}, cada uma com menos de "
        f"{_mm(_MIN_CATEGORIA_NO_DIGEST).lstrip('+')} em 3 meses."
    )


def _celulas_da_categoria(categoria: dict) -> list[str]:
    return [
        milhar(categoria["fundos"]), _mm_ou_traco(categoria["captacao_semana"]),
        _mm_ou_traco(categoria["captacao_3m"]),
        nome_da_tendencia(categoria["ritmo_semanal"]) or "-",
    ]


def _md_por_categoria(visao: VisaoDoDigest) -> list[str]:
    """A captacao das vitrines por categoria: semana, 3 meses e o ritmo da soma."""
    categorias, fora = _categorias_no_digest(visao)
    if not categorias:
        return []
    return [
        f"**Por categoria** - {_POR_CATEGORIA}",
        "",
        "| " + " | ".join(_CABECALHO_POR_CATEGORIA) + " |",
        "|---|---:|---:|---:|---|",
        *(
            f"| {c['categoria']} | " + " | ".join(_celulas_da_categoria(c)) + " |"
            for c in categorias
        ),
        "",
        *([_resto_das_categorias(fora), ""] if fora else []),
    ]


def _html_por_categoria(visao: VisaoDoDigest) -> str:
    categorias, fora = _categorias_no_digest(visao)
    if not categorias:
        return ""
    linhas = [
        [_esc(c["categoria"]), *(_esc(celula) for celula in _celulas_da_categoria(c))]
        for c in categorias
    ]
    resto = f'<p style="{_NOTA}">{_esc(_resto_das_categorias(fora))}</p>' if fora else ""
    return _legenda(f"**Por categoria** · {_POR_CATEGORIA}") + _tabela(
        _CABECALHO_POR_CATEGORIA, linhas, direita={1, 2, 3}, sinal={2, 3}, compacta=True,
        larguras={0: 40},
    ) + resto


def _md_tabela_de_gaps(linhas: list[dict], com_cotistas: bool) -> list[str]:
    cabecalho = ["Fundo", "Categoria", "Casas", "Semana", "3 meses"]
    if com_cotistas:
        cabecalho.append("Cotistas")
    cabecalho += ["Rent. 12m", "Taxa global", "Ritmo na semana", "Esforço"]
    lines = [
        "| " + " | ".join(cabecalho) + " |",
        "|---|---|---|---:|---:|" + ("---:|" if com_cotistas else "") + "---:|---:|---|---|",
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
        celulas += [
            _texto_de_rentabilidade(f), _pct(f.get("taxa_global")),
            nome_da_tendencia(f.get("ritmo_semanal")) or "-", _esforco_na_tabela(f),
        ]
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
    # A legenda do ritmo logo depois dos numeros (17/10/2026); no texto puro nao
    # ha como recolher.
    if _com_ritmo(visao):
        lines += _md_legenda_do_ritmo()
    lines += _md_resumo_por_casa(op)

    com_cotistas = op.get("com_cotistas")
    mostrados = 0
    for casa, linhas in _linhas_por_casa(op):
        if not linhas:
            continue
        mostrados += len(linhas)
        primeiros = "o primeiro" if len(linhas) == 1 else f"os {len(linhas)} primeiros"
        lines += [
            f"**{casa['label']}** - {primeiros}, {_ordem_da_lista(op)}",
            "",
            *_md_tabela_de_gaps(linhas, com_cotistas),
            "",
        ]
    resto = len(op["linhas"]) - mostrados
    if resto > 0:
        lines += [f"Mais {milhar(resto)} na planilha.", ""]

    for nota in _notas_da_oportunidade(visao) + _avisos_da_oportunidade(visao):
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
    linhas = [[_esc(casa["label"]), *_resumo_da_casa(op, casa)] for casa in casas]
    return _tabela(
        ["Concorrente", _rotulo_dos_gaps(op), _coluna_da_lista(op), "Para trazer", "Semana",
         "3 meses"],
        linhas, direita={1, 2, 3, 4, 5}, sinal={4, 5},
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
    # A legenda do ritmo logo depois dos numeros, num bloco que abre no clique
    # (17/10/2026).
    if _com_ritmo(visao):
        parts.append(_como_ler(_html_legenda_do_ritmo(), "Como ler o ritmo"))
    parts.append(_html_resumo_por_casa(op))

    grupos = [(casa, linhas) for casa, linhas in _linhas_por_casa(op) if linhas]
    if grupos:
        semana = _semana(visao.report.retail_flow)
        parts.append(_legenda(_linha(
            f"**Os {_GAP_POR_CASA} primeiros de cada concorrente**",
            _ordem_da_lista(op),
            f"semana {periodo_br(semana['de'], semana['ate'])}" if semana else "",
        )))
        cabecalho = ["Fundo", "Casas", "Semana", "3 meses"]
        if com_cotistas:
            cabecalho.append("Cotistas")
        # A taxa global ao lado da rentabilidade (20/10/2026): o retorno e o custo.
        cabecalho += ["Rent. 12m", "Taxa global", "Esforço"]
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
                linha += [
                    _celula_de_rentabilidade(f), _pct(f.get("taxa_global")),
                    _esc(_esforco_na_tabela(f)),
                ]
                tabela.append(linha)
        # Os numeros a direita, da Semana a Taxa global.
        numericas = set(range(2, cabecalho.index("Taxa global") + 1))
        parts.append(_tabela(
            cabecalho, tabela, direita=numericas, sinal={2, 3}, compacta=True,
            larguras={0: 28, 1: 8, 2: 15, 3: 13},
        ))
        parts.append(_mais(len(op["linhas"]) - mostrados, "na planilha"))

    avisos = _avisos_da_oportunidade(visao)
    if avisos:
        parts.append(f'<p style="{_NOTA}">{_esc(" ".join(avisos))}</p>')
    notas = [_esc(n) for n in _notas_da_oportunidade(visao)]
    flow = visao.report.retail_flow
    if flow and flow.get("available"):
        notas.append(_esc(_capitalizar("; ".join(_prazos_da_captacao(flow)))) + ".")
    parts.append(_como_ler("".join(f'<p style="{_NOTA}">{n}</p>' for n in notas)))
    return "".join(parts)


# --- ritmo de captacao ---------------------------------------------------------

# Os ritmos do quadro por casa, de quem capta mais a quem perde mais. Sao
# excludentes - cada fundo esta num so -, e a linha soma 100% (pedido de
# 19/10/2026). Ate ali o quadro trazia "Captando", que ja somava o acelerando,
# ao lado de "Acelerando", sem o "sem serie": a linha passava de 100%.
_RITMOS_DO_QUADRO = (
    "acelerando", "virou_entrada", "estavel", "desacelerando", "virou_saida",
    "saida_persistente", "sem_serie",
)


def _porcentagens_inteiras(parcelas: list[float]) -> list[int]:
    """As parcelas da linha em % inteiros que somam 100, pelos maiores restos.

    Arredondada celula a celula, a linha saia com 99% ou 101% (BTG em
    14/10/2026: 12 + 18 + 2 + 8 + 9 + 52), e o quadro tem de fechar em 100%.
    """
    brutos = [p * 100 for p in parcelas]
    inteiros = [int(b) for b in brutos]
    falta = round(sum(brutos)) - sum(inteiros)
    for i in sorted(range(len(brutos)), key=lambda i: inteiros[i] - brutos[i])[:falta]:
        inteiros[i] += 1
    return inteiros


def _bloco_do_ritmo(flow: dict) -> dict | None:
    """A tabela de ritmo por casa: % dos fundos em cada ritmo, a linha somando 100%.

    "Sem serie" so vira coluna quando alguma casa tem fundo sem serie - a
    coluna de zeros so alargaria o mapa no telefone.

    Toda linha conta a vitrine do site, a prateleira aberta - tambem a da XP
    (20/10/2026). De 18 a 20/10/2026 a da XP saia da lista distribuivel, como
    "XP Investimentos (lista)".
    """
    por_casa = flow.get("tendencia_por_casa") or {}
    em_foco = [c for c in flow.get("casas") or [] if por_casa.get(c["platform_id"])]
    estados = [
        e for e in _RITMOS_DO_QUADRO
        if e != "sem_serie"
        or any(por_casa[c["platform_id"]].get(e) for c in em_foco)
    ]
    linhas, parcelas, casas = [], [], []
    for casa in flow.get("casas") or []:
        por_estado = por_casa.get(casa["platform_id"]) or {}
        total = sum(por_estado.values())
        if not total:
            continue
        numeros = [por_estado.get(e, 0) / total for e in estados]
        # O rotulo de hoje, e nao o gravado no bloco: um bloco calculado antes de
        # 15/10/2026 ainda trazia "Itaú (íon)" (pedido de 19/10/2026).
        linhas.append([
            _platform_label(casa["platform_id"]), str(total),
            *(f"{n}%" for n in _porcentagens_inteiras(numeros)),
        ])
        parcelas.append(numeros)
        casas.append(casa["platform_id"])
    if not linhas:
        return None
    return {
        "cabecalho": [
            "Casa", "Fundos", *(_capitalizar(nome_da_tendencia(e)) for e in estados)
        ],
        "estados": estados,
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
    rentabilidade = _frase_de_rentabilidade(fundo)
    if rentabilidade:
        detalhe += f", {rentabilidade}"
    item = f"{_nome_do_fundo(fundo['nome'])} - {detalhe} - {_casas_do_fundo(fundo, visao)}"
    return item + (f" - {_frase_na_xp(fundo)}" if _com_na_xp(visao) else "")


# A captacao do fundo na XP ao lado da do mercado (pedido de 20/10/2026), nas
# mesmas janelas e na mesma unidade (`report.captacao_na_xp`): so o fundo que a
# XP distribui, ou os dela com o mesmo master.


def _com_na_xp(visao: VisaoDoDigest) -> bool:
    return na_xp.com_numero(visao.report.captacao_xp)


def _frase_na_xp(fundo: dict) -> str:
    """"na XP +12,0 MM em 3 meses, +1,0 MM na semana, pelo espelho"; "na XP: -"
    quando a XP nao distribui o fundo."""
    if not fundo.get("xp_ligacao"):
        return "na XP: -"
    # A janela sem numero sai da frase; a nota diz por que.
    partes = [
        f"{_mm(valor)} {prazo}" for valor, prazo in (
            (fundo.get("captacao_xp_3m"), "em 3 meses"),
            (fundo.get("captacao_xp_semana"), "na semana"),
        ) if valor is not None
    ]
    via = na_xp.via(fundo)
    return "na XP " + ", ".join(partes + ([via] if via else []))


def _celula_na_xp(fundo: dict) -> str:
    """HTML: a captacao na XP em 3 meses, com a da semana (e o caminho) miudos."""
    if not fundo.get("xp_ligacao"):
        return "-"
    semana = fundo.get("captacao_xp_semana")
    miudo = _linha(
        "" if semana is None else f"{_mm(semana)} na semana", na_xp.via(fundo)
    )
    return _mm_ou_traco(fundo.get("captacao_xp_3m")) + (_miudo(_esc(miudo)) if miudo else "")


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
            "anteriores. Cada fundo está num ritmo só: a linha soma 100%.",
            "",
            "| " + " | ".join(ritmo["cabecalho"]) + " |",
            "|---|" + "---:|" * (len(ritmo["cabecalho"]) - 1),
            *("| " + " | ".join(linha) + " |" for linha in ritmo["linhas"]),
            "",
        ]
    listas = _listas_do_ritmo(flow, com_cotistas)
    for titulo, fundos in listas:
        if fundos:
            lines += [f"**{titulo}**", ""]
            lines += [f"- {_item_de_fundo(f, com_cotistas, visao)}" for f in fundos]
            lines.append("")
    # Por categoria, abaixo das maiores captacoes e resgates (19/10/2026).
    lines += _md_por_categoria(visao)
    if flow.get("total_estruturais"):
        lines += [
            f"_{contagem(flow['total_estruturais'], 'fundo', 'fundos')} com movimento "
            "estrutural (fluxo maior que o próprio patrimônio, ou fundo esvaziado) ficam "
            "fora das listas: é dinheiro trocando de veículo, não demanda._",
            "",
        ]
    for nota in _notas_de_fora(flow):
        lines += [f"_{nota}._", ""]
    categoria = _nota_por_categoria(visao)
    if categoria:
        lines += [f"_{categoria}_", ""]
    for nota in na_xp.notas(visao.report.captacao_xp):
        lines += [f"_{nota}_", ""]
    prazo = _prazo_da_rentabilidade(visao.report)
    if prazo and any(fundos for _, fundos in listas):
        lines += [f"_{prazo}_", ""]
    return lines


# A legenda do ritmo (pedidos de 17/10/2026): logo depois dos numeros em
# destaque, num bloco que abre no clique. O mesmo rotulo vale para a semana
# (tabelas de gaps e por categoria, listas de captacao) e para o mes (mapa por
# casa): muda so a janela, e a faixa e a da regra (`classificar_ritmo`).
_COMO_LER_O_RITMO = (
    "a captação líquida por dia útil de agora contra a de antes. Nas tabelas de gaps "
    "e por categoria e nas listas, a semana contra as 12 semanas anteriores; no mapa "
    "por casa, o último mês contra os anteriores."
)


def _com_ritmo(visao: VisaoDoDigest) -> bool:
    """O digest mostra ritmo so quando ha informe diario nesta execucao."""
    flow = visao.report.retail_flow
    return bool(flow and flow.get("available"))


def _md_legenda_do_ritmo() -> list[str]:
    return [
        f"**Como ler o ritmo** - {_COMO_LER_O_RITMO}",
        "",
        "| Ritmo | O que quer dizer |",
        "|---|---|",
        *(f"| {nome} | {texto} |" for nome, texto in _legenda_do_ritmo()),
        "",
    ]


def _html_legenda_do_ritmo() -> str:
    """O corpo do bloco "Como ler o ritmo": o titulo e o do resumo do bloco."""
    return _legenda(_capitalizar(_COMO_LER_O_RITMO)) + _tabela(
        ["Ritmo", "O que quer dizer"],
        [[_esc(nome), _esc(texto)] for nome, texto in _legenda_do_ritmo()],
        compacta=True,
    )


def _legenda_do_ritmo() -> list[tuple[str, str]]:
    """(rotulo, o que quer dizer) de cada ritmo, na ordem de quem capta mais."""
    faixa = f"{_TREND_BAND:.0%}"
    textos = (
        ("acelerando", f"já captava e passou a captar mais de {faixa} acima do ritmo de antes"),
        ("virou_entrada", "perdia dinheiro antes e passou a captar"),
        ("estavel", f"capta no ritmo de antes, com até {faixa} para mais ou para menos"),
        ("desacelerando", f"ainda capta, mas mais de {faixa} abaixo do ritmo de antes"),
        ("virou_saida", "captava antes e passou a perder dinheiro"),
        ("saida_persistente", "perdia dinheiro antes e continua perdendo"),
        ("sem_serie", "dado de menos para comparar"),
    )
    return [(_capitalizar(nome_da_tendencia(chave)), texto) for chave, texto in textos]


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
    posicao = bloco["estados"].index("saida_persistente")
    persistente = {
        pid: numeros[posicao] for pid, numeros in zip(bloco["casas"], bloco["parcelas"])
    }
    # O titulo escreve o numero da celula, que fecha a linha em 100%.
    escrito = {
        pid: texto[2 + posicao] for pid, texto in zip(bloco["casas"], bloco["linhas"])
    }
    base_id = flow.get("baseline_id")
    if base_id in persistente and len(persistente) > 1:
        outras = {pid: v for pid, v in persistente.items() if pid != base_id}
        melhor = min(outras, key=outras.get)
        if persistente[base_id] <= outras[melhor]:
            titulo = (
                f"{_platform_label(base_id)} tem a menor parcela de fundos em saída "
                f"persistente: {escrito[base_id]}"
            )
        else:
            titulo = (
                f"{_platform_label(base_id)} tem {escrito[base_id]} dos fundos em "
                f"saída persistente; {_platform_label(melhor)}, {escrito[melhor]}"
            )
    else:
        pior = max(persistente, key=persistente.get)
        titulo = (
            f"{_platform_label(pior)} tem a maior parcela de fundos em saída "
            f"persistente: {escrito[pior]}"
        )
    return figura(
        titulo,
        _linha("% dos fundos de cada vitrine por ritmo de captação",
               _janela_da_captacao(flow.get("periodo") or {})),
        corpo,
        "Cada fundo num ritmo só: a linha soma 100% · fundos com PL de ao menos R$ 1 mi "
        "no início.",
    )


def _tabela_de_fundos_da_captacao(
    fundos: list[dict], com_cotistas: bool, visao: VisaoDoDigest
) -> str:
    """Uma lista de fundos da captacao como tabela: Fundo | Semana | 3 meses |
    Na XP | Cotistas | Rent. 12m | Casas. O ritmo da semana vai miudo sob ela, a
    semana na XP sob a captacao na XP, o ticket por cotista novo sob os
    cotistas, a comparacao com a categoria sob a rentabilidade. Sem as bases da
    XP, sem a coluna Na XP."""
    com_xp = _com_na_xp(visao)
    cabecalho = ["Fundo", "Semana", "3 meses"] + (["Na XP"] if com_xp else [])
    if com_cotistas:
        cabecalho.append("Cotistas")
    cabecalho += ["Rent. 12m", "Casas"]
    linhas = []
    for fundo in fundos:
        # Sem o CNPJ: nas listas de mercado o nome basta, e o CNPJ esta na
        # planilha - cada byte conta no orcamento do e-mail.
        linha = [
            _esc(_nome_do_fundo(fundo["nome"])), _celula_da_semana(fundo),
            _mm(fundo["captacao_liquida"]),
        ]
        if com_xp:
            linha.append(_celula_na_xp(fundo))
        if com_cotistas:
            linha.append(_celula_de_cotistas(fundo))
        linha += [_celula_de_rentabilidade(fundo), _esc(_casas_do_fundo(fundo, visao))]
        linhas.append(linha)
    # A semana com folga para o ritmo miudo ("saída persistente") caber numa linha.
    larguras = {0: 26, 1: 16, 2: 13, 3: 15} if com_xp else {0: 30, 1: 17, 2: 14}
    if com_cotistas:
        larguras[cabecalho.index("Cotistas")] = 12
    # Os numeros a direita, da Semana a Rent. 12m.
    return _tabela(
        cabecalho, linhas, direita=set(range(1, cabecalho.index("Rent. 12m") + 1)),
        sinal={1, 2, 3} if com_xp else {1, 2}, larguras=larguras,
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
    listas = _listas_do_ritmo(flow, com_cotistas)
    for titulo, fundos in listas:
        if not fundos:
            continue
        destaque, _, resto = titulo.partition(" - ")
        parts.append(
            f'<p style="{_LEGENDA}"><strong style="color:{TINTA};">'
            f"{_esc(destaque)}</strong>{' · ' + _esc(resto) if resto else ''}</p>"
        )
        parts.append(_tabela_de_fundos_da_captacao(fundos, com_cotistas, visao))
    # Por categoria, abaixo das maiores captacoes e resgates (19/10/2026).
    parts.append(_html_por_categoria(visao))
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
    notas += [f"{nota}." for nota in _notas_de_fora(flow)]
    categoria = _nota_por_categoria(visao)
    if categoria:
        notas.append(categoria)
    notas += na_xp.notas(visao.report.captacao_xp)
    prazo = _prazo_da_rentabilidade(visao.report)
    if prazo and any(fundos for _, fundos in listas):
        notas.append(prazo)
    # As notas ficam no bloco que abre no clique (17/10/2026); a legenda do
    # ritmo fica na oportunidade, depois dos numeros em destaque.
    paragrafos = [_esc(n) for n in notas]
    paragrafos[0] += f" {_fonte_html(CVM_INFORME_DIARIO)}"
    parts.append(_como_ler("".join(f'<p style="{_NOTA}">{p}</p>' for p in paragrafos)))
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
            # A gestora mapeada: a do master, quando o fundo e espelho.
            manager = f" - {_offer_manager(fund)}" if _offer_manager(fund) else ""
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
            # A gestora mapeada: a do master, quando o fundo e espelho.
            manager = f" - {_offer_manager(fund)}" if _offer_manager(fund) else ""
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
            ["Entrou", fundo(f), _esc(_offer_manager(f) or "-")] for f in entradas[:_MAX_LISTED]
        ] + [
            ["Saiu", fundo(f), _esc(_offer_manager(f) or "-")] for f in saidas[:_MAX_LISTED]
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
        lines += _md_custo_por_classe(custo)

    for nota in _notas_de_fora_do_mensal(principal, custo):
        lines += [f"_{nota}._", ""]
    fora = _excluidos(blocos)
    for excluido in fora:
        lines.append(f"> Fora: {excluido['label']} - {excluido['reason']}")
    if fora:
        lines.append("")
    return lines


def _notas_de_fora(bloco: dict | None) -> list[str]:
    """O que o bloco tirou da analise, uma frase por regra: os fundos
    caixa/simples (16/10/2026) e os de previdencia (17/10/2026)."""
    bloco = bloco or {}
    notas = (
        nota_caixa(bloco.get("caixa_fora")),
        nota_previdencia(bloco.get("previdencia_fora")),
    )
    return [nota for nota in notas if nota]


def _notas_de_fora_do_mensal(principal: dict, custo: dict | None) -> list[str]:
    """O que saiu da rentabilidade e do custo, uma frase por regra.

    As duas janelas olham as mesmas vitrines; o custo, tambem. Vale o maior
    numero de cada regra, para a frase nao subcontar quando um bloco faltar.
    """
    custo = custo if custo and custo.get("available") else {}
    return _notas_de_fora({
        chave: max(principal.get(chave) or 0, custo.get(chave) or 0)
        for chave in ("caixa_fora", "previdencia_fora")
    })


def _indice_mediano(casa: dict) -> str:
    """"1,65x", ou "-" quando a casa nao tem amostra comparavel."""
    indice = casa.get("indice_mediano")
    return "-" if indice is None else f"{indice:.2f}x".replace(".", ",")


# --- custo por classe (pedido de 16/10/2026) ----------------------------------------
#
# A mesma conta do custo da casa, dentro de cada classe de fundo (nivel 1 da
# ANBIMA). As colunas sao as classes com indice em alguma das casas do recorte;
# a XP sai da base interna, e a nota do bloco diz isso.


def _celulas_por_classe(casa: dict) -> dict[str, dict]:
    return {c["classe"]: c for c in casa.get("por_classe") or []}


def _md_custo_por_classe(custo: dict) -> list[str]:
    """A nota da base da prateleira e a tabela casa x classe, em markdown."""
    casas = custo.get("casas") or []
    lines = [f"_{custo['nota_prateleira']}_", ""] if custo.get("nota_prateleira") else []
    classes = classes_com_indice(casas)
    if not classes:
        return lines
    lines += [
        "**Custo por classe de fundo** (índice mediano; entre parênteses, os fundos "
        "comparáveis)",
        "",
        "| Casa | " + " | ".join(classes) + " |",
        "|---|" + "---:|" * len(classes),
    ]
    for casa in casas:
        celulas = _celulas_por_classe(casa)
        textos = []
        for classe in classes:
            celula = celulas.get(classe)
            textos.append(
                "-" if celula is None
                else f"{_indice_mediano(celula)} ({_contagem(celula['comparaveis'])})"
            )
        lines.append(f"| {casa['label']} | " + " | ".join(textos) + " |")
    lines += ["", f"_{nota_das_classes(casas)}_", ""]
    return lines


def _html_custo_por_classe(custo: dict) -> str:
    """A nota da base da prateleira e a tabela casa x classe, no e-mail."""
    casas = custo.get("casas") or []
    partes = []
    if custo.get("nota_prateleira"):
        partes.append(f'<p style="{_NOTA}">{_esc(custo["nota_prateleira"])}</p>')
    classes = classes_com_indice(casas)
    if not classes:
        return "".join(partes)
    partes.append(f'<h3 style="{_H3}">Custo por classe de fundo</h3>')
    linhas = []
    for casa in casas:
        celulas = _celulas_por_classe(casa)
        linha = [_esc(casa["label"])]
        for classe in classes:
            celula = celulas.get(classe)
            linha.append(
                "-" if celula is None
                else _indice_mediano(celula) + _miudo(
                    _esc(_contagem_de_fundos(celula["comparaveis"]))
                )
            )
        linhas.append(linha)
    partes.append(_tabela(
        ["Casa", *classes], linhas, direita=range(1, len(classes) + 1),
        base=[_e_base(casa.get("platform_id")) for casa in casas], compacta=True,
    ))
    partes.append(
        f'<p style="{_NOTA}">Índice mediano da classe; embaixo, os fundos '
        f"comparáveis. {_esc(nota_das_classes(casas))}</p>"
    )
    return "".join(partes)


def _contagem_de_fundos(n: int) -> str:
    return f"{_contagem(n)} {'fundo' if n == 1 else 'fundos'}"


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
        partes.append(_html_custo_por_classe(custo))

    for nota in _notas_de_fora_do_mensal(principal, report.custo):
        partes.append(f'<p style="{_NOTA}">{_esc(nota)}.</p>')
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
