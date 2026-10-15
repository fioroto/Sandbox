"""Testes da rentabilidade por janela.

Os casos sao as formas de uma janela mentir: pegar a base no dia errado, deixar
entrar fundo que so existe em parte do periodo, e misturar cotas de subclasses
diferentes - o mesmo erro que ja aparecera no fluxo.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.report.rentabilidade import (
    _MIN_COBERTURA,
    MesSubclasse,
    SerieMensal,
)


def mes(cota_ini: float, cota_fim: float, pl: float = 1_000_000.0,
        cotistas: int = 100, pregoes: int = 21) -> MesSubclasse:
    return MesSubclasse(
        cota_ini=cota_ini, cota_fim=cota_fim, pl_fim=pl,
        cotistas_fim=cotistas, pregoes=pregoes,
    )


def serie(meses: list[str], dados: dict) -> SerieMensal:
    return SerieMensal(meses, dados)


class TestBaseDaJanela(unittest.TestCase):
    def test_base_e_a_cota_do_fim_do_mes_anterior(self):
        """"12 meses ate setembro" compara 30/09 com 30/09 do ano passado.

        Usar a cota do primeiro dia da janela como base perderia o primeiro
        pregao e daria numero diferente de qualquer outra fonte publica.
        """
        s = serie(["202601", "202602", "202603"], {
            ("1", ""): {
                "202601": mes(90.0, 100.0),   # fecha janeiro em 100
                "202602": mes(100.0, 110.0),
                "202603": mes(110.0, 120.0),
            }
        })
        r = s.retorno("1", ["202602", "202603"])
        # 120 sobre 100 (fim de janeiro), nao sobre 100 (inicio de fevereiro):
        # aqui os dois coincidem, entao o teste seguinte e o que separa.
        self.assertAlmostEqual(r.retorno, 20.0)

    def test_base_do_mes_anterior_prevalece_sobre_a_abertura_da_janela(self):
        s = serie(["202601", "202602"], {
            ("1", ""): {
                "202601": mes(50.0, 100.0),
                # Abertura de fevereiro diferente do fechamento de janeiro: so
                # uma das duas pode ser a base, e e a de janeiro.
                "202602": mes(105.0, 110.0),
            }
        })
        r = s.retorno("1", ["202602"])
        self.assertAlmostEqual(r.retorno, 10.0)  # 110/100, nao 110/105

    def test_sem_mes_anterior_a_base_e_a_abertura_da_janela(self):
        """Comeco do historico, ou fundo que nasceu depois."""
        s = serie(["202601", "202602"], {
            ("1", ""): {"202601": mes(100.0, 110.0), "202602": mes(110.0, 121.0)}
        })
        r = s.retorno("1", ["202601", "202602"])
        self.assertAlmostEqual(r.retorno, 21.0)


class TestCobertura(unittest.TestCase):
    def test_fundo_presente_em_parte_da_janela_fica_de_fora(self):
        """Retorno de duas competencias ao lado de um de doze somaria periodos."""
        janela = [f"2026{m:02d}" for m in range(1, 13)]
        s = serie(janela, {("1", ""): {m: mes(100.0, 101.0) for m in janela[-2:]}})
        self.assertIsNone(s.retorno("1", janela))

    def test_fundo_presente_em_quase_toda_a_janela_entra(self):
        janela = [f"2026{m:02d}" for m in range(1, 13)]
        presentes = janela[: int(len(janela) * _MIN_COBERTURA)]
        s = serie(janela, {("1", ""): {m: mes(100.0, 110.0) for m in presentes}})
        r = s.retorno("1", janela)
        self.assertIsNotNone(r)
        self.assertEqual(r.meses, len(presentes))
        self.assertFalse(r.completa)

    def test_janela_completa_e_sinalizada(self):
        janela = ["202601", "202602"]
        s = serie(janela, {("1", ""): {m: mes(100.0, 110.0) for m in janela}})
        self.assertTrue(s.retorno("1", janela).completa)


class TestSubclasses(unittest.TestCase):
    def test_retorno_e_ponderado_pelo_pl_de_cada_subclasse(self):
        janela = ["202601", "202602"]
        s = serie(janela, {
            ("1", "A"): {m: mes(100.0, 110.0, pl=1_000_000.0) for m in janela},
            ("1", "B"): {m: mes(10.0, 10.0, pl=3_000_000.0) for m in janela},
        })
        r = s.retorno("1", janela)
        # +10% com peso 1MM e 0% com peso 3MM -> 2,5%, nao 5%.
        self.assertAlmostEqual(r.retorno, 2.5)
        self.assertEqual(r.subclasses, 2)

    def test_pl_e_cotistas_somam_as_subclasses(self):
        janela = ["202601"]
        s = serie(janela, {
            ("1", "A"): {"202601": mes(100.0, 101.0, pl=1e6, cotistas=100)},
            ("1", "B"): {"202601": mes(50.0, 50.5, pl=3e6, cotistas=300)},
        })
        r = s.retorno("1", janela)
        self.assertAlmostEqual(r.pl, 4e6)
        self.assertEqual(r.cotistas, 400)

    def test_cota_zerada_numa_subclasse_nao_derruba_a_classe(self):
        janela = ["202601"]
        s = serie(janela, {
            ("1", "A"): {"202601": mes(100.0, 110.0, pl=1e6)},
            ("1", "B"): {"202601": mes(0.0, 0.0, pl=1e6)},
        })
        r = s.retorno("1", janela)
        self.assertAlmostEqual(r.retorno, 10.0)
        self.assertEqual(r.subclasses, 1)


class TestJanelas(unittest.TestCase):
    def setUp(self):
        self.meses = ["202510", "202511", "202512", "202601", "202602", "202603"]
        self.s = serie(self.meses, {})

    def test_ytd_usa_o_ano_da_ultima_competencia(self):
        self.assertEqual(self.s.janela("YTD", None), ["202601", "202602", "202603"])

    def test_janela_de_tamanho_fixo_pega_as_ultimas(self):
        self.assertEqual(self.s.janela("3 meses", 3), ["202601", "202602", "202603"])

    def test_janela_maior_que_a_serie_devolve_a_serie_inteira(self):
        self.assertEqual(self.s.janela("24 meses", 24), self.meses)


class TestCache(unittest.TestCase):
    def test_gravar_e_ler_devolve_o_mesmo_resumo(self):
        resumo = {
            ("12345678000191", ""): mes(100.0, 110.0, pl=5e6, cotistas=42, pregoes=20),
            ("12345678000191", "SUB"): mes(10.0, 10.5, pl=1e6, cotistas=7, pregoes=20),
        }
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "202601.csv"
            SerieMensal._gravar_cache(caminho, resumo)
            lido = SerieMensal._ler_cache(caminho)
        self.assertEqual(set(lido), set(resumo))
        a, b = resumo[("12345678000191", "")], lido[("12345678000191", "")]
        self.assertAlmostEqual(a.cota_fim, b.cota_fim)
        self.assertEqual(a.cotistas_fim, b.cotistas_fim)
        self.assertEqual(a.pregoes, b.pregoes)

    def test_cache_de_formato_antigo_e_descartado(self):
        """Refazer e mais barato que adivinhar o que mudou no esquema."""
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "202601.csv"
            caminho.write_text("cnpj;cota\n123;10\n", encoding="utf-8")
            self.assertIsNone(SerieMensal._ler_cache(caminho))

    def test_cache_inexistente_nao_levanta(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(SerieMensal._ler_cache(Path(tmp) / "nao-existe.csv"))


class TestSerieVazia(unittest.TestCase):
    def test_serie_sem_competencia_nao_produz_janela(self):
        s = serie([], {})
        self.assertEqual(s.janela("12 meses", 12), [])
        self.assertIsNone(s.retorno("1", []))
        self.assertIsNone(s.periodo)


if __name__ == "__main__":
    unittest.main()
