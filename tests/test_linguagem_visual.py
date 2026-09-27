"""Linguagem visual: a paleta tem uma fonte so, e as pecas estaticas a seguem.

A cor de cada casa mora em `shared/estilo.py` (digest, e-mail) e nas variaveis
`--casa-*` do CSS do dashboard (o JS le de la). Duas copias do mesmo hex
divergem na primeira troca feita as pressas - estes testes existem para isso
falhar alto.
"""
from __future__ import annotations

import re
import unittest

from shelf_monitor.config import PLATFORMS
from shelf_monitor.report.dashboard import _SCRIPT, _STYLE, _faixa, _mini
from shelf_monitor.report.shared import estilo


def _variaveis_css() -> dict[str, str]:
    return {nome: valor.lower() for nome, valor in re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", _STYLE)}


class TestPaleta(unittest.TestCase):
    def test_css_e_estilo_tem_a_mesma_cor_por_casa(self):
        css = _variaveis_css()
        for casa, cor in estilo.CORES_DAS_CASAS.items():
            self.assertEqual(css.get(f"casa-{casa}"), cor, casa)
        self.assertEqual(css.get("casa-outra"), estilo.COR_OUTRA_CASA)
        self.assertEqual(css.get("destaque-suave"), estilo.DESTAQUE_SUAVE)
        self.assertEqual(css.get("tag"), estilo.ETIQUETA)
        self.assertEqual(css.get("zero"), estilo.ZERO)
        self.assertEqual(css.get("contorno-xp"), estilo.CONTORNO_XP)

    def test_marca_amarela_da_xp_tem_contorno(self):
        # O amarelo da XP fica a 1,6:1 do branco: sem o contorno a barra some.
        self.assertIn('svg [fill="var(--casa-xp)"] { stroke: var(--contorno-xp)', _STYLE)
        self.assertIn('[style*="background:var(--casa-xp)"]', _STYLE)
        self.assertEqual(estilo.contorno_da_casa("xp"), estilo.CONTORNO_XP)
        self.assertIsNone(estilo.contorno_da_casa("btg"))

    def test_toda_casa_configurada_tem_cor_propria(self):
        # Casa nova sem cor cairia no cinza em silencio: a escolha tem de ser feita.
        self.assertEqual(sorted(PLATFORMS), sorted(estilo.CORES_DAS_CASAS))

    def test_js_conhece_as_mesmas_casas_e_a_mesma_rampa(self):
        casas = re.search(r"const CASAS_COM_COR = \[([^\]]*)\]", _SCRIPT).group(1)
        self.assertEqual(sorted(re.findall(r'"([a-z]+)"', casas)), sorted(estilo.CORES_DAS_CASAS))
        rampa = re.search(r"const SEQ_STEPS = \[([^\]]*)\]", _SCRIPT).group(1)
        self.assertEqual(tuple(re.findall(r'"(#[0-9a-f]{6})"', rampa)), estilo.RAMPA)

    def test_passo_da_rampa(self):
        self.assertIsNone(estilo.passo_da_rampa(0, 10))
        self.assertEqual(estilo.passo_da_rampa(1, 100), (estilo.RAMPA[0], estilo.TINTA))
        self.assertEqual(estilo.passo_da_rampa(100, 100), (estilo.RAMPA[-1], "#ffffff"))
        self.assertEqual(estilo.cor_da_casa("xp"), estilo.CORES_DAS_CASAS["xp"])
        self.assertEqual(estilo.cor_da_casa("safra"), estilo.COR_OUTRA_CASA)


class TestFaixaDaManchete(unittest.TestCase):
    def _fmt(self, v: float) -> str:
        return f"{v:.1f}"

    def test_um_ponto_por_casa_e_a_base_maior(self):
        svg = _faixa([("xp", "XP", 1.0), ("btg", "BTG", 3.0), ("inter", "Inter", 2.0)], self._fmt, "xp")
        self.assertEqual(svg.count("<circle"), 3)
        self.assertIn('fill="var(--casa-xp)" stroke="#ffffff" stroke-width="2"', svg)
        self.assertIn('r="6.5"', svg)
        # As pontas levam o menor e o maior valor escritos.
        self.assertIn(">1.0</text>", svg)
        self.assertIn(">3.0</text>", svg)
        self.assertNotIn("<script", svg)

    def test_sem_faixa_quando_nao_ha_o_que_comparar(self):
        self.assertEqual(_faixa([("xp", "XP", 1.0)], self._fmt, "xp"), "")
        self.assertEqual(_faixa([("xp", "XP", 2.0), ("btg", "BTG", 2.0)], self._fmt, "xp"), "")
        self.assertEqual(_faixa([("xp", "XP", 1.0), ("btg", "BTG", None)], self._fmt, "xp"), "")

    def test_rotulo_vindo_da_fonte_e_escapado(self):
        svg = _faixa([("xp", "<b>XP</b>", 1.0), ("btg", "BTG", 2.0)], self._fmt, "xp")
        self.assertNotIn("<b>", svg)
        self.assertIn("&lt;b&gt;XP&lt;/b&gt;", svg)

    def test_casa_fora_do_registro_fica_cinza(self):
        svg = _faixa([("xp", "XP", 1.0), ("safra", "Safra", 2.0)], self._fmt, "xp")
        self.assertIn("var(--casa-outra)", svg)


class TestBarraDoPlacar(unittest.TestCase):
    def test_tamanho_proporcional_ao_maior(self):
        self.assertIn("width:50.0%", _mini(5, 10, "btg"))
        self.assertIn("background:var(--casa-btg)", _mini(5, 10, "btg"))

    def test_captacao_parte_do_meio_para_o_lado_do_sinal(self):
        entra = _mini(10, 10, "xp", sinal=True)
        sai = _mini(-5, 10, "xp", sinal=True)
        self.assertIn("left:50%", entra)
        self.assertIn("width:50.0%", entra)
        self.assertIn("right:50%", sai)
        self.assertIn("width:25.0%", sai)

    def test_sem_numero_sem_barra(self):
        self.assertEqual(_mini(None, 10, "xp"), "")
        self.assertEqual(_mini(3, 0, "xp"), "")
        self.assertEqual(_mini(-3, 10, "xp"), "")


if __name__ == "__main__":
    unittest.main()
