"""Testes do modulo de previdencia (shelf_monitor.previdencia).

Cada grupo trava um erro medido em 2026-09-22 que o pipeline de fundos
cometeria sobre uma vitrine de previdencia, ou uma propriedade de que o modulo
de fundos depende e que os ganchos novos nao podem mudar:

- o eixo grosso colapsava num balde "Previdencia" so;
- a regra de varejo tirava a vitrine inteira da analise (todo FIE e exclusivo);
- o resolvedor por nome escolhia classe de varejo para fundo de previdencia;
- o catalogo do Itau lista o mesmo veiculo duas vezes e mistura previdencia com
  o catalogo geral;
- os ganchos compartilhados, sem argumento, fazem exatamente o de antes.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from shelf_monitor.classify import (
    KEY_CATEGORIA,
    ClasseOficial,
    ClassificationIndex,
)
from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.enrich import KEY_EXCLUSIVO, KEY_PUBLICO
from shelf_monitor.models import (
    AssetClass,
    CollectorStrategy,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import name_slug
from shelf_monitor.previdencia.coletores import COLETORES, construir_coletor
from shelf_monitor.previdencia.coletores import btg as coletor_btg
from shelf_monitor.previdencia.coletores import itau as coletor_itau
from shelf_monitor.previdencia.coletores import xp as coletor_xp
from shelf_monitor.previdencia.coletores.sem_vitrine import SemVitrineCollector
from shelf_monitor.previdencia.config import (
    PLATAFORMAS,
    ROTULOS_PROPRIOS,
    ConfigPrevidencia,
)
from shelf_monitor.previdencia.eixo import (
    KEY_EXCLUSIVO_VEICULO,
    KEY_NOME_REGISTRO,
    KEY_PUBLICO_VEICULO,
    IndicePrevidencia,
    ajustar_previdencia,
    familias_declaradas,
    rotulo_grosso,
    sem_prefixo_previdencia,
)
from shelf_monitor.profile import restriction_reason
from shelf_monitor.report.competitive import KEY_CATEGORIA_GROSSA, coarse_category
from shelf_monitor.report.shared import rotulo_da_casa, rotulos_sobrepostos

RAIZ = Path(__file__).resolve().parent.parent


def _snapshot(platform: str, fundos: list[FundOffer]) -> ShelfSnapshot:
    return ShelfSnapshot(
        platform=platform,
        collected_at=datetime(2026, 9, 22, 10, 0),
        strategy=CollectorStrategy.API_JSON,
        status=SnapshotStatus.OK,
        funds=fundos,
        strategies=[CollectorStrategy.API_JSON.value],
    )


def _classe(denominacao: str, categoria: str, cnpj: str | None = None, **kw) -> ClasseOficial:
    slug = name_slug(denominacao)
    return ClasseOficial(
        cnpj=cnpj, slug=slug, tokens=frozenset(slug.split("-")), categoria=categoria, **kw
    )


# --- configuracao e independencia --------------------------------------------


class TestConfigPrevidencia(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name)

    def test_snapshots_e_relatorios_ficam_em_pasta_propria(self):
        config = ConfigPrevidencia(data_dir=self._tmp.name)
        self.assertEqual(config.snapshots_dir, self.raiz / "previdencia" / "snapshots")
        self.assertEqual(config.reports_dir, self.raiz / "previdencia" / "reports")
        self.assertEqual(
            config.platform_snapshots_dir("btg"),
            self.raiz / "previdencia" / "snapshots" / "btg",
        )

    def test_base_de_mercado_e_cache_sao_os_do_modulo_de_fundos(self):
        previdencia = ConfigPrevidencia(data_dir=self._tmp.name)
        fundos = Config(data_dir=self._tmp.name)
        self.assertEqual(previdencia.market_dir, fundos.market_dir)
        self.assertEqual(previdencia.cache_dir, fundos.cache_dir)
        self.assertEqual(previdencia.cvm_raw_dir, fundos.cvm_raw_dir)

    def test_config_de_fundos_continua_com_as_pastas_e_a_identidade_de_antes(self):
        fundos = Config(data_dir=self._tmp.name)
        self.assertEqual(fundos.snapshots_dir, self.raiz / "snapshots")
        self.assertEqual(fundos.reports_dir, self.raiz / "reports")
        self.assertEqual(fundos.titulo_relatorio, "Monitor de Prateleiras de Fundos")
        self.assertEqual(fundos.prefixo_arquivo, "prateleiras")
        self.assertEqual(fundos.rotulo_assunto, "Monitor de fundos")

    def test_casas_mantem_os_ids_do_modulo_de_fundos(self):
        # Mesmo id = mesma cor no grafico, mesma ordem canonica e a XP como
        # baseline (aggregate procura o baseline em PLATFORMS pelo id).
        self.assertEqual(set(PLATAFORMAS), set(PLATFORMS))
        self.assertTrue(PLATFORMS["xp"].is_baseline)

    def test_platform_devolve_copia_da_vitrine_de_previdencia(self):
        config = ConfigPrevidencia(data_dir=self._tmp.name)
        itau = config.platform("itau")
        self.assertIn("privatebank", itau.shelf_url)
        itau.shelf_url = "https://outro"
        self.assertIn("privatebank", config.platform("itau").shelf_url)

    def test_toda_casa_declara_estrategia(self):
        # BaseCollector le strategies[0] para o snapshot; lista vazia quebrava.
        for platform_id, cfg in PLATAFORMAS.items():
            with self.subTest(platform_id=platform_id):
                self.assertTrue(cfg.strategies)


class TestRotulos(unittest.TestCase):
    def test_itau_sai_como_private_so_durante_o_bloco(self):
        self.assertEqual(ROTULOS_PROPRIOS, {"itau": "Itaú Private"})
        self.assertEqual(rotulo_da_casa("itau"), PLATFORMS["itau"].label)
        with rotulos_sobrepostos(ROTULOS_PROPRIOS):
            self.assertEqual(rotulo_da_casa("itau"), "Itaú Private")
            self.assertEqual(rotulo_da_casa("btg"), PLATFORMS["btg"].label)
        self.assertEqual(rotulo_da_casa("itau"), PLATFORMS["itau"].label)

    def test_rotulo_volta_mesmo_com_excecao(self):
        with self.assertRaises(RuntimeError):
            with rotulos_sobrepostos({"itau": "Itaú Private"}):
                raise RuntimeError("falha no meio da execução")
        self.assertEqual(rotulo_da_casa("itau"), PLATFORMS["itau"].label)


# --- eixo de categoria --------------------------------------------------------


class TestEixo(unittest.TestCase):
    def test_as_quatro_familias_da_previdencia(self):
        casos = {
            "Previdência Multimercado Livre": "Multimercados",
            "Previdência RF Duração Livre Crédito Liv": "Renda Fixa",
            "Previdência Ações Ativo": "Ações",
            "Previdência Balanceados de 30-49": "Balanceados",
        }
        for categoria, esperado in casos.items():
            with self.subTest(categoria=categoria):
                self.assertEqual(rotulo_grosso(categoria), esperado)

    def test_fie_com_rotulo_de_varejo_cai_no_mesmo_balde(self):
        # ~4% da vitrine do BTG sao FIEs que o cadastro rotula sem o prefixo.
        self.assertEqual(
            rotulo_grosso("Multimercados Livre"),
            rotulo_grosso("Previdência Multimercado Livre"),
        )
        self.assertEqual(
            rotulo_grosso("Renda Fixa Duração Livre Crédito Livre"),
            rotulo_grosso("Previdência RF Duração Livre Crédito Liv"),
        )

    def test_sem_prefixo_expande_rf_e_preserva_o_resto(self):
        self.assertEqual(
            sem_prefixo_previdencia("Previdência RF Indexados"), "Renda Fixa Indexados"
        )
        self.assertEqual(sem_prefixo_previdencia("Multimercados Livre"), "Multimercados Livre")
        self.assertIsNone(sem_prefixo_previdencia(None))

    def test_eixo_grosso_le_a_chave_quando_ela_existe(self):
        fundo = FundOffer(platform="btg", name="X Prev")
        fundo.extra[KEY_CATEGORIA] = "Previdência Multimercado Livre"
        # Sem a chave - o modulo de fundos -, o comportamento de sempre.
        self.assertEqual(coarse_category(fundo), "Previdência")
        fundo.extra[KEY_CATEGORIA_GROSSA] = "Multimercados"
        self.assertEqual(coarse_category(fundo), "Multimercados")

    def test_familias_declaradas_no_nome(self):
        self.assertEqual(familias_declaradas("Private Prev RF"), ["Renda Fixa"])
        self.assertEqual(familias_declaradas("Kinea XTR II Prev PVT MM"), ["Multimercados"])
        self.assertEqual(familias_declaradas("Private Prev Dunamis Ações"), ["Ações"])
        self.assertEqual(familias_declaradas("Platinum III"), [])


class TestAjustePrevidencia(unittest.TestCase):
    def _fie(self, **extra) -> FundOffer:
        fundo = FundOffer(platform="btg", name="Forpus 70 Prev FIM RL", cnpj="34474917000140")
        fundo.extra.update(extra)
        return fundo

    def test_marca_de_veiculo_deixa_de_excluir_o_fundo_da_vitrine(self):
        fundo = self._fie(**{KEY_EXCLUSIVO: "S", KEY_PUBLICO: "Profissional"})
        self.assertEqual(restriction_reason(fundo), "exclusivo no registro CVM")

        contagem = ajustar_previdencia([_snapshot("btg", [fundo])])

        self.assertIsNone(restriction_reason(fundo))
        self.assertEqual(fundo.extra[KEY_EXCLUSIVO_VEICULO], "S")
        self.assertEqual(fundo.extra[KEY_PUBLICO_VEICULO], "Profissional")
        self.assertEqual(contagem["marcas_de_veiculo_neutralizadas"], 1)

    def test_restricao_do_plano_continua_valendo(self):
        # Publico profissional declarado pela casa e do PLANO: continua cortando.
        fundo = self._fie(**{KEY_EXCLUSIVO: "S", "publico": "Investidor Profissional"})
        ajustar_previdencia([_snapshot("xp", [fundo])])
        self.assertIsNotNone(restriction_reason(fundo))

    def test_categoria_oficial_fica_inteira_e_o_eixo_ganha_a_familia(self):
        # O custo busca a mediana do mercado pelo rotulo completo.
        fundo = self._fie(**{KEY_CATEGORIA: "Previdência RF Duração Livre Grau de Inv"})
        ajustar_previdencia([_snapshot("btg", [fundo])])
        self.assertEqual(fundo.extra[KEY_CATEGORIA], "Previdência RF Duração Livre Grau de Inv")
        self.assertEqual(fundo.extra[KEY_CATEGORIA_GROSSA], "Renda Fixa")
        self.assertEqual(coarse_category(fundo), "Renda Fixa")

    def test_classe_de_ativo_segue_a_categoria_sem_prefixo(self):
        fundo = self._fie(**{KEY_CATEGORIA: "Previdência Ações Ativo"})
        fundo.asset_class = AssetClass.PREVIDENCIA
        ajustar_previdencia([_snapshot("btg", [fundo])])
        self.assertEqual(fundo.asset_class, AssetClass.ACOES)

    def test_fundo_sem_categoria_nao_ganha_eixo(self):
        fundo = self._fie()
        ajustar_previdencia([_snapshot("btg", [fundo])])
        self.assertNotIn(KEY_CATEGORIA_GROSSA, fundo.extra)


# --- resolvedor por nome ------------------------------------------------------


class TestIndicePrevidencia(unittest.TestCase):
    def test_rejeita_a_classe_de_varejo_que_o_resolvedor_de_fundos_escolhe(self):
        # O caso medido: "Itau Flexprev Platinum III Renda Fixa" saia como
        # "Multimercados Invest. no Exterior" pelo resolvedor de fundos.
        classes = [
            _classe("PLATINUM III RENDA FIXA", "Multimercados Invest. no Exterior"),
            _classe(
                "ITAU FLEXPREV PLATINUM III RENDA FIXA",
                "Previdência RF Duração Livre Grau de Inv",
            ),
        ]
        oferta = FundOffer(platform="itau", name="Platinum III Renda Fixa")

        fundos = ClassificationIndex(classes).resolve(oferta)
        previdencia = IndicePrevidencia(classes).resolve(oferta)

        self.assertEqual(fundos.categoria, "Multimercados Invest. no Exterior")
        self.assertEqual(previdencia.categoria, "Previdência RF Duração Livre Grau de Inv")

    def test_tenta_o_nome_de_registro_antes_do_comercial(self):
        classes = [
            _classe(
                "ITAU FLEXPREV EXCELLENCE RENDA FIXA FIC",
                "Previdência RF Duração Livre Grau de Inv",
            ),
            _classe("PRIVATE PREV RF ATIVO", "Previdência RF Duração Livre Crédito Liv"),
        ]
        oferta = FundOffer(platform="itau", name="Private Prev RF")
        oferta.extra[KEY_NOME_REGISTRO] = "Itau Flexprev Excellence RF FIC"
        resolucao = IndicePrevidencia(classes).resolve(oferta)
        self.assertEqual(resolucao.categoria, "Previdência RF Duração Livre Grau de Inv")

    def test_empate_na_mesma_familia_devolve_a_familia_e_nao_previdencia(self):
        classes = [
            _classe("ALFA PREV FIC", "Previdência RF Duração Livre Grau de Inv"),
            _classe("ALFA PREV FIC", "Previdência RF Duração Baixa Grau de Inv"),
        ]
        resolucao = IndicePrevidencia(classes).resolve(
            FundOffer(platform="itau", name="Alfa Prev FIC")
        )
        self.assertEqual(resolucao.categoria, "Renda Fixa")
        self.assertTrue(resolucao.via.endswith("-n1"))
        # O de fundos, no mesmo empate, devolvia o nivel 1 que nao diz nada.
        fundos = ClassificationIndex(classes).resolve(
            FundOffer(platform="itau", name="Alfa Prev FIC")
        )
        self.assertEqual(fundos.categoria, "Previdência")


# --- coletores: BTG ----------------------------------------------------------

_ITEM_BTG = {
    "product": "Forpus 70 Prev FIM RL",
    "fundName": "FORPUS 70 PREV FIM",
    "cnpj": "34474917000140",
    "classCNPJ": None,
    "categoryBTG": "Renda Variável",
    "subcategoryBTG": "Long Only",
    "riskName": "MODERADO",
    "minimumInitialInvestment": 1000,
    "netEquity": 15200604.12,
    "nmTaxationPursued": "Longo Prazo",
    "issuerName": "FORPUS CAPITAL GESTAO DE RECURSOS LTDA.",
    "benchmarkName": "CDI",
    "applyOpenPortal": True,
    "restrictedOrExclusive": True,
    "isExclusiveFund": False,
    "isExclusiveFundPortal": False,
    "investmentType": "Investidores profissionais",
    "pension": {
        "partner": "KIPREV",
        "iAdhesionBlocked": False,
        "isContributionBlocked": False,
        "susepCodes": [
            {"planType": "PG", "susepCode": "15414900505201819"},
            {"planType": "VG", "susepCode": "15414900506201863"},
            {"planType": "PG", "susepCode": "15414900505201819"},
        ],
    },
    "detail": {
        "administrator": "BNY MELLON SERVICOS FINANCEIROS DTVM S/A",
        "manager": "FORPUS CAPITAL GESTAO DE RECURSOS LTDA.",
        "administrationFee": 2,
        "administrationFeeType": "PERCENTAGE",
        "performanceFee": 20,
        "rescueQuota": "D+8",
        "rescuefinancialSettlement": "D+10",
        "anbimaRating": "Previdência Multimercados",
    },
}


class TestColetorBtg(unittest.TestCase):
    def test_item_mapeado_como_a_pagina_exibe(self):
        oferta = coletor_btg.offer_from_item(_ITEM_BTG)
        self.assertEqual(oferta.name, "Forpus 70 Prev FIM RL")
        self.assertEqual(oferta.extra[KEY_NOME_REGISTRO], "FORPUS 70 PREV FIM")
        self.assertEqual(oferta.cnpj, "34474917000140")
        self.assertEqual(oferta.admin_fee_pct, 2.0)
        self.assertEqual(oferta.performance_fee.amount_pct, 20.0)
        self.assertEqual(oferta.redemption_quotation_days, 8)
        self.assertEqual(oferta.redemption_payment_days, 10)
        self.assertEqual(oferta.min_initial, 1000.0)
        self.assertEqual(oferta.manager_name, "FORPUS CAPITAL GESTAO DE RECURSOS LTDA.")
        self.assertEqual(oferta.extra["seguradora"], "KIPREV")
        self.assertEqual(oferta.extra["planos"], ["PGBL", "VGBL"])
        self.assertEqual(oferta.asset_class, AssetClass.MULTIMERCADO)
        self.assertFalse(oferta.closed_for_investment)

    def test_marcas_de_veiculo_nao_viram_restricao(self):
        # 146/226 restrito e 212/226 "profissionais": descrevem o FIE.
        oferta = coletor_btg.offer_from_item(_ITEM_BTG)
        self.assertNotIn("restrito_casa", oferta.extra)
        self.assertNotIn("publico", oferta.extra)
        self.assertTrue(oferta.extra["restrito_veiculo_casa"])
        self.assertEqual(oferta.extra["publico_veiculo_casa"], "Investidores profissionais")
        self.assertIsNone(restriction_reason(oferta))

    def test_taxa_que_nao_e_percentual_fica_sem_valor(self):
        item = {**_ITEM_BTG, "detail": {**_ITEM_BTG["detail"], "administrationFeeType": "MIXED"}}
        oferta = coletor_btg.offer_from_item(item)
        self.assertIsNone(oferta.admin_fee_pct)
        self.assertEqual(oferta.extra["taxa_unidade_casa"], "MIXED")

    def test_adesao_bloqueada_fecha_o_fundo(self):
        item = {**_ITEM_BTG, "pension": {**_ITEM_BTG["pension"], "iAdhesionBlocked": True}}
        self.assertTrue(coletor_btg.offer_from_item(item).closed_for_investment)

    def test_payload_fora_do_formato_vira_lista_vazia(self):
        self.assertEqual(coletor_btg.parse_payload({"items": []}), [])
        self.assertEqual(len(coletor_btg.parse_payload([_ITEM_BTG, {"sem": "nome"}])), 1)


# --- coletores: XP -----------------------------------------------------------

_ITEM_XP = {
    "name": "Spx Long Bias Xp Seguros Prev Fic Fim Rl",
    "cnpj": "40526764000130",
    "code": "1910112",
    "fundManager": "XP Vida e Previdência",
    "fundAdministrator": "XP Investimentos",
    "targetAudience": "Investidor Qualificado",
    "qualifiedInvestor": True,
    "netEquity": 66251703.33,
    "averageNetEquity12M": 65155072.24,
    "redemptionQuotationDays": 30,
    "redemptionSettlementDays": 2,
    "benchmark": {"name": "IPCA + Yield IMA-B"},
    "classifications": {
        "cvm": "Multimercado",
        "xp": {"description": "Renda Variável Long Biased", "code": 13},
        "riskGeniusDescription": "Médio",
    },
    "investmentConditions": {
        "administrationRate": 2.0,
        "maxAdministrationRate": 2.0,
        "minimalInitialInvestment": 100.0,
    },
    "brands": [{"brandType": 3, "fundingBlocked": False}],
    "plans": [
        {"insurer": {"name": "XP Seguros"}, "modality": 0},
        {"insurer": {"name": "XP Seguros"}, "modality": 1},
    ],
}


class TestColetorXp(unittest.TestCase):
    def test_item_da_api_mapeado(self):
        oferta = coletor_xp.offer_from_item(_ITEM_XP)
        self.assertEqual(oferta.cnpj, "40526764000130")
        self.assertEqual(oferta.admin_fee_pct, 2.0)
        self.assertEqual(oferta.min_initial, 100.0)
        self.assertEqual(oferta.redemption_quotation_days, 30)
        self.assertEqual(oferta.extra["seguradora"], "XP Seguros")
        self.assertEqual(oferta.extra["benchmark"], "IPCA + Yield IMA-B")
        self.assertEqual(oferta.raw_category, "Renda Variável Long Biased")
        self.assertFalse(oferta.closed_for_investment)
        # Qualificado nao e motivo de corte (profile.py).
        self.assertIsNone(restriction_reason(oferta))

    def test_aporte_bloqueado_fecha_e_plano_profissional_restringe(self):
        item = {
            **_ITEM_XP,
            "brands": [{"brandType": 3, "fundingBlocked": True}],
            "targetAudience": "Investidor Profissional",
        }
        oferta = coletor_xp.offer_from_item(item)
        self.assertTrue(oferta.closed_for_investment)
        self.assertIsNotNone(restriction_reason(oferta))

    def test_payload_da_api(self):
        ofertas = coletor_xp.parse_payload(
            {"investmentFunds": [_ITEM_XP, {"cnpj": "1"}], "profileLimitRiskGenio": []}
        )
        self.assertEqual([o.name for o in ofertas], [_ITEM_XP["name"]])
        self.assertEqual(coletor_xp.parse_payload([]), [])

    def test_cards_no_layout_de_linhas_com_title(self):
        html = """<html><body>
          <div class="funds-table-row"><div><div title="Mag Cash Xp Seg Prev Ficfirf Rl">Mag Cash
            Xp Seg Prev Ficfirf Rl</div></div><span>PGBL/VGBL</span><span>R$ 100,00</span>
            <span title="Fechado para captação">x</span></div>
          <div class="funds-table-row"><div title="Occam Liquidez XP Seg Prev FIC FIRF CP RL">
            Occam</div></div>
        </body></html>"""
        nomes = [o.name for o in coletor_xp.parse_cards(html)]
        self.assertEqual(
            nomes, ["Mag Cash Xp Seg Prev Ficfirf Rl", "Occam Liquidez XP Seg Prev FIC FIRF CP RL"]
        )
        self.assertIsNone(coletor_xp.total_declarado(html))

    def test_cards_no_layout_de_colunas_sem_o_rotulo_responsivo(self):
        # O rotulo "223 fundos" de cada linha e escondido por CSS; o
        # BeautifulSoup o le mesmo assim e ele nao pode colar no nome.
        html = """<html><body>
          <header class="xp-flex-header"><div class="name-column">223 fundos</div></header>
          <div class="xp-flex-body"><div class="name-column">
            <div class="xp-flex-body__column__title">223 fundos</div>
            <div class="xp-flex-body__column__content">XP Icatu Horizonte Prev FIC FIRF</div>
          </div></div>
        </body></html>"""
        self.assertEqual(
            [o.name for o in coletor_xp.parse_cards(html)], ["XP Icatu Horizonte Prev FIC FIRF"]
        )
        self.assertEqual(coletor_xp.total_declarado(html), 223)


# --- coletores: Itau Private -------------------------------------------------


def _linha_itau(nome: str, numero: int, cota: str, pl: str = "-", taxa: str = "0,5%",
                status: str = "A") -> str:
    retornos = "".join(f"<td>{v}</td>" for v in ("0,44", "1,14", "8,66", "12,85", "26,06", "38,13"))
    return (
        f'<tr><td class="tabLinhaBrancae">{nome}<sup>({numero})</sup></td>'
        f"<td>10/09</td><td>{cota}</td><td>-0,0207</td>"
        f"<td><table><tr>{retornos}</tr></table></td>"
        f"<td>{pl}</td><td>{taxa}</td><td>{status}</td></tr>"
    )


def _secao(titulo: str) -> str:
    return f'<tr><td colspan="15" class="tabLinhaTitulo">{titulo}</td></tr>'


# O catalogo real em miniatura: bloco 1 (catalogo geral) e bloco 2 (a
# sequencia de secoes recomeca - previdencia), gemeo pela cota entre os blocos,
# duplicata pela cota dentro do bloco 2, plano sem marca de previdencia no nome,
# e um indicador numa tabela solta. Sem charset no HTML, em cp1252, como la.
_CATALOGO_ITAU = (
    "<html><body><table>"
    + _secao("Juros Pós-Fixados")
    + _linha_itau("Itaú Flexprev Excellence RF FIC", 1, "42,831539", "2.676,8", "0,8%")
    + _linha_itau("Kinea Renda Fixa Absoluto Longo Prazo", 2, "21,208225")
    + _secao("Multimercados")
    + _linha_itau("Itaú Precision Advanced Multimercado CP", 3, "21,804627")
    + _secao("Juros Pós-Fixados")
    + _linha_itau("Flexprev Private DI", 4, "45,407981", "424,2", "1,0%", "F")
    + _linha_itau("Platinum III", 5, "35,899427")
    + _secao("PREVIDÊNCIA - Renda Fixa")
    + _linha_itau("Private Prev RF", 6, "42,831539", "2.676,8", "0,8%")
    + _secao("Inflação")
    + _linha_itau("Private Prev Ultra NTN-B 2030", 7, "32,541250", "78,9", "-")
    + _linha_itau("Private Prev NTNB Ultra 2030", 8, "32,541250", "78,9", "0,5%")
    + "</table>"
    + "<table><tr><td>IGPM ( FGV )<sup>(9)</sup></td><td>01/08</td><td>-</td>"
    + "<td>-0,22</td><td>- -0,22 1,85</td><td></td></tr></table>"
    + "</body></html>"
).encode("cp1252")


class TestColetorItau(unittest.TestCase):
    def setUp(self):
        html = coletor_itau.decodificar(_CATALOGO_ITAU, "ISO-8859-1")
        self.ofertas, self.avisos = coletor_itau.parse_catalogo(html)
        self.por_nome = {o.name: o for o in self.ofertas}

    def test_vitrine_e_o_bloco_de_previdencia_pela_estrutura(self):
        # "Platinum III" nao tem marca de previdencia no nome e entra; os fundos
        # do catalogo geral e o indicador da tabela solta nao entram.
        self.assertEqual(
            list(self.por_nome),
            ["Flexprev Private DI", "Platinum III", "Private Prev RF", "Private Prev Ultra NTN-B 2030"],
        )
        self.assertEqual(self.avisos, [])

    def test_mesmo_veiculo_listado_duas_vezes_vira_um(self):
        oferta = self.por_nome["Private Prev Ultra NTN-B 2030"]
        self.assertEqual(oferta.extra["nomes_alternativos"], ["Private Prev NTNB Ultra 2030"])

    def test_nome_de_registro_vem_do_gemeo_no_catalogo_geral(self):
        self.assertEqual(
            self.por_nome["Private Prev RF"].extra[KEY_NOME_REGISTRO],
            "Itaú Flexprev Excellence RF FIC",
        )
        self.assertNotIn(KEY_NOME_REGISTRO, self.por_nome["Platinum III"].extra)

    def test_acentos_decodificados_pelo_cabecalho_http(self):
        secoes = {o.raw_category for o in self.ofertas}
        self.assertIn("Inflação", secoes)
        self.assertIn("PREVIDÊNCIA - Renda Fixa", secoes)

    def test_campos_da_linha(self):
        di = self.por_nome["Flexprev Private DI"]
        self.assertTrue(di.closed_for_investment)
        self.assertEqual(di.admin_fee_pct, 1.0)
        self.assertEqual(di.extra["pl_medio_12m"], 424_200_000.0)
        self.assertEqual(di.extra["rentabilidade"]["12m"], 12.85)
        self.assertEqual(di.asset_class, AssetClass.RENDA_FIXA)
        self.assertIsNone(di.cnpj)

    def test_mesma_cota_com_retornos_diferentes_nao_e_o_mesmo_veiculo(self):
        # Um erro de pagina que repetisse a cota nao pode fundir dois produtos.
        linha_a = _linha_itau("Private Prev Alfa", 2, "20,720489")
        linha_b = _linha_itau("Private Prev Beta", 3, "20,720489").replace(
            "<td>12,85</td>", "<td>6,22</td>"
        )
        html = (
            "<table>" + _secao("Juros Pós-Fixados")
            + _linha_itau("Catálogo Geral RF", 1, "10,000000")
            + _secao("Juros Pós-Fixados") + linha_a + linha_b + "</table>"
        )
        ofertas, _ = coletor_itau.parse_catalogo(html)
        self.assertEqual([o.name for o in ofertas], ["Private Prev Alfa", "Private Prev Beta"])

    def test_sem_bloco_estrutural_filtra_pelo_nome_e_avisa(self):
        html = (
            "<table>" + _secao("Juros Pós-Fixados")
            + _linha_itau("Itaú Flexprev Excellence RF FIC", 1, "42,831539")
            + _linha_itau("Kinea Renda Fixa Absoluto", 2, "21,208225")
            + "</table>"
        )
        ofertas, avisos = coletor_itau.parse_catalogo(html)
        self.assertEqual([o.name for o in ofertas], ["Itaú Flexprev Excellence RF FIC"])
        self.assertEqual(len(avisos), 1)


class TestColetorItauSemMerge(unittest.TestCase):
    """O caso medido ao vivo em 2026-09-22: a vitrine saiu com 40 e nao 41."""

    def test_nomes_que_so_diferem_em_rf_e_mm_nao_sao_fundidos(self):
        # O slug descarta RF e MM: os dois nomes viram `private-prev-vision-plus`,
        # e o merge por identidade fundia dois fundos diferentes.
        html = (
            "<table>" + _secao("Juros Pós-Fixados")
            + _linha_itau("Catálogo Geral RF", 1, "10,000000")
            + _secao("Juros Pós-Fixados")
            + _linha_itau("Private Prev Rf Vision Plus", 2, "20,720489")
            + _secao("Multimercados")
            + _linha_itau("Private Prev Vision Plus MM", 3, "185,977719")
            + "</table>"
        ).encode("cp1252")
        self.assertEqual(
            FundOffer(platform="itau", name="Private Prev Rf Vision Plus").identity,
            FundOffer(platform="itau", name="Private Prev Vision Plus MM").identity,
        )

        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigPrevidencia(data_dir=tmp)
            coletor = construir_coletor(config, "itau")
            resposta = mock.Mock(content=html, encoding="ISO-8859-1")
            with mock.patch.object(coletor_itau, "get", return_value=resposta):
                snapshot = coletor.collect()

        self.assertEqual(snapshot.status, SnapshotStatus.OK)
        self.assertEqual(
            sorted(f.name for f in snapshot.funds),
            ["Private Prev Rf Vision Plus", "Private Prev Vision Plus MM"],
        )
        self.assertEqual(snapshot.source_counts, {"html-scrape": 2})
        self.assertTrue(all(f.on_listing for f in snapshot.funds))


class TestCustoPrevidencia(unittest.TestCase):
    def _classes(self, categoria: str, quantas: int, **kw) -> dict:
        from shelf_monitor.report.custo import CustoClasse

        hoje = datetime.now().date()
        return {
            f"{categoria[:3]}{i:011d}": CustoClasse(
                cnpj=f"{categoria[:3]}{i:011d}", categoria=categoria,
                taxa_adm=0.5 + i / 100, taxa_percentual=True, data_base=hoje, **kw,
            )
            for i in range(quantas)
        }

    def test_classe_exclusiva_de_previdencia_serve_de_referencia(self):
        from shelf_monitor.previdencia.custo import IndiceCustoPrevidencia
        from shelf_monitor.report.custo import IndiceCusto

        classes = self._classes(
            "Previdência Multimercado Livre", 25, exclusivo=True, publico_alvo="PROFISSIONAL"
        )
        self.assertNotIn("Previdência Multimercado Livre", IndiceCusto(classes).medianas)
        self.assertIn(
            "Previdência Multimercado Livre", IndiceCustoPrevidencia(classes).medianas
        )

    def test_taxa_zero_e_categoria_de_varejo_seguem_a_regra_de_sempre(self):
        from shelf_monitor.previdencia.custo import como_referencia

        from dataclasses import replace

        zero = next(iter(self._classes("Previdência Ações Ativo", 1, exclusivo=True).values()))
        zero = replace(zero, taxa_adm=0.0)
        self.assertFalse(como_referencia(zero).serve_de_referencia)

        varejo = next(iter(self._classes("Multimercados Livre", 1, exclusivo=True).values()))
        self.assertTrue(como_referencia(varejo).restrita)


class TestColetoresRegistro(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = ConfigPrevidencia(data_dir=self._tmp.name)

    def test_toda_casa_tem_coletor(self):
        self.assertEqual(set(COLETORES), set(PLATAFORMAS))
        self.assertIsInstance(
            construir_coletor(self.config, "btg"), coletor_btg.BtgPrevidenciaCollector
        )
        with self.assertRaises(KeyError):
            construir_coletor(self.config, "safra")

    def test_casa_sem_vitrine_sai_indisponivel_com_o_motivo_sem_rede(self):
        coletor = construir_coletor(self.config, "nu")
        self.assertIsInstance(coletor, SemVitrineCollector)
        with mock.patch.object(coletor.session, "get") as get:
            snapshot = coletor.collect()
        get.assert_not_called()
        self.assertEqual(snapshot.status, SnapshotStatus.UNAVAILABLE)
        self.assertIn("previdência", snapshot.error)


# --- pipeline ponta a ponta, offline -----------------------------------------


class TestPipelinePrevidencia(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = ConfigPrevidencia(data_dir=self._tmp.name)
        self.config.ensure_dirs()

    def _vitrines(self) -> list[ShelfSnapshot]:
        btg = coletor_btg.parse_payload([_ITEM_BTG])
        xp = coletor_xp.parse_payload({"investmentFunds": [_ITEM_XP]})
        itau, _ = coletor_itau.parse_catalogo(
            coletor_itau.decodificar(_CATALOGO_ITAU, "ISO-8859-1")
        )
        vitrines = []
        for casa, fundos, fonte in (
            ("btg", btg, CollectorStrategy.API_JSON),
            ("xp", xp, CollectorStrategy.PLAYWRIGHT),
            ("itau", itau, CollectorStrategy.HTML_SCRAPE),
        ):
            for fundo in fundos:
                fundo.sources = [fonte.value]
            vitrines.append(_snapshot(casa, fundos))
        return vitrines

    def test_relatorio_e_as_quatro_superficies_na_pasta_da_previdencia(self):
        from shelf_monitor import main as main_mod
        from shelf_monitor.previdencia.cli import construir_relatorio, renderizar

        # Os blocos que leem o informe diario da CVM ficam fora: aqui nao ha
        # rede nem base, e o que se testa e a orquestracao do modulo.
        with mock.patch.object(main_mod, "attach_retail_flow"), \
                mock.patch.object(main_mod, "attach_performance"), \
                mock.patch.object(main_mod, "attach_custo_e_mandato"), \
                rotulos_sobrepostos(ROTULOS_PROPRIOS):
            report = construir_relatorio(self.config, self._vitrines())
            falhas = renderizar(self.config, report)

        self.assertEqual(falhas, [])
        arquivos = {p.name for p in self.config.reports_dir.iterdir()}
        hoje = report.generated_at.date().isoformat()
        self.assertIn("dashboard.html", arquivos)
        self.assertIn(f"digest-{hoje}.md", arquivos)
        self.assertIn(f"previdencia-{hoje}.xlsx", arquivos)
        self.assertIn(f"previdencia-{hoje}.pptx", arquivos)

        dashboard = (self.config.reports_dir / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("<title>Monitor de Previdência</title>", dashboard)
        self.assertIn("Itaú Private", dashboard)
        # A pagina da previdencia, e nao a de fundos com os blocos suprimidos.
        self.assertIn('id="limites-section"', dashboard)
        self.assertNotIn('id="gaps-section"', dashboard)
        # O modulo de fundos nao foi tocado.
        self.assertFalse((Path(self._tmp.name) / "reports").exists())

    def test_ganchos_rodam_depois_do_enriquecimento(self):
        from shelf_monitor import main as main_mod

        chamadas = []
        with mock.patch.object(main_mod, "attach_retail_flow"), \
                mock.patch.object(main_mod, "attach_performance"), \
                mock.patch.object(main_mod, "attach_custo_e_mandato"):
            main_mod.build_report(
                self.config, self._vitrines(), None,
                classificador=IndicePrevidencia,
                pos_enriquecimento=chamadas.append,
            )
        self.assertEqual(len(chamadas), 1)
        self.assertEqual({s.platform for s in chamadas[0]}, {"btg", "xp", "itau"})


# --- os ganchos compartilhados, sem argumento, fazem o de antes ---------------


class TestGanchosCompartilhados(unittest.TestCase):
    def _report(self) -> RunReport:
        return RunReport(generated_at=datetime(2026, 9, 22, 12, 0), snapshots=[])

    def test_digest_sem_config_tem_a_identidade_de_fundos(self):
        from shelf_monitor.report.digest import render_html, render_markdown

        report = self._report()
        self.assertTrue(render_markdown(report).startswith("# Monitor de Prateleiras de Fundos"))
        self.assertIn("Concorrentes da XP - execução de", render_markdown(report))
        self.assertIn("<title>Monitor de Prateleiras de Fundos</title>", render_html(report))

        previdencia = ConfigPrevidencia()
        self.assertTrue(
            render_markdown(report, previdencia).startswith("# Monitor de Previdência")
        )

    def test_assunto_sem_rotulo_e_o_de_fundos(self):
        from shelf_monitor.report.mailer import build_subject

        report = self._report()
        self.assertTrue(build_subject(report).startswith("[Monitor de fundos]"))
        self.assertTrue(
            build_subject(report, ConfigPrevidencia.rotulo_assunto).startswith(
                "[Monitor de previdência]"
            )
        )

    def test_enriquecimento_usa_o_resolvedor_de_fundos_por_padrao(self):
        import inspect

        from shelf_monitor.enrich import enrich_snapshots
        from shelf_monitor.main import build_report

        for funcao in (enrich_snapshots, build_report):
            with self.subTest(funcao=funcao.__name__):
                padrao = inspect.signature(funcao).parameters["classificador"].default
                self.assertIs(padrao, ClassificationIndex)
        self.assertIsNone(
            inspect.signature(build_report).parameters["pos_enriquecimento"].default
        )

    def test_custo_usa_o_indice_de_fundos_por_padrao(self):
        import inspect

        from shelf_monitor.main import attach_custo_e_mandato, build_report
        from shelf_monitor.report.custo import IndiceCusto, build_custo

        for funcao, parametro in (
            (build_custo, "classe_indice"),
            (attach_custo_e_mandato, "indice_custo"),
            (build_report, "indice_custo"),
        ):
            with self.subTest(funcao=funcao.__name__):
                padrao = inspect.signature(funcao).parameters[parametro].default
                self.assertIs(padrao, IndiceCusto)


class TestCli(unittest.TestCase):
    def test_ajuda_do_modulo(self):
        saida = subprocess.run(
            [sys.executable, "-m", "shelf_monitor.previdencia", "run", "--help"],
            capture_output=True, text=True, timeout=120, cwd=RAIZ,
        )
        self.assertEqual(saida.returncode, 0, saida.stderr)
        for opcao in ("--platform", "--git-commit", "--no-market", "--send-email"):
            self.assertIn(opcao, saida.stdout)

    def test_todos_os_subcomandos_existem(self):
        from shelf_monitor.previdencia.cli import criar_parser

        parser = criar_parser()
        for comando in ("run", "collect", "report", "insights"):
            with self.subTest(comando=comando):
                args = parser.parse_args([comando, "--platform", "btg"])
                self.assertEqual(args.platforms, ["btg"])
                self.assertTrue(callable(args.func))


if __name__ == "__main__":
    unittest.main()
