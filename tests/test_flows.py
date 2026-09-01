"""Testes da captacao (informe diario da CVM).

Tudo offline, contra as fixtures de tests/fixtures/market/ - os zips mensais
reproduzem o formato real (latin-1, ';', decimal com ponto) nos dois schemas,
o pre e o pos RCVM 175.

Rode com: python -m unittest tests.test_flows -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.export import write_database
from shelf_monitor.market.flows import (
    import_flows,
    load_summaries,
    month_window,
    parse_informe,
    run_flows_import,
    summarize_watchlist_managers,
    write_flows,
)
from shelf_monitor.market.importer import run_import
from shelf_monitor.market.models import MarketDatabase

FIXTURES = Path(__file__).parent / "fixtures" / "market"
TODAY = date(2026, 8, 15)  # data congelada: 2026-08 e o mes parcial

BTG = "12345678000191"
KINEA = "42345678000194"
VERDE = "70345678000170"


def temp_config() -> tuple[Config, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    config = Config(data_dir=tmp.name)
    config.ensure_dirs()
    return config, tmp


class TestMonthWindow(unittest.TestCase):
    def test_window_ends_at_the_current_month(self):
        self.assertEqual(
            month_window(date(2026, 1, 15), 3), ["2025-11", "2025-12", "2026-01"]
        )

    def test_default_window_is_12_closed_months_plus_the_current(self):
        window = month_window(TODAY)
        self.assertEqual(len(window), 13)
        self.assertEqual(window[0], "2025-08")
        self.assertEqual(window[-1], "2026-08")


class TestParseInforme(unittest.TestCase):
    def setUp(self):
        self.flows, self.unknown = parse_informe(
            FIXTURES / "inf_diario_fi_202607.zip", "2026-07"
        )

    def test_masked_cnpj_matches_the_unmasked_registry(self):
        """O informe pode vir com mascara - mesma causa n.1 de join vazio."""
        self.assertIn(BTG, self.flows)

    def test_inflow_and_outflow_sum_over_the_month(self):
        flow = self.flows[BTG]
        self.assertAlmostEqual(flow.inflow, 3_500_000.0)
        self.assertAlmostEqual(flow.outflow, 750_000.0)
        self.assertAlmostEqual(flow.net, 2_750_000.0)

    def test_net_worth_is_the_last_date_not_the_file_order(self):
        """As linhas de 31/07 vem antes da de 30/07 na fixture de proposito."""
        flow = self.flows[BTG]
        self.assertEqual(flow.last_date, date(2026, 7, 31))
        self.assertAlmostEqual(flow.net_worth, 1_000_000_000.0)

    def test_subclasses_of_the_same_day_sum_their_net_worth(self):
        """Subclasses partilham o CNPJ da classe; o PL do dia e a soma delas."""
        self.assertEqual(self.flows[BTG].shareholders, 11_000)

    def test_days_count_distinct_dates(self):
        self.assertEqual(self.flows[BTG].days, 2)
        self.assertEqual(self.flows[KINEA].days, 1)

    def test_redemption_only_class_goes_negative(self):
        self.assertAlmostEqual(self.flows[KINEA].net, -3_000_000.0)

    def test_unknown_column_is_reported_not_swallowed(self):
        self.assertEqual(self.unknown, ["ID_INFORME"])

    def test_old_schema_still_parses(self):
        """Uma janela de 13 meses pode atravessar a virada da RCVM 175."""
        flows, unknown = parse_informe(
            FIXTURES / "inf_diario_fi_202412.zip", "2024-12"
        )
        self.assertAlmostEqual(flows[BTG].inflow, 100_000.0)
        self.assertAlmostEqual(flows[VERDE].net, 3_000_000.0)
        self.assertEqual(unknown, [])


class TestImportFlows(unittest.TestCase):
    def setUp(self):
        self.config, self._tmp = temp_config()
        self.addCleanup(self._tmp.cleanup)
        self.result = import_flows(
            self.config, months=3, local_dir=FIXTURES, today=TODAY
        )

    def test_missing_month_is_a_note_not_an_exception(self):
        """O caso tipico: o zip do mes ainda nao publicado pela CVM."""
        self.assertEqual(self.result.window, ["2026-06", "2026-07", "2026-08"])
        self.assertEqual(self.result.missing_months, ["2026-06"])

    def test_current_month_is_partial_and_closed_months_are_not(self):
        self.assertFalse(self.result.flows[(BTG, "2026-07")].partial)
        self.assertTrue(self.result.flows[(BTG, "2026-08")].partial)

    def test_counts_reflect_what_actually_came(self):
        counts = self.result.counts()
        self.assertEqual(counts["meses"], 2)
        self.assertEqual(counts["classes_com_captacao"], 2)


class TestWriteFlows(unittest.TestCase):
    def setUp(self):
        self.config, self._tmp = temp_config()
        self.addCleanup(self._tmp.cleanup)
        self.result = import_flows(
            self.config, months=3, local_dir=FIXTURES, today=TODAY
        )
        self.path = write_flows(self.config, self.result)

    def test_rerun_writes_identical_bytes(self):
        """Sem timestamp no CSV, um rerun sem mudanca nao gera diff."""
        first = self.path.read_bytes()
        write_flows(self.config, self.result)
        self.assertEqual(self.path.read_bytes(), first)

    def test_file_has_bom_and_semicolon_like_the_other_tables(self):
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"cnpj_classe;mes;captacao", raw)

    def test_manifest_merge_preserves_the_registry_section(self):
        """Dois produtores, um manifesto: nenhum apaga a secao do outro."""
        manifest_path = self.config.market_dir / "_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("captacao", manifest)
        self.assertEqual(manifest["captacao"]["missing_months"], ["2026-06"])

        # A reimportacao do cadastro regrava o manifesto inteiro - e nao pode
        # perder a secao de captacao no processo.
        write_database(self.config, MarketDatabase(imported_at=TODAY))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("captacao", manifest)
        self.assertIn("imported_at", manifest)

    def test_run_flows_import_returns_none_when_nothing_came(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        result, path = run_flows_import(
            config, months=2, local_dir=Path(empty.name)
        )
        self.assertIsNone(path)
        self.assertEqual(len(result.missing_months), 2)


class TestLoadSummaries(unittest.TestCase):
    def setUp(self):
        self.config, self._tmp = temp_config()
        self.addCleanup(self._tmp.cleanup)
        result = import_flows(self.config, months=3, local_dir=FIXTURES, today=TODAY)
        write_flows(self.config, result)
        self.summaries = load_summaries(self.config)

    def test_windows_cover_only_closed_months(self):
        """Mes de 3 dias uteis nao entra na mesma conta que meses de 21."""
        btg = self.summaries[BTG]
        self.assertAlmostEqual(btg.net_1m, 2_750_000.0)
        self.assertAlmostEqual(btg.net_12m, 2_750_000.0)
        self.assertEqual(btg.months_12m, 1)

    def test_partial_month_is_exposed_separately(self):
        self.assertAlmostEqual(self.summaries[BTG].current_partial, 300_000.0)
        self.assertAlmostEqual(self.summaries[KINEA].current_partial, -1_000_000.0)

    def test_net_worth_comes_from_the_freshest_informe(self):
        """O PL de agosto (parcial) e mais fresco que o de julho fechado."""
        btg = self.summaries[BTG]
        self.assertAlmostEqual(btg.net_worth, 1_001_000_000.0)
        self.assertEqual(btg.as_of, date(2026, 8, 3))
        self.assertEqual(btg.shareholders, 11_050)

    def test_missing_base_returns_none(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        self.assertIsNone(load_summaries(config))


class TestWatchlistManagers(unittest.TestCase):
    def setUp(self):
        self.config, self._tmp = temp_config()
        self.addCleanup(self._tmp.cleanup)
        run_import(self.config, local_dir=FIXTURES)
        result = import_flows(self.config, months=3, local_dir=FIXTURES, today=TODAY)
        write_flows(self.config, result)
        self.summary = summarize_watchlist_managers(self.config)

    def test_houses_aggregate_their_managers_flows(self):
        houses = {h["house"]: h for h in self.summary["houses"]}
        self.assertAlmostEqual(houses["btg"]["net_12m"], 2_750_000.0)
        self.assertAlmostEqual(houses["itau"]["net_12m"], -3_000_000.0)
        self.assertEqual(
            houses["itau"]["managers"][0]["name"], "KINEA INVESTIMENTOS LTDA"
        )

    def test_market_totals_cover_every_class_with_flows(self):
        market = self.summary["market"]
        self.assertEqual(market["classes"], 2)
        self.assertAlmostEqual(market["net_12m"], -250_000.0)

    def test_coverage_is_explicit(self):
        coverage = self.summary["coverage"]
        self.assertEqual(coverage["classes_total"], 2)
        self.assertEqual(coverage["classes_com_gestora"], 2)

    def test_as_of_labels_the_freshest_informe(self):
        self.assertEqual(self.summary["as_of"], "2026-08-03")
        self.assertEqual(self.summary["window_months"], 1)

    def test_missing_either_base_returns_none(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        self.assertIsNone(summarize_watchlist_managers(config))


if __name__ == "__main__":
    unittest.main()
