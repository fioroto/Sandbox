"""Vitrine do Itau: a tabela de rentabilidade de 2026-10-14 e o CNPJ pelo nome.

Trava o conserto de 2026-10-14:

- a coleta de 2026-09-28 nao achou nenhum fundo (a tabela nao carregou) e as
  de 19 a 25/09 traziam CNPJ em 27 dos 459 fundos (6%);
- o parser le todas as linhas do layout novo, sem juntar fundos distintos pelo
  slug, e confere o total que a pagina anuncia;
- o CNPJ vem do nome normalizado, contra o catalogo Open Finance da coleta e o
  cadastro da CVM, e empate nao casa;
- o merge nao desfaz a decisao do resolvedor, e o diff nao le a troca de
  identidade (slug -> CNPJ) como entrada e saida.

As fixtures em tests/fixtures/itau/ sao recortes da evidencia real: 14 das 477
linhas da tabela (data/cache/debug/itau/, 2026-10-14), os fundos do catalogo
de 2026-09-28 e as linhas do cadastro (data/market/, 2026-10-07) que disputam
esses nomes.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from shelf_monitor.cnpj_por_nome import (
    FONTE_CADASTRO,
    FONTE_CATALOGO,
    KEY_CNPJ_PENDENTE,
    KEY_CNPJ_POR_NOME,
    MOTIVO_CONFLITO,
    MOTIVO_EMPATE,
    MOTIVO_SEM_CANDIDATO,
    ResolvedorPorNome,
    palavras_do_nome,
    tem_marca_de_cotas,
)
from shelf_monitor.collectors import itau
from shelf_monitor.collectors.itau import (
    ItauCollector,
    LeituraDaTabela,
    offers_from_rentability_rows,
    parse_total,
)
from shelf_monitor.config import Config
from shelf_monitor.diff import diff_shelf
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.merge import merge_offers
from shelf_monitor.models import (
    AssetClass,
    CollectorStrategy,
    FundOffer,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import name_slug

FIXTURES = Path(__file__).parent / "fixtures" / "itau"
MARKET = FIXTURES / "market"


def _payload() -> dict:
    """O payload que `_paginate_rentability_table` entrega: 3 paginas, 14 linhas."""
    return json.loads((FIXTURES / "rentabilidade-2026-10-14.json").read_text(encoding="utf-8"))


def _catalogo() -> list[FundOffer]:
    dados = json.loads((FIXTURES / "catalogo-open-finance.json").read_text(encoding="utf-8"))
    return [
        FundOffer(platform="itau", name=f["name"], cnpj=f["cnpj"], sources=["open-finance"])
        for f in dados["fundos"]
    ]


def _por_nome(ofertas: list[FundOffer]) -> dict[str, FundOffer]:
    return {o.name: o for o in ofertas}


def _ler(market_dir: Path = MARKET, catalogo: list[FundOffer] | None = None) -> LeituraDaTabela:
    leitura = LeituraDaTabela(market_dir, _catalogo() if catalogo is None else catalogo)
    leitura.mapear("https://www.itau.com.br/x (tabela de rentabilidade)", _payload())
    return leitura


class TestTabelaDe20261014(unittest.TestCase):
    """O layout de 2026-10-14: cabecalho sem o texto dos tooltips, risco sem "risco"."""

    def test_le_todas_as_linhas_pelo_cabecalho(self):
        ofertas = offers_from_rentability_rows(_payload()["itau_rentabilidade"])
        self.assertEqual(len(ofertas), 14)

        diferenciado = _por_nome(ofertas)["Diferenciado Crédito Privado Renda Fixa"]
        self.assertEqual(diferenciado.min_initial, 1.0)
        self.assertEqual(diferenciado.admin_fee_pct, 0.45)
        self.assertEqual(diferenciado.redemption_payment_days, 1)
        self.assertEqual(diferenciado.extra["risco"], "baixo")
        self.assertEqual(diferenciado.asset_class, AssetClass.RENDA_FIXA)

        real = _por_nome(ofertas)["Real Investor Ações"]
        self.assertEqual(real.min_initial, 5_000.0)
        self.assertEqual(real.admin_fee_pct, 2.2)

    def test_slug_igual_nao_junta_fundos_distintos(self):
        """8 dos 477 nomes de 2026-10-14 colidem no slug - o parser antigo lia 469."""
        self.assertEqual(
            name_slug("Real Investor Ações"), name_slug("Real Investor Multimercado")
        )
        nomes = {o.name for o in offers_from_rentability_rows(_payload()["itau_rentabilidade"])}
        self.assertIn("Real Investor Ações", nomes)
        self.assertIn("Real Investor Multimercado", nomes)

    def test_total_da_pagina_denuncia_tabela_incompleta(self):
        payload = _payload()
        leitura = LeituraDaTabela(MARKET, [])
        leitura.mapear("u", payload)
        self.assertEqual((leitura.linhas, leitura.total), (14, 14))
        self.assertFalse(leitura.incompleta)

        payload["itau_total"] = "477"
        leitura = LeituraDaTabela(MARKET, [])
        leitura.mapear("u", payload)
        self.assertTrue(leitura.incompleta)

    def test_parse_total(self):
        self.assertEqual(parse_total("477"), 477)
        self.assertEqual(parse_total("1.234"), 1234)
        self.assertIsNone(parse_total(None))

    def test_payload_que_nao_e_a_tabela_segue_para_o_extrator_generico(self):
        leitura = LeituraDaTabela(MARKET, [])
        self.assertIsNone(leitura.mapear("https://x", {"data": []}))
        self.assertFalse(leitura.lida)


class TestNomeNormalizado(unittest.TestCase):
    """As grafias do mesmo nome viram as mesmas palavras."""

    def test_forma_juridica_e_abreviacoes(self):
        self.assertEqual(
            palavras_do_nome("Itaú Legend Renda Fixa Longo Prazo"),
            palavras_do_nome("ITAÚ LEGEND RENDA FIXA LONGO PRAZO FIF DA CIC RESP LIMITADA"),
        )
        self.assertEqual(
            palavras_do_nome("Sparta Top FIC FI Renda Fixa Cred Privado LP"),
            palavras_do_nome("SPARTA TOP FIC DE FIF RENDA FIXA CRÉDITO PRIVADO LONGO PRAZO"),
        )

    def test_indices_escritos_de_jeitos_diferentes(self):
        self.assertEqual(palavras_do_nome("IMA-B5"), palavras_do_nome("IMA B 5"))
        self.assertEqual(palavras_do_nome("S&P500"), palavras_do_nome("S&P 500"))

    def test_fundo_incentivado_e_infra(self):
        self.assertEqual(
            palavras_do_nome("Absolute Hiker Renda Fixa Infra Seleção"),
            palavras_do_nome(
                "ABSOLUTE HIKER RENDA FIXA INFRA SELEÇÃO FIF INCENTIVADO EM DEBÊNTURES DE INFRA"
            ),
        )
        self.assertIn("INFRA", palavras_do_nome("Itaú Debêntures Incentivadas CDI Mix"))

    def test_subclasse_sai_do_nome(self):
        self.assertEqual(
            palavras_do_nome("Itaú Ações Dunamis - Subclasse II"),
            palavras_do_nome("Itaú Ações Dunamis"),
        )
        # "Subclasse" sem identificador nao come a palavra seguinte.
        self.assertIn("INSTITUCIONAL", palavras_do_nome("X Subclasse Institucional"))

    def test_letra_de_classe_nao_e_artigo(self):
        self.assertNotEqual(
            palavras_do_nome("SPARTA MAX A 60 FIC"), palavras_do_nome("SPARTA MAX 60 FIC")
        )

    def test_marca_de_cotas(self):
        self.assertTrue(tem_marca_de_cotas("ITAÚ ARTAX MULTIMERCADO FIF DA CIC RESP LIMITADA"))
        self.assertTrue(tem_marca_de_cotas("REAL INVESTOR FIC DE FIF EM AÇÕES"))
        self.assertFalse(tem_marca_de_cotas("ITAÚ ARTAX MULTIMERCADO FIF RESP LIMITADA"))


class TestCnpjPorNome(unittest.TestCase):
    """O resolvedor contra o catalogo e o cadastro das fixtures."""

    @classmethod
    def setUpClass(cls):
        cls.leitura = _ler()
        cls.ofertas = _por_nome(cls.leitura.ofertas)

    def _casou(self, nome: str, cnpj: str, fonte: str) -> dict:
        oferta = self.ofertas[nome]
        self.assertEqual(oferta.cnpj, cnpj, oferta.extra.get(KEY_CNPJ_PENDENTE))
        origem = oferta.extra[KEY_CNPJ_POR_NOME]
        self.assertEqual(origem["fonte"], fonte)
        self.assertNotIn(KEY_CNPJ_PENDENTE, oferta.extra)
        return origem

    def _pendente(self, nome: str, motivo: str) -> dict:
        oferta = self.ofertas[nome]
        self.assertIsNone(oferta.cnpj)
        self.assertNotIn(KEY_CNPJ_POR_NOME, oferta.extra)
        pendencia = oferta.extra[KEY_CNPJ_PENDENTE]
        self.assertEqual(pendencia["motivo"], motivo)
        return pendencia

    def test_nome_normalizado_no_catalogo(self):
        origem = self._casou("Real Investor Ações", "10500884000105", FONTE_CATALOGO)
        self.assertEqual(origem["regra"], "nome normalizado")
        self.assertIn("REAL INVESTOR", origem["nome"])

    def test_classe_de_ativo_separa_os_homonimos_do_slug(self):
        self._casou("Real Investor Multimercado", "28911549000157", FONTE_CATALOGO)

    def test_infra_distingue_dois_fundos_da_mesma_gestora(self):
        self._casou("Absolute Hiker Renda Fixa Infra Seleção", "51323039000180", FONTE_CATALOGO)
        self._casou(
            "Absolute Hiker Renda Fixa Seleção Longo Prazo", "42814885000102", FONTE_CATALOGO
        )

    def test_nome_comercial_sem_o_ie(self):
        origem = self._casou("Itaú Index Ações Europa EUR", "36249300000156", FONTE_CATALOGO)
        self.assertEqual(origem["regra"], "nome normalizado, sem classe de ativo")

    def test_nome_comercial_sem_a_marca_da_casa(self):
        origem = self._casou("Privilege DI", "26199519000134", FONTE_CATALOGO)
        self.assertIn("marca da casa", origem["regra"])

    def test_cadastro_resolve_o_que_o_catalogo_nao_tem(self):
        # A subclasse sai do nome; o "DUNAMIS FIF MULTIMERCADO" exclusivo nao concorre.
        self._casou("Itaú Ações Dunamis - Subclasse II", "24571992000175", FONTE_CADASTRO)

    def test_empate_nao_casa(self):
        """O par FIC/master de mesmo nome: dois candidatos, nenhum CNPJ."""
        pendencia = self._pendente("Itaú Legend Renda Fixa Longo Prazo", MOTIVO_EMPATE)
        self.assertEqual(pendencia["candidatos"], ["29197055000115", "29197090000134"])

    def test_catalogo_apontando_o_master_e_empate(self):
        """O catalogo so lista o master; o cadastro tem a classe de cotas de mesmo nome.

        A vitrine vende o FIC (taxa de 2,00% na tabela, 0% no master), entao
        aceitar o candidato unico do catalogo daria o CNPJ errado.
        """
        pendencia = self._pendente("Itaú Artax Multimercado", MOTIVO_EMPATE)
        self.assertEqual(pendencia["candidatos"], ["42698615000183", "42717511000179"])

    def test_fundo_cancelado_nao_e_candidato(self):
        """O catalogo ainda lista o FIC antigo, cancelado na CVM (fundos.csv)."""
        catalogados = {o.cnpj for o in _catalogo()}
        self.assertIn("21407404000174", catalogados)
        self._casou("Itaú Global Dinâmico Plus", "26269983000150", FONTE_CATALOGO)

    def test_classe_exclusiva_nao_e_candidata(self):
        self._pendente("Essential FIDC Subclasse I", MOTIVO_SEM_CANDIDATO)

    def test_sem_candidato(self):
        self._pendente("Kinea Urano Seleção Multimercado", MOTIVO_SEM_CANDIDATO)

    def test_resumo_conta_o_que_nao_casou(self):
        resumo = self.leitura.resumo
        self.assertEqual(resumo.examinados, 14)
        self.assertEqual((resumo.pelo_catalogo, resumo.pelo_cadastro), (7, 2))
        self.assertEqual(
            (len(resumo.empates), len(resumo.sem_candidato), len(resumo.conflitos)),
            (2, 3, 0),
        )
        self.assertEqual(
            resumo.nota(),
            "CNPJ pelo nome em 9 de 14 fundos da vitrine (7 pelo catálogo Open "
            "Finance, 2 pelo cadastro da CVM); sem CNPJ: 2 empates, 3 sem "
            "candidato, 0 conflitos",
        )

    def test_conflito_tira_o_cnpj_dos_dois(self):
        """Dois nomes da vitrine no mesmo CNPJ: um casamento esta errado, nenhum fica."""
        ofertas = [
            FundOffer(platform="itau", name="Real Investor Ações"),
            FundOffer(platform="itau", name="Real Investor FIC FIA"),
            FundOffer(platform="itau", name="Privilege DI"),
        ]
        resolvedor = ResolvedorPorNome.carregar(MARKET, _catalogo(), marcas=("ITAU",))
        resumo = resolvedor.resolver(ofertas)
        self.assertEqual([o.cnpj for o in ofertas], [None, None, "26199519000134"])
        self.assertEqual(ofertas[0].extra[KEY_CNPJ_PENDENTE]["motivo"], MOTIVO_CONFLITO)
        self.assertEqual(ofertas[0].extra[KEY_CNPJ_PENDENTE]["outros"], ["Real Investor FIC FIA"])
        self.assertEqual(len(resumo.conflitos), 2)
        self.assertEqual(resumo.resolvidos, 1)

    def test_sem_base_de_mercado_so_o_catalogo_serve(self):
        with tempfile.TemporaryDirectory() as vazio:
            leitura = _ler(Path(vazio))
        ofertas = _por_nome(leitura.ofertas)
        self.assertTrue(leitura.resumo.sem_cadastro)
        self.assertIn("base de mercado ausente", leitura.resumo.nota())
        # Sem o cadastro, o fundo da subclasse nao tem onde casar...
        self.assertIsNone(ofertas["Itaú Ações Dunamis - Subclasse II"].cnpj)
        # ...e o FIC cancelado volta a disputar o nome com o atual.
        self.assertEqual(
            ofertas["Itaú Global Dinâmico Plus"].extra[KEY_CNPJ_PENDENTE]["motivo"],
            MOTIVO_EMPATE,
        )


class _PaginaFalsa:
    """O minimo de `page` do Playwright que `_paginate_rentability_table` usa."""

    def __init__(self, carrega_na_tentativa: int | None, linhas: list[list[str]]):
        self.url = "https://www.itau.com.br/investimentos/fundos/rentabilidade"
        self.carrega_na_tentativa = carrega_na_tentativa
        self.linhas = linhas
        self.tentativa = 1
        self.recargas = 0

    def evaluate(self, script: str):
        if script == itau._TOTAL_JS:
            return str(len(self.linhas) - 1)
        if self.tentativa == self.carrega_na_tentativa:
            return self.linhas
        return [self.linhas[0]]  # so o cabecalho: o gateway ainda nao entregou

    def wait_for_timeout(self, _ms: int) -> None:
        pass

    def reload(self, **_kwargs) -> None:
        self.recargas += 1
        self.tentativa += 1

    def query_selector(self, _selector: str):
        return None  # uma pagina so


class TestTabelaQueNaoCarrega(unittest.TestCase):
    """2026-09-28: HTTP 200, 1.031 respostas JSON e nenhuma linha na tabela."""

    def setUp(self):
        patcher = mock.patch.multiple(itau, _TABLE_WAIT_MS=1_000, _TABLE_POLL_MS=500)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.linhas = _payload()["itau_rentabilidade"][0]

    def test_recarrega_a_pagina_uma_vez(self):
        pagina = _PaginaFalsa(carrega_na_tentativa=2, linhas=self.linhas)
        (_url, payload), = itau._paginate_rentability_table(pagina)
        self.assertEqual(pagina.recargas, 1)
        self.assertEqual(payload["itau_tentativas"], 2)
        self.assertEqual(len(payload["itau_rentabilidade"]), 1)
        self.assertEqual(parse_total(payload["itau_total"]), 5)

    def test_desiste_com_o_motivo_depois_da_recarga(self):
        pagina = _PaginaFalsa(carrega_na_tentativa=None, linhas=self.linhas)
        (_url, payload), = itau._paginate_rentability_table(pagina)
        self.assertEqual(pagina.recargas, 1)
        self.assertEqual(payload["itau_rentabilidade"], [])

        leitura = LeituraDaTabela(MARKET, [])
        self.assertEqual(leitura.mapear("u", payload), [])
        self.assertTrue(leitura.lida)
        self.assertIn("não carregou", leitura.motivo_da_falha())


class TestColetorItau(unittest.TestCase):
    """O coletor, com o navegador substituido por quem devolve a tabela gravada."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(data_dir=self.tmp.name)
        shutil.copytree(MARKET, self.config.market_dir)
        self.coletor = ItauCollector(self.config, self.config.platform("itau"))
        self.coletor._catalogo = _catalogo()

    def _navegador(self, payload: dict):
        """Imita collect_via_browser: mapper, dedupe por identidade, releitura do DOM."""

        def falso(platform_id, url, *, payload_mapper, warnings=None, **_kwargs):
            ofertas, vistos = [], set()
            for oferta in payload_mapper(f"{url} (tabela)", payload) or []:
                if oferta.identity not in vistos:
                    vistos.add(oferta.identity)
                    ofertas.append(oferta)
            # O DOM da pagina final rele linhas da tabela, sem CNPJ - e com o CNPJ
            # resolvido a identidade (slug) delas ja nao esta entre as vistas.
            releitura = FundOffer(platform=platform_id, name="Privilege DI")
            if ofertas and releitura.identity not in vistos:
                ofertas.append(releitura)
            if not ofertas:
                raise SourceUnavailable(f"nenhum fundo identificado em {url}")
            return ofertas, [f"{url} (tabela)", f"{url} (DOM renderizado)"]

        return mock.patch("shelf_monitor.collectors.browser.collect_via_browser", falso)

    def test_cnpj_resolvido_antes_da_deduplicacao(self):
        with self._navegador(_payload()):
            ofertas = self.coletor._collect_playwright()
        nomes = [o.name for o in ofertas]
        self.assertEqual(len(ofertas), 14)
        self.assertEqual(nomes.count("Privilege DI"), 1)  # a releitura do DOM saiu
        self.assertEqual(sum(1 for o in ofertas if o.cnpj), 9)
        self.assertNotIn("DOM renderizado", " ".join(self.coletor.source_urls))
        self.assertIn("CNPJ pelo nome em 9 de 14 fundos", self.coletor.partial_reason)
        self.assertEqual(self.coletor.warnings, [])

    def test_tabela_incompleta_rebaixa_para_parcial(self):
        payload = _payload()
        payload["itau_total"] = "477"
        with self._navegador(payload):
            self.coletor._collect_playwright()
        self.assertEqual(
            self.coletor.warnings, ["tabela de rentabilidade incompleta: 14 de 477 linhas lidas"]
        )

    def test_tabela_que_nao_carregou_diz_por_que(self):
        payload = {"itau_rentabilidade": [], "itau_total": None, "itau_tentativas": 2}
        with self._navegador(payload):
            with self.assertRaises(SourceUnavailable) as erro:
                self.coletor._collect_playwright()
        self.assertIn("a tabela de rentabilidade não carregou (2 tentativas", str(erro.exception))

    def test_catalogo_da_coleta_vem_da_estrategia_open_finance(self):
        coletor = ItauCollector(self.config, self.config.platform("itau"))
        catalogo = _catalogo()
        with mock.patch(
            "shelf_monitor.collectors.platform.PlatformCollector._collect_open_finance",
            return_value=catalogo,
        ):
            self.assertIs(coletor._collect_open_finance(), catalogo)
        self.assertEqual(coletor._catalogo, catalogo)


class TestMergeRespeitaOEmpate(unittest.TestCase):
    """O merge nao casa pelo slug o fundo que o resolvedor deixou sem CNPJ."""

    def test_pendente_nao_e_absorvido_pelo_homonimo_do_slug(self):
        catalogo = FundOffer(
            platform="itau", name="Real Investor Multimercado", cnpj="28911549000157"
        )
        pendente = FundOffer(
            platform="itau", name="Real Investor Ações",
            extra={KEY_CNPJ_PENDENTE: {"motivo": MOTIVO_SEM_CANDIDATO}},
        )
        unidos = merge_offers({"open-finance": [catalogo], "playwright": [pendente]})
        self.assertEqual(len(unidos), 2)

    def test_sem_a_marca_o_slug_continua_valendo(self):
        catalogo = FundOffer(platform="itau", name="Privilege DI FIC", cnpj="26199519000134")
        site = FundOffer(platform="itau", name="Privilege DI")
        unidos = merge_offers({"open-finance": [catalogo], "playwright": [site]})
        self.assertEqual(len(unidos), 1)


AGORA = datetime(2026, 10, 14, 21, 0)
ANTES = AGORA - timedelta(days=19)


def _snap(fundos: list[FundOffer], quando: datetime) -> ShelfSnapshot:
    return ShelfSnapshot(
        platform="itau",
        collected_at=quando,
        strategy=CollectorStrategy.OPEN_FINANCE,
        status=SnapshotStatus.OK,
        funds=fundos,
        strategies=["open-finance", "playwright"],
    )


def _fundo(nome, cnpj=None, fontes=("playwright",), **kwargs) -> FundOffer:
    return FundOffer(platform="itau", name=nome, cnpj=cnpj, sources=list(fontes), **kwargs)


class TestDiffDaTrocaDeIdentidade(unittest.TestCase):
    """O fundo que ganhou (ou perdeu) o CNPJ nao entrou nem saiu da vitrine."""

    def _antes(self):
        return [
            _fundo("ITAÚ LEGEND RF LP FIF DA CIC", "29197055000115", ("open-finance",)),
            _fundo("Itaú Legend Renda Fixa Longo Prazo", admin_fee_pct=0.9),
            _fundo("Bocaina Infra 60 IPCA Infraestrutura Renda Fixa", admin_fee_pct=1.0),
            _fundo("Kinea Urano Seleção Multimercado"),
        ]

    def _agora(self, taxa_bocaina=1.0):
        return [
            _fundo(
                "ITAÚ LEGEND RF LP FIF DA CIC", "29197055000115", ("open-finance", "playwright"),
                extra={"name_variants": ["Itaú Legend Renda Fixa Longo Prazo"]},
            ),
            _fundo(
                "Bocaina Infra 60 IPCA Infraestrutura Renda Fixa", "55362588000115",
                admin_fee_pct=taxa_bocaina,
            ),
            _fundo("Kinea Urano Seleção Multimercado"),
        ]

    def test_ganhar_o_cnpj_nao_e_saida_nem_entrada(self):
        diff = diff_shelf(_snap(self._agora(), AGORA), _snap(self._antes(), ANTES))
        self.assertEqual((diff.added, diff.removed, diff.changed), ([], [], []))

    def test_perder_o_cnpj_nao_e_entrada_nem_saida(self):
        diff = diff_shelf(_snap(self._antes(), AGORA), _snap(self._agora(), ANTES))
        self.assertEqual((diff.added, diff.removed), ([], []))

    def test_mudanca_de_campo_na_troca_continua_reportada(self):
        diff = diff_shelf(_snap(self._agora(taxa_bocaina=1.2), AGORA), _snap(self._antes(), ANTES))
        self.assertEqual([c.identity for c in diff.changed], ["55362588000115"])
        self.assertEqual(diff.changed[0].changes[0].field, "admin_fee_pct")

    def test_subclasse_no_nome_nao_desfaz_a_troca(self):
        """25/09 -> 14/10: "Itau Cash Enhanced USD Multimercado" ganhou "Subclasse I"."""
        antes = [_fundo("Itau Cash Enhanced USD Multimercado")]
        agora = [
            _fundo(
                "ITAÚ CASH ENHANCED USD FIF MULTIMERCADO", "61303870000124",
                ("open-finance", "playwright"),
                extra={"name_variants": ["Itau Cash Enhanced USD Multimercado Subclasse I"]},
            )
        ]
        diff = diff_shelf(_snap(agora, AGORA), _snap(antes, ANTES))
        self.assertEqual((diff.added, diff.removed), ([], []))

    def test_registro_so_do_catalogo_nao_absorve_quem_chegou_na_vitrine(self):
        """O master do Artax ja estava no catalogo; o fundo da vitrine e novo.

        Em palavras canonicas os dois nomes sao iguais - mas o registro do
        catalogo nunca foi o fundo da vitrine, e a entrada e de verdade.
        """
        master = _fundo(
            "ITAÚ ARTAX MULTIMERCADO FIF RESP LIMITADA", "42717511000179", ("open-finance",)
        )
        antes = [master, _fundo("Kinea Urano Seleção Multimercado")]
        agora = [master, _fundo("Kinea Urano Seleção Multimercado"), _fundo("Itaú Artax Multimercado")]
        diff = diff_shelf(_snap(agora, AGORA), _snap(antes, ANTES))
        self.assertEqual([f.name for f in diff.added], ["Itaú Artax Multimercado"])

    def test_entrada_e_saida_de_verdade_continuam(self):
        agora = self._agora() + [_fundo("SPX Seahawk Seleção Infra Renda Fixa")]
        agora = [f for f in agora if f.name != "Kinea Urano Seleção Multimercado"]
        diff = diff_shelf(_snap(agora, AGORA), _snap(self._antes(), ANTES))
        self.assertEqual([f.name for f in diff.added], ["SPX Seahawk Seleção Infra Renda Fixa"])
        self.assertEqual([f.name for f in diff.removed], ["Kinea Urano Seleção Multimercado"])


if __name__ == "__main__":
    unittest.main()
