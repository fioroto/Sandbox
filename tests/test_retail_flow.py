"""Testes da captacao de varejo dos fundos de vitrine.

Os casos sao os erros que a analise cometeu contra os dados de 07-09/2026 antes
das guardas: contar reestruturacao de veiculo como captacao, e oferecer como
oportunidade de grade o produto proprio da casa concorrente.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path

from shelf_monitor.models import AssetClass, CollectorStrategy, FundOffer, ShelfSnapshot, SnapshotStatus
from shelf_monitor.report.retail_flow import (
    DailyFlow,
    FundFlow,
    MonthlyFlow,
    build_grade_opportunities,
    classify_trend,
    consolidate,
    monthly_by_fund,
    monthly_by_platform,
    months_back,
    read_daily_flows,
)

_HEADER = (
    "TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;ID_SUBCLASSE;DT_COMPTC;VL_TOTAL;"
    "VL_QUOTA;VL_PATRIM_LIQ;CAPTC_DIA;RESG_DIA;NR_COTST"
)


def _linha(cnpj: str, dia: str, pl: float, capt: float, resg: float,
           cotistas: int, cota: float = 10.0) -> str:
    return f"FIF;{cnpj};;{dia};{pl};{cota};{pl};{capt};{resg};{cotistas}"


def _informe(directory: Path, competencia: str, linhas: list[str]) -> Path:
    path = directory / f"inf_diario_fi_{competencia}.zip"
    corpo = "\n".join([_HEADER, *linhas]) + "\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"inf_diario_fi_{competencia}.csv", corpo.encode("utf-8"))
    return path


def offer(platform: str, nome: str, cnpj: str, gestora: str | None = None) -> FundOffer:
    return FundOffer(
        platform=platform,
        name=nome,
        cnpj=cnpj,
        asset_class=AssetClass.RENDA_FIXA,
        manager_name=gestora,
        sources=[CollectorStrategy.PLAYWRIGHT.value],
    )


def snapshot(platform: str, funds: list[FundOffer]) -> ShelfSnapshot:
    return ShelfSnapshot(
        platform=platform,
        collected_at=datetime(2026, 9, 19),
        status=SnapshotStatus.OK,
        strategy=CollectorStrategy.PLAYWRIGHT,
        funds=funds,
    )


def vitrine(platform: str, quantidade: int, gestora: str) -> list[FundOffer]:
    base = {"xp": 0, "btg": 1000, "inter": 2000}.get(platform, 9000)
    return [
        offer(platform, f"{platform.upper()} {i:03d}", f"{base + i:014d}", gestora)
        for i in range(quantidade)
    ]


class TestReadDailyFlows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_le_apenas_os_cnpjs_pedidos(self):
        """O informe tem milhoes de linhas; a vitrine sao centenas de fundos."""
        path = _informe(self.dir, "202608", [
            _linha("11.111.111/0001-11", "2026-08-03", 1000.0, 50.0, 10.0, 100),
            _linha("22.222.222/0001-22", "2026-08-03", 2000.0, 80.0, 20.0, 200),
        ])
        lidos = read_daily_flows(path, {"11111111000111"})
        self.assertEqual(len(lidos), 1)
        self.assertEqual(lidos[0].cnpj, "11111111000111")
        self.assertEqual(lidos[0].captacao, 50.0)
        self.assertEqual(lidos[0].dia, date(2026, 8, 3))

    def test_zip_ausente_ou_invalido_devolve_vazio(self):
        self.assertEqual(read_daily_flows(None, {"x"}), [])
        self.assertEqual(read_daily_flows(self.dir / "nao-existe.zip", {"x"}), [])

    def test_coluna_lida_por_nome_e_nao_por_posicao(self):
        """A CVM renomeou CNPJ_FUNDO para CNPJ_FUNDO_CLASSE na v4 do informe."""
        corpo = "CNPJ_FUNDO;DT_COMPTC;VL_PATRIM_LIQ;CAPTC_DIA;RESG_DIA;NR_COTST\n"
        corpo += "11111111000111;2026-08-03;1000.0;50.0;10.0;100\n"
        path = self.dir / "inf_diario_fi_202608.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("inf_diario_fi_202608.csv", corpo.encode("utf-8"))
        lidos = read_daily_flows(path, {"11111111000111"})
        self.assertEqual(len(lidos), 1)
        self.assertEqual(lidos[0].pl, 1000.0)


class TestFundFlowGuards(unittest.TestCase):
    def _fluxo(self, capt: float, resg: float, pl_ini: float, pl_fim: float,
               cot_ini: int, cot_fim: int) -> FundFlow:
        return FundFlow(
            cnpj="11111111000111", nome="F", categoria="Renda Fixa", gestora="G",
            vitrines=["btg"], captacao_bruta=capt, resgates=resg,
            pl_inicio=pl_ini, pl_fim=pl_fim,
            cotistas_inicio=cot_ini, cotistas_fim=cot_fim,
            cota_inicio=10.0, cota_fim=10.5,
        )

    def test_captacao_maior_que_o_proprio_pl_e_estrutural(self):
        """O caso WHG: +R$ 2.464 MM num fundo de R$ 1.897 MM, perdendo cotistas.

        Dinheiro que passou pelo fundo numa troca de veiculo, nao demanda - e
        copiar isso para a grade da XP nao capturaria fluxo nenhum.
        """
        fluxo = self._fluxo(2464.0, 0.0, 100.0, 1897.0, 500, 494)
        self.assertTrue(fluxo.estrutural)
        self.assertFalse(fluxo.varejo_entrada)

    def test_fundo_esvaziado_e_estrutural_e_nao_perda_de_competitividade(self):
        """O caso Kapitalo K10: -R$ 773 MM deixando PL de R$ 4,6 MM."""
        fluxo = self._fluxo(0.0, 773.0, 800.0, 4.6, 10000, 286)
        self.assertTrue(fluxo.estrutural)
        self.assertFalse(fluxo.varejo_saida)

    def test_captacao_acompanhada_de_cotistas_e_varejo(self):
        """O caso Mapfre: +R$ 2.287 MM com +22.539 cotistas."""
        fluxo = self._fluxo(2287.0, 0.0, 16000.0, 18770.0, 100_000, 122_539)
        self.assertFalse(fluxo.estrutural)
        self.assertTrue(fluxo.varejo_entrada)
        self.assertAlmostEqual(fluxo.ticket_por_cotista, 2287.0 / 22539)

    def test_captacao_sem_cotistas_novos_nao_conta_como_varejo(self):
        """Dinheiro institucional entra sem trazer gente."""
        fluxo = self._fluxo(500.0, 0.0, 10_000.0, 10_500.0, 1000, 1000)
        self.assertFalse(fluxo.estrutural)
        self.assertFalse(fluxo.varejo_entrada)
        self.assertIsNone(fluxo.ticket_por_cotista)

    def test_perda_com_saida_de_cotistas_e_varejo(self):
        fluxo = self._fluxo(0.0, 443.0, 1900.0, 1510.0, 8000, 6875)
        self.assertFalse(fluxo.estrutural)
        self.assertTrue(fluxo.varejo_saida)


class TestConsolidate(unittest.TestCase):
    def test_soma_o_periodo_e_usa_as_pontas_para_estoque(self):
        registros = [
            DailyFlow("11111111000111", date(2026, 8, 3), 100.0, 20.0, 1000.0, 10.0, 500),
            DailyFlow("11111111000111", date(2026, 8, 4), 60.0, 10.0, 1140.0, 10.2, 540),
        ]
        catalogo = {"11111111000111": offer("btg", "FUNDO", "11111111000111", "G")}
        fluxos = consolidate(registros, catalogo, {"11111111000111": ["btg", "xp"]})

        self.assertEqual(len(fluxos), 1)
        fluxo = fluxos[0]
        # Fluxo soma o periodo; PL e cotistas sao estoque, vem das pontas.
        self.assertEqual(fluxo.captacao_bruta, 160.0)
        self.assertEqual(fluxo.resgates, 30.0)
        self.assertEqual(fluxo.captacao_liquida, 130.0)
        self.assertEqual(fluxo.pl_inicio, 1000.0)
        self.assertEqual(fluxo.pl_fim, 1140.0)
        self.assertEqual(fluxo.variacao_cotistas, 40)
        self.assertFalse(fluxo.exclusivo)

    def test_fundo_fora_do_catalogo_de_vitrine_nao_entra(self):
        registros = [DailyFlow("99999999000199", date(2026, 8, 3), 10.0, 0.0, 50.0, 1.0, 5)]
        self.assertEqual(consolidate(registros, {}, {}), [])


class TestGradeOpportunities(unittest.TestCase):
    def _fluxo(self, cnpj: str, nome: str, capt: float, cotistas: int,
               vitrines: list[str], gestora: str) -> FundFlow:
        return FundFlow(
            cnpj=cnpj, nome=nome, categoria="Renda Fixa", gestora=gestora,
            vitrines=vitrines, captacao_bruta=capt, resgates=0.0,
            pl_inicio=100_000_000.0, pl_fim=100_000_000.0 + capt,
            cotistas_inicio=1000, cotistas_fim=1000 + cotistas,
            cota_inicio=10.0, cota_fim=10.1,
        )

    def test_produto_proprio_do_concorrente_sai_dos_transferiveis(self):
        """O caso BTG Tesouro Selic: +R$ 1.862 MM, mas e a asset do BTG.

        A XP distribuir o produto proprio do BTG e uma conversa comercial e
        estrategica, nao uma inclusao de grade - ficar na mesma lista que um
        fundo independente inflava a oportunidade em bilhoes.
        """
        snapshots = [
            snapshot("xp", vitrine("xp", 40, "GESTORA INDEPENDENTE")),
            snapshot("btg", vitrine("btg", 40, "GESTORA INDEPENDENTE") + [
                offer("btg", "BTG TESOURO SELIC", "55555555000155",
                      "BTG PACTUAL ASSET MANAGEMENT S/A DTVM"),
                offer("btg", "FUNDO INDEPENDENTE", "66666666000166",
                      "GESTORA INDEPENDENTE"),
            ]),
        ]
        fluxos = [
            self._fluxo("55555555000155", "BTG TESOURO SELIC", 1_862_000_000.0, 3512,
                        ["btg"], "BTG PACTUAL ASSET MANAGEMENT S/A DTVM"),
            self._fluxo("66666666000166", "FUNDO INDEPENDENTE", 50_000_000.0, 900,
                        ["btg"], "GESTORA INDEPENDENTE"),
        ]
        resultado = build_grade_opportunities(snapshots, fluxos)

        self.assertEqual(resultado["total"], 2)
        self.assertEqual(resultado["total_produto_do_concorrente"], 1)
        self.assertEqual(resultado["total_transferiveis"], 1)
        self.assertEqual(resultado["captacao_transferivel"], 50_000_000.0)
        nomes = [o["nome"] for o in resultado["operacionais"]]
        self.assertIn("FUNDO INDEPENDENTE", nomes)
        self.assertNotIn("BTG TESOURO SELIC", nomes)

    def test_gestora_ja_distribuida_separa_operacional_de_comercial(self):
        snapshots = [
            snapshot("xp", vitrine("xp", 40, "GESTORA CONHECIDA")),
            snapshot("btg", vitrine("btg", 40, "GESTORA NOVA")),
        ]
        fluxos = [
            self._fluxo("77777777000177", "DA CONHECIDA", 30_000_000.0, 800,
                        ["btg"], "GESTORA CONHECIDA"),
            self._fluxo("88888888000188", "DA NOVA", 20_000_000.0, 600,
                        ["btg"], "GESTORA NOVA"),
        ]
        resultado = build_grade_opportunities(snapshots, fluxos)
        self.assertEqual(resultado["total_operacionais"], 1)
        self.assertEqual(resultado["total_comerciais"], 1)
        self.assertEqual(resultado["operacionais"][0]["nome"], "DA CONHECIDA")

    def test_fundo_estrutural_nao_vira_oportunidade(self):
        snapshots = [
            snapshot("xp", vitrine("xp", 40, "GESTORA XP")),
            snapshot("btg", vitrine("btg", 40, "GESTORA BTG")),
        ]
        migracao = FundFlow(
            cnpj="99999999000199", nome="VEICULO EM MIGRACAO", categoria="Renda Fixa",
            gestora="GESTORA BTG", vitrines=["btg"],
            captacao_bruta=2_464_000_000.0, resgates=0.0,
            pl_inicio=10_000_000.0, pl_fim=1_897_000_000.0,
            cotistas_inicio=500, cotistas_fim=494,
            cota_inicio=10.0, cota_fim=10.1,
        )
        resultado = build_grade_opportunities(snapshots, [migracao])
        self.assertFalse(resultado["available"])
        self.assertEqual(resultado["total"], 0)

    def test_sem_vitrine_da_xp_o_bloco_e_suprimido(self):
        resultado = build_grade_opportunities(
            [snapshot("btg", vitrine("btg", 40, "G"))], []
        )
        self.assertFalse(resultado["available"])
        self.assertIn("indisponivel", resultado["reason"])


class TestTrend(unittest.TestCase):
    def _mes(self, competencia: str, liquida: float, dias: int) -> MonthlyFlow:
        return MonthlyFlow(competencia=competencia, captacao_liquida=liquida, dias=dias)

    def test_mes_parcial_nao_vira_desaceleracao_por_calendario(self):
        """O caso real: setembro fecha com 12 pregoes contra 21-23 dos meses
        cheios. Pelo TOTAL, um fundo com o mesmo ritmo diario pareceria estar
        desacelerando - a conclusao seria sobre o calendario, nao sobre o fundo.
        """
        meses = [
            self._mes("202607", 230.0, 23),
            self._mes("202608", 210.0, 21),
            self._mes("202609", 120.0, 12),  # mesmos R$ 10/dia
        ]
        self.assertEqual(classify_trend(meses), "estavel")

    def test_acelerando_compara_media_diaria(self):
        meses = [
            self._mes("202607", 23.0, 23),   # 1/dia
            self._mes("202608", 21.0, 21),   # 1/dia
            self._mes("202609", 60.0, 12),   # 5/dia
        ]
        self.assertEqual(classify_trend(meses), "acelerando")

    def test_desacelerando(self):
        meses = [self._mes("202607", 230.0, 23), self._mes("202608", 21.0, 21)]
        self.assertEqual(classify_trend(meses), "desacelerando")

    def test_virou_saida_e_virou_entrada(self):
        self.assertEqual(
            classify_trend([self._mes("202607", 230.0, 23), self._mes("202608", -50.0, 21)]),
            "virou_saida",
        )
        self.assertEqual(
            classify_trend([self._mes("202607", -230.0, 23), self._mes("202608", 50.0, 21)]),
            "virou_entrada",
        )

    def test_saida_persistente(self):
        self.assertEqual(
            classify_trend([self._mes("202607", -10.0, 23), self._mes("202608", -20.0, 21)]),
            "saida_persistente",
        )

    def test_uma_competencia_so_nao_e_tendencia(self):
        self.assertEqual(classify_trend([self._mes("202609", 100.0, 12)]), "sem_serie")
        self.assertEqual(classify_trend([]), "sem_serie")


class TestMonthlySeries(unittest.TestCase):
    def _registros(self) -> list[DailyFlow]:
        return [
            DailyFlow("11111111000111", date(2026, 7, 1), 100.0, 0.0, 1000.0, 10.0, 500),
            DailyFlow("11111111000111", date(2026, 7, 2), 50.0, 20.0, 1130.0, 10.1, 520),
            DailyFlow("11111111000111", date(2026, 8, 3), 200.0, 0.0, 1330.0, 10.2, 560),
        ]

    def test_quebra_por_competencia_conta_dias_distintos(self):
        series = monthly_by_fund(self._registros())["11111111000111"]
        self.assertEqual([m.competencia for m in series], ["202607", "202608"])
        julho, agosto = series
        self.assertEqual(julho.captacao_liquida, 130.0)
        self.assertEqual(julho.dias, 2)
        self.assertEqual(julho.media_diaria, 65.0)
        self.assertEqual(julho.variacao_cotistas, 20)
        self.assertEqual(agosto.captacao_liquida, 200.0)

    def test_serie_por_casa_ignora_movimento_estrutural(self):
        """Um veiculo em migracao movia o total da vitrine em bilhoes e
        enterrava o sinal de varejo."""
        registros = self._registros() + [
            DailyFlow("99999999000199", date(2026, 7, 1), 5000.0, 0.0, 10.0, 1.0, 5),
        ]
        presenca = {"11111111000111": ["btg"], "99999999000199": ["btg"]}
        series = monthly_by_platform(registros, presenca, estruturais={"99999999000199"})
        julho = next(m for m in series["btg"] if m.competencia == "202607")
        self.assertEqual(julho.captacao_liquida, 130.0)

    def test_fundo_em_duas_vitrines_entra_nas_duas_series(self):
        """A CVM publica fluxo por fundo: a soma de cada casa o inclui inteiro."""
        presenca = {"11111111000111": ["btg", "xp"]}
        series = monthly_by_platform(self._registros(), presenca, estruturais=set())
        self.assertEqual(
            next(m for m in series["btg"] if m.competencia == "202607").captacao_liquida,
            next(m for m in series["xp"] if m.competencia == "202607").captacao_liquida,
        )


class TestFundFlowTrend(unittest.TestCase):
    def test_consolidate_anexa_a_serie_e_a_tendencia(self):
        registros = [
            DailyFlow("11111111000111", date(2026, 7, 1), 10.0, 0.0, 100_000_000.0, 10.0, 500),
            DailyFlow("11111111000111", date(2026, 8, 3), 100.0, 0.0, 100_000_100.0, 10.1, 560),
        ]
        catalogo = {"11111111000111": offer("btg", "FUNDO", "11111111000111", "G")}
        fluxo = consolidate(registros, catalogo, {"11111111000111": ["btg"]})[0]
        self.assertEqual([m.competencia for m in fluxo.meses], ["202607", "202608"])
        self.assertEqual(fluxo.tendencia, "acelerando")
        self.assertIn("tendencia", fluxo.to_dict())


class TestMonthsBack(unittest.TestCase):
    def test_vira_o_ano_para_tras(self):
        self.assertEqual(
            months_back(3, date(2026, 2, 10)), ["202512", "202601", "202602"]
        )


if __name__ == "__main__":
    unittest.main()
