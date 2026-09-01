"""Testes do monitor de prateleiras.

As fontes reais estao bloqueadas pela network policy deste ambiente, entao os
testes exercitam o pipeline inteiro contra as fixtures de tests/fixtures/.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from shelf_monitor.collectors.cvm import (
    CvmCollector,
    WatchlistMatcher,
    _decode_rows,
    discover_managers,
    load_registro_rows,
)
from shelf_monitor.collectors.open_finance import _brand_matches, _flatten_funds
from shelf_monitor.collectors.xp import XpCollector
from shelf_monitor.config import Config
from shelf_monitor.diff import assess_quality, diff_cvm, diff_shelf
from shelf_monitor.merge import merge_offers, source_summary
from shelf_monitor.models import (
    AssetClass,
    CollectorStrategy,
    CvmSnapshot,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import (
    classify_asset_class,
    format_cnpj,
    name_slug,
    normalize_cnpj,
    normalize_cvm_code,
    normalize_text,
    parse_money,
    parse_number,
)
from shelf_monitor.report.aggregate import build_dashboard_data
from shelf_monitor.report.dashboard import render_dashboard
from shelf_monitor.report.digest import render_digest, render_markdown
from shelf_monitor.report.mailer import build_subject, send_digest
from shelf_monitor.storage import latest_shelf_snapshot, save_shelf_snapshot

FIXTURES = Path(__file__).parent / "fixtures"
CVM_ZIP = FIXTURES / "cvm" / "registro_fundo_classe.zip"
OPEN_FINANCE_JSON = FIXTURES / "open_finance" / "funds_page1.json"


def temp_config() -> tuple[Config, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    config = Config(data_dir=tmp.name)
    config.ensure_dirs()
    return config, tmp


class TestNormalize(unittest.TestCase):
    def test_cnpj_normalization(self):
        self.assertEqual(normalize_cnpj("12.345.678/0001-91"), "12345678000191")
        self.assertEqual(normalize_cnpj(12345678000191), "12345678000191")
        self.assertIsNone(normalize_cnpj(None))
        self.assertEqual(format_cnpj("12345678000191"), "12.345.678/0001-91")

    def test_cvm_code_normalization_survives_the_type_of_the_source(self):
        """O CSV traz texto com zero a esquerda; o JSON da casa traz numero."""
        self.assertEqual(normalize_cvm_code("0555001"), "555001")
        self.assertEqual(normalize_cvm_code(555001), "555001")
        self.assertEqual(normalize_cvm_code(555001.0), "555001")
        self.assertEqual(normalize_cvm_code(" c-555.001 "), "C555001")
        # Codigo alfanumerico nao perde o zero: ele nao e zero a esquerda de
        # numero, e parte do identificador.
        self.assertEqual(normalize_cvm_code("S001"), "S001")

    def test_cvm_code_refuses_what_is_not_a_code(self):
        for value in (None, "", "   ", "000", "-", True, float("nan")):
            self.assertIsNone(normalize_cvm_code(value), repr(value))

    def test_slug_collapses_legal_form_tokens(self):
        self.assertEqual(
            name_slug("XP MACRO FIC FIM"),
            name_slug("XP MACRO FUNDO DE INVESTIMENTO EM COTAS MULTIMERCADO"),
        )

    def test_asset_class_word_boundaries(self):
        # "DURACAO" nao pode casar com "ACAO"
        self.assertEqual(
            classify_asset_class("Renda Fixa Duracao Livre"), AssetClass.RENDA_FIXA
        )
        self.assertEqual(classify_asset_class(None, None, "BTG Acoes FIA"), AssetClass.ACOES)
        self.assertEqual(
            classify_asset_class("Previdencia Renda Fixa"), AssetClass.PREVIDENCIA
        )
        self.assertEqual(classify_asset_class("Fundo Generico"), AssetClass.OUTROS)

    def test_number_parsing_handles_both_locales(self):
        self.assertEqual(parse_number("R$ 1.234,56"), 1234.56)
        self.assertEqual(parse_number("1234.56"), 1234.56)
        self.assertEqual(parse_number("0,50"), 0.5)
        self.assertEqual(parse_number("1.000.000"), 1_000_000.0)
        self.assertIsNone(parse_number(""))

    def test_money_parsing_reads_dot_as_thousands(self):
        """"R$ 1.000" e mil reais; le-lo como 1.0 erra por 1000x em silencio."""
        self.assertEqual(parse_money("R$ 1.000"), 1000.0)
        self.assertEqual(parse_money("R$ 25.000"), 25_000.0)
        self.assertEqual(parse_money("1.000.000"), 1_000_000.0)
        self.assertEqual(parse_money("R$ 1.234,56"), 1234.56)
        # Sem grupos de tres digitos, o ponto continua sendo decimal.
        self.assertEqual(parse_money("100.00"), 100.0)
        self.assertIsNone(parse_money(None))


class TestCvmCollector(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()

    def tearDown(self):
        self.tmp.cleanup()

    def test_filters_to_watchlist_and_flags_pre_operational(self):
        snapshot = CvmCollector(self.config).collect(local_zip=CVM_ZIP)

        self.assertEqual(snapshot.status, SnapshotStatus.OK)
        names = {f.name for f in snapshot.funds}
        self.assertNotIn("VERDE AM SCENA FIC FIF MULTIMERCADO", names)
        self.assertNotIn("SPX NIMITZ FIC FIF MULTIMERCADO", names)

        houses = snapshot.by_house()
        # Kinea entra pelo Itau
        itau_names = {f.name for f in houses["itau"]}
        self.assertTrue(any("KINEA" in n for n in itau_names))

        pre_op = [f for f in snapshot.funds if f.is_pre_operational]
        self.assertEqual(len(pre_op), 5)

    def test_classes_are_joined_from_registro_classe(self):
        snapshot = CvmCollector(self.config).collect(local_zip=CVM_ZIP)
        fund = next(f for f in snapshot.funds if f.cnpj == "42345678000194")
        self.assertEqual(fund.cvm_classes, ["Renda Fixa"])

    def test_matcher_prefers_cnpj_root_over_name(self):
        matcher = WatchlistMatcher()
        matcher._roots["inter"].add("29650082")  # raiz do BTG, atribuida ao Inter
        house, reason = matcher.match("BTG PACTUAL ASSET MANAGEMENT", "29650082000100")
        self.assertEqual(house, "inter")
        self.assertTrue(reason.startswith("cnpj_root:"))

    def test_utf8_csv_is_not_read_as_latin1_mojibake(self):
        """latin-1 decodifica qualquer byte: se vier primeiro, utf-8 nunca roda
        e "FASE PRE-OPERACIONAL" vira mojibake que nao casa - o radar zera em
        silencio, com status OK."""
        header = "CNPJ_Fundo;Denominacao_Social;Situacao;Gestor"
        row = "12345678000191;FUNDO ACOES UNICO;FASE PRÉ-OPERACIONAL;KINEA INVESTIMENTOS"
        for encoding in ("utf-8", "iso-8859-1"):
            with self.subTest(encoding=encoding):
                rows = _decode_rows(f"{header}\n{row}".encode(encoding))
                self.assertEqual(rows[0]["Situacao"], "FASE PRÉ-OPERACIONAL")

    def test_corrupted_download_falls_through_to_the_legacy_source(self):
        """Um zip corrompido levanta BadZipFile, nao SourceUnavailable."""
        bad_zip = Path(self.tmp.name) / "corrompido.zip"
        bad_zip.write_text("<html>403 Forbidden</html>", encoding="utf-8")

        snapshot = CvmCollector(self.config).collect(local_zip=bad_zip)
        self.assertEqual(snapshot.status, SnapshotStatus.UNAVAILABLE)
        self.assertIn("cad_fi", snapshot.error, "o fallback legado deve ser tentado")

    def test_discover_managers_ranks_by_fund_count(self):
        rows = load_registro_rows(self.config, local_zip=CVM_ZIP)
        results = discover_managers(rows, "BTG PACTUAL")
        self.assertTrue(results)
        name, cnpj, count = results[0]
        self.assertIn("BTG PACTUAL", name)
        self.assertEqual(cnpj, "29650082000100")
        self.assertEqual(count, 3)


class TestOpenFinanceMapping(unittest.TestCase):
    def test_flatten_handles_grouped_envelope(self):
        payload = json.loads(OPEN_FINANCE_JSON.read_text(encoding="utf-8"))
        items = _flatten_funds(payload["data"])
        self.assertEqual(len(items), 7)
        self.assertTrue(all("cnpjNumber" in item for item in items))

    def test_brand_match_respects_word_boundaries(self):
        """Sem fronteira, "XP" casaria "BANCO EXPRESSO" e adotaria o host errado."""
        self.assertFalse(_brand_matches("XP", normalize_text("Banco Expresso S.A.")))
        self.assertTrue(
            _brand_matches("XP", normalize_text("XP Investimentos CCTVM S.A."))
        )
        self.assertFalse(_brand_matches("INTER", normalize_text("Intermedium Capital")))
        self.assertTrue(_brand_matches("INTER", normalize_text("Banco Inter S.A.")))


class TestSourceMerge(unittest.TestCase):
    def test_union_keeps_funds_that_only_one_source_has(self):
        """A prateleira final e a uniao: o que so o site publica nao some."""
        merged = merge_offers({
            "open-finance": [offer("btg", "Fundo A", "11111111000191")],
            "html-scrape": [
                offer("btg", "Fundo A", "11111111000191"),
                offer("btg", "Fundo B", "22222222000192"),
            ],
        })

        by_cnpj = {f.cnpj: f for f in merged}
        self.assertEqual(len(merged), 2)
        self.assertEqual(by_cnpj["11111111000191"].sources, ["open-finance", "html-scrape"])
        self.assertEqual(by_cnpj["22222222000192"].sources, ["html-scrape"])

    def test_open_finance_wins_a_field_conflict(self):
        """Fonte padronizada por regulacao vence o que a pagina resolveu exibir."""
        merged = merge_offers({
            "html-scrape": [offer("btg", "Fundo A", "11111111000191", min_initial=1000.0)],
            "open-finance": [offer("btg", "Fundo A", "11111111000191", min_initial=500.0)],
        })
        self.assertEqual(merged[0].min_initial, 500.0)

    def test_lower_precedence_source_fills_gaps(self):
        """Onde a fonte melhor nao tem valor, a outra preenche em vez de perder."""
        merged = merge_offers({
            "open-finance": [offer("btg", "Fundo A", "11111111000191", min_initial=500.0)],
            "html-scrape": [
                offer("btg", "Fundo A", "11111111000191",
                      min_initial=1000.0, manager_name="Kinea",
                      detail_url="https://exemplo.test/fundo"),
            ],
        })
        self.assertEqual(merged[0].min_initial, 500.0)
        self.assertEqual(merged[0].manager_name, "Kinea")
        self.assertEqual(merged[0].detail_url, "https://exemplo.test/fundo")

    def test_name_variants_are_preserved(self):
        merged = merge_offers({
            "open-finance": [offer("btg", "BTG PACTUAL RF CP FIC FIF", "11111111000191")],
            "html-scrape": [offer("btg", "BTG Renda Fixa Credito Privado", "11111111000191")],
        })
        self.assertEqual(merged[0].name, "BTG PACTUAL RF CP FIC FIF")
        self.assertIn("BTG Renda Fixa Credito Privado", merged[0].extra["name_variants"])

    def test_concrete_class_beats_outros(self):
        merged = merge_offers({
            "open-finance": [offer("btg", "Fundo A", "11111111000191")],
            "html-scrape": [
                offer("btg", "Fundo A", "11111111000191", asset_class=AssetClass.ACOES),
            ],
        })
        self.assertEqual(merged[0].asset_class, AssetClass.ACOES)

    def test_cvm_code_is_merged_like_the_other_identifiers(self):
        """O codigo que so a via do navegador viu sobrevive a uniao."""
        merged = merge_offers({
            "open-finance": [offer("itau", "Fundo A", "11111111000191")],
            "playwright": [
                offer("itau", "Fundo A", "11111111000191", cvm_code="555001"),
            ],
        })
        self.assertEqual(merged[0].cvm_code, "555001")

    def test_summary_counts_what_each_source_alone_saw(self):
        groups = {
            "open-finance": [offer("btg", "A", "11111111000191")],
            "html-scrape": [
                offer("btg", "A", "11111111000191"),
                offer("btg", "B", "22222222000192"),
            ],
        }
        summary = source_summary(groups, merge_offers(groups))
        self.assertEqual(summary["counts"], {"open-finance": 1, "html-scrape": 2})
        self.assertEqual(summary["exclusive"]["html-scrape"], 1)
        self.assertEqual(summary["exclusive"]["open-finance"], 0)
        self.assertEqual(summary["merged_total"], 2)


class TestStrategyCascade(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unexpected_error_does_not_cancel_remaining_strategies(self):
        """Payload malformado de terceiro nao pode cancelar as outras fontes."""
        collector = XpCollector(self.config, self.config.platform("xp"))

        def exploding_open_finance():
            raise AttributeError("'list' object has no attribute 'get'")

        collector._collect_open_finance = exploding_open_finance
        collector._collect_playwright = lambda: [offer("xp", "Fundo Salvo", "11111111000191")]

        snapshot = collector.collect()
        self.assertEqual(snapshot.status, SnapshotStatus.OK)
        self.assertEqual([f.name for f in snapshot.funds], ["Fundo Salvo"])
        self.assertEqual(snapshot.strategy, CollectorStrategy.PLAYWRIGHT)
        # A fonte que caiu vira observacao, nao erro: explica um numero baixo.
        self.assertIn("open-finance", snapshot.error)

    def test_all_sources_are_consulted_and_united(self):
        """Por padrao a coleta nao para na primeira fonte que responde."""
        collector = XpCollector(self.config, self.config.platform("xp"))
        collector._collect_open_finance = lambda: [
            offer("xp", "Fundo A", "11111111000191"),
        ]
        collector._collect_playwright = lambda: [
            offer("xp", "Fundo A", "11111111000191"),
            offer("xp", "Fundo B", "22222222000192"),
        ]

        snapshot = collector.collect()
        self.assertEqual(len(snapshot.funds), 2)
        self.assertEqual(snapshot.strategies, ["open-finance", "playwright"])
        self.assertEqual(
            snapshot.source_counts, {"open-finance": 1, "playwright": 2}
        )
        shared = next(f for f in snapshot.funds if f.cnpj == "11111111000191")
        self.assertEqual(shared.sources, ["open-finance", "playwright"])

    def test_stop_at_first_source_keeps_the_fast_path(self):
        collector = XpCollector(
            self.config, self.config.platform("xp"), stop_at_first_source=True
        )
        collector._collect_open_finance = lambda: [
            offer("xp", f"Fundo {i}", f"{i:014d}") for i in range(10)
        ]
        called = []
        collector._collect_playwright = lambda: called.append(True) or []

        snapshot = collector.collect()
        self.assertEqual(len(snapshot.funds), 10)
        self.assertEqual(called, [], "a segunda fonte nao deve ser consultada")


def offer(platform: str, name: str, cnpj: str | None = None, **kwargs) -> FundOffer:
    return FundOffer(platform=platform, name=name, cnpj=cnpj, **kwargs)


def snapshot_for(
    platform: str, funds: list[FundOffer], when: datetime, status=SnapshotStatus.OK
) -> ShelfSnapshot:
    return ShelfSnapshot(
        platform=platform,
        collected_at=when,
        strategy=CollectorStrategy.OPEN_FINANCE,
        status=status,
        funds=funds,
    )


class TestShelfDiff(unittest.TestCase):
    def setUp(self):
        self.yesterday = datetime.now() - timedelta(days=1)
        self.today = datetime.now()

    def test_detects_added_removed_and_changed(self):
        previous = snapshot_for("btg", [
            offer("btg", "Fundo A", "11111111000191", min_initial=1000.0),
            offer("btg", "Fundo B", "22222222000192"),
        ], self.yesterday)
        current = snapshot_for("btg", [
            offer("btg", "Fundo A", "11111111000191", min_initial=500.0),
            offer("btg", "Fundo C", "33333333000193"),
        ], self.today)

        diff = diff_shelf(current, previous)
        self.assertEqual([f.name for f in diff.added], ["Fundo C"])
        self.assertEqual([f.name for f in diff.removed], ["Fundo B"])
        self.assertEqual(len(diff.changed), 1)
        self.assertEqual(diff.changed[0].changes[0].field, "min_initial")
        self.assertFalse(diff.suspect)

    def test_partial_collection_suppresses_false_churn(self):
        previous = snapshot_for(
            "btg", [offer("btg", f"Fundo {i}", f"{i:014d}") for i in range(1, 21)],
            self.yesterday,
        )
        current = snapshot_for(
            "btg", [offer("btg", "Fundo 1", f"{1:014d}")], self.today
        )

        diff = diff_shelf(current, previous)
        self.assertTrue(diff.suspect)
        self.assertEqual(diff.removed, [], "saidas devem ser omitidas em coleta suspeita")

    def test_unavailable_snapshot_yields_no_churn(self):
        previous = snapshot_for("nu", [offer("nu", "Fundo A", "11111111000191")], self.yesterday)
        current = snapshot_for("nu", [], self.today, status=SnapshotStatus.UNAVAILABLE)

        diff = diff_shelf(current, previous)
        self.assertTrue(diff.suspect)
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.removed, [])

    def test_legal_form_variation_keeps_the_same_identity(self):
        """FIF -> FIC FIF e mudanca de forma juridica, nao troca de fundo."""
        previous = snapshot_for("inter", [
            offer("inter", "Inter Conservador Renda Fixa Referenciado DI FIF"),
        ], self.yesterday)
        current = snapshot_for("inter", [
            offer("inter", "Inter Conservador Renda Fixa Referenciado DI FIC FIF"),
        ], self.today)

        diff = diff_shelf(current, previous)
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.removed, [])
        self.assertEqual(diff.changed, [], "o slug ja absorve a forma juridica")

    def test_rename_without_cnpj_is_a_change_not_a_swap(self):
        """Nome quase igual sem CNPJ: renomeacao, nao um fundo novo + um extinto."""
        previous = snapshot_for("btg", [
            offer("btg", "BTG Pactual Absoluto Institucional Long Biased"),
        ], self.yesterday)
        current = snapshot_for("btg", [
            offer("btg", "BTG Pactual Absoluto Institucional Long Biased II"),
        ], self.today)

        diff = diff_shelf(current, previous)
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.removed, [])
        self.assertEqual(len(diff.changed), 1)
        self.assertEqual(diff.changed[0].changes[0].field, "name")

    def test_numbered_mandates_are_not_merged_as_a_rename(self):
        """"Selection 30" e "Selection 60" sao 93% parecidos e fundos distintos."""
        previous = snapshot_for("xp", [
            offer("xp", "XP Selection Multimercado 30"),
        ], self.yesterday)
        current = snapshot_for("xp", [
            offer("xp", "XP Selection Multimercado 60"),
        ], self.today)

        diff = diff_shelf(current, previous)
        self.assertEqual([f.name for f in diff.added], ["XP Selection Multimercado 60"])
        self.assertEqual([f.name for f in diff.removed], ["XP Selection Multimercado 30"])
        self.assertEqual(diff.changed, [])

    def test_colliding_slugs_do_not_hide_a_real_removal(self):
        """Dois fundos com o mesmo slug nao podem se sobrescrever no indice."""
        previous = snapshot_for("inter", [
            offer("inter", "Inter Conservador Renda Fixa FIF"),
            offer("inter", "Inter Conservador Renda Fixa FIC FIF"),
            offer("inter", "Outro Fundo X"),
        ], self.yesterday)
        current = snapshot_for("inter", [
            offer("inter", "Inter Conservador Renda Fixa FIF"),
            offer("inter", "Outro Fundo X"),
        ], self.today)

        diff = diff_shelf(current, previous)
        self.assertEqual(len(diff.removed), 1)

    def test_shrunk_collection_is_downgraded_so_it_cannot_become_baseline(self):
        previous = snapshot_for(
            "inter", [offer("inter", f"F{i}", f"{i:014d}") for i in range(400)],
            self.yesterday,
        )
        current = snapshot_for(
            "inter", [offer("inter", f"F{i}", f"{i:014d}") for i in range(150)],
            self.today,
        )

        assess_quality(current, previous)
        self.assertEqual(current.status, SnapshotStatus.PARTIAL)
        self.assertIn("tratada como parcial", current.error)

    def test_different_funds_are_not_merged_as_a_rename(self):
        previous = snapshot_for("btg", [offer("btg", "Verde AM Scena")], self.yesterday)
        current = snapshot_for("btg", [offer("btg", "SPX Nimitz")], self.today)

        diff = diff_shelf(current, previous)
        self.assertEqual([f.name for f in diff.added], ["SPX Nimitz"])
        self.assertEqual([f.name for f in diff.removed], ["Verde AM Scena"])
        self.assertEqual(diff.changed, [])

    def test_missing_field_is_not_reported_as_change(self):
        previous = snapshot_for("btg", [
            offer("btg", "Fundo A", "11111111000191", admin_fee_pct=2.0),
        ], self.yesterday)
        current = snapshot_for("btg", [
            offer("btg", "Fundo A", "11111111000191", admin_fee_pct=None),
        ], self.today)

        diff = diff_shelf(current, previous)
        self.assertEqual(diff.changed, [], "campo que sumiu da fonte nao e mudanca de oferta")


class TestCvmDiff(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        self.snapshot = CvmCollector(self.config).collect(local_zip=CVM_ZIP)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_reports_only_pre_operational(self):
        diff = diff_cvm(self.snapshot, None)
        self.assertTrue(diff.is_first_run)
        self.assertEqual(len(diff.new_registrations), 5)

    def test_launch_is_detected_from_situation_transition(self):
        previous = CvmCollector(self.config).collect(local_zip=CVM_ZIP)
        for fund in previous.funds:
            if fund.cnpj == "22345678000192":
                fund.situation = "FASE PRE-OPERACIONAL"
        for fund in self.snapshot.funds:
            if fund.cnpj == "22345678000192":
                fund.situation = "EM FUNCIONAMENTO NORMAL"

        diff = diff_cvm(self.snapshot, previous)
        self.assertEqual(len(diff.launches), 1)
        self.assertEqual(diff.launches[0].fund.cnpj, "22345678000192")

    def test_collapsed_extract_does_not_report_mass_cancellation(self):
        """Se a CVM renomear uma coluna, a watchlist casa com nada - isso nao
        pode virar "800 fundos sairam do cadastro" no digest."""
        previous = CvmCollector(self.config).collect(local_zip=CVM_ZIP)
        empty = CvmSnapshot(
            collected_at=datetime.now(),
            status=SnapshotStatus.PARTIAL,
            funds=[],
            error="nenhum fundo casou com a watchlist",
        )

        diff = diff_cvm(empty, previous)
        self.assertTrue(diff.suspect)
        self.assertEqual(diff.cancelled, [])

    def test_new_registration_detected_against_baseline(self):
        previous = CvmCollector(self.config).collect(local_zip=CVM_ZIP)
        previous.funds = [f for f in previous.funds if f.cnpj != "82345678000198"]

        diff = diff_cvm(self.snapshot, previous)
        self.assertEqual([f.cnpj for f in diff.new_registrations], ["82345678000198"])


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_preserves_funds(self):
        snapshot = snapshot_for("btg", [
            offer("btg", "Fundo A", "11111111000191", min_initial=1000.0,
                  asset_class=AssetClass.RENDA_FIXA),
        ], datetime.now())
        save_shelf_snapshot(self.config, snapshot)

        loaded = latest_shelf_snapshot(self.config, "btg")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.funds[0].cnpj, "11111111000191")
        self.assertEqual(loaded.funds[0].asset_class, AssetClass.RENDA_FIXA)

    def test_roundtrip_preserves_the_cvm_code(self):
        snapshot = snapshot_for("itau", [
            offer("itau", "Fundo A", None, cvm_code="555001"),
        ], datetime.now())
        save_shelf_snapshot(self.config, snapshot)

        loaded = latest_shelf_snapshot(self.config, "itau")
        self.assertEqual(loaded.funds[0].cvm_code, "555001")

    def test_snapshot_written_before_the_cvm_code_still_loads(self):
        """Historico gravado sem a chave nova continua comparavel."""
        legacy = {
            "platform": "btg",
            "name": "Fundo A",
            "cnpj": "11111111000191",
            "sources": ["open-finance"],
        }
        restored = FundOffer.from_dict(legacy)
        self.assertIsNone(restored.cvm_code)
        self.assertEqual(restored.identity, "11111111000191")

    def test_unavailable_snapshot_is_not_used_as_baseline(self):
        old = snapshot_for("btg", [offer("btg", "Fundo A", "11111111000191")],
                           datetime.now() - timedelta(days=2))
        save_shelf_snapshot(self.config, old)
        broken = snapshot_for("btg", [], datetime.now() - timedelta(days=1),
                              status=SnapshotStatus.UNAVAILABLE)
        save_shelf_snapshot(self.config, broken)

        baseline = latest_shelf_snapshot(self.config, "btg", before=datetime.now().date())
        self.assertEqual(len(baseline.funds), 1)


class TestMailer(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        self.now = datetime.now()
        self.yesterday = self.now - timedelta(days=1)

    def tearDown(self):
        self.tmp.cleanup()

    def _report(self, diffs, cvm_diff=None, snapshots=None):
        return RunReport(
            generated_at=self.now,
            snapshots=snapshots if snapshots is not None else [],
            shelf_diffs=diffs,
            cvm_diff=cvm_diff,
        )

    def test_subject_leads_with_the_news(self):
        previous = snapshot_for("btg", [
            offer("btg", "Fundo A", "11111111000191", min_initial=1000.0),
            offer("btg", "Fundo B", "22222222000192"),
        ], self.yesterday)
        current = snapshot_for("btg", [
            offer("btg", "Fundo A", "11111111000191", min_initial=500.0),
            offer("btg", "Fundo C", "33333333000193"),
        ], self.now)

        subject = build_subject(self._report([diff_shelf(current, previous)]))
        self.assertIn("1 entradas / 1 saidas", subject)
        self.assertIn("1 alteracoes", subject)

    def test_subject_says_when_nothing_changed(self):
        snapshot = snapshot_for("btg", [offer("btg", "Fundo A", "11111111000191")], self.now)
        subject = build_subject(self._report([diff_shelf(snapshot, snapshot)]))
        self.assertIn("sem mudancas", subject)

    def test_subject_distinguishes_total_source_failure(self):
        broken = snapshot_for("nu", [], self.now, status=SnapshotStatus.UNAVAILABLE)
        subject = build_subject(
            self._report([diff_shelf(broken, None)], snapshots=[broken])
        )
        self.assertIn("nenhuma fonte acessivel", subject)

    def test_digest_summary_distinguishes_the_three_silences(self):
        """Coleta falha, primeira coleta e prateleira estavel nao sao a mesma coisa."""
        funds = [offer("btg", "Fundo A", "11111111000191")]
        stable_prev = snapshot_for("btg", funds, self.yesterday)
        stable_curr = snapshot_for("btg", funds, self.now)
        broken = snapshot_for("btg", [], self.now, status=SnapshotStatus.UNAVAILABLE)

        all_broken = render_markdown(
            self._report([diff_shelf(broken, None)], snapshots=[broken])
        )
        self.assertIn("Nenhuma fonte foi acessivel", all_broken)

        first = render_markdown(
            self._report([diff_shelf(stable_curr, None)], snapshots=[stable_curr])
        )
        self.assertIn("Primeira coleta", first)

        stable = render_markdown(
            self._report([diff_shelf(stable_curr, stable_prev)], snapshots=[stable_curr])
        )
        self.assertIn("Nenhuma mudanca", stable)

    def test_send_is_a_noop_without_smtp_config(self):
        snapshot = snapshot_for("btg", [offer("btg", "Fundo A", "11111111000191")], self.now)
        result = send_digest(self.config, self._report([diff_shelf(snapshot, None)]))
        self.assertIn("SMTP nao configurado", result)


class TestReports(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        yesterday = datetime.now() - timedelta(days=1)
        now = datetime.now()

        self.xp = snapshot_for("xp", [
            offer("xp", "XP Allocation Multimercado", "10345678000110",
                  asset_class=AssetClass.MULTIMERCADO, min_initial=500.0,
                  manager_name="XP Allocation", admin_fee_pct=1.5),
            offer("xp", "Verde AM Scena", "20345678000120",
                  asset_class=AssetClass.MULTIMERCADO, min_initial=5000.0,
                  manager_name="Verde Asset"),
        ], now)
        self.btg = snapshot_for("btg", [
            offer("btg", "BTG Renda Fixa CP", "12345678000191",
                  asset_class=AssetClass.RENDA_FIXA, min_initial=100.0,
                  manager_name="BTG Pactual Asset", admin_fee_pct=0.5),
            offer("btg", "Verde AM Scena", "20345678000120",
                  asset_class=AssetClass.MULTIMERCADO, min_initial=5000.0,
                  manager_name="Verde Asset"),
        ], now)
        self.nu = snapshot_for("nu", [], now, status=SnapshotStatus.UNAVAILABLE)
        self.nu.error = "Nu nao expoe prateleira deslogada"

        previous_btg = snapshot_for("btg", [
            offer("btg", "BTG Renda Fixa CP", "12345678000191",
                  asset_class=AssetClass.RENDA_FIXA, min_initial=1000.0,
                  manager_name="BTG Pactual Asset", admin_fee_pct=0.5),
            offer("btg", "Verde AM Scena", "20345678000120",
                  asset_class=AssetClass.MULTIMERCADO, min_initial=5000.0),
            offer("btg", "Fundo Descontinuado", "99999999000199"),
        ], yesterday)

        cvm_snapshot = CvmCollector(self.config).collect(local_zip=CVM_ZIP)
        self.report = RunReport(
            generated_at=now,
            snapshots=[self.xp, self.btg, self.nu],
            shelf_diffs=[
                diff_shelf(self.xp, None),
                diff_shelf(self.btg, previous_btg),
                diff_shelf(self.nu, None),
            ],
            cvm_snapshot=cvm_snapshot,
            cvm_diff=diff_cvm(cvm_snapshot, None),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_exclusive_counts_ignore_unavailable_platforms(self):
        data = build_dashboard_data(self.report)
        by_id = {p["platform_id"]: p for p in data["platforms"]}
        # Verde esta nas duas prateleiras: nao e exclusivo de nenhuma
        self.assertEqual(by_id["xp"]["exclusive_count"], 1)
        self.assertEqual(by_id["btg"]["exclusive_count"], 1)
        self.assertEqual(by_id["nu"]["total_funds"], 0)

    def test_dashboard_is_self_contained(self):
        path = render_dashboard(self.config, self.report)
        content = path.read_text(encoding="utf-8")
        self.assertIn('id="dashboard-data"', content)
        self.assertNotIn("<script src=", content, "dashboard nao deve depender de CDN")
        self.assertIn("prefers-color-scheme: dark", content)
        # Os dados embutidos precisam ser JSON valido
        start = content.index('id="dashboard-data">') + len('id="dashboard-data">')
        end = content.index("</script>", start)
        json.loads(content[start:end])

    def test_digest_reports_change_and_unavailable_source(self):
        markdown = render_markdown(self.report)
        self.assertIn("aplicacao minima", markdown)
        self.assertIn("Fundo Descontinuado", markdown)
        self.assertIn("Fontes indisponiveis", markdown)
        self.assertIn("pre-operacional", markdown.lower())

    def test_external_names_cannot_inject_markup(self):
        """Nome de fundo vem da fonte externa: nao pode virar markup executavel."""
        payload = '<img src=x onerror=alert(1)>"</script><script>alert(2)</script>'
        snapshot = snapshot_for("btg", [
            offer("btg", payload, "11111111000191", manager_name=payload,
                  min_initial=100.0),
        ], datetime.now())
        report = RunReport(
            generated_at=datetime.now(), snapshots=[snapshot],
            shelf_diffs=[diff_shelf(snapshot, None)],
        )

        dashboard = render_dashboard(self.config, report).read_text(encoding="utf-8")
        # O payload precisa estar no arquivo (e o dado); o que nao pode e virar
        # markup: a pagina tem exatamente dois <script> proprios.
        self.assertEqual(dashboard.count("<script"), 2)
        self.assertEqual(dashboard.count("</script>"), 2)
        self.assertNotIn("<img", dashboard)

        # E o bloco de dados continua JSON valido, com o valor intacto.
        start = dashboard.index('id="dashboard-data">') + len('id="dashboard-data">')
        data = json.loads(dashboard[start:dashboard.index("</script>", start)])
        self.assertEqual(data["platforms"][0]["top_managers"][0]["name"], payload)

        from shelf_monitor.report.digest import render_html
        digest = render_html(report)
        self.assertNotIn("<script", digest)
        self.assertNotIn("<img", digest)

    def test_spreadsheet_has_a_tab_per_platform_and_working_formulas(self):
        from openpyxl import load_workbook

        from shelf_monitor.report.spreadsheet import render_spreadsheet

        path = render_spreadsheet(self.config, [self.xp, self.btg, self.nu])
        workbook = load_workbook(path)

        # Resumo primeiro, consolidado, uma aba por plataforma coletada, saude
        self.assertIn("Resumo", workbook.sheetnames)
        self.assertIn("Consolidado", workbook.sheetnames)
        self.assertIn("XP Investimentos", workbook.sheetnames)
        self.assertIn("BTG Pactual", workbook.sheetnames)
        self.assertIn("Coleta", workbook.sheetnames)
        # Nu falhou: nao ganha aba, mas aparece na saude da coleta
        self.assertNotIn("Nu (Nubank)", workbook.sheetnames)

        consolidated = workbook["Consolidado"]
        self.assertEqual(consolidated.max_row, 1 + len(self.xp.funds) + len(self.btg.funds))

        # A exclusividade e formula, com intervalo limitado aos dados: uma
        # referencia a coluna inteira torna a planilha lenta demais para abrir.
        from shelf_monitor.report.spreadsheet import _COL_EXCLUSIVE

        formula = consolidated.cell(row=2, column=_COL_EXCLUSIVE).value
        self.assertTrue(formula.startswith("=IF("))
        self.assertIn("COUNTIF($C$2:$C$", formula)
        self.assertNotIn("$C:$C", formula)

    def test_digest_files_are_written(self):
        md_path, html_path = render_digest(self.config, self.report)
        self.assertTrue(md_path.exists() and html_path.exists())
        html = html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script", html, "digest de e-mail nao deve conter JS")


if __name__ == "__main__":
    unittest.main()
