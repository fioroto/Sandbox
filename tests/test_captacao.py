"""Testes do monitoramento de captacao (CVM INF_DIARIO sobre a vitrine).

As fontes reais estao bloqueadas pela network policy deste ambiente: os testes
rodam contra `tests/fixtures/inf_diario/` (regeravel com
`python tests/fixtures/make_inf_diario_fixture.py`) e a mesma base de mercado
sintetica de `tests/fixtures/enrich/`.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import shutil
import unittest
from datetime import date, datetime
from pathlib import Path

from shelf_monitor.captacao import (
    analise_cruzada,
    calcular_janelas,
    classificar,
    consolidar_por_classificacao,
    ranking_consolidado,
    ranking_por_plataforma,
    resolver_cnpjs,
    run_captacao_pipeline,
    selecionar_universo,
)
from shelf_monitor.config import Config
from shelf_monitor.enrich import KEY_ANBIMA, KEY_CVM, MarketLookup, enrich_snapshots
from shelf_monitor.models import AssetClass, RunReport, SnapshotStatus
from shelf_monitor.storage import latest_captacao, save_captacao
from tests.test_shelf_monitor import offer, snapshot_for, temp_config

FIXTURES = Path(__file__).parent / "fixtures"
INF_DIARIO_DIR = FIXTURES / "inf_diario"
ENRICH_DIR = FIXTURES / "enrich"

REF_DATE = date(2026, 9, 11)

# CNPJs da fixture do INF_DIARIO (ver tests/fixtures/make_inf_diario_fixture.py
# para o fluxo diario de cada um).
RF_ISOLADA = "63446494000152"       # classe RF isolada, +1000/dia
SANGRIA = "11111111000111"          # classe RF isolada, -1000/dia liquido
LEGADO = "99999999000199"           # fundo legado (Acoes), +100/dia
ENVELOPE = "66666666000166"         # envelope com 2 classes (77...+300, 88...+200)
CLASSE_A = "77777777000177"
CLASSE_B = "88888888000188"
PL_ZERO = "10101010000110"          # PL inicial 0 - pct None na janela ano
SEM_FLUXO = "44444444000144"        # vitrine, ausente do informe (cobertura)


def _copy_inf_diario(config: Config) -> None:
    config.market_raw_dir.mkdir(parents=True, exist_ok=True)
    for zip_path in INF_DIARIO_DIR.glob("*.zip"):
        shutil.copy(zip_path, config.market_raw_dir / zip_path.name)


def _copy_market_base(config: Config) -> None:
    config.market_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(ENRICH_DIR / "mercado.csv", config.market_dir / "mercado.csv")
    shutil.copy(ENRICH_DIR / "subclasses.csv", config.market_dir / "subclasses.csv")


class TestCalcularJanelas(unittest.TestCase):
    def test_semana_e_a_ultima_iso_completa_antes_da_ultima_data(self):
        janelas = {j.chave: j for j in calcular_janelas(date(2026, 9, 11), None)}
        semana = janelas["semana"]
        self.assertEqual(semana.inicio, date(2026, 8, 31))
        self.assertEqual(semana.fim, date(2026, 9, 6))
        self.assertTrue(semana.completa)

    def test_mes_e_month_to_date(self):
        janelas = {j.chave: j for j in calcular_janelas(date(2026, 9, 11), None)}
        mes = janelas["mes"]
        self.assertEqual(mes.inicio, date(2026, 9, 1))
        self.assertEqual(mes.fim, date(2026, 9, 11))
        self.assertFalse(mes.completa)

    def test_mes_anterior_e_o_mes_fechado(self):
        janelas = {j.chave: j for j in calcular_janelas(date(2026, 9, 11), None)}
        anterior = janelas["mes_anterior"]
        self.assertEqual(anterior.inicio, date(2026, 8, 1))
        self.assertEqual(anterior.fim, date(2026, 8, 31))

    def test_mes_anterior_atravessa_o_ano_em_janeiro(self):
        janelas = {j.chave: j for j in calcular_janelas(date(2026, 1, 2), None)}
        anterior = janelas["mes_anterior"]
        self.assertEqual(anterior.inicio, date(2025, 12, 1))
        self.assertEqual(anterior.fim, date(2025, 12, 31))

    def test_ano_e_ytd(self):
        janelas = {j.chave: j for j in calcular_janelas(date(2026, 9, 11), None)}
        ano = janelas["ano"]
        self.assertEqual(ano.inicio, date(2026, 1, 1))
        self.assertEqual(ano.fim, date(2026, 9, 11))

    def test_janela_marca_dados_desde_quando_o_download_foi_cortado(self):
        janelas = {j.chave: j for j in calcular_janelas(date(2026, 9, 11), date(2026, 7, 1))}
        ano = janelas["ano"]
        self.assertEqual(ano.dados_desde, date(2026, 7, 1))
        semana = janelas["semana"]
        self.assertIsNone(semana.dados_desde, "semana comeca depois do corte - nao precisa de aviso")


class TestResolverCnpjs(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        _copy_market_base(self.config)
        self.lookup = MarketLookup.load(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cnpj_de_classe_resolve_para_si_mesmo(self):
        cnpjs, agregado = resolver_cnpjs(offer("btg", "Fundo", cnpj=RF_ISOLADA), self.lookup)
        self.assertEqual(cnpjs, [RF_ISOLADA])
        self.assertFalse(agregado)

    def test_cnpj_de_envelope_expande_para_as_classes(self):
        cnpjs, agregado = resolver_cnpjs(offer("btg", "Fundo Multi", cnpj=ENVELOPE), self.lookup)
        self.assertEqual(sorted(cnpjs), sorted([CLASSE_A, CLASSE_B]))
        self.assertTrue(agregado)

    def test_sem_base_de_mercado_usa_o_proprio_cnpj(self):
        cnpjs, agregado = resolver_cnpjs(offer("btg", "Fundo Multi", cnpj=ENVELOPE), None)
        self.assertEqual(cnpjs, [ENVELOPE])
        self.assertFalse(agregado, "sem base nao ha como saber que e um envelope")


class TestClassificar(unittest.TestCase):
    def test_anbima_tem_prioridade(self):
        o = offer("btg", "F", cnpj="1", extra={KEY_ANBIMA: "Renda Fixa Duracao Baixa", KEY_CVM: "Renda Fixa"})
        classificacao, nivel = classificar([o])
        self.assertEqual((classificacao, nivel), ("Renda Fixa Duracao Baixa", "anbima"))

    def test_cvm_e_fallback_da_anbima(self):
        o = offer("btg", "F", cnpj="1", extra={KEY_CVM: "Multimercado"})
        classificacao, nivel = classificar([o])
        self.assertEqual((classificacao, nivel), ("Multimercado", "cvm"))

    def test_heuristica_e_o_ultimo_recurso(self):
        o = offer("btg", "F", cnpj="1", asset_class=AssetClass.ACOES)
        classificacao, nivel = classificar([o])
        self.assertEqual((classificacao, nivel), ("Acoes", "heuristica"))

    def test_sem_classificacao_quando_nada_disponivel(self):
        o = offer("btg", "F", cnpj="1")
        classificacao, nivel = classificar([o])
        self.assertEqual((classificacao, nivel), ("Sem classificacao", "heuristica"))


class TestSelecionarUniverso(unittest.TestCase):
    def test_coleta_indisponivel_e_excluida_com_motivo(self):
        snap = snapshot_for("itau", [], datetime.now(), status=SnapshotStatus.UNAVAILABLE)
        ofertas, motivo = selecionar_universo(snap, "vitrine")
        self.assertEqual(ofertas, [])
        self.assertEqual(motivo["reason"], "coleta indisponivel nesta execucao")

    def test_sem_vitrine_e_excluida_com_motivo(self):
        catalogo = offer("nu", "Fundo Catalogo", cnpj=RF_ISOLADA, sources=["open-finance"])
        snap = snapshot_for("nu", [catalogo], datetime.now())
        ofertas, motivo = selecionar_universo(snap, "vitrine")
        self.assertEqual(ofertas, [])
        self.assertIn("sem vitrine publica", motivo["reason"])

    def test_universo_catalogo_aceita_fundos_fora_da_vitrine(self):
        catalogo = offer("nu", "Fundo Catalogo", cnpj=RF_ISOLADA, sources=["open-finance"])
        snap = snapshot_for("nu", [catalogo], datetime.now())
        ofertas, motivo = selecionar_universo(snap, "catalogo")
        self.assertIsNone(motivo)
        self.assertEqual(len(ofertas), 1)


class TestRunCaptacaoPipeline(unittest.TestCase):
    """Cenario padrao: btg e inter tem vitrine, itau esta indisponivel, nu so
    tem catalogo (Open Finance), xp tem vitrine mas nao capta os fundos-alvo -
    o que exercita o hook "ausentes da baseline"."""

    def setUp(self):
        self.config, self.tmp = temp_config()
        _copy_inf_diario(self.config)
        _copy_market_base(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def _snapshots(self):
        when = datetime(2026, 9, 11, 20, 0, 0)

        xp = snapshot_for(
            "xp",
            [offer("xp", "Fundo Legado Acoes", cnpj=LEGADO, sources=["html-scrape"])],
            when,
        )
        btg = snapshot_for(
            "btg",
            [
                offer("btg", "A1 D30 FICFIRF", cnpj=RF_ISOLADA, sources=["api-json"]),
                offer("btg", "Privilege DI", cnpj=SANGRIA, sources=["api-json"]),
                offer("btg", "Multi Classe (envelope)", cnpj=ENVELOPE, sources=["api-json"]),
                offer("btg", "Diverge Classe RF", cnpj=PL_ZERO, sources=["api-json"]),
                offer("btg", "Fundo Sem Fluxo", cnpj=SEM_FLUXO, sources=["api-json"]),
            ],
            when,
        )
        inter = snapshot_for(
            "inter",
            [
                offer("inter", "A1 D30 FICFIRF", cnpj=RF_ISOLADA, sources=["html-scrape"]),
                offer("inter", "Fundo Legado Acoes", cnpj=LEGADO, sources=["html-scrape"]),
            ],
            when,
        )
        itau = snapshot_for("itau", [], when, status=SnapshotStatus.UNAVAILABLE)
        nu = snapshot_for(
            "nu",
            [offer("nu", "Fundo Catalogo", cnpj=RF_ISOLADA, sources=["open-finance"])],
            when,
        )
        return [xp, btg, inter, itau, nu]

    def _pipeline(self, **kwargs):
        return run_captacao_pipeline(
            self.config, self._snapshots(), ref_date=REF_DATE, download=False, **kwargs
        )

    def test_plataformas_ativas_e_excluidas(self):
        res = self._pipeline()
        self.assertEqual(sorted(res.plataformas), ["btg", "inter", "xp"])
        motivos = {e["platform_id"]: e["reason"] for e in res.excluded}
        self.assertEqual(motivos["itau"], "coleta indisponivel nesta execucao")
        self.assertIn("sem vitrine publica", motivos["nu"])

    def test_fundo_isolado_semana_bate_com_dias_uteis(self):
        res = self._pipeline()
        fundo = next(f for f in res.fundos if RF_ISOLADA in f.cnpjs_classe)
        semana = fundo.janelas["semana"]
        self.assertEqual(semana.captacao_bruta, 5000.0)
        self.assertEqual(semana.resgates, 0.0)
        self.assertEqual(semana.captacao_liquida, 5000.0)
        self.assertEqual(semana.pl_inicio, 10_043_000.0)
        self.assertFalse(semana.pl_inicio_aproximado)

    def test_retificacao_nao_e_contada_em_dobro(self):
        """A linha errada de 50000 no dia 01/09 nao pode inflar o MTD."""
        res = self._pipeline()
        fundo = next(f for f in res.fundos if RF_ISOLADA in f.cnpjs_classe)
        mes = fundo.janelas["mes"]
        self.assertEqual(mes.captacao_bruta, 9000.0)

    def test_fundo_compartilhado_aparece_uma_vez_com_as_duas_plataformas(self):
        res = self._pipeline()
        candidatos = [f for f in res.fundos if RF_ISOLADA in f.cnpjs_classe]
        self.assertEqual(len(candidatos), 1, "mesmo fundo em 2 vitrines nao pode virar 2 entradas")
        self.assertEqual(sorted(candidatos[0].plataformas), ["btg", "inter"])

    def test_sangria_sai_negativa(self):
        res = self._pipeline()
        fundo = next(f for f in res.fundos if SANGRIA in f.cnpjs_classe)
        self.assertLess(fundo.janelas["semana"].captacao_liquida, 0)
        self.assertEqual(fundo.janelas["semana"].captacao_liquida, -5000.0)

    def test_envelope_soma_as_duas_classes(self):
        res = self._pipeline()
        fundo = next(f for f in res.fundos if f.cnpj == ENVELOPE)
        self.assertTrue(fundo.agregado_por_fundo)
        self.assertEqual(sorted(fundo.cnpjs_classe), sorted([CLASSE_A, CLASSE_B]))
        semana = fundo.janelas["semana"]
        self.assertEqual(semana.captacao_liquida, 2500.0)
        ano = fundo.janelas["ano"]
        self.assertAlmostEqual(ano.captacao_liquida_pct_pl, 0.3786, places=4)

    def test_pct_pl_e_none_quando_pl_de_abertura_nao_e_positivo(self):
        res = self._pipeline()
        fundo = next(f for f in res.fundos if f.cnpj == PL_ZERO)
        self.assertIsNone(fundo.janelas["ano"].captacao_liquida_pct_pl)
        self.assertIsNotNone(fundo.janelas["semana"].captacao_liquida_pct_pl)

    def test_cobertura_conta_fundos_sem_fluxo(self):
        res = self._pipeline()
        self.assertEqual(res.cobertura.total_fundos, len(res.fundos))
        self.assertLess(res.cobertura.total_com_fluxo, res.cobertura.total_fundos)
        btg_cov = res.cobertura.por_plataforma["btg"]
        self.assertEqual(btg_cov["vitrine_total"], 5)
        self.assertEqual(btg_cov["com_fluxo"], 4, "o fundo sem informe nao conta")

    def test_meses_lidos_e_faltantes(self):
        res = self._pipeline()
        self.assertIn("202609", res.cobertura.meses_lidos)
        # meses de jan a jun/2026 nao existem na fixture (so jul/ago/set).
        self.assertIn("202601", res.cobertura.meses_faltantes)

    def test_download_false_nao_derruba_a_pipeline_com_meses_ausentes(self):
        res = run_captacao_pipeline(
            self.config, self._snapshots(), ref_date=REF_DATE, download=False, months=12
        )
        self.assertTrue(res.cobertura.meses_faltantes)
        self.assertGreater(len(res.fundos), 0)

    def test_universo_catalogo_muda_o_rotulo(self):
        res = self._pipeline(universo="catalogo")
        self.assertEqual(res.universo, "catalogo")
        self.assertEqual(res.rotulo_universo, "catalogo (nao vitrine)")
        self.assertIn("nu", res.plataformas, "catalogo aceita o fundo so-Open-Finance da Nu")

    def test_roundtrip_json(self):
        res = self._pipeline()
        de_volta = type(res).from_dict(res.to_dict())
        self.assertEqual(de_volta.to_dict(), res.to_dict())

    def test_enrichment_real_estampa_classificacao_anbima(self):
        snapshots = self._snapshots()
        stats = enrich_snapshots(self.config, snapshots)
        self.assertIsNotNone(stats, "a base de mercado foi copiada; enrich nao pode falhar")
        res = run_captacao_pipeline(self.config, snapshots, ref_date=REF_DATE, download=False)
        fundo = next(f for f in res.fundos if RF_ISOLADA in f.cnpjs_classe)
        # "RF duração livre crédito livre" é o valor real da coluna
        # classificacao_anbima na fixture (tests/fixtures/enrich/mercado.csv)
        # para este CNPJ - o nivel "anbima" é o que importa aqui.
        self.assertEqual(fundo.classificacao, "RF duração livre crédito livre")
        self.assertEqual(fundo.nivel_classificacao, "anbima")

    # --- rankings e analise cruzada -----------------------------------

    def test_ranking_por_plataforma_top_e_bottom(self):
        res = self._pipeline()
        rankings = ranking_por_plataforma(res, "semana", limite=10)
        btg_top_cnpjs = [f["cnpj"] for f in rankings["btg"]["top"]]
        self.assertEqual(btg_top_cnpjs[0], RF_ISOLADA, "maior captacao liquida vem primeiro")
        btg_bottom_cnpjs = [f["cnpj"] for f in rankings["btg"]["bottom"]]
        self.assertEqual(btg_bottom_cnpjs, [SANGRIA], "bottom so lista fundos com saida liquida")

    def test_ranking_consolidado_nao_duplica_o_fundo_compartilhado(self):
        res = self._pipeline()
        consolidado = ranking_consolidado(res, "semana", limite=10)
        cnpjs_no_top = [f["cnpj"] for f in consolidado["top"]]
        self.assertEqual(cnpjs_no_top.count(RF_ISOLADA), 1)

    def test_consolidar_por_classificacao_tem_linha_todas_as_vitrines(self):
        res = self._pipeline()
        linhas = consolidar_por_classificacao(res, "semana")
        todas = [linha for linha in linhas if linha["platform_id"] == "__todas__"]
        self.assertTrue(todas)

    def test_analise_cruzada_encontra_captador_ausente_da_baseline(self):
        res = self._pipeline()
        cruzada = analise_cruzada(res, chave="semana", baseline_id="xp")
        self.assertTrue(cruzada["ausentes_na_baseline"]["available"])
        cnpjs_ausentes = [f["cnpj"] for f in cruzada["ausentes_na_baseline"]["fundos"]]
        self.assertIn(RF_ISOLADA, cnpjs_ausentes, "a XP nao capta este fundo nesta coleta")

    def test_analise_cruzada_suprime_quando_baseline_sem_vitrine(self):
        snapshots = [s for s in self._snapshots() if s.platform != "xp"]
        res = run_captacao_pipeline(self.config, snapshots, ref_date=REF_DATE, download=False)
        cruzada = analise_cruzada(res, chave="semana", baseline_id="xp")
        self.assertFalse(cruzada["ausentes_na_baseline"]["available"])

    def test_analise_cruzada_compartilhados_e_exclusivos(self):
        res = self._pipeline()
        cruzada = analise_cruzada(res, chave="semana")
        compartilhados = {f["cnpj"] for f in cruzada["compartilhados_top"]}
        exclusivos = {f["cnpj"] for f in cruzada["exclusivos_top"]}
        self.assertIn(RF_ISOLADA, compartilhados)
        self.assertIn(ENVELOPE, exclusivos)


class TestSemBaseDeMercado(unittest.TestCase):
    """Sem data/market/mercado.csv, o pipeline nao quebra - so perde cobertura."""

    def setUp(self):
        self.config, self.tmp = temp_config()
        _copy_inf_diario(self.config)
        # Deliberadamente NAO copia a base de mercado.

    def tearDown(self):
        self.tmp.cleanup()

    def test_envelope_sem_base_nao_encontra_fluxo(self):
        snap = snapshot_for(
            "btg",
            [offer("btg", "Multi Classe (envelope)", cnpj=ENVELOPE, sources=["api-json"])],
            datetime.now(),
        )
        res = run_captacao_pipeline(self.config, [snap], ref_date=REF_DATE, download=False)
        fundo = res.fundos[0]
        self.assertFalse(fundo.agregado_por_fundo)
        self.assertEqual(fundo.janelas["semana"].dias_com_dado, 0)
        self.assertFalse(res.cobertura.base_mercado)

    def test_classe_isolada_sem_base_ainda_funciona(self):
        snap = snapshot_for(
            "btg",
            [offer("btg", "A1 D30 FICFIRF", cnpj=RF_ISOLADA, sources=["api-json"])],
            datetime.now(),
        )
        res = run_captacao_pipeline(self.config, [snap], ref_date=REF_DATE, download=False)
        fundo = res.fundos[0]
        self.assertEqual(fundo.janelas["semana"].captacao_liquida, 5000.0)
        self.assertEqual(fundo.nivel_classificacao, "heuristica")


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        _copy_inf_diario(self.config)
        _copy_market_base(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load_roundtrip(self):
        snap = snapshot_for(
            "btg",
            [offer("btg", "A1 D30 FICFIRF", cnpj=RF_ISOLADA, sources=["api-json"])],
            datetime(2026, 9, 11, 20, 0, 0),
        )
        res = run_captacao_pipeline(self.config, [snap], ref_date=REF_DATE, download=False)
        path = save_captacao(self.config, res)
        self.assertTrue(path.exists())
        de_volta = latest_captacao(self.config)
        self.assertIsNotNone(de_volta)
        self.assertEqual(de_volta.to_dict(), res.to_dict())

    def test_latest_captacao_is_none_without_a_saved_result(self):
        self.assertIsNone(latest_captacao(self.config))


class TestSuperficies(unittest.TestCase):
    """As quatro superficies (dashboard, digest, planilha, deck) consomem o
    mesmo `build_captacao_block` - disponivel e suprimido."""

    def setUp(self):
        self.config, self.tmp = temp_config()
        _copy_inf_diario(self.config)
        _copy_market_base(self.config)
        when = datetime(2026, 9, 11, 20, 0, 0)
        self.snapshots = [
            snapshot_for(
                "btg",
                [offer("btg", "A1 D30 FICFIRF", cnpj=RF_ISOLADA, sources=["api-json"]),
                 offer("btg", "Privilege DI", cnpj=SANGRIA, sources=["api-json"])],
                when,
            ),
            snapshot_for(
                "xp",
                [offer("xp", "Fundo Legado Acoes", cnpj=LEGADO, sources=["html-scrape"])],
                when,
            ),
        ]
        self.resultado = run_captacao_pipeline(
            self.config, self.snapshots, ref_date=REF_DATE, download=False
        )
        self.report_com_captacao = RunReport(
            generated_at=when, snapshots=self.snapshots, captacao=self.resultado
        )
        self.report_sem_captacao = RunReport(
            generated_at=when, snapshots=self.snapshots, captacao=None
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_dashboard_data_available_and_suppressed(self):
        from shelf_monitor.report.aggregate import build_dashboard_data

        disponivel = build_dashboard_data(self.report_com_captacao)["captacao"]
        self.assertTrue(disponivel["available"])
        self.assertIn("semana", disponivel["rankings"])

        suprimido = build_dashboard_data(self.report_sem_captacao)["captacao"]
        self.assertFalse(suprimido["available"])
        self.assertIn("captacao", suprimido["reason"])

    def test_dashboard_embeds_captacao_and_stays_self_contained(self):
        import json as json_mod

        from shelf_monitor.report.dashboard import render_dashboard

        content = render_dashboard(self.config, self.report_com_captacao).read_text(encoding="utf-8")
        self.assertIn('id="captacao-section"', content)
        self.assertEqual(content.count("<script"), 2)
        start = content.index('id="dashboard-data">') + len('id="dashboard-data">')
        data = json_mod.loads(content[start:content.index("</script>", start)])
        self.assertTrue(data["captacao"]["available"])

        suprimido = render_dashboard(self.config, self.report_sem_captacao).read_text(encoding="utf-8")
        self.assertEqual(suprimido.count("<script"), 2)

    def test_digest_markdown_has_the_section_and_the_note(self):
        from shelf_monitor.captacao import NOTA_FUNDO_NIVEL
        from shelf_monitor.report.digest import render_markdown

        markdown = render_markdown(self.report_com_captacao)
        self.assertIn("Captacao liquida dos fundos das vitrines", markdown)
        self.assertIn(NOTA_FUNDO_NIVEL, markdown)

        suprimido = render_markdown(self.report_sem_captacao)
        self.assertIn("suprimida", suprimido)

    def test_digest_html_has_no_script_tags(self):
        from shelf_monitor.report.digest import render_html

        html = render_html(self.report_com_captacao)
        self.assertNotIn("<script", html)
        self.assertIn("Captacao liquida", html)

    def test_spreadsheet_has_the_four_captacao_tabs_or_just_the_suppressed_one(self):
        from openpyxl import load_workbook

        from shelf_monitor.report.spreadsheet import render_spreadsheet

        path = render_spreadsheet(self.config, self.snapshots, captacao=self.resultado)
        sheets = load_workbook(path).sheetnames
        for name in ("Captacao rankings", "Captacao ANBIMA", "Captacao cruzada", "Captacao cobertura"):
            self.assertIn(name, sheets)

        path2 = render_spreadsheet(
            self.config, self.snapshots, captacao=None, path=self.config.reports_dir / "sem_captacao.xlsx"
        )
        sheets2 = load_workbook(path2).sheetnames
        self.assertIn("Captacao rankings", sheets2)
        self.assertNotIn("Captacao ANBIMA", sheets2)

    def test_deck_slide_only_when_available(self):
        from shelf_monitor.report.deck import render_deck

        path = render_deck(self.config, self.report_com_captacao)
        from pptx import Presentation

        titles = [
            shape.text_frame.text
            for slide in Presentation(path).slides
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text
        ]
        self.assertIn("Captacao liquida dos fundos das vitrines", titles)

        path2 = render_deck(self.config, self.report_sem_captacao)
        titles2 = [
            shape.text_frame.text
            for slide in Presentation(path2).slides
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text
        ]
        self.assertNotIn("Captacao liquida dos fundos das vitrines", titles2)

    def test_captacao_report_console_and_excel(self):
        from shelf_monitor.report.captacao_report import export_captacao_excel, print_captacao_summary

        print_captacao_summary(self.resultado)  # so nao pode lancar excecao

        path = self.config.reports_dir / "captacao-standalone.xlsx"
        export_captacao_excel(self.resultado, path)
        from openpyxl import load_workbook

        sheets = load_workbook(path).sheetnames
        for name in ("Rankings", "Classificacao", "Cruzada", "Cobertura", "Metodologia"):
            self.assertIn(name, sheets)

        path_vazio = self.config.reports_dir / "captacao-vazio.xlsx"
        export_captacao_excel(None, path_vazio)
        sheets_vazio = load_workbook(path_vazio).sheetnames
        self.assertEqual(sheets_vazio, ["Rankings", "Metodologia"])


if __name__ == "__main__":
    unittest.main()
