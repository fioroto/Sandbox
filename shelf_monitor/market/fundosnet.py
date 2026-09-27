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
from shelf_monitor.normalize import normalize_cnpj, normalize_text, parse_number

BASE_URL = "https://fnet.bmfbovespa.com.br/fnet/publico/"

FUND_TYPES = {"FII": 1, "FIDC": 2, "ETF": 3, "ETF RF": 4, "Fundo Setorial": 7, "FIP": 10}
ETF_FUND_TYPES = {"ETF": 3, "ETF RF": 4}
CATEGORY_INFORMES_PERIODICOS = 6
DOC_TYPE_INFORME_DIARIO = 64

# O token vive numa <meta name="_csrf">, que a pagina PUBLICA nao emite - o
# proprio JS do site le com fallback vazio (`$("meta[name='_csrf']")... || ''`).
# Medido em 2026-09-19: os XHR respondem HTTP 200 sem o header CSRFToken e com
# ele vazio. A versao anterior procurava uma atribuicao literal
# (`csrf_token = "..."`), nao achava, e levantava - barrando as quatro sondagens
# de ETF do Fundos.NET por uma exigencia que o servidor nao faz.
_CSRF_RE = re.compile(
    r"""<meta[^>]*name=["']_csrf["'][^>]*content=["']([^"']+)["']""", re.I
)
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
        """Token da pagina, ou string vazia quando ela nao emite nenhum.

        Ausencia de token nao e falha: ver _CSRF_RE. Se um dia a pagina publica
        passar a exigir, o header vai junto sozinho - e o que a meta trouxer.
        """
        return extract_csrf_token(self.main_page()) or ""

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
            raise SourceUnavailable(f"resposta não JSON em {url}: {exc}") from exc

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

    def download_with_headers(self, doc_id: int) -> tuple[bytes, dict[str, str]]:
        """Conteudo e cabecalhos da resposta.

        O CNPJ do fundo vem nulo na busca, mas o nome do arquivo no
        Content-Disposition comeca por ele (medido em 2026-09-24:
        "69131602000184-REG17092026V01-001322547.pdf") - e o que liga um
        regulamento ao ETF sem depender do nome do fundo.
        """
        response = get(self.session, f"{BASE_URL}downloadDocumento", self.config, params={"id": doc_id})
        return response.content, dict(response.headers)

    def exibir(self, doc_id: int) -> str:
        """A pagina que o site mostra para um documento (`exibirDocumento`).

        Para o informe diario de ETF, e o mes inteiro do fundo - ver
        `parse_informe_do_mes_html`.
        """
        response = get(self.session, f"{BASE_URL}exibirDocumento", self.config, params={"id": doc_id})
        return response.text


# --- XML do informe diário ----------------------------------------------------

# Nomes normalizados (maiúsculas, só alfanumérico). Os primeiros de cada grupo
# são os do padrão XML de informe diário da CVM (PadraoXMLInfoDiarioNet); os
# demais são variantes plausíveis - tudo que não casar vai para `unknown_tags`.
_TAG_MAP: dict[str, tuple[str, ...]] = {
    "cnpj": ("CNPJFDO", "CNPJFUNDO", "CNPJ", "CNPJCLASSE", "CNPJFUNDOCLASSE"),
    # DTCOMPT e a tag real do informe de ETF (versao 4.0 do PadraoXMLInfoDiarioNet,
    # medido em 2026-09-19); as demais sao variantes plausiveis. Nao inclua
    # DATAPROXPL aqui: DATA_PROX_PL e a data do PROXIMO PL, nao a do informe.
    "data": (
        "DTCOMPT", "DTCOMPTC", "DTCOMPETENCIA", "DTREF", "DTREFERENCIA", "DATA",
        "DATACOMPETENCIA", "DATAREFERENCIA",
    ),
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


def _has_direct_values(node: ET.Element) -> bool:
    return any(_FIELD_OF.get(_norm_tag(child.tag)) in ("pl", "cota") for child in node)


def _has_values(node: ET.Element) -> bool:
    return any(_FIELD_OF.get(tag) in ("pl", "cota") for tag, _ in _leaves(node))


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

    # Layout do formulario do Fundos.NET (a versao reapresentada, medido em
    # 2026-09-24): o CNPJ_FDO fica no CAB_INFORM e o PL e a cota num INFORM sem
    # CNPJ. Lido so pelos blocos com CNPJ, o informe saia sem PL e sem cota - e
    # 18 ETFs do Itau e o da Caixa sumiam do fechamento de 08/2026. O CNPJ do
    # cabecalho passa para os blocos de valores.
    header_cnpj: str | None = None
    if len(nodes) == 1 and nodes[0] is not root and not _has_values(nodes[0]):
        values = [el for el in root.iter() if _has_direct_values(el) and not _has_direct_cnpj(el)]
        if values:
            header_cnpj = next(
                (normalize_cnpj(text) for tag, text in _leaves(nodes[0]) if _FIELD_OF.get(tag) == "cnpj"),
                None,
            )
            nodes = values

    # A data do informe de ETF mora no cabecalho (CAB_INFORM/DT_COMPT), fora do
    # bloco de cada fundo - entao ler so o bloco deixava `data` vazia e
    # `is_usable` falso, descartando todo registro apesar de PL, cota, captacao,
    # resgate e cotistas terem sido lidos. O cabecalho entra como padrao; um
    # bloco que traga a propria data continua ganhando dela.
    header_date = _header_date(root, nodes)

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
        informe.data = informe.data or header_date
        informe.cnpj = informe.cnpj or header_cnpj
        informe.unknown_tags = sorted(set(unknown))
        informes.append(informe)
    return informes


def _header_date(root, nodes: list) -> date | None:
    """Data do informe declarada fora dos blocos de fundo (o cabecalho)."""
    inside = {id(el) for node in nodes for el in node.iter()}
    for element in root.iter():
        if id(element) in inside:
            continue
        for tag, text in _leaves(element):
            if _FIELD_OF.get(tag) == "data":
                parsed = _parse_any_date(text)
                if parsed:
                    return parsed
    return None


# --- a liquida do dia ---------------------------------------------------------

# O declarado do dia (captacao - resgate) vale contra a variacao de cotas do dia
# - a diferenca de PL sem a valorizacao, (PL / cota de hoje - PL / cota de
# ontem) x cota de hoje - com folga de 5% ou R$ 1 mil, o que for maior. Medido
# em 2026-09-25 nos informes de 10 a 18/09/2026: fechou em 74 de 84 dias com
# movimento, com diferenca abaixo de 1% em quase todos (4% no IT NOW Bitcoin:
# R$ 438 mil declarados, R$ 421 mil pelas cotas). Nos outros 10, o Itau Asset e
# a Grao declararam zero com as cotas mudando e a Buena Vista declarou resgate
# com as cotas paradas.
FOLGA_DO_DIA = 0.05
FOLGA_DO_DIA_MINIMA = 1_000.0
# Cota que dobra ou cai a menos da metade de um dia para o outro: desdobramento
# ou grupamento. A quantidade de cotas muda sem ninguem entrar ou sair, entao o
# dia fica com o declarado.
EVENTO_NA_COTA = 2.0


def declarado_fecha(declarado: float, pelo_pl: float) -> bool:
    """O declarado bate com a variacao de cotas, dentro da folga."""
    folga = max(FOLGA_DO_DIA_MINIMA, FOLGA_DO_DIA * max(abs(declarado), abs(pelo_pl)))
    return abs(declarado - pelo_pl) <= folga


def _tem_pl(informe: InformeDiarioEtf | None) -> bool:
    return informe is not None and informe.pl is not None and bool(informe.cota)


def variacao_de_cotas(dia: InformeDiarioEtf, anterior: InformeDiarioEtf) -> float | None:
    """(cotas do dia - cotas de `anterior`) x cota do dia, com cotas = PL / cota.

    None sem PL e cota nas duas pontas, ou quando a cota dobra ou cai a menos da
    metade (desdobramento ou grupamento: as cotas mudam sem fluxo).
    """
    if not _tem_pl(dia) or not _tem_pl(anterior):
        return None
    razao = dia.cota / anterior.cota
    if not 1 / EVENTO_NA_COTA < razao < EVENTO_NA_COTA:
        return None
    return (dia.pl / dia.cota - anterior.pl / anterior.cota) * dia.cota


def ajustes_do_informe(
    dias: list[InformeDiarioEtf], anterior: InformeDiarioEtf | None = None, estreia: bool = False
) -> list[tuple[float, bool]]:
    """Por dia, na ordem de `dias`: (ajuste pela diferenca de PL, se o ajuste valeu).

    O declarado (captacao - resgate) de cada trecho entre dois dias com PL e
    cota confere contra a variacao de cotas do trecho: dia sem PL no meio entra
    na conta do dia seguinte, que e onde o dinheiro dele aparece nas cotas.
    Quando nao fecha, o dia com PL leva o ajuste que faz o trecho dar a
    variacao de cotas. `anterior`: o ultimo informe antes de `dias` (o
    fechamento do mes anterior). Sem ele, o primeiro dia de um ETF que estreia
    confere contra o proprio PL (toda cota e nova); o de um ETF que ja existia
    fica com o declarado.
    """
    saida: list[tuple[float, bool]] = []
    base = anterior if _tem_pl(anterior) else None
    pendente = 0.0
    for dia in dias:
        declarado = (dia.captacao or 0.0) - (dia.resgates or 0.0)
        if not _tem_pl(dia):
            pendente += declarado
            saida.append((0.0, False))
            continue
        trecho = pendente + declarado
        if base is not None:
            pelo_pl = variacao_de_cotas(dia, base)
        else:
            pelo_pl = dia.pl if estreia else None
        if pelo_pl is not None and not declarado_fecha(trecho, pelo_pl):
            saida.append((pelo_pl - trecho, True))
        else:
            saida.append((0.0, False))
        base, pendente = dia, 0.0
    return saida


def records_from_informes(informes: list[InformeDiarioEtf]) -> list[EtfDailyRecord]:
    """Informes -> registros diários no formato do pipeline de fluxo.

    Quando o informe traz captação e resgate do dia, eles entram como vieram, e
    o dia confere contra a variação de cotas (`ajustes_do_informe`): se o declarado
    não fecha - o Itaú Asset que declara zero, o resgate declarado com as cotas
    paradas -, a diferença entra em `ajuste_dia`, e a líquida do dia é a da
    diferença de PL. Quando o informe traz só cotas emitidas e cota, a captação
    líquida é derivada: (cotas_hoje - cotas_ontem) x cota_hoje. O primeiro dia
    da série não tem "ontem" e fica com o declarado (ou sem fluxo: 0, não
    inventado). `flow_source` diz qual via produziu o número.
    """
    usable = sorted(
        (i for i in informes if i.is_usable),
        key=lambda i: (i.cnpj or "", i.data or date.min),
    )
    # O ajuste de cada dia, conferido na serie de cada ETF.
    ajustes: dict[int, tuple[float, bool]] = {}
    por_cnpj: dict[str, list[InformeDiarioEtf]] = {}
    for informe in usable:
        por_cnpj.setdefault(informe.cnpj or "", []).append(informe)
    for serie in por_cnpj.values():
        for informe, ajuste in zip(serie, ajustes_do_informe(serie)):
            ajustes[id(informe)] = ajuste
    records: list[EtfDailyRecord] = []
    previous: dict[str, InformeDiarioEtf] = {}
    for informe in usable:
        captacao = informe.captacao
        resgates = informe.resgates
        ajuste = 0.0
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
        else:
            ajuste, pelo_pl = ajustes[id(informe)]
            if pelo_pl:
                flow_source = "diferenca_pl"
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
                ajuste_dia=ajuste,
                nr_cotst=informe.cotistas or 0,
                tp_fundo_classe="FIIM",
                flow_source=flow_source,
            )
        )
        previous[informe.cnpj or ""] = informe
    return records


# --- informe do mes (a pagina do Fundos.NET) -----------------------------------

# A pagina de um informe diario de ETF (`exibirDocumento?id=`) traz o MES
# inteiro do fundo, um dia por linha: cota, captacao no dia, resgate no dia, PL,
# total da carteira, cotistas e data do proximo PL. Medido em 2026-09-25: a do
# documento 1324334 (informe de 18/09/2026) tinha os dias 01 a 24/09. Uma pagina
# por ETF por mes da o que o XML so da com um documento por dia - cerca de 220
# paginas por mes contra ~4.800 XMLs.
_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
_COMPETENCIA_RE = re.compile(r"Compet[eê]ncia:\s*(\d{2})/(\d{2})/(\d{4})", re.I)
_DIA_RE = re.compile(r"^(\d{1,2})(?:\s*(C))?$")


def _coluna_do_mes(rotulo: str) -> str | None:
    """Cabecalho da tabela do mes -> campo do informe."""
    texto = normalize_text(rotulo)
    if texto == "DIA":
        return "dia"
    for pedaco, campo in (
        ("CAPTA", "captacao"), ("RESGATE", "resgates"), ("PATRIM", "pl"),
        ("CARTEIRA", "vl_total"), ("COTISTAS", "cotistas"), ("PROXIMA", "proximo_pl"),
        ("QUOTA", "cota"), ("COTA", "cota"),
    ):
        if pedaco in texto:
            return campo
    return None


def parse_informe_do_mes_html(html: str, doc_id: int | None = None) -> list[InformeDiarioEtf]:
    """A pagina do informe -> um registro por dia do mes, com captacao e resgate.

    Linha marcada como cancelada ("C = Cancelado", na legenda da pagina) fica de
    fora; dia repetido fica com a ultima linha. Pagina que nao e a do informe
    (outro documento, fora do ar, bloqueio) da lista vazia.
    """
    from bs4 import BeautifulSoup

    sopa = BeautifulSoup(html or "", "html.parser")
    texto = re.sub(r"\s+", " ", sopa.get_text(" ", strip=True))
    competencia = _COMPETENCIA_RE.search(texto)
    if not competencia:
        return []
    mes, ano = int(competencia.group(2)), int(competencia.group(3))
    cnpj_achado = _CNPJ_RE.search(texto[competencia.end():])
    cnpj = normalize_cnpj(cnpj_achado.group(0)) if cnpj_achado else None
    fundo = None
    nome = re.search(r"Nome do Fundo/Classe:\s*(.+?)\s+CNPJ:", texto)
    if nome:
        fundo = nome.group(1).strip()

    colunas: list[str | None] | None = None
    por_dia: dict[int, InformeDiarioEtf] = {}
    for linha in sopa.find_all("tr"):
        celulas = [c.get_text(" ", strip=True) for c in linha.find_all(["td", "th"])]
        if not celulas:
            continue
        if colunas is None:
            campos = [_coluna_do_mes(c) for c in celulas]
            if "dia" in campos and "captacao" in campos and "pl" in campos:
                colunas = campos
            continue
        if len(celulas) != len(colunas):
            continue
        valores = dict(zip(colunas, celulas))
        dia = _DIA_RE.match(valores.get("dia") or "")
        if not dia or dia.group(2) or any(c.strip().upper() == "C" for c in celulas[1:]):
            continue
        try:
            data = date(ano, mes, int(dia.group(1)))
        except ValueError:
            continue
        cotistas = parse_number(valores.get("cotistas"))
        por_dia[data.day] = InformeDiarioEtf(
            cnpj=cnpj,
            data=data,
            pl=parse_number(valores.get("pl")),
            cota=parse_number(valores.get("cota")),
            cotistas=int(cotistas) if cotistas is not None else None,
            captacao=parse_number(valores.get("captacao")),
            resgates=parse_number(valores.get("resgates")),
            vl_total=parse_number(valores.get("vl_total")),
            fundo=fundo,
            doc_id=doc_id,
        )
    return [por_dia[d] for d in sorted(por_dia)]


# --- persistência -------------------------------------------------------------

_CSV_COLUMNS = (
    "cnpj", "fundo", "data", "pl", "cota", "cotas", "cotistas", "captacao",
    "resgates", "vl_total", "doc_id", "unknown_tags",
)


def write_fnet_day_csv(config: Config, informes: list[InformeDiarioEtf], day: date) -> Path:
    """Um CSV por dia de coleta (`data/etf/fnet/<data>.csv`), versionado."""
    return write_informes_csv(config.etf_fnet_dir / f"{day.isoformat()}.csv", informes)


def write_informes_csv(path: Path, informes: list[InformeDiarioEtf]) -> Path:
    """Informes num CSV `;` - o formato dos dias e dos fechamentos mensais."""
    path.parent.mkdir(parents=True, exist_ok=True)
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


# Série mínima para o fluxo semanal sair do Fundos.NET por padrão: duas
# semanas de pregão com informe. Medido em 23/09: a única coleta em disco
# (2026-09-18.csv) trouxe 853 informes de 7 datas, de 10 a 18/09 - uma semana
# inteira e uma pela metade, que o "semana a semana" leria como queda.
MIN_DIAS_SERIE = 10


# Datas de informe que a competicao le como retrato recente (cotistas e fluxo da
# janela): pouco mais de uma semana de pregao, a janela que a coleta diaria dava
# antes de o mes inteiro estar em disco.
DATAS_RECENTES = 7


def dias_de_serie(config: Config) -> int:
    """Datas distintas com informe do Fundos.NET em disco."""
    return len({i.data for i in read_fnet_csvs(config) if i.data})


def read_fnet_csvs(config: Config) -> list[InformeDiarioEtf]:
    """Todos os informes diarios em disco, um por (CNPJ, data).

    Tres origens, da que menos vale para a que mais vale: os XML coletados dia
    a dia (`data/etf/fnet/`), a pagina do mes que ainda nao fechou
    (`data/etf/fnet_diario/<mes>-parcial.csv`) e a do mes fechado
    (`data/etf/fnet_diario/<mes>.csv`) - a pagina traz a ultima versao de cada
    dia. Dentro de cada origem, o arquivo mais novo vence.
    """
    caminhos: list[Path] = []
    if config.etf_fnet_dir.exists():
        caminhos += sorted(config.etf_fnet_dir.glob("*.csv"))
    diario = config.etf_fnet_diario_dir
    if diario.exists():
        caminhos += sorted(diario.glob("*-parcial.csv"))
        caminhos += sorted(p for p in diario.glob("*.csv") if not p.stem.endswith("-parcial"))
    melhor: dict[tuple[str, date], InformeDiarioEtf] = {}
    sem_chave: list[InformeDiarioEtf] = []
    for path in caminhos:
        for informe in read_informes_csv(path):
            if informe.cnpj and informe.data:
                melhor[(informe.cnpj, informe.data)] = informe
            else:
                sem_chave.append(informe)
    return list(melhor.values()) + sem_chave


def informes_recentes(config: Config, datas: int = DATAS_RECENTES) -> list[InformeDiarioEtf]:
    """Os informes das `datas` datas mais recentes em disco."""
    informes = read_fnet_csvs(config)
    ultimas = sorted({i.data for i in informes if i.data})[-datas:]
    if not ultimas:
        return []
    return [i for i in informes if i.data and i.data >= ultimas[0]]


def read_informes_csv(path: Path) -> list[InformeDiarioEtf]:
    """Um CSV gravado por `write_informes_csv`."""
    informes: list[InformeDiarioEtf] = []
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
