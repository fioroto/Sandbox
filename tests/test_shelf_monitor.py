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

from shelf_monitor.collectors.cvm import CvmCollector, WatchlistMatcher, discover_managers, load_registro_rows
from shelf_monitor.collectors.open_finance import _flatten_funds
from shelf_monitor.config import Config
from shelf_monitor.diff import diff_cvm, diff_shelf
from shelf_monitor.models import (
    AssetClass,
    CollectorStrategy,
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
        self.assertIsNone(parse_number(""))


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

    def test_digest_files_are_written(self):
        md_path, html_path = render_digest(self.config, self.report)
        self.assertTrue(md_path.exists() and html_path.exists())
        html = html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script", html, "digest de e-mail nao deve conter JS")


if __name__ == "__main__":
    unittest.main()
