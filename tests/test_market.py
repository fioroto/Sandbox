"""Testes da base geral do mercado.

As fontes reais estao bloqueadas neste ambiente, entao tudo roda contra as
fixtures de tests/fixtures/market/, que reproduzem o formato da CVM (latin-1,
';', decimal com ponto) e os headers reais de 2026-08.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
try:  # `discover -s tests` poe tests/ no sys.path; `-m unittest tests.X` nao.
    from fixtures_comuns import temp_config
except ImportError:  # pragma: no cover - depende de como a suite foi invocada
    from tests.fixtures_comuns import temp_config
from shelf_monitor.market import import_market, run_import
from shelf_monitor.market.fees import parse_fees, resolve_admin_fee
from shelf_monitor.market.models import FeeUnit, Origin
from shelf_monitor.market.parse import parse_date, unknown_columns

FIXTURES = Path(__file__).parent / "fixtures" / "market"




class TestDateParsing(unittest.TestCase):
    def test_accepts_the_formats_cvm_actually_publishes(self):
        self.assertEqual(parse_date("2026-08-14"), date(2026, 8, 14))
        self.assertEqual(parse_date("14/08/2026"), date(2026, 8, 14))
        self.assertEqual(parse_date("2026-08-14 00:00:00"), date(2026, 8, 14))

    def test_unreadable_date_is_empty_not_an_exception(self):
        """Uma data ruim nao pode derrubar a importacao de 30 mil fundos."""
        self.assertIsNone(parse_date("2026-13-99"))
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))


class TestAdminFeeUnit(unittest.TestCase):
    """TAXA_ADM carrega percentual e reais no mesmo campo, sem discriminador."""

    def test_percentage_is_the_common_case(self):
        value, unit, _ = resolve_admin_fee("2.00")
        self.assertEqual((value, unit), (2.0, FeeUnit.PERCENT))

    def test_currency_with_symbol(self):
        value, unit, raw = resolve_admin_fee("R$ 30.000,00")
        self.assertEqual((value, unit), (30000.0, FeeUnit.CURRENCY))
        self.assertEqual(raw, "R$ 30.000,00")

    def test_large_bare_number_is_currency_not_a_30000_percent_fee(self):
        value, unit, _ = resolve_admin_fee("30000.00")
        self.assertEqual((value, unit), (30000.0, FeeUnit.CURRENCY))

    def test_free_text_is_kept_as_text(self):
        value, unit, raw = resolve_admin_fee("conforme regulamento")
        self.assertIsNone(value)
        self.assertEqual(unit, FeeUnit.TEXT)
        self.assertEqual(raw, "conforme regulamento")

    def test_empty_is_unknown_not_zero(self):
        self.assertEqual(resolve_admin_fee("")[1], FeeUnit.UNKNOWN)
        self.assertEqual(resolve_admin_fee(None)[1], FeeUnit.UNKNOWN)

    def test_text_hint_wins_even_when_the_sentence_has_digits(self):
        """"NAO HA TAXA ATE 31/12/2026" nao pode virar R$ 31 milhoes."""
        value, unit, raw = resolve_admin_fee("NAO HA TAXA DE ADMINISTRACAO ATE 31/12/2026")
        self.assertIsNone(value)
        self.assertEqual(unit, FeeUnit.TEXT)
        self.assertIn("31/12/2026", raw)

    def test_percentage_inside_free_text_is_not_thrown_away(self):
        value, unit, _ = resolve_admin_fee("0,5% a.a.")
        self.assertEqual((value, unit), (0.5, FeeUnit.PERCENT))

    def test_thousands_separator_is_reinterpreted_when_reclassified(self):
        """"30.000" como reais vale 30 mil, nao 30."""
        self.assertEqual(resolve_admin_fee("30.000")[0], 30000.0)
        self.assertEqual(resolve_admin_fee("30000")[0], 30000.0)

    def test_three_decimal_places_stay_a_percentage(self):
        """"1.500" e 1,5% - o arquivo usa ponto decimal."""
        value, unit, _ = resolve_admin_fee("1.500")
        self.assertEqual((value, unit), (1.5, FeeUnit.PERCENT))



class TestFeesFromExtrato(unittest.TestCase):
    def setUp(self):
        self.fees = parse_fees((FIXTURES / "extrato_fi.csv").read_bytes())

    def test_masked_cnpj_matches_the_unmasked_registry(self):
        """O extrato traz mascara e o cadastro nao - causa n.1 de join vazio."""
        self.assertIn("12345678000191", self.fees)

    def test_latest_delivery_wins(self):
        """O arquivo guarda ~5 anos; vale a maior data-base de cada classe."""
        fee = self.fees["12345678000191"]
        self.assertEqual(fee.reference_date, date(2026, 8, 25))
        self.assertEqual(fee.admin_fee, 0.45)

    def test_currency_fee_is_flagged(self):
        fee = self.fees["62345678000196"]
        self.assertEqual(fee.admin_fee_unit, FeeUnit.CURRENCY)
        self.assertEqual(fee.admin_fee, 30000.0)

    def test_commercial_conditions_are_captured(self):
        fee = self.fees["42345678000194"]
        self.assertEqual(fee.min_initial, 5000.0)
        self.assertEqual(fee.redemption_payment_days, 3)
        self.assertEqual(fee.redemption_quotation_days, 1)
        self.assertEqual(fee.performance_fee, 20.0)
        self.assertEqual(fee.performance_fee_benchmark, "IPCA + 5%")
        self.assertEqual(fee.custody_fee_max, 0.05)

    def test_entry_and_exit_fees_keep_both_units(self):
        """A fonte discrimina reais e percentual em colunas separadas."""
        fee = self.fees["42345678000194"]
        self.assertEqual(fee.has_entry_fee, "S")
        self.assertEqual(fee.entry_fee_pct, 2.5)
        self.assertEqual(fee.entry_fee_amount, 0.0)
        self.assertEqual(fee.has_exit_fee, "S")
        self.assertEqual(fee.exit_fee_pct, 5.0)

    def test_not_charging_differs_from_not_informing(self):
        """O flag S/N vem em 100% das linhas; o valor, so quando o flag e S.

        Sem guardar o flag, "nao cobra" e "nao informou" ficariam iguais - e no
        arquivo real so 0,1% das classes cobram ingresso, entao o valor ausente
        e a regra, nao a excecao.
        """
        fee = self.fees["12345678000191"]
        self.assertEqual(fee.has_entry_fee, "N")
        self.assertIsNone(fee.entry_fee_pct)
        self.assertIsNone(fee.entry_fee_amount)

    def test_mandate_is_read_from_the_extrato(self):
        """Mandato declarado: o que o regulamento autoriza, nao o que carrega."""
        fee = self.fees["42345678000194"]
        self.assertEqual(fee.credit_private, "S")
        self.assertEqual(fee.credit_private_max, 50.0)
        self.assertEqual(fee.foreign, "S")
        self.assertEqual(fee.foreign_max, 20.0)
        self.assertEqual(fee.derivatives, "S")
        self.assertEqual(fee.leverage, "S")
        self.assertEqual(fee.leverage_factor, 100.0)
        self.assertEqual(fee.mirror_fund, "S")

    def test_blank_flag_is_none_not_no(self):
        """Coluna vazia nao vira "N" - a fonte deixa FUNDO_ESPELHO em branco em
        57% das linhas, e chamar isso de "nao e espelho" inventaria o dado."""
        self.assertIsNone(self.fees["62345678000196"].mirror_fund)

    def test_no_column_the_parser_reads_is_silently_absent(self):
        """A guarda que faltava quando o mapa de colunas estava errado.

        `_get` devolve None para nome ausente e `unknown_columns` so registra o
        caminho inverso, entao um nome errado sai vazio para sempre sem avisar.
        Foi o que aconteceu ate 2026-09-20 com taxa de ingresso, taxa de saida
        e dias de cotizacao: 0,0% de preenchimento nas 38.547 linhas reais,
        contra 100% em toda coluna de nome certo. Este teste falha se algum
        campo que a fixture preenche voltar a sair None.
        """
        fee = self.fees["42345678000194"]
        obrigatorios = [
            "admin_fee", "performance_fee", "performance_fee_benchmark",
            "has_entry_fee", "entry_fee_pct", "entry_fee_amount",
            "has_exit_fee", "exit_fee_pct", "exit_fee_amount",
            "custody_fee_max", "min_initial",
            "redemption_quotation_days", "redemption_payment_days",
            "target_audience", "investment_policy", "long_term_taxation",
            "credit_private", "credit_private_max", "foreign", "foreign_max",
            "derivatives", "leverage", "leverage_factor", "mirror_fund",
        ]
        vazios = [nome for nome in obrigatorios if getattr(fee, nome) is None]
        self.assertEqual(vazios, [], f"colunas lidas com nome errado: {vazios}")

    def test_without_a_date_column_the_last_delivery_wins(self):
        """Se a CVM renomear DT_COMPTC, travar na primeira linha congelaria a
        base na entrega mais antiga, em silencio."""
        raw = (
            "CNPJ_FUNDO_CLASSE;DATA_BASE_NOVA;TAXA_ADM\n"
            "12.345.678/0001-91;2026-06-30;0.50\n"
            "12.345.678/0001-91;2026-08-25;0.45\n"
        ).encode("iso-8859-1")
        self.assertEqual(parse_fees(raw)["12345678000191"].admin_fee, 0.45)

    def test_newest_wins_regardless_of_file_order(self):
        raw = (
            "CNPJ_FUNDO_CLASSE;DT_COMPTC;TAXA_ADM\n"
            "12.345.678/0001-91;2026-08-25;0.45\n"
            "12.345.678/0001-91;2026-06-30;0.50\n"
        ).encode("iso-8859-1")
        self.assertEqual(parse_fees(raw)["12345678000191"].admin_fee, 0.45)



class TestImport(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        self.db = import_market(self.config, local_dir=FIXTURES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hierarchy_is_preserved(self):
        """Um fundo pode ter varias classes - e o que a RCVM 175 permite."""
        by_fund = self.db.classes_by_fund()
        self.assertEqual(len(by_fund["1002"]), 2)
        by_class = self.db.subclasses_by_class()
        self.assertEqual(len(by_class["2001"]), 2)

    def test_referential_integrity(self):
        fund_ids = {f.registro_id for f in self.db.funds}
        class_ids = {c.classe_id for c in self.db.classes}
        for item in self.db.classes:
            self.assertIn(item.fundo_id, fund_ids)
        for item in self.db.subclasses:
            self.assertIn(item.classe_id, class_ids)

    def test_legacy_funds_are_added_not_used_as_fallback(self):
        """cad_fi cobre os NAO adaptados: e uniao, nao plano B."""
        legacy = [f for f in self.db.funds if f.origin == Origin.LEGACY]
        self.assertEqual({f.cnpj for f in legacy}, {"70345678000170", "80345678000180"})

    def test_fund_present_in_both_files_is_not_duplicated(self):
        cnpjs = [f.cnpj for f in self.db.funds]
        self.assertEqual(cnpjs.count("12345678000191"), 1)

    def test_legacy_fund_keeps_its_anbima_classification(self):
        fund = next(f for f in self.db.funds if f.cnpj == "70345678000170")
        self.assertEqual(fund.legacy_fields["anbima_classification"], "Multimercados Livre")

    def test_anbima_classification_comes_free_from_cvm(self):
        """O dado que a API da ANBIMA cobra vem no cadastro publico."""
        item = next(c for c in self.db.classes if c.classe_id == "2001")
        self.assertEqual(
            item.anbima_classification, "Renda Fixa Duracao Livre Credito Livre"
        )

    def test_unknown_column_is_reported_not_swallowed(self):
        """A CVM adiciona colunas em producao; o manifesto denuncia."""
        self.assertEqual(self.db.unknown_columns["classes"], ["Modalidade_Oferta"])

    def test_pre_operational_class_survives_without_dates(self):
        item = next(c for c in self.db.classes if c.classe_id == "2004")
        self.assertEqual(item.situation, "Fase Pre-Operacional")
        self.assertIsNone(item.net_worth)

    def test_skip_fees_leaves_the_base_usable(self):
        db = import_market(self.config, local_dir=FIXTURES, skip_fees=True)
        self.assertEqual(db.fees, {})
        self.assertTrue(db.classes)


class TestExport(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        self.db, self.paths = run_import(self.config, local_dir=FIXTURES)

    def tearDown(self):
        self.tmp.cleanup()

    def _read(self, key: str) -> list[dict]:
        with self.paths[key].open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle, delimiter=";"))

    def test_writes_one_table_per_entity(self):
        for key in ("fundos", "classes", "subclasses", "taxas", "mercado", "hierarquia"):
            self.assertTrue(self.paths[key].exists(), key)

    def test_consolidated_has_one_row_per_class_plus_legacy_funds(self):
        rows = self._read("mercado")
        self.assertEqual(len(rows), len(self.db.classes) + 2)

    def test_consolidated_carries_fee_unit_beside_the_value(self):
        rows = {r["cnpj"]: r for r in self._read("mercado")}
        subordinada = rows["62.345.678/0001-96"]
        self.assertEqual(subordinada["taxa_adm"], "30000.0")
        self.assertEqual(subordinada["taxa_adm_unidade"], "reais")

    def test_consolidated_reaches_the_manager_through_the_fund(self):
        rows = {r["cnpj"]: r for r in self._read("mercado")}
        self.assertEqual(
            rows["12.345.678/0001-91"]["gestor"],
            "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM",
        )

    def test_consolidated_carries_the_cvm_code_of_both_levels(self):
        """A classe traz o codigo dela; o do envelope vem na coluna do fundo."""
        rows = {r["cnpj"]: r for r in self._read("mercado")}
        btg = rows["12.345.678/0001-91"]
        self.assertEqual(btg["codigo_cvm"], "C555001")
        self.assertEqual(btg["codigo_cvm_fundo"], "555001")

    def test_legacy_row_carries_the_fund_code_in_the_class_column(self):
        """Sem classe, o codigo do proprio fundo ocupa a coluna - sem duplicar."""
        rows = {r["cnpj"]: r for r in self._read("mercado")}
        legacy = rows["70.345.678/0001-70"]
        self.assertEqual(legacy["codigo_cvm"], "444070")
        self.assertEqual(legacy["codigo_cvm_fundo"], "")

    def test_subclass_count_is_visible_per_class(self):
        rows = {r["cnpj"]: r for r in self._read("mercado")}
        self.assertEqual(rows["12.345.678/0001-91"]["subclasses"], "2")

    def test_legacy_fee_is_resolved_like_the_others(self):
        """A coluna nao pode misturar float parseado com texto cru do cad_fi."""
        rows = {r["cnpj"]: r for r in self._read("mercado")}
        legacy = rows["70.345.678/0001-70"]
        self.assertEqual(legacy["taxa_adm"], "1.9")
        self.assertEqual(legacy["taxa_adm_unidade"], "percentual")
        for row in rows.values():
            if row["taxa_adm"]:
                float(row["taxa_adm"])  # a coluna inteira precisa ser numerica

    def test_hierarchy_descends_to_the_subclass(self):
        """A classe unica do BTG tem duas subclasses - sao produtos distintos."""
        rows = self._read("hierarquia")
        btg = [r for r in rows if r["classe_id"] == "2001"]
        self.assertEqual(len(btg), 2)
        self.assertEqual({r["nivel"] for r in btg}, {"subclasse"})
        self.assertEqual(
            {r["subclasse_denominacao"] for r in btg},
            {"BTG DIGITAL RF - VAREJO", "BTG DIGITAL RF - PREVIDENCIA"},
        )

    def test_subclass_discriminators_are_visible(self):
        rows = {r["subclasse_denominacao"]: r for r in self._read("hierarquia")}
        self.assertEqual(rows["BTG DIGITAL RF - PREVIDENCIA"]["subclasse_previdenciario"], "S")
        self.assertEqual(rows["KINEA IPCA - INR"]["subclasse_exclusivo_inr"], "S")

    def test_class_without_subclass_is_its_own_row(self):
        """A tabela cobre o mercado todo: nao exige union de quem consome."""
        rows = {r["classe_id"]: r for r in self._read("hierarquia") if r["classe_id"]}
        self.assertEqual(rows["2003"]["nivel"], "classe")
        self.assertEqual(rows["2003"]["subclasse_id"], "")

    def test_legacy_fund_appears_at_its_own_level(self):
        rows = [r for r in self._read("hierarquia") if r["nivel"] == "fundo_legado"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["classe_id"], "")

    def test_hierarchy_inherits_the_class_fee(self):
        """Subclasses partilham CNPJ e carteira - e a mesma taxa da classe."""
        rows = [r for r in self._read("hierarquia") if r["classe_id"] == "2001"]
        self.assertEqual({r["taxa_adm"] for r in rows}, {"0.45"})

    def test_every_class_is_represented_exactly_once_per_subclass(self):
        rows = self._read("hierarquia")
        expected = sum(
            max(1, len(self.db.subclasses_by_class().get(c.classe_id, [])))
            for c in self.db.classes
        )
        legacy = sum(1 for f in self.db.funds if f.origin.value == "nao_adaptado")
        self.assertEqual(len(rows), expected + legacy)

    def test_manifest_records_counts_and_schema_drift(self):
        manifest = json.loads(self.paths["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(manifest["counts"]["classes"], 4)
        self.assertEqual(manifest["unknown_columns"]["classes"], ["Modalidade_Oferta"])

    def test_csv_opens_with_accents_in_excel(self):
        """Sem BOM, o Excel no Windows abre a acentuacao quebrada."""
        raw = self.paths["mercado"].read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))

    def test_as_duas_pernas_do_consolidado_tem_as_mesmas_colunas(self):
        """O bug que `_fee_columns` foi extraido para impedir, agora travado.

        `_consolidated_rows` monta dois dicts de ~30 chaves - um para a classe
        RCVM 175, outro para o fundo legado - e a docstring de `_fee_columns`
        registra que mante-los iguais a mao foi como `codigo_cvm` acabou numa
        perna so. A extracao cobriu 17 das chaves; as outras continuam
        escritas duas vezes, entao o invariante precisa de teste.

        Uma coluna que existe numa perna e nao na outra sai vazia para metade
        do mercado, e o DictWriter nao reclama: ele escreve o cabecalho da
        primeira linha e preenche o resto com "".
        """
        # Sobre os dicts, ANTES da escrita. Pelo CSV este teste nao prova
        # nada: o DictWriter usa o cabecalho da primeira linha e preenche a
        # chave que falta com "", e o DictReader devolve todas as chaves do
        # cabecalho para todas as linhas. A divergencia some no round-trip -
        # que e exatamente por que ela passou despercebida da primeira vez.
        from shelf_monitor.market.export import _consolidated_rows

        linhas = _consolidated_rows(self.db)
        por_origem: dict[str, list[dict]] = {}
        for linha in linhas:
            por_origem.setdefault(linha["origem"], []).append(linha)
        self.assertGreaterEqual(
            len(por_origem), 2, "a fixture precisa cobrir as duas origens"
        )

        conjuntos = {
            origem: set(grupo[0]) for origem, grupo in por_origem.items()
        }
        origens = sorted(conjuntos)
        for outra in origens[1:]:
            faltando = conjuntos[origens[0]] ^ conjuntos[outra]
            self.assertEqual(
                set(), faltando,
                f"colunas só em uma das pernas ({origens[0]} vs {outra}): {faltando}",
            )

    def test_toda_linha_da_hierarquia_declara_seu_nivel(self):
        """`nivel` e o que permite consumir a tabela sem um union.

        Os tres valores sao um contrato publico (estao na tabela do README):
        `subclasse` quando a classe tem subclasses, `classe` quando ela e a
        propria unidade, `fundo_legado` para o nao adaptado. Linha sem nivel
        obriga quem le a adivinhar o grao.
        """
        linhas = self._read("hierarquia")
        self.assertTrue(linhas)
        niveis = {linha["nivel"] for linha in linhas}
        self.assertTrue(niveis <= {"subclasse", "classe", "fundo_legado"}, niveis)
        self.assertNotIn("", niveis, "linha sem nível declarado")

    def test_rerun_is_idempotent(self):
        before = self.paths["mercado"].read_bytes()
        run_import(self.config, local_dir=FIXTURES)
        self.assertEqual(self.paths["mercado"].read_bytes(), before)


class TestUnknownColumns(unittest.TestCase):
    def test_matches_by_normalized_name(self):
        rows = [{"CNPJ_Fundo": "1", "Denominacao_Social": "X", "Coluna_Nova": "?"}]
        self.assertEqual(
            unknown_columns(rows, ("CNPJ_FUNDO", "denominacao social")),
            ["Coluna_Nova"],
        )

    def test_empty_input_reports_nothing(self):
        self.assertEqual(unknown_columns([], ("A",)), [])


class TestStreaming(unittest.TestCase):
    """O extrato real tem ~100 MB; carrega-lo inteiro custa ~10x isso em RAM."""

    def _write_extract(self, path: Path, classes: int, deliveries: int) -> float:
        import csv as csv_module

        with path.open("w", encoding="iso-8859-1", newline="") as handle:
            writer = csv_module.writer(handle, delimiter=";")
            writer.writerow(["CNPJ_FUNDO_CLASSE", "DT_COMPTC", "TAXA_ADM", "POLIT_INVEST"])
            for delivery in range(deliveries):
                for index in range(classes):
                    writer.writerow([
                        f"{index:012d}{index % 90:02d}",
                        f"202{delivery % 5 + 1}-08-25",
                        "1.50",
                        "Política de investimento com um texto razoavelmente longo",
                    ])
        return path.stat().st_size / (1024 * 1024)

    def _peak_mb(self, path: Path) -> tuple[int, float]:
        import tracemalloc

        tracemalloc.start()
        fees = parse_fees(path)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return len(fees), peak / (1024 * 1024)

    def test_memory_follows_the_result_not_the_file(self):
        """Quintuplicar o arquivo, mantendo o resultado, nao muda o pico.

        E a propriedade que separa streaming de leitura em bloco: materializar
        o CSV custa cerca de dez vezes o tamanho dele, entao o pico subiria
        junto com o arquivo.
        """
        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp) / "small.csv"
            large = Path(tmp) / "large.csv"
            small_mb = self._write_extract(small, classes=400, deliveries=40)
            large_mb = self._write_extract(large, classes=400, deliveries=200)

            small_count, small_peak = self._peak_mb(small)
            large_count, large_peak = self._peak_mb(large)

        self.assertEqual(small_count, large_count, "mesmo resultado nos dois")
        self.assertGreater(large_mb, small_mb * 4, "o arquivo grande e 5x maior")
        # Em leitura por blocos, o pico do grande seria ~5x o do pequeno.
        self.assertLess(large_peak, small_peak * 1.5)

    def test_streaming_detects_encoding_like_the_batch_reader(self):
        from shelf_monitor.market.parse import detect_encoding, iter_rows

        self.assertEqual(detect_encoding("cao".encode("utf-8")), "utf-8-sig")
        self.assertEqual(detect_encoding("ção".encode("iso-8859-1")), "iso-8859-1")

        for encoding in ("utf-8", "iso-8859-1"):
            with self.subTest(encoding=encoding):
                raw = "CNPJ;Situacao\n123;FASE PRÉ-OPERACIONAL".encode(encoding)
                rows = list(iter_rows(raw))
                self.assertEqual(rows[0]["Situacao"], "FASE PRÉ-OPERACIONAL")


if __name__ == "__main__":
    unittest.main()
