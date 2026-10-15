"""Testes do custo relativo a categoria.

O caso central e o que motivou o modulo: duas casas que cobram exatamente o
mesmo preco em cada categoria nao podem aparecer com custos diferentes so
porque uma tem mais renda fixa que a outra.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import date

from shelf_monitor.models import CollectorStrategy, FundOffer
from shelf_monitor.report.custo import (
    _MAX_IDADE_MESES,
    _MIN_AMOSTRA_CATEGORIA,
    CustoCasa,
    CustoClasse,
    IndiceCusto,
)

HOJE = date.today()


def recente(meses_atras: int = 1) -> date:
    ano, mes = HOJE.year, HOJE.month - meses_atras
    while mes <= 0:
        mes += 12
        ano -= 1
    return date(ano, mes, 1)


def classe(
    cnpj: str,
    taxa: float | None = 1.0,
    categoria: str | None = "Renda Fixa Simples",
    percentual: bool = True,
    data_base: date | None = None,
    **kwargs,
) -> CustoClasse:
    return CustoClasse(
        cnpj=cnpj,
        categoria=categoria,
        taxa_adm=taxa,
        taxa_percentual=percentual,
        data_base=data_base if data_base is not None else recente(),
        **kwargs,
    )


def indice_com(*classes: CustoClasse) -> IndiceCusto:
    return IndiceCusto({c.cnpj: c for c in classes})


def universo(categoria: str, taxa: float, quantos: int = _MIN_AMOSTRA_CATEGORIA):
    """Classes bastantes para a categoria virar referencia, todas na mesma taxa."""
    return [
        classe(f"{categoria[:3]}{i:011d}", taxa=taxa, categoria=categoria)
        for i in range(quantos)
    ]


def oferta(cnpj: str, nome: str = "Fundo") -> FundOffer:
    return FundOffer(
        platform="xp",
        name=nome,
        cnpj=cnpj,
        sources=[CollectorStrategy.PLAYWRIGHT.value],
    )


class TestElegibilidade(unittest.TestCase):
    def test_taxa_em_reais_nao_entra_no_indice(self):
        """TAXA_ADM carrega percentual e reais no mesmo campo. Um fundo de
        "R$ 30.000 por ano" no indice lideraria como se cobrasse 30.000%."""
        idx = indice_com(
            classe("1", taxa=30000.0, percentual=False), *universo("Renda Fixa Simples", 1.0)
        )
        avaliado = idx.avaliar(oferta("1"))
        self.assertIsNone(avaliado.indice)
        self.assertIn("reais", avaliado.motivo_fora)

    def test_entrega_antiga_sai_do_indice(self):
        """O extrato guarda uma linha por data-base desde 2015; uma taxa de
        2015 nao e a taxa de hoje."""
        antiga = classe("1", data_base=date(2015, 10, 2))
        idx = indice_com(antiga, *universo("Renda Fixa Simples", 1.0))
        avaliado = idx.avaliar(oferta("1"))
        self.assertIsNone(avaliado.indice)
        self.assertIn("meses", avaliado.motivo_fora)
        self.assertNotIn(str(avaliado.idade_meses), avaliado.motivo_fora)
        self.assertGreater(avaliado.idade_meses, _MAX_IDADE_MESES)

    def test_categoria_com_amostra_pequena_nao_vira_referencia(self):
        idx = indice_com(*universo("Nicho", 1.0, quantos=_MIN_AMOSTRA_CATEGORIA - 1))
        avaliado = idx.avaliar(oferta(f"Nic{0:011d}"))
        self.assertIsNone(avaliado.indice)
        self.assertIn("sem referência", avaliado.motivo_fora)
        # O motivo e agrupavel: sem a contagem no texto, cada fundo viraria uma
        # chave propria e o resumo "por motivo" deixaria de resumir.
        self.assertNotIn("9", avaliado.motivo_fora)

    def test_fundo_fora_da_base_diz_que_nao_casou(self):
        idx = indice_com(*universo("Renda Fixa Simples", 1.0))
        avaliado = idx.avaliar(oferta("99999999999999"))
        self.assertIn("não encontrado na base", avaliado.motivo_fora)


class TestIndiceRemoveComposicao(unittest.TestCase):
    """O motivo de o modulo existir."""

    def _casa(self, platform_id: str, rf: int, acoes: int) -> CustoCasa:
        # Todas as casas cobram 1,0% em renda fixa e 2,0% em acoes - exatamente
        # a mediana de cada categoria. So o MIX difere.
        idx = indice_com(
            *universo("Renda Fixa Simples", 1.0),
            *universo("Ações Livre", 2.0),
            *[classe(f"{platform_id}rf{i:09d}", taxa=1.0) for i in range(rf)],
            *[
                classe(f"{platform_id}ac{i:09d}", taxa=2.0, categoria="Ações Livre")
                for i in range(acoes)
            ],
        )
        ofertas = [oferta(f"{platform_id}rf{i:09d}") for i in range(rf)]
        ofertas += [oferta(f"{platform_id}ac{i:09d}") for i in range(acoes)]
        return CustoCasa(
            platform_id=platform_id,
            label=platform_id.upper(),
            vitrine=len(ofertas),
            fundos=[idx.avaliar(o) for o in ofertas],
        )

    def test_mix_diferente_com_preco_igual_da_o_mesmo_indice(self):
        so_rf = self._casa("aa", rf=40, acoes=2)
        so_acoes = self._casa("bb", rf=2, acoes=40)
        # A taxa mediana bruta separaria as duas (1,0% contra 2,0%); o indice
        # mediano nao, porque cada fundo ja foi comparado com a propria
        # categoria antes de a casa virar um numero.
        self.assertAlmostEqual(so_rf.indice_mediano, 1.0)
        self.assertAlmostEqual(so_acoes.indice_mediano, 1.0)

    def test_cobrar_acima_da_categoria_aparece_no_indice(self):
        idx = indice_com(
            *universo("Renda Fixa Simples", 1.0),
            classe("caro", taxa=2.0),
        )
        avaliado = idx.avaliar(oferta("caro"))
        self.assertAlmostEqual(avaliado.indice, 2.0)
        self.assertEqual(avaliado.faixa, "caro para a categoria")


class TestReferenciaDeVarejo(unittest.TestCase):
    """A mediana da categoria sai do universo de VAREJO, nao do universo todo.

    Medido em 2026-09-20: das 13.093 classes com taxa percentual e entrega
    recente, 47,2% sao exclusivas e 21,1% cobram zero. Com as duas dentro, a
    mediana de "Multimercados Livre" caia para 0,080% e a primeira execucao
    devolveu indice mediano de 6,79x com 82% da vitrine do BTG marcada como
    cara.
    """

    def test_classe_exclusiva_nao_entra_na_mediana(self):
        idx = indice_com(
            *[classe(f"ex{i:012d}", taxa=0.05, exclusivo=True) for i in range(40)],
            *universo("Renda Fixa Simples", 1.0),
            classe("alvo", taxa=1.0),
        )
        # So as 20 de varejo contam: mediana 1,0%, indice 1,00x.
        self.assertAlmostEqual(idx.avaliar(oferta("alvo")).indice, 1.0)

    def test_taxa_zero_nao_entra_na_mediana(self):
        idx = indice_com(
            *[classe(f"ze{i:012d}", taxa=0.0) for i in range(40)],
            *universo("Renda Fixa Simples", 1.0),
            classe("alvo", taxa=1.0),
        )
        self.assertAlmostEqual(idx.avaliar(oferta("alvo")).indice, 1.0)

    def test_publico_profissional_nao_entra_na_mediana(self):
        idx = indice_com(
            *[classe(f"pr{i:012d}", taxa=0.05, publico_alvo="PROFISSIONAL")
              for i in range(40)],
            *universo("Renda Fixa Simples", 1.0),
            classe("alvo", taxa=1.0),
        )
        self.assertAlmostEqual(idx.avaliar(oferta("alvo")).indice, 1.0)

    def test_qualificado_continua_na_mediana(self):
        """`profile.py:12-13` decidiu que qualificado nao e motivo de corte.

        Cortar aqui seria desfazer essa decisao por outra porta - e mudaria o
        numero: no universo real, cortar qualificado leva Multimercados Livre de
        0,800% para 1,000%.
        """
        idx = indice_com(
            *[classe(f"qu{i:012d}", taxa=2.0, publico_alvo="QUALIFICADO")
              for i in range(_MIN_AMOSTRA_CATEGORIA)],
            classe("alvo", taxa=2.0),
        )
        # Os qualificados sao a categoria inteira: a mediana e 2,0% e o alvo 1,00x.
        self.assertAlmostEqual(idx.avaliar(oferta("alvo")).indice, 1.0)

    def test_fundo_da_vitrine_com_taxa_zero_sai_com_motivo(self):
        """Feeder que cobra na outra ponta nao e "infinitamente barato"."""
        idx = indice_com(classe("feeder", taxa=0.0), *universo("Renda Fixa Simples", 1.0))
        avaliado = idx.avaliar(oferta("feeder"))
        self.assertIsNone(avaliado.indice)
        self.assertIn("outra ponta", avaliado.motivo_fora)


class TestPortaDeEntrada(unittest.TestCase):
    def test_classe_mais_barata_do_mesmo_fundo_e_sinalizada(self):
        idx = indice_com(
            classe("cara", minimo=50_000.0, id_fundo="F1"),
            classe("barata", minimo=100.0, id_fundo="F1"),
            *universo("Renda Fixa Simples", 1.0),
        )
        avaliado = idx.avaliar(oferta("cara"))
        self.assertEqual(avaliado.porta_menor, 100.0)

    def test_sem_classe_mais_barata_nao_inventa_porta(self):
        idx = indice_com(
            classe("unica", minimo=100.0, id_fundo="F1"),
            *universo("Renda Fixa Simples", 1.0),
        )
        self.assertIsNone(idx.avaliar(oferta("unica")).porta_menor)


class TestLiquidezEIsencao(unittest.TestCase):
    def test_resgate_ate_d1_conta_como_liquidez_imediata(self):
        idx = indice_com(
            classe("rapido", dias_cotizacao=1.0),
            classe("lento", dias_cotizacao=30.0),
            *universo("Renda Fixa Simples", 1.0),
        )
        self.assertTrue(idx.avaliar(oferta("rapido")).liquidez_imediata)
        self.assertFalse(idx.avaliar(oferta("lento")).liquidez_imediata)

    def test_sem_prazo_publicado_nao_e_falso(self):
        idx = indice_com(classe("x"), *universo("Renda Fixa Simples", 1.0))
        self.assertIsNone(idx.avaliar(oferta("x")).liquidez_imediata)

    def test_share_sem_entrada_ou_saida_ignora_quem_nao_respondeu(self):
        idx = indice_com(
            classe("livre", tem_taxa_entrada="N", tem_taxa_saida="N"),
            classe("cobra", tem_taxa_entrada="S", tem_taxa_saida="N"),
            classe("mudo"),
            *universo("Renda Fixa Simples", 1.0),
        )
        casa = CustoCasa(
            platform_id="xp", label="XP", vitrine=3,
            fundos=[idx.avaliar(oferta(c)) for c in ("livre", "cobra", "mudo")],
        )
        # 1 de 2 respondidos, nao 1 de 3.
        self.assertAlmostEqual(casa.share_sem_entrada_ou_saida, 0.5)


if __name__ == "__main__":
    unittest.main()
