"""Testes da camada compartilhada dos blocos analiticos.

O risco que este modulo carrega e o oposto do normal: como TODOS os blocos
passam a chamar as mesmas funcoes, um erro aqui move cinco numeros publicados
de uma vez. Por isso os casos cobrem as bordas (lista vazia, peso zero,
empate) e nao so o caminho feliz.
"""
from __future__ import annotations

import unittest

from shelf_monitor.mercado_csv import categoria_da_linha
from shelf_monitor.report.shared import (
    MIN_COBERTURA_CUSTO,
    MIN_COBERTURA_JANELA,
    MIN_COBERTURA_VITRINE,
    arredonda,
    mediana,
    ordem_canonica,
    ponderada,
    quartis,
    rotulo_da_casa,
)


class TestMediana(unittest.TestCase):
    def test_impar_devolve_o_do_meio(self):
        self.assertEqual(mediana([3, 1, 2]), 2)

    def test_par_devolve_a_media_dos_dois_centrais(self):
        self.assertEqual(mediana([1, 2, 3, 4]), 2.5)

    def test_vazio_e_none_e_nao_zero(self):
        """Zero seria um valor; a ausencia de amostra nao e um valor."""
        self.assertIsNone(mediana([]))

    def test_aceita_qualquer_iteravel(self):
        self.assertEqual(mediana(x for x in (5, 1, 3)), 3)


class TestQuartis(unittest.TestCase):
    def test_menos_de_quatro_valores_nao_tem_quartil(self):
        self.assertIsNone(quartis([1, 2, 3]))

    def test_exclui_a_mediana_das_metades_quando_o_n_e_impar(self):
        q = quartis([1, 2, 3, 4, 5])
        self.assertEqual(q["median"], 3)
        self.assertEqual(q["q1"], 1.5)
        self.assertEqual(q["q3"], 4.5)
        self.assertEqual(q["n"], 5)


class TestPonderada(unittest.TestCase):
    def test_pondera_pelo_peso(self):
        itens = [(10.0, 1.0), (20.0, 3.0)]
        self.assertAlmostEqual(
            ponderada(itens, lambda i: i[0], lambda i: i[1]), 17.5
        )

    def test_peso_zero_cai_para_media_simples(self):
        """Peso zero e "nao sei o tamanho", nao "nao existe".

        Os cinco calculos que este helper substituiu divergiam justamente
        aqui: uns devolviam None, outros a media simples.
        """
        itens = [(10.0, 0.0), (20.0, 0.0)]
        self.assertAlmostEqual(
            ponderada(itens, lambda i: i[0], lambda i: i[1]), 15.0
        )

    def test_sem_peso_none_devolve_none_em_vez_da_media(self):
        """A rentabilidade ponderada precisa disto.

        "Quanto rendeu o real medio" sem real nenhum nao e a media simples dos
        fundos - e uma pergunta sem resposta. As cinco implementacoes que este
        helper substituiu divergiam exatamente aqui, e a escolha muda numero
        publicado, entao ela e um argumento e nao um default escondido.
        """
        itens = [(10.0, 0.0), (20.0, 0.0)]
        self.assertIsNone(
            ponderada(itens, lambda i: i[0], lambda i: i[1], sem_peso="none")
        )

    def test_sem_peso_invalido_falha_alto(self):
        with self.assertRaises(ValueError):
            ponderada([(1.0, 1.0)], lambda i: i[0], lambda i: i[1], sem_peso="zero")

    def test_peso_negativo_nao_inverte_o_sinal(self):
        """PL negativo nao existe, mas o informe traz celula ilegivel como 0."""
        itens = [(10.0, -5.0), (20.0, 5.0)]
        self.assertAlmostEqual(
            ponderada(itens, lambda i: i[0], lambda i: i[1]), 20.0
        )

    def test_ignora_item_sem_valor_mas_conta_o_resto(self):
        itens = [(None, 10.0), (20.0, 1.0)]
        self.assertAlmostEqual(
            ponderada(itens, lambda i: i[0], lambda i: i[1]), 20.0
        )

    def test_sem_valor_nenhum_e_none(self):
        self.assertIsNone(
            ponderada([(None, 1.0)], lambda i: i[0], lambda i: i[1])
        )


class TestArredonda(unittest.TestCase):
    def test_preserva_none(self):
        self.assertIsNone(arredonda(None))
        self.assertEqual(arredonda(1.23456), 1.2346)


class TestPlataformas(unittest.TestCase):
    def test_casa_desconhecida_devolve_o_proprio_id(self):
        self.assertEqual(rotulo_da_casa("casa-que-nao-existe"), "casa-que-nao-existe")

    def test_casa_conhecida_devolve_o_rotulo(self):
        self.assertEqual(rotulo_da_casa("xp"), "XP Investimentos")

    def test_ordem_e_a_da_configuracao_e_nao_alfabetica(self):
        ordem = ordem_canonica({"inter", "xp", "btg"})
        self.assertEqual(ordem, ["xp", "btg", "inter"])

    def test_casa_fora_da_configuracao_vai_para_o_fim_em_ordem_estavel(self):
        ordem = ordem_canonica({"zz", "xp", "aa"})
        self.assertEqual(ordem[0], "xp")
        self.assertEqual(ordem[1:], ["aa", "zz"])


class TestLimiares(unittest.TestCase):
    def test_cobertura_de_vitrine_e_de_janela_sao_eixos_diferentes(self):
        """O bug que este modulo veio resolver: um nome, tres significados.

        Se estes dois voltarem a ser o mesmo numero, o teste nao pega - mas o
        assert documenta que eles medem coisas diferentes e nao devem ser
        unificados por parecerem iguais.
        """
        self.assertNotEqual(MIN_COBERTURA_VITRINE, MIN_COBERTURA_JANELA)
        self.assertLess(MIN_COBERTURA_CUSTO, MIN_COBERTURA_VITRINE)



class TestCategoriaDaLinha(unittest.TestCase):
    """A cascata ANBIMA -> CVM estava copiada em tres modulos.

    A ordem e uma decisao: a classificacao ANBIMA e de tres niveis ("Renda
    Fixa Duracao Baixa Soberano") e a da CVM e o balde generico ("Renda
    Fixa"). Tres copias eram tres lugares para esquecer de mudar.
    """

    def test_anbima_vence_a_cvm(self):
        linha = {
            "classificacao_anbima": "Renda Fixa Duracao Baixa Soberano",
            "classificacao_cvm": "Renda Fixa",
        }
        self.assertEqual(
            categoria_da_linha(linha), "Renda Fixa Duracao Baixa Soberano"
        )

    def test_cai_para_a_cvm_quando_nao_ha_anbima(self):
        self.assertEqual(
            categoria_da_linha({"classificacao_anbima": "", "classificacao_cvm": "Renda Fixa"}),
            "Renda Fixa",
        )

    def test_espaco_em_branco_nao_conta_como_categoria(self):
        """Celula com espacos e celula vazia - a CVM entrega as duas formas."""
        self.assertEqual(
            categoria_da_linha({"classificacao_anbima": "   ", "classificacao_cvm": "Acoes"}),
            "Acoes",
        )

    def test_sem_nenhuma_devolve_vazio_e_nao_none(self):
        """Quem chama decide se pula a linha; o helper nao decide por ele."""
        self.assertEqual(categoria_da_linha({}), "")
        self.assertEqual(
            categoria_da_linha({"classificacao_anbima": None, "classificacao_cvm": None}), ""
        )


if __name__ == "__main__":
    unittest.main()
