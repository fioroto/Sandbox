"""CDI diario do Banco Central como benchmark das janelas de rentabilidade.

Um retorno de fundo so significa alguma coisa contra o CDI da MESMA janela.
A serie vem do SGS do BCB (serie 12, taxa diaria em % a.d.), pequena o
bastante para um JSON de alguns KB, e segue o mesmo padrao captura -> leitura
da captacao: `run_cdi_import` grava data/market/cdi.csv (no `market` e no
auto-import do run), `cdi_block` so le disco - o build_report nunca toca a
rede.

Sem cdi.csv nao ha benchmark, e nenhuma superficie inventa um "% do CDI".
"""
from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable, build_session, get_json
from shelf_monitor.market.flows import (
    DEFAULT_MONTHS,
    merge_manifest_section,
    month_window,
    shift_month,
)
from shelf_monitor.market.parse import parse_date
from shelf_monitor.normalize import parse_number

BCB_SGS_CDI_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
CDI_FILENAME = "cdi.csv"
CDI_SOURCE_LABEL = "BCB SGS 12 (CDI diario)"

# Um mes util tem ~21 dias; abaixo disto a serie do mes esta furada e a janela
# acumulada mentiria por omissao - melhor None do que um CDI "de 8 dias".
_MIN_RATES_PER_MONTH = 15


def _parse_sgs(payload: object) -> dict[date, float]:
    """[{"data": "dd/mm/aaaa", "valor": "0.05"}] -> {date: taxa}."""
    if not isinstance(payload, list):
        raise SourceUnavailable("resposta do SGS nao e uma lista de taxas")
    rates: dict[date, float] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        day = parse_date(item.get("data"))
        value = parse_number(item.get("valor"))
        if day is not None and value is not None:
            rates[day] = value
    if not rates:
        raise SourceUnavailable("serie do CDI vazia no periodo pedido")
    return rates


def fetch_cdi(
    config: Config,
    start: date,
    end: date,
    *,
    local_path: Path | None = None,
) -> dict[date, float]:
    """Taxas diarias entre start e end. JSON local (formato do SGS) ou BCB."""
    if local_path is not None:
        try:
            payload = json.loads(local_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SourceUnavailable(f"{local_path}: {exc}") from exc
    else:
        session = build_session(config)
        payload = get_json(
            session,
            BCB_SGS_CDI_URL,
            config,
            params={
                "formato": "json",
                "dataInicial": start.strftime("%d/%m/%Y"),
                "dataFinal": end.strftime("%d/%m/%Y"),
            },
        )
    rates = _parse_sgs(payload)
    return {day: rate for day, rate in rates.items() if start <= day <= end}


def load_cdi(config: Config) -> dict[date, float] | None:
    """Le cdi.csv; None quando nao ha serie em disco."""
    path = config.market_dir / CDI_FILENAME
    if not path.is_file():
        return None
    rates: dict[date, float] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            day = parse_date(row.get("data"))
            value = parse_number(row.get("taxa_dia"))
            if day is not None and value is not None:
                rates[day] = value
    return rates


def write_cdi(
    config: Config, rates: dict[date, float], notes: list[str], source: str
) -> Path:
    """Grava cdi.csv ordenado e a secao `cdi` do manifesto."""
    config.market_dir.mkdir(parents=True, exist_ok=True)
    path = config.market_dir / CDI_FILENAME
    ordered = sorted(rates.items())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["data", "taxa_dia"])
        for day, rate in ordered:
            writer.writerow([day.isoformat(), format(rate, ".12g")])
    merge_manifest_section(
        config,
        "cdi",
        {
            "imported_at": date.today().isoformat(),
            "source": source,
            "first": ordered[0][0].isoformat() if ordered else None,
            "last": ordered[-1][0].isoformat() if ordered else None,
            "count": len(ordered),
            "notes": notes,
        },
    )
    return path


def run_cdi_import(
    config: Config,
    months: int = DEFAULT_MONTHS,
    local_dir: Path | None = None,
    force: bool = False,
    today: date | None = None,
) -> tuple[Path | None, list[str]]:
    """Baixa (incrementalmente) a serie da janela e grava. (caminho, notas).

    So pede ao BCB o que falta depois da ultima taxa em disco; CDI de mes
    fechado nao muda. Origem fora do ar mantem o arquivo anterior com nota -
    e nada em disco mais falha devolve (None, notas), nunca um CSV vazio.
    """
    anchor = today or date.today()
    window = month_window(anchor, months)
    start = date(int(window[0][:4]), int(window[0][5:7]), 1)
    existing = {} if force else (load_cdi(config) or {})
    notes: list[str] = []

    if existing and not force:
        start = max(start, max(existing) + timedelta(days=1))
        if start > anchor:
            notes.append("cdi: serie em dia, nada a baixar")
            return write_cdi(config, existing, notes, "disco"), notes

    local_path = (local_dir / "cdi.json") if local_dir is not None else None
    source = str(local_path) if local_path is not None else BCB_SGS_CDI_URL
    try:
        fetched = fetch_cdi(config, start, anchor, local_path=local_path)
    except SourceUnavailable as exc:
        notes.append(f"cdi: {exc}")
        if not existing:
            return None, notes
        notes.append("cdi: origem indisponivel, mantido o arquivo anterior")
        return write_cdi(config, existing, notes, "disco"), notes

    existing.update(fetched)
    notes.append(
        f"cdi: {len(fetched)} taxas de {start.isoformat()} a {anchor.isoformat()}"
    )
    return write_cdi(config, existing, notes, source), notes


def cdi_block(config: Config, window_end: str | None) -> dict | None:
    """CDI acumulado nas janelas de meses fechados que terminam em window_end.

    Cada janela exige todos os seus meses com pelo menos 15 taxas; um mes
    furado zera a janela inteira (None) em vez de acumular por cima do buraco.
    None sem serie em disco ou sem window_end - o benchmark nunca sai pela
    metade se passando por inteiro.
    """
    if not window_end:
        return None
    rates = load_cdi(config)
    if not rates:
        return None

    by_month: dict[str, list[float]] = {}
    for day, rate in rates.items():
        by_month.setdefault(f"{day.year:04d}-{day.month:02d}", []).append(rate)

    windows: dict[str, float | None] = {}
    for length, key in ((1, "1m"), (3, "3m"), (12, "12m")):
        accumulated = 1.0
        complete = True
        for offset in range(length):
            month_rates = by_month.get(shift_month(window_end, -offset), [])
            if len(month_rates) < _MIN_RATES_PER_MONTH:
                complete = False
                break
            for rate in month_rates:
                accumulated *= 1.0 + rate / 100.0
        windows[key] = (accumulated - 1.0) if complete else None

    return {
        "source": CDI_SOURCE_LABEL,
        "window_end": window_end,
        "last_rate": max(rates).isoformat(),
        "windows": windows,
    }
