"""Coletor da vitrine de previdencia do BTG Pactual.

A pagina /previdencia-privada/produtos e uma SPA alimentada por um endpoint
publico proprio - nao o `funds/list/v2` da vitrine de fundos:

    GET /services/api/pension-management/funds/public

Medido em 2026-09-22: array JSON plano, sem paginacao, 226 fundos, CNPJ em 226
de 226, e join com a base de mercado em 226. HTTP simples respondeu 200; se o
WAF passar a barrar, a mesma URL e buscada por dentro do navegador.

Onde cada campo mora no payload:

- nome comercial em `product` (e o que a pagina exibe); o de registro em
  `fundName`, guardado em `nome_registro` quando difere;
- taxa, gestor, administrador, performance e prazos D+N em `detail`, como na
  API de fundos. So `administrationFeeType == PERCENTAGE` vira taxa percentual
  (165 de 226); MIXED (57) e FINANCIAL (4) ficam sem taxa da casa - o custo le a
  taxa oficial do extrato da CVM pelo CNPJ, entao nada se perde;
- seguradora em `pension.partner` (KIPREV 165, ICATU 61 em 2026-09-22). Guardado
  como a API escreve: a pagina chama os dois de "BTG Vida e Previdencia" e
  "Icatu", mas a correspondencia do primeiro nao esta no payload;
- planos em `pension.susepCodes[].planType` (PG e VG em todos os 226).

Duas marcas NAO viram restricao: `restrictedOrExclusive`/`isExclusiveFund`
(146 de 226) e `investmentType` "Investidores profissionais" (212 de 226).
Descrevem o FIE, cujo cotista e a seguradora - nao o acesso do cliente ao
plano. O coletor de fundos as grava em `restrito_casa` e `publico`, que a regra
de varejo le; aqui vao para `restrito_veiculo_casa` e `publico_veiculo_casa`.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from shelf_monitor.collectors.btg import parse_dplus
from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable, get_json
from shelf_monitor.models import FundOffer, PerformanceFee
from shelf_monitor.normalize import classify_asset_class, normalize_cnpj, parse_number
from shelf_monitor.previdencia.eixo import (
    KEY_NOME_REGISTRO,
    KEY_SEGURADORA,
    sem_prefixo_previdencia,
)

API_PATH = "/services/api/pension-management/funds/public"
_ORIGEM_PADRAO = "https://investimentos.btgpactual.com"

_PLANOS = {"PG": "PGBL", "VG": "VGBL"}


def _texto(valor: object) -> str | None:
    if isinstance(valor, str):
        return valor.strip() or None
    return None


def _dict(valor: object) -> dict:
    return valor if isinstance(valor, dict) else {}


def offer_from_item(item: object) -> FundOffer | None:
    """Um item do `pension-management/funds/public` como FundOffer."""
    if not isinstance(item, dict):
        return None
    nome = (
        _texto(item.get("product"))
        or _texto(item.get("fundName"))
        or _texto(item.get("productOriginal"))
    )
    if not nome:
        return None

    detalhe = _dict(item.get("detail"))
    previdencia = _dict(item.get("pension"))
    categoria = _texto(item.get("categoryBTG"))
    subcategoria = _texto(item.get("subcategoryBTG"))
    anbima_casa = _texto(detalhe.get("anbimaRating"))

    unidade_taxa = _texto(detalhe.get("administrationFeeType"))
    taxa = (
        parse_number(detalhe.get("administrationFee"))
        if unidade_taxa == "PERCENTAGE"
        else None
    )

    performance = parse_number(detalhe.get("performanceFee"))
    taxa_performance = (
        PerformanceFee(amount_pct=performance, benchmark=_texto(item.get("benchmarkName")))
        if performance
        else None
    )

    # Fechado quando a casa nao aceita aplicacao pelo portal ou bloqueou a
    # adesao ao plano. Sem nenhum dos dois sinais, desconhecido.
    aberto = item.get("applyOpenPortal")
    adesao_bloqueada = previdencia.get("iAdhesionBlocked")
    if adesao_bloqueada is True or aberto is False:
        fechado: bool | None = True
    elif aberto is True:
        fechado = False
    else:
        fechado = None

    planos = sorted(
        {
            _PLANOS.get(codigo.get("planType"), codigo.get("planType"))
            for codigo in previdencia.get("susepCodes") or []
            if isinstance(codigo, dict) and codigo.get("planType")
        }
    )

    nome_registro = _texto(item.get("fundName"))
    extra = {
        chave: valor
        for chave, valor in (
            ("classificacao_anbima", anbima_casa),
            ("subcategoria_btg", subcategoria),
            ("risco", _texto(item.get("riskName"))),
            ("pl", parse_number(item.get("netEquity"))),
            (KEY_SEGURADORA, _texto(previdencia.get("partner"))),
            ("planos", planos or None),
            ("adesao_bloqueada", adesao_bloqueada),
            ("aporte_bloqueado", previdencia.get("isContributionBlocked")),
            ("taxa_unidade_casa", unidade_taxa if unidade_taxa != "PERCENTAGE" else None),
            (
                "restrito_veiculo_casa",
                bool(
                    item.get("restrictedOrExclusive")
                    or item.get("isExclusiveFund")
                    or item.get("isExclusiveFundPortal")
                )
                or None,
            ),
            ("publico_veiculo_casa", _texto(item.get("investmentType"))),
            (KEY_NOME_REGISTRO, nome_registro if nome_registro != nome else None),
        )
        if valor is not None
    }

    return FundOffer(
        platform="btg",
        name=nome,
        cnpj=normalize_cnpj(
            _texto(item.get("classCNPJ"))
            or _texto(item.get("cnpj"))
            or _texto(item.get("fundCNPJ"))
        ),
        isin=_texto(item.get("isinCode")),
        manager_name=_texto(detalhe.get("manager")) or _texto(item.get("issuerName")),
        admin_name=_texto(detalhe.get("administrator")),
        asset_class=classify_asset_class(
            sem_prefixo_previdencia(anbima_casa), categoria, subcategoria, nome
        ),
        raw_category=categoria,
        min_initial=parse_number(
            item.get("minimumInitialInvestment")
            if item.get("minimumInitialInvestment") is not None
            else detalhe.get("minimumInitialInvestment")
        ),
        admin_fee_pct=taxa,
        performance_fee=taxa_performance,
        redemption_quotation_days=parse_dplus(detalhe.get("rescueQuota")),
        redemption_payment_days=parse_dplus(detalhe.get("rescuefinancialSettlement")),
        taxation=_texto(item.get("nmTaxationPursued")),
        closed_for_investment=fechado,
        extra=extra,
    )


def parse_payload(payload: Any) -> list[FundOffer]:
    """O array da API como FundOffers; qualquer outra forma vira lista vazia."""
    if not isinstance(payload, list):
        return []
    return [oferta for oferta in map(offer_from_item, payload) if oferta is not None]


class BtgPrevidenciaCollector(PlatformCollector):
    """API publica por HTTP; o navegador so quando o HTTP simples e barrado."""

    def _url_api(self) -> str:
        partes = urlsplit(self.platform.shelf_url or _ORIGEM_PADRAO)
        return f"{partes.scheme}://{partes.netloc}{API_PATH}"

    def _collect_web(self) -> list[FundOffer]:
        url = self._url_api()
        try:
            payload = get_json(self.session, url, self.config)
        except SourceUnavailable as erro:
            payload = self._pelo_navegador(url, erro)
        self.source_urls.append(url)

        ofertas = parse_payload(payload)
        if not ofertas:
            raise SourceUnavailable(f"{url}: resposta sem fundos reconhecíveis")
        return ofertas

    def _pelo_navegador(self, url: str, erro_http: SourceUnavailable) -> Any:
        """A mesma API, lida da resposta que a propria pagina recebe."""
        from shelf_monitor.collectors.browser import render_page

        captura = render_page(
            self.platform.shelf_url or _ORIGEM_PADRAO,
            user_agent=self.config.user_agent,
            executable_path=self.config.chromium_path,
            headed=self.config.browser_headed,
            channel=self.config.browser_channel,
        )
        for origem, payload in captura.payloads:
            if API_PATH in origem:
                # Observacao, nao aviso: os dados sao os mesmos, so o caminho
                # mudou - a coleta continua OK.
                self.partial_reason = f"API lida pelo navegador ({erro_http})"
                return payload
        raise SourceUnavailable(f"{erro_http}; e a página não chamou {API_PATH}")
