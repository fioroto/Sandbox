"""Ingestão e agregação semanal de captação líquida dos ETFs (CVM INF_DIARIO).

Baixa os arquivos mensais `inf_diario_fi_{YYYYMM}.zip` da CVM, filtra apenas os CNPJs
dos ETFs identificados no catálogo e consolida métricas semanais por ETF e por categoria.
"""
from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.datasets import Dataset
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.fetch import fetch
from shelf_monitor.normalize import normalize_cnpj, parse_number


@dataclass
class EtfDailyRecord:
    """Registro de informe diário de um ETF numa data-base."""

    cnpj: str
    dt_comptc: date
    vl_total: float = 0.0
    vl_quota: float = 0.0
    vl_patrim_liq: float = 0.0
    captc_dia: float = 0.0
    resg_dia: float = 0.0
    nr_cotst: int = 0

    @property
    def net_inflow(self) -> float:
        return self.captc_dia - self.resg_dia


@dataclass
class EtfWeeklyFlow:
    """Consolidado semanal de captação de um ETF."""

    cnpj: str
    etf_name: str
    ticker: str | None
    category: str
    manager_name: str | None
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


def get_recent_months(count: int = 3, ref_date: date | None = None) -> list[str]:
    """Gera lista de sufixos YYYYMM para os últimos `count` meses."""
    ref = ref_date or date.today()
    months = []
    curr_year = ref.year
    curr_month = ref.month

    for _ in range(count):
        months.append(f"{curr_year:04d}{curr_month:02d}")
        curr_month -= 1
        if curr_month == 0:
            curr_month = 12
            curr_year -= 1

    months.reverse()
    return months


def fetch_inf_diario_month(config: Config, yyyymm: str, force: bool = False) -> Path | None:
    """Baixa o zip mensal do INF_DIARIO da CVM com cache condicional."""
    filename = f"inf_diario_fi_{yyyymm}.zip"
    dest = config.market_raw_dir / filename
    if dest.exists() and not force:
        return dest

    url = f"https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{yyyymm}.zip"

    ds = Dataset(
        key=f"inf_diario_{yyyymm}",
        url=url,
        filename=filename,
        description=f"Informe diário CVM {yyyymm}",
        members={"inf_diario": f"inf_diario_fi_{yyyymm}.csv"},
        approx_mb=11,
    )

    try:
        res = fetch(config, ds, force=force)
        return res.path
    except Exception:  # noqa: BLE001
        return None


_CNPJ_TRANS = str.maketrans("", "", "./- ")


def read_etf_daily_records(
    zip_path: Path, target_cnpjs: set[str]
) -> list[EtfDailyRecord]:
    """Lê o CSV do zip mensal do INF_DIARIO filtrando apenas os CNPJs de ETFs."""
    if not zip_path or not zip_path.exists() or not zipfile.is_zipfile(zip_path):
        return []

    records: list[EtfDailyRecord] = []

    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            return []

        raw_bytes = archive.read(csv_names[0])
        text = raw_bytes.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()

        if not lines:
            return []

        header = lines[0].split(";")
        header_map = {col.strip().upper(): idx for idx, col in enumerate(header)}

        def find_col(*names: str) -> int | None:
            for name in names:
                if name.upper() in header_map:
                    return header_map[name.upper()]
            return None

        idx_cnpj = find_col("CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO", "CNPJ_CLASSE", "CNPJ_FUNDO")
        idx_dt = find_col("DT_COMPTC")
        idx_total = find_col("VL_TOTAL")
        idx_quota = find_col("VL_QUOTA")
        idx_pl = find_col("VL_PATRIM_LIQ")
        idx_captc = find_col("CAPTC_DIA")
        idx_resg = find_col("RESG_DIA")
        idx_cotst = find_col("NR_COTST")

        if idx_cnpj is None or idx_dt is None:
            return []

        for line in lines[1:]:
            if not line:
                continue
            cols = line.split(";")
            if len(cols) <= idx_cnpj:
                continue
            cnpj_raw = cols[idx_cnpj]
            if not cnpj_raw:
                continue

            cnpj_clean = cnpj_raw.translate(_CNPJ_TRANS)
            if len(cnpj_clean) < 14:
                cnpj_clean = cnpj_clean.zfill(14)

            if cnpj_clean not in target_cnpjs:
                continue

            dt_str = cols[idx_dt] if len(cols) > idx_dt else None
            if not dt_str:
                continue
            try:
                dt = datetime.strptime(dt_str.strip(), "%Y-%m-%d").date()
            except ValueError:
                continue

            vl_total = parse_number(cols[idx_total]) if idx_total is not None and len(cols) > idx_total else 0.0
            vl_quota = parse_number(cols[idx_quota]) if idx_quota is not None and len(cols) > idx_quota else 0.0
            vl_pl = parse_number(cols[idx_pl]) if idx_pl is not None and len(cols) > idx_pl else 0.0
            captc = parse_number(cols[idx_captc]) if idx_captc is not None and len(cols) > idx_captc else 0.0
            resg = parse_number(cols[idx_resg]) if idx_resg is not None and len(cols) > idx_resg else 0.0

            cotst_raw = cols[idx_cotst] if idx_cotst is not None and len(cols) > idx_cotst else None
            try:
                cotst = int(float(cotst_raw)) if cotst_raw else 0
            except (ValueError, TypeError):
                cotst = 0

            records.append(
                EtfDailyRecord(
                    cnpj=cnpj_clean,
                    dt_comptc=dt,
                    vl_total=vl_total or 0.0,
                    vl_quota=vl_quota or 0.0,
                    vl_patrim_liq=vl_pl or 0.0,
                    captc_dia=captc or 0.0,
                    resg_dia=resg or 0.0,
                    nr_cotst=cotst,
                )
            )

    return records


def aggregate_etf_weekly_flows(
    catalog: dict[str, EtfMetadata], daily_records: list[EtfDailyRecord]
) -> list[EtfWeeklyFlow]:
    """Consolida os registros diários por ETF e por semana ISO."""
    # Agrupar por (cnpj, iso_year, iso_week)
    grouped: dict[tuple[str, int, int], list[EtfDailyRecord]] = defaultdict(list)

    for rec in daily_records:
        iso_year, iso_week, _ = rec.dt_comptc.isocalendar()
        grouped[(rec.cnpj, iso_year, iso_week)].append(rec)

    weekly_flows: list[EtfWeeklyFlow] = []

    for (cnpj, iso_year, iso_week), recs in grouped.items():
        if not recs:
            continue

        recs.sort(key=lambda r: r.dt_comptc)
        meta = catalog.get(cnpj)

        etf_name = meta.name if meta else cnpj
        ticker = meta.ticker if meta else None
        category = meta.category if meta else "Outros"
        manager = meta.manager_name if meta else None

        # Data de referência da semana (última data com dado na semana)
        week_end_date = recs[-1].dt_comptc
        week_key = f"{iso_year}-W{iso_week:02d}"

        captacao_bruta = sum(r.captc_dia for r in recs)
        resgates = sum(r.resg_dia for r in recs)
        captacao_liquida = captacao_bruta - resgates

        pl_inicio = recs[0].vl_patrim_liq
        pl_fim = recs[-1].vl_patrim_liq

        cotistas_inicio = recs[0].nr_cotst
        cotistas_fim = recs[-1].nr_cotst
        variacao_cotistas = cotistas_fim - cotistas_inicio

        quota_ini = recs[0].vl_quota
        quota_fim = recs[-1].vl_quota
        rentab = None
        if quota_ini and quota_ini > 0:
            rentab = round(((quota_fim / quota_ini) - 1.0) * 100.0, 4)

        weekly_flows.append(
            EtfWeeklyFlow(
                cnpj=cnpj,
                etf_name=etf_name,
                ticker=ticker,
                category=category,
                manager_name=manager,
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
            )
        )

    # Ordenar por semana decrescente e captação líquida decrescente
    weekly_flows.sort(key=lambda w: (w.iso_year, w.iso_week, w.captacao_liquida), reverse=True)
    return weekly_flows


def aggregate_category_weekly_flows(
    weekly_flows: list[EtfWeeklyFlow],
) -> list[CategoryWeeklySummary]:
    """Consolida os fluxos semanais por Categoria de ETF."""
    grouped: dict[tuple[str, int, int], list[EtfWeeklyFlow]] = defaultdict(list)

    for flow in weekly_flows:
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
