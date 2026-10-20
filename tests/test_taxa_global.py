"""Taxa global nos gaps por casa (pedido de 20/10/2026): a do site, nunca a do catalogo.

O merge da precedencia ao Open Finance, cujo `maxAdminFee` e outra conta: vem em
fracao (0,02 para 2%) e, em parte dos fundos, so com a taxa de administracao -
nas vitrines de 14/10/2026, o Itau Small Cap Valuation tinha 0,0005 no Open
Finance e 2,0% no site do Itau. Os testes travam:

- o merge guardando a taxa da vitrine antes da precedencia, e o snapshot antigo
  lendo a da divergencia que o merge registrou;
- a linha do gap com a menor taxa global entre as casas que o exibem;
- a coluna Taxa global nas tabelas de gaps do digest e na aba Gaps XP.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from shelf_monitor.merge import merge_offers
from shelf_monitor.models import KEY_TAXA_VITRINE, CollectorStrategy, FundOffer

try:
    from fixtures_comuns import temp_config
    from test_digest_v2 import A, E, _relatorio, _texto
except ImportError:  # rodando da raiz, como pacote
    from tests.fixtures_comuns import temp_config
    from tests.test_digest_v2 import A, E, _relatorio, _texto

OF = CollectorStrategy.OPEN_FINANCE.value
NAVEGADOR = CollectorStrategy.PLAYWRIGHT.value


class TestTaxaDaVitrine(unittest.TestCase):
    def test_merge_guarda_a_taxa_da_vitrine_antes_da_precedencia(self):
        catalogo = FundOffer(platform="itau", name="Small Cap", cnpj="2" * 14,
                             admin_fee_pct=0.0005)
        vitrine = FundOffer(platform="itau", name="Small Cap", cnpj="2" * 14,
                            admin_fee_pct=2.0)
        [unido] = merge_offers({OF: [catalogo], NAVEGADOR: [vitrine]})
        # No campo, o Open Finance segue vencendo; a taxa do site nao se perde.
        self.assertEqual(unido.admin_fee_pct, 0.0005)
        self.assertEqual(unido.extra[KEY_TAXA_VITRINE], 2.0)
        self.assertEqual(unido.taxa_da_vitrine, 2.0)

    def test_snapshot_antigo_le_a_divergencia_e_nao_inventa_taxa(self):
        antigo = FundOffer(
            platform="btg", name="JGP Hedge", cnpj="3" * 14, admin_fee_pct=0.02,
            sources=[OF, NAVEGADOR],
            extra={"divergencias": {"admin_fee_pct": {
                "valor_usado": 0.02, "fonte_usada": OF,
                "valor_alternativo": 2.0, "fonte_alternativa": NAVEGADOR,
            }}},
        )
        self.assertEqual(antigo.taxa_da_vitrine, 2.0)
        so_vitrine = FundOffer(platform="btg", name="X", cnpj="4" * 14, admin_fee_pct=1.5,
                               sources=[NAVEGADOR])
        self.assertEqual(so_vitrine.taxa_da_vitrine, 1.5)
        so_catalogo = FundOffer(platform="btg", name="Y", cnpj="5" * 14, admin_fee_pct=0.02,
                                sources=[OF])
        self.assertIsNone(so_catalogo.taxa_da_vitrine)


class TestTaxaGlobalNosGaps(unittest.TestCase):
    """A no BTG (2,00%) e no Itau (1,90%); E so no Itau, sem taxa no site."""

    def setUp(self):
        self.report = _relatorio(com_itau=True)
        for snapshot in self.report.snapshots:
            for oferta in snapshot.funds:
                if oferta.cnpj == A:
                    oferta.extra[KEY_TAXA_VITRINE] = {"btg": 2.0, "itau": 1.9}[snapshot.platform]

    def test_a_menor_taxa_global_entre_as_casas(self):
        from shelf_monitor.report.digest_visao import montar_visao

        linhas = {f["cnpj"]: f for f in montar_visao(self.report, None, "semanal")
                  .oportunidade["linhas"]}
        self.assertEqual(linhas[A]["taxa_global"], 1.9)
        self.assertIsNone(linhas[E]["taxa_global"])

    def test_digest_traz_a_coluna_e_a_nota(self):
        from shelf_monitor.report.digest import render_html, render_markdown

        md = render_markdown(self.report, None, "semanal")
        # Rent. 12m e Taxa global lado a lado; sem serie de cota, a rentabilidade e "-".
        self.assertRegex(md, r"\| Fundo A FIC FIM .*\| \+49 \| - \| 1,90% \| acelerando \|")
        self.assertRegex(md, r"\| Fundo E FIC FIM .*\| \+49 \| - \| - \| acelerando \|")
        self.assertIn("_Taxa global: a que o site da casa mostra para o fundo, em % ao ano", md)
        texto = _texto(render_html(self.report, None, "semanal"))
        self.assertIn("Rent. 12m Taxa global Esforço", texto)
        self.assertIn("1,90%", texto)

    def test_planilha_traz_a_taxa_global_nos_gaps(self):
        from openpyxl import load_workbook

        from shelf_monitor.report.spreadsheet import render_spreadsheet

        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        caminho = render_spreadsheet(config, self.report.snapshots,
                                     retail_flow=self.report.retail_flow)
        aba = load_workbook(caminho)["Gaps XP"]
        cabecalho = [c.value for c in aba[1]]
        coluna = cabecalho.index("Taxa global na vitrine (%)")
        por_cnpj = {
            linha[cabecalho.index("CNPJ")]: linha[coluna]
            for linha in aba.iter_rows(min_row=2, values_only=True) if linha[0]
        }
        self.assertEqual(por_cnpj["11.000.000/0001-01"], 1.9)


if __name__ == "__main__":
    unittest.main()
