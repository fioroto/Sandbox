"""Coletor da vitrine de previdencia da XP - baseline do modulo.

A lista em `institucional.xpi.com.br/previdencia-privada/lista-de-fundos/#/`
desenha 50 cards, mas o app da pagina chama uma API que devolve a vitrine
inteira:

    GET https://api.xpi.com.br/corprev-apim-portal-noauth/v1/investment-fund?status=1

Medido em 2026-09-22: 223 fundos, CNPJ em 223, join com a base de mercado em
219, e os 50 nomes da tela estao todos entre eles.

A API exige a chave de subscricao do Azure APIM - responde 401 sem ela -, e so o
app da pagina a tem. Por isso o caminho e o navegador, lendo a resposta que a
propria pagina recebe; nao ha chave nenhuma neste codigo. HTTP simples na
pagina leva 403, como na lista de fundos da XP.

Custo: a espera do `render_page` procura CNPJ ou linha de tabela, e esta pagina
nao tem nenhum dos dois (sao cards). Ele espera o teto e depois rola - por volta
de um minuto por coleta.

Se a resposta da API nao for interceptada, os nomes dos cards da tela entram no
lugar - 50 fundos, sem CNPJ - e o snapshot sai PARTIAL com o motivo, para nao
virar a baseline do diff de amanha. So o nome: o DOM da lista NAO e estavel. Na
mesma data, o navegador sem janela recebeu um layout (`.funds-table-row`, nome
no atributo `title`) e o navegador com janela outro (`.name-column`). Taxa e
minimo lidos de um DOM que muda arriscariam atribuir o numero de um fundo a
outro; o nome, no `title` da propria linha, nao.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import (
    classify_asset_class,
    normalize_cnpj,
    parse_int,
    parse_number,
)
from shelf_monitor.previdencia.eixo import KEY_SEGURADORA

API_FRAGMENTO = "corprev-apim-portal-noauth/v1/investment-fund"

# Os dois layouts da lista vistos em 2026-09-22 (ver o docstring). No de
# `.name-column`, a primeira e o cabecalho e traz o total ("223 fundos"), e cada
# linha repete esse total num rotulo responsivo escondido por CSS - que o
# BeautifulSoup le mesmo escondido, dai o descarte de `_SEL_ROTULO`.
_SEL_LINHA = ".funds-table-row"
_SEL_NOME = ".name-column"
_SEL_ROTULO = ".xp-flex-body__column__title"
_TOTAL_RE = re.compile(r"(\d+)\s+fundos?\b", re.IGNORECASE)


def _texto(valor: object) -> str | None:
    if isinstance(valor, str):
        return " ".join(valor.split()) or None
    return None


def _dict(valor: object) -> dict:
    return valor if isinstance(valor, dict) else {}


def offer_from_item(item: object) -> FundOffer | None:
    """Um item de `investmentFunds` como FundOffer."""
    if not isinstance(item, dict):
        return None
    nome = _texto(item.get("name")) or _texto(item.get("companyName"))
    if not nome:
        return None

    condicoes = _dict(item.get("investmentConditions"))
    classificacoes = _dict(item.get("classifications"))
    categoria_xp = _texto(_dict(classificacoes.get("xp")).get("description"))
    cvm_casa = _texto(classificacoes.get("cvm"))

    # Uma marca por fundo na medicao (brandType 3). `fundingBlocked` fecha o
    # fundo para aporte: 36 de 223 em 2026-09-22.
    marcas = [m for m in item.get("brands") or [] if isinstance(m, dict)]
    fechado = any(m.get("fundingBlocked") for m in marcas) if marcas else None

    planos = [p for p in item.get("plans") or [] if isinstance(p, dict)]
    seguradoras = sorted(
        {
            _texto(_dict(p.get("insurer")).get("name"))
            for p in planos
            if _texto(_dict(p.get("insurer")).get("name"))
        }
    )

    benchmark = item.get("benchmark")
    benchmark = (
        _texto(_dict(benchmark).get("name"))
        if isinstance(benchmark, dict)
        else _texto(benchmark)
    )

    extra = {
        chave: valor
        for chave, valor in (
            ("classificacao_xp", categoria_xp),
            ("classificacao_cvm_casa", cvm_casa),
            ("taxa_maxima", parse_number(condicoes.get("maxAdministrationRate"))),
            (KEY_SEGURADORA, ", ".join(seguradoras) or None),
            # Publico do PLANO, declarado pela casa: varia por fundo (74
            # qualificado, 5 profissional...), ao contrario do publico do
            # veiculo no registro, que e o mesmo para todo FIE. E este que a
            # regra de varejo deve ler - profissional sai da analise.
            ("publico", _texto(item.get("targetAudience"))),
            ("qualificado", item.get("qualifiedInvestor")),
            ("pl", parse_number(item.get("netEquity"))),
            ("pl_medio_12m", parse_number(item.get("averageNetEquity12M"))),
            ("benchmark", benchmark),
            ("risco", _texto(classificacoes.get("riskGeniusDescription"))),
            ("codigo_xp", _texto(item.get("code"))),
        )
        if valor is not None
    }

    return FundOffer(
        platform="xp",
        name=nome,
        cnpj=normalize_cnpj(_texto(item.get("cnpj"))),
        manager_name=_texto(item.get("fundManager")),
        admin_name=_texto(item.get("fundAdministrator")),
        asset_class=classify_asset_class(categoria_xp, cvm_casa, nome),
        raw_category=categoria_xp,
        min_initial=parse_number(condicoes.get("minimalInitialInvestment")),
        admin_fee_pct=parse_number(condicoes.get("administrationRate")),
        redemption_quotation_days=parse_int(item.get("redemptionQuotationDays")),
        redemption_payment_days=parse_int(item.get("redemptionSettlementDays")),
        closed_for_investment=fechado,
        extra=extra,
    )


def parse_payload(payload: Any) -> list[FundOffer]:
    """A resposta da API (`{"investmentFunds": [...]}`) como FundOffers."""
    fundos = _dict(payload).get("investmentFunds")
    if not isinstance(fundos, list):
        return []
    return [oferta for oferta in map(offer_from_item, fundos) if oferta is not None]


def total_declarado(html: str) -> int | None:
    """O total que o cabecalho da lista anuncia ("223 fundos"), quando anuncia.

    So o layout de `.name-column` tem esse cabecalho; no outro, None.
    """
    cabecalho = BeautifulSoup(html, "lxml").select_one(_SEL_NOME)
    if cabecalho is None:
        return None
    achado = _TOTAL_RE.search(" ".join(cabecalho.get_text(" ").split()))
    return int(achado.group(1)) if achado else None


def _nomes_por_linha(soup) -> list[str]:
    """Layout `.funds-table-row`: o `title` do primeiro elemento que tem um."""
    nomes = []
    for linha in soup.select(_SEL_LINHA):
        titulado = linha.find(attrs={"title": True})
        nome = " ".join((titulado.get("title") if titulado else "").split())
        if nome:
            nomes.append(nome)
    return nomes


def _nomes_por_coluna(soup) -> list[str]:
    """Layout `.name-column`: o texto da coluna sem o rotulo responsivo."""
    nomes = []
    for coluna in soup.select(_SEL_NOME):
        for rotulo in coluna.select(_SEL_ROTULO):
            rotulo.decompose()
        nome = " ".join(coluna.get_text(" ").split())
        if nome and not _TOTAL_RE.fullmatch(nome):
            nomes.append(nome)
    return nomes


def parse_cards(html: str) -> list[FundOffer]:
    """Os nomes dos cards da tela - o fallback quando a API nao foi interceptada."""
    soup = BeautifulSoup(html, "lxml")
    nomes = max((_nomes_por_linha(soup), _nomes_por_coluna(soup)), key=len)
    return [
        FundOffer(platform="xp", name=nome, asset_class=classify_asset_class(nome))
        for nome in dict.fromkeys(nomes)
    ]


def _esperar_cards(page) -> None:
    """Gancho do `render_page`: espera os cards, para a API ja ter respondido."""
    try:
        page.wait_for_function(
            f"document.querySelectorAll('{_SEL_LINHA}, {_SEL_NOME}').length > 1",
            timeout=20_000,
        )
    except Exception:  # noqa: BLE001 - sem cards, o coletor decide pelo que veio
        pass


class XpPrevidenciaCollector(PlatformCollector):
    """Navegador na lista, lendo a resposta da API que a pagina chama."""

    def _collect_playwright(self) -> list[FundOffer]:
        from shelf_monitor.collectors.browser import render_page

        if not self.platform.shelf_url:
            raise SourceUnavailable("shelf_url não configurada para a XP")

        captura = render_page(
            self.platform.shelf_url,
            user_agent=self.config.user_agent,
            executable_path=self.config.chromium_path,
            headed=self.config.browser_headed,
            channel=self.config.browser_channel,
            on_page=_esperar_cards,
        )
        self.source_urls.append(self.platform.shelf_url)
        declarados = total_declarado(captura.html)

        for origem, payload in captura.payloads:
            if API_FRAGMENTO not in origem:
                continue
            ofertas = parse_payload(payload)
            if ofertas:
                self.source_urls.append(origem)
                if declarados and len(ofertas) < declarados:
                    self.warnings.append(
                        f"API trouxe {len(ofertas)} fundos e a página declara {declarados}"
                    )
                return ofertas

        ofertas = parse_cards(captura.html)
        if not ofertas:
            raise SourceUnavailable(
                "nem a API da lista foi interceptada nem havia cards na página"
            )
        total = f" de {declarados} declarados" if declarados else ""
        self.warnings.append(
            f"API da lista não interceptada - só os {len(ofertas)} nomes da tela"
            f"{total}, sem CNPJ, taxa nem mínimo"
        )
        return ofertas
