"""Diagnostico das fontes de prateleira.

Os coletores foram escritos a partir de documentacao, nao de observacao dos
sites. Quando um deles nao traz nada, a pergunta e sempre a mesma: o site
respondeu? respondeu o que se esperava? Este modulo responde isso e grava a
resposta bruta em disco, para que o problema possa ser corrigido com a
evidencia na mao em vez de por tentativa.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from shelf_monitor.config import (
    OPEN_FINANCE_FUNDS_PATH,
    PLATFORMS,
    Config,
    PlatformConfig,
)
from shelf_monitor.http import SourceUnavailable, build_session, describe_error, get
from shelf_monitor.normalize import normalize_text

_CNPJ_RE = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}")
_MAX_SAVE = 4 * 1024 * 1024
# O mesmo page-size que a coleta usa, para a sondagem medir o caso real.
_PROBE_PAGE_SIZE = 500


@dataclass
class Probe:
    """O resultado de uma tentativa contra uma fonte."""

    source: str
    url: str
    ok: bool
    detail: str
    evidence: Path | None = None
    findings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "url": self.url,
            "ok": self.ok,
            "detail": self.detail,
            "evidence": str(self.evidence) if self.evidence else None,
            "findings": self.findings,
        }


def _save(config: Config, name: str, content: bytes) -> Path | None:
    """Grava a resposta bruta para inspecao posterior."""
    directory = config.cache_dir / "debug"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content[:_MAX_SAVE])
    return path


def probe_open_finance(config: Config, platform: PlatformConfig) -> list[Probe]:
    """Testa a descoberta no diretorio e o endpoint /funds da instituicao."""
    probes: list[Probe] = []
    session = build_session(config)

    try:
        response = get(session, config.directory_url, config, accept="application/json")
        participants = response.json()
    except (SourceUnavailable, ValueError) as exc:
        probes.append(
            Probe("open-finance/diretorio", config.directory_url, False, str(exc)[:200])
        )
        return probes

    if not isinstance(participants, list):
        probes.append(
            Probe(
                "open-finance/diretorio",
                config.directory_url,
                False,
                f"esperava uma lista, veio {type(participants).__name__}",
                _save(config, "of-directory.json", response.content),
            )
        )
        return probes

    # Quais organizacoes casam com a marca, e quais endpoints elas publicam.
    brands = [normalize_text(b) for b in platform.open_finance_brands if b.strip()]
    matched: list[dict] = []
    for participant in participants:
        org = normalize_text(
            participant.get("OrganisationName")
            or participant.get("RegisteredName")
            or ""
        )
        if not any(
            re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", org) for brand in brands
        ):
            continue
        families: set[str] = set()
        endpoints: list[str] = []
        for server in participant.get("AuthorisationServers") or []:
            for resource in server.get("ApiResources") or []:
                families.add(str(resource.get("ApiFamilyType", "")))
                for endpoint in resource.get("ApiDiscoveryEndpoints") or []:
                    uri = endpoint.get("ApiEndpoint") or ""
                    if "investment" in uri.lower() or OPEN_FINANCE_FUNDS_PATH in uri:
                        endpoints.append(uri)
        matched.append(
            {
                "organisation": participant.get("OrganisationName"),
                "api_families": sorted(f for f in families if f),
                "investment_endpoints": endpoints,
            }
        )

    probes.append(
        Probe(
            "open-finance/diretorio",
            config.directory_url,
            bool(matched),
            f"{len(participants)} participantes; {len(matched)} casaram com "
            f"{platform.open_finance_brands}",
            _save(
                config,
                f"of-directory-{platform.platform_id}.json",
                json.dumps(matched, indent=2, ensure_ascii=False).encode(),
            ),
            {"matched": matched[:5]},
        )
    )

    funds_url = next(
        (
            e.split(OPEN_FINANCE_FUNDS_PATH)[0].rstrip("/") + OPEN_FINANCE_FUNDS_PATH
            for m in matched
            for e in m["investment_endpoints"]
            if OPEN_FINANCE_FUNDS_PATH in e
        ),
        None,
    )
    if platform.open_finance_base_url:
        funds_url = platform.open_finance_base_url.rstrip("/") + OPEN_FINANCE_FUNDS_PATH

    if not funds_url:
        probes.append(
            Probe(
                f"open-finance/{platform.platform_id}",
                "-",
                False,
                "nenhum endpoint /funds encontrado no diretorio para esta marca",
            )
        )
        return probes

    # Pede a pagina cheia de proposito: o que interessa medir e se o servidor
    # respeita o page-size que a coleta usa. Uma casa que devolve 10 quando se
    # pede 500 pagina 50x mais - e era isso que estourava o teto antigo.
    requested_page_size = _PROBE_PAGE_SIZE
    try:
        response = get(
            session, funds_url, config,
            params={"page": 1, "page-size": requested_page_size},
            accept="application/json",
        )
        payload = response.json()
    except (SourceUnavailable, ValueError) as exc:
        probes.append(
            Probe(f"open-finance/{platform.platform_id}", funds_url, False, str(exc)[:200])
        )
        return probes

    from shelf_monitor.collectors.open_finance import _flatten_funds

    items = _flatten_funds(payload.get("data") if isinstance(payload, dict) else payload)
    meta = payload.get("meta") if isinstance(payload, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    total_records = meta.get("totalRecords")
    total_pages = meta.get("totalPages")

    findings = {
        "page_size_pedido": requested_page_size,
        "page_size_efetivo": len(items),
        "total_records": total_records,
        "total_pages": total_pages,
        "first_item_keys": sorted(items[0]) if items else [],
    }
    detail = (
        f"{len(items)} fundos na pagina 1 (pedi {requested_page_size}); "
        f"totalRecords={total_records}, totalPages={total_pages}"
    )
    if items and len(items) < requested_page_size and (total_pages or 0) > 1:
        detail += " - o servidor ignora o page-size pedido"
    probes.append(
        Probe(
            f"open-finance/{platform.platform_id}",
            funds_url,
            bool(items),
            detail,
            _save(config, f"of-funds-{platform.platform_id}.json", response.content),
            findings,
        )
    )
    return probes


def probe_browser(config: Config, platform: PlatformConfig) -> Probe:
    """Abre a lista num Chromium real e grava o que ele viu.

    E a sondagem que responde a pergunta que o `requests` nao responde: a lista
    aparece na tela? por qual XHR ela chega? A evidencia (HTML renderizado,
    todas as requisicoes, os payloads JSON e um screenshot) fica em
    data/cache/debug/<plataforma>/ - com ela da para escrever o parser certo em
    vez de supor mais uma vez.
    """
    from shelf_monitor.collectors.browser import (
        dump_capture,
        extract_fund_dicts,
        extract_from_dom,
        render_page,
        to_offer,
    )

    if not platform.shelf_url:
        return Probe(f"navegador/{platform.platform_id}", "-", False, "sem shelf_url")

    try:
        capture = render_page(
            platform.shelf_url,
            user_agent=config.user_agent,
            executable_path=config.chromium_path,
            screenshot=True,
            block_assets=False,
            headed=config.browser_headed,
            channel=config.browser_channel,
        )
    except SourceUnavailable as exc:
        return Probe(
            f"navegador/{platform.platform_id}", platform.shelf_url, False, str(exc)[:200]
        )

    evidence = dump_capture(config.debug_dir, platform.platform_id, capture)

    from_json = {
        offer.identity
        for _, payload in capture.payloads
        for item in extract_fund_dicts(payload)
        for offer in [to_offer(platform.platform_id, item, capture.final_url)]
        if offer
    }
    from_dom = {
        offer.identity
        for offer in extract_from_dom(
            platform.platform_id, capture.html, capture.final_url
        )
    }

    findings = {
        "status": capture.status,
        "final_url": capture.final_url,
        "html_bytes": len(capture.html),
        "requisicoes": len(capture.responses),
        "respostas_json": capture.json_responses,
        "payloads_lidos": len(capture.payloads),
        "fundos_pelo_json": len(from_json),
        "fundos_pelo_dom": len(from_dom),
        "fundos_unicos": len(from_json | from_dom),
        **capture.content_stats,
    }
    total = len(from_json | from_dom)
    detail = (
        f"HTTP {capture.status}, {len(capture.html)} bytes renderizados, "
        f"{len(capture.responses)} requisicoes ({capture.json_responses} JSON); "
        f"{total} fundos ({len(from_json)} pelo JSON, {len(from_dom)} pelo DOM)"
    )
    if not total:
        detail += " - nada reconhecido; veja os payloads gravados"
    return Probe(
        f"navegador/{platform.platform_id}",
        platform.shelf_url,
        bool(total),
        detail,
        evidence,
        findings,
    )


def probe_page(config: Config, platform: PlatformConfig) -> Probe:
    """Baixa a pagina da prateleira e mede o que ela realmente entrega."""
    if not platform.shelf_url:
        return Probe(f"pagina/{platform.platform_id}", "-", False, "sem shelf_url")

    session = build_session(config)
    try:
        response = get(session, platform.shelf_url, config)
    except SourceUnavailable as exc:
        return Probe(f"pagina/{platform.platform_id}", platform.shelf_url, False, str(exc)[:200])

    html = response.text
    evidence = _save(config, f"pagina-{platform.platform_id}.html", response.content)

    # O que distingue uma pagina com dados de uma casca de SPA: CNPJs no HTML,
    # tabelas, e blocos JSON embutidos.
    findings = {
        "status": response.status_code,
        "bytes": len(response.content),
        "cnpjs_no_html": len(set(_CNPJ_RE.findall(html))),
        "tabelas": html.lower().count("<table"),
        "linhas_tr": html.lower().count("<tr"),
        "scripts_json": len(re.findall(r'<script[^>]*type="application/json"', html)),
        "next_data": "__NEXT_DATA__" in html,
        "nuxt": "__NUXT__" in html,
        "cloudflare": "cf-ray" in {k.lower() for k in response.headers},
    }
    usable = findings["cnpjs_no_html"] > 0 or findings["linhas_tr"] > 3 or findings["scripts_json"] > 0
    detail = (
        f"{findings['bytes']} bytes, {findings['cnpjs_no_html']} CNPJs, "
        f"{findings['linhas_tr']} <tr>, {findings['scripts_json']} blocos JSON"
    )
    if not usable:
        detail += " - parece casca de SPA (dados vem por JavaScript)"
    return Probe(
        f"pagina/{platform.platform_id}", platform.shelf_url, usable, detail, evidence, findings
    )


def probe_tls(config: Config, host: str, port: int = 443) -> Probe:
    """Por que a verificacao de TLS falhou nesse host.

    Deliberadamente **nao** existe aqui a opcao de desligar a verificacao: um
    `verify=False` faria a coleta passar escondendo se o problema e o servidor
    (cadeia incompleta, certificado vencido) ou o endereco (host errado). O que
    esta sondagem faz e ler o que o servidor apresenta - sem confiar nele - para
    dizer qual dos casos e.

    Casos que distingue:
      - cadeia incompleta: o servidor manda so a folha, sem a intermediaria. E
        erro do servidor; nada no cliente corrige sem baixar a guarda.
      - host errado: o certificado e valido, mas para outro nome.
      - certificado vencido, ou auto-assinado (tipico de proxy interceptador).
    """
    import socket
    import ssl

    url = f"https://{host}"

    try:
        socket.getaddrinfo(host, port)
    except OSError as exc:
        return Probe("tls", url, False, f"DNS nao resolve: {exc}")

    def connect() -> socket.socket:
        """Socket ate o host, atravessando o proxy quando ha um configurado."""
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        if not proxy:
            return socket.create_connection((host, port), timeout=20)

        parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
        sock = socket.create_connection(
            (parts.hostname, parts.port or 8080), timeout=20
        )
        sock.sendall(
            f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()
        )
        response = sock.recv(4096)
        status_line = response.split(b"\r\n")[0].decode("latin-1", "replace")
        if " 200 " not in status_line:
            sock.close()
            raise OSError(f"proxy recusou o CONNECT: {status_line[:120]}")
        return sock

    # 1. Com verificacao completa: se passar, o problema nao e TLS.
    try:
        with connect() as raw:
            with ssl.create_default_context().wrap_socket(
                raw, server_hostname=host
            ) as tls:
                return Probe(
                    "tls",
                    url,
                    True,
                    f"handshake verificado OK ({tls.version()})",
                )
    except ssl.SSLError as exc:
        verify_error = " ".join(str(exc).split())
    except OSError as exc:
        return Probe("tls", url, False, f"conexao falhou: {exc}")

    # 2. Sem verificacao, so para ler o que o servidor apresentou. Nada e
    #    coletado por esta conexao - ela existe para explicar a falha acima.
    findings: dict = {"erro_de_verificacao": verify_error[:200]}
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with connect() as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                findings.update(_describe_chain(tls))
    except OSError as exc:
        return Probe(
            "tls", url, False, f"{verify_error[:120]} (e o handshake cru tambem falhou: {exc})"
        )

    cause = _tls_cause(verify_error, findings, host)
    findings["causa"] = cause
    return Probe("tls", url, False, f"{cause} | {verify_error[:140]}", None, findings)


def _describe_chain(tls) -> dict:
    """Le a cadeia que o servidor apresentou, sem confiar nela."""
    info: dict = {"tls_version": tls.version()}

    chain_length = None
    try:  # privado ate 3.13, mas e a unica via de ver a cadeia crua
        chain_length = len(tls._sslobj.get_unverified_chain() or [])
    except Exception:  # noqa: BLE001
        pass
    info["certificados_apresentados"] = chain_length

    der = tls.getpeercert(binary_form=True)
    if not der:
        return info
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID
    except ImportError:
        return info

    cert = x509.load_der_x509_certificate(der)
    info["subject"] = cert.subject.rfc4514_string()
    info["issuer"] = cert.issuer.rfc4514_string()
    info["valido_ate"] = cert.not_valid_after_utc.isoformat()
    info["auto_assinado"] = cert.subject == cert.issuer
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        info["nomes"] = san.value.get_values_for_type(x509.DNSName)[:20]
    except x509.ExtensionNotFound:
        info["nomes"] = []
    return info


def _tls_cause(verify_error: str, findings: dict, host: str) -> str:
    """Traduz o erro do OpenSSL mais a cadeia observada em uma causa."""
    lowered = verify_error.lower()

    if findings.get("auto_assinado") or "self signed" in lowered or "self-signed" in lowered:
        return "certificado auto-assinado (tipico de proxy que intercepta TLS)"
    if "expired" in lowered:
        return f"certificado vencido (valido ate {findings.get('valido_ate')})"
    if "hostname mismatch" in lowered or "doesn't match" in lowered:
        return f"certificado e de outro nome: {findings.get('nomes')}"
    if "unable to get local issuer" in lowered:
        if findings.get("certificados_apresentados") == 1:
            return (
                "cadeia incompleta no servidor: ele apresenta so o certificado "
                "folha, sem a intermediaria. E defeito do servidor - do lado do "
                "cliente so se contorna baixando a guarda, o que este monitor "
                "nao faz"
            )
        return (
            "emissor desconhecido: a CA que assinou nao esta no bundle de "
            "confianca deste ambiente"
        )

    names = findings.get("nomes") or []
    if names and not any(
        host == n or (n.startswith("*.") and host.endswith(n[1:])) for n in names
    ):
        return f"certificado e de outro nome: {names}"
    return "falha de verificacao nao classificada - veja os campos em findings"


def probe_inter_sitemap(config: Config) -> list[Probe]:
    """O coletor do Inter depende do sitemap - vale checar se ele existe."""
    from shelf_monitor.collectors.inter import _SHELF_PREFIX, _SITEMAP_URLS

    session = build_session(config)
    probes: list[Probe] = []
    for url in _SITEMAP_URLS:
        try:
            response = get(session, url, config)
        except SourceUnavailable as exc:
            probes.append(Probe("inter/sitemap", url, False, str(exc)[:160]))
            continue

        text = response.text
        total = text.count("<loc>")
        matching = text.count(_SHELF_PREFIX)
        children = len(re.findall(r"<loc>([^<]*sitemap[^<]*\.xml)</loc>", text))
        probes.append(
            Probe(
                "inter/sitemap",
                url,
                matching > 0 or children > 0,
                f"{total} <loc>, {matching} com o prefixo de fundos, "
                f"{children} sitemaps filhos",
                _save(config, f"inter-sitemap-{url.rsplit('/', 1)[-1]}", response.content),
                {"total": total, "matching": matching, "children": children},
            )
        )
    return probes


def _is_tls_failure(detail: str) -> bool:
    lowered = detail.lower()
    return "tls" in lowered or "ssl" in lowered or "certificate" in lowered


def run(
    config: Config, platform_ids: list[str], browser: bool = False
) -> list[Probe]:
    """Roda todas as sondagens e devolve os resultados.

    Com `browser`, acrescenta a sondagem por Chromium real. Ela e lenta (dezenas
    de segundos por site) mas e a unica que mede o que a lista de fato entrega -
    as tres casas que interessam devolvem 403 ou casca vazia a um cliente HTTP.
    """
    config.ensure_dirs()
    probes: list[Probe] = []

    for platform_id in platform_ids:
        platform = config.platform(platform_id)
        found = probe_open_finance(config, platform)
        probes.extend(found)
        # "falha de TLS" sem mais nada nao diz o que corrigir. Quando aparece,
        # a sondagem seguinte separa cadeia incompleta de host errado.
        for probe in found:
            if not probe.ok and _is_tls_failure(probe.detail) and "://" in probe.url:
                probes.append(probe_tls(config, urlsplit(probe.url).hostname or ""))

        probes.append(probe_page(config, platform))
        if browser and platform.shelf_url:
            probes.append(probe_browser(config, platform))
        if platform_id == "inter":
            probes.extend(probe_inter_sitemap(config))

    report = {
        "generated_at": datetime.now().isoformat(),
        "probes": [p.to_dict() for p in probes],
    }
    path = config.cache_dir / "debug" / "diagnostico.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return probes
