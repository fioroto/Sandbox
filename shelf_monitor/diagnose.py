"""Diagnostico das fontes de prateleira.

Os coletores foram escritos a partir de documentacao, nao de observacao dos
sites. Quando um deles nao traz nada, a pergunta e sempre a mesma: o site
respondeu? respondeu o que se esperava? Este modulo responde isso e grava a
resposta bruta em disco, para que o problema possa ser corrigido com a
evidencia na mao em vez de por tentativa.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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

    try:
        response = get(
            session, funds_url, config, params={"page": 1, "page-size": 10},
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
    probes.append(
        Probe(
            f"open-finance/{platform.platform_id}",
            funds_url,
            bool(items),
            f"{len(items)} fundos extraidos do envelope; meta={payload.get('meta') if isinstance(payload, dict) else '-'}",
            _save(config, f"of-funds-{platform.platform_id}.json", response.content),
            {"first_item_keys": sorted(items[0]) if items else []},
        )
    )
    return probes


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


def run(config: Config, platform_ids: list[str]) -> list[Probe]:
    """Roda todas as sondagens e devolve os resultados."""
    config.ensure_dirs()
    probes: list[Probe] = []

    for platform_id in platform_ids:
        platform = config.platform(platform_id)
        probes.extend(probe_open_finance(config, platform))
        probes.append(probe_page(config, platform))
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
