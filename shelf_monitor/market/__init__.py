"""Base geral do mercado brasileiro de fundos, a partir dos dados abertos da CVM.

Diferente do radar competitivo (`collectors/cvm.py`), que filtra pela watchlist
das casas monitoradas, aqui nada e descartado: o objetivo e ser fonte de
consulta do mercado inteiro, com a hierarquia da RCVM 175 preservada.
"""
from shelf_monitor.market.importer import import_market, run_import
from shelf_monitor.market.models import (
    ClassFees,
    FeeUnit,
    MarketClass,
    MarketDatabase,
    MarketFund,
    MarketSubclass,
    Origin,
)

__all__ = [
    "ClassFees",
    "FeeUnit",
    "MarketClass",
    "MarketDatabase",
    "MarketFund",
    "MarketSubclass",
    "Origin",
    "import_market",
    "run_import",
]
