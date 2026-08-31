"""Testes das listagens publicas: extracao do DOM, paginacao e diagnostico.

Os sites reais estao bloqueados neste ambiente (e duas das tres listas barram
cliente HTTP mesmo fora dele), entao aqui se exercita o que e determinavel sem
rede: o HTML renderizado vira fundos, a paginacao nao trunca em silencio, e uma
falha de TLS e classificada em vez de virar "falha de TLS" e mais nada.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from shelf_monitor.collectors import open_finance as of
from shelf_monitor.collectors.browser import extract_from_dom, looks_like_fund_name
from shelf_monitor.collectors.inter import _SITEMAP_URLS
from shelf_monitor.collectors.xp import XpCollector
from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.diagnose import _tls_cause
from shelf_monitor.models import AssetClass, CollectorStrategy, SnapshotStatus

# --- HTML de exemplo ---------------------------------------------------------

# Formato da lista de rentabilidade do Itau: tabela com colunas de retorno.
TABELA_RENTABILIDADE = """
<html><body>
<table>
  <thead><tr><th>Fundo</th><th>Classe</th><th>Aplicacao</th><th>Taxa</th><th>12 meses</th></tr></thead>
  <tbody>
    <tr>
      <td>Itau Renda Fixa Referenciado DI FIC</td><td>Renda Fixa</td>
      <td>R$ 1,00</td><td>0,30%</td><td>10,45%</td>
    </tr>
    <tr>
      <td>Kinea Chronos Multimercado FIC FIM</td><td>Multimercado</td>
      <td>R$ 5.000,00</td><td>1,90%</td><td>13,20%</td>
    </tr>
    <tr>
      <td>Itau Acoes Dividendos FIA</td><td>Acoes</td>
      <td>R$ 500,00</td><td>2,00%</td><td>-4,10%</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

# Formato de vitrine de produtos: cartoes repetidos com a mesma classe CSS.
CARTOES = """
<html><body>
<nav><a href="/fundos">Fundos de investimento</a><a href="/simule">Simule agora</a></nav>
<div class="shelf">
  <div class="fund-card">
    <h3 class="fund-card__name">BTG Pactual Digital Renda Fixa FI</h3>
    <span class="fund-card__category">Renda Fixa</span>
    <p>Aplicacao minima R$ 500,00</p>
    <a href="/fundos-de-investimento/btg-rf">Ver detalhes</a>
  </div>
  <div class="fund-card">
    <h3 class="fund-card__name">BTG Pactual Absoluto Institucional FIC FIA</h3>
    <span class="fund-card__category">Acoes</span>
    <p>Aplicacao minima R$ 25.000,00</p>
    <a href="/fundos-de-investimento/btg-absoluto">Ver detalhes</a>
  </div>
  <div class="fund-card">
    <h3 class="fund-card__name">BTG Pactual Cambial Dolar FIC FIM</h3>
    <span class="fund-card__category">Cambial</span>
    <p>Aplicacao minima R$ 1.000,00</p>
    <a href="/fundos-de-investimento/btg-cambial">Ver detalhes</a>
  </div>
</div>
</body></html>
"""

# Nem tabela nem cartao: o CNPJ no texto e a unica ancora.
CNPJ_SOLTO = """
<html><body>
<section>
  <article>
    <h2>Inter Conservador Renda Fixa FI</h2>
    <p>CNPJ: 12.345.678/0001-90</p>
  </article>
  <article>
    <h2>Inter Selection Multimercado FIC</h2>
    <p>CNPJ: 98.765.432/0001-10</p>
  </article>
</section>
</body></html>
"""

# Uma mencao isolada num rodape nao e prateleira.
RODAPE = """
<html><body>
<main><h1>Fundos de investimento</h1><p>Conheca nossas opcoes.</p></main>
<footer>
  <p>Inter Conservador Renda Fixa FI - CNPJ: 12.345.678/0001-90</p>
</footer>
</body></html>
"""


class TestFundNameHeuristic(unittest.TestCase):
    def test_accepts_real_fund_names(self):
        for name in (
            "Itau Renda Fixa Referenciado DI FIC",
            "BTG Pactual Absoluto Institucional FIC FIA",
            "XP Selection Multimercado FIC FIM",
        ):
            self.assertTrue(looks_like_fund_name(name), name)

    def test_rejects_navigation_labels(self):
        """Rotulo de menu tem token de fundo mas nao e fundo."""
        for label in (
            "Fundos de investimento",
            "Renda Fixa",
            "Simule agora",
            "Ver todos os fundos",
            "Fundos",
        ):
            self.assertFalse(looks_like_fund_name(label), label)

    def test_cnpj_alongside_relaxes_the_criterion(self):
        """Com CNPJ na mesma caixa, o nome nao precisa trazer token conhecido."""
        self.assertFalse(looks_like_fund_name("Nu Reserva Imediata"))
        self.assertTrue(looks_like_fund_name("Nu Reserva Imediata", has_cnpj=True))

    def test_a_cnpj_is_not_a_name(self):
        """Sem isso o proprio numero viraria o nome do fundo."""
        self.assertFalse(looks_like_fund_name("CNPJ: 12.345.678/0001-90", has_cnpj=True))


class TestDomExtraction(unittest.TestCase):
    def test_reads_a_rentability_table(self):
        offers = extract_from_dom("itau", TABELA_RENTABILIDADE, "https://www.itau.com.br/x")
        names = [o.name for o in offers]
        self.assertEqual(len(offers), 3, names)
        self.assertIn("Kinea Chronos Multimercado FIC FIM", names)

        kinea = next(o for o in offers if o.name.startswith("Kinea"))
        self.assertEqual(kinea.raw_category, "Multimercado")
        self.assertEqual(kinea.asset_class, AssetClass.MULTIMERCADO)
        self.assertEqual(kinea.min_initial, 5000.0)

    def test_header_row_is_not_a_fund(self):
        offers = extract_from_dom("itau", TABELA_RENTABILIDADE)
        self.assertNotIn("Fundo", [o.name for o in offers])

    def test_percent_columns_are_not_read_as_fees(self):
        """Numa lista de rentabilidade quase todo "%" e retorno, nao taxa.

        Confundir os dois e pior que nao ter o dado: o dashboard passaria a
        ranquear custo por uma coluna de desempenho.
        """
        offers = extract_from_dom("itau", TABELA_RENTABILIDADE)
        self.assertTrue(all(o.admin_fee_pct is None for o in offers))

    def test_reads_repeated_cards(self):
        offers = extract_from_dom("btg", CARTOES, "https://investimentos.btgpactual.com/x")
        names = [o.name for o in offers]
        self.assertEqual(len(offers), 3, names)

        absoluto = next(o for o in offers if "Absoluto" in o.name)
        self.assertEqual(absoluto.raw_category, "Acoes")
        self.assertEqual(absoluto.min_initial, 25000.0)
        self.assertEqual(
            absoluto.detail_url,
            "https://investimentos.btgpactual.com/fundos-de-investimento/btg-absoluto",
        )

    def test_navigation_links_do_not_become_funds(self):
        names = [o.name for o in extract_from_dom("btg", CARTOES)]
        self.assertNotIn("Fundos de investimento", names)
        self.assertNotIn("Simule agora", names)

    def test_reads_from_cnpj_in_the_text(self):
        """Sem tabela e sem cartao repetido, o CNPJ e a ancora."""
        offers = extract_from_dom("inter", CNPJ_SOLTO)
        self.assertEqual(len(offers), 2, [o.name for o in offers])
        self.assertEqual(
            {o.cnpj for o in offers}, {"12345678000190", "98765432000110"}
        )
        self.assertEqual(
            {o.name for o in offers},
            {"Inter Conservador Renda Fixa FI", "Inter Selection Multimercado FIC"},
        )

    def test_a_lone_mention_is_not_a_shelf(self):
        """Um fundo citado no rodape nao pode virar a prateleira do dia."""
        self.assertEqual(extract_from_dom("inter", RODAPE), [])

    def test_empty_page_yields_nothing(self):
        self.assertEqual(extract_from_dom("btg", "<html><body></body></html>"), [])


# Cartoes com CNPJ: cada fundo tem de sair uma vez, nao duas.
CARTOES_COM_CNPJ = """
<html><body>
<div class="shelf">
  <div class="fund-card">
    <h3 class="fund-card__name">BTG Pactual Digital Renda Fixa FI</h3>
    <p>CNPJ: 11.111.111/0001-11 - aplicacao minima R$ 500,00</p>
  </div>
  <div class="fund-card">
    <h3 class="fund-card__name">BTG Pactual Absoluto FIC FIA</h3>
    <p>CNPJ: 22.222.222/0001-22 - aplicacao minima R$ 25.000,00</p>
  </div>
  <div class="fund-card">
    <h3 class="fund-card__name">BTG Pactual Cambial Dolar FIC FIM</h3>
    <p>CNPJ: 33.333.333/0001-33 - aplicacao minima R$ 1.000,00</p>
  </div>
</div>
</body></html>
"""

# Master e feeder: nomes quase iguais, CNPJs diferentes. Sao dois fundos.
MASTER_E_FEEDER = """
<html><body><table><tbody>
  <tr><td>Kinea Chronos Master FIM</td><td>Multimercado</td><td>11.111.111/0001-11</td></tr>
  <tr><td>Kinea Chronos FIC FIM</td><td>Multimercado</td><td>22.222.222/0001-22</td></tr>
  <tr><td>Kinea Atlas Master FIA</td><td>Acoes</td><td>33.333.333/0001-33</td></tr>
</tbody></table></body></html>
"""


# Duas mencoes soltas: passam do minimo, entao servem de caso para a limpeza
# do nome (uma so seria descartada antes de chegar la).
RODAPE_DUPLO = """
<html><body><footer>
  <p>Inter Conservador Renda Fixa FI - CNPJ: 12.345.678/0001-90</p>
  <p>Inter Selection Multimercado FIC - CNPJ: 98.765.432/0001-10</p>
</footer></body></html>
"""

class TestNoDoubleCounting(unittest.TestCase):
    """Um fundo lido por duas caixas da pagina continua sendo um fundo.

    A pagina renderizada expoe o mesmo dado em niveis aninhados (o cartao e o
    titulo dentro dele) e por vias diferentes (tabela e cartao). Sem consolidar,
    a prateleira do BTG sairia com o dobro do tamanho - e o diff do dia seguinte
    leria a correcao como uma saida em massa.
    """

    def test_a_card_and_its_heading_are_one_fund(self):
        offers = extract_from_dom("btg", CARTOES_COM_CNPJ)
        self.assertEqual(len(offers), 3, [(o.name, o.cnpj) for o in offers])
        self.assertEqual(
            {o.cnpj for o in offers},
            {"11111111000111", "22222222000122", "33333333000133"},
        )

    def test_the_richer_reading_wins(self):
        """Entre a leitura com CNPJ e minimo e a sem nada, fica a com dados."""
        offers = extract_from_dom("btg", CARTOES_COM_CNPJ)
        digital = next(o for o in offers if "Digital" in o.name)
        self.assertEqual(digital.cnpj, "11111111000111")
        self.assertEqual(digital.min_initial, 500.0)

    def test_master_and_feeder_are_not_merged(self):
        """Nomes quase iguais com CNPJs distintos sao fundos distintos."""
        offers = extract_from_dom("itau", MASTER_E_FEEDER)
        self.assertEqual(len(offers), 3, [(o.name, o.cnpj) for o in offers])
        chronos = [o for o in offers if "Chronos" in o.name]
        self.assertEqual(len(chronos), 2)
        self.assertEqual(
            {o.cnpj for o in chronos}, {"11111111000111", "22222222000122"}
        )

    def test_the_cnpj_does_not_leak_into_the_name(self):
        """Numa caixa sem marcacao o nome vem grudado no numero."""
        offers = extract_from_dom("inter", RODAPE_DUPLO)
        self.assertEqual(
            {o.name for o in offers},
            {"Inter Conservador Renda Fixa FI", "Inter Selection Multimercado FIC"},
        )



class TestOpenFinancePagination(unittest.TestCase):
    """A paginacao nao pode cortar a prateleira sem dizer."""

    def setUp(self):
        self.config = Config(data_dir="/tmp/nao-usado-nestes-testes")
        self.original_get_json = of.get_json
        self.original_max_pages = of._MAX_PAGES

    def tearDown(self):
        of.get_json = self.original_get_json
        of._MAX_PAGES = self.original_max_pages

    def _serve(self, total_pages: int, per_page: int, total_records: int):
        """Finge uma instituicao que devolve `per_page` itens por pagina."""
        self.requested: list[dict] = []

        def fake_get_json(session, url, config, params=None):
            self.requested.append(params or {})
            page = (params or {}).get("page", 1)
            return {
                "data": [
                    {
                        "name": f"Fundo {page}-{i}",
                        "cnpjNumber": f"{page:07d}{i:07d}",
                    }
                    for i in range(per_page)
                ],
                "meta": {"totalPages": total_pages, "totalRecords": total_records},
            }

        of.get_json = fake_get_json

    def _client(self) -> of.OpenFinanceFunds:
        platform = self.config.platform("btg")
        platform.open_finance_base_url = "https://exemplo.invalido"
        return of.OpenFinanceFunds(self.config, platform, session=None)

    def test_records_the_effective_page_size(self):
        """O servidor que ignora o page-size pedido tem de ficar registrado."""
        self._serve(total_pages=3, per_page=10, total_records=30)
        client = self._client()
        offers = client.fetch()

        self.assertEqual(self.requested[0]["page-size"], of._PAGE_SIZE)
        self.assertEqual(client.effective_page_size, 10)
        self.assertEqual(client.total_records, 30)
        self.assertEqual(client.pages_read, 3)
        self.assertEqual(len(offers), 30)
        self.assertFalse(client.truncated)
        self.assertEqual(client.warnings, [])

    def test_truncation_is_reported_not_silent(self):
        of._MAX_PAGES = 2
        self._serve(total_pages=20, per_page=10, total_records=200)
        client = self._client()
        offers = client.fetch()

        self.assertEqual(len(offers), 20, "parou no teto, como esperado")
        self.assertTrue(client.truncated)
        self.assertEqual(len(client.warnings), 1)
        self.assertIn("truncada", client.warnings[0])
        self.assertIn("200", client.warnings[0])

    def test_a_truncated_collection_is_partial_not_ok(self):
        """Meia prateleira nao pode virar a baseline do diff de amanha."""
        of._MAX_PAGES = 2
        self._serve(total_pages=20, per_page=10, total_records=200)

        collector = XpCollector(self.config, self._client().platform)
        collector._collect_playwright = lambda: []
        snapshot = collector.collect()

        self.assertEqual(snapshot.status, SnapshotStatus.PARTIAL)
        self.assertIn("truncada", snapshot.error)


class TestPlatformSources(unittest.TestCase):
    """As URLs que o usuario indicou, e a estrategia que as alcanca."""

    def test_btg_points_at_the_product_listing(self):
        btg = PLATFORMS["btg"]
        self.assertEqual(
            btg.shelf_url,
            "https://investimentos.btgpactual.com/fundos-de-investimento/produtos",
        )
        self.assertIn(CollectorStrategy.PLAYWRIGHT, btg.strategies)

    def test_itau_points_at_the_public_rentability_list(self):
        itau = PLATFORMS["itau"]
        self.assertEqual(
            itau.shelf_url, "https://www.itau.com.br/investimentos/fundos/rentabilidade"
        )
        self.assertNotIn("personnalite", itau.shelf_url)
        self.assertIn(CollectorStrategy.PLAYWRIGHT, itau.strategies)

    def test_pages_that_block_plain_http_do_not_try_html_scrape(self):
        """XP, BTG e Itau devolvem 403 ou casca vazia a cliente HTTP simples."""
        for platform_id in ("xp", "btg", "itau"):
            self.assertNotIn(
                CollectorStrategy.HTML_SCRAPE,
                PLATFORMS[platform_id].strategies,
                platform_id,
            )

    def test_open_finance_comes_first_everywhere(self):
        """E a fonte de maior confianca; o site acrescenta, nao substitui."""
        for platform_id, platform in PLATFORMS.items():
            self.assertIn(
                CollectorStrategy.OPEN_FINANCE, platform.strategies, platform_id
            )

    def test_inter_only_keeps_the_sitemap_that_exists(self):
        """/sitemap-0.xml devolve 404 - so poluia o log a cada coleta."""
        self.assertEqual(_SITEMAP_URLS, ["https://inter.co/sitemap.xml"])


class TestTlsDiagnosis(unittest.TestCase):
    """"Falha de TLS" nao diz o que corrigir; a causa diz."""

    def test_incomplete_chain(self):
        cause = _tls_cause(
            "certificate verify failed: unable to get local issuer certificate",
            {"certificados_apresentados": 1},
            "api.openbanking.bancointer.com.br",
        )
        self.assertIn("cadeia incompleta", cause)

    def test_unknown_issuer_with_full_chain_is_not_the_same_thing(self):
        cause = _tls_cause(
            "certificate verify failed: unable to get local issuer certificate",
            {"certificados_apresentados": 3},
            "exemplo.com.br",
        )
        self.assertIn("emissor desconhecido", cause)

    def test_expired_certificate(self):
        cause = _tls_cause(
            "certificate verify failed: certificate has expired",
            {"valido_ate": "2024-01-01T00:00:00+00:00"},
            "exemplo.com.br",
        )
        self.assertIn("vencido", cause)

    def test_wrong_host(self):
        cause = _tls_cause(
            "certificate verify failed: Hostname mismatch",
            {"nomes": ["outro.com.br"]},
            "exemplo.com.br",
        )
        self.assertIn("outro nome", cause)

    def test_intercepting_proxy(self):
        cause = _tls_cause(
            "certificate verify failed: self signed certificate in certificate chain",
            {"auto_assinado": True},
            "exemplo.com.br",
        )
        self.assertIn("auto-assinado", cause)


if __name__ == "__main__":
    unittest.main()
