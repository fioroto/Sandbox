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
    PL_ACCOUNT,
    RETAINED_RESULT_ACCOUNT,
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


class TestReadEtfBalances(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_le_as_tres_contas_de_um_etf(self):
        path = _write_balancete(
            self.dir,
            "202608",
            [
                _row("21.407.758/0001-19", PL_ACCOUNT, "8086776795.12"),
                _row("21.407.758/0001-19", SHARE_CAPITAL_ACCOUNT, "5095556452.74"),
                _row("21.407.758/0001-19", RETAINED_RESULT_ACCOUNT, "2991220342.38"),
                # Conta fora do mapa: entra no arquivo, nao no registro.
                _row("21.407.758/0001-19", "10000007", "8056822693.69"),
            ],
        )
        balances = read_etf_balances(path, {"21407758000119"}, "202608")

        self.assertEqual(len(balances), 1)
        balance = balances[0]
        self.assertEqual(balance.cnpj, "21407758000119")
        self.assertEqual(balance.competencia, "202608")
        self.assertEqual(balance.dt_comptc, date(2026, 8, 31))
        self.assertAlmostEqual(balance.pl, 8086776795.12, places=2)
        self.assertAlmostEqual(balance.share_capital, 5095556452.74, places=2)
        self.assertAlmostEqual(balance.retained_result, 2991220342.38, places=2)

    def test_identidade_contabil_fecha_com_os_numeros_reais(self):
        """PL = capital de cotas + resultado, com os saldos medidos em 08/2026."""
        balance = EtfMonthlyBalance(
            cnpj="21407758000119",
            competencia="202608",
            dt_comptc=date(2026, 8, 31),
            pl=8086776795.12,
            share_capital=5095556452.74,
            retained_result=2991220342.38,
        )
        self.assertTrue(balance.identity_holds)

    def test_identidade_quebra_quando_a_conta_lida_e_outra(self):
        """A guarda contra renumeracao do plano: sem ela, o monitor publicaria
        como PL um saldo que e outra coisa."""
        balance = EtfMonthlyBalance(
            cnpj="21407758000119",
            competencia="202608",
            dt_comptc=None,
            pl=8086776795.12,
            share_capital=1.0,
            retained_result=1.0,
        )
        self.assertFalse(balance.identity_holds)

    def test_ignora_cnpj_fora_do_catalogo(self):
        path = _write_balancete(
            self.dir,
            "202608",
            [
                _row("11.111.111/0001-11", PL_ACCOUNT, "1000.00"),
                _row("22.222.222/0001-22", PL_ACCOUNT, "2000.00"),
            ],
        )
        balances = read_etf_balances(path, {"11111111000111"}, "202608")
        self.assertEqual([b.cnpj for b in balances], ["11111111000111"])

    def test_fundo_sem_conta_de_pl_fica_de_fora(self):
        """Sem o PL nao ha o que publicar - o registro nao entra meio vazio."""
        path = _write_balancete(
            self.dir,
            "202608",
            [_row("11.111.111/0001-11", SHARE_CAPITAL_ACCOUNT, "500.00")],
        )
        self.assertEqual(read_etf_balances(path, {"11111111000111"}, "202608"), [])

    def test_zip_ausente_ou_invalido_devolve_vazio(self):
        self.assertEqual(read_etf_balances(None, {"x"}, "202608"), [])
        self.assertEqual(
            read_etf_balances(self.dir / "nao-existe.zip", {"x"}, "202608"), []
        )
        quebrado = self.dir / "quebrado.zip"
        quebrado.write_text("nao sou um zip", encoding="utf-8")
        self.assertEqual(read_etf_balances(quebrado, {"x"}, "202608"), [])


def _metadata(cnpj: str, name: str, category: str = "Ações Brasil") -> EtfMetadata:
    return EtfMetadata(cnpj=cnpj, name=name, kind="etf", category=category)


class TestMonthlyPlSeries(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "21407758000119": _metadata("21407758000119", "IT NOW IBOVESPA"),
            "10406511000161": _metadata("10406511000161", "ISHARES IBOVESPA"),
        }

    def _balance(self, cnpj: str, competencia: str, pl: float) -> EtfMonthlyBalance:
        # Saldos coerentes com a identidade, para nao marcar suspeita sem motivo.
        return EtfMonthlyBalance(
            cnpj=cnpj,
            competencia=competencia,
            dt_comptc=None,
            pl=pl,
            share_capital=pl,
            retained_result=0.0,
        )

    def test_calcula_a_variacao_contra_o_mes_anterior(self):
        series = monthly_pl_series(
            self.catalog,
            [
                self._balance("21407758000119", "202608", 8_086_776_795.12),
                self._balance("21407758000119", "202607", 7_254_168_942.00),
            ],
        )
        por_competencia = {s.competencia: s for s in series}

        julho = por_competencia["202607"]
        self.assertIsNone(julho.pl_anterior)
        self.assertIsNone(julho.variacao_pl)

        agosto = por_competencia["202608"]
        self.assertAlmostEqual(agosto.pl_anterior, 7_254_168_942.00, places=2)
        self.assertAlmostEqual(agosto.variacao_pl, 832_607_853.12, places=2)
        self.assertAlmostEqual(agosto.variacao_pct, 11.4776, places=3)

    def test_cnpj_fora_do_catalogo_nao_entra_na_serie(self):
        series = monthly_pl_series(
            self.catalog, [self._balance("99999999000199", "202608", 1_000.0)]
        )
        self.assertEqual(series, [])

    def test_identidade_fora_de_linha_marca_o_registro(self):
        balance = EtfMonthlyBalance(
            cnpj="21407758000119",
            competencia="202608",
            dt_comptc=None,
            pl=1_000.0,
            share_capital=1.0,
            retained_result=1.0,
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
            EtfMonthlyBalance("1", "202607", None, 10.0, 10.0, 0.0),
            EtfMonthlyBalance("1", "202608", None, 20.0, 20.0, 0.0),
            EtfMonthlyBalance("2", "202608", None, 30.0, 30.0, 0.0),
        ]
        coverage = pl_coverage(catalog, monthly_pl_series(catalog, balances))

        self.assertEqual(coverage.competencia, "202608")
        self.assertEqual(coverage.etfs_no_catalogo, 3)
        self.assertEqual(coverage.etfs_com_pl, 2)
        self.assertAlmostEqual(coverage.pl_total, 50.0)
        self.assertAlmostEqual(coverage.coverage_ratio, 2 / 3)

    def test_serie_vazia_nao_divide_por_zero(self):
        coverage = pl_coverage({}, [])
        self.assertEqual(coverage.etfs_com_pl, 0)
        self.assertEqual(coverage.coverage_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
