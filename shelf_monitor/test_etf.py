"""Testes unitários para catalogação e agregação de fluxo de ETFs."""
from __future__ import annotations

from datetime import date

from shelf_monitor.market.etf import (
    categorize_etf,
    extract_ticker,
    is_etf_record,
)
from shelf_monitor.market.etf_flow import (
    EtfDailyRecord,
    EtfWeeklyFlow,
    aggregate_category_weekly_flows,
    aggregate_etf_weekly_flows,
    get_recent_months,
)


def test_extract_ticker():
    assert extract_ticker("ISHARES IBOVESPA FUNDO DE INDICE IE (BOVA11)") == "BOVA11"
    assert extract_ticker("HASHDEX NASDAQ CRYPTO INDEX ETF (HASH11)") == "HASH11"
    assert extract_ticker("IT NOW IVVB11 FI EM COTAS DE ETF") == "IVVB11"
    assert extract_ticker("FUNDO SEM TICKER NO NOME") is None


def test_is_etf_record():
    assert is_etf_record(fund_type="ETF") is True
    assert is_etf_record(cvm_class="FUNDO DE INDICE DE ACOES") is True
    assert is_etf_record(anbima_class="ETF - Renda Fixa") is True
    assert is_etf_record(name="ISHARES SP 500 FI EM COTAS DE ETF") is True
    assert is_etf_record(name="FUNDO MULTIMERCADO TRADICIONAL") is False


def test_categorize_etf():
    assert categorize_etf(cvm_class="Fundo de Índice", name="HASH11 BITCOIN ETF") == "Cripto"
    assert categorize_etf(cvm_class="Renda Fixa", name="FIXA11 TITULOS PUBLICOS ETF") == "Renda Fixa"
    assert categorize_etf(name="IVVB11 S&P 500 ETF EUA") == "Internacional"
    assert categorize_etf(name="BOVA11 IBOVESPA ACOES BRASIL ETF") == "Ações Brasil"
    assert categorize_etf(name="GOLD11 OURO COMMODITY ETF") == "Multimercado"


def test_get_recent_months():
    ref = date(2026, 9, 13)
    months = get_recent_months(3, ref_date=ref)
    assert months == ["202607", "202608", "202609"]


def test_aggregate_etf_weekly_flows():
    recs = [
        EtfDailyRecord(
            cnpj="11111111000111",
            dt_comptc=date(2026, 9, 7),  # Segunda
            vl_patrim_liq=1000.0,
            captc_dia=100.0,
            resg_dia=20.0,
            nr_cotst=10,
            vl_quota=10.0,
        ),
        EtfDailyRecord(
            cnpj="11111111000111",
            dt_comptc=date(2026, 9, 11),  # Sexta
            vl_patrim_liq=1150.0,
            captc_dia=50.0,
            resg_dia=10.0,
            nr_cotst=15,
            vl_quota=11.0,
        ),
    ]

    catalog = {}
    weekly = aggregate_etf_weekly_flows(catalog, recs)

    assert len(weekly) == 1
    w = weekly[0]
    assert w.cnpj == "11111111000111"
    assert w.captacao_bruta == 150.0
    assert w.resgates == 30.0
    assert w.captacao_liquida == 120.0
    assert w.pl_inicio == 1000.0
    assert w.pl_fim == 1150.0
    assert w.cotistas_inicio == 10
    assert w.cotistas_fim == 15
    assert w.variacao_cotistas == 5
    assert w.rentabilidade_semana == 10.0  # (11/10 - 1)*100


def test_aggregate_category_weekly_flows():
    weekly = [
        EtfWeeklyFlow(
            cnpj="111",
            etf_name="BOVA11",
            ticker="BOVA11",
            category="Ações Brasil",
            manager_name="BlackRock",
            iso_year=2026,
            iso_week=37,
            week_key="2026-W37",
            week_end_date=date(2026, 9, 11),
            captacao_bruta=200.0,
            resgates=50.0,
            captacao_liquida=150.0,
            pl_inicio=1000.0,
            pl_fim=1150.0,
            cotistas_inicio=10,
            cotistas_fim=15,
            variacao_cotistas=5,
        ),
        EtfWeeklyFlow(
            cnpj="222",
            etf_name="SMAL11",
            ticker="SMAL11",
            category="Ações Brasil",
            manager_name="BlackRock",
            iso_year=2026,
            iso_week=37,
            week_key="2026-W37",
            week_end_date=date(2026, 9, 11),
            captacao_bruta=100.0,
            resgates=20.0,
            captacao_liquida=80.0,
            pl_inicio=500.0,
            pl_fim=580.0,
            cotistas_inicio=5,
            cotistas_fim=8,
            variacao_cotistas=3,
        ),
    ]

    cats = aggregate_category_weekly_flows(weekly)
    assert len(cats) == 1
    c = cats[0]
    assert c.category == "Ações Brasil"
    assert c.qtd_etfs == 2
    assert c.captacao_bruta == 300.0
    assert c.resgates == 70.0
    assert c.captacao_liquida == 230.0
