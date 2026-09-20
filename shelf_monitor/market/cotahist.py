"""COTAHIST - cotação e liquidez diária de todo ativo listado na B3, ETFs inclusive.

Arquivo público, sem autenticação, publicado pela B3 ao fim de cada pregão:

    https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_D<ddmmaaaa>.ZIP   (dia)
    https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_M<mmaaaa>.ZIP     (mês)
    https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A<aaaa>.ZIP       (ano)

Layout de largura fixa (245 caracteres por linha, registro "01"), estável há
décadas. As posições abaixo vêm da biblioteca aberta `mercados` (PythonicCafe),
que lê o mesmo arquivo; preços têm duas casas decimais implícitas (centavos).

O que o COTAHIST responde sobre um ETF: preço, volume financeiro, número de
negócios e quantidade negociada por dia - liquidez, não patrimônio. Cruzado com
a cota patrimonial do informe diário (Fundos.NET) dá o prêmio/desconto; sozinho,
é o que diz se um ETF de renda fixa que captou também negocia.
"""
from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable, build_session, get

COTAHIST_BASE = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/"

# (início, fim) em índice zero, como fatias de string.
LAYOUT = {
    "tipreg": (0, 2),
    "data": (2, 10),
    "codbdi": (10, 12),
    "codneg": (12, 24),
    "tpmerc": (24, 27),
    "nomres": (27, 39),
    "especi": (39, 49),
    "prazot": (49, 52),
    "modref": (52, 56),
    "preabe": (56, 69),
    "premax": (69, 82),
    "premin": (82, 95),
    "premed": (95, 108),
    "preult": (108, 121),
    "preofc": (121, 134),
    "preofv": (134, 147),
    "totneg": (147, 152),
    "quatot": (152, 170),
    "voltot": (170, 188),
    "preexe": (188, 201),
    "indopc": (201, 202),
    "datven": (202, 210),
    "fatcot": (210, 217),
    "ptoexe": (217, 230),
    "codisi": (230, 242),
    "dismes": (242, 245),
}
LINE_LENGTH = 245
# Mercado à vista, lote padrão: é onde ETF negocia (010 = vista; BDI 02 = lote padrão).
MARKET_SPOT = 10
BDI_LOTE_PADRAO = 2


def url_for_day(day: date) -> str:
    return f"{COTAHIST_BASE}COTAHIST_D{day:%d%m%Y}.ZIP"


def url_for_month(day: date) -> str:
    return f"{COTAHIST_BASE}COTAHIST_M{day:%m%Y}.ZIP"


def url_for_year(day: date) -> str:
    return f"{COTAHIST_BASE}COTAHIST_A{day:%Y}.ZIP"


def _centavos(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return int(text) / 100.0
    except ValueError:
        return None


def _int(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


@dataclass
class CotacaoDia:
    """Uma linha do COTAHIST: um ativo num pregão."""

    data: date
    codneg: str
    nomres: str
    especi: str
    tpmerc: int | None
    codbdi: int | None
    abertura: float | None
    maximo: float | None
    minimo: float | None
    medio: float | None
    fechamento: float | None
    negocios: int | None
    quantidade: int | None
    volume: float | None
    isin: str
    fator_cotacao: int | None

    @property
    def is_lote_padrao_vista(self) -> bool:
        return self.tpmerc == MARKET_SPOT and self.codbdi == BDI_LOTE_PADRAO

    def to_row(self) -> list:
        return [
            self.data.isoformat(), self.codneg, self.nomres, self.especi, self.tpmerc,
            self.codbdi, self.abertura, self.maximo, self.minimo, self.medio,
            self.fechamento, self.negocios, self.quantidade, self.volume, self.isin,
            self.fator_cotacao,
        ]


CSV_COLUMNS = (
    "data", "codneg", "nomres", "especi", "tpmerc", "codbdi", "abertura", "maximo",
    "minimo", "medio", "fechamento", "negocios", "quantidade", "volume", "isin",
    "fator_cotacao",
)


def parse_line(line: str) -> CotacaoDia | None:
    """Uma linha do arquivo -> CotacaoDia; None para cabeçalho, trailer ou linha curta."""
    if len(line) < LINE_LENGTH or line[0:2] != "01":
        return None

    def cut(name: str) -> str:
        start, end = LAYOUT[name]
        return line[start:end]

    try:
        day = datetime.strptime(cut("data"), "%Y%m%d").date()
    except ValueError:
        return None

    return CotacaoDia(
        data=day,
        codneg=cut("codneg").strip(),
        nomres=cut("nomres").strip(),
        especi=cut("especi").strip(),
        tpmerc=_int(cut("tpmerc")),
        codbdi=_int(cut("codbdi")),
        abertura=_centavos(cut("preabe")),
        maximo=_centavos(cut("premax")),
        minimo=_centavos(cut("premin")),
        medio=_centavos(cut("premed")),
        fechamento=_centavos(cut("preult")),
        negocios=_int(cut("totneg")),
        quantidade=_int(cut("quatot")),
        volume=_centavos(cut("voltot")),
        isin=cut("codisi").strip(),
        fator_cotacao=_int(cut("fatcot")),
    )


def looks_like_fund_quota(row: CotacaoDia) -> bool:
    """Heurística de fallback quando não há lista de tickers: cota de fundo
    listado ("CI") com código terminado em 11. Pega ETFs e também FIIs - por
    isso a via principal é sempre o join com `tickers.csv`."""
    return row.especi.upper().startswith("CI") and row.codneg.endswith("11")


def read_cotahist(
    source: bytes | Path,
    tickers: set[str] | None = None,
    only_lote_padrao: bool = True,
) -> list[CotacaoDia]:
    """Lê o zip (bytes ou caminho) e devolve as linhas, filtradas por ticker se houver."""
    if isinstance(source, Path):
        if not source.exists() or not zipfile.is_zipfile(source):
            return []
        archive = zipfile.ZipFile(source)
    else:
        if not source:
            return []
        try:
            archive = zipfile.ZipFile(io.BytesIO(source))
        except zipfile.BadZipFile:
            return []

    wanted = {t.upper() for t in tickers} if tickers else None
    rows: list[CotacaoDia] = []
    with archive:
        names = archive.namelist()
        if not names:
            return []
        with archive.open(names[0]) as handle:
            for raw in io.TextIOWrapper(handle, encoding="iso-8859-1"):
                row = parse_line(raw.rstrip("\r\n"))
                if row is None:
                    continue
                if only_lote_padrao and not row.is_lote_padrao_vista:
                    continue
                if wanted is not None:
                    if row.codneg.upper() not in wanted:
                        continue
                elif not looks_like_fund_quota(row):
                    continue
                rows.append(row)
    return rows


def fetch_cotahist_day(config: Config, day: date) -> Path | None:
    """Baixa o arquivo do dia para o cache (imutável: baixado uma vez, fica).

    Devolve None num dia sem pregão (a B3 responde um arquivo vazio) ou quando
    a fonte não respondeu.
    """
    directory = config.cache_dir / "cotahist"
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"COTAHIST_D{day:%d%m%Y}.ZIP"
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    session = build_session(config)
    try:
        response = get(session, url_for_day(day), config, stream=True)
        content = response.content
    except SourceUnavailable:
        return None
    if not content or not zipfile.is_zipfile(io.BytesIO(content)):
        return None
    dest.write_bytes(content)
    return dest


def write_cotahist_day_csv(config: Config, rows: Iterable[CotacaoDia], day: date) -> Path:
    """Só as linhas de ETF, num CSV por dia (`data/etf/cotahist/<data>.csv`)."""
    config.etf_cotahist_dir.mkdir(parents=True, exist_ok=True)
    path = config.etf_cotahist_dir / f"{day.isoformat()}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row.to_row())
    return path


def read_cotahist_csvs(config: Config) -> list[CotacaoDia]:
    rows: list[CotacaoDia] = []
    if not config.etf_cotahist_dir.exists():
        return rows
    for path in sorted(config.etf_cotahist_dir.glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for r in csv.DictReader(handle, delimiter=";"):
                try:
                    day = datetime.strptime(r["data"], "%Y-%m-%d").date()
                except (KeyError, ValueError):
                    continue

                def f(key: str) -> float | None:
                    value = (r.get(key) or "").strip()
                    return float(value) if value else None

                def n(key: str) -> int | None:
                    value = (r.get(key) or "").strip()
                    return int(float(value)) if value else None

                rows.append(
                    CotacaoDia(
                        data=day, codneg=r.get("codneg") or "", nomres=r.get("nomres") or "",
                        especi=r.get("especi") or "", tpmerc=n("tpmerc"), codbdi=n("codbdi"),
                        abertura=f("abertura"), maximo=f("maximo"), minimo=f("minimo"),
                        medio=f("medio"), fechamento=f("fechamento"), negocios=n("negocios"),
                        quantidade=n("quantidade"), volume=f("volume"), isin=r.get("isin") or "",
                        fator_cotacao=n("fator_cotacao"),
                    )
                )
    return rows


@dataclass
class LiquidezSemanal:
    """Liquidez de um ETF numa semana ISO."""

    ticker: str
    iso_year: int
    iso_week: int
    week_key: str
    week_end_date: date
    pregoes: int
    fechamento: float | None
    volume_financeiro: float
    negocios: int
    quantidade: int
    retorno_preco: float | None  # vs. fechamento da semana anterior, em %


def aggregate_weekly_liquidity(rows: Iterable[CotacaoDia]) -> list[LiquidezSemanal]:
    """Consolida por ticker e semana ISO; o retorno compara fechamentos entre semanas."""
    by_ticker: dict[str, list[CotacaoDia]] = defaultdict(list)
    seen: set[tuple[str, date]] = set()
    for row in rows:
        key = (row.codneg, row.data)
        if key in seen:
            continue
        seen.add(key)
        by_ticker[row.codneg].append(row)

    result: list[LiquidezSemanal] = []
    for ticker, items in by_ticker.items():
        items.sort(key=lambda r: r.data)
        weeks: dict[tuple[int, int], list[CotacaoDia]] = defaultdict(list)
        for row in items:
            y, w, _ = row.data.isocalendar()
            weeks[(y, w)].append(row)
        prev_close: float | None = None
        for (y, w) in sorted(weeks):
            wrows = weeks[(y, w)]
            close = wrows[-1].fechamento
            ret = None
            if prev_close and close is not None and prev_close > 0:
                ret = round((close / prev_close - 1.0) * 100.0, 4)
            result.append(
                LiquidezSemanal(
                    ticker=ticker, iso_year=y, iso_week=w, week_key=f"{y}-W{w:02d}",
                    week_end_date=wrows[-1].data, pregoes=len(wrows), fechamento=close,
                    volume_financeiro=round(sum(r.volume or 0.0 for r in wrows), 2),
                    negocios=sum(r.negocios or 0 for r in wrows),
                    quantidade=sum(r.quantidade or 0 for r in wrows),
                    retorno_preco=ret,
                )
            )
            prev_close = close if close is not None else prev_close
    result.sort(key=lambda s: (s.iso_year, s.iso_week, s.volume_financeiro), reverse=True)
    return result
