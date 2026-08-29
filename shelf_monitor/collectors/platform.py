"""Coletor de plataforma com estrategias em cascata.

Cada plataforma declara sua ordem de preferencia em PlatformConfig.strategies.
O Open Finance vem primeiro onde disponivel porque entrega dados estruturados
(CNPJ, taxas, minimos, liquidez) sem depender de layout de site; scraping entra
como fallback quando a instituicao publica pouco ou nada no endpoint publico.
"""
from __future__ import annotations

from shelf_monitor.collectors.base import BaseCollector
from shelf_monitor.collectors.open_finance import OpenFinanceFunds
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import CollectorStrategy, FundOffer

# Abaixo disso o resultado do Open Finance nao e considerado uma prateleira util
# e o coletor tenta a estrategia web da plataforma.
_MIN_OPEN_FINANCE_FUNDS = 5


class PlatformCollector(BaseCollector):
    """Percorre as estrategias da plataforma ate uma devolver fundos."""

    def _collect_impl(self) -> list[FundOffer]:
        errors: list[str] = []

        for strategy in self.platform.strategies:
            try:
                funds = self._run_strategy(strategy)
            except SourceUnavailable as exc:
                errors.append(f"{strategy.value}: {exc}")
                continue
            except NotImplementedError as exc:
                errors.append(f"{strategy.value}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                # Um payload malformado de terceiro nao pode cancelar as demais
                # estrategias: e exatamente para isso que a cascata existe.
                errors.append(f"{strategy.value}: {type(exc).__name__}: {exc}")
                continue

            if funds and len(funds) >= self._min_funds_for(strategy):
                self.strategy_used = strategy
                return funds
            if funds:
                errors.append(
                    f"{strategy.value}: apenas {len(funds)} fundos "
                    f"(abaixo do minimo util)"
                )
            else:
                errors.append(f"{strategy.value}: zero fundos")

        raise SourceUnavailable(" | ".join(errors) or "nenhuma estrategia configurada")

    def _min_funds_for(self, strategy: CollectorStrategy) -> int:
        if strategy == CollectorStrategy.OPEN_FINANCE:
            return _MIN_OPEN_FINANCE_FUNDS
        return 1

    def _run_strategy(self, strategy: CollectorStrategy) -> list[FundOffer]:
        if strategy == CollectorStrategy.OPEN_FINANCE:
            return self._collect_open_finance()
        if strategy in (
            CollectorStrategy.HTML_SCRAPE,
            CollectorStrategy.API_JSON,
        ):
            return self._collect_web()
        if strategy == CollectorStrategy.PLAYWRIGHT:
            return self._collect_playwright()
        raise NotImplementedError(f"estrategia {strategy.value} nao suportada aqui")

    def _collect_open_finance(self) -> list[FundOffer]:
        client = OpenFinanceFunds(self.config, self.platform, self.session)
        try:
            return client.fetch()
        finally:
            self.source_urls.extend(client.source_urls)

    def _collect_web(self) -> list[FundOffer]:
        """Scraping HTML/API interna. Subclasses implementam."""
        raise NotImplementedError(
            f"coletor web nao implementado para {self.platform_id}"
        )

    def _collect_playwright(self) -> list[FundOffer]:
        """Renderizacao headless para prateleiras que sao SPA. Subclasses implementam."""
        raise NotImplementedError(
            f"coletor Playwright nao implementado para {self.platform_id}"
        )
