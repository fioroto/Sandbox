"""Download do informe diario da CVM (INF_DIARIO), um zip por competencia.

Usado pela captacao, pela rentabilidade e pelo monitor da industria. Morava no
fluxo de ETF (`market/etf_flow.py`) ate o monitor de ETF virar o repositorio
etf-monitor, em 07/10/2026.
"""
from __future__ import annotations

from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.datasets import Dataset
from shelf_monitor.market.fetch import fetch


def fetch_inf_diario_month(
    config: Config, yyyymm: str, force: bool = False, revalidar: bool = False
) -> Path | None:
    """Baixa o zip mensal do INF_DIARIO da CVM.

    Sem `force` nem `revalidar`, o zip que ja esta em disco serve como esta - o
    caso das competencias antigas, que ninguem mais mexe. `revalidar` pergunta
    a CVM se o arquivo mudou (GET condicional, com o ETag e o Last-Modified
    guardados por `fetch`): a CVM regrava o mes fechado por semanas, e o zip
    baixado como "mes corrente" para no dia em que foi baixado - o de 09/2026
    em disco parou em 28/09, sem o 25/09 completo nem os dias 28 a 30. Uma
    semana que cruza a virada do mes saia desse zip velho. `force` baixa de
    novo sem perguntar (o mes corrente, que muda todo dia).
    """
    filename = f"inf_diario_fi_{yyyymm}.zip"
    dest = config.market_raw_dir / filename
    if dest.exists() and not (force or revalidar):
        return dest

    url = f"https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{yyyymm}.zip"

    ds = Dataset(
        key=f"inf_diario_{yyyymm}",
        url=url,
        filename=filename,
        description=f"Informe diário CVM {yyyymm}",
        members={"inf_diario": f"inf_diario_fi_{yyyymm}.csv"},
        approx_mb=11,
    )

    try:
        res = fetch(config, ds, force=force)
        return res.path
    except Exception:  # noqa: BLE001
        return dest if dest.exists() else None
