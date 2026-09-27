"""Ticket de entrada: o minimo que o cliente ve, nunca o do catalogo.

Trava a cadeia que publicou uma conclusao falsa em 2026-09-21 - "a vitrine da XP
nao tem nenhum fundo abaixo de R$ 5 mil; e a ausencia de uma porta de entrada",
com a lista da XP mostrando 80% dela abaixo disso:

1. o extrator nao reconhecia `minimalInitialInvestment`, o campo do XHR da XP;
2. o merge deixava o Open Finance vencer e apagava o minimo da vitrine;
3. as faixas liam o valor mesclado, e o veredito nao exigia amostra.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import datetime

from shelf_monitor.collectors.browser import to_offer
from shelf_monitor.merge import merge_offers
from shelf_monitor.models import (
    KEY_MINIMO_VITRINE,
    CollectorStrategy,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.report.aggregate import leitura_do_ticket, summarize_platform
from shelf_monitor.report.competitive import build_positioning, vitrine_only_values
from shelf_monitor.report.digest import _frase_do_ticket
from shelf_monitor.report.shared import MIN_AMOSTRA_TICKET

OF = CollectorStrategy.OPEN_FINANCE.value
NAVEGADOR = CollectorStrategy.PLAYWRIGHT.value


def _fundo(platform: str, i: int, minimo: float | None, sources: list[str], **extra) -> FundOffer:
    base = {"xp": 0, "btg": 1_000, "itau": 2_000}[platform]
    fundo = FundOffer(
        platform=platform,
        name=f"{platform.upper()} Fundo {i}",
        cnpj=f"{base + i:014d}",
        min_initial=minimo,
        sources=list(sources),
    )
    fundo.extra.update(extra)
    return fundo


def _snapshot(platform: str, fundos: list[FundOffer]) -> ShelfSnapshot:
    return ShelfSnapshot(
        platform=platform,
        collected_at=datetime(2026, 9, 23, 10, 0),
        strategy=CollectorStrategy.PLAYWRIGHT,
        status=SnapshotStatus.OK,
        funds=fundos,
    )


class TestMinimoDaVitrineNaColeta(unittest.TestCase):
    def test_extrator_le_o_minimo_e_a_taxa_do_xhr_da_xp(self):
        # Formato real do XHR da lista (api.xpi.com.br/investment-funds).
        oferta = to_offer("xp", {
            "name": "XP Fundo Exemplo FIC FIM",
            "cnpj": "11111111000111",
            "minimalInitialInvestment": 500.0,
            "administrationRate": 1.34,
        })
        self.assertEqual(oferta.min_initial, 500.0)
        self.assertEqual(oferta.admin_fee_pct, 1.34)

    def test_merge_guarda_o_minimo_da_vitrine_antes_da_precedencia(self):
        catalogo = FundOffer(platform="xp", name="XP Fundo", cnpj="22222222000122",
                             min_initial=50_000.0)
        vitrine = FundOffer(platform="xp", name="XP Fundo", cnpj="22222222000122",
                            min_initial=500.0)
        [unido] = merge_offers({OF: [catalogo], NAVEGADOR: [vitrine]})

        # No campo, o Open Finance continua vencendo...
        self.assertEqual(unido.min_initial, 50_000.0)
        # ...mas o que o cliente ve nao se perde mais.
        self.assertEqual(unido.extra[KEY_MINIMO_VITRINE], 500.0)
        self.assertEqual(unido.minimo_vitrine, 500.0)

    def test_fundo_so_do_catalogo_nao_tem_minimo_de_vitrine(self):
        [unido] = merge_offers({OF: [FundOffer(platform="xp", name="Cat", cnpj="3" * 14,
                                               min_initial=50_000.0)]})
        self.assertIsNone(unido.minimo_vitrine)

    def test_snapshot_antigo_nao_inventa_minimo(self):
        # Gravado antes da chave: fundo so da vitrine usa o proprio minimo; fundo
        # que tambem estava no catalogo fica sem, em vez de herdar o institucional.
        so_vitrine = _fundo("xp", 1, 1_000.0, [NAVEGADOR])
        com_catalogo = _fundo("xp", 2, 50_000.0, [OF, NAVEGADOR])
        self.assertEqual(so_vitrine.minimo_vitrine, 1_000.0)
        self.assertIsNone(com_catalogo.minimo_vitrine)
        self.assertEqual(vitrine_only_values([so_vitrine, com_catalogo], "min_initial"), [1_000.0])


class TestFaixasEVeredito(unittest.TestCase):
    def _xp_como_em_2026_09_21(self, n: int = 40) -> list[FundOffer]:
        """Todos com o minimo institucional do Open Finance, nenhum da vitrine."""
        return [_fundo("xp", i, 50_000.0, [OF, NAVEGADOR]) for i in range(n)]

    def _xp_com_minimo_da_vitrine(self, n: int = 40) -> list[FundOffer]:
        return [
            _fundo("xp", i, 50_000.0, [OF, NAVEGADOR], **{KEY_MINIMO_VITRINE: 500.0})
            for i in range(n)
        ]

    def _btg_barato(self, n: int = 40) -> list[FundOffer]:
        return [_fundo("btg", i, 500.0, [NAVEGADOR]) for i in range(n)]

    def test_faixas_ignoram_o_minimo_do_catalogo(self):
        resumo = summarize_platform(_snapshot("xp", self._xp_como_em_2026_09_21()), set())
        self.assertEqual(resumo.min_bands, {})
        self.assertIsNone(resumo.lowest_min)

        resumo = summarize_platform(_snapshot("xp", self._xp_com_minimo_da_vitrine()), set())
        self.assertEqual(resumo.min_bands, {"R$ 100 a 1 mil": 40})
        self.assertEqual(resumo.lowest_min, 500.0)

    def test_sem_amostra_da_vitrine_a_leitura_nao_conclui(self):
        """O caso publicado: nao dizer "nenhum fundo abaixo de R$ 5 mil"."""
        resumos = [
            summarize_platform(_snapshot("xp", self._xp_como_em_2026_09_21()), set()).to_dict(),
            summarize_platform(_snapshot("btg", self._btg_barato()), set()).to_dict(),
        ]
        leitura = leitura_do_ticket(resumos)
        self.assertTrue(leitura["sem_base"])
        self.assertIsNone(leitura["baseline_varejo"])
        self.assertEqual(leitura["amostra_minima"], MIN_AMOSTRA_TICKET)

        report = RunReport(
            generated_at=datetime(2026, 9, 23),
            snapshots=[_snapshot("xp", self._xp_como_em_2026_09_21()),
                       _snapshot("btg", self._btg_barato())],
        )
        frase = _frase_do_ticket(report)
        self.assertTrue(frase.startswith("Sem base para comparar o ticket"))
        self.assertNotIn("não tem nenhum fundo", frase)
        self.assertNotIn("porta de entrada", frase)

    def test_com_amostra_o_veredito_forte_continua_possivel(self):
        caros = [
            _fundo("xp", i, 50_000.0, [OF, NAVEGADOR], **{KEY_MINIMO_VITRINE: 30_000.0})
            for i in range(40)
        ]
        resumos = [
            summarize_platform(_snapshot("xp", caros), set()).to_dict(),
            summarize_platform(_snapshot("btg", self._btg_barato()), set()).to_dict(),
        ]
        leitura = leitura_do_ticket(resumos)
        self.assertFalse(leitura["sem_base"])
        self.assertEqual(leitura["baseline_varejo"], 0)
        self.assertEqual(leitura["melhor_varejo"], 40)

    def test_concorrente_sem_amostra_nao_serve_de_comparacao(self):
        resumos = [
            summarize_platform(_snapshot("xp", self._xp_com_minimo_da_vitrine()), set()).to_dict(),
            summarize_platform(_snapshot("btg", self._btg_barato(n=10)), set()).to_dict(),
        ]
        self.assertIsNone(leitura_do_ticket(resumos))

    def test_posicionamento_usa_o_mesmo_minimo(self):
        # As duas regras de "ticket" viraram uma: o posicionamento competitivo
        # tambem nao pode ler o minimo institucional.
        posicoes = build_positioning([
            _snapshot("xp", self._xp_com_minimo_da_vitrine()),
            _snapshot("btg", self._btg_barato()),
        ])
        xp = next(p for p in posicoes["positions"] if p["platform_id"] == "xp")
        self.assertEqual(xp["median_min"], 500.0)

        posicoes = build_positioning([
            _snapshot("xp", self._xp_como_em_2026_09_21()),
            _snapshot("btg", self._btg_barato()),
        ])
        xp = next(p for p in posicoes["positions"] if p["platform_id"] == "xp")
        self.assertIsNone(xp["median_min"])


if __name__ == "__main__":
    unittest.main()
