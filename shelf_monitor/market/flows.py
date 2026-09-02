"""Captacao e resgate por classe, a partir do informe diario da CVM.

O informe diario e a unica fonte publica de fluxo financeiro por fundo:
CAPTC_DIA, RESG_DIA, VL_PATRIM_LIQ e NR_COTST, uma linha por classe (ou
subclasse) por dia util, um zip por mes. O CNPJ reportado e o da CLASSE -
o mesmo grao do mercado.csv e a primeira chave do join do enrich, entao a
captacao chega as prateleiras pelo caminho que ja existe.

Duas decisoes governam o modulo:

1. O agregado e mensal por classe. O dia a dia e ruido para uma leitura
   competitiva; o mes fecha a conta de captacao liquida e reduz ~500 mil
   linhas/mes a uma por classe.
2. O mes corrente e sempre PARCIAL e fica fora das janelas moveis (1m/3m/12m):
   comparar um mes de 3 dias uteis com meses de 21 produziria um ranking
   mentiroso. Ele entra no CSV rotulado, para quem quiser a leitura quente.
3. A cota (VL_QUOTA) e de UMA serie por classe - a subclasse representativa,
   de maior PL no ultimo dia do mes. Subclasses tem taxas e cotas distintas;
   misturar duas num par de dias produziria retorno-lixo. O retorno entre
   meses so sai quando a serie e a mesma nos dois pontos.
"""
from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from array import array
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market.datasets import INF_DIARIO_COLUMNS, informe_diario_dataset
from shelf_monitor.market.fetch import CacheReason, fetch
from shelf_monitor.market.models import FlowsResult, MonthlyFlow
from shelf_monitor.market.parse import parse_date
from shelf_monitor.normalize import (
    format_cnpj,
    get_column,
    normalize_cnpj,
    normalize_text,
    parse_int,
    parse_number,
)

FLOWS_FILENAME = "captacao.csv"

# 13 meses fechados + o corrente (parcial). Retorno e variacao de cotistas de
# 12 meses precisam do valor de fim do 13o mes fechado - a base da janela -
# e nada disso pode depender do dia do mes em que o comando roda.
DEFAULT_MONTHS = 14

# Um log-retorno diario acima disto nao e mercado: e grupamento de cota ou
# dado sujo da CVM. O par sai da conta em vez de virar volatilidade fantasma.
_MAX_ABS_DAILY_LOG_RETURN = 0.7

_HEADERS = (
    "cnpj_classe",
    "mes",
    "captacao",
    "resgates",
    "captacao_liquida",
    "pl_fim_mes",
    "cotistas_fim_mes",
    "data_ultimo_informe",
    "dias_informados",
    "mes_parcial",
    "cota_fim_mes",
    "cota_serie",
    "ret_diario_soma",
    "ret_diario_soma2",
    "ret_diario_n",
)


def month_window(today: date | None = None, months: int = DEFAULT_MONTHS) -> list[str]:
    """['2025-09', ..., '2026-09']: N meses terminando no corrente."""
    anchor = today or date.today()
    result: list[str] = []
    year, month = anchor.year, anchor.month
    for _ in range(max(months, 1)):
        result.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(result))


def _csv_member(archive: zipfile.ZipFile, needle: str) -> str:
    """Nome do CSV dentro do zip; o especifico primeiro, senao o unico."""
    names = {name.lower(): name for name in archive.namelist()}
    csvs = [orig for low, orig in names.items() if low.endswith(".csv")]
    for lowered, original in names.items():
        if needle in lowered and lowered.endswith(".csv"):
            return original
    if len(csvs) == 1:
        return csvs[0]
    raise SourceUnavailable(
        f"{needle}.csv ausente no zip (contem: {sorted(names.values())})"
    )


def _member_encoding(head: bytes) -> str:
    """BOM presente pede utf-8-sig; senao latin-1, que nunca falha.

    O conteudo do informe e numerico/ASCII, entao latin-1 le qualquer arquivo
    sem levantar. O unico estrago possivel e um BOM utf-8 grudado no nome da
    primeira coluna - e e exatamente isso que a espiada nos 3 bytes evita.
    """
    return "utf-8-sig" if head.startswith(b"\xef\xbb\xbf") else "latin-1"


def parse_informe(
    path: Path, month: str, *, partial: bool = False
) -> tuple[dict[str, MonthlyFlow], list[str]]:
    """Zip mensal -> agregado por CNPJ de classe. (flows, colunas desconhecidas).

    Streaming direto do membro do zip, sem materializar os ~70 MB
    descomprimidos. Nao depende da ordem do arquivo: PL/cotistas valem na
    maior DT_COMPTC vista, somando linhas do mesmo dia (subclasses partilham
    o CNPJ da classe) e ignorando dias anteriores.
    """
    needle = f"inf_diario_fi_{month.replace('-', '')}"
    flows: dict[str, MonthlyFlow] = {}
    day_masks: dict[str, int] = {}
    # Cota por (classe, subclasse) e dia do mes. array('d') de 32 posicoes:
    # ~25 MB transitorios para ~80 mil series, em vez dos ~80 MB de listas.
    quotas: dict[tuple[str, str], array] = {}
    # PL de cada serie na maior data dela - escolhe a serie representativa.
    series_pl: dict[tuple[str, str], tuple[date, float]] = {}
    unknown: list[str] = []

    with zipfile.ZipFile(path) as archive:
        member = _csv_member(archive, needle)
        with archive.open(member) as probe:
            encoding = _member_encoding(probe.read(3))
        with archive.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding=encoding, newline="")
            reader = csv.DictReader(text, delimiter=";")

            known = {normalize_text(name) for name in INF_DIARIO_COLUMNS}
            unknown = sorted(
                name
                for name in (reader.fieldnames or [])
                if name and normalize_text(name) not in known
            )

            for row in reader:
                cnpj = normalize_cnpj(
                    get_column(row, "CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO")
                )
                if not cnpj:
                    continue
                flow = flows.get(cnpj)
                if flow is None:
                    flow = flows[cnpj] = MonthlyFlow(
                        cnpj=cnpj, month=month, partial=partial
                    )

                flow.inflow += parse_number(get_column(row, "CAPTC_DIA")) or 0.0
                flow.outflow += parse_number(get_column(row, "RESG_DIA")) or 0.0

                day = parse_date(get_column(row, "DT_COMPTC"))
                if day is None:
                    continue
                mask = day_masks.get(cnpj, 0)
                bit = 1 << day.day
                if not mask & bit:
                    day_masks[cnpj] = mask | bit
                    flow.days += 1

                net_worth = parse_number(get_column(row, "VL_PATRIM_LIQ"))
                shareholders = parse_int(get_column(row, "NR_COTST"))
                if flow.last_date is None or day > flow.last_date:
                    flow.last_date = day
                    flow.net_worth = net_worth
                    flow.shareholders = shareholders
                elif day == flow.last_date:
                    # Outra subclasse da mesma classe no mesmo dia: o PL da
                    # classe e a soma das linhas do dia.
                    if net_worth is not None:
                        flow.net_worth = (flow.net_worth or 0.0) + net_worth
                    if shareholders is not None:
                        flow.shareholders = (flow.shareholders or 0) + shareholders

                # A cota e por serie (classe, subclasse) - nunca somada.
                series = (cnpj, get_column(row, "ID_SUBCLASSE") or "")
                quota = parse_number(get_column(row, "VL_QUOTA"))
                if quota is not None and quota > 0:
                    vector = quotas.get(series)
                    if vector is None:
                        vector = quotas[series] = array("d", [math.nan] * 32)
                    vector[day.day] = quota
                seen = series_pl.get(series)
                if seen is None or day > seen[0]:
                    series_pl[series] = (day, net_worth or 0.0)

    _finish_quota_series(flows, quotas, series_pl)
    return flows, unknown


def _finish_quota_series(
    flows: dict[str, MonthlyFlow],
    quotas: dict[tuple[str, str], array],
    series_pl: dict[tuple[str, str], tuple[date, float]],
) -> None:
    """Escolhe a serie representativa de cada classe e fecha cota e retornos.

    Representativa = a subclasse de maior PL na ultima data da classe (empate:
    menor ID). Se ela nao tem cota nesse dia, `quota_end` fica None - um dia
    de desalinhamento silencioso seria pior do que a lacuna declarada.
    """
    by_class: dict[str, list[tuple[str, tuple[str, str]]]] = {}
    for series in quotas:
        by_class.setdefault(series[0], []).append((series[1], series))

    for cnpj, candidates in by_class.items():
        flow = flows.get(cnpj)
        if flow is None or flow.last_date is None:
            continue
        last = flow.last_date

        def rank(item: tuple[str, tuple[str, str]]) -> tuple[float, str]:
            sub_id, series = item
            seen = series_pl.get(series)
            pl = seen[1] if seen is not None and seen[0] == last else -1.0
            return (-pl, sub_id)

        sub_id, series = min(candidates, key=rank)
        vector = quotas[series]
        flow.quota_series = sub_id
        end = vector[last.day]
        flow.quota_end = None if math.isnan(end) else end
        flow.ret_sum, flow.ret_sumsq, flow.ret_n = _daily_return_sums(vector)


def _daily_return_sums(vector: array) -> tuple[float, float, int]:
    """(soma, soma dos quadrados, n) dos log-retornos entre dias informados
    consecutivos. Um salto acima do limite e descartado, nao suavizado."""
    total = 0.0
    total_sq = 0.0
    count = 0
    previous: float | None = None
    for day in range(1, 32):
        quota = vector[day]
        if math.isnan(quota) or quota <= 0:
            continue
        if previous is not None:
            log_return = math.log(quota / previous)
            if abs(log_return) <= _MAX_ABS_DAILY_LOG_RETURN:
                total += log_return
                total_sq += log_return * log_return
                count += 1
        previous = quota
    return total, total_sq, count


def import_flows(
    config: Config,
    months: int = DEFAULT_MONTHS,
    local_dir: Path | None = None,
    force: bool = False,
    today: date | None = None,
) -> FlowsResult:
    """Baixa e agrega a janela de meses. Um mes que falha nao derruba os outros.

    `today` e injetavel para teste deterministico; em producao e a data do
    relogio. O zip do mes corrente ainda nao publicado (404 nos primeiros
    dias uteis) e o caso tipico de missing_months, nao um erro.
    """
    anchor = today or date.today()
    result = FlowsResult(
        imported_at=date.today(), window=month_window(anchor, months)
    )
    current = f"{anchor.year:04d}-{anchor.month:02d}"

    for month in result.window:
        dataset = informe_diario_dataset(month.replace("-", ""))

        if local_dir is not None:
            path = local_dir / dataset.filename
            if not path.exists():
                result.missing_months.append(month)
                result.notes.append(f"{dataset.key}: ausente em {local_dir}")
                continue
            result.sources[month] = str(path)
        else:
            try:
                fetched = fetch(config, dataset, force=force)
            except SourceUnavailable as exc:
                result.missing_months.append(month)
                result.notes.append(f"{dataset.key}: {exc}")
                continue
            result.sources[month] = dataset.url
            if fetched.from_cache and fetched.reason is CacheReason.SOURCE_DOWN:
                # Dado de ontem nunca se passa por dado de hoje.
                result.stale_months.append(month)
                note = f"{dataset.key}: {fetched.reason.value}, reusado do disco"
                if fetched.detail:
                    note += f" ({fetched.detail})"
                result.notes.append(note)
            path = fetched.path

        try:
            flows, unknown = parse_informe(path, month, partial=month == current)
        except (SourceUnavailable, zipfile.BadZipFile, OSError) as exc:
            result.missing_months.append(month)
            result.notes.append(f"{dataset.key}: {exc}")
            continue

        for flow in flows.values():
            result.flows[(flow.cnpj, month)] = flow
        for column in unknown:
            if column not in result.unknown_columns:
                result.unknown_columns.append(column)

    return result


def _cell(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _precise_cell(value: float | None) -> str:
    """Cota e somas de retorno precisam de todas as casas - 2 decimais
    arredondariam um retorno diario inteiro para zero."""
    return "" if value is None else format(value, ".12g")


def merge_manifest_section(config: Config, section: str, payload: dict) -> None:
    """Atualiza uma chave do _manifest.json preservando as demais.

    O manifesto e escrito por varios produtores (cadastro, captacao, CDI);
    cada um regrava apenas a propria secao, senao a importacao de um apagaria
    o registro do outro.
    """
    path = config.market_dir / "_manifest.json"
    manifest: dict = {}
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            manifest = {}
    manifest[section] = payload
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_flows(config: Config, result: FlowsResult) -> Path:
    """Grava captacao.csv (uma linha por classe x mes) e a secao no manifesto.

    Linhas ordenadas por (cnpj, mes) e sem timestamp no CSV: um rerun sem
    mudanca na origem produz bytes identicos, e o diff denuncia so o que
    mudou de fato.
    """
    config.market_dir.mkdir(parents=True, exist_ok=True)
    path = config.market_dir / FLOWS_FILENAME

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_HEADERS), delimiter=";")
        writer.writeheader()
        for (cnpj, month) in sorted(result.flows):
            flow = result.flows[(cnpj, month)]
            writer.writerow(
                {
                    "cnpj_classe": format_cnpj(cnpj),
                    "mes": month,
                    "captacao": _cell(flow.inflow),
                    "resgates": _cell(flow.outflow),
                    "captacao_liquida": _cell(flow.net),
                    "pl_fim_mes": _cell(flow.net_worth),
                    "cotistas_fim_mes": (
                        "" if flow.shareholders is None else str(flow.shareholders)
                    ),
                    "data_ultimo_informe": (
                        flow.last_date.isoformat() if flow.last_date else ""
                    ),
                    "dias_informados": str(flow.days),
                    "mes_parcial": "S" if flow.partial else "N",
                    "cota_fim_mes": _precise_cell(flow.quota_end),
                    "cota_serie": flow.quota_series,
                    "ret_diario_soma": _precise_cell(flow.ret_sum),
                    "ret_diario_soma2": _precise_cell(flow.ret_sumsq),
                    "ret_diario_n": str(flow.ret_n),
                }
            )

    merge_manifest_section(
        config,
        "captacao",
        {
            "imported_at": result.imported_at.isoformat(),
            "window": result.window,
            "counts": result.counts(),
            "sources": result.sources,
            "missing_months": result.missing_months,
            "stale_months": result.stale_months,
            "unknown_columns": result.unknown_columns,
            "notes": result.notes,
        },
    )
    return path


def run_flows_import(
    config: Config,
    months: int = DEFAULT_MONTHS,
    local_dir: Path | None = None,
    force: bool = False,
) -> tuple[FlowsResult, Path | None]:
    """Importa e grava. None no caminho quando nenhum mes veio."""
    result = import_flows(config, months=months, local_dir=local_dir, force=force)
    if not result.flows:
        return result, None
    return result, write_flows(config, result)


# --- leitura: janelas moveis por classe ---------------------------------------


# Abaixo disto a volatilidade e ruido estatistico, nao risco: ~3 meses de
# pares diarios e o minimo para o desvio-padrao dizer alguma coisa.
_MIN_VOL_OBS = 60
_TRADING_DAYS = 252
# Distancias (em meses fechados a partir do window_end) que ancoram as
# janelas: 0 = o proprio window_end; 1/3/12 = a base de cada janela.
_ANCHORS = (0, 1, 3, 12)


@dataclass
class FlowSummary:
    """Janelas moveis pre-computadas por classe, prontas para o enrich.

    Todas as janelas sao ancoradas no MESMO mes - `window_end`, o ultimo mes
    fechado da base - e nao no ultimo mes de cada classe: e o que permite
    comparar o retorno de um fundo com o CDI da mesma janela e um fundo com
    outro. Captacao soma o que ha (fluxo antes de existir e zero, legitimo);
    retorno e variacao de cotistas sao ESTRITOS: sem o valor da base
    exatamente k meses antes, ficam None, e `quota_months` explica o porque.
    """

    cnpj: str
    window_end: str | None = None
    net_1m: float | None = None
    net_3m: float | None = None
    net_12m: float | None = None
    months_12m: int = 0  # meses fechados presentes na janela de 12
    current_partial: float | None = None  # liquido do mes corrente, se presente
    net_worth: float | None = None  # PL do informe mais recente
    shareholders: int | None = None
    as_of: date | None = None  # data do ultimo informe visto
    quota_months: int = 0  # meses fechados com cota valida (ate 13)
    return_1m: float | None = None
    return_3m: float | None = None
    return_12m: float | None = None
    vol_12m: float | None = None  # anualizada, sobre os retornos diarios
    vol_obs: int = 0  # pares diarios que entraram na volatilidade
    shareholders_delta_1m: int | None = None
    shareholders_delta_3m: int | None = None
    shareholders_delta_12m: int | None = None
    shareholders_start_12m: int | None = None

    @property
    def ticket(self) -> float | None:
        """PL por cotista do informe mais fresco - o porte do investidor."""
        if self.net_worth is None or not self.shareholders:
            return None
        return self.net_worth / self.shareholders


@dataclass
class _Accumulator:
    """Estado por classe durante a leitura em streaming do captacao.csv."""

    net_1m: float | None = None
    net_3m: float | None = None
    net_12m: float = 0.0
    months_12m: int = 0
    ret_sum: float = 0.0
    ret_sumsq: float = 0.0
    ret_n: int = 0
    quota_months: int = 0
    quotas: dict[int, tuple[float, str]] = field(default_factory=dict)
    shareholders: dict[int, int] = field(default_factory=dict)
    partial_net: float | None = None
    latest: tuple[date, float | None, int | None] | None = None


def months_between(later: str, earlier: str) -> int:
    """Meses de `earlier` ate `later` ("YYYY-MM"); negativo se invertidos."""
    y1, m1 = later.split("-")
    y2, m2 = earlier.split("-")
    return (int(y1) * 12 + int(m1)) - (int(y2) * 12 + int(m2))


def shift_month(month: str, delta: int) -> str:
    """"2026-07" - 3 -> "2026-04"; o inverso de months_between."""
    year, mon = month.split("-")
    index = int(year) * 12 + int(mon) - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def last_closed_month(config: Config) -> str | None:
    """Ultimo mes FECHADO da base de captacao - a ancora de todas as janelas.

    Le a secao `captacao` do manifesto: o `month_window` sempre termina no
    mes corrente (parcial), entao o ultimo fechado e o ultimo dos anteriores
    que nao esta em `missing_months`. Sem manifesto, varre o CSV.
    """
    manifest_path = config.market_dir / "_manifest.json"
    if manifest_path.is_file():
        try:
            section = json.loads(manifest_path.read_text(encoding="utf-8")).get(
                "captacao", {}
            )
        except (ValueError, OSError):
            section = {}
        window = list(section.get("window") or [])
        missing = set(section.get("missing_months") or [])
        closed = [month for month in window[:-1] if month not in missing]
        if closed:
            return closed[-1]

    path = config.market_dir / FLOWS_FILENAME
    if not path.is_file():
        return None
    latest: str | None = None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if (row.get("mes_parcial") or "").strip().upper() == "S":
                continue
            month = (row.get("mes") or "").strip()
            if month and (latest is None or month > latest):
                latest = month
    return latest


def load_summaries(config: Config) -> dict[str, FlowSummary] | None:
    """Le captacao.csv em streaming e computa as janelas. None sem base.

    Um acumulador pequeno por classe, nunca o dicionario classe x mes: com
    seis campos por mes e ~1 milhao de linhas, materializar custaria
    centenas de MB. Colunas ausentes (captacao.csv importado antes da cota)
    viram None nos retornos; captacao, ticket e cotistas seguem normais.
    """
    path = config.market_dir / FLOWS_FILENAME
    if not path.is_file():
        return None
    window_end = last_closed_month(config)

    accumulators: dict[str, _Accumulator] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            cnpj = normalize_cnpj(row.get("cnpj_classe"))
            month = (row.get("mes") or "").strip()
            if not cnpj or not month:
                continue
            acc = accumulators.get(cnpj)
            if acc is None:
                acc = accumulators[cnpj] = _Accumulator()

            net = parse_number(row.get("captacao_liquida")) or 0.0
            if (row.get("mes_parcial") or "").strip().upper() == "S":
                acc.partial_net = net
            elif window_end is not None:
                k = months_between(window_end, month)
                if 0 <= k <= 11:
                    acc.net_12m += net
                    acc.months_12m += 1
                    if k == 0:
                        acc.net_1m = net
                    if k <= 2:
                        acc.net_3m = (acc.net_3m or 0.0) + net
                    acc.ret_sum += parse_number(row.get("ret_diario_soma")) or 0.0
                    acc.ret_sumsq += parse_number(row.get("ret_diario_soma2")) or 0.0
                    acc.ret_n += parse_int(row.get("ret_diario_n")) or 0
                if 0 <= k <= 12:
                    quota = parse_number(row.get("cota_fim_mes"))
                    if quota is not None and quota > 0:
                        acc.quota_months += 1
                        if k in _ANCHORS:
                            acc.quotas[k] = (quota, (row.get("cota_serie") or "").strip())
                    if k in _ANCHORS:
                        shareholders = parse_int(row.get("cotistas_fim_mes"))
                        if shareholders is not None:
                            acc.shareholders[k] = shareholders

            as_of = parse_date(row.get("data_ultimo_informe"))
            if as_of is not None and (acc.latest is None or as_of > acc.latest[0]):
                acc.latest = (
                    as_of,
                    parse_number(row.get("pl_fim_mes")),
                    parse_int(row.get("cotistas_fim_mes")),
                )

    return {
        cnpj: _finish_summary(cnpj, acc, window_end)
        for cnpj, acc in accumulators.items()
    }


def _finish_summary(cnpj: str, acc: _Accumulator, window_end: str | None) -> FlowSummary:
    """Deriva as janelas do acumulador - as formulas moram aqui."""
    summary = FlowSummary(cnpj=cnpj, window_end=window_end)
    summary.net_1m = acc.net_1m
    summary.net_3m = acc.net_3m
    summary.net_12m = acc.net_12m if acc.months_12m else None
    summary.months_12m = acc.months_12m
    summary.current_partial = acc.partial_net
    summary.quota_months = acc.quota_months
    if acc.latest is not None:
        summary.as_of, summary.net_worth, summary.shareholders = acc.latest

    # Retorno: cota do window_end sobre a cota-base, so na MESMA serie.
    end = acc.quotas.get(0)
    for k, attr in ((1, "return_1m"), (3, "return_3m"), (12, "return_12m")):
        base = acc.quotas.get(k)
        if end is not None and base is not None and end[1] == base[1]:
            setattr(summary, attr, end[0] / base[0] - 1.0)

    # Volatilidade: desvio-padrao pooled dos log-retornos diarios, anualizado.
    summary.vol_obs = acc.ret_n
    if acc.ret_n >= _MIN_VOL_OBS:
        variance = (acc.ret_sumsq - acc.ret_sum * acc.ret_sum / acc.ret_n) / (
            acc.ret_n - 1
        )
        summary.vol_12m = math.sqrt(max(0.0, variance)) * math.sqrt(_TRADING_DAYS)

    current = acc.shareholders.get(0)
    for k, attr in (
        (1, "shareholders_delta_1m"),
        (3, "shareholders_delta_3m"),
        (12, "shareholders_delta_12m"),
    ):
        base_count = acc.shareholders.get(k)
        if current is not None and base_count is not None:
            setattr(summary, attr, current - base_count)
    summary.shareholders_start_12m = acc.shareholders.get(12)
    return summary


# --- visao de mercado: captacao por gestora e por casa ------------------------


def summarize_watchlist_managers(config: Config) -> dict | None:
    """Market share de captacao das gestoras da watchlist, casa a casa.

    captacao.csv x mercado.csv por CNPJ de classe -> agrega por gestora ->
    o WatchlistMatcher atribui a casa. E o mercado INTEIRO, nao a prateleira:
    mede quanto as gestoras de cada casa captaram, esteja o fundo em
    prateleira propria, de terceiros ou fora delas.

    None quando falta qualquer uma das duas bases - a visao nunca sai pela
    metade se passando por inteira.
    """
    from shelf_monitor.collectors.cvm import WatchlistMatcher

    summaries = load_summaries(config)
    mercado = config.market_dir / "mercado.csv"
    if not summaries or not mercado.is_file():
        return None

    managers_by_class: dict[str, tuple[str | None, str | None]] = {}
    with mercado.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            cnpj = normalize_cnpj(row.get("cnpj"))
            if cnpj and cnpj not in managers_by_class:
                managers_by_class[cnpj] = (
                    (row.get("gestor") or "").strip() or None,
                    normalize_cnpj(row.get("cnpj_gestor")),
                )

    def _empty() -> dict:
        return {
            "net_3m": 0.0,
            "net_12m": 0.0,
            "pl": 0.0,
            "classes": 0,
            "shareholders": 0,
            # Variacao de cotistas em 12m, somada so nas classes que tem a
            # base - classes_with_delta diz sobre quantas o numero fala.
            "shareholders_delta_12m": 0,
            "classes_with_delta": 0,
        }

    def _add(bucket: dict, summary: FlowSummary) -> None:
        bucket["net_3m"] += summary.net_3m or 0.0
        bucket["net_12m"] += summary.net_12m or 0.0
        bucket["pl"] += summary.net_worth or 0.0
        bucket["classes"] += 1
        bucket["shareholders"] += summary.shareholders or 0
        if summary.shareholders_delta_12m is not None:
            bucket["shareholders_delta_12m"] += summary.shareholders_delta_12m
            bucket["classes_with_delta"] += 1

    market = _empty()
    by_manager: dict[str, dict] = {}
    matched_classes = 0
    as_of: date | None = None
    window_months = 0
    window_end: str | None = None

    for cnpj, summary in summaries.items():
        _add(market, summary)
        if summary.as_of and (as_of is None or summary.as_of > as_of):
            as_of = summary.as_of
        window_months = max(window_months, summary.months_12m)
        window_end = window_end or summary.window_end

        manager_name, manager_cnpj = managers_by_class.get(cnpj, (None, None))
        if not manager_name and not manager_cnpj:
            continue
        matched_classes += 1
        key = manager_cnpj or normalize_text(manager_name)
        bucket = by_manager.setdefault(
            key, {**_empty(), "name": manager_name, "cnpj": manager_cnpj}
        )
        _add(bucket, summary)

    matcher = WatchlistMatcher()
    houses: dict[str, dict] = {}
    for bucket in by_manager.values():
        hit = matcher.match(bucket["name"], bucket["cnpj"])
        if hit is None:
            continue
        house, _ = hit
        entry = houses.setdefault(
            house,
            {
                "house": house,
                "label": PLATFORMS[house].label if house in PLATFORMS else house,
                **_empty(),
                "managers": [],
            },
        )
        for key in (
            "net_3m", "net_12m", "pl", "classes", "shareholders",
            "shareholders_delta_12m", "classes_with_delta",
        ):
            entry[key] += bucket[key]
        entry["managers"].append(
            {
                "name": bucket["name"],
                "cnpj": bucket["cnpj"],
                "net_3m": bucket["net_3m"],
                "net_12m": bucket["net_12m"],
                "pl": bucket["pl"],
                "classes": bucket["classes"],
                "shareholders": bucket["shareholders"],
                "shareholders_delta_12m": bucket["shareholders_delta_12m"],
                "classes_with_delta": bucket["classes_with_delta"],
            }
        )

    ordered = []
    for house in matcher.watchlist:
        entry = houses.get(house)
        if entry is None:
            continue
        entry["managers"].sort(key=lambda m: m["net_12m"], reverse=True)
        entry["managers"] = entry["managers"][:5]
        ordered.append(entry)

    return {
        "as_of": as_of.isoformat() if as_of else None,
        "window_end": window_end,
        "window_months": window_months,
        "market": market,
        "houses": ordered,
        "coverage": {
            "classes_com_gestora": matched_classes,
            "classes_total": len(summaries),
        },
    }
