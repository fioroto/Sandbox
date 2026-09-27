"""Testes do PL mensal de ETF lido do balancete de FIE.

O zip e montado aqui, na forma exata do arquivo da CVM (uma linha por conta
contabil, separador ';', latin-1), para que o parser seja exercitado sem rede.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.fie_balancete import (
    ASSETS_ACCOUNT,
    EQUITY_GROUP_ACCOUNT,
    EXPENSES_ACCOUNT,
    LIABILITIES_ACCOUNT,
    RETAINED_RESULT_ACCOUNT,
    REVENUES_ACCOUNT,
    SHARE_CAPITAL_ACCOUNT,
    EtfMonthlyBalance,
    monthly_pl_series,
    pl_coverage,
    read_etf_balances,
)

_HEADER = (
    "TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;DT_COMPTC;PLANO_CONTA_BALCTE;"
    "CD_CONTA_BALCTE;VL_SALDO_BALCTE"
)

# Saldos reais do IT NOW IBOVESPA (21.407.758/0001-19) no balancete da CVM.
# Em 31/08/2026 o Fundos.NET publicou o mesmo PL de Ativo - Passivo:
# R$ 7.758.102.046,04. O grupo 6 dava R$ 8.086.776.795,12.
_AGOSTO = {
    ASSETS_ACCOUNT: "8056822693.69",
    LIABILITIES_ACCOUNT: "298720647.65",
    EQUITY_GROUP_ACCOUNT: "8086776795.12",
    SHARE_CAPITAL_ACCOUNT: "5095556452.74",
    RETAINED_RESULT_ACCOUNT: "2991220342.38",
    REVENUES_ACCOUNT: "5046342012.41",
    EXPENSES_ACCOUNT: "-5375016761.49",
}
_JULHO = {
    ASSETS_ACCOUNT: "6901379555.13",
    LIABILITIES_ACCOUNT: "7450472.98",
    EQUITY_GROUP_ACCOUNT: "7254168941.57",
    SHARE_CAPITAL_ACCOUNT: "4262948599.19",
    RETAINED_RESULT_ACCOUNT: "2991220342.38",
    REVENUES_ACCOUNT: "4156610512.81",
    EXPENSES_ACCOUNT: "-4516850372.23",
}
# BUENA VISTA NASDAQ-100 NEOS HIGH INCOME (Vortx) em 30/06/2026: o grupo 6 trazia
# outras subcontas e nao era capital + resultado; o PL fechava assim mesmo.
_JUNHO_VORTX = {
    ASSETS_ACCOUNT: "180419701.16",
    LIABILITIES_ACCOUNT: "2130914.11",
    EQUITY_GROUP_ACCOUNT: "170288463.17",
    SHARE_CAPITAL_ACCOUNT: "154370635.45",
    RETAINED_RESULT_ACCOUNT: "-2413109.70",
    REVENUES_ACCOUNT: "8502348.23",
    EXPENSES_ACCOUNT: "-502024.35",
}
_CAMPOS = {
    ASSETS_ACCOUNT: "assets",
    LIABILITIES_ACCOUNT: "liabilities",
    EQUITY_GROUP_ACCOUNT: "equity_group",
    SHARE_CAPITAL_ACCOUNT: "share_capital",
    RETAINED_RESULT_ACCOUNT: "retained_result",
    REVENUES_ACCOUNT: "revenues",
    EXPENSES_ACCOUNT: "expenses",
}


def _row(cnpj: str, account: str, value: str, comptc: str = "2026-08-31") -> str:
    return f"CLASSE FIIM;{cnpj};{comptc};COFI;{account};{value}"


def _write_balancete(directory: Path, competencia: str, rows: list[str]) -> Path:
    path = directory / f"balancete_fie_{competencia}.zip"
    body = "\n".join([_HEADER, *rows]) + "\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"balancete_fie_{competencia}.csv", body.encode("latin-1")
        )
    return path


def _saldos(cnpj: str, competencia: str, contas: dict[str, str]) -> EtfMonthlyBalance:
    return EtfMonthlyBalance(
        cnpj=cnpj,
        competencia=competencia,
        dt_comptc=None,
        **{_CAMPOS[conta]: float(valor) for conta, valor in contas.items()},
    )


def _coerente(cnpj: str, competencia: str, pl: float) -> EtfMonthlyBalance:
    """Saldos que fecham as duas identidades, para nao marcar suspeita sem motivo."""
    return EtfMonthlyBalance(
        cnpj=cnpj,
        competencia=competencia,
        dt_comptc=None,
        assets=pl,
        equity_group=pl,
        share_capital=pl,
    )


class TestReadEtfBalances(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_le_as_contas_de_um_etf(self):
        cnpj = "21.407.758/0001-19"
        path = _write_balancete(
            self.dir,
            "202608",
            [
                *(_row(cnpj, conta, valor) for conta, valor in _AGOSTO.items()),
                # Conta fora do mapa: entra no arquivo, nao no registro.
                _row(cnpj, "13000004", "8056800000.00"),
            ],
        )
        balances = read_etf_balances(path, {"21407758000119"}, "202608")

        self.assertEqual(len(balances), 1)
        balance = balances[0]
        self.assertEqual(balance.cnpj, "21407758000119")
        self.assertEqual(balance.competencia, "202608")
        self.assertEqual(balance.dt_comptc, date(2026, 8, 31))
        self.assertAlmostEqual(balance.assets, 8056822693.69, places=2)
        self.assertAlmostEqual(balance.liabilities, 298720647.65, places=2)
        self.assertAlmostEqual(balance.equity_group, 8086776795.12, places=2)
        self.assertAlmostEqual(balance.share_capital, 5095556452.74, places=2)
        self.assertAlmostEqual(balance.retained_result, 2991220342.38, places=2)
        self.assertAlmostEqual(balance.revenues, 5046342012.41, places=2)
        self.assertAlmostEqual(balance.expenses, -5375016761.49, places=2)

    def test_pl_e_ativo_menos_passivo_e_nao_o_grupo_6(self):
        """A regressao medida em 2026-09-24: o grupo 6 deixa de fora o resultado
        do exercicio em curso e errou o PL de 180 de 181 ETFs em 31/08/2026."""
        balance = _saldos("21407758000119", "202608", _AGOSTO)
        self.assertAlmostEqual(balance.pl, 7758102046.04, places=2)
        self.assertGreater(abs(balance.pl - balance.equity_group), 300_000_000)

    def test_identidade_do_pl_fecha_com_os_numeros_reais(self):
        for contas in (_JULHO, _AGOSTO, _JUNHO_VORTX):
            with self.subTest(ativo=contas[ASSETS_ACCOUNT]):
                self.assertTrue(_saldos("1", "202608", contas).identity_holds)

    def test_grupo_6_com_outras_subcontas_nao_e_suspeito(self):
        """Em 06/2026 a abertura do grupo 6 nao fechava em cinco ETFs da Vortx
        (sobravam 18,3 mi neste), e o PL estava certo: a guarda que exigia
        grupo 6 = capital + resultado marcava suspeita falsa."""
        balance = _saldos("1", "202606", _JUNHO_VORTX)
        abertura = balance.share_capital + balance.retained_result
        self.assertGreater(abs(balance.equity_group - abertura), 18_000_000)
        self.assertAlmostEqual(balance.pl, 178288787.05, places=2)
        self.assertTrue(balance.identity_holds)

    def test_identidade_quebra_quando_ativo_e_passivo_nao_batem_com_o_resultado(self):
        """A guarda contra renumeracao do plano: Ativo lido de outra conta, e o
        monitor publicaria como PL um saldo que e outra coisa."""
        contas = dict(_AGOSTO, **{ASSETS_ACCOUNT: "8086776795.12"})
        self.assertFalse(_saldos("21407758000119", "202608", contas).identity_holds)

    def test_ignora_cnpj_fora_do_catalogo(self):
        path = _write_balancete(
            self.dir,
            "202608",
            [
                _row("11.111.111/0001-11", ASSETS_ACCOUNT, "1000.00"),
                _row("22.222.222/0001-22", ASSETS_ACCOUNT, "2000.00"),
            ],
        )
        balances = read_etf_balances(path, {"11111111000111"}, "202608")
        self.assertEqual([b.cnpj for b in balances], ["11111111000111"])

    def test_fundo_sem_conta_de_ativo_fica_de_fora(self):
        """Sem o Ativo nao ha PL a publicar - nem o Passivo sozinho vira PL
        negativo, nem o grupo 6 volta a fazer as vezes de PL."""
        path = _write_balancete(
            self.dir,
            "202608",
            [
                _row("11.111.111/0001-11", LIABILITIES_ACCOUNT, "500.00"),
                _row("11.111.111/0001-11", EQUITY_GROUP_ACCOUNT, "500.00"),
                _row("11.111.111/0001-11", SHARE_CAPITAL_ACCOUNT, "500.00"),
            ],
        )
        self.assertEqual(read_etf_balances(path, {"11111111000111"}, "202608"), [])

    def test_zip_ausente_ou_invalido_devolve_vazio(self):
        self.assertEqual(read_etf_balances(None, {"x"}, "202608"), [])
        self.assertEqual(
            read_etf_balances(self.dir / "nao-existe.zip", {"x"}, "202608"), []
        )
        quebrado = self.dir / "quebrado.zip"
        quebrado.write_text("não sou um zip", encoding="utf-8")
        self.assertEqual(read_etf_balances(quebrado, {"x"}, "202608"), [])


def _metadata(cnpj: str, name: str, category: str = "Ações Brasil") -> EtfMetadata:
    return EtfMetadata(cnpj=cnpj, name=name, kind="etf", category=category)


class TestMonthlyPlSeries(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "21407758000119": _metadata("21407758000119", "IT NOW IBOVESPA"),
            "10406511000161": _metadata("10406511000161", "ISHARES IBOVESPA"),
        }

    def test_calcula_a_variacao_contra_o_mes_anterior(self):
        series = monthly_pl_series(
            self.catalog,
            [
                _saldos("21407758000119", "202608", _AGOSTO),
                _saldos("21407758000119", "202607", _JULHO),
            ],
        )
        por_competencia = {s.competencia: s for s in series}

        julho = por_competencia["202607"]
        self.assertAlmostEqual(julho.pl, 6_893_929_082.15, places=2)
        self.assertIsNone(julho.pl_anterior)
        self.assertIsNone(julho.variacao_pl)

        agosto = por_competencia["202608"]
        self.assertAlmostEqual(agosto.pl_anterior, 6_893_929_082.15, places=2)
        self.assertAlmostEqual(agosto.variacao_pl, 864_172_963.89, places=2)
        self.assertAlmostEqual(agosto.variacao_pct, 12.5353, places=3)
        self.assertFalse(agosto.identidade_suspeita)

    def test_cnpj_fora_do_catalogo_nao_entra_na_serie(self):
        series = monthly_pl_series(
            self.catalog, [_coerente("99999999000199", "202608", 1_000.0)]
        )
        self.assertEqual(series, [])

    def test_identidade_fora_de_linha_marca_o_registro(self):
        balance = EtfMonthlyBalance(
            cnpj="21407758000119",
            competencia="202608",
            dt_comptc=None,
            assets=1_000.0,
            equity_group=10.0,
            share_capital=10.0,
        )
        series = monthly_pl_series(self.catalog, [balance])
        self.assertTrue(series[0].identidade_suspeita)


class TestPlCoverage(unittest.TestCase):
    def test_cobertura_usa_a_competencia_mais_recente(self):
        catalog = {
            "1": EtfMetadata(cnpj="1", name="A", kind="etf"),
            "2": EtfMetadata(cnpj="2", name="B", kind="etf"),
            "3": EtfMetadata(cnpj="3", name="C", kind="etf"),
            # Feeder nao conta no denominador: a lacuna e de ETF.
            "4": EtfMetadata(cnpj="4", name="D", kind="feeder"),
        }
        balances = [
            _coerente("1", "202607", 10.0),
            _coerente("1", "202608", 20.0),
            _coerente("2", "202608", 30.0),
        ]
        coverage = pl_coverage(catalog, monthly_pl_series(catalog, balances))

        self.assertEqual(coverage.competencia, "202608")
        self.assertEqual(coverage.etfs_no_catalogo, 3)
        self.assertEqual(coverage.etfs_com_pl, 2)
        self.assertAlmostEqual(coverage.pl_total, 50.0)
        self.assertAlmostEqual(coverage.coverage_ratio, 2 / 3)
        self.assertEqual(coverage.identidades_suspeitas, 0)

    def test_serie_vazia_nao_divide_por_zero(self):
        coverage = pl_coverage({}, [])
        self.assertEqual(coverage.etfs_com_pl, 0)
        self.assertEqual(coverage.coverage_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
