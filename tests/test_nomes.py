"""O nome do fundo no padrao das vitrines (pedido de 19/10/2026)."""
from __future__ import annotations

import unittest

from shelf_monitor.report.shared.nomes import nome_de_fundo


class TestNomeDeFundo(unittest.TestCase):
    def test_caixa_alta_vira_caixa_de_titulo_com_as_siglas(self):
        self.assertEqual(
            nome_de_fundo("TREND CASH RENDA FIXA SIMPLES FIF RL"),
            "Trend Cash Renda Fixa Simples FIF RL",
        )
        self.assertEqual(nome_de_fundo("BTG PACTUAL CDB PLUS FI RF CP"), "BTG Pactual CDB Plus FI RF CP")

    def test_forma_juridica_por_extenso_vira_sigla(self):
        self.assertEqual(
            nome_de_fundo(
                "ITAÚ GLOBAL DINÂMICO BAIXA VOL MULTIMERCADO FUNDO DE INVESTIMENTO FINANCEIRO "
                "DA CLASSE DE INVESTIMENTO EM COTAS RESPONSABILIDADE LIMITADA"
            ),
            "Itaú Global Dinâmico Baixa Vol Multimercado FIF CIC RL",
        )
        # O traco do sufixo por extenso nao fica solto.
        self.assertEqual(
            nome_de_fundo("V8 CASH FUNDO DE INVESTIMENTO FINANCEIRO – RESPONSABILIDADE LIMITADA"),
            "V8 Cash FIF RL",
        )

    def test_conector_em_minuscula_e_palavra_com_digito_como_veio(self):
        self.assertEqual(
            nome_de_fundo("RENDA FIXA E CRÉDITO D+30 EM AÇÕES"), "Renda Fixa e Crédito D+30 em Ações"
        )
        self.assertEqual(nome_de_fundo("PORTO INFLACAO IMA-B 5 RF FI"), "Porto Inflacao IMA-B 5 RF FI")
        self.assertEqual(
            nome_de_fundo("XP MACRO FIC FIM II - CLASSE A"), "XP Macro FIC FIM II - Classe A"
        )

    def test_nome_ja_no_padrao_fica_como_veio(self):
        for nome in (
            "SPX Nimitz FIC FIM",
            "AZ Quest Termo FIF RF LP",
            "RPS Long Bias Advisory D30 FIF em Cotas de FIA",
            "BB RF LP FIC FIF RL",
        ):
            self.assertEqual(nome_de_fundo(nome), nome)

    def test_sem_nome(self):
        self.assertEqual(nome_de_fundo(None), "")
        self.assertEqual(nome_de_fundo(""), "")


if __name__ == "__main__":
    unittest.main()
