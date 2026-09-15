"""Cadastro de fundos listados na B3 - a fonte do ticker e do CNPJ de cada ETF.

O portal de fundos listados da B3 (`sistemaswebb3-listados.b3.com.br`) é uma
SPA alimentada por uma API pública, sem autenticação, cujos parâmetros vão
codificados em base64 na própria URL:

    fundsProxy/fundsCall/GetListedFundsSIG/<b64 {"typeFund": N, "pageNumber": 1, "pageSize": 100}>
    fundsProxy/fundsCall/GetDetailFundSIG/<b64 {"typeFund": N, "identifierFund": "BOVA"}>

A evidência desses endpoints e do formato da resposta (`results` + `page.totalPages`
na listagem; `detailFund` + `shareHolder` no detalhe, com `cnpj`, `codes`,
`quotaCount`, `quotaDateApproved`, `classification`, `segment`, `typeFNET`) vem da
biblioteca aberta `mercados` (PythonicCafe), que usa exatamente esses caminhos
para FII (typeFund 7), FIP (21), FI-Infra (27) e FI-Agro (34). Dois cuidados
que ela documenta e que reproduzimos: a API às vezes devolve o JSON *dentro de
uma string JSON* (precisa de dois `json.loads`), e uma visita prévia a uma página
de b3.com.br é necessária para receber os cookies que a API espera.

O que NÃO está confirmado por evidência: o `typeFund` dos ETFs (renda variável
e renda fixa). Por isso `discover_etf_types` varre candidatos e reconhece o tipo
certo pelo CNPJ - um tipo cujos fundos batem com o catálogo FIIM da CVM é um
tipo de ETF. É a sondagem (`etf --probe-sources`) que grava esse resultado.

Uso principal aqui: preencher `data/etf/tickers.csv` (CNPJ -> ticker) a partir
de uma fonte, em vez de uma lista escrita de memória.
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

FUNDS_CALL_URL = "https://sistemaswebb3-listados.b3.com.br/fundsProxy/fundsCall/"
# Qualquer página de b3.com.br serve para receber os cookies; esta é a lista
# pública de ETFs, o que torna o aquecimento ele mesmo uma sondagem útil.
WARMUP_URL = (
    "https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-variavel/"
    "etf/renda-variavel/etfs-listados/"
)

# typeFund conhecidos, com evidência (biblioteca `mercados`).
KNOWN_TYPE_FUND = {"FII": 7, "FIP": 21, "FI-Infra": 27, "FI-Agro": 34}
# Candidatos varridos pela descoberta do tipo dos ETFs.
ETF_TYPE_CANDIDATES = tuple(range(1, 41))

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

    type_id: int
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
        }


def parse_listed_fund_detail(payload: dict, type_id: int) -> FundoListadoB3 | None:
    """`GetDetailFundSIG` -> FundoListadoB3. Devolve None se o payload não tem `detailFund`."""
    detail = payload.get("detailFund") if isinstance(payload, dict) else None
    if not isinstance(detail, dict):
        return None
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
        url = f"{FUNDS_CALL_URL}{endpoint}/{encode_params(params)}"
        response = get(self.session, url, self.config, accept="application/json")
        try:
            return decode_b3_json(response.text)
        except ValueError as exc:
            raise SourceUnavailable(f"resposta nao-JSON em {url}: {exc}") from exc

    def list_funds(self, type_id: int, page_size: int = 100, max_pages: int = 50) -> Iterator[dict]:
        """Linhas de `GetListedFundsSIG` (cada uma traz ao menos `acronym`)."""
        page = 1
        while page <= max_pages:
            data = self._call(
                "GetListedFundsSIG",
                {"typeFund": type_id, "pageNumber": page, "pageSize": page_size},
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

    def fund_detail(self, type_id: int, acronym: str) -> FundoListadoB3 | None:
        data = self._call(
            "GetDetailFundSIG", {"typeFund": type_id, "identifierFund": acronym}
        )
        return parse_listed_fund_detail(data if isinstance(data, dict) else {}, type_id)

    def funds_of_type(self, type_id: int) -> Iterator[FundoListadoB3]:
        """Lista + detalhe de todos os fundos de um tipo (um GET por fundo)."""
        for row in self.list_funds(type_id):
            acronym = _clean(row.get("acronym")) if isinstance(row, dict) else None
            if not acronym:
                continue
            try:
                fund = self.fund_detail(type_id, acronym)
            except SourceUnavailable:
                continue
            if fund is not None:
                yield fund

    def discover_etf_types(
        self,
        known_cnpjs: set[str],
        candidates: Iterable[int] = ETF_TYPE_CANDIDATES,
        sample: int = 3,
    ) -> dict[int, dict]:
        """Descobre quais `typeFund` são ETFs, pelo CNPJ.

        Para cada candidato, pede a primeira página e o detalhe de até `sample`
        fundos; um tipo cujos CNPJs aparecem no catálogo FIIM da CVM é um tipo
        de ETF. Devolve, por tipo que respondeu, os acrônimos vistos e quantos
        CNPJs bateram - o dado bruto que a sondagem grava.
        """
        found: dict[int, dict] = {}
        for type_id in candidates:
            try:
                rows = list(self.list_funds(type_id, page_size=sample, max_pages=1))
            except SourceUnavailable:
                continue
            acronyms = [
                str(r.get("acronym")).strip()
                for r in rows
                if isinstance(r, dict) and r.get("acronym")
            ]
            if not acronyms:
                continue
            hits = 0
            samples: list[dict] = []
            for acronym in acronyms[:sample]:
                try:
                    fund = self.fund_detail(type_id, acronym)
                except SourceUnavailable:
                    continue
                if fund is None:
                    continue
                if fund.cnpj and fund.cnpj in known_cnpjs:
                    hits += 1
                samples.append(
                    {"acronym": fund.acronym, "cnpj": fund.cnpj, "ticker": fund.ticker,
                     "segment": fund.segment, "type_fnet": fund.type_fnet}
                )
            found[type_id] = {"acronyms": acronyms, "cnpj_hits": hits, "sample": samples}
        return found


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
