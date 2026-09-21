"""Rentabilidade da vitrine ponderada por PL e por numero de cotistas.

A pergunta que este modulo responde nao e "quanto rende o fundo mediano da
prateleira", e sim **os fundos onde estao o dinheiro e as pessoas estao rendendo
de forma adequada**. Sao perguntas diferentes, e a diferenca entre as respostas
e o achado:

    mediana da vitrine    quanto rende o fundo MEDIANO da prateleira
    ponderada por PL      quanto rendeu o REAL medio
    ponderada por cotista quanto rendeu o COTISTA medio

O gap entre as duas ponderadas diz onde esta o desalinhamento. Positivo, o
dinheiro grande esta nos fundos bons e o varejo nos piores; negativo, o inverso.

Medido em 2026-09-20, janela 2026-07-01 a 2026-09-18 (56 pregoes), sobre as
vitrines coletadas:

                mediana   por PL   por cotista      gap   abaixo da categoria
    XP  (567)     2,93%    2,96%       3,61%    -0,65pp   34% do PL, 34% dos cotistas
    BTG (601)     2,91%    3,12%       3,62%    -0,50pp   37% do PL, 32% dos cotistas
    Inter (363)   2,92%    3,54%       3,93%    -0,39pp   34% do PL, 33% dos cotistas

Nas tres casas o cotista medio rendeu mais que o real medio. A causa esta no
recorte por categoria: em Multimercados Livre a XP tem gap de -0,99pp e em Acoes
Livre +2,05pp, enquanto a renda fixa de duracao baixa - onde esta a maior parte
do PL - fica em 0,00pp. O varejo se pulveriza em categorias que subiram no
periodo; o dinheiro grande fica onde a dispersao e minima.

O Itau fica de fora, e por um motivo que o resolver de categoria nao resolve:
ele publica CNPJ em 6% da vitrine, e sem CNPJ nao ha como buscar a serie de cota
no informe. `shelf_monitor.classify` resolve CATEGORIA pelo nome; nenhuma serie
e indexada por nome.

Tres coisas que este numero NAO e
---------------------------------

**Nao e o retorno dos clientes da casa.** A CVM publica cota, PL e cotistas por
FUNDO, somando todos os distribuidores. Uma rentabilidade ponderada pela vitrine
e o retorno dos fundos que a casa exibe, com o peso que eles tem no mercado
inteiro - nao com o peso que tem na base dela. O rotulo tem de dizer isso, e
`FlowAttribution` (ver retail_flow) e onde o dado interno entraria para que
passasse a ser.

**Nao e retorno total.** A cota patrimonial e ex-provento; fundo que distribui
aparece abaixo do que o investidor recebeu.

**Nao diz "bom" sozinho.** "Adequado" so significa algo contra uma referencia, e
a referencia aqui e a mediana da MESMA categoria no universo oficial inteiro -
nao na propria vitrine. Comparar a prateleira contra ela mesma mediria a
prateleira, nao o mercado.

Ressalva de janela
------------------

Janela curta comprime a dispersao: em 2,5 meses dominados pelo CDI, quase tudo
fica a poucos centesimos da mediana da categoria. Doze meses separam muito mais.
O bloco publica `dias` e `periodo` para que a leitura saiba o que esta olhando.
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field

from shelf_monitor.config import Config
from shelf_monitor.models import FundOffer, ShelfSnapshot
from shelf_monitor.normalize import normalize_cnpj
from shelf_monitor.report.aggregate import _platform_label, categoria_comparavel
from shelf_monitor.report.competitive import identifiable_vitrines, vitrine_snapshots
from shelf_monitor.report.retail_flow import (
    DailyFlow,
    agrupar_por_classe,
    months_back,
    read_daily_flows,
)
from shelf_monitor.report.shared import (
    MIN_AMOSTRA_CATEGORIA,
    ponderada,
    MIN_COBERTURA_JANELA,
    MIN_COBERTURA_VITRINE,
)

# Fracao dos pregoes do periodo em que o fundo precisa ter sido observado.
#
# Um limiar absoluto de pregoes nao basta, e a diferenca importa: com "pelo
# menos 20 dias", um fundo que so aparece em setembro entra com retorno de tres
# semanas ao lado de outro com retorno de tres meses, e a media - ponderada ou
# nao - soma periodos diferentes. Medido em 2026-09-20 sobre a vitrine da XP,
# trocar o limiar absoluto por este moveu a ponderada por cotista de 4,09% para
# 3,56%: meio ponto percentual que era janela curta, nao rentabilidade.
#
# 80% tolera feriado e atraso de entrega sem deixar passar fundo que nasceu no
# meio do periodo.
_MIN_COBERTURA_JANELA = MIN_COBERTURA_JANELA

# Piso absoluto, para o caso de o proprio periodo ser curto.
_MIN_PREGOES = 20

# Minimo de fundos numa categoria para a mediana dela servir de referencia. Com
# meia duzia, um fundo atipico move a mediana e todo mundo vira "acima" ou
# "abaixo" dela por artefato de amostra.
_MIN_AMOSTRA_CATEGORIA = MIN_AMOSTRA_CATEGORIA

# Minimo de fundos casados para a casa entrar nos agregados ponderados. Mesma
# regra que o fluxo usa: abaixo disso a soma descreve a amostra que casou, nao a
# vitrine.
_MIN_COBERTURA = MIN_COBERTURA_VITRINE


@dataclass
class RetornoFundo:
    """Retorno de um fundo no periodo, com os pesos que a ponderacao usa."""

    cnpj: str
    categoria: str | None
    retorno: float
    pl: float
    cotistas: int
    dias: int
    nome: str = ""
    captacao_liquida: float = 0.0
    variacao_cotistas: int = 0
    vitrines: list[str] = field(default_factory=list)
    # Preenchido quando a categoria tem amostra suficiente no universo oficial.
    mediana_categoria: float | None = None

    @property
    def excesso(self) -> float | None:
        """Quanto o fundo rendeu acima da mediana da propria categoria."""
        if self.mediana_categoria is None:
            return None
        return self.retorno - self.mediana_categoria

    @property
    def abaixo_da_categoria(self) -> bool | None:
        excesso = self.excesso
        return None if excesso is None else excesso < 0

    @property
    def ticket_medio(self) -> float | None:
        """PL por cotista - separa fundo de varejo de veiculo institucional."""
        if self.cotistas <= 0:
            return None
        return self.pl / self.cotistas

    def to_dict(self) -> dict:
        return {
            "cnpj": self.cnpj,
            "nome": self.nome,
            "categoria": self.categoria,
            "vitrines": self.vitrines,
            "retorno": round(self.retorno, 4),
            "mediana_categoria": (
                None if self.mediana_categoria is None
                else round(self.mediana_categoria, 4)
            ),
            "excesso": None if self.excesso is None else round(self.excesso, 4),
            "pl": round(self.pl, 2),
            "cotistas": self.cotistas,
            "variacao_cotistas": self.variacao_cotistas,
            "captacao_liquida": round(self.captacao_liquida, 2),
            "ticket_medio": (
                None if self.ticket_medio is None else round(self.ticket_medio, 2)
            ),
            "dias": self.dias,
        }


def _ponderada(fundos: list[RetornoFundo], peso) -> float | None:
    """Media de retorno ponderada por um peso. None quando o peso soma zero.

    `sem_peso="none"` e a semantica deste bloco especificamente: o numero e
    "quanto rendeu o real medio", e sem real nenhum nao ha real medio. Os
    outros blocos que ponderam caem para a media simples - a diferenca e
    deliberada e esta documentada em `shared.numeros.ponderada`.
    """
    return ponderada(fundos, lambda f: f.retorno, peso, sem_peso="none")


@dataclass
class PerformanceCasa:
    """Como rende a vitrine de uma casa, nas tres leituras."""

    platform_id: str
    label: str
    vitrine: int
    com_serie: int
    fundos: list[RetornoFundo] = field(default_factory=list)

    @property
    def cobertura(self) -> float:
        return self.com_serie / self.vitrine if self.vitrine else 0.0

    @property
    def mediana(self) -> float | None:
        return statistics.median(f.retorno for f in self.fundos) if self.fundos else None

    @property
    def por_pl(self) -> float | None:
        return _ponderada(self.fundos, lambda f: f.pl)

    @property
    def por_cotista(self) -> float | None:
        return _ponderada(self.fundos, lambda f: float(f.cotistas))

    @property
    def gap(self) -> float | None:
        """Ponderada por PL menos ponderada por cotista, em pontos percentuais.

        Positivo: o dinheiro grande esta nos fundos melhores e o varejo nos
        piores. Negativo: o inverso.
        """
        pl, cotista = self.por_pl, self.por_cotista
        if pl is None or cotista is None:
            return None
        return pl - cotista

    def _massa(self, selecao) -> dict:
        com_ref = [f for f in self.fundos if f.mediana_categoria is not None]
        pl_total = sum(f.pl for f in com_ref)
        cot_total = sum(f.cotistas for f in com_ref)
        alvo = [f for f in com_ref if selecao(f)]
        share_fundos = len(alvo) / len(com_ref) if com_ref else None
        share_pl = sum(f.pl for f in alvo) / pl_total if pl_total else None
        share_cot = sum(f.cotistas for f in alvo) / cot_total if cot_total else None

        def indice(share: float | None) -> float | None:
            if share is None or not share_fundos:
                return None
            return share / share_fundos

        return {
            "fundos": len(alvo),
            "de": len(com_ref),
            "share_fundos": None if share_fundos is None else round(share_fundos, 4),
            "pl": round(sum(f.pl for f in alvo), 2),
            "cotistas": sum(f.cotistas for f in alvo),
            "share_pl": None if share_pl is None else round(share_pl, 4),
            "share_cotistas": None if share_cot is None else round(share_cot, 4),
            "indice_pl": (
                None if indice(share_pl) is None else round(indice(share_pl), 3)
            ),
            "indice_cotistas": (
                None if indice(share_cot) is None else round(indice(share_cot), 3)
            ),
        }

    @property
    def alocacao_abaixo_da_mediana(self) -> dict:
        """Concentracao do PL e dos cotistas nos fundos abaixo da categoria.

        **O numero que decide e o INDICE, nao o share.** Mediana e mediana: por
        construcao, cerca de metade de qualquer universo fica abaixo dela, e uma
        vitrine com 51% dos fundos abaixo esta exatamente no esperado. Ler esse
        51% como alarme - ou pior, ler o share de PL isolado - inverte o sinal.

        O indice e `share do PL nos fundos abaixo` dividido por `share dos fundos
        abaixo`. Abaixo de 1, o dinheiro EVITA os piores da categoria; acima de
        1, se concentra neles. Medido em 2026-09-20 na XP: 288 de 563 fundos
        (51%) abaixo da mediana carregando 34% do PL e 34% dos cotistas - indice
        0,66 nos dois, ou seja, dinheiro e pessoas desproporcionalmente nos
        fundos melhores. Era boa noticia publicada sob o rotulo "massa mal
        alocada", que e o tipo de erro que faz um comite pedir plano de acao
        para um problema que nao existe.
        """
        return self._massa(lambda f: f.abaixo_da_categoria is True)

    def to_dict(self) -> dict:
        def r(valor: float | None, casas: int = 4) -> float | None:
            return None if valor is None else round(valor, casas)

        return {
            "platform_id": self.platform_id,
            "label": self.label,
            "vitrine": self.vitrine,
            "com_serie": self.com_serie,
            "cobertura": round(self.cobertura, 3),
            "mediana": r(self.mediana),
            "por_pl": r(self.por_pl),
            "por_cotista": r(self.por_cotista),
            "gap": r(self.gap),
            "pl_total": round(sum(f.pl for f in self.fundos), 2),
            "cotistas_total": sum(f.cotistas for f in self.fundos),
            "alocacao_abaixo_da_mediana": self.alocacao_abaixo_da_mediana,
        }


def _consolidar(
    registros: list[DailyFlow],
) -> dict[str, tuple[float, float, int, int, float, int]]:
    """Por CNPJ: retorno, PL final, cotistas finais, pregoes, captacao, delta cotistas.

    O agrupamento por classe (subclasses somadas, retificacao deduplicada) mora
    em `retail_flow.agrupar_por_classe`, uma vez so - era ali que o mesmo erro
    aparecia primeiro. Aqui fica a regra de elegibilidade: o fundo precisa ter
    sido observado na quase totalidade da janela, e ter cota e PL utilizaveis.
    """
    classes = agrupar_por_classe(registros)
    pregoes_periodo = {r.dia for r in registros}
    minimo = max(_MIN_PREGOES, int(len(pregoes_periodo) * _MIN_COBERTURA_JANELA))

    consolidado: dict[str, tuple[float, float, int, int, float, int]] = {}
    for cnpj, classe in classes.items():
        if classe.dias < minimo:
            continue
        if classe.retorno_pct is None or classe.pl_fim <= 0:
            continue
        consolidado[cnpj] = (
            classe.retorno_pct,
            classe.pl_fim,
            classe.cotistas_fim,
            classe.dias,
            classe.captacao_bruta - classe.resgates,
            classe.cotistas_fim - classe.cotistas_inicio,
        )
    return consolidado


def categorias_por_cnpj(config: Config) -> dict[str, str]:
    """CNPJ de classe -> categoria oficial, direto do cadastro.

    Por CNPJ, sem passar pelo resolver por nome: aqui o universo e a base
    inteira, onde toda linha ja tem CNPJ e classificacao.
    """
    caminho = config.market_dir / "mercado.csv"
    if not caminho.is_file():
        return {}
    indice: dict[str, str] = {}
    with caminho.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            cnpj = normalize_cnpj(row.get("cnpj"))
            categoria = (row.get("classificacao_anbima") or "").strip() or (
                row.get("classificacao_cvm") or ""
            ).strip()
            if cnpj and categoria:
                indice[cnpj] = categoria
    return indice


def medianas_de_referencia(
    consolidado: dict[str, tuple], categorias: dict[str, str]
) -> tuple[dict[str, float], dict[str, int]]:
    """Mediana de retorno por categoria, sobre o universo oficial inteiro."""
    por_categoria: dict[str, list[float]] = {}
    for cnpj, dados in consolidado.items():
        categoria = categorias.get(cnpj)
        if categoria:
            por_categoria.setdefault(categoria, []).append(dados[0])
    medianas = {
        categoria: statistics.median(valores)
        for categoria, valores in por_categoria.items()
        if len(valores) >= _MIN_AMOSTRA_CATEGORIA
    }
    amostras = {categoria: len(v) for categoria, v in por_categoria.items()}
    return medianas, amostras


def build_performance(
    config: Config,
    snapshots: list[ShelfSnapshot],
    meses: int = 3,
    force_download: bool = False,
) -> dict:
    """Rentabilidade ponderada da vitrine de cada casa, contra a categoria."""
    from shelf_monitor.market.etf_flow import fetch_inf_diario_month

    vitrines, fora = vitrine_snapshots(snapshots)
    cruzaveis, sem_identidade = identifiable_vitrines(vitrines)
    # O motivo generico de `identifiable_vitrines` fala em cruzar casas; aqui a
    # razao e outra e mais dura: sem CNPJ nao ha como buscar a serie de cota do
    # fundo no informe. O resolver por nome resolve CATEGORIA, nao identidade -
    # e nenhuma serie e indexada por nome.
    sem_cnpj = [
        {**item, "reason": (
            "sem CNPJ na vitrine nao ha como buscar a serie de cota no informe "
            f"diario ({item['reason']})"
        )}
        for item in sem_identidade
    ]
    if not cruzaveis:
        return {
            "available": False,
            "reason": (
                "nenhuma vitrine publica CNPJ o bastante para buscar o informe "
                "diario - sem CNPJ nao ha como ligar o fundo a serie de cota"
            ),
            "excluded": fora + sem_cnpj,
        }

    # O informe e lido para o UNIVERSO INTEIRO, nao so para a vitrine: a
    # mediana de referencia precisa do mercado, e compara-la contra os proprios
    # fundos da prateleira mediria a prateleira.
    periodos = months_back(meses)
    registros: list[DailyFlow] = []
    lidos: list[str] = []
    ausentes: list[str] = []
    for yyyymm in periodos:
        corrente = yyyymm == periodos[-1]
        caminho = fetch_inf_diario_month(
            config, yyyymm, force=force_download or corrente
        )
        parcial = read_daily_flows(caminho, None) if caminho else []
        if parcial:
            registros.extend(parcial)
            lidos.append(yyyymm)
        else:
            ausentes.append(yyyymm)

    if not registros:
        return {
            "available": False,
            "reason": (
                "nenhum informe diario disponivel para o periodo - rode com a "
                "rede liberada para dados.cvm.gov.br"
            ),
            "excluded": fora + sem_cnpj,
        }

    consolidado = _consolidar(registros)
    categorias = categorias_por_cnpj(config)
    medianas, amostras = medianas_de_referencia(consolidado, categorias)

    dias = sorted({r.dia for r in registros})
    periodo = {
        "de": dias[0].isoformat() if dias else None,
        "ate": dias[-1].isoformat() if dias else None,
        "meses_lidos": lidos,
        "meses_ausentes": ausentes,
        "pregoes": len(dias),
    }

    catalogo: dict[str, FundOffer] = {}
    presenca: dict[str, list[str]] = {}
    for platform_id, funds in cruzaveis.items():
        for fund in funds:
            if fund.cnpj:
                catalogo.setdefault(fund.cnpj, fund)
                presenca.setdefault(fund.cnpj, []).append(platform_id)

    def montar(cnpj: str) -> RetornoFundo | None:
        dados = consolidado.get(cnpj)
        if dados is None:
            return None
        retorno, pl, cotistas, pregoes, captacao, delta = dados
        oferta = catalogo.get(cnpj)
        categoria = (
            categoria_comparavel(oferta) if oferta else None
        ) or categorias.get(cnpj)
        return RetornoFundo(
            cnpj=cnpj,
            categoria=categoria,
            retorno=retorno,
            pl=pl,
            cotistas=cotistas,
            dias=pregoes,
            nome=oferta.name if oferta else "",
            captacao_liquida=captacao,
            variacao_cotistas=delta,
            vitrines=presenca.get(cnpj, []),
            mediana_categoria=medianas.get(categoria) if categoria else None,
        )

    casas: list[PerformanceCasa] = []
    sem_amostra: list[dict] = []
    for platform_id, funds in cruzaveis.items():
        cnpjs = {f.cnpj for f in funds if f.cnpj}
        fundos = [r for r in (montar(c) for c in cnpjs) if r is not None]
        casa = PerformanceCasa(
            platform_id=platform_id,
            label=_platform_label(platform_id),
            vitrine=len(funds),
            com_serie=len(fundos),
            fundos=fundos,
        )
        if casa.cobertura < _MIN_COBERTURA:
            sem_amostra.append({
                "platform_id": platform_id,
                "label": casa.label,
                "reason": (
                    f"apenas {casa.com_serie} de {casa.vitrine} fundos da vitrine "
                    "tem serie de cota no periodo - a ponderacao descreveria a "
                    "amostra que casou, nao a vitrine"
                ),
            })
            continue
        casas.append(casa)

    if not casas:
        return {
            "available": False,
            "reason": "nenhuma vitrine com cobertura suficiente de serie de cota",
            "excluded": fora + sem_cnpj + sem_amostra,
        }

    return {
        "available": True,
        "periodo": periodo,
        "participantes": [c.platform_id for c in casas],
        "excluded": fora + sem_cnpj + sem_amostra,
        "universo": {
            "fundos_com_serie": len(consolidado),
            "categorias_com_referencia": len(medianas),
            "categorias_vistas": len(amostras),
            "amostra_minima": _MIN_AMOSTRA_CATEGORIA,
        },
        "casas": [c.to_dict() for c in casas],
        "por_categoria": _por_categoria(casas),
        "onde_esta_a_gente": _onde_esta_a_gente(casas),
        "_casas": casas,
    }


def _por_categoria(casas: list[PerformanceCasa], minimo: int = 5) -> list[dict]:
    """A mesma leitura, dentro de cada categoria - onde o gap fica legivel."""
    linhas: list[dict] = []
    for casa in casas:
        agrupado: dict[str, list[RetornoFundo]] = {}
        for fundo in casa.fundos:
            if fundo.categoria:
                agrupado.setdefault(fundo.categoria, []).append(fundo)
        for categoria, fundos in agrupado.items():
            if len(fundos) < minimo:
                continue
            por_pl = _ponderada(fundos, lambda f: f.pl)
            por_cot = _ponderada(fundos, lambda f: float(f.cotistas))
            linhas.append({
                "platform_id": casa.platform_id,
                "label": casa.label,
                "categoria": categoria,
                "fundos": len(fundos),
                "mediana": round(statistics.median(f.retorno for f in fundos), 4),
                "por_pl": None if por_pl is None else round(por_pl, 4),
                "por_cotista": None if por_cot is None else round(por_cot, 4),
                "gap": (
                    None if por_pl is None or por_cot is None
                    else round(por_pl - por_cot, 4)
                ),
                "referencia": (
                    round(fundos[0].mediana_categoria, 4)
                    if fundos[0].mediana_categoria is not None else None
                ),
                "pl": round(sum(f.pl for f in fundos), 2),
                "cotistas": sum(f.cotistas for f in fundos),
            })
    linhas.sort(key=lambda linha: (linha["platform_id"], -linha["cotistas"]))
    return linhas


def _onde_esta_a_gente(casas: list[PerformanceCasa], limite: int = 25) -> dict:
    """Os fundos com mais cotistas de cada casa, e como eles rendem.

    E a tabela que responde a pergunta caso a caso: ordenada por cotistas,
    porque a duvida nao e sobre o fundo medio da prateleira, e sim sobre aquele
    em que a maior parte das pessoas esta.
    """
    return {
        casa.platform_id: [
            fundo.to_dict()
            for fundo in sorted(casa.fundos, key=lambda f: -f.cotistas)[:limite]
        ]
        for casa in casas
    }


# --- renderizacao no console --------------------------------------------------


def _pct(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:6.2f}%"


def _pp(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:+.2f}pp"


def print_performance(resultado: dict) -> None:
    """Imprime as tres leituras por casa, com quem ficou de fora e por que."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print()
    console.print(
        "[bold]Rentabilidade da vitrine[/] - o fundo mediano, o real medio e o cotista medio"
    )
    if not resultado.get("available"):
        console.print(f"   [yellow]{resultado.get('reason')}[/]")
        for e in resultado.get("excluded", []):
            console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
        return

    periodo = resultado["periodo"]
    universo = resultado["universo"]
    console.print(
        f"   [dim]{periodo['de']} a {periodo['ate']} ({periodo['pregoes']} pregoes). "
        f"Referencia: mediana da categoria no universo oficial "
        f"({universo['fundos_com_serie']} fundos, "
        f"{universo['categorias_com_referencia']} categorias com amostra "
        f">= {universo['amostra_minima']}).[/]"
    )

    table = Table(title="Retorno do periodo, por casa", title_style="bold")
    table.add_column("Casa")
    table.add_column("Fundos", justify="right")
    table.add_column("Mediana", justify="right")
    table.add_column("Por PL", justify="right")
    table.add_column("Por cotista", justify="right")
    table.add_column("Gap", justify="right")
    table.add_column("Abaixo da mediana da categoria", justify="right")
    for casa in resultado["casas"]:
        massa = casa["alocacao_abaixo_da_mediana"]
        abaixo = (
            f"{massa['fundos']}/{massa['de']} fundos ({(massa['share_fundos'] or 0):.0%})"
            f" carregam {(massa['share_pl'] or 0):.0%} do PL"
            f" - indice {massa['indice_pl']:.2f}"
            if massa.get("indice_pl") is not None else "-"
        )
        table.add_row(
            casa["label"],
            str(casa["com_serie"]),
            _pct(casa["mediana"]),
            _pct(casa["por_pl"]),
            _pct(casa["por_cotista"]),
            _pp(casa["gap"]),
            abaixo,
        )
    console.print(table)
    console.print(
        "   [dim]Retorno dos fundos que a casa exibe, com o peso que eles tem no "
        "mercado - nao o retorno dos clientes dela. A CVM publica cota, PL e "
        "cotistas por fundo, somando todos os distribuidores.[/]"
    )
    console.print(
        "   [dim]Indice abaixo de 1 = o dinheiro EVITA os fundos piores da "
        "categoria; acima de 1 = se concentra neles. Cerca de metade de qualquer "
        "universo fica abaixo da mediana por construcao, entao o share de fundos "
        "sozinho nao diz nada.[/]"
    )
    for e in resultado.get("excluded", []):
        console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
