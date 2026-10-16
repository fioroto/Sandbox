"""Fundos caixa/simples fora das analises (pedido de 16/10/2026).

O fundo caixa e o simples sao produto padrao - toda casa tem o seu - e enchiam
o topo das captacoes. Os testes travam:

- a regra (versao "nucleo"): renda fixa com rotulo ANBIMA de caixa ou com a
  marca no nome (Simples, Selic, Referenciado DI); o rotulo sozinho nao basta,
  e o CDB e o credito bancario ficam dentro;
- a captacao, os gaps, a rentabilidade e o custo sem o fundo, cada bloco
  dizendo quantos tirou (`caixa_fora`);
- o digest dizendo isso nas secoes.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock

from shelf_monitor.caixa import e_caixa, nota_caixa, oferta_caixa, sem_caixa
from shelf_monitor.classify import KEY_CATEGORIA
from shelf_monitor.config import Config
from shelf_monitor.models import CollectorStrategy, FundOffer, RunReport
from shelf_monitor.report.aggregate import build_gap_block, notas_da_cobertura
from shelf_monitor.report.custo import IndiceCusto, build_custo
from shelf_monitor.report.performance import build_performance_mensal
from shelf_monitor.report.retail_flow import DailyFlow, build_retail_flow

from fixtures_comuns import temp_config
from test_captacao import _DIAS, MI, PL, RF, _cenario, _oferta
from test_custo import classe, universo
from test_rentabilidade_12m import FUNDOS_POR_CASA, _categorias, _serie, _vitrines

# O caso real: o BTGP Tesouro Selic esta como "Duracao Livre Grau de
# Investimento" no cadastro - so o nome o denuncia.
SELIC = "29000000000101"
NOME_SELIC = "BTG Tesouro Selic FI Renda Fixa Referenciado"
ROTULO_SELIC = "Renda Fixa Duração Livre Grau de Investimento"


def _selic(platform: str = "btg") -> FundOffer:
    fundo = FundOffer(
        platform=platform, name=NOME_SELIC, cnpj=SELIC,
        sources=[CollectorStrategy.PLAYWRIGHT.value],
    )
    fundo.extra[KEY_CATEGORIA] = ROTULO_SELIC
    return fundo


class TestRegra(unittest.TestCase):
    def test_rotulo_de_caixa_ou_marca_no_nome_dentro_da_renda_fixa(self):
        casos = [
            (NOME_SELIC, ROTULO_SELIC, True),
            ("Itaú Privilège RF Referenciado DI", "Renda Fixa Duração Baixa Grau de Invest.", True),
            ("Bradesco Bancos II FIF CIC RF CP RL", "Renda Fixa Duração Baixa Grau de Invest.", True),
            ("Mapfre Confianza FIF RF Ref. DI CP RL", "Renda Fixa Duração Baixa Crédito Livre", True),
            ("BTG Digital Tesouro Selic Simples FI", "Renda Fixa", True),
            ("XP Renda Fixa Simples", "Renda Fixa Simples", True),
            # A versao escolhida e o nucleo: CDB e credito bancario ficam dentro.
            ("BTG Pactual CDB Plus FI RF CP", ROTULO_SELIC, False),
            ("BTG Pactual Crédito Bancário FIRF", "Renda Fixa Duração Alta Crédito Livre", False),
            ("Kinea Atacama FIRF RL", "Renda Fixa Duração Livre Crédito Livre", False),
            # A marca so vale em renda fixa.
            ("Selic Ações FIA", "Ações Livre", False),
        ]
        for nome, categoria, esperado in casos:
            with self.subTest(nome=nome):
                self.assertEqual(e_caixa(nome, categoria), esperado)

    def test_sem_caixa_tira_e_conta_cada_fundo_uma_vez(self):
        outro = _oferta("btg", "11000000000101", "Compartilhado RF", RF, "G")
        vitrines, fora = sem_caixa({"xp": [_selic("xp")], "btg": [_selic(), outro]})
        self.assertEqual(fora, 1)
        self.assertEqual(vitrines, {"xp": [], "btg": [outro]})
        self.assertTrue(oferta_caixa(_selic()))
        self.assertIsNone(nota_caixa(0))
        self.assertEqual(
            nota_caixa(2),
            "2 fundos caixa/simples ficam fora (Renda Fixa Simples, Selic e DI): "
            "produto padrão, que toda casa tem",
        )


def _cenario_com_selic():
    """O cenario da captacao com o Tesouro Selic do BTG captando mais que todos."""
    snapshots, registros = _cenario()
    for snapshot in snapshots:
        if snapshot.platform == "btg":
            snapshot.funds.append(_selic())
    parcela = 900 * MI / len(_DIAS)
    for i, dia in enumerate(_DIAS):
        registros.append(DailyFlow(
            cnpj=SELIC, dia=dia, captacao=parcela, pl=PL, cota=10.0,
            cotistas=1000 + 3000 * i,
        ))
    return snapshots, registros


def _fluxo(snapshots, registros) -> dict:
    def ler(config, yyyymm, alvo, **_):
        return [r for r in registros if r.dia.strftime("%Y%m") == yyyymm and r.cnpj in alvo]

    with mock.patch("shelf_monitor.report.retail_flow.ler_filtrado", side_effect=ler), \
            mock.patch("shelf_monitor.report.retail_flow.ler_filtrado_sem_guardar",
                       side_effect=ler), \
            mock.patch("shelf_monitor.report.retail_flow.months_back",
                       return_value=["202608", "202609"]):
        return build_retail_flow(Config(), snapshots)


class TestCaptacao(unittest.TestCase):
    def test_o_caixa_sai_das_listas_e_e_contado(self):
        flow = _fluxo(*_cenario_com_selic())
        self.assertEqual(flow["caixa_fora"], 1)
        self.assertNotIn(SELIC, {f["cnpj"] for f in flow["fundos"]})
        # Sem ele, o topo e o fundo que capta varejo de verdade.
        self.assertEqual(flow["top_captacao"][0]["cnpj"], "21000000000101")


class TestGaps(unittest.TestCase):
    def test_o_caixa_nao_e_gap_e_a_nota_diz_quantos_sairam(self):
        snapshots, _ = _cenario_com_selic()
        bloco = build_gap_block(snapshots)
        self.assertEqual(bloco["caixa_fora"], 1)
        self.assertNotIn(SELIC, {f["cnpj"] for f in bloco["funds"]})
        self.assertTrue(any("1 fundo caixa/simples fica fora" in n for n in notas_da_cobertura(bloco)))


class TestRentabilidadeECusto(unittest.TestCase):
    def setUp(self):
        self.config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)

    def test_rentabilidade_sem_o_caixa(self):
        vitrines = _vitrines()
        btg = next(s for s in vitrines if s.platform == "btg")
        btg.funds[0].name = NOME_SELIC
        bloco = build_performance_mensal(
            self.config, vitrines, _serie(), categorias=_categorias()
        )
        self.assertEqual(bloco["caixa_fora"], 1)
        casas = {c["platform_id"]: c for c in bloco["casas"]}
        self.assertEqual(casas["btg"]["vitrine"], FUNDOS_POR_CASA - 1)
        self.assertEqual(casas["xp"]["vitrine"], FUNDOS_POR_CASA)

    def test_custo_sem_o_caixa(self):
        categoria = "Renda Fixa Duração Livre Crédito Livre"
        snapshots, _ = _cenario_com_selic()
        cnpjs = {f.cnpj for s in snapshots for f in s.funds}
        classes = [classe(c, taxa=1.0, categoria=categoria) for c in cnpjs]
        classes += universo(categoria, 1.0)

        class _Indice(IndiceCusto):
            @classmethod
            def load(cls, market_dir):
                return cls({c.cnpj: c for c in classes})

        bloco = build_custo(self.config, snapshots, classe_indice=_Indice)
        self.assertEqual(bloco["caixa_fora"], 1)
        btg = next(c for c in bloco["casas"] if c["platform_id"] == "btg")
        self.assertEqual(btg["vitrine"], len(next(s for s in snapshots if s.platform == "btg").funds) - 1)


class TestDigest(unittest.TestCase):
    def test_o_digest_diz_quantos_fundos_caixa_sairam(self):
        from shelf_monitor.report.digest import render_html, render_markdown

        snapshots, registros = _cenario_com_selic()
        report = RunReport(
            generated_at=datetime(2026, 9, 24), snapshots=snapshots,
            retail_flow=_fluxo(snapshots, registros),
        )
        md = render_markdown(report, None, "semanal")
        # Na captacao e nos gaps.
        self.assertIn("_1 fundo caixa/simples fica fora (Renda Fixa Simples, Selic e DI)", md)
        self.assertIn("_Gaps: 1 fundo caixa/simples fica fora", md)
        self.assertNotIn(NOME_SELIC, md)
        self.assertIn("1 fundo caixa/simples fica fora", render_html(report, None, "semanal"))


if __name__ == "__main__":
    unittest.main()
