"""Fundos de previdencia fora das analises do monitor de fundos (pedido de 17/10/2026).

A previdencia tem o monitor proprio (shelf_monitor.previdencia). Os testes travam:

- a regra: rotulo ANBIMA "Previdencia ..." ou a marca no nome (Prev, FIE, VGBL,
  PGBL) - o caso real de 14/10/2026 e uma debenture incentivada que so o
  rotulo denuncia;
- a captacao e os gaps sem o fundo, cada bloco dizendo quantos tirou
  (`previdencia_fora`), e o digest dizendo isso;
- o monitor de previdencia, que analisa justamente esses fundos, com eles.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock

from shelf_monitor.classify import KEY_CATEGORIA
from shelf_monitor.config import Config
from shelf_monitor.models import CollectorStrategy, FundOffer, RunReport
from shelf_monitor.previdencia.config import ConfigPrevidencia
from shelf_monitor.previdencia_fora import (
    e_previdencia,
    nota_previdencia,
    oferta_previdencia,
    sem_previdencia,
)
from shelf_monitor.report.aggregate import build_gap_block, notas_da_cobertura
from shelf_monitor.report.retail_flow import DailyFlow, build_retail_flow

from test_captacao import _DIAS, MI, PL, RF, _cenario, _oferta

# O caso real das vitrines de 14/10/2026: debentures incentivadas que o cadastro
# classifica como previdencia - o nome nao diz.
PREV = "28000000000101"
NOME_PREV = "Plural Debentures Incentivadas Hedge 30 FIC FIF RF"
ROTULO_PREV = "Previdência RF Duração Baixa Crédito Liv"


def _prev(platform: str = "btg") -> FundOffer:
    fundo = FundOffer(
        platform=platform, name=NOME_PREV, cnpj=PREV,
        sources=[CollectorStrategy.PLAYWRIGHT.value],
    )
    fundo.extra[KEY_CATEGORIA] = ROTULO_PREV
    return fundo


class _ComPrevidencia(Config):
    """O que o monitor de previdencia faz: analisa o fundo de previdencia."""

    tira_previdencia = False


class TestRegra(unittest.TestCase):
    def test_rotulo_de_previdencia_ou_marca_no_nome(self):
        casos = [
            (NOME_PREV, ROTULO_PREV, True),
            ("XP Prev Crédito Privado FIE", "Renda Fixa Duração Livre Crédito Livre", True),
            ("Icatu Seg Previdência Multimercado", None, True),
            # "Brasilprev" nao separa a marca; o VGBL, sim.
            ("Brasilprev Top VGBL", "", True),
            ("Kinea Atacama FIRF RL", "Renda Fixa Duração Livre Crédito Livre", False),
            # "Previsao" nao e a marca.
            ("Previsão Capital FIA", "Ações Livre", False),
        ]
        for nome, categoria, esperado in casos:
            with self.subTest(nome=nome):
                self.assertEqual(e_previdencia(nome, categoria), esperado)

    def test_sem_previdencia_so_no_monitor_de_fundos(self):
        outro = _oferta("btg", "11000000000101", "Compartilhado RF", RF, "G")
        vitrines = {"xp": [_prev("xp")], "btg": [_prev(), outro]}
        limpas, fora = sem_previdencia(vitrines, Config)
        self.assertEqual(fora, 1)
        self.assertEqual(limpas, {"xp": [], "btg": [outro]})
        self.assertTrue(oferta_previdencia(_prev()))
        # O monitor de previdencia fica com eles.
        self.assertEqual(sem_previdencia(vitrines, ConfigPrevidencia), (vitrines, 0))
        self.assertIsNone(nota_previdencia(0))
        self.assertEqual(
            nota_previdencia(1),
            "1 fundo de previdência fica fora: a previdência tem o monitor próprio",
        )


def _cenario_com_previdencia():
    """O cenario da captacao com um fundo de previdencia do BTG captando bem."""
    snapshots, registros = _cenario()
    for snapshot in snapshots:
        if snapshot.platform == "btg":
            snapshot.funds.append(_prev())
    parcela = 400 * MI / len(_DIAS)
    for i, dia in enumerate(_DIAS):
        registros.append(DailyFlow(
            cnpj=PREV, dia=dia, captacao=parcela, pl=PL, cota=10.0,
            cotistas=1000 + 2000 * i,
        ))
    return snapshots, registros


def _fluxo(snapshots, registros, config: Config | None = None) -> dict:
    def ler(config, yyyymm, alvo, **_):
        return [r for r in registros if r.dia.strftime("%Y%m") == yyyymm and r.cnpj in alvo]

    with mock.patch("shelf_monitor.report.retail_flow.ler_filtrado", side_effect=ler), \
            mock.patch("shelf_monitor.report.retail_flow.ler_filtrado_sem_guardar",
                       side_effect=ler), \
            mock.patch("shelf_monitor.report.retail_flow.months_back",
                       return_value=["202608", "202609"]):
        return build_retail_flow(config or Config(), snapshots)


class TestCaptacaoEGaps(unittest.TestCase):
    def test_a_previdencia_sai_da_captacao_e_e_contada(self):
        flow = _fluxo(*_cenario_com_previdencia())
        self.assertEqual(flow["previdencia_fora"], 1)
        self.assertNotIn(PREV, {f["cnpj"] for f in flow["fundos"]})

    def test_o_monitor_de_previdencia_fica_com_ela(self):
        flow = _fluxo(*_cenario_com_previdencia(), config=_ComPrevidencia())
        self.assertEqual(flow["previdencia_fora"], 0)
        self.assertIn(PREV, {f["cnpj"] for f in flow["fundos"]})

    def test_a_previdencia_nao_e_gap_e_a_nota_diz_quantos_sairam(self):
        snapshots, _ = _cenario_com_previdencia()
        bloco = build_gap_block(snapshots)
        self.assertEqual(bloco["previdencia_fora"], 1)
        self.assertNotIn(PREV, {f["cnpj"] for f in bloco["funds"]})
        self.assertTrue(any(
            "1 fundo de previdência fica fora" in nota for nota in notas_da_cobertura(bloco)
        ))

    def test_o_digest_diz_quantos_sairam(self):
        from shelf_monitor.report.digest import render_html, render_markdown

        snapshots, registros = _cenario_com_previdencia()
        report = RunReport(
            generated_at=datetime(2026, 9, 24), snapshots=snapshots,
            retail_flow=_fluxo(snapshots, registros),
        )
        md = render_markdown(report, None, "semanal")
        # Na captacao e nos gaps.
        self.assertIn("_1 fundo de previdência fica fora: a previdência tem o monitor próprio._", md)
        self.assertIn("_Gaps: 1 fundo de previdência fica fora", md)
        self.assertNotIn(NOME_PREV, md)
        self.assertIn("1 fundo de previdência fica fora", render_html(report, None, "semanal"))


if __name__ == "__main__":
    unittest.main()
