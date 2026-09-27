"""Captacao liquida mensal dos ETFs, pelo informe diario do Fundos.NET.

Como sai o numero
-----------------

O informe diario de cada ETF no Fundos.NET traz, dia a dia, a captacao e o
resgate declarados pelo administrador, o PL e a cota; a pagina de um informe
traz o mes inteiro do fundo (`etf_sources.collect_informes_do_mes`). A
captacao liquida do mes e a soma, dia a dia, de

    captacao declarada - resgate declarado

conferida contra a diferenca de patrimonio do dia sem a valorizacao, que e a
variacao de cotas: (cotas do dia - cotas da vespera) x cota do dia, com cotas
= PL / cota. Quando o declarado nao fecha com ela (folga de 5% ou R$ 1 mil),
vale a diferenca de PL - a quantidade de cotas so muda quando o agente
autorizado cria ou resgata cotas. Medido em 2026-09-25 nos informes de 10 a
18/09/2026: o declarado fechou com a variacao de cotas em 74 de 84 dias com
movimento. Nos outros 10, o Itau Asset declarou zero com as cotas mudando em
4 (BOVV11: 600 mil cotas a menos, -R$ 115 mi, em 14/09), a Grao em 1 (R$ 7,6
mi) e cinco ETFs de renda com opcoes da Buena Vista declararam resgate com as
cotas paradas, todos no mesmo dia - o padrao de pagamento de rendimento. Dia
sem PL entra na conta do dia seguinte, onde o dinheiro dele aparece nas
cotas. Dia de desdobramento ou grupamento
(cota que dobra ou cai a menos da metade de um dia para o outro) fica com o
declarado; o primeiro dia de um ETF que estreia confere contra o PL do dia.

Cada mes sai com a captacao e o resgate declarados, o ajuste pela diferenca de
PL (liquida - (captacao - resgates)) e a diferenca de PL do mes (PL no
fechamento - PL no fechamento anterior), que carrega tambem a valorizacao.

Sem o informe do mes em disco, vale a conta so com os fechamentos (abaixo).

Por que cotas, e nao o balancete
--------------------------------

O capital de cotas do balancete de FIE parecia dar o fluxo, mas cada
administrador contabiliza de um jeito. Medido em 2026-09-24, captacao de
08/2026 pela variacao de cotas (Fundos.NET) contra a variacao do capital
(balancete): concordam no BNP (59 de 63 ETFs), no BTG (48 de 55), no BB (10 de
10), no Bradesco (12 de 14) e na Genial (8 de 9); na Vortx, em 1 de 13. No QR
Bitcoin o capital subiu R$ 112,7 mi num mes em que as cotas cairam (R$ -3,8
mi): o capital absorveu a alta do bitcoin. No ETF Bradesco IMA-B o capital
ficou parado e as cotas cairam R$ 82,7 mi - o resgate que derrubou o PL em 5%
num mes de alta do IMA-B entrou nas receitas. A quantidade de cotas so muda
quando o agente autorizado cria ou resgata cotas, e isso e a captacao;
valorizacao e distribuicao mexem na cota, nao nela.

Sem o informe do mes: os fechamentos
------------------------------------

Fechamento do mes: o ultimo informe diario do Fundos.NET de cada ETF com
data-base ate o fim do mes (`etf_sources.collect_fechamentos_mensais`). O PL
dele e o do balancete da CVM na mesma data em 178 de 179 ETFs (31/07/2026), e
a contagem de ETFs com fechamento bate com a do balancete (219 de 219 em
08/2026). Com cotas = PL / cota, a captacao do mes e

    (cotas no fechamento - cotas no fechamento anterior) x cota media

com a cota media simples das duas pontas. A aproximacao erra no valor, nunca
no sinal: num mes de alta de 28% (bitcoin, 08/2026) o valor pode sair ate ~14%
acima ou abaixo, conforme o dia em que o dinheiro entrou; em renda fixa o erro
e desprezivel.

Casos de borda - a `origem` de cada mes diz qual valeu:

- estreia: sem fechamento anterior e sem PL antes (nem no balancete, nem em
  fechamento mais antigo). A captacao e o PL do fechamento;
- desdobramento ou grupamento: a cota dobrou, ou caiu a menos da metade, num
  mes. O fator (a cota antiga dividida por k) e o inteiro que deixa o retorno
  da cota mais perto da mediana dos pares do segmento no mes; sem par, com o
  melhor fator a mais de 5 pp dos pares ou com outro fator quase tao perto, o
  mes fica sem dado. Medido em
  2026-09-24: tres desdobramentos em 12 meses, como o do IT NOW S&P500 TRN em
  01/2026 (cota de 411,29 para 49,97; cotas de 3,42 mi para 30,56 mi);
- sem informe: o ETF existia, mas falta um dos fechamentos. Com o informe do
  mes em disco vale o declarado (o primeiro dia sem vespera para conferir);
  sem ele, fica sem dado;
- encerramento: so para ETF em liquidacao no cadastro, sem PL no mes. A
  captacao e menos o PL anterior. ETF cancelado nem entra no catalogo, e a
  saida do mes em que foi liquidado fica fora da conta. Um ETF em
  funcionamento que some dos informes nao e dado como encerrado: o BTG Pactual
  Teva Dividendos Ativos Reais Listados zerou no balancete de 07/2026 com o
  cadastro ainda dizendo "Em Funcionamento Normal", com PL de R$ 11,8 mi.

Estados do mes (de um ETF, de uma gestora, de um segmento ou do mercado)
------------------------------------------------------------------------

    acelerando         captou mais que no mes anterior, que tambem captou
    captando           captou
    virou saida        saiu dinheiro depois de um mes sem saida
    saida persistente  saiu dinheiro pelo segundo mes seguido, ou mais
    sem movimento      captacao abaixo de 0,1% do PL do inicio do mes (e nunca
                       menos de R$ 100 mil), para mais ou para menos
    sem dado           o ETF existia, mas falta o fechamento
    fora               antes da estreia: o mes nao entra (sai em branco)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.etf_competicao import (
    casa_da_gestora,
    curto,
    pct,
    reais,
    rotulo_da_gestora,
    segmento_do_etf,
)
from shelf_monitor.market.fundosnet import InformeDiarioEtf, ajustes_do_informe, read_informes_csv
from shelf_monitor.normalize import normalize_text
from shelf_monitor.texto import competencia_br, data_br

# Doze meses na visao, mais o mes anterior ao primeiro (o estado depende do mes
# anterior), mais o fechamento que abre esse mes: 14 fechamentos.
MESES_DA_VISAO = 12
FECHAMENTOS = MESES_DA_VISAO + 2
# Dias depois do fim do mes ate o fechamento ser dado como completo.
PRAZO_DE_ENTREGA = 7
# Sem movimento: abaixo de 0,1% do PL do inicio do mes, com piso de R$ 100 mil.
# So o piso fazia "captando" dos +R$ 0,6 mi liquidos do BTG em 08/2026 - uma
# gestora de R$ 21 bi em que um resgate de R$ 1,33 bi anulou as entradas.
FLUXO_MINIMO = 100_000.0
MATERIALIDADE = 0.001
# Cota que dobra ou cai a menos da metade num mes: desdobramento ou grupamento.
# A folga do declarado contra a variacao de cotas de cada dia mora em
# `fundosnet` (FOLGA_DO_DIA), com a conta do dia, que o fluxo semanal tambem usa.
_EVENTO_NA_COTA = 2.0
_FATORES = tuple(range(2, 101)) + tuple(1 / k for k in range(2, 101))
# O fator so vale se deixar a cota a ate 5 pp dos pares e o segundo melhor
# ficar ao menos 5 pp mais longe: com a cota caindo a 12% da anterior, os
# fatores inteiros ficam a 12 pp de retorno um do outro, e uma tolerancia larga
# casaria qualquer coisa.
_TOLERANCIA_DO_FATOR = 0.05
_FOLGA_DO_FATOR = 0.05
# Oportunidade: segmento que captou ao menos isto em 12 meses e onde a casa em
# foco ficou com menos de 10% da captacao (os mesmos cortes do bloco de PL).
_CAPTACAO_OPORTUNIDADE = 500_000_000.0
_SHARE_PEQUENA = 0.10

ROTULOS = {
    "acelerando": "acelerando",
    "captando": "captando",
    "sem_movimento": "sem movimento",
    "virou_saida": "virou saída",
    "saida_persistente": "saída persistente",
    "sem_dado": "sem dado",
    "fora": "antes da estreia",
}
SIMBOLOS = {
    "acelerando": "▲▲",
    "captando": "▲",
    "sem_movimento": "–",
    "virou_saida": "▼",
    "saida_persistente": "▼▼",
    "sem_dado": "?",
    "fora": "",
}
ORDEM_DA_LEGENDA = (
    "acelerando", "captando", "sem_movimento", "virou_saida", "saida_persistente", "sem_dado",
)
ORIGENS = {
    "estreia": "estreia",
    "desdobramento": "desdobramento de cotas",
    "grupamento": "grupamento de cotas",
    "evento_na_cota": "desdobramento ou grupamento sem fator",
    "sem_informe": "sem informe",
    "encerramento": "encerramento",
    "fechamentos": "sem o informe diário do mês: variação de cotas entre fechamentos",
}
# Origens de mes comum, que nao viram observacao.
_ORIGENS_COMUNS = ("cotas", "informe")
_EM_SAIDA = ("virou_saida", "saida_persistente")
_CAPTANDO = ("acelerando", "captando")


# --- meses ------------------------------------------------------------------


def fim_do_mes(competencia: str) -> date:
    """"202608" -> 31/08/2026."""
    ano, mes = int(competencia[:4]), int(competencia[4:6])
    proximo = date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)
    return proximo - timedelta(days=1)


def meses_fechados(hoje: date, quantos: int = FECHAMENTOS) -> list[str]:
    """Os `quantos` meses mais recentes com a entrega do fechamento encerrada,
    do mais antigo ao mais novo."""
    meses: list[str] = []
    ano, mes = hoje.year, hoje.month
    while len(meses) < quantos:
        ano, mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
        competencia = f"{ano}{mes:02d}"
        if fim_do_mes(competencia) + timedelta(days=PRAZO_DE_ENTREGA) < hoje:
            meses.append(competencia)
    return sorted(meses)


def caminho_do_fechamento(config: Config, competencia: str) -> Path:
    return config.etf_fnet_mensal_dir / f"{competencia}.csv"


def caminho_do_informe_do_mes(config: Config, competencia: str) -> Path:
    return config.etf_fnet_diario_dir / f"{competencia}.csv"


def caminho_do_informe_parcial(config: Config, competencia: str) -> Path:
    """O mes que ainda nao fechou: refeito a cada dia, nunca vira o do mes fechado."""
    return config.etf_fnet_diario_dir / f"{competencia}-parcial.csv"


def ler_fechamentos(config: Config, meses: list[str]) -> dict[str, dict[str, InformeDiarioEtf]]:
    """Por competencia em disco: o fechamento de cada ETF, com PL e cota."""
    fechamentos: dict[str, dict[str, InformeDiarioEtf]] = {}
    for mes in meses:
        path = caminho_do_fechamento(config, mes)
        if path.exists():
            fechamentos[mes] = {
                i.cnpj: i for i in read_informes_csv(path) if i.cnpj and i.pl and i.cota
            }
    return fechamentos


def ler_informes_do_mes(config: Config, meses: list[str]) -> dict[str, dict[str, list[InformeDiarioEtf]]]:
    """Por competencia em disco: os dias do informe de cada ETF, em ordem."""
    informes: dict[str, dict[str, list[InformeDiarioEtf]]] = {}
    for mes in meses:
        path = caminho_do_informe_do_mes(config, mes)
        if not path.exists():
            continue
        por_etf: dict[str, list[InformeDiarioEtf]] = {}
        for i in read_informes_csv(path):
            if i.cnpj and i.data:
                por_etf.setdefault(i.cnpj, []).append(i)
        informes[mes] = {c: sorted(dias, key=lambda i: i.data) for c, dias in por_etf.items()}
    return informes


# --- fluxo de um ETF --------------------------------------------------------


@dataclass
class FluxoDoMes:
    """Captacao de um ETF num mes, e de onde ela saiu."""

    competencia: str
    fluxo: float | None
    # informe | cotas | fechamentos | estreia | desdobramento | grupamento |
    # evento_na_cota | sem_informe | encerramento
    origem: str
    pl_fim: float | None = None
    data_inicio: date | None = None
    data_fim: date | None = None
    pl_inicio: float | None = None
    cota_inicio: float | None = None
    cota_fim: float | None = None
    fator: float | None = None
    # Do informe diario do mes (None sem ele): a soma do declarado, os dias com
    # informe e os dias em que valeu a diferenca de PL.
    captacao: float | None = None
    resgates: float | None = None
    dias: int = 0
    dias_pelo_pl: int = 0
    fonte: str = "fechamentos"  # informe | fechamentos

    @property
    def retorno_da_cota(self) -> float | None:
        if self.origem not in (*_ORIGENS_COMUNS, "fechamentos") or not self.cota_inicio or not self.cota_fim:
            return None
        return self.cota_fim / self.cota_inicio - 1

    @property
    def ajuste(self) -> float | None:
        """O que a liquida tem alem do declarado: os dias em que valeu a
        diferenca de PL. So existe com o informe do mes."""
        if self.fluxo is None or self.fonte != "informe":
            return None
        return self.fluxo - (self.captacao or 0.0) + (self.resgates or 0.0)

    @property
    def sem_informe(self) -> float | None:
        """A liquida do mes que saiu so dos fechamentos, sem o informe do mes."""
        if self.fluxo is None or self.fonte == "informe":
            return None
        return self.fluxo

    @property
    def diferenca_pl(self) -> float | None:
        """PL no fechamento menos PL no fechamento anterior (zero antes da estreia)."""
        if self.pl_fim is None:
            return None
        if self.pl_inicio is None:
            return self.pl_fim if self.origem == "estreia" else None
        return self.pl_fim - self.pl_inicio


def fluxo_do_mes(
    competencia: str,
    anterior: InformeDiarioEtf | None,
    atual: InformeDiarioEtf | None,
    existia_antes: bool,
    encerrado: bool = False,
) -> FluxoDoMes | None:
    """A captacao do mes entre dois fechamentos (ver o cabecalho).

    `existia_antes`: o ETF tinha PL antes do fechamento anterior (balancete ou
    fechamento mais antigo). `encerrado`: esta em liquidacao e nao tem PL no mes.
    """
    if anterior and atual:
        pontas = dict(
            pl_fim=atual.pl, data_inicio=anterior.data, data_fim=atual.data,
            pl_inicio=anterior.pl, cota_inicio=anterior.cota, cota_fim=atual.cota,
        )
        razao = atual.cota / anterior.cota
        if razao >= _EVENTO_NA_COTA or razao <= 1 / _EVENTO_NA_COTA:
            return FluxoDoMes(competencia, None, "evento_na_cota", **pontas)
        variacao = atual.pl / atual.cota - anterior.pl / anterior.cota
        return FluxoDoMes(competencia, variacao * (anterior.cota + atual.cota) / 2, "cotas", **pontas)
    if atual:
        if existia_antes:
            return FluxoDoMes(competencia, None, "sem_informe", atual.pl, None, atual.data)
        return FluxoDoMes(competencia, atual.pl, "estreia", atual.pl, None, atual.data, cota_fim=atual.cota)
    if anterior:
        if encerrado:
            return FluxoDoMes(competencia, -anterior.pl, "encerramento", 0.0, anterior.data, None, anterior.pl)
        return FluxoDoMes(competencia, None, "sem_informe", None, anterior.data, None, anterior.pl)
    return None


@dataclass
class SomaDoInforme:
    """O mes de um ETF pelo informe diario (ver o cabecalho)."""

    liquida: float
    captacao: float
    resgates: float
    dias: int
    dias_pelo_pl: int


def fluxo_pelo_informe(
    dias: list[InformeDiarioEtf], vespera: InformeDiarioEtf | None, estreia: bool = False
) -> SomaDoInforme | None:
    """Captacao, resgates e liquida do mes pelos dias do informe
    (`fundosnet.ajustes_do_informe` confere cada trecho contra a variacao de cotas).

    `vespera`: o fechamento do mes anterior, que confere o primeiro dia. Sem
    ela, o primeiro dia de um ETF que estreia confere contra o proprio PL (toda
    cota e nova); o de um ETF que ja existia fica com o declarado.
    """
    dias = sorted((d for d in dias if d.data), key=lambda d: d.data)
    if not dias:
        return None
    ajustes = ajustes_do_informe(dias, vespera, estreia)
    captacao = sum(d.captacao or 0.0 for d in dias)
    resgates = sum(d.resgates or 0.0 for d in dias)
    return SomaDoInforme(
        liquida=captacao - resgates + sum(a for a, _ in ajustes),
        captacao=captacao,
        resgates=resgates,
        dias=len(dias),
        dias_pelo_pl=sum(1 for _, valeu in ajustes if valeu),
    )


def com_informe(
    fluxo: FluxoDoMes,
    dias: list[InformeDiarioEtf] | None,
    vespera: InformeDiarioEtf | None,
    estreia: bool,
) -> FluxoDoMes:
    """O mes com o informe diario, quando ele esta em disco; senao, como veio."""
    soma = fluxo_pelo_informe(dias or [], vespera, estreia)
    if soma is None:
        return fluxo
    origem = fluxo.origem if fluxo.origem in ("estreia", "desdobramento", "grupamento", "evento_na_cota") else "informe"
    return replace(
        fluxo,
        fluxo=soma.liquida,
        origem=origem,
        captacao=soma.captacao,
        resgates=soma.resgates,
        dias=soma.dias,
        dias_pelo_pl=soma.dias_pelo_pl,
        fonte="informe",
    )


def ajustar_evento_na_cota(fluxo: FluxoDoMes, retorno_dos_pares: float | None) -> FluxoDoMes:
    """Desdobramento ou grupamento: o fator que deixa a cota rendendo como os pares.

    A cota antiga dividida por k (k > 1 desdobra, k < 1 agrupa) vira comparavel
    com a nova; o k escolhido e o que deixa o retorno mais perto da mediana dos
    pares. Sem par, ou longe demais deles, o mes continua sem dado.
    """
    if fluxo.origem != "evento_na_cota" or retorno_dos_pares is None:
        return fluxo
    razao = fluxo.cota_fim / fluxo.cota_inicio
    distancia = sorted((abs(razao * k - 1 - retorno_dos_pares), k) for k in _FATORES)
    (melhor, fator), (segundo, _) = distancia[0], distancia[1]
    if melhor > _TOLERANCIA_DO_FATOR or segundo - melhor < _FOLGA_DO_FATOR:
        return fluxo
    origem = "desdobramento" if fator > 1 else "grupamento"
    if fluxo.fonte == "informe":
        # O informe ja deu a captacao dia a dia; o fator so identifica o evento.
        return replace(fluxo, origem=origem, fator=fator)
    cota_antes = fluxo.cota_inicio / fator
    variacao = fluxo.pl_fim / fluxo.cota_fim - fluxo.pl_inicio / cota_antes
    return replace(fluxo, fluxo=variacao * (cota_antes + fluxo.cota_fim) / 2, origem=origem, fator=fator)


def limiar_do_mes(pl_inicio: float | None) -> float:
    """Captacao abaixo disto, para mais ou para menos, e mes sem movimento."""
    return max(FLUXO_MINIMO, MATERIALIDADE * (pl_inicio or 0.0))


def estado_do_mes(
    fluxo: float | None,
    anterior: float | None,
    limiar: float = FLUXO_MINIMO,
    limiar_anterior: float | None = None,
) -> str:
    """O estado do mes, dado o fluxo dele e o do mes anterior (ver o cabecalho).

    O mes anterior so conta como captacao ou saida se passou do proprio limiar.
    """
    if fluxo is None:
        return "sem_dado"
    if abs(fluxo) < limiar:
        return "sem_movimento"
    limiar_anterior = limiar if limiar_anterior is None else limiar_anterior
    relevante = anterior is not None and abs(anterior) >= limiar_anterior
    if fluxo > 0:
        return "acelerando" if relevante and anterior > 0 and fluxo > anterior else "captando"
    return "saida_persistente" if relevante and anterior < 0 else "virou_saida"


# --- agregacao --------------------------------------------------------------


# As colunas do informe que se somam por mes, alem da liquida: liquida =
# captacao - resgates + ajuste + sem_informe (o ETF sem a pagina do mes, pelos
# fechamentos). A diferenca de PL tem tambem a valorizacao.
DETALHE = ("captacao", "resgates", "ajuste", "sem_informe", "diferenca_pl")


@dataclass
class _Serie:
    fluxo: dict[str, float] = field(default_factory=dict)
    pl: dict[str, float] = field(default_factory=dict)
    pl_inicio: dict[str, float] = field(default_factory=dict)
    com_dado: dict[str, int] = field(default_factory=dict)
    presente: set[str] = field(default_factory=set)
    estreias: dict[str, float] = field(default_factory=dict)
    # Por coluna de DETALHE, por mes; e os ETFs com o informe do mes.
    detalhe: dict[str, dict[str, float]] = field(default_factory=lambda: {c: {} for c in DETALHE})
    com_informe: dict[str, int] = field(default_factory=dict)

    @classmethod
    def vazia(cls) -> "_Serie":
        return cls()

    def somar(self, fluxos: dict[str, FluxoDoMes]) -> None:
        for mes, f in fluxos.items():
            self.presente.add(mes)
            if f.fluxo is not None:
                self.fluxo[mes] = self.fluxo.get(mes, 0.0) + f.fluxo
                self.com_dado[mes] = self.com_dado.get(mes, 0) + 1
                if f.origem == "estreia":
                    self.estreias[mes] = self.estreias.get(mes, 0.0) + f.fluxo
                # A diferenca de PL so entra com a liquida do mes: e o que deixa
                # "diferenca de PL - liquida" ser a valorizacao dos mesmos ETFs.
                do_informe = f.fonte == "informe"
                valores = {
                    "captacao": (f.captacao or 0.0) if do_informe else None,
                    "resgates": (f.resgates or 0.0) if do_informe else None,
                    "ajuste": f.ajuste, "sem_informe": f.sem_informe, "diferenca_pl": f.diferenca_pl,
                }
                for coluna, valor in valores.items():
                    if valor is not None:
                        self.detalhe[coluna][mes] = self.detalhe[coluna].get(mes, 0.0) + valor
                if f.fonte == "informe":
                    self.com_informe[mes] = self.com_informe.get(mes, 0) + 1
            if f.pl_fim:
                self.pl[mes] = self.pl.get(mes, 0.0) + f.pl_fim
            if f.pl_inicio:
                self.pl_inicio[mes] = self.pl_inicio.get(mes, 0.0) + f.pl_inicio


def _soma(valores) -> float | None:
    lista = [v for v in valores if v is not None]
    return round(sum(lista), 2) if lista else None


def _historico(serie: _Serie, meses: list[str]) -> dict[str, dict]:
    """Por mes (o mes-base incluso): captacao, a parte dela que e estreia, e o PL.

    E o que a conferencia com a ANBIMA usa: o boletim fecha num mes que, na
    visao de 12 meses daqui, pode ser o mes-base."""
    return {
        m: {
            "fluxo": round(serie.fluxo[m], 2) if m in serie.fluxo else None,
            "estreias": round(serie.estreias.get(m, 0.0), 2),
            "pl": round(serie.pl.get(m, 0.0), 2),
            "etfs_com_dado": serie.com_dado.get(m, 0),
            "etfs_com_informe": serie.com_informe.get(m, 0),
            **{c: (round(serie.detalhe[c][m], 2) if m in serie.detalhe[c] else None) for c in DETALHE},
        }
        for m in meses
    }


def _detalhe(serie: _Serie, meses: list[str], meses_ano: list[str]) -> dict:
    """Captacao, resgates, ajuste pela diferenca de PL e diferenca de PL: por
    mes, em 12 meses e no ano."""
    por_mes = {
        c: {m: (round(serie.detalhe[c][m], 2) if m in serie.detalhe[c] else None) for m in meses}
        for c in DETALHE
    }
    return {
        **por_mes,
        "doze_meses": {c: _soma(por_mes[c][m] for m in meses) for c in DETALHE},
        "no_ano": {c: _soma(por_mes[c][m] for m in meses_ano) for c in DETALHE},
        "com_informe": {m: serie.com_informe.get(m, 0) for m in meses},
    }


def _resumo(serie: _Serie, meses: list[str], meses_ano: list[str], base: list[str]) -> dict:
    fluxo = {m: (round(serie.fluxo[m], 2) if m in serie.fluxo else None) for m in base + meses}
    limiar = {m: limiar_do_mes(serie.pl_inicio.get(m)) for m in base + meses}
    estados: dict[str, str] = {}
    anterior = base[0] if base else None
    for mes in meses:
        estados[mes] = (
            estado_do_mes(
                fluxo[mes],
                fluxo[anterior] if anterior else None,
                limiar[mes],
                limiar[anterior] if anterior else None,
            )
            if mes in serie.presente else "fora"
        )
        anterior = mes
    return {
        "fluxo": {m: fluxo[m] for m in meses},
        "estado": estados,
        "doze_meses": _soma(fluxo[m] for m in meses),
        "no_ano": _soma(fluxo[m] for m in meses_ano),
        "pl": round(serie.pl.get(meses[-1], 0.0), 2),
        "estado_atual": estados[meses[-1]],
        "meses_captando": sum(1 for m in meses if estados[m] in _CAPTANDO),
        "meses_em_saida": sum(1 for m in meses if estados[m] in _EM_SAIDA),
        "detalhe": _detalhe(serie, meses, meses_ano),
    }


def _share(parte: float | None, total: float | None) -> float | None:
    if parte is None or not total or total <= 0:
        return None
    return parte / total


# --- bloco ------------------------------------------------------------------


def build_captacao_mensal(
    catalog: dict[str, EtfMetadata],
    fechamentos: dict[str, dict[str, InformeDiarioEtf]],
    pl_series: list | None = None,
    casa_foco: str = "xp",
    informes: dict[str, dict[str, list[InformeDiarioEtf]]] | None = None,
) -> dict:
    """Captacao mes a mes, no ano e em 12 meses: mercado, gestoras, segmentos,
    oportunidades e defesa, e cada ETF com o seu segmento.

    `fechamentos` vem de `ler_fechamentos`; `informes` (os dias do informe de
    cada mes, de `ler_informes_do_mes`) dao captacao e resgates - sem eles, a
    captacao sai so dos fechamentos. `pl_series` (balancete de FIE, se houver)
    so decide se um ETF sem fechamento anterior estreou ou ficou sem informe.
    """
    informes = informes or {}
    competencias = sorted(fechamentos)
    if len(competencias) < 2:
        return {
            "available": False,
            "reason": (
                "menos de dois fechamentos mensais do Fundos.NET em disco - rode `etf` "
                "com a rede liberada para fnet.bmfbovespa.com.br"
            ),
        }
    meses_fluxo = competencias[1:]
    meses = meses_fluxo[-MESES_DA_VISAO:]
    base = [m for m in meses_fluxo if m < meses[0]][-1:]
    ano = meses[-1][:4]
    meses_ano = [m for m in meses if m.startswith(ano)]

    com_pl: dict[str, set[str]] = {}
    for s in pl_series or []:
        if s.pl:
            com_pl.setdefault(s.competencia, set()).add(s.cnpj)

    etfs_catalogo = {c: m for c, m in catalog.items() if m.is_etf}
    segmento_de = {c: segmento_do_etf(m.name) for c, m in etfs_catalogo.items()}
    por_etf: dict[str, dict[str, FluxoDoMes]] = {}
    for cnpj, meta in etfs_catalogo.items():
        em_liquidacao = "LIQUIDA" in normalize_text(meta.situation)
        fluxos: dict[str, FluxoDoMes] = {}
        for i, (antes, mes) in enumerate(zip(competencias, competencias[1:])):
            existia = (
                any(cnpj in fechamentos[m] for m in competencias[:i])
                or cnpj in com_pl.get(antes, ())
            )
            encerrado = em_liquidacao and cnpj not in com_pl.get(mes, ())
            vespera = fechamentos[antes].get(cnpj)
            f = fluxo_do_mes(mes, vespera, fechamentos[mes].get(cnpj), existia, encerrado)
            if f is None:
                continue
            dias = informes.get(mes, {}).get(cnpj)
            if dias:
                f = com_informe(f, dias, vespera, estreia=not existia)
            elif mes in informes and f.origem == "cotas":
                # O mes tem informe em disco, mas nao o deste ETF (pagina que
                # falhou): vale a conta pelos fechamentos, e isso aparece.
                f = replace(f, origem="fechamentos")
            fluxos[mes] = f
        if fluxos:
            por_etf[cnpj] = fluxos

    # Retorno da cota dos pares, por segmento e mes - o que decide o fator de
    # um desdobramento ou grupamento.
    retornos: dict[tuple[str, str], list[float]] = {}
    for cnpj, fluxos in por_etf.items():
        for mes, f in fluxos.items():
            if f.retorno_da_cota is not None:
                retornos.setdefault((segmento_de[cnpj][1], mes), []).append(f.retorno_da_cota)
    for cnpj, fluxos in por_etf.items():
        for mes, f in list(fluxos.items()):
            if f.origem == "evento_na_cota":
                pares = retornos.get((segmento_de[cnpj][1], mes), [])
                fluxos[mes] = ajustar_evento_na_cota(f, statistics.median(pares) if pares else None)

    foco_rotulo: str | None = None
    mercado = _Serie.vazia()
    series_gestora: dict[str, _Serie] = {}
    series_segmento: dict[tuple[str, str], _Serie] = {}
    series_gestora_segmento: dict[tuple[str, str, str], _Serie] = {}
    etfs = []
    for cnpj, fluxos in por_etf.items():
        meta = etfs_catalogo[cnpj]
        gestora = rotulo_da_gestora(meta.manager_name, meta.name)
        casa = casa_da_gestora(meta.manager_name)
        if casa == casa_foco:
            foco_rotulo = gestora
        grupo, segmento = segmento_de[cnpj]
        mercado.somar(fluxos)
        series_gestora.setdefault(gestora, _Serie.vazia()).somar(fluxos)
        series_segmento.setdefault((grupo, segmento), _Serie.vazia()).somar(fluxos)
        series_gestora_segmento.setdefault((grupo, segmento, gestora), _Serie.vazia()).somar(fluxos)
        serie = _Serie.vazia()
        serie.somar(fluxos)
        etfs.append({
            "cnpj": cnpj, "nome": meta.name, "ticker": meta.ticker, "gestora": gestora, "casa": casa,
            "grupo": grupo, "segmento": segmento, **_resumo(serie, meses, meses_ano, base),
            "origem": {
                m: _descreve_origem(f) for m, f in fluxos.items()
                if m in meses and f.origem not in _ORIGENS_COMUNS
            },
            "dias_pelo_pl": sum(f.dias_pelo_pl for m, f in fluxos.items() if m in meses),
            "dias_com_informe": sum(f.dias for m, f in fluxos.items() if m in meses),
            # As pontas e a fonte de cada mes, para a planilha e o CSV longos.
            "mes_a_mes": {
                m: {
                    "pl_inicio": f.pl_inicio, "pl_fim": f.pl_fim, "dias": f.dias,
                    "dias_pelo_pl": f.dias_pelo_pl, "fonte": f.fonte, "origem": f.origem,
                }
                for m, f in fluxos.items() if m in meses
            },
        })
    for etf in etfs:
        etf["foco"] = etf["gestora"] == foco_rotulo

    total = _resumo(mercado, meses, meses_ano, base)
    total["historico"] = _historico(mercado, base + meses)
    series_grupo: dict[str, _Serie] = {}
    for cnpj, fluxos in por_etf.items():
        series_grupo.setdefault(segmento_de[cnpj][0], _Serie.vazia()).somar(fluxos)
    grupos = sorted(
        (
            {"grupo": grupo, **_resumo(serie, meses, meses_ano, base),
             "historico": _historico(serie, base + meses)}
            for grupo, serie in series_grupo.items()
        ),
        key=lambda g: -(g["doze_meses"] or 0),
    )
    gestoras = _gestoras(series_gestora, total, meses, meses_ano, base, foco_rotulo, etfs)
    segmentos = _segmentos(
        series_segmento, series_gestora_segmento, meses, meses_ano, base, foco_rotulo, etfs
    )
    oportunidades = [
        s for s in segmentos
        if (s["doze_meses"] or 0) >= _CAPTACAO_OPORTUNIDADE
        and (not s["foco_tem_etf"] or (s["foco_share"] or 0) < _SHARE_PEQUENA)
    ]
    defesa = sorted((s for s in segmentos if s["foco_lidera_pl"]), key=lambda s: -s["pl"])
    for s in defesa:
        s["alertas"] = alertas_de_defesa(s, foco_rotulo)

    etfs.sort(key=lambda e: (e["grupo"], e["segmento"], -(e["doze_meses"] or 0), e["nome"]))
    datas = {m: fim_do_mes(m).isoformat() for m in competencias}
    origens = [o for e in etfs for o in e["origem"].values()]
    bloco = {
        "available": True,
        "casa_foco": casa_foco,
        "foco": foco_rotulo,
        "meses": meses,
        "meses_ano": meses_ano,
        "ano": ano,
        "mes_base": base[0] if base else None,
        "datas": datas,
        "janela_12m": {
            "de": datas[competencias[competencias.index(meses[0]) - 1]], "ate": datas[meses[-1]],
        },
        "janela_ano": {
            "de": datas.get(f"{int(ano) - 1}12") or datas[competencias[competencias.index(meses_ano[0]) - 1]],
            "ate": datas[meses[-1]],
        },
        "limiares": {
            "fluxo_minimo": FLUXO_MINIMO, "materialidade": MATERIALIDADE,
            "captacao_oportunidade": _CAPTACAO_OPORTUNIDADE, "share_pequena": _SHARE_PEQUENA,
        },
        "metodo": "informe" if any(m in informes for m in meses) else "fechamentos",
        "cobertura": {
            "etfs": len(por_etf),
            "etfs_catalogo": len(etfs_catalogo),
            "com_dado": {m: mercado.com_dado.get(m, 0) for m in meses},
            "com_informe": {m: mercado.com_informe.get(m, 0) for m in meses},
            "meses_com_informe": [m for m in meses if m in informes],
            "dias_com_informe": sum(e["dias_com_informe"] for e in etfs),
            "dias_pelo_pl": sum(e["dias_pelo_pl"] for e in etfs),
            "sem_informe_do_mes": sum(
                1 for fluxos in por_etf.values() for m, f in fluxos.items()
                if m in meses and f.origem == "fechamentos"
            ),
            "ajuste_por_gestora": sorted(
                (
                    {"gestora": g["gestora"], **g["detalhe"]["doze_meses"], "liquida": g["doze_meses"]}
                    for g in gestoras if abs(g["detalhe"]["doze_meses"]["ajuste"] or 0) >= FLUXO_MINIMO
                ),
                key=lambda g: -abs(g["ajuste"] or 0),
            ),
            "sem_dado": sum(1 for e in etfs for m in meses if e["estado"][m] == "sem_dado"),
            "estreias": sum(1 for o in origens if o.startswith("estreia")),
            "eventos_na_cota": sorted(
                (e["nome"], m, o) for e in etfs for m, o in e["origem"].items()
                if o.startswith(("desdobramento", "grupamento"))
            ),
        },
        "mercado": total,
        "grupos": grupos,
        "gestoras": gestoras,
        "segmentos": segmentos,
        "oportunidades": oportunidades,
        "defesa": defesa,
        "etfs": etfs,
    }
    bloco["leitura"] = leitura_captacao_mensal(bloco)
    return bloco


def _descreve_origem(fluxo: FluxoDoMes) -> str:
    """"desdobramento de cotas (1:8)", "estreia"..."""
    texto = ORIGENS.get(fluxo.origem, fluxo.origem)
    if fluxo.fator:
        k = fluxo.fator if fluxo.fator > 1 else 1 / fluxo.fator
        proporcao = f"1:{k:.0f}" if fluxo.fator > 1 else f"{k:.0f}:1"
        texto += f" ({proporcao})"
    return texto


def _gestoras(series, total, meses, meses_ano, base, foco, etfs) -> list[dict]:
    pl_total = total["pl"]
    linhas = []
    for gestora, serie in series.items():
        resumo = _resumo(serie, meses, meses_ano, base)
        linhas.append({
            "gestora": gestora,
            "casa": next((e["casa"] for e in etfs if e["gestora"] == gestora and e["casa"]), None),
            "foco": gestora == foco,
            "etfs": sum(1 for e in etfs if e["gestora"] == gestora and e["pl"] > 0),
            **resumo,
            "share_pl": _share(resumo["pl"], pl_total),
            "share_doze_meses": _share(resumo["doze_meses"], total["doze_meses"]),
            "share_no_ano": _share(resumo["no_ano"], total["no_ano"]),
        })
    linhas.sort(key=lambda g: (-(g["doze_meses"] or 0), g["gestora"]))
    for posicao, linha in enumerate(linhas, start=1):
        linha["posicao"] = posicao
    return linhas


def _segmentos(series, por_gestora, meses, meses_ano, base, foco, etfs) -> list[dict]:
    linhas = []
    for (grupo, segmento), serie in series.items():
        resumo = _resumo(serie, meses, meses_ano, base)
        gestoras = {
            g: _resumo(s, meses, meses_ano, base)
            for (gr, sg, g), s in por_gestora.items() if (gr, sg) == (grupo, segmento)
        }
        ranking = sorted(gestoras.items(), key=lambda kv: -(kv[1]["doze_meses"] or 0))
        lider, dados_lider = ranking[0] if ranking else (None, None)
        rivais = [(g, d) for g, d in ranking if g != foco and (d["doze_meses"] or 0) > 0]
        maior_pl = max(gestoras.items(), key=lambda kv: kv[1]["pl"], default=(None, None))[0]
        do_foco = gestoras.get(foco) if foco else None
        linhas.append({
            "grupo": grupo,
            "segmento": segmento,
            **resumo,
            "gestoras": len([g for g, d in gestoras.items() if d["pl"] > 0]),
            "etfs": sum(1 for e in etfs if (e["grupo"], e["segmento"]) == (grupo, segmento) and e["pl"] > 0),
            "lider_captacao": lider if dados_lider and (dados_lider["doze_meses"] or 0) > 0 else None,
            "lider_captacao_valor": dados_lider["doze_meses"] if dados_lider else None,
            "rivais_captando": [
                {"gestora": g, "doze_meses": d["doze_meses"], "estado_atual": d["estado_atual"]}
                for g, d in rivais[:3]
            ],
            "lider_pl": maior_pl,
            "foco_tem_etf": do_foco is not None and do_foco["pl"] > 0,
            "foco_lidera_pl": bool(foco) and maior_pl == foco and (do_foco or {}).get("pl", 0) > 0,
            "foco_fluxo": do_foco["fluxo"] if do_foco else None,
            "foco_estado": do_foco["estado"] if do_foco else None,
            "foco_estado_atual": do_foco["estado_atual"] if do_foco else None,
            "foco_doze_meses": do_foco["doze_meses"] if do_foco else None,
            "foco_no_ano": do_foco["no_ano"] if do_foco else None,
            "foco_share": _share(do_foco["doze_meses"], resumo["doze_meses"]) if do_foco else None,
        })
    linhas.sort(key=lambda s: -(s["doze_meses"] or 0))
    return linhas


# --- leitura ----------------------------------------------------------------


def _mes(competencia: str | None) -> str:
    return competencia_br(competencia) if competencia else "-"


def rotulo(estado: str | None) -> str:
    return ROTULOS.get(estado or "sem_dado", "sem dado")


def reais_com_sinal(valor: float | None) -> str:
    """"+R$ 1,2 bi" / "-R$ 83 mi" - captacao, que tem direcao."""
    if valor is None:
        return "-"
    return ("-" if valor < 0 else "+") + reais(abs(valor))


def legenda() -> str:
    """"▲▲ acelerando; ▲ captando; ..." - a chave dos simbolos, em toda superficie."""
    return "; ".join(f"{SIMBOLOS[e]} {ROTULOS[e]}" for e in ORDEM_DA_LEGENDA) + "; em branco: antes da estreia"


def prazos_captacao(bloco: dict) -> list[str]:
    """O prazo de cada numero do bloco, para sair junto dele."""
    if not bloco.get("available"):
        return []
    meses = bloco["meses"]
    if bloco.get("metodo") == "informe":
        metodo = [
            "captação: informe diário do Fundos.NET, dia a dia - captação menos resgate "
            "declarados pelo administrador; no dia em que o declarado não fecha com a "
            "variação de cotas (a diferença de PL sem a valorização), vale ela, e a diferença "
            "entra como ajuste pela diferença de PL",
            "diferença de PL: PL no último informe do mês menos o do mês anterior - "
            "captação líquida mais valorização",
        ]
    else:
        metodo = [
            "captação: variação de cotas entre os fechamentos mensais do Fundos.NET (último "
            "informe de cada ETF até o fim do mês), valorizada pela cota média do mês",
        ]
    linhas = [
        *metodo,
        f"12 meses: de {_mes(meses[0])} a {_mes(meses[-1])} (fechamentos de "
        f"{data_br(bloco['janela_12m']['de'])} a {data_br(bloco['janela_12m']['ate'])})",
        f"no ano: de {_mes(bloco['meses_ano'][0])} a {_mes(meses[-1])} (fechamentos de "
        f"{data_br(bloco['janela_ano']['de'])} a {data_br(bloco['janela_ano']['ate'])})",
    ]
    if bloco.get("mes_base"):
        linhas.append(f"o estado de {_mes(meses[0])} compara com {_mes(bloco['mes_base'])}")
    return linhas


def alertas_de_defesa(segmento: dict, foco: str | None) -> list[str]:
    """Por que um segmento que a casa lidera em PL merece atencao."""
    alertas = []
    if segmento["foco_estado_atual"] in _EM_SAIDA:
        alertas.append(f"a {foco} está em {rotulo(segmento['foco_estado_atual'])}")
    rival = segmento["rivais_captando"][0] if segmento["rivais_captando"] else None
    if rival and (rival["doze_meses"] or 0) > (segmento["foco_doze_meses"] or 0):
        alertas.append(
            f"{curto(rival['gestora'])} captou mais em 12 meses: "
            f"{reais_com_sinal(rival['doze_meses'])}, contra "
            f"{reais_com_sinal(segmento['foco_doze_meses'])} da {foco}"
        )
    return alertas


def leitura_captacao_mensal(bloco: dict) -> list[str]:
    """As frases que abrem a visao mensal em toda superficie."""
    if not bloco.get("available"):
        return []
    meses = bloco["meses"]
    ultimo = meses[-1]
    mercado = bloco["mercado"]
    foco = bloco.get("foco")
    frases = [
        f"Em 12 meses ({_mes(meses[0])} a {_mes(ultimo)}), os ETFs captaram "
        f"{reais_com_sinal(mercado['doze_meses'])} líquidos; no ano, "
        f"{reais_com_sinal(mercado['no_ano'])}. Em {_mes(ultimo)}, "
        f"{reais_com_sinal(mercado['fluxo'][ultimo])}: {rotulo(mercado['estado_atual'])}."
    ]
    if bloco.get("metodo") == "informe":
        doze = mercado["detalhe"]["doze_meses"]
        frase = (
            f"No informe diário do Fundos.NET, em 12 meses: captação de {reais(doze['captacao'] or 0)} "
            f"e resgates de {reais(doze['resgates'] or 0)}, com ajuste de "
            f"{reais_com_sinal(doze['ajuste'] or 0)} pela diferença de PL"
            + (
                f" e {reais_com_sinal(doze['sem_informe'])} de ETFs sem o informe do mês"
                if doze.get("sem_informe") else ""
            )
            + f"; o PL variou {reais_com_sinal(doze['diferenca_pl'])}"
        )
        maiores = [g for g in bloco["cobertura"].get("ajuste_por_gestora", []) if g["gestora"]][:2]
        if maiores:
            frase += (
                " - o ajuste vem sobretudo de "
                + " e ".join(f"{g['gestora']} ({reais_com_sinal(g['ajuste'])})" for g in maiores)
            )
        frases.append(frase + ".")
    lideres = [g for g in bloco["gestoras"] if (g["doze_meses"] or 0) > 0][:3]
    if lideres:
        frases.append(
            "Quem mais captou em 12 meses: "
            + "; ".join(
                f"{g['gestora']}, {reais_com_sinal(g['doze_meses'])} "
                f"({pct(g['share_doze_meses'])} da captação)"
                for g in lideres
            )
            + "."
        )
    da_casa = next((g for g in bloco["gestoras"] if g["foco"]), None)
    if foco and da_casa:
        frases.append(
            f"A {foco} captou {reais_com_sinal(da_casa['doze_meses'])} em 12 meses - "
            f"{pct(da_casa['share_doze_meses'])} da captação dos ETFs, contra "
            f"{pct(da_casa['share_pl'])} do PL - e é a {da_casa['posicao']}ª em captação; "
            f"em {_mes(ultimo)}, {reais_com_sinal(da_casa['fluxo'][ultimo])}: "
            f"{rotulo(da_casa['estado_atual'])}."
        )
    por_estado: dict[str, list[str]] = {}
    for s in bloco["segmentos"]:
        if s["estado_atual"] in ("acelerando", "virou_saida", "saida_persistente"):
            por_estado.setdefault(s["estado_atual"], []).append(s["segmento"])
    partes = [
        f"{rotulo(e)}: {', '.join(por_estado[e][:4])}"
        for e in ("acelerando", "virou_saida", "saida_persistente") if por_estado.get(e)
    ]
    if partes:
        frases.append(f"Segmentos em {_mes(ultimo)} - " + "; ".join(partes) + ".")
    if foco and bloco["oportunidades"]:
        frases.append(
            f"Onde o dinheiro entrou e a {foco} ficou de fora ou com menos de "
            f"{pct(_SHARE_PEQUENA, 0)} da captação em 12 meses: "
            + "; ".join(
                f"{s['segmento']}, {reais_com_sinal(s['doze_meses'])} com "
                f"{curto(s['lider_captacao'])} à frente"
                for s in bloco["oportunidades"][:3]
            )
            + "."
        )
    alertas = [s for s in bloco["defesa"] if s.get("alertas")]
    if foco and alertas:
        frases.append(
            f"Na defesa, segmentos que a {foco} lidera em PL com sinal de alerta: "
            + "; ".join(f"{s['segmento']} - {'; '.join(s['alertas'])}" for s in alertas[:3])
            + "."
        )
    return frases
