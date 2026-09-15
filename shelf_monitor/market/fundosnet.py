"""Fundos.NET - o informe diário dos ETFs, que o INF_DIARIO da CVM não traz.

O INF_DIARIO (`inf_diario_fi`) cobre FI/FIF. Fundos de índice (FIIM) entregam
seus documentos periódicos pelo Fundos.NET (`fnet.bmfbovespa.com.br`), o mesmo
canal de FII, FIDC e FIP - inclusive um **Informe Diário**. É a única fonte
pública, oficial e diária que cobre todos os ETFs listados, os de renda fixa
inclusive. Este módulo lista e baixa esses informes.

Evidência dos endpoints e códigos (biblioteca aberta `mercados`, PythonicCafe):

- `abrirGerenciadorDocumentosCVM`: página principal; traz o `csrf_token` num
  script e os `<select>` de tipo de fundo e categoria.
- `tipoFundo`: 1 FII, 2 FIDC, **3 ETF, 4 ETF RF**, 7 Fundo Setorial, 10 FIP.
- categoria 6 = "Informes Periódicos"; tipo de documento 64 = "Informe Diário".
- `listarTodosTiposPorCategoriaETipoFundo?idTipoFundo=&idCategoria=` (XHR):
  tipos de documento válidos para um tipo de fundo - é o que confirma o 64.
- `pesquisarGerenciadorDocumentosDados` (XHR, DataTables): busca paginada com
  `s` (offset), `l` (tamanho), `o[0][dataEntrega]=desc`, `idCategoriaDocumento`,
  `idTipoDocumento`, `tipoFundo`, `dataInicial`/`dataFinal` (dd/mm/aaaa);
  responde `{recordsTotal, data: [...]}` com `id`, `descricaoFundo`,
  `nomePregao`, `dataEntrega`, `dataReferencia` + `formatoDataReferencia`, etc.
- `downloadDocumento?id=`: o documento em si (XML nos informes estruturados).
- `listarFundos?term=&page=&idTipoFundo=&idAdm=0&paraCerts=false`: fundos por tipo.

O que NÃO está confirmado por evidência é o esquema do XML do informe diário de
ETF. `parse_informe_diario_xml` é tolerante de propósito: mapeia as tags que
reconhece (nomes do padrão XML de informe diário da CVM - VL_PATRIM_LIQ,
VL_QUOTA, CAPTC_DIA, RESG_DIA, NR_COTST - e variantes plausíveis) e devolve as
que não reconhece em `unknown_tags`, como o importador de mercado faz com
colunas novas. A sondagem grava o XML bruto para o mapeamento ser fechado com o
documento real na mão.
"""
from __future__ import annotations

import csv
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable, build_session, get
from shelf_monitor.market.etf_flow import EtfDailyRecord
from shelf_monitor.normalize import normalize_cnpj, parse_number

BASE_URL = "https://fnet.bmfbovespa.com.br/fnet/publico/"

FUND_TYPES = {"FII": 1, "FIDC": 2, "ETF": 3, "ETF RF": 4, "Fundo Setorial": 7, "FIP": 10}
ETF_FUND_TYPES = {"ETF": 3, "ETF RF": 4}
CATEGORY_INFORMES_PERIODICOS = 6
DOC_TYPE_INFORME_DIARIO = 64

_CSRF_RE = re.compile(r"""csrf_token ?= ?["']([^"']+)["']""")
_SELECT_RE = r"""<select[^>]*\bid=["']{id}["'][^>]*>(.*?)</select>"""
_OPTION_RE = re.compile(r"""<option[^>]*\bvalue=["']([^"']*)["'][^>]*>(.*?)</option>""", re.S | re.I)


def extract_csrf_token(html: str) -> str | None:
    match = _CSRF_RE.search(html or "")
    return match.group(1) if match else None


def parse_select_options(html: str, select_id: str) -> dict[str, int]:
    """Opções de um `<select>` da página principal -> {texto: valor}.

    Opções sem valor ("Todos") são ignoradas: o que interessa é o código.
    """
    match = re.search(_SELECT_RE.format(id=re.escape(select_id)), html or "", re.S | re.I)
    if not match:
        return {}
    options: dict[str, int] = {}
    for value, text in _OPTION_RE.findall(match.group(1)):
        value = value.strip()
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()
        if not value or not value.lstrip("-").isdigit():
            continue
        options[label] = int(value)
    return options


def _parse_reference_date(fmt: str | None, value: str | None) -> date | None:
    """`dataReferencia` vem com o formato em `formatoDataReferencia`.

    Códigos observados na biblioteca de referência: 1 = ano, 2 = mês/ano,
    3 = dia/mês/ano, 4 = dia/mês/ano hora:minuto.
    """
    text = (value or "").strip()
    if not text:
        return None
    fmt = (fmt or "").strip()
    try:
        if fmt == "1":
            return date(int(text), 1, 1)
        if fmt == "2":
            month, year = text.split("/")
            return date(int(year), int(month), 1)
        if fmt == "4":
            return datetime.strptime(text[:16], "%d/%m/%Y %H:%M").date()
        return datetime.strptime(text[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


@dataclass
class DocumentoFnet:
    """Metadados de um documento publicado no Fundos.NET."""

    id: int
    fundo: str
    nome_pregao: str | None = None
    cnpj_fundo: str | None = None
    categoria: str | None = None
    tipo: str | None = None
    especie: str | None = None
    data_entrega: datetime | None = None
    data_referencia: date | None = None
    versao: int | None = None
    situacao: str | None = None
    status: str | None = None

    @property
    def url(self) -> str:
        return f"{BASE_URL}downloadDocumento?id={self.id}"


def parse_document_row(row: dict) -> DocumentoFnet | None:
    """Uma linha de `pesquisarGerenciadorDocumentosDados` -> DocumentoFnet."""
    if not isinstance(row, dict) or row.get("id") in (None, ""):
        return None
    try:
        doc_id = int(row["id"])
    except (TypeError, ValueError):
        return None

    entrega = None
    raw_entrega = str(row.get("dataEntrega") or "").strip()
    if raw_entrega:
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                entrega = datetime.strptime(raw_entrega[: len("dd/mm/yyyy HH:MM")], fmt)
                break
            except ValueError:
                continue

    versao = None
    try:
        versao = int(row.get("versao")) if row.get("versao") not in (None, "") else None
    except (TypeError, ValueError):
        versao = None

    return DocumentoFnet(
        id=doc_id,
        fundo=str(row.get("descricaoFundo") or "").strip(),
        nome_pregao=(str(row.get("nomePregao") or "").strip() or None),
        cnpj_fundo=normalize_cnpj(row.get("cnpjFundo")),
        categoria=(str(row.get("categoriaDocumento") or "").strip() or None),
        tipo=(str(row.get("tipoDocumento") or "").strip() or None),
        especie=(str(row.get("especieDocumento") or "").strip() or None),
        data_entrega=entrega,
        data_referencia=_parse_reference_date(row.get("formatoDataReferencia"), row.get("dataReferencia")),
        versao=versao,
        situacao=(str(row.get("situacaoDocumento") or "").strip() or None),
        status=(str(row.get("descricaoStatus") or "").strip() or None),
    )


class FundosNetClient:
    """Cliente mínimo do Fundos.NET público (sem login)."""

    def __init__(self, config: Config):
        self.config = config
        self.session = build_session(config)
        self._main_page: str | None = None
        self._draw = 0

    def main_page(self) -> str:
        if self._main_page is None:
            response = get(self.session, f"{BASE_URL}abrirGerenciadorDocumentosCVM", self.config)
            self._main_page = response.text
        return self._main_page

    @property
    def csrf_token(self) -> str:
        token = extract_csrf_token(self.main_page())
        if not token:
            raise SourceUnavailable("Fundos.NET: csrf_token nao encontrado na pagina principal")
        return token

    def fund_types(self) -> dict[str, int]:
        return parse_select_options(self.main_page(), "tipoFundo")

    def categories(self) -> dict[str, int]:
        return parse_select_options(self.main_page(), "categoriaDocumento")

    def _xhr(self, path: str, params: dict) -> dict | list:
        self._draw += 1
        query = dict(params)
        query["d"] = self._draw
        query["_"] = int(time.time() * 1000)
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "CSRFToken": self.csrf_token,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        url = f"{BASE_URL}{path}"
        try:
            response = self.session.get(url, params=query, headers=headers, timeout=self.config.http_timeout)
        except Exception as exc:  # noqa: BLE001 - convertido em SourceUnavailable
            raise SourceUnavailable(f"{url}: {exc}") from exc
        if response.status_code >= 400:
            raise SourceUnavailable(f"HTTP {response.status_code} em {url}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceUnavailable(f"resposta nao-JSON em {url}: {exc}") from exc

    def document_types(self, fund_type_id: int, category_id: int = CATEGORY_INFORMES_PERIODICOS) -> list[dict]:
        """Tipos de documento válidos para (tipo de fundo, categoria) - confirma o 64."""
        data = self._xhr(
            "listarTodosTiposPorCategoriaETipoFundo",
            {"idTipoFundo": fund_type_id, "idCategoria": category_id},
        )
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def list_funds(self, fund_type_id: int, max_pages: int = 100) -> Iterator[dict]:
        """Fundos cadastrados no Fundos.NET de um tipo (ETF = 3, ETF RF = 4)."""
        page = 1
        while page <= max_pages:
            data = self._xhr(
                "listarFundos",
                {"term": "", "page": page, "idTipoFundo": fund_type_id, "idAdm": "0", "paraCerts": "false"},
            )
            if not isinstance(data, dict):
                return
            yield from (r for r in data.get("results") or [] if isinstance(r, dict))
            if not data.get("more"):
                return
            page += 1

    def search(
        self,
        fund_type_id: int,
        doc_type_id: int = DOC_TYPE_INFORME_DIARIO,
        category_id: int = CATEGORY_INFORMES_PERIODICOS,
        start: date | None = None,
        end: date | None = None,
        page_size: int = 200,
        max_pages: int = 50,
    ) -> Iterator[DocumentoFnet]:
        """Documentos publicados, mais recentes primeiro."""
        params = {
            "o[0][dataEntrega]": "desc",
            "idCategoriaDocumento": category_id,
            "idTipoDocumento": doc_type_id,
            "tipoFundo": fund_type_id,
            "idEspecieDocumento": "0",
            "dataInicial": start.strftime("%d/%m/%Y") if start else "",
            "dataFinal": end.strftime("%d/%m/%Y") if end else "",
            "s": 0,
            "l": page_size,
        }
        pages = 0
        total: int | None = None
        while pages < max_pages:
            data = self._xhr("pesquisarGerenciadorDocumentosDados", params)
            if not isinstance(data, dict):
                return
            rows = data.get("data") or []
            if total is None:
                try:
                    total = int(data.get("recordsTotal") or 0)
                except (TypeError, ValueError):
                    total = 0
            for row in rows:
                doc = parse_document_row(row)
                if doc is not None:
                    yield doc
            params["s"] += len(rows)
            pages += 1
            if not rows or params["s"] >= (total or 0):
                return

    def download(self, doc_id: int) -> tuple[bytes, str | None]:
        """Conteúdo bruto do documento e seu content-type."""
        response = get(self.session, f"{BASE_URL}downloadDocumento", self.config, params={"id": doc_id})
        return response.content, response.headers.get("Content-Type")


# --- XML do informe diário ----------------------------------------------------

# Nomes normalizados (maiúsculas, só alfanumérico). Os primeiros de cada grupo
# são os do padrão XML de informe diário da CVM (PadraoXMLInfoDiarioNet); os
# demais são variantes plausíveis - tudo que não casar vai para `unknown_tags`.
_TAG_MAP: dict[str, tuple[str, ...]] = {
    "cnpj": ("CNPJFDO", "CNPJFUNDO", "CNPJ", "CNPJCLASSE", "CNPJFUNDOCLASSE"),
    "data": ("DTCOMPTC", "DTCOMPETENCIA", "DTREF", "DTREFERENCIA", "DATA", "DATACOMPETENCIA", "DATAREFERENCIA"),
    "vl_total": ("VLTOTAL", "VALORTOTAL", "VLTOTALATV"),
    "cota": ("VLQUOTA", "VLCOTA", "VALORCOTA", "VALORDACOTA", "COTA"),
    "pl": ("VLPATRIMLIQ", "PATRIMLIQ", "PL", "PATRIMONIOLIQUIDO", "VLPL", "VLPATRIMONIOLIQUIDO"),
    "captacao": ("CAPTCDIA", "CAPTACAO", "VLCAPTC", "VLCAPTACAO", "CAPTACAODIA", "EMISSOES", "VLEMISSAO", "VLEMISSOES"),
    "resgates": ("RESGDIA", "RESGATE", "RESGATES", "VLRESG", "VLRESGATE", "VLRESGATES", "RESGATESDIA"),
    "cotistas": ("NRCOTST", "NRCOTISTAS", "COTISTAS", "QTDCOTISTAS", "NUMEROCOTISTAS", "QTCOTISTAS"),
    "cotas": (
        "QTCOTAS", "QTDCOTAS", "NRCOTAS", "COTASEMITIDAS", "QTCOTASEMITIDAS",
        "QTDCOTASEMITIDAS", "QUANTIDADECOTAS", "NUMEROCOTAS", "QTCOTASEMCIRCULACAO",
    ),
}
_FIELD_OF: dict[str, str] = {tag: fld for fld, tags in _TAG_MAP.items() for tag in tags}


def _norm_tag(tag: str) -> str:
    tag = tag.rsplit("}", 1)[-1]  # sem namespace
    return re.sub(r"[^A-Z0-9]", "", tag.upper())


def _parse_any_date(text: str | None) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class InformeDiarioEtf:
    """Um informe diário de ETF lido do Fundos.NET, nos campos que reconhecemos."""

    cnpj: str | None = None
    data: date | None = None
    pl: float | None = None
    cota: float | None = None
    cotas: float | None = None
    cotistas: int | None = None
    captacao: float | None = None
    resgates: float | None = None
    vl_total: float | None = None
    fundo: str | None = None
    doc_id: int | None = None
    fields: dict[str, str] = field(default_factory=dict)
    unknown_tags: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return bool(self.cnpj and self.data and (self.pl is not None or self.cota is not None))


def _leaves(node: ET.Element) -> Iterator[tuple[str, str]]:
    for child in node.iter():
        if child is node:
            continue
        if len(child) == 0 and child.text and child.text.strip():
            yield _norm_tag(child.tag), child.text.strip()


def _has_direct_cnpj(node: ET.Element) -> bool:
    return any(_FIELD_OF.get(_norm_tag(child.tag)) == "cnpj" for child in node)


def parse_informe_diario_xml(
    xml: bytes | str, doc_id: int | None = None, fundo: str | None = None
) -> list[InformeDiarioEtf]:
    """XML do informe -> registros (um por bloco com CNPJ; um só quando não há blocos).

    Tolerante por desenho (ver docstring do módulo): o que não for reconhecido
    sai em `unknown_tags`, nunca some em silêncio.
    """
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    nodes = [el for el in root.iter() if _has_direct_cnpj(el)]
    if not nodes:
        nodes = [root]

    informes: list[InformeDiarioEtf] = []
    for node in nodes:
        informe = InformeDiarioEtf(doc_id=doc_id, fundo=fundo)
        unknown: list[str] = []
        for tag, text in _leaves(node):
            informe.fields[tag] = text
            fld = _FIELD_OF.get(tag)
            if fld is None:
                unknown.append(tag)
                continue
            if fld == "cnpj":
                informe.cnpj = informe.cnpj or normalize_cnpj(text)
            elif fld == "data":
                informe.data = informe.data or _parse_any_date(text)
            elif fld == "cotistas":
                number = parse_number(text)
                informe.cotistas = int(number) if number is not None else informe.cotistas
            else:
                number = parse_number(text)
                if number is not None and getattr(informe, fld) is None:
                    setattr(informe, fld, number)
        informe.unknown_tags = sorted(set(unknown))
        informes.append(informe)
    return informes


def records_from_informes(informes: list[InformeDiarioEtf]) -> list[EtfDailyRecord]:
    """Informes -> registros diários no formato do pipeline de fluxo.

    Quando o informe traz captação e resgate do dia, eles entram direto. Quando
    traz só cotas emitidas e cota, a captação líquida é derivada:
    (cotas_hoje - cotas_ontem) x cota_hoje - o que na prática é a criação ou
    resgate de cotas do ETF pelo agente autorizado. O primeiro dia da série não
    tem "ontem" e fica sem fluxo (0, não inventado). `flow_source` diz qual das
    duas vias produziu o número.
    """
    usable = sorted(
        (i for i in informes if i.is_usable),
        key=lambda i: (i.cnpj or "", i.data or date.min),
    )
    records: list[EtfDailyRecord] = []
    previous: dict[str, InformeDiarioEtf] = {}
    for informe in usable:
        captacao = informe.captacao
        resgates = informe.resgates
        flow_source: str | None = "informe" if (captacao is not None or resgates is not None) else None
        if flow_source is None:
            prev = previous.get(informe.cnpj or "")
            if (
                prev is not None
                and prev.cotas is not None
                and informe.cotas is not None
                and informe.cota is not None
            ):
                net = (informe.cotas - prev.cotas) * informe.cota
                captacao, resgates = (net, 0.0) if net >= 0 else (0.0, -net)
                flow_source = "delta_cotas"
        pl = informe.pl if informe.pl is not None else (
            (informe.cotas or 0.0) * (informe.cota or 0.0) or 0.0
        )
        records.append(
            EtfDailyRecord(
                cnpj=informe.cnpj or "",
                dt_comptc=informe.data,
                vl_total=informe.vl_total if informe.vl_total is not None else pl,
                vl_quota=informe.cota or 0.0,
                vl_patrim_liq=pl,
                captc_dia=captacao or 0.0,
                resg_dia=resgates or 0.0,
                nr_cotst=informe.cotistas or 0,
                tp_fundo_classe="FIIM",
                flow_source=flow_source,
            )
        )
        previous[informe.cnpj or ""] = informe
    return records


# --- persistência -------------------------------------------------------------

_CSV_COLUMNS = (
    "cnpj", "fundo", "data", "pl", "cota", "cotas", "cotistas", "captacao",
    "resgates", "vl_total", "doc_id", "unknown_tags",
)


def write_fnet_day_csv(config: Config, informes: list[InformeDiarioEtf], day: date) -> Path:
    """Um CSV por dia de coleta (`data/etf/fnet/<data>.csv`), versionado."""
    config.etf_fnet_dir.mkdir(parents=True, exist_ok=True)
    path = config.etf_fnet_dir / f"{day.isoformat()}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(_CSV_COLUMNS)
        for i in informes:
            writer.writerow([
                i.cnpj or "", i.fundo or "", i.data.isoformat() if i.data else "",
                "" if i.pl is None else i.pl, "" if i.cota is None else i.cota,
                "" if i.cotas is None else i.cotas, "" if i.cotistas is None else i.cotistas,
                "" if i.captacao is None else i.captacao, "" if i.resgates is None else i.resgates,
                "" if i.vl_total is None else i.vl_total, i.doc_id or "",
                json.dumps(i.unknown_tags, ensure_ascii=False) if i.unknown_tags else "",
            ])
    return path


def read_fnet_csvs(config: Config) -> list[InformeDiarioEtf]:
    """Todos os informes já coletados, de todos os dias."""
    informes: list[InformeDiarioEtf] = []
    if not config.etf_fnet_dir.exists():
        return informes
    for path in sorted(config.etf_fnet_dir.glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=";"):
                informe = InformeDiarioEtf(
                    cnpj=normalize_cnpj(row.get("cnpj")),
                    fundo=row.get("fundo") or None,
                    data=_parse_any_date(row.get("data")),
                    pl=parse_number(row.get("pl")),
                    cota=parse_number(row.get("cota")),
                    cotas=parse_number(row.get("cotas")),
                    captacao=parse_number(row.get("captacao")),
                    resgates=parse_number(row.get("resgates")),
                    vl_total=parse_number(row.get("vl_total")),
                )
                cotistas = parse_number(row.get("cotistas"))
                informe.cotistas = int(cotistas) if cotistas is not None else None
                doc_id = parse_number(row.get("doc_id"))
                informe.doc_id = int(doc_id) if doc_id is not None else None
                informes.append(informe)
    return informes
