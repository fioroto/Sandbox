"""Texto das saidas: acentuacao, mojibake e fontes com link.

- UTF-8 lido como Latin-1 ("DÃ³lar") e reparado na leitura e evitado na coleta:
  em 2026-09-24, 132 dos 466 nomes da vitrine do Inter estavam gravados assim.
- Cada relatorio lista as fontes usadas, com o endereco publico para consulta.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock

import requests

from shelf_monitor.config import Config
from shelf_monitor.http import get
from shelf_monitor.models import (
    CollectorStrategy,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.report.shared.fontes import (
    CVM_CADASTRO,
    CVM_INFORME_DIARIO,
    OPEN_FINANCE,
    fontes_do_relatorio,
)
from shelf_monitor.texto import consertar_mojibake, contagem


class TestMojibake(unittest.TestCase):
    def test_utf8_lido_como_latin1_volta_ao_normal(self):
        self.assertEqual(
            consertar_mojibake("Western Asset Hedge DÃ³lar FI Cambial"),
            "Western Asset Hedge Dólar FI Cambial",
        )
        self.assertEqual(consertar_mojibake("Porto Seguro ClÃ¡ssico"), "Porto Seguro Clássico")

    def test_texto_correto_nao_muda(self):
        for texto in ("INFLAÇÃO", "CÂMBIO", "Dólar", "Ações", "sem acento", "à\xa0vista", "", None):
            self.assertEqual(consertar_mojibake(texto), texto)

    def test_acento_decomposto_lido_como_latin1_volta_composto(self):
        # "Sulamérica" em NFD (e + U+0301) lido como Latin-1: 1 nome do Inter em 2026-09-24.
        quebrado = "Sulamérica Infra".encode("utf-8").decode("latin-1")
        self.assertEqual(quebrado, "SulameÌ\x81rica Infra")
        self.assertEqual(consertar_mojibake(quebrado), "Sulamérica Infra")

    def test_nome_em_nfd_sai_composto(self):
        # A Nu publica alguns nomes em NFD: certos, mas so aparecem certos se o leitor compuser.
        self.assertEqual(consertar_mojibake("AÇÕES"), "AÇÕES")

    def test_snapshot_gravado_com_mojibake_sai_limpo_na_leitura(self):
        dado = FundOffer(platform="inter", name="Ventor DÃ³lar Hedge FIC FIM").to_dict()
        self.assertEqual(FundOffer.from_dict(dado).name, "Ventor Dólar Hedge FIC FIM")

    def test_resposta_sem_charset_e_lida_como_utf8(self):
        resposta = requests.Response()
        resposta.status_code = 200
        resposta._content = "<h1>Ventor Dólar Hedge</h1>".encode("utf-8")
        resposta.headers["Content-Type"] = "text/html"
        resposta.encoding = "ISO-8859-1"  # o que o requests assume sem charset
        sessao = mock.Mock()
        sessao.get.return_value = resposta
        texto = get(sessao, "https://exemplo", Config()).text
        self.assertIn("Dólar", texto)
        self.assertNotIn("Ã", texto)


class TestContagem(unittest.TestCase):
    def test_singular_so_com_um(self):
        self.assertEqual(contagem(1, "alteração", "alterações"), "1 alteração")
        self.assertEqual(contagem(0, "saída", "saídas"), "0 saídas")
        self.assertEqual(contagem(3, "entrada", "entradas"), "3 entradas")

    def test_separa_o_milhar(self):
        # O digest de 2026-09-24 trazia "(6631 no catálogo)" e "11855 fundos na watchlist".
        self.assertEqual(contagem(6631, "fundo", "fundos"), "6.631 fundos")
        self.assertEqual(contagem(11855, "fundo", "fundos"), "11.855 fundos")


def _snapshot(platform: str, fontes: list[str]) -> ShelfSnapshot:
    fundo = FundOffer(platform=platform, name="Fundo", cnpj="11111111000111", sources=fontes)
    return ShelfSnapshot(
        platform=platform, collected_at=datetime(2026, 9, 24),
        strategy=CollectorStrategy.API_JSON, status=SnapshotStatus.OK, funds=[fundo],
    )


class TestFontes(unittest.TestCase):
    def test_so_entra_a_fonte_usada(self):
        vitrine = _snapshot("btg", [CollectorStrategy.API_JSON.value])
        report = RunReport(generated_at=datetime(2026, 9, 24), snapshots=[vitrine])
        fontes = fontes_do_relatorio(report, Config())
        self.assertEqual([f.nome for f in fontes], ["Vitrine - BTG Pactual"])
        self.assertTrue(fontes[0].url.startswith("https://investimentos.btgpactual.com"))

        report.snapshots.append(_snapshot("nu", [CollectorStrategy.OPEN_FINANCE.value]))
        report.enrichment = {"matched": 1}
        report.retail_flow = {"available": True}
        nomes = [f.nome for f in fontes_do_relatorio(report, Config())]
        self.assertIn(OPEN_FINANCE.nome, nomes)
        self.assertIn(CVM_CADASTRO.nome, nomes)
        self.assertIn(CVM_INFORME_DIARIO.nome, nomes)

    def test_digest_lista_as_fontes_com_link(self):
        from shelf_monitor.report.digest import render_html, render_markdown

        report = RunReport(
            generated_at=datetime(2026, 9, 24),
            snapshots=[_snapshot("btg", [CollectorStrategy.API_JSON.value])],
        )
        md = render_markdown(report)
        self.assertIn("## Fontes", md)
        self.assertIn(
            "[Vitrine - BTG Pactual](https://investimentos.btgpactual.com/"
            "fundos-de-investimento/produtos)", md,
        )
        html = render_html(report)
        self.assertIn('<a href="https://investimentos.btgpactual.com/', html)
