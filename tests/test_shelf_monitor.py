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
from unittest import mock

from shelf_monitor.collectors import cvm as cvm_mod
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
try:  # `discover -s tests` poe tests/ no sys.path; `-m unittest tests.X` nao.
    from fixtures_comuns import temp_config
except ImportError:  # pragma: no cover - depende de como a suite foi invocada
    from tests.fixtures_comuns import temp_config
from shelf_monitor.diff import assess_quality, diff_cvm, diff_shelf
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.merge import merge_offers, source_summary
from shelf_monitor.models import (
    AssetClass,
    CollectorStrategy,
    CvmFund,
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
from shelf_monitor.storage import (
    latest_cvm_snapshot,
    latest_shelf_snapshot,
    save_cvm_snapshot,
    save_shelf_snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures"
CVM_ZIP = FIXTURES / "cvm" / "registro_fundo_classe.zip"
OPEN_FINANCE_JSON = FIXTURES / "open_finance" / "funds_page1.json"




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
        """Um zip corrompido levanta BadZipFile, nao SourceUnavailable.

        O `download` do fallback e substituido porque o que se testa aqui e o
        encadeamento primaria -> legado, nao a CVM: sem isso o teste passava so
        em maquina sem rede, e numa maquina conectada o cad_fi real baixava e o
        status saia `ok`.
        """
        bad_zip = Path(self.tmp.name) / "corrompido.zip"
        bad_zip.write_text("<html>403 Forbidden</html>", encoding="utf-8")

        def sem_rede(config, url, dest):
            raise SourceUnavailable("rede bloqueada no teste")

        with mock.patch.object(cvm_mod, "download", sem_rede):
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
    platform: str,
    funds: list[FundOffer],
    when: datetime,
    status=SnapshotStatus.OK,
    strategy=CollectorStrategy.OPEN_FINANCE,
) -> ShelfSnapshot:
    """Snapshot de teste, com a procedencia dos fundos declarada.

    Carimba `sources` com a estrategia do snapshot, como os coletores reais
    fazem. Sem isso o fundo nasce sem procedencia e cai fora da vitrine - o que
    e correto e nao e o que a maioria destes testes quer dizer: eles descrevem
    prateleira, e prateleira e o que a casa publica no canal dela.
    """
    for fund in funds:
        if not fund.sources:
            fund.sources = [strategy.value]
    return ShelfSnapshot(
        platform=platform,
        collected_at=when,
        strategy=strategy,
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

    def _pair_with_lost_source(self, prev_count: int, curr_count: int):
        """Baseline de duas fontes contra uma coleta em que a segunda caiu."""
        previous = snapshot_for(
            "btg", [offer("btg", f"F{i}", f"{i:014d}") for i in range(prev_count)],
            self.yesterday,
        )
        previous.strategies = ["open-finance", "playwright"]
        previous.source_counts = {"open-finance": prev_count, "playwright": 30}
        current = snapshot_for(
            "btg", [offer("btg", f"F{i}", f"{i:014d}") for i in range(curr_count)],
            self.today,
        )
        current.strategies = ["open-finance"]
        current.source_counts = {"open-finance": curr_count}
        return previous, current

    def test_missing_source_marks_diff_suspect_even_with_mild_shrink(self):
        """O caso BTG: 9.950 -> 9.818 nao dispara o limiar de encolhimento, mas
        a vitrine comercial inteira sumiu junto com o navegador."""
        previous, current = self._pair_with_lost_source(200, 198)

        diff = diff_shelf(current, previous)
        self.assertTrue(diff.suspect)
        self.assertIn("playwright", diff.suspect_reason)
        self.assertEqual(diff.removed, [], "saida de fonte nao vira saida de fundo")

    def test_missing_source_downgrades_snapshot_to_partial(self):
        previous, current = self._pair_with_lost_source(200, 198)
        current.error = "fontes indisponiveis -> playwright: bloqueado"

        assess_quality(current, previous)
        self.assertEqual(current.status, SnapshotStatus.PARTIAL)
        self.assertIn("tratada como parcial", current.error)
        self.assertIn("bloqueado", current.error, "a causa original e preservada")

    def test_legacy_baseline_without_strategies_stays_calm(self):
        """Snapshot anterior a coluna de fontes: nada a comparar, nada a acusar."""
        previous = snapshot_for(
            "btg", [offer("btg", f"F{i}", f"{i:014d}") for i in range(200)],
            self.yesterday,
        )
        current = snapshot_for(
            "btg", [offer("btg", f"F{i}", f"{i:014d}") for i in range(198)],
            self.today,
        )
        current.strategies = ["open-finance"]

        assess_quality(current, previous)
        diff = diff_shelf(current, previous)
        self.assertEqual(current.status, SnapshotStatus.OK)
        self.assertFalse(diff.suspect)

    def test_missing_source_without_shrink_is_not_suspect(self):
        """A uniao compensou: nao ha cobertura perdida a proteger."""
        previous, current = self._pair_with_lost_source(200, 200)

        assess_quality(current, previous)
        diff = diff_shelf(current, previous)
        self.assertEqual(current.status, SnapshotStatus.OK)
        self.assertFalse(diff.suspect)

    def test_empty_source_in_baseline_is_not_a_loss(self):
        previous, current = self._pair_with_lost_source(200, 198)
        previous.source_counts["playwright"] = 0

        diff = diff_shelf(current, previous)
        self.assertFalse(diff.suspect, "fonte que nao trouxe nada nao e perda")

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

    def _cvm_snapshot(self, count: int, status=SnapshotStatus.OK) -> CvmSnapshot:
        return CvmSnapshot(
            collected_at=datetime.now(),
            status=status,
            funds=[
                CvmFund(cnpj=f"{i:014d}", name=f"Fundo {i}", house="btg",
                        matched_by="regex")
                for i in range(count)
            ],
        )

    def test_worse_cvm_extract_does_not_overwrite_same_day(self):
        """Um retry da tarde que pegou a CVM fora do ar nao pode apagar o
        extrato bom da manha - ele viraria a baseline do radar amanha."""
        save_cvm_snapshot(self.config, self._cvm_snapshot(3))
        refused = save_cvm_snapshot(
            self.config, self._cvm_snapshot(0, SnapshotStatus.UNAVAILABLE)
        )

        self.assertIsNone(refused)
        self.assertEqual(len(latest_cvm_snapshot(self.config).funds), 3)

    def test_better_cvm_extract_overwrites_same_day(self):
        save_cvm_snapshot(self.config, self._cvm_snapshot(1, SnapshotStatus.PARTIAL))
        stored = save_cvm_snapshot(self.config, self._cvm_snapshot(3))

        self.assertIsNotNone(stored)
        self.assertEqual(len(latest_cvm_snapshot(self.config).funds), 3)


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

        self.previous_btg = previous_btg = snapshot_for("btg", [
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
        data = build_dashboard_data(self._com_vitrine())
        by_id = {p["platform_id"]: p for p in data["platforms"]}
        # Verde esta nas duas vitrines: nao e exclusivo de nenhuma
        self.assertEqual(by_id["xp"]["exclusive_count"], 1)
        self.assertEqual(by_id["btg"]["exclusive_count"], 1)
        self.assertEqual(by_id["nu"]["listing_funds"], 0)

    def test_catalog_never_feeds_the_comparison(self):
        """Fundo que so o Open Finance publica nao entra em metrica de vitrine.

        E a regra que o BTG obrigou a existir: com o catalogo dentro, ele saia
        com 884 gestoras contra 213 da XP e 5.767 "exclusivos" contra 284 - tudo
        fundo que ele administra e nenhum cliente ve.
        """
        report = self._com_vitrine()
        btg = next(s for s in report.snapshots if s.platform == "btg")
        oculto = offer("btg", "BTG Carteira Administrada FIC FIM", "77777777000177",
                       manager_name="Gestora Que So Ele Administra", min_initial=50.0)
        oculto.sources = ["open-finance"]
        btg.funds.append(oculto)

        linha = next(
            p for p in build_dashboard_data(report)["platforms"]
            if p["platform_id"] == "btg"
        )
        self.assertEqual(linha["listing_funds"], 2, "a vitrine nao cresceu")
        self.assertEqual(linha["catalog_funds"], 3, "o catalogo cresceu, e fica dito")
        self.assertNotIn(
            "Gestora Que So Ele Administra",
            [m["name"] for m in linha["top_managers"]],
        )
        self.assertEqual(linha["exclusive_count"], 1, "o oculto nao vira diferencial")

    def test_platform_without_a_listing_leaves_the_comparison_with_a_reason(self):
        """Casa so com catalogo sai dos graficos declarando por que."""
        linha = next(
            p for p in build_dashboard_data(self.report)["platforms"]
            if p["platform_id"] == "btg"
        )
        self.assertEqual(linha["listing_funds"], 0)
        self.assertEqual(linha["catalog_funds"], 2)
        self.assertIn("sem vitrine", linha["listing_reason"])

    def test_motivo_do_bloco_indisponivel_chega_ao_dashboard(self):
        """O motivo real, nao um generico.

        `main` anexa o bloco mesmo indisponivel justamente para isto: antes ele
        deixava o campo None, `_bloco` recebia None e a superficie caia num
        "sem base de mercado" que nao era o que tinha acontecido. O motivo era
        impresso no console e morria ali.
        """
        report = self._com_vitrine()
        motivo = "categoria com menos de 20 classes comparaveis"
        report.custo = {"available": False, "reason": motivo}
        report.retail_flow = {"available": False, "reason": motivo}

        data = build_dashboard_data(report)
        self.assertFalse(data["custo"]["available"])
        self.assertEqual(data["custo"]["reason"], motivo)
        self.assertFalse(data["flow"]["available"])
        self.assertEqual(data["flow"]["reason"], motivo)

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

    def test_dashboard_embute_os_assets_e_nao_os_referencia(self):
        """CSS e JS viraram arquivos, mas continuam EMBUTIDOS no HTML.

        O dashboard tem de abrir por file:// sem servidor e sem nada ao lado -
        e o que permite anexa-lo num e-mail ou abri-lo de um pen drive. Um
        `<link>` ou `<script src=>` para os assets quebraria isso em silencio:
        a pagina abriria sem estilo nenhum e sem nenhum grafico.

        O teste tambem falha se um dos arquivos sumir, em vez de gerar um
        dashboard mudo.
        """
        from shelf_monitor.report.dashboard import _ASSETS, _SCRIPT, _STYLE

        self.assertTrue((_ASSETS / "dashboard.css").is_file())
        self.assertTrue((_ASSETS / "dashboard.js").is_file())
        self.assertGreater(len(_STYLE), 500, "CSS vazio ou truncado")
        self.assertGreater(len(_SCRIPT), 5000, "JS vazio ou truncado")

        content = render_dashboard(self.config, self.report).read_text(encoding="utf-8")
        # Um trecho de cada asset tem de estar DENTRO do HTML.
        self.assertIn(_STYLE.strip().splitlines()[0], content)
        self.assertIn("const DATA = JSON.parse", content)
        self.assertNotIn('href="dashboard.css"', content)
        self.assertNotIn('src="dashboard.js"', content)

    def test_digest_reports_change_and_unavailable_source(self):
        markdown = render_markdown(self._com_vitrine())
        self.assertIn("aplicacao minima", markdown)
        self.assertIn("Fundo Descontinuado", markdown)
        self.assertIn("Fontes indisponiveis", markdown)
        self.assertIn("pre-operacional", markdown.lower())

    def test_digest_separates_the_window_from_the_catalog(self):
        """Fundo que entrou so no Open Finance nao vira "entrou na vitrine".

        Num dia de churn do catalogo do BTG - que publica 9.413 fundos que
        nenhum cliente ve - a secao dele afogaria a mudanca real das outras
        casas. A vitrine vai listada; o catalogo vai contado, numa frase.
        """
        now = datetime.now()
        vitrine = offer("btg", "BTG Multimercado FIC FIM", "11111111000111")
        vitrine.sources = ["api-json"]
        anterior = snapshot_for("btg", [vitrine], now - timedelta(days=1),
                                strategy=CollectorStrategy.API_JSON)

        entrou_na_vitrine = offer("btg", "BTG Acoes Novo FIC FIA", "22222222000122")
        entrou_na_vitrine.sources = ["api-json"]
        so_no_catalogo = offer("btg", "BTG Carteira Fechada FIC FIM", "33333333000133")
        so_no_catalogo.sources = ["open-finance"]
        atual = snapshot_for(
            "btg",
            [offer("btg", "BTG Multimercado FIC FIM", "11111111000111"),
             entrou_na_vitrine, so_no_catalogo],
            now, strategy=CollectorStrategy.API_JSON,
        )
        atual.funds[0].sources = ["api-json"]

        report = RunReport(
            generated_at=now, snapshots=[atual],
            shelf_diffs=[diff_shelf(atual, anterior)],
        )
        markdown = render_markdown(report)

        self.assertIn("BTG Acoes Novo FIC FIA", markdown)
        self.assertNotIn("BTG Carteira Fechada FIC FIM", markdown)
        self.assertIn("so no catalogo Open Finance", markdown)
        # A tabela de sumario conta 1 entrada de vitrine, nao as 2 do diff.
        self.assertIn("| BTG Pactual | 2 | 3 | ok | 1 | 0 | 0 |", markdown)

    def test_external_names_cannot_inject_markup(self):
        """Nome de fundo vem da fonte externa: nao pode virar markup executavel."""
        payload = '<img src=x onerror=alert(1)>"</script><script>alert(2)</script>'
        snapshot = snapshot_for("btg", [
            offer("btg", payload, "11111111000191", manager_name=payload,
                  min_initial=100.0),
        ], datetime.now(), strategy=CollectorStrategy.PLAYWRIGHT)
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

        cabecalho_consolidado = [c.value for c in consolidated[1]]
        self.assertNotIn("So nesta casa (por CNPJ)", cabecalho_consolidado)

    def test_exclusividade_se_mede_na_vitrine_e_nao_no_catalogo(self):
        """Uma pasta nao pode ter duas colunas quase homonimas discordando.

        "So nesta casa (por CNPJ)" no Consolidado contava sobre o CATALOGO
        (todo fundo de varejo, inclusive o que so o Open Finance publica),
        enquanto "So nesta vitrine" contava sobre a vitrine. No BTG a diferenca
        chegava a 5.767 contra 284 - tudo fundo que ele administra e nenhum
        cliente ve. Agora a conta existe num lugar so, sobre a vitrine.
        """
        from openpyxl import load_workbook

        from shelf_monitor.report.spreadsheet import render_spreadsheet

        relatorio = self._com_vitrine()
        path = render_spreadsheet(self.config, relatorio.snapshots)
        workbook = load_workbook(path)

        # A formula tem intervalo limitado aos dados: uma referencia a coluna
        # inteira torna a planilha lenta demais para abrir.
        vitrine = workbook["Vitrine varejo"]
        coluna = [c.value for c in vitrine[1]].index("So nesta vitrine") + 1
        formula = vitrine.cell(row=2, column=coluna).value
        self.assertTrue(formula.startswith("=IF("))
        self.assertIn("COUNTIF($C$2:$C$", formula)
        self.assertNotIn("$C:$C", formula)

        # E o Resumo aponta para la, nao para o Consolidado.
        resumo = workbook["Resumo"]
        coluna_resumo = [c.value for c in resumo[4]].index("So nesta vitrine") + 1
        self.assertIn("'Vitrine varejo'!", resumo.cell(row=5, column=coluna_resumo).value)

    def test_digest_files_are_written(self):
        md_path, html_path = render_digest(self.config, self.report)
        self.assertTrue(md_path.exists() and html_path.exists())
        html = html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script", html, "digest de e-mail nao deve conter JS")

    def _com_vitrine(self):
        """Os mesmos snapshots do setUp, agora publicados na vitrine da casa.

        O setUp monta catalogo puro (Open Finance) de proposito: e o fixture do
        bloco de gaps suprimido, que precisa de uma XP sem vitrine. Quem for
        exercitar prateleira - resumo por casa, exclusivos, digest de mudanca -
        carimba a procedencia aqui, porque desde que a comparacao passou a rodar
        so na vitrine, snapshot sem canal proprio nao descreve prateleira
        nenhuma.
        """
        import copy

        def publicado(snapshot):
            clone = copy.deepcopy(snapshot)
            clone.strategy = CollectorStrategy.PLAYWRIGHT
            for fund in clone.funds:
                fund.sources = ["playwright"]
            return clone

        xp, btg = publicado(self.xp), publicado(self.btg)
        return RunReport(
            generated_at=datetime.now(),
            snapshots=[xp, btg, self.nu],
            shelf_diffs=[
                diff_shelf(xp, None),
                diff_shelf(btg, publicado(self.previous_btg)),
                diff_shelf(self.nu, None),
            ],
            cvm_snapshot=self.report.cvm_snapshot,
            cvm_diff=self.report.cvm_diff,
        )

    def _listing_report(self):
        """Report com vitrines de verdade: XP via navegador, BTG via API.

        Os snapshots do setUp nao tem sources (vitrine vazia), entao o bloco
        de gaps sai suprimido neles - este report exercita o caminho com dado.
        """
        now = datetime.now()
        xp_fund = offer("xp", "XP Macro FIC FIM", "10345678000110",
                        manager_name="XP Gestao")
        xp_fund.sources = ["playwright"]
        btg_shared = offer("btg", "XP Macro FIC FIM", "10345678000110")
        btg_shared.sources = ["api-json"]
        btg_gap = offer("btg", "Kapitalo Zeta FIC FIM", "40345678000140",
                        manager_name="Kapitalo Gestao")
        btg_gap.sources = ["api-json"]
        xp = snapshot_for("xp", [xp_fund], now)
        btg = snapshot_for("btg", [btg_shared, btg_gap], now)
        return RunReport(
            generated_at=now, snapshots=[xp, btg],
            shelf_diffs=[diff_shelf(xp, None), diff_shelf(btg, None)],
        )

    def test_dashboard_embeds_gap_block(self):
        content = render_dashboard(
            self.config, self._listing_report()
        ).read_text(encoding="utf-8")
        self.assertIn('id="gaps-section"', content)
        self.assertEqual(content.count("<script"), 2)
        start = content.index('id="dashboard-data">') + len('id="dashboard-data">')
        data = json.loads(content[start:content.index("</script>", start)])
        self.assertTrue(data["gaps"]["available"])
        self.assertEqual(data["gaps"]["total_gaps"], 1)
        self.assertEqual(data["gaps"]["funds"][0]["cnpj"], "40345678000140")

        # Sem vitrine da XP (snapshots do setUp), o bloco vem suprimido com
        # motivo - nunca com numeros.
        suppressed = render_dashboard(self.config, self.report).read_text(
            encoding="utf-8"
        )
        start = suppressed.index('id="dashboard-data">') + len('id="dashboard-data">')
        data = json.loads(suppressed[start:suppressed.index("</script>", start)])
        self.assertFalse(data["gaps"]["available"])
        self.assertIn("vitrine", data["gaps"]["reason"])

    def test_spreadsheet_gap_tabs_and_bounded_formula(self):
        from openpyxl import load_workbook

        from shelf_monitor.report.spreadsheet import render_spreadsheet

        report = self._listing_report()
        path = render_spreadsheet(self.config, report.snapshots)
        workbook = load_workbook(path)
        self.assertIn("Gaps XP", workbook.sheetnames)
        self.assertIn("Gaps por categoria", workbook.sheetnames)

        gaps = workbook["Gaps XP"]
        self.assertEqual(gaps.cell(row=2, column=1).value, "Kapitalo Zeta FIC FIM")
        # Pelo cabecalho e nao por indice fixo: a aba ganhou as colunas de
        # captacao e esforco, e um indice cravado quebra a cada coluna nova
        # sem que nada de fato tenha se perdido.
        coluna = [c.value for c in gaps[1]].index("Verificacao vs. Vitrine varejo") + 1
        formula = gaps.cell(row=2, column=coluna).value
        self.assertTrue(formula.startswith("=IF("))
        self.assertIn("COUNTIFS('Vitrine varejo'!$A$2:$A$", formula)
        self.assertNotIn("$A:$A", formula)
        self.assertNotIn("$C:$C", formula)

        # Sem vitrine da XP: a aba existe so para dizer por que nao ha numeros.
        suppressed_path = render_spreadsheet(
            self.config, [self.xp, self.btg, self.nu],
            path=self.config.reports_dir / "suprimida.xlsx",
        )
        suppressed = load_workbook(suppressed_path)
        self.assertIn("Gaps XP", suppressed.sheetnames)
        self.assertNotIn("Gaps por categoria", suppressed.sheetnames)
        self.assertIn("suprimida", suppressed["Gaps XP"].cell(row=3, column=1).value)

    def test_digest_gap_section_and_suppression(self):
        markdown = render_markdown(self._listing_report())
        self.assertIn("Gaps de prateleira da XP", markdown)
        self.assertIn("Kapitalo Zeta FIC FIM", markdown)
        self.assertIn("vitrine x vitrine", markdown)

        suppressed = render_markdown(self.report)
        self.assertIn("Gaps de prateleira da XP", suppressed)
        self.assertIn("suprimida", suppressed)
        self.assertNotIn("Kapitalo", suppressed)


if __name__ == "__main__":
    unittest.main()
