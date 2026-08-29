"""Coletor de plataforma com uniao de fontes.

Cada casa e consultada por todas as estrategias que ela declara, e o resultado
final e a uniao - nao a primeira que respondeu. Uma fonte que falha nao impede
as outras; se todas falharem, o snapshot sai UNAVAILABLE com o motivo de cada
uma.

O modo antigo (parar na primeira fonte util) continua disponivel via
`stop_at_first_source`, para quando o tempo de coleta importa mais que a
cobertura - o Playwright sozinho leva dezenas de segundos por site.
"""
from __future__ import annotations

from shelf_monitor.collectors.base import BaseCollector
from shelf_monitor.collectors.open_finance import OpenFinanceFunds
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.merge import merge_offers, source_rank, source_summary
from shelf_monitor.models import CollectorStrategy, FundOffer

# Abaixo disso o Open Finance nao e considerado uma prateleira util por si so.
# Os fundos continuam entrando na uniao - o limite so evita que uma resposta
# quase vazia seja tratada como a prateleira inteira no modo de fonte unica.
_MIN_OPEN_FINANCE_FUNDS = 5


class PlatformCollector(BaseCollector):
    """Percorre as estrategias da plataforma e une o que cada uma trouxe."""

    def __init__(self, config, platform, stop_at_first_source: bool = False):
        super().__init__(config, platform)
        self.stop_at_first_source = stop_at_first_source
        self.source_counts: dict[str, int] = {}
        self.strategies_used: list[str] = []

    def _collect_impl(self) -> list[FundOffer]:
        groups: dict[str, list[FundOffer]] = {}
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
                # fontes: e exatamente para isso que a cascata existe.
                errors.append(f"{strategy.value}: {type(exc).__name__}: {exc}")
                continue

            if not funds:
                errors.append(f"{strategy.value}: zero fundos")
                continue

            groups[strategy.value] = funds
            self.source_counts[strategy.value] = len(funds)

            if self.stop_at_first_source and len(funds) >= self._min_funds_for(strategy):
                break

        if not groups:
            raise SourceUnavailable(
                " | ".join(errors) or "nenhuma estrategia configurada"
            )

        merged = merge_offers(groups)
        self.strategies_used = sorted(groups, key=source_rank)
        # A estrategia "principal" e a de maior confianca entre as que deram
        # certo - e o que aparece no resumo de uma linha do CLI.
        self.strategy_used = CollectorStrategy(self.strategies_used[0])
        self.summary = source_summary(groups, merged)

        # Fontes que falharam viram observacao, nao erro: a coleta deu certo,
        # mas saber que uma das vias caiu explica um numero abaixo do esperado.
        if errors:
            self.partial_reason = "fontes indisponiveis -> " + " | ".join(errors)
        return merged

    def _min_funds_for(self, strategy: CollectorStrategy) -> int:
        if strategy == CollectorStrategy.OPEN_FINANCE:
            return _MIN_OPEN_FINANCE_FUNDS
        return 1

    def _run_strategy(self, strategy: CollectorStrategy) -> list[FundOffer]:
        if strategy == CollectorStrategy.OPEN_FINANCE:
            return self._collect_open_finance()
        if strategy in (CollectorStrategy.HTML_SCRAPE, CollectorStrategy.API_JSON):
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
