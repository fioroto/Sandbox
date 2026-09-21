"""Testes do orquestrador.

`main.py` tinha 1.564 linhas e um unico teste (`ensure_market_base`). O que
esta coberto aqui e o que quebra em silencio: a politica de codigo de saida,
de que o cron depende, e o contrato de opt-in da rentabilidade.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import argparse
import unittest
from datetime import datetime

from shelf_monitor.main import carregar_snapshots, run_outcome
from shelf_monitor.models import (
    CollectorStrategy,
    FundOffer,
    CvmSnapshot,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)

try:
    from fixtures_comuns import temp_config
except ImportError:  # pragma: no cover - depende da invocacao
    from tests.fixtures_comuns import temp_config


def snap(platform: str, status: SnapshotStatus, com_fundos: bool = False) -> ShelfSnapshot:
    """Snapshot minimo.

    `com_fundos` importa para o que vai a disco: `latest_shelf_snapshot`
    descarta por desenho um snapshot sem fundos, entao um teste de carga que
    grave snapshot vazio mede o descarte e nao o filtro.
    """
    funds = []
    if com_fundos:
        funds = [
            FundOffer(
                platform=platform,
                name=f"{platform.upper()} FUNDO",
                cnpj="12345678000191",
                sources=[CollectorStrategy.OPEN_FINANCE.value],
            )
        ]
    return ShelfSnapshot(
        platform=platform,
        collected_at=datetime(2026, 9, 21),
        status=status,
        strategy=CollectorStrategy.OPEN_FINANCE,
        funds=funds,
    )


def relatorio(*status: SnapshotStatus, cvm: SnapshotStatus | None = None) -> RunReport:
    report = RunReport(
        generated_at=datetime(2026, 9, 21),
        snapshots=[snap(f"casa{i}", st) for i, st in enumerate(status)],
    )
    if cvm is not None:
        report.cvm_snapshot = CvmSnapshot(
            collected_at=datetime(2026, 9, 21), status=cvm
        )
    return report


class TestRunOutcome(unittest.TestCase):
    """A politica de codigo de saida de que o cron depende.

    O docstring da funcao diz por que ela existe: sem isto o `run` sai 0 com as
    cinco casas bloqueadas e o job aparece verde. Nada verificava isso.
    """

    def test_tudo_coletado_sai_zero(self):
        linha, codigo = run_outcome(
            relatorio(SnapshotStatus.OK, SnapshotStatus.OK, cvm=SnapshotStatus.OK),
            cvm_requested=True,
            report_failures=[],
        )
        self.assertEqual(codigo, 0)
        self.assertIn("2 de 2 casas coletadas", linha)
        self.assertIn("CVM ok", linha)

    def test_todas_as_casas_bloqueadas_sai_um(self):
        """O caso que a funcao existe para pegar."""
        linha, codigo = run_outcome(
            relatorio(SnapshotStatus.UNAVAILABLE, SnapshotStatus.UNAVAILABLE),
            cvm_requested=True,
            report_failures=[],
        )
        self.assertEqual(codigo, 1)
        self.assertIn("0 de 2 casas coletadas", linha)

    def test_coleta_parcial_conta_como_coletada(self):
        """PARTIAL trouxe fundos - e prateleira incompleta, nao ausencia."""
        _, codigo = run_outcome(
            relatorio(SnapshotStatus.PARTIAL, SnapshotStatus.UNAVAILABLE),
            cvm_requested=True,
            report_failures=[],
        )
        self.assertEqual(codigo, 0)

    def test_cvm_caida_nao_derruba_a_execucao(self):
        """A prateleira foi entregue; o radar CVM e um bloco a mais."""
        linha, codigo = run_outcome(
            relatorio(SnapshotStatus.OK, cvm=SnapshotStatus.UNAVAILABLE),
            cvm_requested=True,
            report_failures=[],
        )
        self.assertEqual(codigo, 0)
        self.assertIn("CVM falhou", linha)

    def test_cvm_pulada_nao_e_cvm_falhou(self):
        linha, _ = run_outcome(
            relatorio(SnapshotStatus.OK), cvm_requested=False, report_failures=[]
        )
        self.assertIn("CVM pulada", linha)

    def test_relatorio_que_falhou_derruba_mesmo_com_tudo_coletado(self):
        """Coletar e nao conseguir escrever e uma execucao falha."""
        linha, codigo = run_outcome(
            relatorio(SnapshotStatus.OK, cvm=SnapshotStatus.OK),
            cvm_requested=True,
            report_failures=["planilha"],
        )
        self.assertEqual(codigo, 1)
        self.assertIn("1 relatorio(s) falharam", linha)

    def test_execucao_sem_nenhuma_casa_pedida_nao_e_falha(self):
        """`collect --platform` de uma casa que nao existe nao e erro do job."""
        _, codigo = run_outcome(relatorio(), cvm_requested=False, report_failures=[])
        self.assertEqual(codigo, 0)


class TestCarregarSnapshots(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()

    def tearDown(self):
        self.tmp.cleanup()

    def test_sem_snapshot_devolve_lista_vazia_e_nao_levanta(self):
        self.assertEqual(carregar_snapshots(self.config), [])

    def test_respeita_o_filtro_de_plataforma(self):
        from shelf_monitor.storage import save_shelf_snapshot

        for platform in ("xp", "btg"):
            save_shelf_snapshot(self.config, snap(platform, SnapshotStatus.OK, True))

        todos = carregar_snapshots(self.config)
        self.assertEqual({s.platform for s in todos}, {"xp", "btg"})

        so_xp = carregar_snapshots(self.config, ["xp"])
        self.assertEqual([s.platform for s in so_xp], ["xp"])

    def test_casa_sem_snapshot_e_pulada_e_nao_vira_none(self):
        """O laco que esta funcao substituiu fazia isso em sete lugares."""
        from shelf_monitor.storage import save_shelf_snapshot

        save_shelf_snapshot(self.config, snap("xp", SnapshotStatus.OK, True))
        saida = carregar_snapshots(self.config, ["xp", "btg"])
        self.assertEqual([s.platform for s in saida], ["xp"])


class TestContratoDaRentabilidade(unittest.TestCase):
    def test_os_tres_comandos_expoem_o_opt_in(self):
        """O contrato em `models.RunReport.rentabilidade` exige a flag.

        Ela custa ate 24 competencias do informe (~2,5 min e ~260 MB na
        primeira vez). Antes o `build_report` a calculava sempre e nao havia
        como pedir - o docstring dizia uma coisa e o codigo fazia outra.
        """
        import subprocess
        import sys

        for comando in ("run", "report", "export"):
            with self.subTest(comando=comando):
                saida = subprocess.run(
                    [sys.executable, "-m", "shelf_monitor", comando, "--help"],
                    capture_output=True, text=True, timeout=120,
                )
                self.assertIn("--with-rentabilidade", saida.stdout, comando)

    def test_build_report_nao_calcula_rentabilidade_sem_a_flag(self):
        from unittest import mock

        import shelf_monitor.main as main_mod

        with mock.patch.object(main_mod, "attach_rentabilidade") as anexar:
            main_mod.build_report(self.config, [], None)
        anexar.assert_not_called()

        with mock.patch.object(main_mod, "attach_rentabilidade") as anexar:
            main_mod.build_report(self.config, [], None, with_rentabilidade=True)
        anexar.assert_called_once()

    def setUp(self):
        self.config, self.tmp = temp_config()

    def tearDown(self):
        self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
