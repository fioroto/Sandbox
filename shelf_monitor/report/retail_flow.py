"""Captacao dos fundos que estao na vitrine de cada casa.

O que esta analise pode e o que nao pode dizer - a distincao mais importante do
modulo, e a que separa um numero util de um numero errado:

**A CVM publica fluxo por FUNDO, nunca por distribuidor.** O informe diario traz
`CAPTC_DIA` e `RESG_DIA` do fundo inteiro, somando todas as casas que o
distribuem. Entao "a vitrine do BTG captou R$ X" NAO significa que entrou R$ X
pelo BTG: significa que os fundos que ele exibe captaram R$ X no mercado todo. Um
fundo exibido por tres casas entra inteiro nas tres somas.

Por isso toda visao agregada sai rotulada como *fluxo dos fundos da vitrine*, e
existe um corte paralelo - `exclusivos` - restrito aos fundos que aparecem em uma
unica vitrine, onde a atribuicao e inequivoca. Os dois numeros juntos respondem
perguntas diferentes: o agregado diz em que a casa apostou espaco; o exclusivo
diz o que so ela leva.

Cobertura medida em 2026-09-20 contra `inf_diario_fi_202608`: XP 573 de 622
CNPJs de vitrine (92%), BTG 617 de 649 (95%), Inter 368 de 463 (79%). O Itau fica
fora - a lista de rentabilidade dele publica CNPJ em 27 de 458 fundos, e sem CNPJ
nao ha como buscar o informe. O Nu tambem: nao tem vitrine deslogada.

**Duas janelas.** A de 3 meses (competencias M-2 a M) e a visao de medio prazo
de sempre. A semanal - pedida em 14/10/2026, porque o digest e semanal - e a
semana de referencia de `shared/semanas.py` contra as 12 anteriores, por dia
util: a captacao da semana e o ritmo dela (acelerando, desacelerando...), pela
mesma regra e a mesma faixa do ritmo mensal. As duas terminam no ultimo dia
completo do informe: o dia que ainda esta chegando fica fora de todas as somas.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from shelf_monitor.caixa import sem_caixa
from shelf_monitor.config import Config
from shelf_monitor.models import (
    BLOCO_CNPJ,
    BLOCO_COTISTAS,
    BLOCO_PRODUTO_PROPRIO,
    FundOffer,
    ShelfSnapshot,
)
from shelf_monitor.normalize import normalize_cnpj
from shelf_monitor.previdencia_fora import sem_previdencia
from shelf_monitor.report.aggregate import (
    _offer_manager,
    _platform_label,
    is_own_product,
)
from shelf_monitor.report.competitive import (
    coarse_category,
    identifiable_vitrines,
    vitrine_snapshots,
)
from shelf_monitor.report.shared.informe import ler_filtrado, ler_filtrado_sem_guardar
from shelf_monitor.report.shared.limiares import MIN_COBERTURA_VITRINE
from shelf_monitor.report.shared.semanas import RecorteSemanal, Semana, recorte_semanal
from shelf_monitor.texto import periodo_br


# Abaixo disto a casa nao entra nos rankings: com um punhado de fundos casados, a
# soma descreve a amostra que casou, nao a vitrine.
_MIN_MATCH_RATIO = MIN_COBERTURA_VITRINE

# Um fundo com PL irrisorio produz percentual de captacao instavel (R$ 10 mil
# sobre R$ 50 mil e +20%, e nao e noticia). Fica nos totais, fora do ranking.
_MIN_PL_RANKING = 1_000_000.0

# Quando o fluxo supera este multiplo do PL final, o dinheiro nao se acumulou no
# fundo: passou por ele. Medido em 2026-09-20 no periodo 07 a 09/2026, e o que o
# limiar pega:
#
#   WHG WM Global LB FIF CIC Acoes  +R$ 2.464 MM com PL de R$ 1.897 MM, -6 cotistas
#   WHG GLOBAL LB FIC FIA           -R$ 2.594 MM com PL de R$   178 MM, -186 cotistas
#   Kapitalo K10 FIC FIM            -R$   773 MM com PL de R$     5 MM, -9.714 cotistas
#
# Os dois primeiros sao o mesmo dinheiro trocando de veiculo dentro da mesma
# casa; o terceiro e um fundo sendo esvaziado. Nenhum dos tres e demanda de
# varejo, e colocar o primeiro na grade da XP nao capturaria fluxo nenhum -
# seria copiar uma reestruturacao societaria.
_STRUCTURAL_FLOW_TO_PL = 1.0

# Resgate que leva o PL a menos disto do que era: fundo em encerramento ou
# migracao, nao perda de competitividade.
_WIND_DOWN_RATIO = 0.2


def _norm(cnpj: str | None) -> str:
    """CNPJ do informe na mesma largura da vitrine.

    Usa `normalize_cnpj` em vez de so tirar pontuacao porque o `alvo` de
    `read_daily_flows` vem de `FundOffer.cnpj`, que ja passou por ela e tem 14
    digitos. Um informe que serializa o CNPJ como numero perde o zero a
    esquerda; comparar "1234567000191" com "01234567000191" falhava em silencio
    no filtro, e o fundo reaparecia como cobertura baixa da casa -
    indistinguivel de fundo que de fato nao entregou informe.
    """
    return normalize_cnpj(cnpj) or ""


def _num(raw: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


@dataclass(slots=True)
class DailyFlow:
    """Uma linha do informe diario, nos campos que interessam ao fluxo.

    `slots=True` porque esta classe e instanciada aos milhoes: o informe de
    09/2026 sozinho tem 302 mil linhas, e uma execucao le tres competencias.
    Sem slots, cada instancia carrega um `__dict__` proprio.

    Uma linha e uma SUBCLASSE, nao uma classe. O informe v4 traz `ID_SUBCLASSE`,
    e uma classe com subclasses emite uma linha por subclasse por dia, cada uma
    com cota, PL e cotistas proprios. Medido em 2026-09-20 no informe de 09/2026:
    273 dos 25.307 CNPJs (1,1%) tem mais de uma subclasse, somando R$ 183 bi, e
    um deles chega a sete. Tratar cada linha como se fosse a classe inteira pega
    o PL de uma subclasse arbitraria ao lado da captacao de todas.
    """

    cnpj: str
    dia: date
    captacao: float = 0.0
    resgates: float = 0.0
    pl: float = 0.0
    cota: float = 0.0
    cotistas: int = 0
    subclasse: str = ""


def read_daily_flows(zip_path: Path, alvo: set[str] | None) -> list[DailyFlow]:
    """Le o informe diario mensal, so para os CNPJs pedidos.

    O arquivo tem milhoes de linhas e a vitrine sao centenas de fundos: filtrar
    na leitura e o que mantem a analise rodando em segundos.

    `alvo=None` le o universo inteiro. E o que a mediana de referencia por
    categoria precisa: comparar um fundo da vitrine contra a mediana dos outros
    fundos da vitrine mediria a propria prateleira, nao o mercado.
    """
    if not zip_path or not zip_path.exists() or not zipfile.is_zipfile(zip_path):
        return []

    registros: list[DailyFlow] = []
    with zipfile.ZipFile(zip_path) as archive:
        membros = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not membros:
            return []
        bruto = archive.read(membros[0]).decode("utf-8-sig", errors="replace")

    linhas = bruto.splitlines()
    if not linhas:
        return []

    cabecalho = {c.strip().upper(): i for i, c in enumerate(linhas[0].split(";"))}

    def coluna(*nomes: str) -> int | None:
        for nome in nomes:
            if nome in cabecalho:
                return cabecalho[nome]
        return None

    # Lidos por NOME: a CVM renomeou CNPJ_FUNDO para CNPJ_FUNDO_CLASSE na v4 do
    # informe, e ler por posicao quebraria em silencio na proxima mudanca.
    col_cnpj = coluna("CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO")
    col_data = coluna("DT_COMPTC")
    col_capt = coluna("CAPTC_DIA")
    col_resg = coluna("RESG_DIA")
    col_pl = coluna("VL_PATRIM_LIQ")
    col_cota = coluna("VL_QUOTA")
    col_cotst = coluna("NR_COTST")
    col_sub = coluna("ID_SUBCLASSE")
    if col_cnpj is None or col_data is None:
        return []

    for linha in linhas[1:]:
        partes = linha.split(";")
        if len(partes) <= col_cnpj:
            continue
        cnpj = _norm(partes[col_cnpj])
        if alvo is not None and cnpj not in alvo:
            continue
        try:
            dia = datetime.strptime(partes[col_data].strip()[:10], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            continue

        def valor(indice: int | None) -> float:
            return _num(partes[indice]) if indice is not None and indice < len(partes) else 0.0

        registros.append(DailyFlow(
            cnpj=cnpj,
            dia=dia,
            captacao=valor(col_capt),
            resgates=valor(col_resg),
            pl=valor(col_pl),
            cota=valor(col_cota),
            cotistas=int(valor(col_cotst)),
            subclasse=(
                partes[col_sub].strip()
                if col_sub is not None and col_sub < len(partes) else ""
            ),
        ))
    return registros


@dataclass
class ClasseNoPeriodo:
    """As linhas de um CNPJ consolidadas no nivel da CLASSE, nao da subclasse."""

    cnpj: str
    dias: int = 0
    pl_inicio: float = 0.0
    pl_fim: float = 0.0
    cotistas_inicio: int = 0
    cotistas_fim: int = 0
    captacao_bruta: float = 0.0
    resgates: float = 0.0
    # Media dos retornos das subclasses ponderada pelo PL de cada uma. None
    # quando nenhuma subclasse tem cota utilizavel nas duas pontas.
    retorno_pct: float | None = None


def agrupar_por_classe(registros: list[DailyFlow]) -> dict[str, ClasseNoPeriodo]:
    """Consolida o informe no nivel da classe, tratando subclasse e retificacao.

    Duas coisas que uma leitura linha-a-linha erra:

    **Uma linha e uma subclasse.** Uma classe com subclasses emite uma linha por
    subclasse por dia, cada uma com cota, PL e cotistas proprios - 273 dos
    25.307 CNPJs em 09/2026 (1,1%), somando R$ 183 bi, um deles com sete
    subclasses. Ordenar as linhas do CNPJ por data e pegar a primeira e a ultima
    compara pontas de subclasses DIFERENTES.

    O sintoma aparece no proprio numero publicado: em
    `data/reports/captacao-2026-09-20.json`, o Kapitalo K10 sai com 336 "dias"
    numa janela de ~56 pregoes - sao 6 subclasses x 56 - e rentabilidade de
    -59,65%, que e a cota final de uma subclasse dividida pela inicial de outra.
    Do lado da rentabilidade ponderada, o Kinea RF Absoluto aparecia com 107,70%
    e 220 "pregoes" pelo mesmo motivo.

    Aqui PL, cotistas e fluxo SOMAM entre subclasses, e o retorno e a media dos
    retornos delas ponderada pelo PL: somar cotas seria somar precos unitarios
    de bases diferentes. `dias` passa a contar datas distintas, nao linhas.

    **A CVM retifica o informe.** O mesmo (CNPJ, subclasse, data) aparece mais de
    uma vez no arquivo do mes (1,03% dos pares em 09/2026). Sem deduplicar, duas
    versoes do mesmo dia contam como dois pregoes. Vence a ultima ocorrencia, que
    e a retificacao - o mesmo criterio do fluxo de ETF no etf-monitor.
    """
    por_subclasse: dict[tuple[str, str], dict[date, DailyFlow]] = {}
    for registro in registros:
        por_subclasse.setdefault((registro.cnpj, registro.subclasse), {})[
            registro.dia
        ] = registro

    agrupado: dict[str, list[dict[date, DailyFlow]]] = {}
    for (cnpj, _subclasse), dias in por_subclasse.items():
        agrupado.setdefault(cnpj, []).append(dias)

    saida: dict[str, ClasseNoPeriodo] = {}
    for cnpj, subclasses in agrupado.items():
        classe = ClasseNoPeriodo(cnpj=cnpj)
        observados: set[date] = set()
        retornos: list[tuple[float, float]] = []
        for dias in subclasses:
            observados |= dias.keys()
            ordenados = sorted(dias)
            primeira, ultima = dias[ordenados[0]], dias[ordenados[-1]]
            classe.pl_inicio += primeira.pl
            classe.pl_fim += ultima.pl
            classe.cotistas_inicio += primeira.cotistas
            classe.cotistas_fim += ultima.cotistas
            classe.captacao_bruta += sum(r.captacao for r in dias.values())
            classe.resgates += sum(r.resgates for r in dias.values())
            if primeira.cota > 0 and ultima.cota > 0:
                retornos.append(
                    ((ultima.cota / primeira.cota - 1.0) * 100.0, max(ultima.pl, 0.0))
                )
        classe.dias = len(observados)
        if retornos:
            peso = sum(p for _, p in retornos)
            classe.retorno_pct = (
                sum(r * p for r, p in retornos) / peso
                if peso > 0
                else sum(r for r, _ in retornos) / len(retornos)
            )
        saida[cnpj] = classe
    return saida


@dataclass
class FundFlow:
    """Fluxo consolidado de um fundo no periodo, com onde ele esta exposto."""

    cnpj: str
    nome: str
    categoria: str
    gestora: str | None
    vitrines: list[str] = field(default_factory=list)
    captacao_bruta: float = 0.0
    resgates: float = 0.0
    pl_inicio: float = 0.0
    pl_fim: float = 0.0
    cotistas_inicio: int = 0
    cotistas_fim: int = 0
    cota_inicio: float = 0.0
    cota_fim: float = 0.0
    # Retorno ja consolidado no nivel da classe (ver agrupar_por_classe). Quando
    # presente, prevalece sobre a razao das cotas - que so descreve o fundo
    # inteiro quando ele nao tem subclasses.
    retorno_pct: float | None = None
    dias: int = 0
    # Serie mensal do proprio fundo, preenchida por consolidate quando ha
    # competencias suficientes - e dela que sai a tendencia.
    meses: list = field(default_factory=list)
    # Casas em cuja vitrine o fundo e produto da gestora da propria casa
    # (`aggregate.is_own_product`), preenchido por build_retail_flow.
    proprio_de: list[str] = field(default_factory=list)
    # Serie semanal (`SemanaDoFundo`), alinhada as semanas do recorte - a de
    # referencia por ultimo. Preenchida por build_retail_flow. Fica fora do
    # `to_dict`: o JSON e a planilha levam so a semana e o ritmo dela.
    semanas: list = field(default_factory=list)

    @property
    def captacao_liquida(self) -> float:
        return self.captacao_bruta - self.resgates

    @property
    def variacao_cotistas(self) -> int:
        return self.cotistas_fim - self.cotistas_inicio

    @property
    def rentabilidade(self) -> float | None:
        if self.retorno_pct is not None:
            return self.retorno_pct
        if not self.cota_inicio:
            return None
        return (self.cota_fim / self.cota_inicio - 1.0) * 100.0

    @property
    def captacao_sobre_pl(self) -> float | None:
        """Captacao liquida como fracao do PL inicial - o que compara fundos
        de tamanhos diferentes sem deixar o maior ganhar sempre."""
        if not self.pl_inicio:
            return None
        return self.captacao_liquida / self.pl_inicio * 100.0

    @property
    def exclusivo(self) -> bool:
        return len(self.vitrines) == 1

    @property
    def tendencia(self) -> str:
        return classify_trend(self.meses)

    @property
    def captacao_semana(self) -> float | None:
        """Captacao liquida da semana de referencia; None sem informe nela."""
        semana = self.semanas[-1] if self.semanas else None
        return semana.captacao_liquida if semana and semana.dias else None

    @property
    def ritmo_semanal(self) -> str:
        return ritmo_da_semana(self.semanas)

    @property
    def estrutural(self) -> bool:
        """O fluxo e reestruturacao, nao demanda (ver _STRUCTURAL_FLOW_TO_PL)."""
        if self.pl_fim > 0 and abs(self.captacao_liquida) > _STRUCTURAL_FLOW_TO_PL * self.pl_fim:
            return True
        if (
            self.captacao_liquida < 0
            and self.pl_inicio > 0
            and self.pl_fim < _WIND_DOWN_RATIO * self.pl_inicio
        ):
            return True
        return False

    @property
    def ticket_por_cotista(self) -> float | None:
        """Quanto entrou por cotista ganho - a leitura de varejo do fluxo.

        Um fundo que capta bilhoes perdendo cotistas recebeu dinheiro
        institucional ou de uma migracao; um que capta acompanhado de milhares de
        cotistas novos recebeu varejo. A pergunta do monitor e a segunda.
        """
        if self.variacao_cotistas <= 0 or self.captacao_liquida <= 0:
            return None
        return self.captacao_liquida / self.variacao_cotistas

    @property
    def varejo_entrada(self) -> bool:
        """Captou dinheiro E cotistas, sem ser movimento estrutural."""
        return (
            not self.estrutural
            and self.captacao_liquida > 0
            and self.variacao_cotistas > 0
        )

    @property
    def varejo_saida(self) -> bool:
        """Perdeu dinheiro E cotistas, sem ser encerramento."""
        return (
            not self.estrutural
            and self.captacao_liquida < 0
            and self.variacao_cotistas < 0
        )

    def entrou(self, com_cotistas: bool = True) -> bool:
        """Captou - com cotistas novos quando cotista mede gente (`varejo_entrada`).

        Em previdencia o cotista do FIE e a seguradora: exigir cotista novo
        esvaziava a lista (em 24/09, 1 fundo em "maiores captadores" e nenhum
        em "maiores perdas"). La o criterio e so o dinheiro.
        """
        if com_cotistas:
            return self.varejo_entrada
        return not self.estrutural and self.captacao_liquida > 0

    def saiu(self, com_cotistas: bool = True) -> bool:
        """Perdeu - com saida de cotistas quando cotista mede gente (`varejo_saida`)."""
        if com_cotistas:
            return self.varejo_saida
        return not self.estrutural and self.captacao_liquida < 0

    def to_dict(self) -> dict:
        return {
            "cnpj": self.cnpj,
            "nome": self.nome,
            "categoria": self.categoria,
            "gestora": self.gestora,
            "vitrines": self.vitrines,
            "captacao_bruta": round(self.captacao_bruta, 2),
            "resgates": round(self.resgates, 2),
            "captacao_liquida": round(self.captacao_liquida, 2),
            "pl_inicio": round(self.pl_inicio, 2),
            "pl_fim": round(self.pl_fim, 2),
            "variacao_cotistas": self.variacao_cotistas,
            "cotistas_inicio": self.cotistas_inicio,
            "cotistas_fim": self.cotistas_fim,
            "rentabilidade": (
                None if self.rentabilidade is None else round(self.rentabilidade, 2)
            ),
            "captacao_sobre_pl": (
                None if self.captacao_sobre_pl is None
                else round(self.captacao_sobre_pl, 2)
            ),
            "exclusivo": self.exclusivo,
            "tendencia": self.tendencia,
            "captacao_semana": (
                None if self.captacao_semana is None else round(self.captacao_semana, 2)
            ),
            "ritmo_semanal": self.ritmo_semanal,
            "meses": [m.to_dict() for m in self.meses],
            "estrutural": self.estrutural,
            "proprio_de": self.proprio_de,
            "varejo_entrada": self.varejo_entrada,
            "varejo_saida": self.varejo_saida,
            "ticket_por_cotista": (
                None if self.ticket_por_cotista is None
                else round(self.ticket_por_cotista, 2)
            ),
            "dias": self.dias,
        }


def consolidate(
    registros: list[DailyFlow],
    catalogo: dict[str, FundOffer],
    presenca: dict[str, list[str]],
) -> list[FundFlow]:
    """Soma o fluxo de cada fundo no periodo, com a serie mensal e a vitrine."""
    series = monthly_by_fund(registros)
    classes = agrupar_por_classe(registros)

    fluxos: list[FundFlow] = []
    for cnpj, classe in classes.items():
        oferta = catalogo.get(cnpj)
        if oferta is None:
            continue

        fluxo = FundFlow(
            cnpj=cnpj,
            nome=oferta.name,
            categoria=coarse_category(oferta),
            gestora=_offer_manager(oferta),
            vitrines=presenca.get(cnpj, []),
            captacao_bruta=classe.captacao_bruta,
            resgates=classe.resgates,
            pl_inicio=classe.pl_inicio,
            pl_fim=classe.pl_fim,
            cotistas_inicio=classe.cotistas_inicio,
            cotistas_fim=classe.cotistas_fim,
            retorno_pct=classe.retorno_pct,
            dias=classe.dias,
            meses=series.get(cnpj, []),
        )
        fluxos.append(fluxo)
    return fluxos


@dataclass
class PlatformFlow:
    """O fluxo dos fundos que uma casa exibe."""

    platform_id: str
    label: str
    vitrine: int
    com_informe: int
    captacao_bruta: float = 0.0
    resgates: float = 0.0
    pl_fim: float = 0.0
    exclusivos: int = 0
    captacao_exclusivos: float = 0.0
    # Fora das somas acima, mostrados a parte (ver _STRUCTURAL_FLOW_TO_PL).
    estruturais: int = 0
    captacao_estrutural: float = 0.0
    # A composicao do saldo. Sem ela o numero da casa nao dizia de onde vinha:
    # medido em 24/09 (07 a 09/2026), a vitrine da XP perdia R$ 1,3 bi somando
    # +R$ 1,5 bi nos fundos que divide com outra casa e -R$ 2,8 bi nos que so
    # ela exibe; e o saldo de +R$ 3,0 bi do BTG vinha do produto proprio (+R$
    # 3,3 bi), com os fundos de terceiros da vitrine dele perdendo dinheiro.
    pl_inicio: float = 0.0
    compartilhados: int = 0
    captacao_compartilhados: float = 0.0
    proprios: int = 0
    captacao_propria: float = 0.0
    terceiros: int = 0
    captacao_terceiros: float = 0.0
    # Fundo sem gestor conhecido nao e proprio nem de terceiros: fica fora das
    # duas colunas, contado aqui (ver `is_own_product`, tri-estado).
    sem_gestora: int = 0

    @property
    def captacao_liquida(self) -> float:
        return self.captacao_bruta - self.resgates

    @property
    def cobertura(self) -> float:
        return self.com_informe / self.vitrine if self.vitrine else 0.0

    @property
    def sobre_pl(self) -> float | None:
        """Captacao liquida em % do PL de inicio - o saldo sem o efeito de escala.

        R$ 1 bi numa vitrine de R$ 380 bi e outra coisa que numa de R$ 160 bi;
        em reais, a maior vitrine ganha ou perde sempre mais.
        """
        if not self.pl_inicio:
            return None
        return self.captacao_liquida / self.pl_inicio * 100.0

    def to_dict(self) -> dict:
        return {
            "platform_id": self.platform_id,
            "label": self.label,
            "vitrine": self.vitrine,
            "com_informe": self.com_informe,
            "cobertura": round(self.cobertura, 3),
            "captacao_bruta": round(self.captacao_bruta, 2),
            "resgates": round(self.resgates, 2),
            "captacao_liquida": round(self.captacao_liquida, 2),
            "pl_fim": round(self.pl_fim, 2),
            "exclusivos": self.exclusivos,
            "captacao_exclusivos": round(self.captacao_exclusivos, 2),
            "estruturais": self.estruturais,
            "captacao_estrutural": round(self.captacao_estrutural, 2),
            "pl_inicio": round(self.pl_inicio, 2),
            "sobre_pl": None if self.sobre_pl is None else round(self.sobre_pl, 3),
            "compartilhados": self.compartilhados,
            "captacao_compartilhados": round(self.captacao_compartilhados, 2),
            "proprios": self.proprios,
            "captacao_propria": round(self.captacao_propria, 2),
            "terceiros": self.terceiros,
            "captacao_terceiros": round(self.captacao_terceiros, 2),
            "sem_gestora": self.sem_gestora,
        }


def months_back(quantidade: int, referencia: date | None = None) -> list[str]:
    """Sufixos YYYYMM dos ultimos meses, do mais antigo para o mais novo."""
    referencia = referencia or date.today()
    ano, mes = referencia.year, referencia.month
    meses: list[str] = []
    for _ in range(quantidade):
        meses.append(f"{ano:04d}{mes:02d}")
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
    return list(reversed(meses))


# Quantos meses antes da janela a serie semanal recua, no maximo, atras da base
# de 12 semanas: com o informe atrasado, a semana de referencia recua junto.
_MAX_MESES_ANTES_DA_JANELA = 2


def _mes_anterior(yyyymm: str) -> str:
    ano, mes = int(yyyymm[:4]), int(yyyymm[4:])
    return f"{ano - 1:04d}12" if mes == 1 else f"{ano:04d}{mes - 1:02d}"


def contagem_por_dia(registros: list[DailyFlow]) -> dict[date, int]:
    """Quantos fundos reportaram em cada dia - o insumo da regra do dia completo."""
    por_dia: dict[date, set[str]] = {}
    for registro in registros:
        por_dia.setdefault(registro.dia, set()).add(registro.cnpj)
    return {dia: len(cnpjs) for dia, cnpjs in por_dia.items()}


def _recorte_da_janela(
    config: Config, primeiro_mes: str, alvo: set[str], registros: list[DailyFlow]
) -> tuple[list[DailyFlow], RecorteSemanal | None]:
    """Os registros dos meses antes da janela, e o recorte semanal sobre tudo.

    Le o mes anterior a janela e, se a comparacao de 12 semanas ainda comecar
    antes dele (informe atrasado, ou a execucao nos primeiros dias do mes), mais
    um. Sem guardar no cache do processo: so a serie semanal os usa.
    """
    anteriores: list[DailyFlow] = []
    recorte = recorte_semanal(contagem_por_dia(registros))
    mes = primeiro_mes
    for _ in range(_MAX_MESES_ANTES_DA_JANELA):
        if recorte is None:
            break
        inicio_lido = date(int(mes[:4]), int(mes[4:]), 1)
        if recorte.inicio_da_comparacao >= inicio_lido:
            break
        mes = _mes_anterior(mes)
        anteriores = ler_filtrado_sem_guardar(config, mes, alvo) + anteriores
        recorte = recorte_semanal(contagem_por_dia(anteriores + registros))
    return anteriores, recorte


def build_retail_flow(
    config: Config,
    snapshots: list[ShelfSnapshot],
    meses: int = 3,
    baseline_id: str = "xp",
    force_download: bool = False,
) -> dict:
    """Captacao dos fundos de vitrine, por casa e por fundo."""

    # Onde o modulo suprime cotistas (previdencia), as listas ranqueiam so por
    # dinheiro - ver FundFlow.entrou.
    com_cotistas = not dict(config.blocos_suprimidos).get(BLOCO_COTISTAS)
    vitrines, fora = vitrine_snapshots(snapshots)
    cruzaveis, sem_cnpj = identifiable_vitrines(vitrines)
    if not cruzaveis:
        return {
            "available": False,
            "reason": (
                "nenhuma vitrine publica CNPJ o bastante para buscar o informe "
                "diário - sem CNPJ não há como ligar o fundo ao fluxo"
            ),
            "excluded": fora + sem_cnpj,
        }

    # Fundo caixa/simples fica fora (pedido de 16/10/2026): produto padrao de
    # caixa, que enchia o topo das captacoes (ver shelf_monitor.caixa).
    cruzaveis, caixa_fora = sem_caixa(cruzaveis)
    # E o de previdencia, que tem o monitor proprio (17/10/2026, ver
    # shelf_monitor.previdencia_fora); o monitor de previdencia o mantem.
    cruzaveis, previdencia_fora = sem_previdencia(cruzaveis, config)

    catalogo: dict[str, FundOffer] = {}
    presenca: dict[str, list[str]] = {}
    for platform_id, funds in cruzaveis.items():
        for fund in funds:
            if not fund.cnpj:
                continue
            # O primeiro a registrar define nome e gestora: o merge ja
            # normalizou ambos, e a casa que publica mais campos costuma vir
            # antes na ordem canonica.
            catalogo.setdefault(fund.cnpj, fund)
            presenca.setdefault(fund.cnpj, []).append(platform_id)

    alvo = set(catalogo)
    periodos = months_back(meses)
    registros: list[DailyFlow] = []
    meses_lidos: list[str] = []
    meses_ausentes: list[str] = []
    for yyyymm in periodos:
        # O mes corrente muda todo dia: baixar de novo e o que mantem a leitura
        # com os pregoes mais recentes em vez do arquivo que sobrou no cache.
        # O anterior e revalidado: a CVM ainda o regrava, e a semana de
        # referencia pode cruzar a virada do mes. O `force` vale para a
        # PRIMEIRA leitura da competencia nesta execucao; `performance` e
        # `rentabilidade`, que vem depois, reaproveitam.
        corrente = yyyymm == periodos[-1]
        anterior = len(periodos) > 1 and yyyymm == periodos[-2]
        lidos = ler_filtrado(
            config, yyyymm, alvo, force=force_download or corrente,
            revalidar=anterior,
        )
        if lidos:
            registros.extend(lidos)
            meses_lidos.append(yyyymm)
        else:
            meses_ausentes.append(yyyymm)

    if not registros:
        return {
            "available": False,
            "reason": (
                "nenhum informe diário disponível para o período - rode com a "
                "rede liberada para dados.cvm.gov.br"
            ),
            "excluded": fora + sem_cnpj,
        }

    # A semana de referencia e as 12 anteriores pedem o mes antes da janela
    # (as vezes dois): lidos sem guardar no cache do processo, porque so a
    # serie semanal os usa.
    anteriores, recorte = _recorte_da_janela(config, periodos[0], alvo, registros)
    if recorte is not None:
        # O dia que ainda esta chegando (e o feriado com linha avulsa) sai de
        # todas as somas: a janela de 3 meses termina no ultimo dia completo,
        # como a semana.
        validos = set(recorte.dias.completos)
        aparados = [r for r in registros if r.dia in validos]
        if aparados:
            registros = aparados
            anteriores = [r for r in anteriores if r.dia in validos]
        else:
            recorte = None

    fluxos = consolidate(registros, catalogo, presenca)
    por_cnpj = {f.cnpj: f for f in fluxos}
    semanas = recorte.todas() if recorte is not None else []
    if semanas:
        por_semana = weekly_by_fund(anteriores + registros, semanas)
        for fluxo in fluxos:
            fluxo.semanas = por_semana.get(fluxo.cnpj, [])

    # Produto proprio e relativo a casa: o fundo da XP Asset e proprio na
    # vitrine da XP e de terceiros na do BTG. Tri-estado, como `is_own_product`.
    proprio: dict[tuple[str, str], bool | None] = {}
    for platform_id, funds in cruzaveis.items():
        for fund in funds:
            if fund.cnpj:
                proprio[(platform_id, fund.cnpj)] = is_own_product(platform_id, fund)
    for fluxo in fluxos:
        fluxo.proprio_de = [
            pid for pid in fluxo.vitrines if proprio.get((pid, fluxo.cnpj)) is True
        ]

    casas: list[PlatformFlow] = []
    sem_amostra: list[dict] = []
    for platform_id, funds in cruzaveis.items():
        cnpjs = {f.cnpj for f in funds if f.cnpj}
        casados = [por_cnpj[c] for c in cnpjs if c in por_cnpj]
        # Movimento estrutural fica fora da soma da casa: um unico veiculo
        # trocando de casco movia o total da vitrine em bilhoes e enterrava o
        # sinal de varejo, que e o que a analise procura.
        organicos = [f for f in casados if not f.estrutural]
        casa = PlatformFlow(
            platform_id=platform_id,
            label=_platform_label(platform_id),
            vitrine=len(funds),
            com_informe=len(casados),
            captacao_bruta=sum(f.captacao_bruta for f in organicos),
            resgates=sum(f.resgates for f in organicos),
            pl_fim=sum(f.pl_fim for f in casados),
            exclusivos=sum(1 for f in organicos if f.exclusivo),
            captacao_exclusivos=sum(
                f.captacao_liquida for f in organicos if f.exclusivo
            ),
            estruturais=len(casados) - len(organicos),
            captacao_estrutural=sum(f.captacao_liquida for f in casados if f.estrutural),
            pl_inicio=sum(f.pl_inicio for f in organicos),
            compartilhados=sum(1 for f in organicos if not f.exclusivo),
            captacao_compartilhados=sum(
                f.captacao_liquida for f in organicos if not f.exclusivo
            ),
            proprios=sum(1 for f in organicos if proprio.get((platform_id, f.cnpj)) is True),
            captacao_propria=sum(
                f.captacao_liquida for f in organicos
                if proprio.get((platform_id, f.cnpj)) is True
            ),
            terceiros=sum(
                1 for f in organicos if proprio.get((platform_id, f.cnpj)) is False
            ),
            captacao_terceiros=sum(
                f.captacao_liquida for f in organicos
                if proprio.get((platform_id, f.cnpj)) is False
            ),
            sem_gestora=sum(
                1 for f in organicos if proprio.get((platform_id, f.cnpj)) is None
            ),
        )
        if casa.cobertura < _MIN_MATCH_RATIO:
            sem_amostra.append({
                "platform_id": platform_id,
                "label": casa.label,
                "reason": (
                    f"apenas {casa.com_informe} de {casa.vitrine} fundos da vitrine "
                    f"têm informe diário ({casa.cobertura:.0%}) - a soma descreveria "
                    "a amostra que casou, não a vitrine"
                ),
            })
            continue
        casas.append(casa)

    casas.sort(key=lambda c: -c.captacao_liquida)

    ranqueaveis = [f for f in fluxos if f.pl_inicio >= _MIN_PL_RANKING]
    estruturais = sorted(
        [f for f in ranqueaveis if f.estrutural],
        key=lambda f: -abs(f.captacao_liquida),
    )
    captadores = sorted(
        [f for f in ranqueaveis if f.entrou(com_cotistas)],
        key=lambda f: -f.captacao_liquida,
    )
    perdedores = sorted(
        [f for f in ranqueaveis if f.saiu(com_cotistas)],
        key=lambda f: f.captacao_liquida,
    )

    estruturais_cnpj = {f.cnpj for f in fluxos if f.estrutural}
    serie_casas = monthly_by_platform(registros, presenca, estruturais_cnpj)
    competencias = sorted({m.competencia for serie in serie_casas.values() for m in serie})

    # Resumo de tendencia so sobre os fundos de varejo ranqueaveis: contar
    # tendencia de fundo institucional ou de veiculo em migracao diria mais sobre
    # o mercado de atacado do que sobre a prateleira.
    varejo = [f for f in ranqueaveis if not f.estrutural]
    resumo_tendencia: dict[str, int] = {}
    for fluxo in varejo:
        resumo_tendencia[fluxo.tendencia] = resumo_tendencia.get(fluxo.tendencia, 0) + 1
    ids_casas = [c.platform_id for c in casas]
    base_cnpjs = {f.cnpj for f in cruzaveis.get(baseline_id, []) if f.cnpj}

    return {
        "available": True,
        "competencias": competencias,
        "serie_por_casa": {
            pid: [m.to_dict() for m in serie] for pid, serie in serie_casas.items()
        },
        "tendencias": dict(
            sorted(resumo_tendencia.items(), key=lambda kv: -kv[1])
        ),
        "tendencia_labels": TREND_LABELS,
        # A contagem global descreve o mercado (em 24/09, 696 de ~1.000 fundos
        # perdiam dinheiro nos dois periodos); por casa ela compara vitrines.
        "tendencia_por_casa": tendencia_por_casa(varejo, ids_casas),
        "por_categoria": fluxo_por_categoria(
            [f for f in fluxos if not f.estrutural], ids_casas
        ),
        # O que a vitrine da baseline exibe e esta perdendo dinheiro e cotistas:
        # a lista de venda ao lado da de compra (a grade). Mora aqui, e nao so
        # na grade, porque vale mesmo onde a grade por CNPJ nao se aplica.
        "baseline_perdendo": [
            f.to_dict()
            for f in perdendo_na_vitrine(fluxos, base_cnpjs, com_cotistas)[:12]
        ],
        # O criterio das listas: com cotistas (fundos) ou so dinheiro (previdencia).
        "com_cotistas": com_cotistas,
        "acelerando": [
            f.to_dict() for f in sorted(
                [f for f in varejo if f.tendencia == "acelerando"],
                key=lambda f: -f.captacao_liquida,
            )[:12]
        ],
        "virou_saida": [
            f.to_dict() for f in sorted(
                [f for f in varejo if f.tendencia == "virou_saida"],
                key=lambda f: f.captacao_liquida,
            )[:12]
        ],
        "periodo": {
            "meses_lidos": meses_lidos,
            "meses_ausentes": meses_ausentes,
            "de": min(r.dia for r in registros).isoformat(),
            "ate": max(r.dia for r in registros).isoformat(),
            # Os dias do fim do informe que ainda estavam chegando: fora das somas.
            "dias_incompletos": recorte.incompletos_dict() if recorte else [],
        },
        # A semana de referencia, a comparacao (12 semanas) e o que ficou de
        # fora - o formato de `shared.semanas.RecorteSemanal.to_dict`. None sem
        # dia completo no informe.
        "recorte_semanal": recorte.to_dict() if recorte else None,
        "_recorte": recorte,
        "fundos_analisados": len(fluxos),
        # Fundos caixa/simples tirados da analise (ver shelf_monitor.caixa).
        "caixa_fora": caixa_fora,
        # Fundos de previdencia tirados da analise (ver shelf_monitor.previdencia_fora).
        "previdencia_fora": previdencia_fora,
        "baseline_id": baseline_id,
        "casas": [c.to_dict() for c in casas],
        # A lista INTEIRA, para a planilha. Os rankings abaixo continuam porque
        # o digest e o dashboard sao superficies de leitura rapida; a planilha e
        # onde alguem vai conferir o fundo especifico que o fez abrir o arquivo.
        "fundos": [f.to_dict() for f in sorted(
            fluxos, key=lambda f: -f.captacao_liquida
        )],
        "top_captacao": [f.to_dict() for f in captadores[:15]],
        "top_resgate": [f.to_dict() for f in perdedores[:15]],
        "estruturais": [f.to_dict() for f in estruturais[:8]],
        "total_estruturais": len(estruturais),
        "captacao_varejo_total": round(
            sum(f.captacao_liquida for f in ranqueaveis if not f.estrutural), 2
        ),
        "excluded": fora + sem_cnpj + sem_amostra,
        "_fluxos": fluxos,
        "_casas": casas,
    }


@dataclass
class GradeOpportunity:
    """Um fundo captando forte que a vitrine da XP nao exibe."""

    fluxo: FundFlow
    gestora_ja_distribuida: bool
    # O fundo e da asset da propria casa que o exibe. Nao e oportunidade
    # transferivel: a XP distribuir o produto proprio do BTG e outra conversa,
    # comercial e estrategica, nao uma inclusao de grade.
    #
    # None quando o fundo nao tem gestor conhecido em nenhuma vitrine em que
    # aparece: e "nao verificado", nao "nao e". Continua na lista - o monitor
    # informa e nao remove -, mas sai contado a parte.
    produto_do_concorrente: bool | None = False

    @property
    def gestora_desconhecida(self) -> bool:
        return self.produto_do_concorrente is None

    def to_dict(self) -> dict:
        dados = self.fluxo.to_dict()
        dados["gestora_ja_distribuida"] = self.gestora_ja_distribuida
        dados["produto_do_concorrente"] = self.produto_do_concorrente
        dados["gestora_desconhecida"] = self.gestora_desconhecida
        return dados


def build_grade_opportunities(
    snapshots: list[ShelfSnapshot],
    fluxos: list[FundFlow],
    baseline_id: str = "xp",
    cobertos: set[str] | None = None,
) -> dict:
    """Fundos que captam nas concorrentes e estao fora da vitrine da XP.

    O corte e por captacao liquida no periodo, nao por presenca: um fundo em duas
    vitrines que esta perdendo dinheiro nao e oportunidade de grade, e sim um
    produto que o mercado esta deixando - e a analise de consenso por presenca,
    sozinha, nao distinguia os dois.

    `cobertos` sao os CNPJs que a vitrine da XP cobre por espelho ou
    equivalente (`aggregate.cnpjs_cobertos`): a XP ja oferta a estrategia, entao
    nao sao oportunidade - a mesma regra do bloco de gaps. Saem contados em
    `total_cobertos`.
    """
    vitrines, _ = vitrine_snapshots(snapshots)
    cruzaveis, _ = identifiable_vitrines(vitrines)
    if baseline_id not in cruzaveis:
        return {
            "available": False,
            "reason": f"vitrine da {_platform_label(baseline_id)} indisponível ou sem CNPJ",
        }

    base = cruzaveis[baseline_id]
    base_cnpjs = {f.cnpj for f in base if f.cnpj}
    base_gestoras = {g for g in (_offer_manager(f) for f in base) if g}

    # `varejo_entrada` e o filtro: exige que o dinheiro tenha vindo acompanhado
    # de cotistas novos. Sem ele, o topo da lista era uma reestruturacao de
    # veiculo - copiar aquilo para a grade da XP nao traria fluxo nenhum.
    cobertos = cobertos or set()
    fora_da_vitrine = [
        f for f in fluxos
        if f.cnpj not in base_cnpjs
        and f.varejo_entrada
        and f.pl_inicio >= _MIN_PL_RANKING
    ]
    candidatos = [f for f in fora_da_vitrine if f.cnpj not in cobertos]
    total_cobertos = len(fora_da_vitrine) - len(candidatos)
    candidatos.sort(key=lambda f: -f.captacao_liquida)

    catalogo_ofertas: dict[str, FundOffer] = {}
    for platform_id, funds in cruzaveis.items():
        for fund in funds:
            if fund.cnpj:
                catalogo_ofertas.setdefault(fund.cnpj, fund)

    # Um fundo e "produto do concorrente" quando a gestora dele e a asset de
    # alguma casa em cuja vitrine ele aparece - a mesma definicao de casa propria
    # que o resto do monitor usa (watchlist do radar CVM).
    #
    # `is_own_product` e tri-estado: None quando o fundo nao tem gestor
    # conhecido. Um `any()` direto colapsava None em False e o fundo entrava na
    # lista de transferiveis como se estivesse verificado - a soma publicada
    # ("restam N fundos, +R$ X MM") afirmava sobre fundo que ninguem checou.
    def do_concorrente(f: FundFlow) -> bool | None:
        oferta = catalogo_ofertas.get(f.cnpj)
        if oferta is None:
            return None
        respostas = [is_own_product(pid, oferta) for pid in f.vitrines]
        if any(r is True for r in respostas):
            return True
        if all(r is None for r in respostas):
            return None
        return False

    oportunidades = [
        GradeOpportunity(
            fluxo=f,
            gestora_ja_distribuida=bool(f.gestora and f.gestora in base_gestoras),
            produto_do_concorrente=do_concorrente(f),
        )
        for f in candidatos
    ]

    transferiveis = [o for o in oportunidades if o.produto_do_concorrente is not True]
    operacionais = [o for o in transferiveis if o.gestora_ja_distribuida]
    comerciais = [o for o in transferiveis if not o.gestora_ja_distribuida]
    # Intensidade de varejo: quem trouxe MAIS COTISTAS, nao mais reais. Um fundo
    # que capta R$ 300 MM de 11 mil cotistas e outra coisa que um que capta os
    # mesmos R$ 300 MM de 245 - e a grade de varejo quer o primeiro.
    mais_varejo = sorted(
        [o for o in transferiveis if o.fluxo.variacao_cotistas > 0],
        key=lambda o: -o.fluxo.variacao_cotistas,
    )

    # A contrapartida: o que a XP exibe e esta perdendo dinheiro. Sem isso a
    # leitura vira uma lista de compras sem nenhuma de venda.
    na_vitrine_perdendo = perdendo_na_vitrine(fluxos, base_cnpjs)

    return {
        "available": bool(transferiveis),
        "reason": (
            None if transferiveis
            else "nenhum fundo transferível fora da vitrine da XP com captação de varejo no período"
        ),
        "total": len(oportunidades),
        # Captando fora da vitrine, mas a XP ja cobre a estrategia por espelho
        # ou equivalente: fora da lista, contados aqui.
        "total_cobertos": total_cobertos,
        "total_transferiveis": len(transferiveis),
        "total_produto_do_concorrente": len(oportunidades) - len(transferiveis),
        # Dentro dos transferiveis: quantos nao puderam ser verificados porque a
        # fonte nao publica gestor. Nao saem da lista; saem ditos.
        "total_gestora_desconhecida": sum(
            1 for o in transferiveis if o.gestora_desconhecida
        ),
        "total_operacionais": len(operacionais),
        "total_comerciais": len(comerciais),
        "captacao_somada": round(sum(o.fluxo.captacao_liquida for o in oportunidades), 2),
        "captacao_transferivel": round(
            sum(o.fluxo.captacao_liquida for o in transferiveis), 2
        ),
        "cotistas_transferivel": sum(o.fluxo.variacao_cotistas for o in transferiveis),
        "operacionais": [o.to_dict() for o in operacionais[:12]],
        "comerciais": [o.to_dict() for o in comerciais[:12]],
        "mais_varejo": [o.to_dict() for o in mais_varejo[:12]],
        "produto_do_concorrente": [
            o.to_dict() for o in oportunidades if o.produto_do_concorrente
        ][:8],
        "na_vitrine_perdendo": [f.to_dict() for f in na_vitrine_perdendo[:12]],
        "_oportunidades": oportunidades,
        "_transferiveis": transferiveis,
    }


def perdendo_na_vitrine(
    fluxos: list[FundFlow], cnpjs: set[str], com_cotistas: bool = True
) -> list[FundFlow]:
    """Fundos da vitrine perdendo dinheiro (e cotistas, quando eles medem gente)."""
    return sorted(
        [
            f for f in fluxos
            if f.cnpj in cnpjs and f.saiu(com_cotistas) and f.pl_inicio >= _MIN_PL_RANKING
        ],
        key=lambda f: f.captacao_liquida,
    )


def fluxo_por_categoria(organicos: list[FundFlow], casas: list[str]) -> list[dict]:
    """Captacao por familia de ativo, em cada vitrine e no universo das vitrines.

    E o corte que diz de onde vem a diferenca entre as casas. A categoria fina
    (36 categorias ANBIMA em 24/09) e dominada por fundos isolados; a familia
    (Renda Fixa, Multimercados, Acoes...) e estavel. O universo conta cada fundo
    uma vez, mesmo exibido por varias casas.
    """
    linhas: dict[str, dict] = {}
    for fluxo in organicos:
        linha = linhas.setdefault(fluxo.categoria, {
            "categoria": fluxo.categoria,
            "universo": {"fundos": 0, "captacao_liquida": 0.0},
            "casas": {pid: {"fundos": 0, "captacao_liquida": 0.0} for pid in casas},
        })
        linha["universo"]["fundos"] += 1
        linha["universo"]["captacao_liquida"] += fluxo.captacao_liquida
        for pid in fluxo.vitrines:
            if pid in linha["casas"]:
                linha["casas"][pid]["fundos"] += 1
                linha["casas"][pid]["captacao_liquida"] += fluxo.captacao_liquida
    for linha in linhas.values():
        linha["universo"]["captacao_liquida"] = round(linha["universo"]["captacao_liquida"], 2)
        for valores in linha["casas"].values():
            valores["captacao_liquida"] = round(valores["captacao_liquida"], 2)
    return sorted(linhas.values(), key=lambda l: -l["universo"]["captacao_liquida"])


def tendencia_por_casa(varejo: list[FundFlow], casas: list[str]) -> dict[str, dict[str, int]]:
    """Quantos fundos de cada vitrine estao em cada tendencia."""
    saida: dict[str, dict[str, int]] = {pid: {} for pid in casas}
    for fluxo in varejo:
        for pid in fluxo.vitrines:
            if pid in saida:
                saida[pid][fluxo.tendencia] = saida[pid].get(fluxo.tendencia, 0) + 1
    return saida


# --- leitura -------------------------------------------------------------------

# Participacao minima de uma familia na diferenca entre duas casas para entrar
# na frase: abaixo disto ela e ruido ao lado das que explicam o numero.
_PARCELA_RELEVANTE = 0.10


def _reais(valor: float) -> str:
    """"+R$ 1,3 bi", "-R$ 690 mi" - com sinal, na unidade em que o fluxo se le."""
    sinal = "+" if valor > 0 else "-" if valor < 0 else ""
    absoluto = abs(valor)
    if absoluto >= 1e9:
        texto = f"{absoluto / 1e9:,.1f} bi"
    elif absoluto >= 1e7:
        texto = f"{absoluto / 1e6:,.0f} mi"
    else:
        texto = f"{absoluto / 1e6:,.1f} mi"
    return f"{sinal}R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(valor: float | None) -> str:
    if valor is None:
        return "sem PL de início"
    return f"{valor:+.2f}%".replace(".", ",")


def leitura_da_captacao(flow: dict, suprimidos: dict | None = None) -> list[str]:
    """As frases que abrem o bloco - calculadas aqui para toda superficie dizer igual.

    Compara a baseline com a vitrine que mais captou (ou, se a baseline lidera,
    com a segunda): o saldo em reais e em % do PL, a composicao compartilhado x
    so na casa, de que familia vem a diferenca e o peso do produto proprio.
    Com BLOCO_CNPJ em `suprimidos` (previdencia), sai a frase de composicao, que
    separa os fundos por CNPJ; com BLOCO_PRODUTO_PROPRIO, a de produto proprio.
    """
    if not (flow and flow.get("available")):
        return []
    casas = {c["platform_id"]: c for c in flow.get("casas") or []}
    base = casas.get(flow.get("baseline_id"))
    if base is None:
        return []
    outras = sorted(
        (c for pid, c in casas.items() if pid != base["platform_id"]),
        key=lambda c: -c["captacao_liquida"],
    )
    # O prazo na propria frase: ela abre o bloco em todas as superficies, e
    # "no periodo" obrigava o leitor a procurar qual periodo era.
    periodo = flow.get("periodo") or {}
    prazo = (
        periodo_br(periodo["de"], periodo["ate"])
        if periodo.get("de") and periodo.get("ate") else "no período"
    )
    if not outras:
        return [
            f"Captação líquida dos fundos da vitrine {prazo}: {base['label']} "
            f"{_reais(base['captacao_liquida'])} ({_pct(base.get('sobre_pl'))} do PL de "
            "início)."
        ]
    ref = outras[0]
    lider = base if base["captacao_liquida"] >= ref["captacao_liquida"] else ref
    frases = [
        f"Captação líquida dos fundos da vitrine {prazo}: {base['label']} "
        f"{_reais(base['captacao_liquida'])} ({_pct(base.get('sobre_pl'))} do PL de "
        f"início), {ref['label']} {_reais(ref['captacao_liquida'])} "
        f"({_pct(ref.get('sobre_pl'))}). Maior captação entre as casas: "
        f"{lider['label']}."
    ]

    if not (suprimidos or {}).get(BLOCO_CNPJ):
        frases.append(
            "Fundos que outra casa também exibe: "
            f"{base['label']} {_reais(base.get('captacao_compartilhados', 0.0))}, "
            f"{ref['label']} {_reais(ref.get('captacao_compartilhados', 0.0))}. "
            "Fundos que só a própria casa exibe: "
            f"{base['label']} {_reais(base.get('captacao_exclusivos', 0.0))}, "
            f"{ref['label']} {_reais(ref.get('captacao_exclusivos', 0.0))}."
        )

    diferencas = []
    for linha in flow.get("por_categoria") or []:
        valores = linha["casas"]
        if base["platform_id"] in valores and ref["platform_id"] in valores:
            diferencas.append((
                linha["categoria"],
                valores[base["platform_id"]]["captacao_liquida"]
                - valores[ref["platform_id"]]["captacao_liquida"],
            ))
    total = base["captacao_liquida"] - ref["captacao_liquida"]
    if diferencas and abs(total) >= 1e7:
        mesmo_lado = sorted(
            (d for d in diferencas
             if d[1] * total > 0 and abs(d[1]) >= _PARCELA_RELEVANTE * abs(total)),
            key=lambda d: -abs(d[1]),
        )[:2]
        contra = sorted(
            (d for d in diferencas
             if d[1] * total < 0 and abs(d[1]) >= _PARCELA_RELEVANTE * abs(total)),
            key=lambda d: -abs(d[1]),
        )[:1]
        if mesmo_lado:
            frase = (
                f"Por família de ativo, a diferença entre {base['label']} e "
                f"{ref['label']} ({_reais(total)}) vem de "
                + " e ".join(f"{cat} ({_reais(v)})" for cat, v in mesmo_lado)
            )
            if contra:
                cat, v = contra[0]
                frase += f"; {cat} vai no sentido contrário ({_reais(v)})"
            frases.append(frase + ".")

    sem_proprio = (suprimidos or {}).get(BLOCO_PRODUTO_PROPRIO)
    if not sem_proprio and (base.get("proprios") or ref.get("proprios")):
        frases.append(
            "Fundos da gestora da própria casa: "
            f"{base['label']} {_reais(base.get('captacao_propria', 0.0))}, "
            f"{ref['label']} {_reais(ref.get('captacao_propria', 0.0))}. "
            "De terceiros: "
            f"{base['label']} {_reais(base.get('captacao_terceiros', 0.0))}, "
            f"{ref['label']} {_reais(ref.get('captacao_terceiros', 0.0))}."
        )
    return frases


# --- tendencia ----------------------------------------------------------------

# Classificar tendencia com uma competencia so nao e tendencia, e um ponto.
_MIN_MESES_TENDENCIA = 2

# Quanto a media diaria do ultimo mes precisa se afastar da media dos anteriores
# para virar aceleracao ou desaceleracao, em vez de ruido de calendario.
_TREND_BAND = 0.25


@dataclass
class MonthlyFlow:
    """Fluxo de um fundo numa competencia, com os dias efetivamente observados."""

    competencia: str
    captacao_liquida: float = 0.0
    variacao_cotistas: int = 0
    pl_fim: float = 0.0
    dias: int = 0

    @property
    def media_diaria(self) -> float:
        """Captacao por dia observado.

        Existe por causa do mes corrente: em 2026-09-20 o informe cobre ate o
        dia 18, entao setembro tem ~13 pregoes contra ~22 de agosto. Comparar os
        totais brutos marcaria TODO fundo como desacelerando - o que seria uma
        propriedade do calendario, nao do fundo.
        """
        return self.captacao_liquida / self.dias if self.dias else 0.0

    def to_dict(self) -> dict:
        return {
            "competencia": self.competencia,
            "captacao_liquida": round(self.captacao_liquida, 2),
            "variacao_cotistas": self.variacao_cotistas,
            "pl_fim": round(self.pl_fim, 2),
            "dias": self.dias,
            "media_diaria": round(self.media_diaria, 2),
        }


# Os rotulos de tendencia e o que cada um afirma. Nomeados pelo que o dinheiro
# fez, nao por julgamento ("bom"/"ruim"): um fundo de liquidez que desacelera a
# captacao pode estar exatamente onde a casa quer.
TREND_LABELS = {
    "acelerando": "captando mais por dia que nos meses anteriores",
    "desacelerando": "ainda captando, mas menos por dia que antes",
    "virou_entrada": "estava perdendo dinheiro e passou a captar",
    "virou_saida": "estava captando e passou a perder dinheiro",
    "saida_persistente": "perdendo dinheiro no último mês e na média anterior",
    "estavel": "sem mudança material no ritmo",
    "sem_serie": "competências de menos para falar em tendência",
}


# Nome de cada tendencia para exibicao. A chave e dado (vai ao JSON e ao
# dashboard como identificador); o leitor ve o nome, com a grafia correta.
TREND_NOMES = {
    "acelerando": "acelerando",
    "desacelerando": "desacelerando",
    "virou_entrada": "virou entrada",
    "virou_saida": "virou saída",
    "saida_persistente": "saída persistente",
    "estavel": "estável",
    "sem_serie": "sem série",
}


def nome_da_tendencia(chave: str | None) -> str:
    if not chave:
        return ""
    return TREND_NOMES.get(chave, chave.replace("_", " "))


def classify_trend(meses: list[MonthlyFlow]) -> str:
    """Compara a media diaria do ultimo mes com a dos anteriores."""
    if len(meses) < _MIN_MESES_TENDENCIA:
        return "sem_serie"

    ordenados = sorted(meses, key=lambda m: m.competencia)
    ultimo = ordenados[-1]
    anteriores = ordenados[:-1]
    dias_anteriores = sum(m.dias for m in anteriores)
    if not dias_anteriores or not ultimo.dias:
        return "sem_serie"

    base = sum(m.captacao_liquida for m in anteriores) / dias_anteriores
    return classificar_ritmo(ultimo.media_diaria, base)


def classificar_ritmo(agora: float, base: float) -> str:
    """O ritmo de `agora` contra `base`, as duas em captacao por dia.

    A regra do ritmo mensal (o ultimo mes contra os anteriores) e do semanal
    (a semana de referencia contra as 12 anteriores): mesma ordem, mesma faixa.
    """
    if base <= 0 and agora > 0:
        return "virou_entrada"
    if base > 0 and agora < 0:
        return "virou_saida"
    if base <= 0 and agora <= 0:
        return "saida_persistente"

    # Ambos positivos: e questao de ritmo.
    if agora > base * (1 + _TREND_BAND):
        return "acelerando"
    if agora < base * (1 - _TREND_BAND):
        return "desacelerando"
    return "estavel"


# --- semana ---------------------------------------------------------------------

# A base do ritmo semanal pede ao menos isto de semanas com informe: com menos,
# "a media das anteriores" e uma ou duas semanas, e o rotulo seria ruido.
_MIN_SEMANAS_DE_BASE = 4


@dataclass
class SemanaDoFundo:
    """Fluxo de um fundo numa semana do recorte, com os dias observados."""

    segunda: date
    captacao_liquida: float = 0.0
    dias: int = 0

    @property
    def media_diaria(self) -> float:
        """Captacao por dia observado - a semana com feriado nao parece fraca."""
        return self.captacao_liquida / self.dias if self.dias else 0.0


def weekly_by_fund(
    registros: list[DailyFlow], semanas: list[Semana]
) -> dict[str, list[SemanaDoFundo]]:
    """Quebra o fluxo de cada fundo pelas semanas do recorte, alinhado a elas.

    Cada semana passa por `agrupar_por_classe`, pelo mesmo motivo da serie
    mensal: e a consolidacao por classe que deduplica a retificacao da CVM e
    soma as subclasses. So contam os dias completos de cada semana; semana sem
    linha do fundo sai zerada, com 0 dias.
    """
    semana_do_dia = {dia: semana.segunda for semana in semanas for dia in semana.dias}
    por_fundo: dict[str, dict[date, list[DailyFlow]]] = {}
    for registro in registros:
        segunda = semana_do_dia.get(registro.dia)
        if segunda is not None:
            por_fundo.setdefault(registro.cnpj, {}).setdefault(segunda, []).append(registro)

    saida: dict[str, list[SemanaDoFundo]] = {}
    for cnpj, por_semana in por_fundo.items():
        serie: list[SemanaDoFundo] = []
        for semana in semanas:
            linhas = por_semana.get(semana.segunda)
            if not linhas:
                serie.append(SemanaDoFundo(semana.segunda))
                continue
            classe = agrupar_por_classe(linhas)[cnpj]
            serie.append(SemanaDoFundo(
                semana.segunda, classe.captacao_bruta - classe.resgates, classe.dias,
            ))
        saida[cnpj] = serie
    return saida


def ritmo_da_semana(semanas: list[SemanaDoFundo]) -> str:
    """A captacao por dia da semana de referencia contra a das anteriores."""
    if not semanas:
        return "sem_serie"
    atual = semanas[-1]
    anteriores = [s for s in semanas[:-1] if s.dias]
    dias_da_base = sum(s.dias for s in anteriores)
    if not atual.dias or len(anteriores) < _MIN_SEMANAS_DE_BASE or not dias_da_base:
        return "sem_serie"
    base = sum(s.captacao_liquida for s in anteriores) / dias_da_base
    return classificar_ritmo(atual.media_diaria, base)


def monthly_by_fund(registros: list[DailyFlow]) -> dict[str, list[MonthlyFlow]]:
    """Quebra o fluxo de cada fundo por competencia."""
    por_fundo: dict[str, dict[str, list[DailyFlow]]] = {}
    for registro in registros:
        competencia = f"{registro.dia.year:04d}{registro.dia.month:02d}"
        por_fundo.setdefault(registro.cnpj, {}).setdefault(competencia, []).append(registro)

    saida: dict[str, list[MonthlyFlow]] = {}
    for cnpj, meses in por_fundo.items():
        serie: list[MonthlyFlow] = []
        for competencia, linhas in sorted(meses.items()):
            # Pelo mesmo motivo de `agrupar_por_classe`: PL e cotistas de uma
            # linha sao os da subclasse, e a variacao do mes tem de somar todas.
            classe = agrupar_por_classe(linhas)[cnpj]
            serie.append(MonthlyFlow(
                competencia=competencia,
                captacao_liquida=classe.captacao_bruta - classe.resgates,
                variacao_cotistas=classe.cotistas_fim - classe.cotistas_inicio,
                pl_fim=classe.pl_fim,
                dias=classe.dias,
            ))
        saida[cnpj] = serie
    return saida


def monthly_by_platform(
    registros: list[DailyFlow],
    presenca: dict[str, list[str]],
    estruturais: set[str],
) -> dict[str, list[MonthlyFlow]]:
    """Fluxo mensal dos fundos de cada vitrine, sem os movimentos estruturais."""
    por_casa: dict[str, dict[str, list[DailyFlow]]] = {}
    for registro in registros:
        if registro.cnpj in estruturais:
            continue
        competencia = f"{registro.dia.year:04d}{registro.dia.month:02d}"
        for platform_id in presenca.get(registro.cnpj, []):
            por_casa.setdefault(platform_id, {}).setdefault(competencia, []).append(registro)

    saida: dict[str, list[MonthlyFlow]] = {}
    for platform_id, meses in por_casa.items():
        serie: list[MonthlyFlow] = []
        for competencia, linhas in sorted(meses.items()):
            # Pelo mesmo motivo de `monthly_by_fund`: a consolidacao por classe
            # e quem deduplica a retificacao da CVM e soma o PL das subclasses.
            # Somar as linhas cruas contava a retificacao duas vezes na
            # captacao, e o PL vinha de UMA subclasse arbitraria entre as
            # empatadas no ultimo dia - nao-deterministico e subestimado. A
            # serie por casa do dashboard nao fechava com o total da mesma casa
            # em `PlatformFlow`, que ja passava por aqui.
            classes = agrupar_por_classe(linhas)
            serie.append(MonthlyFlow(
                competencia=competencia,
                captacao_liquida=sum(
                    c.captacao_bruta - c.resgates for c in classes.values()
                ),
                variacao_cotistas=sum(
                    c.cotistas_fim - c.cotistas_inicio for c in classes.values()
                ),
                pl_fim=sum(c.pl_fim for c in classes.values()),
                dias=len({r.dia for r in linhas}),
            ))
        saida[platform_id] = serie
    return saida


# --- renderizacao no console --------------------------------------------------


def _mm(valor: float) -> str:
    """Reais em milhoes, com sinal - a unidade em que fluxo de fundo se le.

    Milhar com ponto e decimal com virgula, como no digest. O `.replace(",",
    ".")` que havia aqui trocava so o milhar e deixava o ponto decimal: saia
    "+2.962.3 MM" no console para R$ 2.962,3 milhoes.
    """
    texto = f"{valor / 1_000_000:+,.1f} MM"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _cot(valor: int) -> str:
    return f"{valor:+,}".replace(",", ".")


def _ticket(fluxo_dict: dict) -> str:
    valor = fluxo_dict.get("ticket_por_cotista")
    if not valor:
        return "-"
    return f"{valor / 1000:,.0f}k".replace(",", ".")


def print_retail_flow(resultado: dict, oportunidades: dict) -> None:
    """Imprime a leitura de captacao de varejo e as oportunidades de grade."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    if not resultado["available"]:
        console.print(f"[yellow]{resultado['reason']}[/]")
        return

    periodo = resultado["periodo"]
    console.print()
    console.print(
        f"[bold]Captação dos fundos de vitrine[/] - {periodo_br(periodo['de'], periodo['ate'])} "
        f"({len(periodo['meses_lidos'])} meses, {resultado['fundos_analisados']} fundos)"
    )
    console.print(
        "[dim]A CVM publica fluxo por FUNDO, nunca por distribuidor: os números por "
        "casa são o fluxo dos fundos que ela exibe, no mercado inteiro - não o que "
        "entrou por ela. A coluna Só nesta vitrine é onde a atribuição é inequívoca.[/]"
    )

    for frase in leitura_da_captacao(resultado):
        console.print(f"  - {frase}")

    def _cor(valor: float) -> str:
        return f"[{'green' if valor > 0 else 'red'}]{_mm(valor)}[/]"

    tabela = Table(title="De onde vem o saldo de cada vitrine", title_style="bold")
    for titulo in ("Casa", "Com informe", "Captação líquida", "% do PL", "Compartilhados",
                   "Só nesta vitrine", "Produto próprio", "Terceiros", "Estrutural (fora)"):
        tabela.add_column(titulo, justify="left" if titulo == "Casa" else "right")
    for casa in resultado["casas"]:
        tabela.add_row(
            casa["label"],
            f"{casa['com_informe']} de {casa['vitrine']} ({casa['cobertura']:.0%})",
            _cor(casa["captacao_liquida"]),
            _pct(casa.get("sobre_pl")),
            _cor(casa.get("captacao_compartilhados", 0.0)),
            _cor(casa["captacao_exclusivos"]),
            _cor(casa.get("captacao_propria", 0.0)),
            _cor(casa.get("captacao_terceiros", 0.0)),
            f"{casa['estruturais']} / {_mm(casa['captacao_estrutural'])}",
        )
    console.print(tabela)

    categorias = resultado.get("por_categoria") or []
    if categorias:
        familias = Table(title="Por família de ativo", title_style="bold")
        familias.add_column("Família")
        for casa in resultado["casas"]:
            familias.add_column(casa["label"], justify="right")
        familias.add_column("Todas as vitrines", justify="right")
        for linha in categorias:
            familias.add_row(
                linha["categoria"],
                *[
                    _cor(linha["casas"][c["platform_id"]]["captacao_liquida"])
                    if linha["casas"].get(c["platform_id"], {}).get("fundos") else "-"
                    for c in resultado["casas"]
                ],
                f"{_cor(linha['universo']['captacao_liquida'])} "
                f"({linha['universo']['fundos']})",
            )
        console.print(familias)

    for excluida in resultado.get("excluded", []):
        console.print(f"   [dim]fora: {excluida['label']} - {excluida['reason']}[/]")

    if resultado.get("estruturais"):
        console.print(
            f"   [yellow]{resultado['total_estruturais']} fundos com movimento "
            "estrutural[/] (fluxo maior que o próprio PL, ou fundo esvaziado) ficam "
            "fora das somas: é dinheiro trocando de veículo, não demanda. Maiores: "
            + "; ".join(
                f"{f['nome'][:32]} ({_mm(f['captacao_liquida'])})"
                for f in resultado["estruturais"][:3]
            )
        )

    def _ranking(titulo: str, linhas: list[dict], estilo: str) -> None:
        if not linhas:
            return
        tabela = Table(title=titulo, title_style=f"bold {estilo}")
        tabela.add_column("Fundo")
        tabela.add_column("Classe")
        tabela.add_column("Captação líquida", justify="right")
        tabela.add_column("Cotistas", justify="right")
        tabela.add_column("Por cotista novo", justify="right")
        tabela.add_column("Vitrines")
        for f in linhas[:10]:
            tabela.add_row(
                f["nome"][:42],
                f["categoria"][:14],
                f"[{estilo}]{_mm(f['captacao_liquida'])}[/]",
                _cot(f["variacao_cotistas"]),
                _ticket(f),
                ", ".join(_platform_label(p) for p in f["vitrines"]),
            )
        console.print(tabela)

    criterio = "dinheiro e cotistas" if resultado.get("com_cotistas", True) else "dinheiro"
    _ranking(f"Maiores captações - fundos ganhando {criterio}", resultado["top_captacao"], "green")
    _ranking(f"Maiores resgates - fundos perdendo {criterio}", resultado["top_resgate"], "red")
    _ranking(
        f"Na vitrine da {_platform_label(resultado.get('baseline_id') or 'xp')}, "
        f"perdendo {criterio}",
        resultado.get("baseline_perdendo") or [], "red",
    )

    # --- oportunidades ---
    console.print()
    console.print("[bold]Oportunidades de grade para a XP[/]")
    if not oportunidades.get("available"):
        console.print(f"   [yellow]{oportunidades.get('reason')}[/]")
        return

    console.print(
        f"   {oportunidades['total']} fundos captam varejo fora da vitrine da XP "
        f"({_mm(oportunidades['captacao_somada'])}), mas "
        f"[bold]{oportunidades['total_produto_do_concorrente']}[/] deles são produto "
        "da própria casa que os exibe e não se transferem."
    )
    console.print(
        f"   [bold]Transferíveis: {oportunidades['total_transferiveis']} fundos, "
        f"{_mm(oportunidades['captacao_transferivel'])} e "
        f"{_cot(oportunidades['cotistas_transferivel'])} cotistas[/] - "
        f"{oportunidades['total_operacionais']} de gestora que a XP já distribui, "
        f"{oportunidades['total_comerciais']} exigem abrir gestora."
    )

    _ranking(
        "Mais varejo: quem trouxe mais COTISTAS (transferíveis)",
        oportunidades["mais_varejo"], "green",
    )
    _ranking(
        "Inclusão operacional: gestora já distribuída pela XP",
        oportunidades["operacionais"], "green",
    )
    _ranking(
        "Exige abrir gestora",
        oportunidades["comerciais"], "yellow",
    )
