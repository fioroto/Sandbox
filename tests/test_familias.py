"""Mapa de familias: espelho, master, classes paralelas, liquidez e veredito.

Costura 1 das duas combinadas na spec (docs/spec-gaps-xp-familias.md): o mapa
e montado com linhas do mercado.csv e da CDA em memoria, com os cabecalhos
reais da CVM, e os testes afirmam sobre as DECISOES - quem e espelho de quem,
que familias uma gestora tem, que veredito um gap recebe -, nunca sobre o
vetor de perfil ou a distancia numerica.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from shelf_monitor.familias import (
    ALTA,
    BAIXA,
    COBERTO_EQUIVALENTE,
    COBERTO_ESPELHO,
    GAP_ESTRATEGIA,
    GAP_GESTORA,
    MEDIA,
    OUTRA_LIQUIDEZ,
    PRESENTE,
    SEM_DADO,
    MapaDeFamilias,
)
from shelf_monitor.market import carteira as carteira_mod
try:
    from carteira_apoio import Cda, classe
    from fixtures_comuns import temp_config
except ImportError:  # rodando da raiz, como pacote
    from tests.carteira_apoio import Cda, classe
    from tests.fixtures_comuns import temp_config

# Gestoras (CNPJ do gestor) e os fundos de cada teste.
ALFA = ("ALFA GESTORA DE RECURSOS", "11111111000191")
ALFA_FILIAL = ("ALFA GESTORA DE RECURSOS - FILIAL RJ", "11111111000272")
BETA = ("BETA ASSET", "22222222000191")
GAMA = ("GAMA INVESTIMENTOS", "33333333000191")

MASTER = "10000000000101"
FEEDER_XP = "10000000000202"
FEEDER_BTG = "10000000000303"


def mapa_de(classes: list[dict], *cdas: Cda) -> MapaDeFamilias:
    return MapaDeFamilias.montar(classes, [c.resumo() for c in cdas])


def carteira_de_credito(cda: Cda, cnpj: str, pl: float = 100.0) -> Cda:
    """Credito corporativo: debentures de tres emissores."""
    return (cda.pl(cnpj, pl)
            .debenture(cnpj, pl * 0.4, "CSAN33")
            .debenture(cnpj, pl * 0.3, "AURE13")
            .debenture(cnpj, pl * 0.3, "LIGHC4"))


class TestEspelhoEMaster(unittest.TestCase):
    def test_feeder_com_95_por_cento_num_fundo_e_espelho_dele(self):
        cda = carteira_de_credito(Cda(), MASTER, 1000.0)
        cda.pl(FEEDER_XP, 100.0).cota(FEEDER_XP, MASTER, 99.0)
        mapa = mapa_de([
            classe(MASTER, "ALFA CREDITO MASTER", *ALFA),
            classe(FEEDER_XP, "ALFA CREDITO ADVISORY FIC", *ALFA),
        ], cda)

        self.assertEqual(mapa.master_de(FEEDER_XP), MASTER)
        familia = mapa.familia_de(FEEDER_XP)
        self.assertIs(familia, mapa.familia_de(MASTER))
        papeis = {m.cnpj: m.papel for v in familia.versoes for m in v.membros}
        self.assertEqual(papeis, {MASTER: "master", FEEDER_XP: "espelho"})

    def test_94_por_cento_nao_e_espelho(self):
        cda = carteira_de_credito(Cda(), MASTER, 1000.0)
        cda.pl(FEEDER_XP, 100.0).cota(FEEDER_XP, MASTER, 94.0).titulo(FEEDER_XP, 6.0)
        mapa = mapa_de([classe(MASTER, "M", *ALFA), classe(FEEDER_XP, "F", *ALFA)], cda)
        self.assertEqual(mapa.master_de(FEEDER_XP), FEEDER_XP)

    def test_fic_de_fic_chega_ao_mesmo_master(self):
        fic_de_fic = "10000000000404"
        cda = carteira_de_credito(Cda(), MASTER, 1000.0)
        cda.pl(FEEDER_XP, 100.0).cota(FEEDER_XP, MASTER, 100.0)
        cda.pl(fic_de_fic, 50.0).cota(fic_de_fic, FEEDER_XP, 50.0)
        mapa = mapa_de([
            classe(c, c, *ALFA) for c in (MASTER, FEEDER_XP, fic_de_fic)
        ], cda)
        self.assertEqual(mapa.master_de(fic_de_fic), MASTER)

    def test_ciclo_nao_trava_e_devolve_um_master(self):
        a, b = "20000000000101", "20000000000202"
        cda = Cda().pl(a, 100.0).cota(a, b, 100.0).pl(b, 100.0).cota(b, a, 100.0)
        mapa = mapa_de([classe(a, "A", *ALFA), classe(b, "B", *ALFA)], cda)
        self.assertIn(mapa.master_de(a), {a, b})

    def test_cadeia_longa_para_na_profundidade_maxima(self):
        cadeia = [f"3000000000{n:02d}01" for n in range(7)]
        cda = Cda()
        for de, para in zip(cadeia, cadeia[1:]):
            cda.pl(de, 100.0).cota(de, para, 100.0)
        carteira_de_credito(cda, cadeia[-1])
        mapa = mapa_de([classe(c, c, *ALFA) for c in cadeia], cda)
        # Quatro saltos a partir do primeiro: para no quinto elo, nao no ultimo.
        self.assertEqual(mapa.master_de(cadeia[0]), cadeia[4])

    def test_ligacao_recua_para_a_competencia_anterior(self):
        """Em 08 a cota do master estava sob sigilo; em 07 ela aparece."""
        agosto = carteira_de_credito(Cda("202608"), MASTER, 1000.0)
        agosto.pl(FEEDER_XP, 100.0).sigilo(FEEDER_XP, 100.0, "Cotas de Fundos")
        julho = Cda("202607").pl(FEEDER_XP, 100.0).cota(FEEDER_XP, MASTER, 100.0)
        mapa = MapaDeFamilias.montar(
            [classe(MASTER, "M", *ALFA), classe(FEEDER_XP, "F", *ALFA)],
            [agosto.resumo(), julho.resumo()],
        )
        self.assertEqual(mapa.master_de(FEEDER_XP), MASTER)
        self.assertEqual(mapa.competencia_de(FEEDER_XP), "202608")

    def test_classe_ausente_da_ultima_competencia_usa_a_anterior(self):
        agosto = carteira_de_credito(Cda("202608"), MASTER)
        julho = carteira_de_credito(Cda("202607"), FEEDER_XP)
        mapa = MapaDeFamilias.montar(
            [classe(MASTER, "M", *ALFA), classe(FEEDER_XP, "F", *ALFA)],
            [agosto.resumo(), julho.resumo()],
        )
        self.assertEqual(mapa.competencia_de(FEEDER_XP), "202607")
        self.assertEqual(mapa.competencias, ["202608", "202607"])


class TestFamiliasDaGestora(unittest.TestCase):
    def setUp(self):
        self.credito_a, self.credito_b = "40000000000101", "40000000000202"
        self.acoes = "40000000000303"
        cda = Cda()
        carteira_de_credito(cda, self.credito_a, 1000.0)
        carteira_de_credito(cda, self.credito_b, 500.0)
        cda.pl(self.acoes, 800.0).acao(self.acoes, 400.0, "ITUB4").acao(self.acoes, 400.0, "VALE3")
        self.mapa = mapa_de([
            classe(self.credito_a, "ALFA CREDITO 30", *ALFA,
                   anbima="Renda Fixa Duração Livre Crédito Livre", pl=1000.0),
            classe(self.credito_b, "ALFA CREDITO 5", *ALFA_FILIAL,
                   anbima="Renda Fixa Duração Livre Crédito Livre", pl=500.0, dias=(5, 0)),
            classe(self.acoes, "ALFA ACOES", *ALFA, anbima="Ações Livre", pl=800.0),
        ], cda)

    def test_por_nome_e_por_cnpj_da_gestora_as_familias_sao_as_mesmas(self):
        por_nome = self.mapa.familias_da_gestora("alfa gestora")
        por_cnpj = self.mapa.familias_da_gestora("11.111.111/0001-91")
        self.assertEqual([f.id for f in por_nome], [f.id for f in por_cnpj])

    def test_filial_da_mesma_raiz_e_a_mesma_gestora(self):
        """A versao D+5 foi registrada pela filial: mesma raiz, mesma familia."""
        familia = self.mapa.familia_de(self.credito_b)
        self.assertEqual(familia.id, self.credito_a)
        self.assertEqual(
            [m.dmais for v in familia.versoes for m in v.membros], [31, 5],
        )

    def test_categorias_diferentes_nunca_sao_a_mesma_familia(self):
        self.assertNotEqual(
            self.mapa.familia_de(self.acoes).id, self.mapa.familia_de(self.credito_a).id
        )

    def test_ordem_e_deterministica(self):
        de_novo = self.mapa.familias_da_gestora("ALFA")
        self.assertEqual(
            [f.id for f in de_novo], [f.id for f in self.mapa.familias_da_gestora("ALFA")]
        )
        # Maior PL primeiro dentro da gestora.
        self.assertEqual(de_novo[0].id, self.credito_a)


class TestClassesParalelas(unittest.TestCase):
    """Mesma gestora, mesma categoria: so a carteira decide."""

    def _par(self, carteira_b, *, gestora_b=ALFA, anbima_b="Multimercados Livre"):
        a, b = "50000000000101", "50000000000202"
        cda = Cda()
        cda.pl(a, 100.0).cdb(a, 60.0, "60746948000112").titulo(a, 40.0)
        carteira_b(cda, b)
        mapa = mapa_de([
            classe(a, "A", *ALFA), classe(b, "B", *gestora_b, anbima=anbima_b),
        ], cda)
        return mapa, a, b

    def test_carteira_proxima_com_ativos_em_comum_e_a_mesma_estrategia(self):
        mapa, a, b = self._par(
            lambda cda, b: cda.pl(b, 50.0).cdb(b, 31.0, "60746948000112").titulo(b, 19.0)
        )
        self.assertIs(mapa.familia_de(a), mapa.familia_de(b))

    def test_carteira_distante_nao(self):
        mapa, a, b = self._par(
            lambda cda, b: cda.pl(b, 50.0).acao(b, 50.0, "PETR4")
        )
        self.assertIsNot(mapa.familia_de(a), mapa.familia_de(b))

    def test_categoria_diferente_nao(self):
        mapa, a, b = self._par(
            lambda cda, b: cda.pl(b, 50.0).cdb(b, 30.0, "60746948000112").titulo(b, 20.0),
            anbima_b="Renda Fixa Duração Baixa Grau de Investimento",
        )
        self.assertIsNot(mapa.familia_de(a), mapa.familia_de(b))

    def test_gestoras_diferentes_com_carteira_identica_nao(self):
        mapa, a, b = self._par(
            lambda cda, b: cda.pl(b, 100.0).cdb(b, 60.0, "60746948000112").titulo(b, 40.0),
            gestora_b=BETA,
        )
        self.assertIsNot(mapa.familia_de(a), mapa.familia_de(b))

    def test_credito_bancario_nao_e_equivalente_de_credito_corporativo(self):
        mapa, a, b = self._par(
            lambda cda, b: carteira_de_credito(cda.titulo(b, 40.0), b, 60.0) and cda.pl(b, 100.0)
        )
        self.assertIsNot(mapa.familia_de(a), mapa.familia_de(b))

    def test_fundos_offshore_diferentes_do_mesmo_gestor_local_nao(self):
        """O caso que o vetor por faixa nao via: 100% exterior nos dois.

        Medido em 26/09/2026: Schroder GAIA, MAN AHL e Pearl Diver, feeders
        do mesmo gestor local, saiam na mesma familia sem a sobreposicao de
        ativos.
        """
        a, b = "60000000000101", "60000000000202"
        cda = (Cda().pl(a, 100.0).exterior(a, 100.0, "MAN AHL TARGET RISK")
               .pl(b, 100.0).exterior(b, 100.0, "SCHRODER GAIA 2 CLC"))
        mapa = mapa_de([classe(a, "A", *GAMA), classe(b, "B", *GAMA)], cda)
        self.assertIsNot(mapa.familia_de(a), mapa.familia_de(b))

    def test_carteira_sob_sigilo_nao_se_compara(self):
        a, b = "70000000000101", "70000000000202"
        cda = Cda()
        cda.pl(a, 100.0).acao(a, 100.0, "ITUB4")
        cda.pl(b, 100.0).acao(b, 20.0, "ITUB4").sigilo(b, 80.0, "Ações")
        mapa = mapa_de([
            classe(a, "A", *ALFA, anbima="Ações Livre"),
            classe(b, "B", *ALFA, anbima="Ações Livre"),
        ], cda)
        self.assertIsNone(mapa.perfil_determinado(b))
        self.assertIsNot(mapa.familia_de(a), mapa.familia_de(b))

    def test_derivativo_nao_pesa_no_perfil(self):
        a = "80000000000101"
        cda = Cda().pl(a, 100.0).titulo(a, 100.0).futuro(a, 5_000.0)
        mapa = mapa_de([classe(a, "A", *ALFA)], cda)
        self.assertEqual(set(mapa.perfil(a)), {"soberano_pos"})


class TestVeredito(unittest.TestCase):
    """O veredito de um gap aparente contra uma base (a vitrine da XP)."""

    def _mapa(self, dias_btg=(30, 1), dias_xp=(30, 1), dias_master=(0, 1)):
        cda = carteira_de_credito(Cda(), MASTER, 1000.0)
        cda.pl(FEEDER_XP, 100.0).cota(FEEDER_XP, MASTER, 100.0)
        cda.pl(FEEDER_BTG, 100.0).cota(FEEDER_BTG, MASTER, 100.0)
        return mapa_de([
            classe(MASTER, "ALFA CREDITO MASTER", *ALFA, dias=dias_master),
            classe(FEEDER_XP, "ALFA CREDITO ADVISORY", *ALFA, dias=dias_xp),
            classe(FEEDER_BTG, "BTG ACCESS ALFA CREDITO", "BTG PACTUAL ASSET MANAGEMENT",
                   "44444444000191", dias=dias_btg),
        ], cda)

    def test_espelho_do_mesmo_master_na_mesma_faixa_e_coberto_com_confianca_alta(self):
        veredito = self._mapa().cobertura(FEEDER_BTG, [FEEDER_XP])
        self.assertEqual(veredito.tipo, COBERTO_ESPELHO)
        self.assertEqual(veredito.confianca, ALTA)
        self.assertEqual(veredito.cobre, FEEDER_XP)
        self.assertEqual(veredito.master, MASTER)
        self.assertTrue(veredito.coberto)

    def test_a_estrategia_e_da_gestora_do_master_nao_do_veiculo(self):
        """O feeder de marca propria do concorrente leva a gestora do master."""
        veredito = self._mapa().cobertura(FEEDER_BTG, [FEEDER_XP])
        self.assertEqual(veredito.gestora, ALFA[0])

    def test_faixa_de_liquidez_diferente_e_outra_liquidez(self):
        veredito = self._mapa(dias_btg=(60, 1), dias_xp=(120, 1)).cobertura(
            FEEDER_BTG, [FEEDER_XP]
        )
        self.assertEqual(veredito.tipo, OUTRA_LIQUIDEZ)
        self.assertFalse(veredito.coberto)
        self.assertFalse(veredito.gap_real)
        self.assertEqual((veredito.dmais, veredito.dmais_cobre), (61, 121))

    def test_mesma_faixa_com_d_mais_diferente_continua_coberto(self):
        veredito = self._mapa(dias_btg=(10, 0), dias_xp=(21, 1)).cobertura(
            FEEDER_BTG, [FEEDER_XP]
        )
        self.assertEqual(veredito.tipo, COBERTO_ESPELHO)

    def test_liquidez_nao_informada_nao_rebaixa_o_veredito(self):
        veredito = self._mapa(dias_btg=None).cobertura(FEEDER_BTG, [FEEDER_XP])
        self.assertEqual(veredito.tipo, COBERTO_ESPELHO)
        self.assertIsNone(veredito.dmais)

    def test_o_proprio_master_na_base_cobre_o_espelho(self):
        veredito = self._mapa(dias_master=(30, 1)).cobertura(FEEDER_BTG, [MASTER])
        self.assertEqual(veredito.tipo, COBERTO_ESPELHO)

    def test_mesma_classe_por_outro_cnpj_e_presente(self):
        envelope = "90000000000100"
        cda = carteira_de_credito(Cda(), MASTER)
        mapa = mapa_de([classe(MASTER, "M", *ALFA, cnpj_fundo=envelope)], cda)
        veredito = mapa.cobertura(envelope, [MASTER])
        self.assertEqual(veredito.tipo, PRESENTE)

    def test_equivalente_e_confianca_media(self):
        a, b = "50000000000101", "50000000000202"
        cda = Cda()
        cda.pl(a, 100.0).cdb(a, 60.0, "60746948000112").titulo(a, 40.0)
        cda.pl(b, 50.0).cdb(b, 31.0, "60746948000112").titulo(b, 19.0)
        mapa = mapa_de([classe(a, "A", *ALFA), classe(b, "B", *ALFA)], cda)
        veredito = mapa.cobertura(a, [b])
        self.assertEqual(veredito.tipo, COBERTO_EQUIVALENTE)
        self.assertEqual(veredito.confianca, MEDIA)
        self.assertIsNotNone(veredito.sobreposicao)

    def test_gestora_fora_da_base_e_gap_de_gestora(self):
        cda = carteira_de_credito(Cda(), MASTER)
        outra = "91000000000101"
        carteira_de_credito(cda, outra)
        mapa = mapa_de([classe(MASTER, "M", *ALFA), classe(outra, "O", *BETA)], cda)
        self.assertEqual(mapa.cobertura(MASTER, [outra]).tipo, GAP_GESTORA)

    def test_gestora_na_base_sem_estrategia_equivalente_e_gap_de_estrategia(self):
        acoes = "92000000000101"
        cda = carteira_de_credito(Cda(), MASTER)
        cda.pl(acoes, 100.0).acao(acoes, 100.0, "VALE3")
        mapa = mapa_de([
            classe(MASTER, "M", *ALFA),
            classe(acoes, "ALFA ACOES", *ALFA, anbima="Ações Livre"),
        ], cda)
        self.assertEqual(mapa.cobertura(MASTER, [acoes]).tipo, GAP_ESTRATEGIA)

    def test_sem_carteira_que_decida_o_possivel_equivalente_so_informa(self):
        a, b = "93000000000101", "93000000000202"
        cda = Cda().pl(b, 100.0).sigilo(b, 100.0, "Debêntures")
        mapa = mapa_de([classe(a, "ALFA X FIC", *ALFA), classe(b, "ALFA X ADVISORY", *ALFA)], cda)
        veredito = mapa.cobertura(a, [b])
        self.assertEqual(veredito.tipo, SEM_DADO)
        self.assertTrue(veredito.gap_real)
        self.assertEqual(veredito.possivel_equivalente, b)
        self.assertEqual(veredito.confianca, BAIXA)

    def test_cnpj_fora_do_cadastro_e_sem_dado(self):
        mapa = mapa_de([classe(MASTER, "M", *ALFA)], carteira_de_credito(Cda(), MASTER))
        self.assertEqual(mapa.cobertura("99999999000199", [MASTER]).tipo, SEM_DADO)


class TestResolver(unittest.TestCase):
    def test_cnpj_do_fundo_vale_para_todas_as_classes_e_codigo_para_a_classe(self):
        envelope = "12000000000100"
        a, b = "12000000000201", "12000000000302"
        mapa = mapa_de([
            classe(a, "A", *ALFA, cnpj_fundo=envelope, codigo="555001"),
            classe(b, "B", *ALFA, cnpj_fundo=envelope, codigo="555002"),
        ])
        self.assertEqual(sorted(mapa.resolver(envelope)), [a, b])
        self.assertEqual(mapa.resolver(None, "555002"), [b])
        self.assertEqual(mapa.resolver("99999999000199"), [])


class TestResumoDaCarteira(unittest.TestCase):
    def test_tipo_desconhecido_entra_como_outros_e_vira_aviso(self):
        a = "13000000000101"
        mapa = mapa_de([classe(a, "A", *ALFA)], Cda().pl(a, 100.0).estranho(a, 100.0))
        self.assertEqual(set(mapa.perfil(a)), {"outros"})
        self.assertTrue(any("tipo desconhecido" in aviso for aviso in mapa.avisos))

    def test_lca_e_credito_bancario_e_cra_e_corporativo(self):
        a = "14000000000101"
        cda = Cda().pl(a, 100.0)
        cda._linha("BLC_6", a, TP_APLIC="Títulos ligados ao agronegócio", TP_ATIVO="LCA",
                   VL_MERC_POS_FINAL=50.0, CPF_CNPJ_EMISSOR="60.746.948/0001-12")
        cda._linha("BLC_6", a, TP_APLIC="Títulos ligados ao agronegócio", TP_ATIVO="CRA",
                   VL_MERC_POS_FINAL=50.0, CPF_CNPJ_EMISSOR="02.916.265/0001-60")
        mapa = mapa_de([classe(a, "A", *ALFA)], cda)
        self.assertEqual(
            mapa.perfil(a), {"credito_bancario": 0.5, "credito_corporativo": 0.5}
        )

    def test_zip_da_cvm_e_resumido_uma_vez_e_depois_vem_do_cache(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        config.market_raw_dir.mkdir(parents=True, exist_ok=True)
        carteira_de_credito(Cda("202608"), MASTER).zip(config.market_raw_dir)

        primeiro, aviso = carteira_mod.carregar_competencia(config, "202608", baixar=False)
        self.assertIsNone(aviso)
        self.assertIn(MASTER, primeiro.classes)
        with mock.patch.object(carteira_mod, "ler_zip") as ler:
            segundo, _ = carteira_mod.carregar_competencia(config, "202608", baixar=False)
        ler.assert_not_called()
        self.assertEqual(
            segundo.classes[MASTER].faixas, primeiro.classes[MASTER].faixas
        )

    def test_carregar_sem_base_de_mercado_devolve_none(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        self.assertIsNone(MapaDeFamilias.carregar(config, date(2026, 9, 26), baixar=False))


if __name__ == "__main__":
    unittest.main()
