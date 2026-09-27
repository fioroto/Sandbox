"""Sondagem e coleta das fontes que cobrem ETF (FIIM) de verdade.

O INF_DIARIO da CVM não traz fundos de índice; três fontes públicas trazem o
que falta, cada uma um pedaço:

| Fonte        | O que dá                                              | Frequência |
|--------------|-------------------------------------------------------|------------|
| Fundos.NET   | informe diário do ETF: PL, cota, cotas, cotistas      | diária     |
| B3 listados  | ticker, CNPJ, classificação, cotas aprovadas           | cadastro   |
| COTAHIST     | preço, volume, negócios por pregão                     | diária     |

Nada aqui pôde ser exercitado contra a fonte real no ambiente em que foi
escrito (rede bloqueada); os endpoints vêm de evidência em código aberto (ver
docstring de cada módulo). Por isso a sondagem vem antes da coleta: ela grava
a resposta bruta de cada fonte em `data/cache/debug/etf-sources/` e um resumo
em `diagnostico-etf.json` - é com esse material que o mapeamento do XML do
informe se fecha e que qualquer parser é corrigido com a evidência na mão, na
mesma doutrina do `diagnose` das prateleiras.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.diagnose import Probe
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market import b3_funds, cotahist, fundosnet
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.etf_captacao_mensal import (
    FECHAMENTOS,
    caminho_do_fechamento,
    caminho_do_informe_do_mes,
    caminho_do_informe_parcial,
    fim_do_mes,
    meses_fechados,
)
from shelf_monitor.market.etf_flow import read_etf_daily_records
from shelf_monitor.texto import contagem
from shelf_monitor.normalize import name_slug, normalize_cnpj

_MAX_SAVE = 4 * 1024 * 1024


def _save(config: Config, name: str, content: bytes) -> Path:
    directory = config.etf_sources_debug_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content[:_MAX_SAVE])
    return path


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def last_business_day(reference: date | None = None) -> date:
    """Ontem, recuando fins de semana (feriado é descoberto pelo arquivo vazio)."""
    day = (reference or date.today()) - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def etf_cnpjs(catalog: dict[str, EtfMetadata]) -> set[str]:
    return {cnpj for cnpj, meta in catalog.items() if meta.is_etf}


def catalog_tickers(config: Config, catalog: dict[str, EtfMetadata]) -> set[str]:
    """Tickers conhecidos: do catálogo (extraídos do nome) e de tickers.csv."""
    tickers = {m.ticker.upper() for m in catalog.values() if m.ticker}
    for row in b3_funds.read_tickers_csv(config.etf_tickers_path):
        ticker = (row.get("ticker") or "").strip().upper()
        if ticker:
            tickers.add(ticker)
    return tickers


# --- sondagens ---------------------------------------------------------------


def probe_inf_diario_fiim(config: Config, known: set[str]) -> Probe:
    """Fase 0 do plano: o INF_DIARIO já baixado tem alguma linha de FIIM?"""
    zips = sorted(config.market_raw_dir.glob("inf_diario_fi_*.zip"))
    if not zips:
        return Probe(
            "cvm/inf_diario/FIIM", str(config.market_raw_dir), False,
            "nenhum inf_diario_fi_*.zip em cache - rode `etf` uma vez antes",
        )
    latest = zips[-1]
    records = read_etf_daily_records(latest, known)
    found = {r.cnpj for r in records}
    tipos = sorted({r.tp_fundo_classe or "" for r in records})
    return Probe(
        "cvm/inf_diario/FIIM", str(latest), bool(found),
        f"{len(found)} de {len(known)} FIIM do catálogo têm linha em {latest.name}"
        + (f" (TP_FUNDO_CLASSE: {tipos})" if tipos else ""),
        None,
        {"fiim_no_informe": len(found), "fiim_no_catalogo": len(known), "arquivo": latest.name},
    )


def probe_b3(config: Config, known: set[str]) -> list[Probe]:
    """A API de fundos listados responde para os dois tipos de ETF, e os CNPJs batem?"""
    probes: list[Probe] = []
    client = b3_funds.B3ListedFunds(config)
    try:
        client.warm_up()
    except SourceUnavailable as exc:
        probes.append(Probe("b3/portal", b3_funds.WARMUP_URL, False, str(exc)[:200]))
        return probes
    probes.append(Probe("b3/portal", b3_funds.WARMUP_URL, True, "página de ETFs listados respondeu (cookies recebidos)"))

    for type_name in b3_funds.ETF_TYPES:
        try:
            linhas = [r for r in client.list_funds(type_name, page_size=5, max_pages=1) if isinstance(r, dict)]
            amostra = [
                client.fund_detail(type_name, r["id"], r["acronym"])
                for r in linhas[:3] if r.get("id") and r.get("acronym")
            ]
        except SourceUnavailable as exc:
            probes.append(Probe(f"b3/{type_name}", f"{b3_funds.LISTED_URL}GetListFunds", False, str(exc)[:200]))
            continue
        amostra = [f for f in amostra if f is not None]
        batem = sum(1 for f in amostra if f.cnpj in known)
        evidencia = _save(config, f"b3-{type_name.lower()}.json", _json_bytes([f.to_dict() for f in amostra]))
        probes.append(
            Probe(
                f"b3/{type_name}", f"{b3_funds.LISTED_URL}GetListFunds", bool(amostra),
                f"{len(linhas)} fundos na primeira página; na amostra de {len(amostra)}, "
                f"{batem} CNPJs no catálogo FIIM e tickers {[f.ticker for f in amostra]}",
                evidencia,
                {"amostra": [f.to_dict() for f in amostra]},
            )
        )
    return probes


def probe_fundosnet(config: Config, days: int = 7, sample: int = 3) -> list[Probe]:
    probes: list[Probe] = []
    client = fundosnet.FundosNetClient(config)
    try:
        types = client.fund_types()
        categories = client.categories()
    except SourceUnavailable as exc:
        probes.append(Probe("fundosnet/portal", fundosnet.BASE_URL, False, str(exc)[:200]))
        return probes

    etf_types = {label: value for label, value in types.items() if "ETF" in label.upper()}
    if not etf_types:
        etf_types = dict(fundosnet.ETF_FUND_TYPES)
    probes.append(
        Probe(
            "fundosnet/portal", f"{fundosnet.BASE_URL}abrirGerenciadorDocumentosCVM", bool(types),
            f"tipos de fundo: {types or 'não lidos'}; ETF: {etf_types}",
            _save(config, "fnet-tipos.json", _json_bytes({"tipos": types, "categorias": categories})),
            {"etf_types": etf_types},
        )
    )

    end = date.today()
    start = end - timedelta(days=days)
    for label, type_id in etf_types.items():
        # 1. tipo de documento "Informe Diário" para este tipo de fundo
        doc_type_id = fundosnet.DOC_TYPE_INFORME_DIARIO
        try:
            doc_types = client.document_types(type_id)
            hit = next(
                (row for row in doc_types if "DIARIO" in fundosnet._norm_tag(str(row.get("descricao", "")))),
                None,
            )
            if hit and str(hit.get("id", "")).isdigit():
                doc_type_id = int(hit["id"])
            probes.append(
                Probe(
                    f"fundosnet/tipos-doc/{label}", f"{fundosnet.BASE_URL}listarTodosTiposPorCategoriaETipoFundo",
                    hit is not None,
                    f"{len(doc_types)} tipos na categoria 'Informes Periódicos'; Informe Diário = {doc_type_id}"
                    + ("" if hit else " (não listado - usando 64 por hipótese)"),
                    _save(config, f"fnet-tipos-doc-{type_id}.json", _json_bytes(doc_types)),
                    {"informe_diario_id": doc_type_id},
                )
            )
        except SourceUnavailable as exc:
            probes.append(Probe(f"fundosnet/tipos-doc/{label}", fundosnet.BASE_URL, False, str(exc)[:200]))

        # 2. informes publicados nos últimos dias
        try:
            docs = list(client.search(type_id, doc_type_id=doc_type_id, start=start, end=end, max_pages=5))
        except SourceUnavailable as exc:
            probes.append(Probe(f"fundosnet/informes/{label}", fundosnet.BASE_URL, False, str(exc)[:200]))
            continue
        example = ""
        if docs:
            first = docs[0]
            example = f"; ex.: {first.fundo[:40]} ref. {first.data_referencia}"
        probes.append(
            Probe(
                f"fundosnet/informes/{label}", f"{fundosnet.BASE_URL}pesquisarGerenciadorDocumentosDados",
                bool(docs), f"{len(docs)} informes diários em {days} dias{example}",
                _save(config, f"fnet-informes-{type_id}.json", _json_bytes([d.__dict__ for d in docs[:200]])),
                {"count": len(docs)},
            )
        )

        # 3. o XML em si - é aqui que o mapeamento de campos se fecha
        for doc in docs[:sample]:
            try:
                content, content_type = client.download(doc.id)
            except SourceUnavailable as exc:
                probes.append(Probe(f"fundosnet/xml/{label}", doc.url, False, str(exc)[:200]))
                continue
            ext = "xml" if content.lstrip().startswith(b"<") else "bin"
            evidence = _save(config, f"fnet-doc-{doc.id}.{ext}", content)
            informes = fundosnet.parse_informe_diario_xml(content, doc_id=doc.id, fundo=doc.fundo)
            recognized = {
                k: v for k, v in (informes[0].__dict__.items() if informes else [])
                if k in ("cnpj", "data", "pl", "cota", "cotas", "cotistas", "captacao", "resgates") and v is not None
            }
            unknown = informes[0].unknown_tags if informes else []
            probes.append(
                Probe(
                    f"fundosnet/xml/{label}", doc.url, bool(informes and informes[0].is_usable),
                    f"{content_type or 'sem content-type'}; campos reconhecidos: {sorted(recognized)}; "
                    f"tags não mapeadas: {unknown[:12]}",
                    evidence,
                    {"recognized": recognized, "unknown_tags": unknown, "usable": bool(informes and informes[0].is_usable)},
                )
            )
    return probes


def probe_cotahist(config: Config, tickers: set[str], day: date | None = None) -> Probe:
    day = day or last_business_day()
    path = cotahist.fetch_cotahist_day(config, day)
    if path is None:
        return Probe("b3/cotahist", cotahist.url_for_day(day), False, f"sem arquivo para {day} (fonte fora ou dia sem pregão)")
    all_quotas = cotahist.read_cotahist(path, tickers=None)
    matched = cotahist.read_cotahist(path, tickers=tickers) if tickers else []
    sample = sorted({r.codneg for r in all_quotas})[:15]
    return Probe(
        "b3/cotahist", cotahist.url_for_day(day), bool(all_quotas),
        f"{len(all_quotas)} cotas de fundo listado em {day}; {len(matched)} casaram com {len(tickers)} tickers conhecidos; ex.: {sample}",
        path,
        {"fund_quotas": len(all_quotas), "matched": len(matched), "tickers_known": len(tickers)},
    )


def run_probes(config: Config, catalog: dict[str, EtfMetadata]) -> list[Probe]:
    config.ensure_dirs()
    known = etf_cnpjs(catalog)
    probes: list[Probe] = [probe_inf_diario_fiim(config, known)]
    probes.extend(probe_fundosnet(config))
    probes.extend(probe_b3(config, known))
    probes.append(probe_cotahist(config, catalog_tickers(config, catalog)))

    report = {"generated_at": datetime.now().isoformat(), "probes": [p.to_dict() for p in probes]}
    # default=str: a evidencia carrega o que a fonte devolveu, e o informe do
    # Fundos.NET traz `date`. Sem isso a gravacao explodia justamente quando a
    # sondagem encontrava dados - o oposto do que este arquivo existe para fazer.
    path = config.etf_sources_debug_dir / "diagnostico-etf.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return probes


# --- coleta ------------------------------------------------------------------


def _load_probe_findings(config: Config) -> dict:
    path = config.etf_sources_debug_dir / "diagnostico-etf.json"
    if not path.exists():
        return {}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    findings: dict = {}
    for probe in report.get("probes", []):
        findings[probe.get("source", "")] = probe.get("findings") or {}
    return findings


def match_informe_to_catalog(informe: fundosnet.InformeDiarioEtf, catalog: dict[str, EtfMetadata]) -> str | None:
    """CNPJ do XML quando ele traz; senão, o nome do fundo contra o catálogo FIIM."""
    if informe.cnpj and informe.cnpj in catalog:
        return informe.cnpj
    slug = name_slug(informe.fundo)
    if not slug:
        return None
    for cnpj, meta in catalog.items():
        if meta.is_etf and name_slug(meta.name) == slug:
            return cnpj
    return None


def collect_b3(config: Config, catalog: dict[str, EtfMetadata], day: date) -> dict:
    """Ticker e CNPJ de cada ETF listado na B3, de renda variavel e de renda fixa.

    Grava `data/etf/tickers.csv` (so os ETFs que casam com o catalogo) e a foto
    do dia em `data/etf/b3/`. O CNPJ do detalhe da B3 e o do fundo; quando ele
    nao e o da classe no catalogo da CVM, o nome do fundo decide - a mesma regra
    do informe do Fundos.NET.
    """
    client = b3_funds.B3ListedFunds(config)
    funds: list[b3_funds.FundoListadoB3] = []
    por_tipo: dict[str, int] = {}
    for type_name in b3_funds.ETF_TYPES:
        antes = len(funds)
        funds.extend(client.funds_of_type(type_name))
        por_tipo[type_name] = len(funds) - antes
    no_catalogo = []
    for fund in funds:
        cnpj = _cnpj_no_catalogo(fund, catalog)
        if cnpj:
            fund.cnpj = cnpj
            no_catalogo.append(fund)
    snapshot = b3_funds.save_b3_snapshot(config, funds, day) if funds else None
    rows = b3_funds.merge_tickers(b3_funds.read_tickers_csv(config.etf_tickers_path), no_catalogo, day)
    if no_catalogo:
        b3_funds.write_tickers_csv(config.etf_tickers_path, rows)
    return {
        "por_tipo": por_tipo, "funds": len(funds), "no_catalogo": len(no_catalogo),
        "snapshot": str(snapshot) if snapshot else None,
        "tickers": sum(1 for r in rows if r.get("fonte") == "b3"),
    }


def _cnpj_no_catalogo(fund: b3_funds.FundoListadoB3, catalog: dict[str, EtfMetadata]) -> str | None:
    """O CNPJ do catalogo para um fundo da B3: o dele, ou o do ETF de mesmo nome."""
    if fund.cnpj and fund.cnpj in catalog:
        return fund.cnpj
    informe = fundosnet.InformeDiarioEtf(cnpj=None, fundo=fund.company_name)
    return match_informe_to_catalog(informe, catalog)


# Ticker muda pouco (entra ETF novo todo mes): a lista da B3 e refeita quando a
# ultima coleta tem mais de 30 dias.
VALIDADE_DOS_TICKERS = 30


def tickers_desatualizados(config: Config, hoje: date) -> bool:
    """Sem coleta da B3 em `tickers.csv`, ou com a mais recente ha mais de 30 dias."""
    datas = []
    for row in b3_funds.read_tickers_csv(config.etf_tickers_path):
        if (row.get("fonte") or "").strip().lower() != "b3":
            continue
        try:
            datas.append(date.fromisoformat((row.get("atualizado_em") or "")[:10]))
        except ValueError:
            continue
    return not datas or (hoje - max(datas)).days > VALIDADE_DOS_TICKERS


def tickers_do_arquivo(config: Config) -> dict[str, str]:
    """CNPJ -> ticker de `tickers.csv`, na ordem de precedencia do catalogo."""
    return {
        normalize_cnpj(row.get("cnpj")): (row.get("ticker") or "").strip().upper()
        for row in b3_funds.read_tickers_csv(config.etf_tickers_path)
        if normalize_cnpj(row.get("cnpj")) and (row.get("ticker") or "").strip()
    }


def collect_fundosnet(config: Config, catalog: dict[str, EtfMetadata], day: date, days: int = 3) -> dict:
    """Informes diários entregues nos últimos `days` dias, um XML por documento.

    A entrega acontece no dia útil seguinte à data-base, então a janela de
    alguns dias garante que o informe de ontem já esteja lá. Duplicatas por
    (CNPJ, data-base) ficam com a versão entregue por último.
    """
    findings = _load_probe_findings(config)
    client = fundosnet.FundosNetClient(config)
    try:
        types = {k: v for k, v in client.fund_types().items() if "ETF" in k.upper()} or dict(fundosnet.ETF_FUND_TYPES)
    except SourceUnavailable:
        types = dict(fundosnet.ETF_FUND_TYPES)

    start = day - timedelta(days=days)
    best: dict[tuple[str, date], fundosnet.InformeDiarioEtf] = {}
    unmatched = 0
    downloaded = 0
    for label, type_id in types.items():
        doc_type_id = int(findings.get(f"fundosnet/tipos-doc/{label}", {}).get("informe_diario_id") or fundosnet.DOC_TYPE_INFORME_DIARIO)
        for doc in client.search(type_id, doc_type_id=doc_type_id, start=start, end=day):
            try:
                content, _ = client.download(doc.id)
            except SourceUnavailable:
                continue
            downloaded += 1
            for informe in fundosnet.parse_informe_diario_xml(content, doc_id=doc.id, fundo=doc.fundo):
                cnpj = match_informe_to_catalog(informe, catalog)
                if cnpj is None:
                    unmatched += 1
                    continue
                informe.cnpj = cnpj
                if informe.data is None:
                    informe.data = doc.data_referencia
                if not informe.is_usable:
                    unmatched += 1
                    continue
                key = (cnpj, informe.data)
                current = best.get(key)
                if current is None or (informe.doc_id or 0) >= (current.doc_id or 0):
                    best[key] = informe
    informes = list(best.values())
    path = fundosnet.write_fnet_day_csv(config, informes, day) if informes else None
    unknown = sorted({tag for i in informes for tag in i.unknown_tags})
    return {"documentos": downloaded, "informes": len(informes), "sem_cnpj_ou_incompletos": unmatched,
            "arquivo": str(path) if path else None, "tags_nao_mapeadas": unknown[:30]}


# Data-base aceita ate 7 dias antes do fim do mes (o ultimo pregao pode cair
# antes do dia 31) e entrega ate 7 dias depois: em 2026-09-24, parte dos ETFs
# ainda nao tinha entregue o informe de 31/08 no dia 01/09.
_JANELA_DO_FECHAMENTO = 7
# Documentos tentados por fundo ate achar um informe utilizavel.
_TENTATIVAS_POR_FUNDO = 3


def collect_fechamentos_mensais(
    config: Config,
    catalog: dict[str, EtfMetadata],
    day: date | None = None,
    meses: list[str] | None = None,
    force: bool = False,
) -> dict:
    """Fechamento de cada mes no Fundos.NET: o ultimo informe de cada ETF no mes.

    Um documento por ETF por mes - cerca de 220, contra ~4.800 de um mes inteiro
    de informes diarios. Dele saem o PL das pontas de cada mes, o documento de
    partida da pagina do mes (`collect_informes_do_mes`) e, sem ela, a captacao
    pela variacao de cotas entre dois fechamentos (`etf_captacao_mensal`). Mes ja gravado nao e baixado de
    novo, e mes com a entrega ainda aberta nao e baixado: gravado pela metade,
    viraria cache de um fechamento incompleto.
    """
    meses = meses or meses_fechados(day or date.today(), FECHAMENTOS)
    resumo: dict = {"meses": {}, "documentos": 0}
    client: fundosnet.FundosNetClient | None = None
    for mes in meses:
        path = caminho_do_fechamento(config, mes)
        if path.exists() and not force:
            resumo["meses"][mes] = "em disco"
            continue
        client = client or fundosnet.FundosNetClient(config)
        informes, baixados = _fechamento_do_mes(config, client, catalog, fim_do_mes(mes))
        resumo["documentos"] += baixados
        if informes:
            fundosnet.write_informes_csv(path, informes)
        resumo["meses"][mes] = contagem(len(informes), "ETF", "ETFs")
    return resumo


def _tipos_de_etf(config: Config, client: fundosnet.FundosNetClient) -> list[tuple[int, int]]:
    """(tipoFundo, tipo do documento de informe diario) de cada tipo de ETF."""
    findings = _load_probe_findings(config)
    try:
        types = {k: v for k, v in client.fund_types().items() if "ETF" in k.upper()} or dict(fundosnet.ETF_FUND_TYPES)
    except SourceUnavailable:
        types = dict(fundosnet.ETF_FUND_TYPES)
    return [
        (
            type_id,
            int(findings.get(f"fundosnet/tipos-doc/{label}", {}).get("informe_diario_id")
                or fundosnet.DOC_TYPE_INFORME_DIARIO),
        )
        for label, type_id in types.items()
    ]


def _fechamento_do_mes(
    config: Config, client: fundosnet.FundosNetClient, catalog: dict[str, EtfMetadata], fim: date
) -> tuple[list[fundosnet.InformeDiarioEtf], int]:
    """(ultimo informe de cada ETF com data-base ate `fim`, documentos baixados).

    Tenta os documentos de cada fundo do mais recente ao mais antigo - versao
    ativa antes da inativa - e para no primeiro que da um informe utilizavel:
    uma reapresentacao que o leitor nao entenda nao pode apagar o fundo do mes.
    """
    janela = timedelta(days=_JANELA_DO_FECHAMENTO)
    por_fundo: dict[tuple[str, str], list[fundosnet.DocumentoFnet]] = {}
    for type_id, doc_type_id in _tipos_de_etf(config, client):
        for doc in client.search(type_id, doc_type_id=doc_type_id, start=fim - janela, end=fim + janela):
            ref = doc.data_referencia
            if ref is None or ref > fim or ref < fim - janela:
                continue
            por_fundo.setdefault((doc.cnpj_fundo or "", doc.fundo), []).append(doc)
    melhor: dict[str, fundosnet.InformeDiarioEtf] = {}
    baixados = 0
    for docs in por_fundo.values():
        docs.sort(key=lambda d: (d.data_referencia, (d.situacao or "") == "A", d.id), reverse=True)
        for doc in docs[:_TENTATIVAS_POR_FUNDO]:
            try:
                content, _ = client.download(doc.id)
            except SourceUnavailable:
                continue
            baixados += 1
            achou = False
            for informe in fundosnet.parse_informe_diario_xml(content, doc_id=doc.id, fundo=doc.fundo):
                cnpj = match_informe_to_catalog(informe, catalog)
                if cnpj is None:
                    continue
                informe.cnpj = cnpj
                informe.data = informe.data or doc.data_referencia
                if not informe.is_usable or not informe.data or informe.data > fim:
                    continue
                achou = True
                atual = melhor.get(cnpj)
                if atual is None or (informe.data, informe.doc_id or 0) > (atual.data, atual.doc_id or 0):
                    melhor[cnpj] = informe
            if achou:
                break
    return list(melhor.values()), baixados


# Pausa entre paginas do Fundos.NET: a coleta do mes vai uma pagina por ETF, em
# sequencia (~0,1 s cada, medido em 2026-09-25).
_PAUSA_ENTRE_PAGINAS = 0.1
# Paginas seguidas sem o informe que interrompem a coleta: o site saiu do ar ou
# passou a responder outra coisa, e insistir so gera carga.
_FALHAS_SEGUIDAS = 15


def collect_informes_do_mes(
    config: Config,
    catalog: dict[str, EtfMetadata],
    day: date | None = None,
    meses: list[str] | None = None,
    force: bool = False,
) -> dict:
    """O mes inteiro do informe diario de cada ETF: captacao, resgate, PL e cota
    de todo dia, uma pagina do Fundos.NET por ETF (`fundosnet.parse_informe_do_mes_html`).

    O documento de partida e o do fechamento do mes (`collect_fechamentos_mensais`,
    ja em disco): a pagina dele traz o mes todo. Os meses sao os da visao mensal
    mais o mes-base - o fechamento que so abre a janela nao precisa. Mes gravado
    so busca de novo os ETFs que faltaram (pagina que falhou); `force` refaz.
    """
    meses = meses or meses_fechados(day or date.today(), FECHAMENTOS)[1:]
    resumo: dict = {"meses": {}, "paginas": 0, "falhas": 0}
    client: fundosnet.FundosNetClient | None = None
    seguidas = 0
    for mes in meses:
        fechamento = caminho_do_fechamento(config, mes)
        if not fechamento.exists():
            resumo["meses"][mes] = "sem fechamento em disco"
            continue
        documentos = {
            i.cnpj: i.doc_id for i in fundosnet.read_informes_csv(fechamento) if i.cnpj and i.doc_id
        }
        path = caminho_do_informe_do_mes(config, mes)
        existentes = [] if force or not path.exists() else fundosnet.read_informes_csv(path)
        feitos = {i.cnpj for i in existentes}
        faltam = [cnpj for cnpj in documentos if cnpj not in feitos]
        if not faltam:
            resumo["meses"][mes] = "em disco"
            continue
        client = client or fundosnet.FundosNetClient(config)
        novos: list[fundosnet.InformeDiarioEtf] = []
        for cnpj in faltam:
            if seguidas >= _FALHAS_SEGUIDAS:
                break
            try:
                linhas = fundosnet.parse_informe_do_mes_html(
                    client.exibir(documentos[cnpj]), doc_id=documentos[cnpj]
                )
            except SourceUnavailable:
                linhas = []
            resumo["paginas"] += 1
            # A pagina e a do documento do fechamento deste ETF: o CNPJ que vale
            # e o do catalogo, como no fechamento.
            linhas = [linha for linha in linhas if linha.data and f"{linha.data:%Y%m}" == mes]
            for linha in linhas:
                linha.cnpj = cnpj
            if linhas:
                novos.extend(linhas)
                seguidas = 0
            else:
                resumo["falhas"] += 1
                seguidas += 1
            time.sleep(_PAUSA_ENTRE_PAGINAS)
        todos = existentes + novos
        if novos:
            fundosnet.write_informes_csv(path, todos)
        resumo["meses"][mes] = f"{len({i.cnpj for i in todos})} de {contagem(len(documentos), 'ETF', 'ETFs')}"
        if seguidas >= _FALHAS_SEGUIDAS:
            resumo["interrompida"] = (
                f"{_FALHAS_SEGUIDAS} páginas seguidas sem o informe em {mes}: "
                "a coleta parou para não insistir"
            )
            break
    return resumo


# Entregas dos ultimos dias que dao o documento de partida do mes em aberto: o
# informe sai no dia util seguinte a data-base, e a folga cobre feriado e
# reapresentacao.
_JANELA_DO_MES_ABERTO = 10


def meses_abertos(hoje: date) -> list[str]:
    """Os meses depois do ultimo fechado, ate o corrente (AAAAMM)."""
    ultimo = meses_fechados(hoje, 1)[-1]
    ano, mes = int(ultimo[:4]), int(ultimo[4:])
    abertos: list[str] = []
    while True:
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
        competencia = f"{ano}{mes:02d}"
        if competencia > f"{hoje:%Y%m}":
            return abertos
        abertos.append(competencia)


def _gravado_em(path: Path, dia: date) -> bool:
    return path.exists() and date.fromtimestamp(path.stat().st_mtime) == dia


def collect_informes_do_mes_aberto(
    config: Config,
    catalog: dict[str, EtfMetadata],
    day: date | None = None,
    force: bool = False,
) -> dict:
    """O informe do mes que ainda nao fechou, ate o ultimo dia entregue.

    Sem fechamento em disco, o documento de partida de cada ETF e o mais recente
    que a busca do Fundos.NET devolve nos ultimos dias; a pagina dele traz o mes
    ate ali, e o CNPJ da pagina liga ao catalogo. Grava a parte
    (`<mes>-parcial.csv`) e refaz uma vez por dia: e o que leva o fluxo semanal
    ao ultimo informe. O mes fechado sai de `collect_informes_do_mes`.
    """
    hoje = day or date.today()
    abertos = meses_abertos(hoje)
    resumo: dict = {"meses": {}, "paginas": 0, "falhas": 0}
    pendentes = [m for m in abertos if force or not _gravado_em(caminho_do_informe_parcial(config, m), hoje)]
    for mes in abertos:
        if mes not in pendentes:
            resumo["meses"][mes] = "em disco (hoje)"
    if not pendentes:
        return resumo
    client = fundosnet.FundosNetClient(config)
    inicio = max(
        min(date(int(m[:4]), int(m[4:]), 1) for m in pendentes),
        hoje - timedelta(days=_JANELA_DO_MES_ABERTO),
    )
    ultimo_doc: dict[tuple[str, str], fundosnet.DocumentoFnet] = {}
    for type_id, doc_type_id in _tipos_de_etf(config, client):
        for doc in client.search(type_id, doc_type_id=doc_type_id, start=inicio, end=hoje):
            ref = doc.data_referencia
            if ref is None or f"{ref:%Y%m}" not in pendentes:
                continue
            chave = (f"{ref:%Y%m}", doc.fundo)
            atual = ultimo_doc.get(chave)
            if atual is None or (ref, doc.id) > (atual.data_referencia, atual.id):
                ultimo_doc[chave] = doc
    por_mes: dict[str, dict[str, list[fundosnet.InformeDiarioEtf]]] = {m: {} for m in pendentes}
    seguidas = 0
    for (mes, _fundo), doc in sorted(ultimo_doc.items(), key=lambda kv: kv[1].id):
        if seguidas >= _FALHAS_SEGUIDAS:
            resumo["interrompida"] = (
                f"{_FALHAS_SEGUIDAS} páginas seguidas sem o informe em {mes}: "
                "a coleta parou para não insistir"
            )
            break
        try:
            linhas = fundosnet.parse_informe_do_mes_html(client.exibir(doc.id), doc_id=doc.id)
        except SourceUnavailable:
            linhas = []
        resumo["paginas"] += 1
        time.sleep(_PAUSA_ENTRE_PAGINAS)
        linhas = [linha for linha in linhas if linha.data and f"{linha.data:%Y%m}" == mes]
        if not linhas:
            resumo["falhas"] += 1
            seguidas += 1
            continue
        seguidas = 0
        cnpj = match_informe_to_catalog(
            fundosnet.InformeDiarioEtf(cnpj=linhas[0].cnpj, fundo=linhas[0].fundo or doc.fundo), catalog
        )
        if cnpj is None:
            continue
        for linha in linhas:
            linha.cnpj = cnpj
        # Dois documentos do mesmo ETF: fica a pagina que vai mais longe.
        atual = por_mes[mes].get(cnpj)
        if atual is None or linhas[-1].data > atual[-1].data:
            por_mes[mes][cnpj] = linhas
    for mes, por_etf in por_mes.items():
        linhas = [linha for dias in por_etf.values() for linha in dias]
        if linhas:
            fundosnet.write_informes_csv(caminho_do_informe_parcial(config, mes), linhas)
        ultimo = max((linha.data for linha in linhas), default=None)
        resumo["meses"][mes] = (
            f"{contagem(len(por_etf), 'ETF', 'ETFs')}"
            + (f", até {ultimo:%d/%m/%Y}" if ultimo else "")
        )
    return resumo


def collect_cotahist(config: Config, catalog: dict[str, EtfMetadata], day: date) -> dict:
    tickers = catalog_tickers(config, catalog)
    path = cotahist.fetch_cotahist_day(config, day)
    if path is None:
        return {"dia": day.isoformat(), "linhas": 0, "arquivo": None, "motivo": "sem arquivo (fonte fora ou dia sem pregão)"}
    rows = cotahist.read_cotahist(path, tickers=tickers or None)
    out = cotahist.write_cotahist_day_csv(config, rows, day) if rows else None
    return {"dia": day.isoformat(), "linhas": len(rows), "tickers_conhecidos": len(tickers), "arquivo": str(out) if out else None}


def collect_sources(
    config: Config, catalog: dict[str, EtfMetadata], sources: list[str], day: date | None = None
) -> dict[str, dict]:
    """Uma foto do dia de cada fonte pedida. Uma fonte que falha não derruba as outras."""
    config.ensure_dirs()
    day = day or last_business_day()
    summary: dict[str, dict] = {}
    runners = {
        "b3": collect_b3, "fnet": collect_fundosnet, "fnet_mensal": collect_fechamentos_mensais,
        "fnet_diario": collect_informes_do_mes, "fnet_aberto": collect_informes_do_mes_aberto,
        "cotahist": collect_cotahist,
    }
    for name in sources:
        runner = runners.get(name)
        if runner is None:
            summary[name] = {
                "erro": "fonte desconhecida (use b3, fnet, fnet_mensal, fnet_diario, fnet_aberto, cotahist)"
            }
            continue
        try:
            summary[name] = runner(config, catalog, day)
        except SourceUnavailable as exc:
            summary[name] = {"erro": str(exc)[:200]}
        except Exception as exc:  # noqa: BLE001 - uma fonte nao derruba as outras
            summary[name] = {"erro": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return summary
