"""Taxas e condicoes comerciais, do extrato de informacoes da CVM.

Duas particularidades do arquivo governam este modulo:

1. Ele guarda ~5 anos de entregas, uma linha por classe por data-base. O estado
   atual e a maior DT_COMPTC de cada classe - as anteriores sao historico.
2. TAXA_ADM e polimorfica sem discriminador: para investidor qualificado o
   regulamento admite texto livre, entao a mesma coluna carrega ora percentual
   ao ano, ora um valor fixo em reais. Um ranking de custo que ignore isso
   coloca um fundo de "R$ 30.000 por ano" no topo como se cobrasse 30.000%.
"""
from __future__ import annotations

import re

from pathlib import Path

from shelf_monitor.collectors.cvm import _get
from shelf_monitor.market.models import ClassFees, FeeUnit
from shelf_monitor.market.parse import iter_rows, parse_date
from shelf_monitor.normalize import (
    normalize_cnpj,
    normalize_text,
    parse_money,
    parse_number,
)

# Acima deste valor, um numero em TAXA_ADM quase certamente e reais e nao
# percentual: taxa de administracao de fundo brasileiro nao passa disso.
# A heuristica e usada em producao por quem consome o mesmo arquivo.
_PERCENT_CEILING = 20.0

_CURRENCY_HINTS = ("R$", "REAIS", "REAL")
_TEXT_HINTS = ("REGULAMENTO", "CONFORME", "NAO HA", "ISENTO", "VARIAVEL")


def resolve_admin_fee(raw: str | None) -> tuple[float | None, FeeUnit, str | None]:
    """Interpreta TAXA_ADM. Devolve (valor, unidade, texto original).

    O texto original e sempre preservado: quando a heuristica erra, o dado
    bruto continua la para conferencia.
    """
    if raw is None:
        return None, FeeUnit.UNKNOWN, None

    text = str(raw).strip()
    if not text:
        return None, FeeUnit.UNKNOWN, None

    upper = normalize_text(text)

    # Texto explicando a taxa em vez de um numero.
    if any(hint in upper for hint in _TEXT_HINTS) and not re.search(r"\d", text):
        return None, FeeUnit.TEXT, text

    has_currency = any(hint in upper for hint in _CURRENCY_HINTS) or "R$" in text
    value = parse_money(text) if has_currency else parse_number(text)

    if value is None:
        return None, FeeUnit.TEXT, text
    if has_currency:
        return value, FeeUnit.CURRENCY, text
    if value > _PERCENT_CEILING:
        # Sem simbolo, mas grande demais para ser percentual ao ano.
        return value, FeeUnit.CURRENCY, text
    return value, FeeUnit.PERCENT, text


def parse_fees(source: Path | bytes) -> dict[str, ClassFees]:
    """extrato_fi.csv -> taxas por CNPJ de classe, so a entrega mais recente.

    Le em streaming: o arquivo tem ~100 MB e cinco anos de entregas, mas o
    resultado e uma entrada por classe, entao a memoria fica no tamanho do
    resultado e nao no do arquivo.
    """
    latest: dict[str, ClassFees] = {}

    for row in iter_rows(source):
        cnpj = normalize_cnpj(
            _get(row, "CNPJ_FUNDO_CLASSE", "CNPJ_Fundo_Classe", "CNPJ_FUNDO", "CNPJ_Classe")
        )
        if not cnpj:
            continue

        reference = parse_date(_get(row, "DT_COMPTC", "Data_Competencia"))
        existing = latest.get(cnpj)
        if existing is not None and not _is_newer(reference, existing.reference_date):
            continue

        admin_fee, unit, admin_raw = resolve_admin_fee(_get(row, "TAXA_ADM"))

        latest[cnpj] = ClassFees(
            cnpj=cnpj,
            reference_date=reference,
            admin_fee=admin_fee,
            admin_fee_unit=unit,
            admin_fee_raw=admin_raw,
            performance_fee=parse_number(_get(row, "TAXA_PERFM")),
            performance_fee_benchmark=_get(row, "PARAM_TAXA_PERFM"),
            has_performance_fee=_get(row, "EXISTE_TAXA_PERFM"),
            entry_fee=parse_number(_get(row, "VL_TAXA_ENTRADA", "TAXA_ENTRADA")),
            exit_fee=parse_number(_get(row, "VL_TAXA_SAIDA", "TAXA_SAIDA")),
            min_initial=parse_money(
                _get(row, "INVEST_INICIAL_MIN", "VL_APLIC_MIN", "APLIC_MIN")
            ),
            min_balance=parse_money(_get(row, "VL_SALDO_MIN", "SALDO_MIN")),
            redemption_quotation_days=_parse_days(_get(row, "QT_DIA_CONVER_COTA")),
            redemption_payment_days=_parse_days(_get(row, "QT_DIA_PAGTO_RESGATE")),
            target_audience=_get(row, "PUBLICO_ALVO"),
            investment_policy=_get(row, "POLIT_INVEST"),
            long_term_taxation=_get(row, "TRIB_LPRAZO"),
        )

    return latest


def _is_newer(candidate, current) -> bool:
    """Uma data ausente nunca substitui uma conhecida."""
    if candidate is None:
        return False
    if current is None:
        return True
    return candidate > current


def _parse_days(raw: str | None) -> int | None:
    value = parse_number(raw)
    return int(value) if value is not None else None
