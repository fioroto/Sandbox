"""Cadastro de fundos listados na B3 - a fonte do ticker e do CNPJ de cada ETF.

O portal de fundos listados da B3 (`sistemaswebb3-listados.b3.com.br`) é uma
SPA alimentada por uma API pública, sem autenticação, cujos parâmetros vão
codificados em base64 na própria URL. Medido em 2026-09-24 nas requisições que
a própria página de ETFs listados da B3 faz (`fundsListedPage/ETF-RF`):

    fundsListedProxy/Search/GetListFunds/<b64 {"language": "pt-br", "typeFund": "ETF-RF", "pageNumber": 1, "pageSize": 20}>
    fundsListedProxy/Search/GetDetailFund/<b64 {"language": "pt-br", "idFNET": "10553", "idCEM": "BDAP", "typeFund": "ETF-RF"}>

A listagem traz `id`, `acronym`, `fundName` e `tradingName`; o detalhe traz o
código de negociação (`tradingCode`: BDAP11), o `cnpj`, o `segment` e o
`shareHolder`. O tipo vai por nome: "ETF" (renda variável) e "ETF-RF" (renda
fixa), "ETF-CRIPTO" e "ETF-FII". O endpoint anterior, documentado pela biblioteca aberta `mercados`
(`fundsProxy/fundsCall/GetListedFundsSIG`, tipo numérico), responde 200 com
zero registros para todo tipo - inclusive FII (7), o caso que a biblioteca
cobre. O leitor do detalhe ainda entende o formato antigo (`detailFund` +
`shareHolder`). A API às vezes devolve o JSON *dentro de uma string JSON* (dois
`json.loads`), e uma visita prévia a uma página de b3.com.br entrega os
cookies que ela espera.

Uso principal aqui: preencher `data/etf/tickers.csv` (CNPJ -> ticker) a partir
de uma fonte, em vez de uma lista escrita de memória. O cadastro da CVM não traz
o código de negociação, e o COTAHIST não serve de ponte para ETF de renda fixa:
nenhum dos 11 códigos procurados (IMAB11, B5P211, LFTS11...) está no arquivo de
08/2026.
"""
from __future__ import annotations

import base64
import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable, build_session, get
from shelf_monitor.normalize import normalize_cnpj, parse_number

LISTED_URL = "https://sistemaswebb3-listados.b3.com.br/fundsListedProxy/Search/"
# Qualquer página de b3.com.br serve para receber os cookies; esta é a lista
# pública de ETFs, o que torna o aquecimento ele mesmo uma sondagem útil.
WARMUP_URL = (
    "https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-variavel/"
    "etf/renda-variavel/etfs-listados/"
)

# Os tipos de ETF da API, pelo nome. Medido em 2026-09-24: 130 de renda variavel,
# 65 de renda fixa, 26 de cripto e 2 de FII - 223, contra os 219 produtos locais
# que a B3 contou em 08/2026 mais os lancamentos de setembro.
ETF_TYPES = ("ETF", "ETF-RF", "ETF-CRIPTO", "ETF-FII")

TICKERS_COLUMNS = ("cnpj", "ticker", "nome_b3", "segmento", "fonte", "atualizado_em")


def encode_params(params: dict) -> str:
    """Parâmetros da API vão como JSON em base64 no path da URL."""
    return base64.b64encode(json.dumps(params).encode("utf-8")).decode("ascii")


def decode_b3_json(text: str) -> dict | list:
    """A API às vezes devolve o JSON dentro de uma string JSON ("WTF, B3?")."""
    text = text.strip()
    if not text:
        return {}
    data = json.loads(text)
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _inteiro_br(value: object) -> float | None:
    """"100.000" -> 100000: a API atual escreve a quantidade de cotas com o ponto
    de milhar brasileiro, que `parse_number` leria como decimal."""
    text = _clean(value)
    if not text:
        return None
    try:
        return float(text.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _parse_br_date(value: object) -> date | None:
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class FundoListadoB3:
    """Um fundo listado na B3, como o portal o descreve."""

    type_id: int | str
    acronym: str
    trading_name: str | None = None
    cnpj: str | None = None
    codes: list[str] = field(default_factory=list)
    quota_count: float | None = None
    quota_date: date | None = None
    classification: str | None = None
    segment: str | None = None
    type_fnet: str | None = None
    company_name: str | None = None
    website: str | None = None
    admin_name: str | None = None
    id_fnet: int | None = None

    @property
    def ticker(self) -> str:
        """Código de negociação principal (BOVA11); a B3 lista o acrônimo sem o sufixo."""
        return self.codes[0] if self.codes else f"{self.acronym}11"

    def to_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "acronym": self.acronym,
            "ticker": self.ticker,
            "trading_name": self.trading_name,
            "cnpj": self.cnpj,
            "codes": list(self.codes),
            "quota_count": self.quota_count,
            "quota_date": self.quota_date.isoformat() if self.quota_date else None,
            "classification": self.classification,
            "segment": self.segment,
            "type_fnet": self.type_fnet,
            "company_name": self.company_name,
            "website": self.website,
            "admin_name": self.admin_name,
            # Chave do fundo no Fundos.NET: e o que liga B3 e FNET sem depender do nome.
            "id_fnet": self.id_fnet,
        }


def parse_listed_fund_detail(payload: dict, type_id: int | str) -> FundoListadoB3 | None:
    """Detalhe de um fundo -> FundoListadoB3. None quando o payload nao descreve um.

    Entende os dois formatos: o atual (`GetDetailFund`, tudo no primeiro nivel,
    com `tradingCode`) e o antigo (`GetDetailFundSIG`, em `detailFund`).
    """
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detailFund")
    if not isinstance(detail, dict):
        return _parse_detalhe_atual(payload, type_id)
    shareholder = payload.get("shareHolder") or {}

    codes: list[str] = []
    for key in ("codes", "codesOther"):
        for item in detail.get(key) or []:
            code = _clean(item)
            if code and code.upper() not in codes:
                codes.append(code.upper())

    acronym = _clean(detail.get("acronym")) or ""
    return FundoListadoB3(
        type_id=type_id,
        acronym=acronym.upper(),
        trading_name=_clean(detail.get("tradingName")),
        cnpj=normalize_cnpj(_clean(detail.get("cnpj"))),
        codes=codes,
        quota_count=parse_number(_clean(detail.get("quotaCount"))),
        quota_date=_parse_br_date(detail.get("quotaDateApproved")),
        classification=_clean(detail.get("classification")),
        segment=_clean(detail.get("segment")),
        type_fnet=_clean(detail.get("typeFNET")),
        company_name=_clean(detail.get("companyName")),
        website=_clean(detail.get("webSite")),
        admin_name=_clean(shareholder.get("shareHolderName")) if isinstance(shareholder, dict) else None,
    )


def _parse_detalhe_atual(payload: dict, type_id: int | str) -> FundoListadoB3 | None:
    acronym = _clean(payload.get("acronym"))
    if not acronym:
        return None
    codigo = _clean(payload.get("tradingCode"))
    shareholder = payload.get("shareHolder") or {}
    try:
        id_fnet = int(payload.get("idFNET")) if payload.get("idFNET") not in (None, "") else None
    except (TypeError, ValueError):
        id_fnet = None
    return FundoListadoB3(
        type_id=_clean(payload.get("typeName")) or type_id,
        acronym=acronym.upper(),
        trading_name=_clean(payload.get("tradingName")),
        cnpj=normalize_cnpj(_clean(payload.get("cnpj"))),
        codes=[codigo.upper()] if codigo else [],
        quota_count=_inteiro_br(payload.get("quotaCount")),
        quota_date=_parse_br_date(payload.get("quotaDateApproved")),
        classification=_clean(payload.get("classification")),
        segment=_clean(payload.get("segment")),
        type_fnet=_clean(payload.get("typeFNET")),
        company_name=_clean(payload.get("fundName")),
        website=_clean(payload.get("webSite")),
        admin_name=_clean(shareholder.get("shareHolderName")) if isinstance(shareholder, dict) else None,
        id_fnet=id_fnet,
    )


class B3ListedFunds:
    """Cliente mínimo da API de fundos listados."""

    def __init__(self, config: Config):
        self.config = config
        self.session = build_session(config)
        self._warm = False

    def warm_up(self) -> None:
        """Visita uma página do portal para receber os cookies que a API exige."""
        if self._warm:
            return
        get(self.session, WARMUP_URL, self.config)
        self._warm = True

    def _call(self, endpoint: str, params: dict) -> dict | list:
        self.warm_up()
        url = f"{LISTED_URL}{endpoint}/{encode_params(params)}"
        response = get(self.session, url, self.config, accept="application/json")
        try:
            return decode_b3_json(response.text)
        except ValueError as exc:
            raise SourceUnavailable(f"resposta não JSON em {url}: {exc}") from exc

    def list_funds(self, type_name: str, page_size: int = 100, max_pages: int = 50) -> Iterator[dict]:
        """Linhas de `GetListFunds` (`id`, `acronym`, `fundName`, `tradingName`)."""
        page = 1
        while page <= max_pages:
            data = self._call(
                "GetListFunds",
                {"language": "pt-br", "typeFund": type_name, "pageNumber": page, "pageSize": page_size},
            )
            if isinstance(data, list):
                yield from data
                return
            if not isinstance(data, dict):
                return
            results = data.get("results") or []
            yield from results
            total_pages = int((data.get("page") or {}).get("totalPages") or 1)
            if page >= total_pages or not results:
                return
            page += 1

    def fund_detail_raw(self, type_name: str, id_fnet: object, acronym: str) -> dict | list:
        """O detalhe como a API devolve - o extrator bruto guarda isto inteiro
        (`tradingCodeOthers`, `idFNET`), que `parse_listed_fund_detail` resume."""
        return self._call(
            "GetDetailFund",
            {"language": "pt-br", "idFNET": str(id_fnet), "idCEM": acronym, "typeFund": type_name},
        )

    def fund_detail(self, type_name: str, id_fnet: object, acronym: str) -> FundoListadoB3 | None:
        data = self.fund_detail_raw(type_name, id_fnet, acronym)
        return parse_listed_fund_detail(data if isinstance(data, dict) else {}, type_name)

    def funds_of_type(self, type_name: str) -> Iterator[FundoListadoB3]:
        """Lista + detalhe de todos os fundos de um tipo (um GET por fundo)."""
        for row in self.list_funds(type_name):
            if not isinstance(row, dict):
                continue
            acronym = _clean(row.get("acronym"))
            if not acronym or row.get("id") in (None, ""):
                continue
            try:
                fund = self.fund_detail(type_name, row["id"], acronym)
            except SourceUnavailable:
                continue
            if fund is None:
                continue
            fund.trading_name = fund.trading_name or _clean(row.get("tradingName"))
            fund.company_name = fund.company_name or _clean(row.get("fundName"))
            yield fund


# --- tickers.csv -------------------------------------------------------------


def read_tickers_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter=";")]


def merge_tickers(existing: list[dict], funds: Iterable[FundoListadoB3], today: date, source: str = "b3") -> list[dict]:
    """Substitui as linhas vindas de `source` pelas novas e preserva as demais.

    Uma linha escrita à mão (`fonte` diferente) nunca é sobrescrita por uma
    coleta: se a pessoa corrigiu um ticker, a correção vale. Linhas da mesma
    fonte são trocadas inteiras - é uma foto, não um acumulado.
    """
    kept = [row for row in existing if (row.get("fonte") or "").strip().lower() != source]
    manual_cnpjs = {normalize_cnpj(row.get("cnpj")) for row in kept}

    fresh: dict[str, dict] = {}
    for fund in funds:
        if not fund.cnpj or fund.cnpj in manual_cnpjs:
            continue
        fresh[fund.cnpj] = {
            "cnpj": fund.cnpj,
            "ticker": fund.ticker,
            "nome_b3": fund.trading_name or fund.acronym,
            "segmento": fund.segment or "",
            "fonte": source,
            "atualizado_em": today.isoformat(),
        }
    rows = kept + sorted(fresh.values(), key=lambda r: r["ticker"])
    return rows


def write_tickers_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(TICKERS_COLUMNS), delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in TICKERS_COLUMNS})
    return path


def save_b3_snapshot(config: Config, funds: list[FundoListadoB3], day: date) -> Path:
    """Foto do dia do cadastro de ETFs listados (`data/etf/b3/<data>.json`)."""
    config.etf_b3_dir.mkdir(parents=True, exist_ok=True)
    path = config.etf_b3_dir / f"{day.isoformat()}.json"
    payload = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(funds),
        "funds": [f.to_dict() for f in funds],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
