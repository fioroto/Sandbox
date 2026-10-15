"""Vitrine de reserva: o Itau nao sai de nenhuma analise por falha de coleta.

Pedido em 14/10/2026 ("sempre retorne Itau em todas as analises"). Em
28/09/2026 a tabela do ion nao carregou e o Itau sumiu dos gaps, da captacao e
da rentabilidade. Os testes travam:

- o que e vitrine utilizavel (a que os blocos que cruzam casas aceitam) e o
  motivo de cada uma que nao e;
- a coleta sem vitrine utilizavel entra nas analises com a ultima valida, de
  ate 30 dias, marcada e com a data dita - so para as casas configuradas;
- o arquivo gravado e o diff continuam sendo os da coleta do dia, e a linha
  final da execucao nao conta a casa como coletada;
- o digest diz de que dia e a vitrine e nao inventa movimento.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock

from shelf_monitor.models import CollectorStrategy, FundOffer, ShelfSnapshot, SnapshotStatus
from shelf_monitor.reserva import (
    IDADE_MAXIMA_DA_RESERVA_DIAS,
    com_vitrine_de_reserva,
    problema_da_vitrine,
)
from shelf_monitor.storage import latest_shelf_snapshot, save_shelf_snapshot

from fixtures_comuns import base_cnpj, temp_config

HOJE = datetime(2026, 10, 14, 8, 0)
VITRINE = CollectorStrategy.PLAYWRIGHT.value
CATALOGO = CollectorStrategy.OPEN_FINANCE.value


def _fundos(platform: str, n: int, fonte: str, com_cnpj: bool = True) -> list[FundOffer]:
    fundos = []
    for i in range(n):
        fundo = FundOffer(
            platform=platform, name=f"{platform.upper()} Fundo {i:03d} FIC FIM",
            cnpj=f"{base_cnpj(platform) + i:014d}" if com_cnpj else None,
        )
        fundo.sources = [fonte]
        fundos.append(fundo)
    return fundos


def _snap(platform: str, fundos: list[FundOffer], quando: datetime,
          status: SnapshotStatus = SnapshotStatus.OK, error: str | None = None) -> ShelfSnapshot:
    return ShelfSnapshot(
        platform=platform, collected_at=quando, strategy=CollectorStrategy.PLAYWRIGHT,
        status=status, funds=fundos, error=error,
    )


def _utilizavel(platform: str, quando: datetime) -> ShelfSnapshot:
    return _snap(platform, _fundos(platform, 40, VITRINE), quando)


def _so_catalogo(platform: str, quando: datetime) -> ShelfSnapshot:
    return _snap(platform, _fundos(platform, 40, CATALOGO), quando, SnapshotStatus.PARTIAL)


class TestVitrineUtilizavel(unittest.TestCase):
    def test_o_que_serve_e_o_motivo_do_que_nao_serve(self):
        self.assertIsNone(problema_da_vitrine(_utilizavel("itau", HOJE)))
        fora = _snap("itau", [], HOJE, SnapshotStatus.UNAVAILABLE, "tabela não carregou")
        self.assertEqual(problema_da_vitrine(fora), "coleta indisponível (tabela não carregou)")
        self.assertEqual(
            problema_da_vitrine(_so_catalogo("itau", HOJE)),
            "sem vitrine coletada, só o catálogo Open Finance",
        )
        sem_cnpj = _snap("itau", _fundos("itau", 40, VITRINE, com_cnpj=False), HOJE)
        self.assertEqual(problema_da_vitrine(sem_cnpj), "CNPJ em só 0% da vitrine")
        pequena = _snap("itau", _fundos("itau", 5, VITRINE), HOJE)
        self.assertIn("abaixo do mínimo", problema_da_vitrine(pequena))


class TestReserva(unittest.TestCase):
    def setUp(self):
        self.config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)

    def test_coleta_que_falha_entra_com_a_ultima_vitrine_valida(self):
        save_shelf_snapshot(self.config, _utilizavel("itau", datetime(2026, 9, 25, 8, 0)))
        # A de 28/09 tambem falhou: nao serve de reserva.
        save_shelf_snapshot(self.config, _so_catalogo("itau", datetime(2026, 9, 28, 8, 0)))
        xp, itau = _utilizavel("xp", HOJE), _so_catalogo("itau", HOJE)

        analise, substituidos = com_vitrine_de_reserva(self.config, [xp, itau])

        self.assertIs(analise[0], xp)
        self.assertEqual(substituidos, [itau])
        reserva = analise[1]
        self.assertEqual(reserva.snapshot_date.isoformat(), "2026-09-25")
        self.assertEqual(
            reserva.reserva,
            "vitrine de reserva: a coleta de 14/10/2026 não trouxe vitrine utilizável "
            "(sem vitrine coletada, só o catálogo Open Finance); as análises usam a de "
            "25/09/2026, a última válida",
        )
        # Planilha, deck e dashboard ja mostram o `error` do snapshot.
        self.assertEqual(reserva.error, reserva.reserva)
        # O arquivo gravado continua sem a marca: ela so existe no relatorio.
        self.assertNotIn("reserva", reserva.to_dict())
        gravado = latest_shelf_snapshot(self.config, "itau")
        self.assertIsNone(gravado.reserva)

    def test_reserva_velha_demais_nao_entra(self):
        antiga = HOJE - timedelta(days=IDADE_MAXIMA_DA_RESERVA_DIAS + 1)
        save_shelf_snapshot(self.config, _utilizavel("itau", antiga))
        itau = _so_catalogo("itau", HOJE)
        analise, substituidos = com_vitrine_de_reserva(self.config, [itau])
        self.assertIs(analise[0], itau)
        self.assertEqual(substituidos, [])

    def test_vitrine_sem_cnpj_e_identificada_na_hora_do_relatorio(self):
        """O Itau Private guardado antes de 14/10/2026: sem CNPJ, com a cota."""
        from shelf_monitor.config import Config

        class _ConfigQueIdentifica(Config):
            def identificar_vitrine(self, snapshot):
                for i, fundo in enumerate(snapshot.funds):
                    fundo.cnpj = f"{90000 + i:014d}"
                return "CNPJ pela cota"

        config = _ConfigQueIdentifica(data_dir=str(self.config.data_dir))
        sem_cnpj = _snap("itau", _fundos("itau", 40, VITRINE, com_cnpj=False),
                         datetime(2026, 9, 25, 8, 0))
        save_shelf_snapshot(config, sem_cnpj)
        fora = _snap("itau", [], HOJE, SnapshotStatus.UNAVAILABLE, "página fora do ar")

        analise, substituidos = com_vitrine_de_reserva(config, [fora])

        self.assertEqual(substituidos, [fora])
        self.assertEqual(analise[0].snapshot_date.isoformat(), "2026-09-25")
        self.assertIn("com o CNPJ identificado na hora do relatório", analise[0].reserva)
        self.assertTrue(all(f.cnpj for f in analise[0].funds))
        # O arquivo de 25/09 continua sem CNPJ: so o relatorio identificou.
        self.assertFalse(any(f.cnpj for f in latest_shelf_snapshot(config, "itau").funds))
        # Sem o gancho (o Config de fundos), a mesma vitrine nao serve.
        analise, substituidos = com_vitrine_de_reserva(self.config, [fora])
        self.assertIs(analise[0], fora)

    def test_vitrine_do_dia_sem_cnpj_e_identificada_antes_da_reserva(self):
        from shelf_monitor.config import Config

        class _ConfigQueIdentifica(Config):
            def identificar_vitrine(self, snapshot):
                for i, fundo in enumerate(snapshot.funds):
                    fundo.cnpj = f"{90000 + i:014d}"
                return "CNPJ pela cota"

        config = _ConfigQueIdentifica(data_dir=str(self.config.data_dir))
        hoje = _snap("itau", _fundos("itau", 40, VITRINE, com_cnpj=False), HOJE)

        analise, substituidos = com_vitrine_de_reserva(config, [hoje])

        # A propria vitrine do dia, identificada: nao e reserva.
        self.assertEqual(analise[0].snapshot_date, hoje.snapshot_date)
        self.assertIsNone(analise[0].reserva)
        self.assertTrue(all(f.cnpj for f in analise[0].funds))
        # A da coleta fica como veio - e sobre ela que o diff roda.
        self.assertEqual(substituidos, [hoje])
        self.assertFalse(any(f.cnpj for f in hoje.funds))

    def test_so_as_casas_configuradas_e_so_quando_falta(self):
        save_shelf_snapshot(self.config, _utilizavel("btg", datetime(2026, 10, 7, 8, 0)))
        save_shelf_snapshot(self.config, _utilizavel("itau", datetime(2026, 10, 7, 8, 0)))
        btg, itau = _so_catalogo("btg", HOJE), _utilizavel("itau", HOJE)
        # O BTG nao esta em `vitrine_de_reserva`; o Itau de hoje serve.
        analise, substituidos = com_vitrine_de_reserva(self.config, [btg, itau])
        self.assertEqual(analise, [btg, itau])
        self.assertEqual(substituidos, [])


class TestReservaNoRelatorio(unittest.TestCase):
    def setUp(self):
        self.config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        save_shelf_snapshot(self.config, _utilizavel("xp", datetime(2026, 10, 7, 8, 0)))
        save_shelf_snapshot(self.config, _utilizavel("itau", datetime(2026, 10, 7, 8, 0)))

    def _relatorio(self):
        from shelf_monitor import main as main_mod

        # Os blocos que leem o informe diario da CVM ficam fora: aqui nao ha
        # rede nem base, e o que se testa e a orquestracao.
        with mock.patch.object(main_mod, "attach_retail_flow"), \
                mock.patch.object(main_mod, "attach_performance"), \
                mock.patch.object(main_mod, "attach_custo_e_mandato"):
            return main_mod.build_report(
                self.config,
                [_utilizavel("xp", HOJE), _so_catalogo("itau", HOJE)],
                None,
            )

    def test_analises_usam_a_reserva_e_o_diff_e_o_da_coleta(self):
        from shelf_monitor.main import run_outcome
        from shelf_monitor.report.aggregate import bloco_de_gaps

        report = self._relatorio()
        itau = report.snapshot_for("itau")
        self.assertIsNotNone(itau.reserva)
        self.assertEqual(itau.snapshot_date.isoformat(), "2026-10-07")
        # O diff e o da coleta de hoje contra a ultima confiavel.
        self.assertEqual(report.diff_for("itau").curr_date.isoformat(), "2026-10-14")
        # O Itau entra nos gaps como concorrente, pela vitrine de reserva.
        gaps = bloco_de_gaps(report)
        self.assertEqual([c["platform_id"] for c in gaps["competitors"]], ["itau"])
        # A coleta do dia falhou: a casa nao conta como coletada.
        linha, _ = run_outcome(report, False, [])
        self.assertTrue(linha.startswith("1 de 2 casas coletadas"))

    def test_digest_diz_de_que_dia_e_a_vitrine_e_nao_inventa_movimento(self):
        from shelf_monitor.report.digest import render_html, render_markdown

        report = self._relatorio()
        md = render_markdown(report, None, "semanal")
        self.assertIn("as análises usam a de 07/10/2026, a última válida", md)
        self.assertIn("Entradas e saídas não medidas nesta execução", md)
        self.assertIn("nas vitrines de Itaú (íon)", md)
        html = render_html(report, None, "semanal")
        self.assertIn("as análises usam a de 07/10/2026, a última válida", html)
        self.assertIn("entradas e saídas não medidas", html)


if __name__ == "__main__":
    unittest.main()
