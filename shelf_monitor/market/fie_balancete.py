"""PL mensal dos ETFs, lido do balancete de Fundos de Investimento Estruturados.

Por que este modulo existe: o INF_DIARIO e o informe de FI/FIF e nao cobre fundo
de indice. Medido em 2026-09-19 contra o catalogo FIIM: 0 de 249 ETFs tinham
linha em `inf_diario_fi_202609.zip` - a lacuna que fazia o monitor mostrar fluxo
so de feeder. O balancete de FIE cobre 219 desses 249.

Onde os ETFs aparecem nos dados abertos da CVM (medido, nao suposto):

    /FIE/DOC/BALANCETE/DADOS/balancete_fie_{YYYYMM}.zip   219/249 ETFs, ~0,1 MB
    /FI/DOC/CDA/DADOS/cda_fi_{YYYYMM}.zip                 204/249 (no membro
                                                          cda_fie_{YYYYMM}.csv)

Nao existe `cda_fiim_*` nem diretorio `/dados/FIIM/`: o dado de fundo de indice
vem sob o guarda-chuva FIE (Estruturados). O balancete e a escolha aqui - cobre
mais ETFs, pesa 150x menos e traz o PL direto, sem somar carteira.

As contas COSIF, identificadas por medicao (plano COFI, competencia 08/2026, 219
ETFs). A identidade abaixo fecha com desvio relativo ZERO nos 219:

    60000002 = 61100004 + 61800005
    PL total   capital     resultado
               de cotas    acumulado

O que este modulo NAO entrega, e por que: captacao liquida separada da
valorizacao. A decomposicao acima parece prometer isso - a variacao do capital de
cotas seria o fluxo -, mas entre 07 e 08/2026 o resultado acumulado ficou
identico nos 213 ETFs presentes nos dois meses, e a variacao do PL igualou a do
capital de cotas em todos eles. Ou seja: nesse plano a conta de cotas absorve
marcacao a mercado junto com integralizacao, e a variacao dela nao e fluxo.
Separar exigiria a quantidade de cotas em circulacao, que o balancete nao traz -
as contas de quantidade (90917000/30915000) carregam numeros redondos de cotas
autorizadas: por elas, dois ETFs de Ibovespa teriam rendido -12,5% e +9,6% no
mesmo mes, o que e impossivel. Por isso o que sai daqui se chama variacao de PL e
nao captacao: publicar a variacao do PL como fluxo seria dar por captacao o que
e, em parte, mercado.
"""
from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.datasets import CVM_BASE, Dataset
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.fetch import fetch

# Contas do plano COFI - ver o cabecalho do modulo para como foram identificadas.
PL_ACCOUNT = "60000002"
SHARE_CAPITAL_ACCOUNT = "61100004"
RETAINED_RESULT_ACCOUNT = "61800005"

_CNPJ_TRANS = str.maketrans("", "", "./- ")


def _normalize_cnpj(raw: str | None) -> str:
    return (raw or "").translate(_CNPJ_TRANS).strip()


def _parse_float(raw: str | None) -> float:
    if raw in (None, ""):
        return 0.0
    try:
        return float(str(raw).replace(",", "."))
    except ValueError:
        return 0.0


def _parse_date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError:
        return None


@dataclass
class EtfMonthlyBalance:
    """Saldos de um ETF numa competencia mensal do balancete."""

    cnpj: str
    competencia: str  # YYYYMM
    dt_comptc: date | None
    pl: float = 0.0
    share_capital: float = 0.0
    retained_result: float = 0.0

    @property
    def identity_holds(self) -> bool:
        """PL = capital de cotas + resultado acumulado.

        A guarda contra ler a conta errada: se a CVM renumerar o plano, isto
        para de fechar e o chamador ve o registro como suspeito, em vez de
        publicar como PL um numero que e outra coisa.
        """
        if not self.pl:
            return False
        total = self.share_capital + self.retained_result
        return abs(self.pl - total) / abs(self.pl) < 1e-6


def fetch_balancete_month(
    config: Config, yyyymm: str, force: bool = False
) -> Path | None:
    """Baixa o balancete mensal de FIE. None quando a competencia nao existe.

    Nao levanta: a competencia do mes corrente ainda nao foi publicada (a CVM
    publica com defasagem), e isso e rotina, nao falha.
    """
    filename = f"balancete_fie_{yyyymm}.zip"
    dataset = Dataset(
        key=f"balancete_fie_{yyyymm}",
        url=f"{CVM_BASE}/FIE/DOC/BALANCETE/DADOS/{filename}",
        filename=filename,
        description=f"Balancete de Fundos Estruturados {yyyymm}",
        members={"balancete": f"balancete_fie_{yyyymm}"},
        approx_mb=1,
    )
    try:
        return fetch(config, dataset, force=force).path
    except Exception:  # noqa: BLE001 - competencia ausente ou origem fora do ar
        return None


def read_etf_balances(
    zip_path: Path | None, target_cnpjs: set[str], competencia: str
) -> list[EtfMonthlyBalance]:
    """Le o balancete e devolve um registro por ETF do catalogo.

    O arquivo tem uma linha por conta contabil: o registro de um fundo e a
    reducao das poucas contas que interessam.
    """
    if not zip_path or not zip_path.exists() or not zipfile.is_zipfile(zip_path):
        return []

    wanted = {PL_ACCOUNT, SHARE_CAPITAL_ACCOUNT, RETAINED_RESULT_ACCOUNT}
    by_cnpj: dict[str, EtfMonthlyBalance] = {}

    with zipfile.ZipFile(zip_path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not members:
            return []
        # latin-1 e a codificacao dos CSV de documento da CVM (o cadastro e
        # utf-8, o balancete nao) - decodificar errado corrompe a razao social.
        text = archive.read(members[0]).decode("latin-1", errors="replace")

    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        cnpj = _normalize_cnpj(row.get("CNPJ_FUNDO_CLASSE"))
        if cnpj not in target_cnpjs:
            continue
        account = (row.get("CD_CONTA_BALCTE") or "").strip()
        if account not in wanted:
            continue

        record = by_cnpj.get(cnpj)
        if record is None:
            record = EtfMonthlyBalance(
                cnpj=cnpj,
                competencia=competencia,
                dt_comptc=_parse_date(row.get("DT_COMPTC")),
            )
            by_cnpj[cnpj] = record

        value = _parse_float(row.get("VL_SALDO_BALCTE"))
        if account == PL_ACCOUNT:
            record.pl = value
        elif account == SHARE_CAPITAL_ACCOUNT:
            record.share_capital = value
        else:
            record.retained_result = value

    return [r for r in by_cnpj.values() if r.pl]


@dataclass
class EtfMonthlyPl:
    """PL de um ETF numa competencia, com a variacao contra a anterior."""

    cnpj: str
    etf_name: str
    ticker: str | None
    category: str
    manager_name: str | None
    competencia: str
    dt_comptc: date | None
    pl: float
    pl_anterior: float | None = None
    # Variacao de PL - NAO e captacao liquida. Ver o cabecalho do modulo.
    variacao_pl: float | None = None
    variacao_pct: float | None = None
    # A identidade contabil nao fechou: o plano de contas pode ter mudado.
    identidade_suspeita: bool = False


def monthly_pl_series(
    catalog: dict[str, EtfMetadata], balances: list[EtfMonthlyBalance]
) -> list[EtfMonthlyPl]:
    """Ordena os saldos por ETF e competencia e calcula a variacao mes a mes."""
    by_cnpj: dict[str, list[EtfMonthlyBalance]] = {}
    for balance in balances:
        by_cnpj.setdefault(balance.cnpj, []).append(balance)

    series: list[EtfMonthlyPl] = []
    for cnpj, records in by_cnpj.items():
        metadata = catalog.get(cnpj)
        if metadata is None:
            continue
        records.sort(key=lambda r: r.competencia)
        previous: float | None = None
        for record in records:
            variacao = None if previous is None else record.pl - previous
            series.append(
                EtfMonthlyPl(
                    cnpj=cnpj,
                    etf_name=metadata.name,
                    ticker=metadata.ticker,
                    category=metadata.category,
                    manager_name=metadata.manager_name,
                    competencia=record.competencia,
                    dt_comptc=record.dt_comptc,
                    pl=record.pl,
                    pl_anterior=previous,
                    variacao_pl=variacao,
                    variacao_pct=(
                        variacao / previous * 100
                        if variacao is not None and previous
                        else None
                    ),
                    identidade_suspeita=not record.identity_holds,
                )
            )
            previous = record.pl
    series.sort(key=lambda s: (s.competencia, -s.pl))
    return series


@dataclass
class PlCoverage:
    """Quanto do catalogo o balancete alcancou - a linha que impede o silencio."""

    competencia: str | None = None
    etfs_no_catalogo: int = 0
    etfs_com_pl: int = 0
    pl_total: float = 0.0
    identidades_suspeitas: int = 0

    @property
    def coverage_ratio(self) -> float:
        if not self.etfs_no_catalogo:
            return 0.0
        return self.etfs_com_pl / self.etfs_no_catalogo


def pl_coverage(
    catalog: dict[str, EtfMetadata], series: list[EtfMonthlyPl]
) -> PlCoverage:
    """Cobertura da competencia mais recente presente na serie."""
    etfs_no_catalogo = sum(1 for m in catalog.values() if m.is_etf)
    if not series:
        return PlCoverage(etfs_no_catalogo=etfs_no_catalogo)

    latest = max(s.competencia for s in series)
    current = [s for s in series if s.competencia == latest]
    return PlCoverage(
        competencia=latest,
        etfs_no_catalogo=etfs_no_catalogo,
        etfs_com_pl=len({s.cnpj for s in current}),
        pl_total=sum(s.pl for s in current),
        identidades_suspeitas=sum(1 for s in current if s.identidade_suspeita),
    )


def run_etf_pl_pipeline(
    config: Config,
    catalog: dict[str, EtfMetadata],
    months: list[str],
    force_download: bool = False,
) -> tuple[list[EtfMonthlyPl], PlCoverage, list[str]]:
    """Baixa as competencias pedidas e devolve (serie, cobertura, meses ausentes)."""
    target = set(catalog)
    balances: list[EtfMonthlyBalance] = []
    missing: list[str] = []

    for yyyymm in months:
        path = fetch_balancete_month(config, yyyymm, force=force_download)
        found = read_etf_balances(path, target, yyyymm)
        if not found:
            missing.append(yyyymm)
            continue
        balances.extend(found)

    series = monthly_pl_series(catalog, balances)
    return series, pl_coverage(catalog, series), missing
