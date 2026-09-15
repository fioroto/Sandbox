"""Ingestão e agregação semanal de captação líquida dos ETFs (CVM INF_DIARIO).

Baixa os arquivos mensais `inf_diario_fi_{YYYYMM}.zip` da CVM, filtra apenas os CNPJs
dos ETFs identificados no catálogo e consolida métricas semanais por ETF e por categoria.

Ressalva de cobertura: o INF_DIARIO é o informe de FI/FIF. Fundos de índice
(FIIM - o tipo estrutural do ETF) têm arquivos próprios na CVM (carteira em
`cda_fiim_AAAAMM.csv`, balancete em "Fundos Estruturados"), não este. Na
prática isso significa que a captação diária de um ETF de verdade normalmente
não aparece aqui - só a dos *feeders* (FIF que replica um ETF) aparece, porque
feeder é FI/FIF normal. `run_etf_flow_pipeline` reporta a cobertura real (quantos
ETFs do catálogo têm alguma linha no informe) em vez de assumir que ela é total;
ver `flow_coverage` e o aviso que `print_etf_summary` imprime quando ela é baixa.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from shelf_monitor.config import Config
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.inf_diario import (
    DailyRecord,
    dedupe_by_day,
    fetch_inf_diario_month,
    get_recent_months,
    read_daily_records,
)

# Aliases de compatibilidade: o parser generico do INF_DIARIO mudou de nome e
# de modulo (shelf_monitor.market.inf_diario), mas ETFs continuam usando esta
# mesma leitura - so o alvo de CNPJs (o catalogo de ETF, nao a vitrine) muda.
EtfDailyRecord = DailyRecord
_dedupe_by_day = dedupe_by_day
read_etf_daily_records = read_daily_records

# Heurística, não fato de mercado: um ETF/feeder que varie mais que isso em
# cota numa única semana quase sempre denuncia descontinuidade nos dados (duas
# linhas do mesmo CNPJ na mesma data, ou uma resubmissão que trocou o
# denominador da cota), não retorno real - cripto é a exceção que justifica um
# teto mais largo. Quando o teto é ultrapassado, a rentabilidade da semana sai
# None e a linha é marcada `data_suspeita`, em vez de publicar um número que
# não é real.
_MAX_PLAUSIBLE_WEEKLY_RETURN_PCT: dict[str, float] = {
    "Cripto": 60.0,
    "Renda Fixa": 8.0,
}
_DEFAULT_MAX_PLAUSIBLE_WEEKLY_RETURN_PCT = 30.0


@dataclass
class EtfWeeklyFlow:
    """Consolidado semanal de captação de um ETF ou feeder."""

    cnpj: str
    etf_name: str
    ticker: str | None
    category: str
    manager_name: str | None
    kind: str  # "etf" ou "feeder" - ver shelf_monitor.market.etf.classify_etf_kind
    iso_year: int
    iso_week: int
    week_key: str  # Ex: "2026-W35"
    week_end_date: date  # Sexta ou último dia útil da semana
    captacao_bruta: float
    resgates: float
    captacao_liquida: float
    pl_inicio: float
    pl_fim: float
    cotistas_inicio: int
    cotistas_fim: int
    variacao_cotistas: int
    rentabilidade_semana: float | None = None
    # True quando a rentabilidade calculada excedeu o teto plausível para a
    # categoria e foi descartada (ver _MAX_PLAUSIBLE_WEEKLY_RETURN_PCT) - sinal
    # de descontinuidade nos dados, não de um resultado extremo real.
    data_suspeita: bool = False


@dataclass
class CategoryWeeklySummary:
    """Consolidado semanal por Categoria de ETF (ex: Renda Fixa, Ações Brasil, Cripto)."""

    category: str
    iso_year: int
    iso_week: int
    week_key: str
    week_end_date: date
    captacao_bruta: float
    resgates: float
    captacao_liquida: float
    pl_total: float
    qtd_etfs: int


def aggregate_etf_weekly_flows(
    catalog: dict[str, EtfMetadata], daily_records: list[EtfDailyRecord]
) -> list[EtfWeeklyFlow]:
    """Consolida os registros diários por ETF/feeder e por semana ISO.

    A rentabilidade da semana compara a cota de fechamento com a cota de
    fechamento da semana ISO anterior (quando existe) - não com a primeira
    linha da própria semana, que descartaria o retorno do primeiro pregão toda
    vez que a série é cortada em semanas.
    """
    deduped = _dedupe_by_day(daily_records)

    by_cnpj: dict[str, list[EtfDailyRecord]] = defaultdict(list)
    for rec in deduped:
        by_cnpj[rec.cnpj].append(rec)

    weekly_flows: list[EtfWeeklyFlow] = []

    for cnpj, recs in by_cnpj.items():
        recs.sort(key=lambda r: r.dt_comptc)

        weeks: dict[tuple[int, int], list[EtfDailyRecord]] = defaultdict(list)
        for rec in recs:
            iso_year, iso_week, _ = rec.dt_comptc.isocalendar()
            weeks[(iso_year, iso_week)].append(rec)

        meta = catalog.get(cnpj)
        etf_name = meta.name if meta else cnpj
        ticker = meta.ticker if meta else None
        category = meta.category if meta else "Outros"
        manager = meta.manager_name if meta else None
        kind = meta.kind if meta else "etf"
        max_return = _MAX_PLAUSIBLE_WEEKLY_RETURN_PCT.get(
            category, _DEFAULT_MAX_PLAUSIBLE_WEEKLY_RETURN_PCT
        )

        prev_week_last: EtfDailyRecord | None = None
        for iso_year, iso_week in sorted(weeks.keys()):
            week_recs = weeks[(iso_year, iso_week)]
            week_recs.sort(key=lambda r: r.dt_comptc)

            reference = prev_week_last or week_recs[0]
            week_end_date = week_recs[-1].dt_comptc
            week_key = f"{iso_year}-W{iso_week:02d}"

            captacao_bruta = sum(r.captc_dia for r in week_recs)
            resgates = sum(r.resg_dia for r in week_recs)
            captacao_liquida = captacao_bruta - resgates

            pl_inicio = reference.vl_patrim_liq
            pl_fim = week_recs[-1].vl_patrim_liq

            cotistas_inicio = reference.nr_cotst
            cotistas_fim = week_recs[-1].nr_cotst
            variacao_cotistas = cotistas_fim - cotistas_inicio

            quota_ini = reference.vl_quota
            quota_fim = week_recs[-1].vl_quota
            rentab = None
            suspeita = False
            if quota_ini and quota_ini > 0:
                rentab = round(((quota_fim / quota_ini) - 1.0) * 100.0, 4)
                if abs(rentab) > max_return:
                    suspeita = True
                    rentab = None

            weekly_flows.append(
                EtfWeeklyFlow(
                    cnpj=cnpj,
                    etf_name=etf_name,
                    ticker=ticker,
                    category=category,
                    manager_name=manager,
                    kind=kind,
                    iso_year=iso_year,
                    iso_week=iso_week,
                    week_key=week_key,
                    week_end_date=week_end_date,
                    captacao_bruta=round(captacao_bruta, 2),
                    resgates=round(resgates, 2),
                    captacao_liquida=round(captacao_liquida, 2),
                    pl_inicio=round(pl_inicio, 2),
                    pl_fim=round(pl_fim, 2),
                    cotistas_inicio=cotistas_inicio,
                    cotistas_fim=cotistas_fim,
                    variacao_cotistas=variacao_cotistas,
                    rentabilidade_semana=rentab,
                    data_suspeita=suspeita,
                )
            )
            prev_week_last = week_recs[-1]

    # Ordenar por semana decrescente e captação líquida decrescente
    weekly_flows.sort(key=lambda w: (w.iso_year, w.iso_week, w.captacao_liquida), reverse=True)
    return weekly_flows


def aggregate_category_weekly_flows(
    weekly_flows: list[EtfWeeklyFlow],
) -> list[CategoryWeeklySummary]:
    """Consolida os fluxos semanais por Categoria de ETF.

    Só entram fluxos de ETF (`kind == "etf"`) - um feeder somado ao ETF que ele
    replica contaria o mesmo dinheiro duas vezes.
    """
    grouped: dict[tuple[str, int, int], list[EtfWeeklyFlow]] = defaultdict(list)

    for flow in weekly_flows:
        if flow.kind != "etf":
            continue
        grouped[(flow.category, flow.iso_year, flow.iso_week)].append(flow)

    summaries: list[CategoryWeeklySummary] = []

    for (cat, iso_year, iso_week), flows in grouped.items():
        week_key = f"{iso_year}-W{iso_week:02d}"
        week_end_date = max(f.week_end_date for f in flows)

        cap_bruta = sum(f.captacao_bruta for f in flows)
        resgates = sum(f.resgates for f in flows)
        cap_liq = sum(f.captacao_liquida for f in flows)
        pl_total = sum(f.pl_fim for f in flows)

        summaries.append(
            CategoryWeeklySummary(
                category=cat,
                iso_year=iso_year,
                iso_week=iso_week,
                week_key=week_key,
                week_end_date=week_end_date,
                captacao_bruta=round(cap_bruta, 2),
                resgates=round(resgates, 2),
                captacao_liquida=round(cap_liq, 2),
                pl_total=round(pl_total, 2),
                qtd_etfs=len(flows),
            )
        )

    summaries.sort(key=lambda s: (s.iso_year, s.iso_week, s.captacao_liquida), reverse=True)
    return summaries


@dataclass
class FlowCoverage:
    """O que a fonte (INF_DIARIO) realmente cobriu do catálogo.

    Existe para que "captação de ETF" nunca saia em silêncio como se fosse
    completa quando é, na prática, só a dos feeders - ver a ressalva no topo
    do módulo.
    """

    etfs_no_catalogo: int = 0
    etfs_com_fluxo: int = 0
    feeders_no_catalogo: int = 0
    feeders_com_fluxo: int = 0
    semanas_suspeitas: int = 0

    @property
    def etf_coverage_ratio(self) -> float:
        if not self.etfs_no_catalogo:
            return 0.0
        return self.etfs_com_fluxo / self.etfs_no_catalogo


def flow_coverage(
    catalog: dict[str, EtfMetadata], weekly_flows: list[EtfWeeklyFlow]
) -> FlowCoverage:
    etfs_no_catalogo = sum(1 for m in catalog.values() if m.is_etf)
    feeders_no_catalogo = sum(1 for m in catalog.values() if not m.is_etf)

    cnpjs_etf_com_fluxo = {f.cnpj for f in weekly_flows if f.kind == "etf"}
    cnpjs_feeder_com_fluxo = {f.cnpj for f in weekly_flows if f.kind == "feeder"}

    return FlowCoverage(
        etfs_no_catalogo=etfs_no_catalogo,
        etfs_com_fluxo=len(cnpjs_etf_com_fluxo),
        feeders_no_catalogo=feeders_no_catalogo,
        feeders_com_fluxo=len(cnpjs_feeder_com_fluxo),
        semanas_suspeitas=sum(1 for f in weekly_flows if f.data_suspeita),
    )


def run_etf_flow_pipeline(
    config: Config,
    months_count: int = 3,
    force_download: bool = False,
    catalog: dict[str, EtfMetadata] | None = None,
) -> tuple[dict[str, EtfMetadata], list[EtfWeeklyFlow], list[CategoryWeeklySummary]]:
    """Executa a pipeline completa: catálogo CVM -> download INF_DIARIO -> agregação semanal."""
    if catalog is None:
        from shelf_monitor.market.etf import build_etf_catalog

        catalog = build_etf_catalog(config, force_import=force_download)

    target_cnpjs = set(catalog.keys())
    months = get_recent_months(count=months_count)

    all_daily_records: list[EtfDailyRecord] = []
    for yyyymm in months:
        zip_path = fetch_inf_diario_month(config, yyyymm, force=force_download)
        if zip_path:
            recs = read_etf_daily_records(zip_path, target_cnpjs)
            all_daily_records.extend(recs)

    weekly_flows = aggregate_etf_weekly_flows(catalog, all_daily_records)
    category_summaries = aggregate_category_weekly_flows(weekly_flows)

    return catalog, weekly_flows, category_summaries
