"""Testes da lista unica de gaps.

O monitor publicava TRES listas de "fundo que a XP nao tem", com universos e
ordens diferentes e sem hierarquia entre elas. Fundidas numa so, duas decisoes
precisam ficar travadas: o rotulo de esforco tem de vir da gestora (e nao do
bloco de captacao em que o fundo por acaso caiu), e a ordem tem de abrir pelo
que da para incluir.
"""
from __future__ import annotations

import unittest

from shelf_monitor.report.aggregate import (
    ESFORCO_ABRIR_GESTORA,
    ESFORCO_INDEFINIDO,
    ESFORCO_NAO_TRANSFERIVEL,
    ESFORCO_OPERACIONAL,
    ranquear_gaps,
)


def gap(nome: str, *, cnpj: str, gestora: str | None, casas: list[str],
        na_xp: bool | None = False, pl: float = 0.0) -> dict:
    return {
        "cnpj": cnpj,
        "cnpj_display": cnpj,
        "name": nome,
        "category": "Renda Fixa",
        "manager": gestora,
        "houses": casas,
        "houses_label": ", ".join(casas),
        "houses_count": len(casas),
        "manager_on_xp": na_xp,
        "pl_cvm": pl,
    }


def relatorio(top_captacao: list[dict] | None = None) -> dict:
    """O bloco de captacao, que e o unico insumo externo do ranqueamento."""
    return {"available": True, "top_captacao": top_captacao or []}


class TestEsforco(unittest.TestCase):
    def test_produto_da_casa_que_exibe_nao_e_transferivel(self):
        """A gestora e testada contra a watchlist das casas que EXIBEM o fundo.

        Caso real: o BTGP Tesouro Selic capta +R$ 1,86 bi e e produto do
        proprio BTG. Derivando o rotulo do bloco de captacao em que o fundo
        caiu, ele vinha de `top_captacao`, que nao carrega o campo, e saia
        como "inclusao operacional" - o oposto do que ele e, no topo da lista.
        """
        fundos = [gap("BTGP TESOURO SELIC FI RENDA FIXA REFERENCIADO",
                      cnpj="1", gestora="BTG PACTUAL ASSET MANAGEMENT S/A DTVM",
                      casas=["btg"], na_xp=True)]
        saida = ranquear_gaps(fundos, relatorio())
        self.assertEqual(saida[0]["esforco"], ESFORCO_NAO_TRANSFERIVEL)

    def test_gestora_ja_distribuida_e_inclusao_operacional(self):
        fundos = [gap("Safra IA", cnpj="2", gestora="SAFRA DTVM",
                      casas=["btg"], na_xp=True)]
        self.assertEqual(
            ranquear_gaps(fundos, relatorio())[0]["esforco"], ESFORCO_OPERACIONAL
        )

    def test_gestora_nova_exige_abrir_relacionamento(self):
        fundos = [gap("Nord Select", cnpj="3", gestora="NORD ASSET",
                      casas=["btg"], na_xp=False)]
        self.assertEqual(
            ranquear_gaps(fundos, relatorio())[0]["esforco"], ESFORCO_ABRIR_GESTORA
        )

    def test_sem_gestora_nao_se_afirma_esforco(self):
        """Sem gestora nao da para dizer de quem e o produto."""
        fundos = [gap("Fundo Sem Gestora", cnpj="4", gestora=None, casas=["btg"])]
        self.assertEqual(
            ranquear_gaps(fundos, relatorio())[0]["esforco"], ESFORCO_INDEFINIDO
        )


class TestOrdem(unittest.TestCase):
    def test_abre_pelo_que_da_para_incluir_e_nao_pela_maior_captacao(self):
        """Ordenada so por captacao, a lista abria com o que nao se transfere.

        O produto do concorrente continua na lista - e informacao competitiva -
        mas no fim: quem abre o relatorio esta decidindo o que incluir.
        """
        fundos = [
            gap("Proprio do BTG, capta muito", cnpj="1",
                gestora="BTG PACTUAL ASSET MANAGEMENT", casas=["btg"], na_xp=True),
            gap("De terceiro, capta pouco", cnpj="2",
                gestora="SAFRA DTVM", casas=["btg"], na_xp=True),
        ]
        report = relatorio(top_captacao=[
            {"cnpj": "1", "captacao_liquida": 1_800_000_000.0, "variacao_cotistas": 10},
            {"cnpj": "2", "captacao_liquida": 5_000_000.0, "variacao_cotistas": 900},
        ])
        saida = ranquear_gaps(fundos, report)
        self.assertEqual(saida[0]["cnpj"], "2")
        self.assertEqual(saida[0]["esforco"], ESFORCO_OPERACIONAL)
        self.assertEqual(saida[-1]["esforco"], ESFORCO_NAO_TRANSFERIVEL)
        self.assertEqual(saida[-1]["captacao_liquida"], 1_800_000_000.0)

    def test_dentro_da_faixa_vale_a_captacao(self):
        fundos = [
            gap("Pouco", cnpj="1", gestora="SAFRA DTVM", casas=["btg"], na_xp=True),
            gap("Muito", cnpj="2", gestora="AZ QUEST", casas=["btg"], na_xp=True),
        ]
        report = relatorio(top_captacao=[
            {"cnpj": "1", "captacao_liquida": 1_000_000.0},
            {"cnpj": "2", "captacao_liquida": 900_000_000.0},
        ])
        saida = ranquear_gaps(fundos, report)
        self.assertEqual([f["cnpj"] for f in saida], ["2", "1"])

    def test_gap_sem_informe_continua_na_lista(self):
        """Nao ter serie no informe diario nao e motivo para sumir."""
        fundos = [
            gap("Com fluxo", cnpj="1", gestora="SAFRA DTVM", casas=["btg"], na_xp=True),
            gap("Sem fluxo", cnpj="2", gestora="AZ QUEST", casas=["btg"], na_xp=True),
        ]
        report = relatorio(top_captacao=[{"cnpj": "1", "captacao_liquida": 5.0}])
        saida = ranquear_gaps(fundos, report)
        self.assertEqual(len(saida), 2)
        self.assertEqual(saida[0]["cnpj"], "1")
        self.assertIsNone(saida[1]["captacao_liquida"])

    def test_lista_vazia_nao_quebra(self):
        self.assertEqual(ranquear_gaps([], relatorio()), [])


if __name__ == "__main__":
    unittest.main()
