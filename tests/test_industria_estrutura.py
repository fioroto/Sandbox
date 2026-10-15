"""Estrutura e taxonomia do monitor da industria (Fase 1B).

Trava:

- o papel de cada classe pela carteira e pelo perfil: master, feeder (mesmo
  grupo, ainda que outra raiz de CNPJ), espelho (outro grupo), FoF da casa e
  multigestor, feeder de fundo no exterior, hibrido, direto, sem carteira;
- o flag de espelho do extrato nunca contradiz a carteira;
- a casa dedicada: espelho de grupo distribuidor, ou marca de outra casa no
  nome ("ADVISORY" -> XP);
- a cascata da estrategia: override, heranca da raiz (menos o involucro),
  rotulo ANBIMA truncado, indexado pelo indicador, as regras de carteira,
  credito incentivado pelo nome, FoF multigestor e o recuo para a CVM.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from shelf_monitor.familias import MapaDeFamilias
from shelf_monitor.industria.cadastro import Cadastro, Produto
from shelf_monitor.industria.cotistas import CHAVES, PerfilClasse
from shelf_monitor.industria.curadoria import carregar_curadoria
from shelf_monitor.industria.estrutura import MapaDeEstrutura
from shelf_monitor.industria.taxonomia import (
    CREDITO,
    CREDITO_VIA_MM,
    INCENTIVADO,
    JUROS,
    JUROS_ATIVO,
    MULTIGESTOR,
    MULTIMERCADO,
    POS,
    Taxonomia,
    indexado_pelo_indicador,
)

from carteira_apoio import Cda, classe
from fixtures_comuns import temp_config

SPX, SPX_OUTRA, ITAU, VERDE = "11111111000101", "11112222000101", "22222222000101", "33333333000101"

M = "10000000000001"   # master SPX
F = "10000000000002"   # feeder SPX por outra raiz do grupo
A = "10000000000003"   # feeder Advisory (marca da XP)
E = "10000000000004"   # espelho Itau
G = "10000000000005"   # FoF multigestor Itau
H = "10000000000006"   # FoF da casa Itau
O = "10000000000007"   # feeder de fundo no exterior
Y = "10000000000008"   # hibrido
D = "10000000000009"   # direto
S = "10000000000010"   # sem carteira, declarado espelho
X = "10000000000011"   # fundo da Verde, alvo do FoF
I1 = "10000000000012"  # fundos Itau, alvos do FoF da casa
I2 = "10000000000013"
C = "10000000000014"   # MM Livre de credito
J = "10000000000015"   # RF duracao livre GI com carteira em inflacao
P = "10000000000016"   # RF duracao livre GI pos
INC = "10000000000017"  # incentivado
IDX = "10000000000018"  # indexado IMA-B

GRUPOS = (
    "raiz;grupo;tipo;nome_de_referencia\n"
    "11111111;SPX;independente;SPX\n"
    "11112222;SPX;independente;SPX OUTRA ENTIDADE\n"
    "22222222;Itaú Unibanco;banco;ITAU\n"
    "33333333;Verde;independente;VERDE\n"
)
MARCAS = "token;casa;observacao\nADVISORY;XP;\nITAU;Itaú Unibanco;\n"
TAXONOMIA = (
    "prefixo_anbima;n1;n2;confianca;observacao\n"
    "MULTIMERCADOS MACRO;Multimercado;Macro;alta;\n"
    "MULTIMERCADOS LIVRE;Multimercado;Livre;media;\n"
    "RENDA FIXA DURACAO LIVRE GRAU DE INVEST;Pós-fixado e caixa;Grau de investimento, duração média e livre;media;\n"
    "RENDA FIXA DURACAO LIVRE CREDITO LIVRE;Crédito privado;Crédito média, alta e livre duração;alta;\n"
    "RENDA FIXA INDEXADOS;Juros e inflação;Indexado;alta;\n"
    "PREVIDENCIA RF;Previdência;Previdência renda fixa;alta;\n"
)


def _perfil(fundos: float) -> PerfilClasse:
    fatias = tuple(fundos if c == "fundos" else (1 - fundos if c == "pf_varejo" else 0.0) for c in CHAVES)
    return PerfilClasse(
        cnpj="x", versao=4, fatias=fatias, cotistas=tuple(100 for _ in CHAVES),
        previdencia=0.0, maior_cotista=None, soma_informada=1.0,
    )


def _produto(cnpj: str, nome: str, gestor_cnpj: str, **campos) -> Produto:
    padrao = dict(
        classe=cnpj, subclasse="", nivel="classe", nome=nome, gestor=nome.split()[0],
        gestor_cnpj=gestor_cnpj, tipo="Classes de Cotas de Fundos FIF",
        situacao="Em Funcionamento Normal", classificacao_anbima="Multimercados Macro",
        classificacao_cvm="Multimercado",
    )
    padrao.update(campos)
    return Produto(**padrao)


class _Cenario(unittest.TestCase):
    def setUp(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        pasta = config.industria_dir
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "grupos_gestores.csv").write_text(GRUPOS, encoding="utf-8")
        (pasta / "marcas_de_distribuidor.csv").write_text(MARCAS, encoding="utf-8")
        (pasta / "taxonomia.csv").write_text(TAXONOMIA, encoding="utf-8")
        (pasta / "overrides_estrategia.csv").write_text(
            f"cnpj_classe;n1;n2;motivo;autor;data\n{D};Ações;Livre;teste;x;2026-09-28\n",
            encoding="utf-8",
        )
        self.curadoria = carregar_curadoria(config)

        cda = Cda()
        for cnpj, pl in ((M, 1000), (F, 100), (A, 100), (E, 100), (G, 100), (H, 100), (O, 100),
                         (Y, 100), (D, 100), (X, 100), (I1, 100), (I2, 100), (C, 100), (J, 100),
                         (P, 100), (INC, 100), (IDX, 100)):
            cda.pl(cnpj, pl)
        cda.titulo(M, 1000)
        cda.cota(F, M, 99.0).titulo(F, 1.0)
        cda.cota(A, M, 98.0).titulo(A, 2.0)
        cda.cota(E, F, 97.0).titulo(E, 3.0)  # espelho do feeder: a cadeia leva ao master
        cda.cota(G, M, 40.0).cota(G, X, 40.0).titulo(G, 20.0)
        cda.cota(H, I1, 45.0).cota(H, I2, 45.0).titulo(H, 10.0)
        cda.exterior(O, 98.0, "PIMCO GIS INCOME").titulo(O, 2.0)
        cda.titulo(Y, 100.0)
        cda.acao(D, 100.0, "PETR4")
        cda.titulo(X, 100.0).titulo(I1, 100.0).titulo(I2, 100.0)
        cda.debenture(C, 70.0, "ABCD11").cdb(C, 10.0, "90400888000142").titulo(C, 20.0)
        cda.titulo(J, 80.0, titulo="NOTAS DO TESOURO NACIONAL SERIE B").titulo(J, 20.0)
        cda.titulo(P, 100.0).titulo(INC, 100.0).titulo(IDX, 100.0)

        linhas_mercado = [
            classe(cnpj, f"CLASSE {cnpj}", "G", gestor) for cnpj, gestor in (
                (M, SPX), (F, SPX_OUTRA), (A, SPX), (E, ITAU), (G, ITAU), (H, ITAU), (O, SPX),
                (Y, SPX), (D, SPX), (X, VERDE), (I1, ITAU), (I2, ITAU), (C, SPX), (J, SPX),
                (P, SPX), (INC, SPX), (IDX, SPX),
            )
        ]
        self.mapa = MapaDeFamilias.montar(linhas_mercado, [cda.resumo()])

        produtos = {
            M: _produto(M, "SPX NIMITZ MASTER", SPX),
            F: _produto(F, "SPX NIMITZ FIC", SPX_OUTRA, classificacao_anbima="Multimercados Livre"),
            A: _produto(A, "SPX NIMITZ ADVISORY FIC", SPX),
            E: _produto(E, "ITAU SPX NIMITZ FIC", ITAU, espelho_declarado=False),
            G: _produto(G, "ITAU MULTIGESTOR FIC", ITAU, classificacao_anbima="Multimercados Livre"),
            H: _produto(H, "ITAU ALOCACAO FIC", ITAU),
            O: _produto(O, "SPX GLOBAL FEEDER", SPX, classificacao_anbima=""),
            Y: _produto(Y, "SPX HIBRIDO", SPX),
            D: _produto(D, "SPX DIRETO", SPX),
            S: _produto(S, "SPX SEM CARTEIRA", SPX, espelho_declarado=True),
            X: _produto(X, "VERDE", VERDE),
            I1: _produto(I1, "ITAU UM", ITAU),
            I2: _produto(I2, "ITAU DOIS", ITAU),
            C: _produto(C, "SPX CREDITO FIM CP", SPX, classificacao_anbima="Multimercados Livre"),
            J: _produto(J, "SPX JUROS", SPX, classificacao_anbima="Renda Fixa Duração Livre Grau de Invest."),
            P: _produto(P, "SPX POS", SPX, classificacao_anbima="Renda Fixa Duração Livre Grau de Invest."),
            INC: _produto(INC, "SPX INFRA FIC FI-INFRA RF", SPX,
                          classificacao_anbima="Renda Fixa Duração Livre Crédito Livre"),
            IDX: _produto(IDX, "SPX IMA-B", SPX, classificacao_anbima="Renda Fixa Indexados",
                          indicador="Índice de Mercado Andima todas NTN-B"),
        }
        self.cadastro = Cadastro("202609", {(c, ""): p for c, p in produtos.items()})
        perfis = {M: _perfil(0.97), Y: _perfil(0.30), D: _perfil(0.0), F: _perfil(0.0)}
        self.estrutura = MapaDeEstrutura(self.cadastro, perfis, self.mapa, self.curadoria)
        self.taxonomia = Taxonomia(self.cadastro, self.estrutura, self.mapa, self.curadoria)


class TestPapeis(_Cenario):
    def test_cada_papel(self):
        esperado = {
            M: "master", F: "feeder", A: "feeder", E: "espelho", G: "fof_multigestor",
            H: "fof_casa", O: "feeder_exterior", Y: "hibrido", D: "direto", S: "sem_carteira",
        }
        for cnpj, papel in esperado.items():
            self.assertEqual(self.estrutura.de(cnpj).papel, papel, cnpj)

    def test_feeder_por_outra_raiz_do_mesmo_grupo_e_a_raiz_e_o_master(self):
        """Pela raiz do CNPJ, SPX_OUTRA seria outra gestora e o feeder viraria espelho."""
        estrutura = self.estrutura.de(F)
        self.assertEqual(estrutura.raiz, M)
        self.assertEqual(estrutura.grupo.nome, "SPX")

    def test_espelho_segue_a_cadeia_ate_o_master_e_a_casa_e_o_grupo_distribuidor(self):
        estrutura = self.estrutura.de(E)
        self.assertEqual(estrutura.raiz, M)
        self.assertEqual(estrutura.grupo_estrategia.nome, "SPX")
        self.assertEqual(estrutura.casa_dedicada, "Itaú Unibanco")

    def test_advisory_e_feeder_dedicado_a_xp(self):
        self.assertEqual(self.estrutura.de(A).casa_dedicada, "XP")
        # O feeder comum, sem marca de outra casa, nao tem casa dedicada.
        self.assertIsNone(self.estrutura.de(F).casa_dedicada)

    def test_flag_declarado_nao_contradiz_a_carteira(self):
        self.assertEqual(self.estrutura.de(E).papel, "espelho")  # declarado N
        sem = self.estrutura.de(S)
        self.assertEqual(sem.papel, "sem_carteira")
        self.assertTrue(sem.espelho_declarado)

    def test_feeder_exterior_guarda_o_veiculo_offshore(self):
        self.assertEqual(self.estrutura.de(O).exterior, "PIMCO GIS INCOME")

    def test_perfil_e_a_fonte_principal_da_fatia_detida(self):
        self.assertEqual(self.estrutura.de(M).fonte_detida, "perfil")
        # Sem perfil, a carteira: X e 40% do FoF G (40 de 100 do PL de X).
        self.assertEqual(self.estrutura.de(X).fonte_detida, "carteira")
        self.assertAlmostEqual(self.estrutura.de(X).fatia_detida, 0.40)


class TestTaxonomia(_Cenario):
    def test_feeder_herda_a_estrategia_da_raiz(self):
        estrategia = self.taxonomia.de(F)  # rotulo proprio "Livre", raiz "Macro"
        self.assertEqual((estrategia.n1, estrategia.n2, estrategia.origem), (MULTIMERCADO, "Macro", "raiz"))

    def test_override_vence_tudo(self):
        estrategia = self.taxonomia.de(D)
        self.assertEqual((estrategia.n1, estrategia.origem), ("Ações", "override"))

    def test_multimercado_livre_de_credito_vira_credito(self):
        estrategia = self.taxonomia.de(C)
        self.assertEqual((estrategia.n1, estrategia.n2, estrategia.origem), (CREDITO, CREDITO_VIA_MM, "carteira"))

    def test_rotulo_largo_de_duracao_livre_decide_pela_carteira(self):
        self.assertEqual(self.taxonomia.de(J).n2, JUROS_ATIVO)
        self.assertEqual(self.taxonomia.de(J).n1, JUROS)
        self.assertEqual(self.taxonomia.de(P).n1, POS)

    def test_incentivado_pelo_nome(self):
        self.assertEqual(self.taxonomia.de(INC).n2, INCENTIVADO)

    def test_indexado_pelo_indicador(self):
        self.assertEqual(self.taxonomia.de(IDX).n2, "Indexado inflação")
        self.assertEqual(indexado_pelo_indicador("IRF-M"), (JUROS, "Indexado pré"))
        self.assertEqual(indexado_pelo_indicador("DI de um dia"), (POS, "Indexado DI/Selic"))

    def test_fof_multigestor_tem_peer_group_proprio(self):
        self.assertEqual(self.taxonomia.de(G).n2, MULTIGESTOR)

    def test_sem_rotulo_usa_a_carteira_e_depois_a_cvm(self):
        estrategia = self.taxonomia.de(O)  # sem rotulo, carteira 98% exterior
        self.assertEqual((estrategia.n1, estrategia.origem), ("Internacional", "carteira"))
        sem_nada = self.taxonomia.de(S)
        self.assertEqual(sem_nada.n1, MULTIMERCADO)  # rotulo "Macro" do proprio cadastro

    def test_rotulo_truncado_casa_pelo_prefixo(self):
        linha = self.curadoria.linha_da_taxonomia("Previdência RF Duração Livre Crédito Liv")
        self.assertEqual(linha.n1, "Previdência")


if __name__ == "__main__":
    unittest.main()
