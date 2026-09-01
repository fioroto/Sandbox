"""Base geral do mercado brasileiro de fundos, a partir dos dados abertos da CVM.

Diferente do radar competitivo (`collectors/cvm.py`), que filtra pela watchlist
das casas monitoradas, aqui nada e descartado: o objetivo e ser fonte de
consulta do mercado inteiro, com a hierarquia da RCVM 175 preservada.
"""
from shelf_monitor.market.flows import (
    FlowSummary,
    import_flows,
    load_summaries,
    run_flows_import,
    summarize_watchlist_managers,
)
from shelf_monitor.market.importer import import_market, run_import
from shelf_monitor.market.models import (
    ClassFees,
    FeeUnit,
    FlowsResult,
    MarketClass,
    MarketDatabase,
    MarketFund,
    MarketSubclass,
    MonthlyFlow,
    Origin,
)

__all__ = [
    "ClassFees",
    "FeeUnit",
    "FlowSummary",
    "FlowsResult",
    "MarketClass",
    "MarketDatabase",
    "MarketFund",
    "MarketSubclass",
    "MonthlyFlow",
    "Origin",
    "import_flows",
    "import_market",
    "load_summaries",
    "run_flows_import",
    "run_import",
    "summarize_watchlist_managers",
]
