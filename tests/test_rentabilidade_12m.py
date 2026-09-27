"""Rentabilidade da vitrine em 12 meses, e o prazo escrito em toda saida.

Ate 2026-09-24 a rentabilidade da vitrine tinha uma janela so, de tres meses, e
o digest dizia "2026-07-01 a 2026-09-23 (61 pregões)" sem dizer que eram tres
meses nem de onde partia a conta. Em tres meses dominados pelo CDI, quase tudo
fica a centesimos da mediana da categoria. Os testes travam:

- a janela de 12 meses sobre a serie mensal: base no fechamento da competencia
  anterior, datas e pregoes certos, mediana da categoria no mesmo prazo;
- o cache da serie com os dias, e o conserto da competencia gravada sem eles;
- os pregoes pela contagem mais comum entre quem informou ate o ultimo dia;
- as duas janelas em cada superficie, cada numero com o proprio prazo, em data
  brasileira e com virgula decimal.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest import mock

from shelf_monitor.classify import KEY_CATEGORIA
from shelf_monitor.models import (
    CollectorStrategy,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.report.performance import (
    _pregoes_do_periodo,
    build_performance_mensal,
    janela_do_bloco,
)
from shelf_monitor.report.rentabilidade import MesSubclasse, SerieMensal
from shelf_monitor.report.retail_flow import DailyFlow
from shelf_monitor.texto import competencia_br, data_br, meses_br, periodo_br

from fixtures_comuns import temp_config

RF = "Renda Fixa Duração Baixa Grau de Investimento"
MESES = ["202509", "202510", "202511", "202512", "202601", "202602", "202603",
         "202604", "202605", "202606", "202607", "202608", "202609"]
# Ultimo pregao de cada competencia no cenario: o dia 28, menos a base e a corrente.
FIM = {m: date(int(m[:4]), int(m[4:]), 28) for m in MESES}
FIM["202509"] = date(2025, 9, 30)
FIM["202609"] = date(2026, 9, 23)
FUNDOS_POR_CASA = 35  # acima do minimo de 30 fundos de vitrine
RETORNO = {"xp": 10.0, "btg": 20.0}  # % em 12 meses, igual em todos os fundos da casa


def _cnpj(casa: str, i: int) -> str:
    return f"{'31' if casa == 'xp' else '32'}{i:012d}"


def _mes(cota_fim: float, yyyymm: str, pregoes: int = 21) -> MesSubclasse:
    return MesSubclasse(
        cota_ini=cota_fim, cota_fim=cota_fim, pl_fim=1_000_000.0, cotistas_fim=1000,
        pregoes=pregoes, dia_ini=date(int(yyyymm[:4]), int(yyyymm[4:]), 1),
        dia_fim=FIM[yyyymm],
    )


def _serie() -> SerieMensal:
    """Cota 100 no fechamento de 09/2025, crescendo ate 100 x (1 + retorno)."""
    dados = {}
    for casa, retorno in RETORNO.items():
        for i in range(FUNDOS_POR_CASA):
            meses = {}
            for n, m in enumerate(MESES):
                cota = 100.0 * (1 + retorno / 100 * n / (len(MESES) - 1))
                meses[m] = _mes(cota, m, pregoes=16 if m == "202609" else 21)
            dados[(_cnpj(casa, i), "")] = meses
    return SerieMensal(list(MESES), dados)


def _vitrines() -> list[ShelfSnapshot]:
    snapshots = []
    for casa in RETORNO:
        fundos = []
        for i in range(FUNDOS_POR_CASA):
            fundo = FundOffer(
                platform=casa, name=f"{casa.upper()} Fundo {i:02d}", cnpj=_cnpj(casa, i),
                sources=[CollectorStrategy.PLAYWRIGHT.value],
            )
            fundo.extra[KEY_CATEGORIA] = RF
            fundos.append(fundo)
        snapshots.append(ShelfSnapshot(
            platform=casa, collected_at=datetime(2026, 9, 24),
            status=SnapshotStatus.OK, strategy=CollectorStrategy.PLAYWRIGHT, funds=fundos,
        ))
    return snapshots


def _categorias() -> dict[str, str]:
    return {_cnpj(casa, i): RF for casa in RETORNO for i in range(FUNDOS_POR_CASA)}


class _ComConfig(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()

    def tearDown(self):
        self.tmp.cleanup()

    def _bloco(self, meses: int = 12) -> dict:
        return build_performance_mensal(
            self.config, _vitrines(), _serie(), meses=meses, categorias=_categorias()
        )


class TestJanelaDeDozeMeses(_ComConfig):
    def test_prazo_da_janela(self):
        janela = self._bloco()["janela"]
        self.assertEqual(janela["rotulo"], "12 meses")
        # Base no fechamento da competencia anterior, fim no ultimo pregao.
        self.assertEqual((janela["de"], janela["ate"]), ("2025-09-30", "2026-09-23"))
        self.assertEqual(janela["competencia_base"], "202509")
        self.assertEqual(janela["base"], "cota do fechamento de set/2025")
        self.assertEqual(janela["competencias"], MESES[1:])
        self.assertEqual(janela["pregoes"], 11 * 21 + 16)

    def test_retorno_das_casas_e_a_mediana_da_categoria_no_mesmo_prazo(self):
        bloco = self._bloco()
        casas = {c["platform_id"]: c for c in bloco["casas"]}
        self.assertAlmostEqual(casas["xp"]["por_pl"], 10.0)
        self.assertAlmostEqual(casas["btg"]["mediana"], 20.0)
        # Mediana de RF no universo: 35 fundos a 10% e 35 a 20% -> 15%. Todos os
        # da XP ficam abaixo dela, nenhum do BTG.
        self.assertEqual(casas["xp"]["alocacao_abaixo_da_mediana"]["fundos"], FUNDOS_POR_CASA)
        self.assertEqual(casas["btg"]["alocacao_abaixo_da_mediana"]["fundos"], 0)
        gente = bloco["onde_esta_a_gente"]["xp"][0]
        self.assertAlmostEqual(gente["mediana_categoria"], 15.0)
        self.assertAlmostEqual(gente["excesso"], -5.0)

    def test_sem_serie_o_bloco_sai_indisponivel_com_o_motivo(self):
        bloco = build_performance_mensal(self.config, _vitrines(), None, categorias=_categorias())
        self.assertFalse(bloco["available"])
        self.assertIn("série mensal", bloco["reason"])


class TestSerieComDatas(_ComConfig):
    def test_cache_guarda_os_dias(self):
        caminho = SerieMensal._cache_path(self.config, "202609")
        SerieMensal._gravar_cache(caminho, {("1", ""): _mes(110.0, "202609")})
        lido = SerieMensal._ler_cache(caminho)[("1", "")]
        self.assertEqual((lido.dia_ini, lido.dia_fim), (date(2026, 9, 1), date(2026, 9, 23)))

    def test_cache_antigo_sem_dias_continua_valendo_para_o_retorno(self):
        caminho = SerieMensal._cache_path(self.config, "202509")
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(
            "cnpj;subclasse;dia_ini;cota_ini;dia_fim;cota_fim;pl_fim;cotistas_fim;"
            "captacao;resgates;pregoes\n1;;;99.0;;100.0;1000.00;10;0.00;0.00;22\n",
            encoding="utf-8",
        )
        lido = SerieMensal._ler_cache(caminho)[("1", "")]
        self.assertEqual(lido.cota_fim, 100.0)
        self.assertIsNone(lido.dia_fim)

    def test_competencia_sem_dias_e_refeita_do_zip_uma_vez(self):
        sem_dias = MesSubclasse(cota_ini=99.0, cota_fim=100.0, pl_fim=1e6, pregoes=22)
        serie = SerieMensal(["202509"], {("1", ""): {"202509": sem_dias}})
        self.assertIsNone(serie.calendario("202509")[1])
        registros = [
            DailyFlow(cnpj="1", dia=date(2025, 9, d), cota=99.0 + d / 30, pl=1e6)
            for d in (1, 2, 29, 30)
        ]
        with mock.patch(
            "shelf_monitor.report.rentabilidade.ler_sem_guardar", return_value=registros
        ) as ler:
            self.assertTrue(serie.garantir_datas(self.config, "202509"))
            self.assertTrue(serie.garantir_datas(self.config, "202509"))
        ler.assert_called_once()  # a segunda ja encontra os dias
        self.assertEqual(serie.calendario("202509")[1], date(2025, 9, 30))
        cache = SerieMensal._cache_path(self.config, "202509").read_text(encoding="utf-8")
        self.assertIn("2025-09-30", cache)


class TestPregoes(unittest.TestCase):
    def test_contagem_mais_comum_entre_quem_informou_ate_o_fim(self):
        dados = {}
        # 20 fundos com 21 pregoes ate o dia 30; um que publica cota em dia sem
        # pregao (23); dois que ainda nao informaram o ultimo dia (20).
        for i in range(20):
            dados[(f"a{i}", "")] = {"202609": MesSubclasse(pregoes=21, dia_fim=date(2026, 9, 30))}
        dados[("b", "")] = {"202609": MesSubclasse(pregoes=23, dia_fim=date(2026, 9, 30))}
        for i in range(30):
            dados[(f"c{i}", "")] = {"202609": MesSubclasse(pregoes=20, dia_fim=date(2026, 9, 29))}
        serie = SerieMensal(["202609"], dados)
        self.assertEqual(serie.calendario("202609"), (None, date(2026, 9, 30), 21))

    def test_informe_diario_conta_os_dias_do_fundo_tipico(self):
        dias = [date(2026, 7, d) for d in (1, 2, 3, 6, 7)]
        registros = [DailyFlow(cnpj=str(i), dia=d) for i in range(5) for d in dias]
        # Um fundo que publica cota num sabado nao vira pregao a mais.
        registros += [DailyFlow(cnpj="x", dia=d) for d in dias + [date(2026, 7, 4)]]
        self.assertEqual(_pregoes_do_periodo(registros), 5)


class TestJanelaDeCalendario(unittest.TestCase):
    def test_competencia_faltando_nao_estica_a_janela(self):
        # Sem 202603, as "ultimas 12 que a serie tem" comecariam em 202509.
        meses = [m for m in MESES if m != "202603"]
        serie = SerieMensal(meses, {})
        self.assertEqual(serie.janela_de_calendario(12), MESES[1:])
        self.assertEqual(serie.janela("12 meses", 12), meses[-12:])


class TestBlocoAntigo(unittest.TestCase):
    def test_bloco_sem_descritor_ganha_um_a_partir_do_periodo(self):
        janela = janela_do_bloco({"periodo": {
            "de": "2026-07-01", "ate": "2026-09-22", "pregoes": 59,
            "meses_lidos": ["202607", "202608", "202609"],
        }})
        self.assertEqual(janela["rotulo"], "3 meses")
        self.assertEqual(janela["base"], "cota do primeiro pregão do período")


class TestTextoDoPrazo(unittest.TestCase):
    def test_datas_e_competencias_em_formato_brasileiro(self):
        self.assertEqual(data_br("2026-07-01"), "01/07/2026")
        self.assertEqual(data_br(date(2025, 9, 30)), "30/09/2025")
        self.assertEqual(competencia_br("202607"), "jul/2026")
        self.assertEqual(periodo_br("2025-09-30", "2026-09-23"), "de 30/09/2025 a 23/09/2026")
        self.assertEqual((meses_br(1), meses_br(12)), ("1 mês", "12 meses"))
        # O que nao e data nem competencia volta como veio.
        self.assertEqual((data_br(None), competencia_br("abc")), ("-", "abc"))


class TestSuperficies(_ComConfig):
    def _report(self) -> RunReport:
        report = RunReport(generated_at=datetime(2026, 9, 24, 10, 0), snapshots=_vitrines())
        report.performance_12m = self._bloco(12)
        report.performance = self._bloco(3)
        return report

    def test_digest_traz_as_duas_janelas_com_o_prazo(self):
        from shelf_monitor.report.digest import render_html, render_markdown

        md = render_markdown(self._report())
        self.assertIn("Prazos: **12 meses**, de 30/09/2025 a 23/09/2026", md)
        self.assertIn("**Em 12 meses** (de 30/09/2025 a 23/09/2026)", md)
        self.assertIn("**Em 3 meses** (de 28/06/2026 a 23/09/2026)", md)
        # 12 meses vem antes, e a lista de fundos atras da categoria e dele.
        self.assertLess(md.index("**Em 12 meses**"), md.index("**Em 3 meses**"))
        self.assertIn("rendendo abaixo da categoria em 12 meses", md)
        # Virgula decimal: 10% e -5pp da XP contra a mediana de 15%.
        self.assertIn("10,00% contra 15,00% da categoria (-5,00pp)", md)
        html = render_html(self._report())
        self.assertIn("Em 12 meses", html)
        self.assertIn("de 30/09/2025 a 23/09/2026", html)

    def test_planilha_tem_uma_aba_por_janela(self):
        from openpyxl import load_workbook

        from shelf_monitor.report.spreadsheet import render_spreadsheet

        report = self._report()
        wb = load_workbook(render_spreadsheet(
            self.config, report.snapshots, generated_at=report.generated_at,
            path=self.config.reports_dir / "rent.xlsx",
            performance=report.performance, performance_12m=report.performance_12m,
        ))
        self.assertIn("Rentabilidade 12 meses", wb.sheetnames)
        self.assertIn("Rentabilidade 3 meses", wb.sheetnames)
        self.assertNotIn("Rentabilidade ponderada", wb.sheetnames)
        doze = {
            linha[0].value: linha[1].value
            for linha in wb["Rentabilidade 12 meses"].iter_rows(min_row=2, max_row=8)
        }
        self.assertEqual(doze["Período"], "de 30/09/2025 a 23/09/2026")
        self.assertEqual(doze["Janela"], "12 meses, base na cota do fechamento de set/2025")
        self.assertEqual(doze["Competências"], "12 (out/2025 a set/2026)")
        cabecalho = [
            c.value for linha in wb["Onde está a gente"].iter_rows() for c in linha
            if c.value and "Rentabilidade em" in str(c.value)
        ]
        self.assertEqual(
            cabecalho, ["Rentabilidade em 12 meses (%)", "Rentabilidade em 3 meses (%)"]
        )

    def test_dashboard_leva_as_duas_janelas(self):
        from shelf_monitor.report.aggregate import build_dashboard_data
        from shelf_monitor.report.dashboard import render_dashboard

        report = self._report()
        payload = build_dashboard_data(report)
        self.assertTrue(payload["performance_12m"]["available"])
        self.assertEqual(payload["performance_12m"]["janela"]["rotulo"], "12 meses")
        self.assertNotIn("_casas", payload["performance_12m"])
        html = render_dashboard(self.config, report).read_text(encoding="utf-8")
        self.assertIn('id="perf-janelas"', html)
        self.assertIn('id="flow-prazo"', html)


if __name__ == "__main__":
    unittest.main()
