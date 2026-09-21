"""Testes da rentabilidade ponderada por PL e por cotistas.

Os casos aqui sao as formas de a ponderacao mentir: peso zero virando divisao
por zero, cotista ausente contaminando a media por cotista, mediana de
referencia calculada sobre meia duzia de fundos, e casa com meia vitrine casada
entrando no ranking como se fosse a vitrine inteira.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from shelf_monitor.report.performance import (
    _MIN_AMOSTRA_CATEGORIA,
    _MIN_PREGOES,
    PerformanceCasa,
    RetornoFundo,
    _consolidar,
    _ponderada,
    medianas_de_referencia,
)
from shelf_monitor.report.retail_flow import DailyFlow


def fundo(
    cnpj: str = "1",
    retorno: float = 3.0,
    pl: float = 1_000_000.0,
    cotistas: int = 100,
    categoria: str | None = "Renda Fixa Simples",
    mediana: float | None = None,
) -> RetornoFundo:
    return RetornoFundo(
        cnpj=cnpj,
        categoria=categoria,
        retorno=retorno,
        pl=pl,
        cotistas=cotistas,
        dias=40,
        mediana_categoria=mediana,
    )


def serie(cnpj: str, pregoes: int, cota_inicial: float, cota_final: float,
          pl: float = 1_000_000.0, cotistas: int = 100) -> list[DailyFlow]:
    """Serie linear de cota, com o primeiro e o ultimo dia nos valores pedidos."""
    if pregoes == 1:
        cotas = [cota_inicial]
    else:
        passo = (cota_final - cota_inicial) / (pregoes - 1)
        cotas = [cota_inicial + passo * i for i in range(pregoes)]
    return [
        DailyFlow(
            cnpj=cnpj,
            dia=date(2026, 7, 1) + timedelta(days=i),
            captacao=0.0,
            resgates=0.0,
            pl=pl,
            cota=cota,
            cotistas=cotistas,
        )
        for i, cota in enumerate(cotas)
    ]


class TestPonderacao(unittest.TestCase):
    def test_peso_zero_nao_divide_por_zero(self):
        """Fundo recem-aberto tem PL e cotistas em zero no fim do periodo."""
        self.assertIsNone(_ponderada([fundo(pl=0.0)], lambda f: f.pl))
        self.assertIsNone(
            _ponderada([fundo(cotistas=0)], lambda f: float(f.cotistas))
        )

    def test_peso_negativo_nao_inverte_a_media(self):
        """PL negativo existe no informe (passivo a descoberto). Somado como
        peso, ele subtrairia o retorno do fundo em vez de ignora-lo."""
        fundos = [fundo("a", retorno=10.0, pl=-500.0), fundo("b", retorno=2.0, pl=100.0)]
        self.assertEqual(_ponderada(fundos, lambda f: f.pl), 2.0)

    def test_fundo_sem_cotistas_sai_da_por_cotista_e_fica_na_por_pl(self):
        """Veiculo institucional com 1 cotista nao move a leitura de varejo,
        mas move a de dinheiro - e as duas coisas tem de valer ao mesmo tempo."""
        casa = PerformanceCasa(
            platform_id="xp", label="XP", vitrine=2, com_serie=2,
            fundos=[
                fundo("institucional", retorno=1.0, pl=9_000_000.0, cotistas=0),
                fundo("varejo", retorno=5.0, pl=1_000_000.0, cotistas=50_000),
            ],
        )
        self.assertAlmostEqual(casa.por_cotista, 5.0)
        self.assertAlmostEqual(casa.por_pl, 1.4)

    def test_gap_positivo_significa_dinheiro_bem_alocado_e_varejo_mal(self):
        casa = PerformanceCasa(
            platform_id="xp", label="XP", vitrine=2, com_serie=2,
            fundos=[
                fundo("grande", retorno=8.0, pl=9_000_000.0, cotistas=10),
                fundo("pulverizado", retorno=1.0, pl=1_000_000.0, cotistas=90_000),
            ],
        )
        self.assertGreater(casa.gap, 0)

    def test_gap_negativo_e_o_inverso(self):
        casa = PerformanceCasa(
            platform_id="xp", label="XP", vitrine=2, com_serie=2,
            fundos=[
                fundo("grande", retorno=1.0, pl=9_000_000.0, cotistas=10),
                fundo("pulverizado", retorno=8.0, pl=1_000_000.0, cotistas=90_000),
            ],
        )
        self.assertLess(casa.gap, 0)


class TestAlocacaoAbaixoDaMediana(unittest.TestCase):
    """Mediana e mediana: metade de qualquer universo fica abaixo dela.

    Publicar "51% dos fundos abaixo da mediana" como alarme inverte o sinal - e
    foi o que a primeira versao fez, sob o rotulo "massa mal alocada". O numero
    que decide e o INDICE: share do PL sobre share de fundos.
    """

    def test_indice_abaixo_de_um_significa_dinheiro_nos_fundos_melhores(self):
        # Metade dos fundos abaixo da mediana, carregando um quarto do PL.
        casa = PerformanceCasa(
            platform_id="xp", label="XP", vitrine=4, com_serie=4,
            fundos=[
                fundo("ruim1", retorno=1.0, pl=500_000.0, cotistas=50, mediana=2.0),
                fundo("ruim2", retorno=1.0, pl=500_000.0, cotistas=50, mediana=2.0),
                fundo("bom1", retorno=3.0, pl=1_500_000.0, cotistas=150, mediana=2.0),
                fundo("bom2", retorno=3.0, pl=1_500_000.0, cotistas=150, mediana=2.0),
            ],
        )
        massa = casa.alocacao_abaixo_da_mediana
        self.assertAlmostEqual(massa["share_fundos"], 0.5)
        self.assertAlmostEqual(massa["share_pl"], 0.25)
        self.assertAlmostEqual(massa["indice_pl"], 0.5)

    def test_indice_acima_de_um_significa_dinheiro_nos_piores(self):
        casa = PerformanceCasa(
            platform_id="xp", label="XP", vitrine=2, com_serie=2,
            fundos=[
                fundo("ruim", retorno=1.0, pl=3_000_000.0, cotistas=300, mediana=2.0),
                fundo("bom", retorno=3.0, pl=1_000_000.0, cotistas=100, mediana=2.0),
            ],
        )
        massa = casa.alocacao_abaixo_da_mediana
        self.assertAlmostEqual(massa["share_fundos"], 0.5)
        self.assertAlmostEqual(massa["indice_pl"], 1.5)

    def test_conta_pl_e_cotistas_abaixo_da_mediana_da_categoria(self):
        casa = PerformanceCasa(
            platform_id="xp", label="XP", vitrine=3, com_serie=3,
            fundos=[
                fundo("ruim", retorno=1.0, pl=3_000_000.0, cotistas=300, mediana=2.0),
                fundo("bom", retorno=3.0, pl=1_000_000.0, cotistas=100, mediana=2.0),
                fundo("sem_ref", retorno=0.0, pl=9_000_000.0, cotistas=900, mediana=None),
            ],
        )
        massa = casa.alocacao_abaixo_da_mediana
        self.assertEqual(massa["fundos"], 1)
        # O fundo sem referencia fica fora do denominador: sem mediana da
        # categoria nao da para dizer se ele esta acima ou abaixo dela.
        self.assertEqual(massa["de"], 2)
        self.assertAlmostEqual(massa["share_pl"], 0.75)
        self.assertAlmostEqual(massa["share_cotistas"], 0.75)

    def test_sem_referencia_nenhuma_a_massa_e_vazia_e_nao_zero(self):
        casa = PerformanceCasa(
            platform_id="xp", label="XP", vitrine=1, com_serie=1,
            fundos=[fundo("x", mediana=None)],
        )
        massa = casa.alocacao_abaixo_da_mediana
        self.assertEqual(massa["de"], 0)
        self.assertIsNone(massa["share_pl"])
        self.assertIsNone(massa["indice_pl"])


class TestConsolidacao(unittest.TestCase):
    def test_retorno_vem_da_cota_de_ponta_a_ponta(self):
        consolidado = _consolidar(serie("1", 40, 10.0, 11.0))
        self.assertAlmostEqual(consolidado["1"][0], 10.0)

    def test_serie_curta_nao_vira_retorno_do_periodo(self):
        """Com poucos pregoes, a cota inicial e a final podem ser da mesma
        semana - e o numero descreveria dias, nao o periodo."""
        curta = serie("1", _MIN_PREGOES - 1, 10.0, 11.0)
        self.assertNotIn("1", _consolidar(curta))
        longa = serie("2", _MIN_PREGOES, 10.0, 11.0)
        self.assertIn("2", _consolidar(longa))

    def test_cota_inicial_zerada_fica_de_fora(self):
        """Dividir por zero daria retorno infinito; o fundo simplesmente nao
        tinha cota publicada no primeiro dia."""
        self.assertNotIn("1", _consolidar(serie("1", 40, 0.0, 11.0)))

    def test_pl_final_zerado_fica_de_fora(self):
        self.assertNotIn("1", _consolidar(serie("1", 40, 10.0, 11.0, pl=0.0)))


class TestSubclasses(unittest.TestCase):
    """Uma linha do informe e uma SUBCLASSE, nao uma classe.

    Medido em 2026-09-20: sem separar, o primeiro e o ultimo registro de um CNPJ
    podiam vir de subclasses diferentes, e o "retorno" comparava a cota final de
    uma com a cota inicial da outra. O Kinea RF Absoluto aparecia com 107,70% no
    periodo e 220 "pregoes" numa janela de 57 - eram 4 subclasses x 56 dias.
    """

    def _duas_subclasses(self) -> list[DailyFlow]:
        a = serie("1", 40, 100.0, 103.0, pl=1_000_000.0, cotistas=100)
        b = serie("1", 40, 10.0, 10.3, pl=3_000_000.0, cotistas=300)
        for registro in a:
            registro.subclasse = "SUB-A"
        for registro in b:
            registro.subclasse = "SUB-B"
        return a + b

    def test_retorno_nao_mistura_cotas_de_subclasses_diferentes(self):
        consolidado = _consolidar(self._duas_subclasses())
        # As duas rendem 3%; misturar as bases (10,3 / 100) daria -89,7%.
        self.assertAlmostEqual(consolidado["1"][0], 3.0, places=6)

    def test_pl_e_cotistas_somam_as_subclasses(self):
        retorno, pl, cotistas, pregoes, _captacao, _delta = _consolidar(
            self._duas_subclasses()
        )["1"]
        self.assertEqual(pl, 4_000_000.0)
        self.assertEqual(cotistas, 400)
        # 40 dias observados, nao 80 linhas.
        self.assertEqual(pregoes, 40)

    def test_retorno_da_classe_e_ponderado_pelo_pl_da_subclasse(self):
        a = serie("1", 40, 100.0, 110.0, pl=1_000_000.0)   # +10%
        b = serie("1", 40, 100.0, 100.0, pl=3_000_000.0)   # 0%
        for r in a:
            r.subclasse = "A"
        for r in b:
            r.subclasse = "B"
        # 10% com peso 1MM e 0% com peso 3MM -> 2,5%, nao 5%.
        self.assertAlmostEqual(_consolidar(a + b)["1"][0], 2.5, places=6)

    def test_retificacao_do_mesmo_dia_nao_conta_como_dois_pregoes(self):
        """A CVM republica o informe do mes com correcoes; o mesmo (CNPJ,
        subclasse, data) aparece duas vezes. Vence a ultima."""
        base = serie("1", 40, 100.0, 103.0)
        repetido = base + [base[-1]]
        self.assertEqual(_consolidar(repetido)["1"][3], 40)


class TestMedianaDeReferencia(unittest.TestCase):
    def _consolidado(self, quantidade: int) -> dict:
        return {
            str(i): (float(i), 1_000_000.0, 100, 40, 0.0, 0)
            for i in range(quantidade)
        }

    def test_categoria_com_amostra_pequena_nao_vira_referencia(self):
        """Com meia duzia de fundos, um atipico move a mediana e todo mundo
        vira "acima" ou "abaixo" dela por artefato de amostra."""
        poucos = self._consolidado(_MIN_AMOSTRA_CATEGORIA - 1)
        categorias = {cnpj: "Renda Fixa Simples" for cnpj in poucos}
        medianas, amostras = medianas_de_referencia(poucos, categorias)
        self.assertEqual(medianas, {})
        # A amostra continua visivel, para o bloco poder dizer por que excluiu.
        self.assertEqual(amostras["Renda Fixa Simples"], _MIN_AMOSTRA_CATEGORIA - 1)

    def test_categoria_com_amostra_suficiente_vira_referencia(self):
        muitos = self._consolidado(_MIN_AMOSTRA_CATEGORIA)
        categorias = {cnpj: "Renda Fixa Simples" for cnpj in muitos}
        medianas, _ = medianas_de_referencia(muitos, categorias)
        self.assertIn("Renda Fixa Simples", medianas)

    def test_fundo_sem_categoria_nao_entra_em_nenhuma_mediana(self):
        consolidado = self._consolidado(_MIN_AMOSTRA_CATEGORIA)
        medianas, amostras = medianas_de_referencia(consolidado, {})
        self.assertEqual(medianas, {})
        self.assertEqual(amostras, {})


class TestTicketMedio(unittest.TestCase):
    def test_pl_por_cotista_separa_varejo_de_institucional(self):
        institucional = fundo(pl=500_000_000.0, cotistas=3)
        varejo = fundo(pl=500_000_000.0, cotistas=200_000)
        self.assertGreater(institucional.ticket_medio, varejo.ticket_medio)

    def test_sem_cotista_nao_ha_ticket(self):
        self.assertIsNone(fundo(cotistas=0).ticket_medio)


if __name__ == "__main__":
    unittest.main()
