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
from datetime import date, datetime, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.diagnose import Probe
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market import b3_funds, cotahist, fundosnet
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.etf_flow import read_etf_daily_records
from shelf_monitor.normalize import name_slug

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
    probes: list[Probe] = []
    client = b3_funds.B3ListedFunds(config)
    try:
        client.warm_up()
    except SourceUnavailable as exc:
        probes.append(Probe("b3/portal", b3_funds.WARMUP_URL, False, str(exc)[:200]))
        return probes
    probes.append(Probe("b3/portal", b3_funds.WARMUP_URL, True, "página de ETFs listados respondeu (cookies recebidos)"))

    found = client.discover_etf_types(known)
    etf_types = sorted(t for t, info in found.items() if info["cnpj_hits"] > 0)
    evidence = _save(config, "b3-typefund.json", _json_bytes(found))
    detail = f"{len(found)} tipos responderam à listagem; tipos com CNPJ no catálogo FIIM: {etf_types or 'nenhum'}"
    if found and not etf_types:
        detail += " - a listagem responde, mas nenhum CNPJ bateu: veja b3-typefund.json"
    probes.append(
        Probe(
            "b3/typeFund", f"{b3_funds.FUNDS_CALL_URL}GetListedFundsSIG", bool(etf_types), detail, evidence,
            {"etf_types": etf_types, "types": {str(k): v for k, v in found.items()}},
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
    findings = _load_probe_findings(config).get("b3/typeFund", {})
    etf_types = [int(t) for t in findings.get("etf_types") or []]
    client = b3_funds.B3ListedFunds(config)
    if not etf_types:
        found = client.discover_etf_types(etf_cnpjs(catalog))
        etf_types = sorted(t for t, info in found.items() if info["cnpj_hits"] > 0)
    funds: list[b3_funds.FundoListadoB3] = []
    for type_id in etf_types:
        funds.extend(client.funds_of_type(type_id))
    snapshot = b3_funds.save_b3_snapshot(config, funds, day) if funds else None
    rows = b3_funds.merge_tickers(b3_funds.read_tickers_csv(config.etf_tickers_path), funds, day)
    if funds:
        b3_funds.write_tickers_csv(config.etf_tickers_path, rows)
    return {"etf_types": etf_types, "funds": len(funds), "snapshot": str(snapshot) if snapshot else None,
            "tickers": sum(1 for r in rows if r.get("fonte") == "b3")}


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
    runners = {"b3": collect_b3, "fnet": collect_fundosnet, "cotahist": collect_cotahist}
    for name in sources:
        runner = runners.get(name)
        if runner is None:
            summary[name] = {"erro": "fonte desconhecida (use b3, fnet, cotahist)"}
            continue
        try:
            summary[name] = runner(config, catalog, day)
        except SourceUnavailable as exc:
            summary[name] = {"erro": str(exc)[:200]}
        except Exception as exc:  # noqa: BLE001 - uma fonte nao derruba as outras
            summary[name] = {"erro": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return summary
