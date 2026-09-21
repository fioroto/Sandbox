"""Testes da leitura competitiva das vitrines.

Os casos aqui sao os erros que a analise cometeu de verdade contra os dados de
2026-09-19 e que as guardas passaram a impedir: comparar categoria em
granularidades diferentes, comparar aplicacao minima de fontes diferentes, e
tratar a vitrine que acabou de nascer como movimento de prateleira.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.enrich import KEY_ANBIMA
from shelf_monitor.models import (
    AssetClass,
    CollectorStrategy,
    FundOffer,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.classify import (
    CATEGORIA_ESTRUTURADO,
    KEY_CATEGORIA,
    KEY_VIA,
)
from shelf_monitor.report.competitive import (
    _UNCLASSIFIED,
    build_manager_reach,
    build_momentum,
    build_positioning,
    build_xp_decisions,
    coarse_category,
    fine_category,
    vitrine_only_values,
    vitrine_snapshots,
)
from shelf_monitor.storage import save_shelf_snapshot


def offer(
    platform: str,
    name: str,
    *,
    cnpj: str | None = None,
    asset_class: AssetClass = AssetClass.RENDA_FIXA,
    anbima: str | None = None,
    minimo: float | None = None,
    manager: str | None = None,
    sources: list[str] | None = None,
) -> FundOffer:
    fund = FundOffer(
        platform=platform,
        name=name,
        cnpj=cnpj,
        asset_class=asset_class,
        min_initial=minimo,
        manager_name=manager,
        sources=sources or [CollectorStrategy.PLAYWRIGHT.value],
    )
    if anbima:
        # Estado pos-enriquecimento: o cadastro carimbou as duas chaves. Sem
        # KEY_CATEGORIA o fundo fica fora do eixo comparavel, que e o que
        # acontece - de proposito - com rotulo publicado so pela casa.
        fund.extra[KEY_ANBIMA] = anbima
        fund.extra[KEY_CATEGORIA] = anbima
        fund.extra[KEY_VIA] = "cnpj"
    return fund


def snapshot(
    platform: str,
    funds: list[FundOffer],
    *,
    strategy: CollectorStrategy = CollectorStrategy.PLAYWRIGHT,
    status: SnapshotStatus = SnapshotStatus.OK,
    dia: date | None = None,
) -> ShelfSnapshot:
    # snapshot_date e propriedade derivada de collected_at.
    momento = datetime.combine(dia or date(2026, 9, 19), datetime.min.time())
    return ShelfSnapshot(
        platform=platform,
        collected_at=momento,
        status=status,
        strategy=strategy,
        funds=funds,
    )


# Faixa de CNPJ por casa: sem isso duas vitrines montadas pelo helper teriam os
# MESMOS CNPJs e nada seria exclusivo de ninguem - o teste de exclusivos passava
# a medir a colisao do fixture, nao a funcao.
_FAIXA = {"xp": 0, "btg": 1, "itau": 2, "inter": 3, "nu": 4}


def vitrine(platform: str, quantidade: int, **kwargs) -> list[FundOffer]:
    """Uma vitrine acima do minimo exigido para entrar na analise."""
    base = _FAIXA.get(platform, 9) * 1_000
    return [
        offer(
            platform,
            f"{platform.upper()} FUNDO {i:03d}",
            cnpj=f"{base + i:014d}",
            **kwargs,
        )
        for i in range(quantidade)
    ]


class TestVitrineSnapshots(unittest.TestCase):
    def test_casa_sem_vitrine_sai_com_motivo_e_nao_como_vitrine_vazia(self):
        """O caso do Nu: so catalogo Open Finance, nenhuma vitrine deslogada.

        Sem a distincao, "o Nu nao tem renda fixa na prateleira" seria publicado
        quando o que houve e que o Nu nao tem prateleira deslogada.
        """
        catalogo = [
            offer("nu", f"NU FUNDO {i}", cnpj=f"{i:014d}",
                  sources=[CollectorStrategy.OPEN_FINANCE.value])
            for i in range(50)
        ]
        usaveis, fora = vitrine_snapshots([
            snapshot("nu", catalogo, strategy=CollectorStrategy.OPEN_FINANCE)
        ])
        self.assertEqual(usaveis, {})
        self.assertEqual(len(fora), 1)
        self.assertIn("sem vitrine", fora[0]["reason"])

    def test_vitrine_curta_demais_nao_sustenta_posicionamento(self):
        usaveis, fora = vitrine_snapshots([snapshot("xp", vitrine("xp", 5))])
        self.assertEqual(usaveis, {})
        self.assertIn("abaixo do minimo", fora[0]["reason"])

    def test_coleta_indisponivel_sai_com_motivo(self):
        usaveis, fora = vitrine_snapshots([
            snapshot("xp", [], status=SnapshotStatus.UNAVAILABLE)
        ])
        self.assertEqual(usaveis, {})
        self.assertIn("indisponivel", fora[0]["reason"])


class TestCategoryAxes(unittest.TestCase):
    """Os dois eixos sao duas granularidades da MESMA taxonomia oficial.

    Ate 2026-09-20 eles eram taxonomias diferentes: o grosso vinha da heuristica
    `AssetClass` e o fino da ANBIMA. Misturar as duas foi o que fez o Itau
    aparecer com 17x o share das concorrentes em "Renda Fixa" - nao era aposta
    de prateleira, era o balde grosso que so ele usava, porque so ele nao casava
    com o cadastro. Com o resolver por nome (shelf_monitor.classify) a cobertura
    dele foi a 100% e os dois eixos passaram a sair da mesma fonte.
    """

    def test_eixo_grosso_e_o_nivel_1_da_categoria_oficial(self):
        self.assertEqual(
            coarse_category(offer("xp", "F", anbima="Acoes Livre")), "Acoes"
        )
        self.assertEqual(
            coarse_category(offer("xp", "F", anbima="Renda Fixa Simples")),
            "Renda Fixa",
        )

    def test_heuristica_nao_entra_em_nenhum_dos_eixos(self):
        """`AssetClass` e inferida do nome, nao lida do cadastro.

        Deixa-la entrar no eixo comparavel e o bug original: a casa que nao casa
        com o cadastro ganha um balde so dela e aparece super-indexada nele.
        """
        so_heuristica = offer("itau", "F", asset_class=AssetClass.ACOES)
        self.assertEqual(coarse_category(so_heuristica), _UNCLASSIFIED)
        self.assertEqual(fine_category(so_heuristica), _UNCLASSIFIED)

    def test_eixo_fino_traz_a_categoria_oficial_inteira(self):
        com_anbima = offer("xp", "F", anbima="Renda Fixa Simples")
        self.assertEqual(fine_category(com_anbima), "Renda Fixa Simples")

    def test_categoria_resolvida_por_nome_vale_nos_dois_eixos(self):
        """O resolver por nome produz categoria oficial em conteudo.

        A diferenca e a procedencia, que fica em `categoria_via` - nao a
        validade. Uma casa que nao publica CNPJ entra na comparacao pelo mesmo
        rotulo que as outras.
        """
        fund = offer("itau", "ABSOLUTE ALPHA GLOBAL")
        fund.extra[KEY_CATEGORIA] = "Multimercados Livre"
        fund.extra[KEY_VIA] = "nome"
        self.assertEqual(fine_category(fund), "Multimercados Livre")
        self.assertEqual(coarse_category(fund), "Multimercados")

    def test_rotulo_publicado_pela_casa_nao_entra_no_eixo(self):
        """Os coletores escrevem em KEY_ANBIMA o rotulo da propria plataforma.

        Medido em 2026-09-20: com o rotulo da casa valendo como oficial, o BTG
        aparecia apostando em "Direitos Creditorios" que "so ele tem" - o balde
        era dele (`anbimaRating`, collectors/btg.py:95), nao do cadastro, e
        nenhuma concorrente usava. E o mesmo erro do eixo grosso heuristico,
        por outra porta.
        """
        so_da_casa = FundOffer(platform="btg", name="F", sources=["api-json"])
        so_da_casa.extra[KEY_ANBIMA] = "Direitos Creditorios"
        self.assertEqual(coarse_category(so_da_casa), _UNCLASSIFIED)
        self.assertEqual(fine_category(so_da_casa), _UNCLASSIFIED)

    def test_resolvido_so_no_nivel_1_fica_fora_do_eixo_fino(self):
        """Nivel 1 vale no eixo grosso e nao no fino.

        Medido em 2026-09-20: promovendo a resolucao de nivel 1 ao eixo fino, o
        Itau aparecia com "RENDA FIXA 22,20x" e "MULTIMERCADO so ela" - baldes
        grossos dentro da comparacao fina, que so ele tinha porque so nele havia
        fundo resolvido nesse nivel.
        """
        fund = offer("itau", "ITAU GLOBAL DINAMICO")
        fund.extra[KEY_CATEGORIA] = "Multimercados"
        fund.extra[KEY_VIA] = "nome-rotulo-n1"
        self.assertEqual(coarse_category(fund), "Multimercados")
        self.assertEqual(fine_category(fund), _UNCLASSIFIED)

    def test_estruturado_tem_balde_proprio(self):
        """FIDC e FII nao tem classificacao ANBIMA nem CVM no cadastro - 0%
        medido. Nao e lacuna de join: e produto que a taxonomia nao cobre."""
        fund = offer("itau", "ESSENTIAL FIDC SUBCLASSE I")
        fund.extra[KEY_CATEGORIA] = CATEGORIA_ESTRUTURADO
        fund.extra[KEY_VIA] = "estruturado"
        self.assertEqual(coarse_category(fund), "Estruturado")
        self.assertEqual(fine_category(fund), _UNCLASSIFIED)

    def test_casa_sem_cobertura_anbima_fica_fora_do_eixo_fino(self):
        xp = vitrine("xp", 40, anbima="Renda Fixa Simples")
        btg = vitrine("btg", 40, anbima="Renda Fixa Duracao Livre")
        itau = vitrine("itau", 40)  # sem ANBIMA nenhuma
        pos = build_positioning([
            snapshot("xp", xp), snapshot("btg", btg), snapshot("itau", itau)
        ])
        por_casa = {p["platform_id"]: p for p in pos["positions"]}
        self.assertTrue(por_casa["xp"]["fine_over"] or por_casa["xp"]["fine_under"])
        self.assertEqual(por_casa["itau"]["fine_over"], [])
        self.assertEqual(por_casa["itau"]["fine_under"], [])
        self.assertEqual(por_casa["itau"]["classificacao_cov"], 0.0)

    def test_sem_classificacao_nao_entra_como_aposta(self):
        """"A casa X tem 2,8x mais Sem classificacao" e fato sobre o join com a
        base oficial, nao sobre a estrategia da casa."""
        xp = vitrine("xp", 30, asset_class=AssetClass.OUTROS) + vitrine("xp", 10)
        btg = vitrine("btg", 40)
        pos = build_positioning([snapshot("xp", xp), snapshot("btg", btg)])
        rotulos = [
            b["category"]
            for p in pos["positions"]
            for b in p["over_index"] + p["under_index"]
        ]
        self.assertNotIn(_UNCLASSIFIED, rotulos)


class TestPositioningIndex(unittest.TestCase):
    def test_indice_compara_contra_as_demais_e_nao_contra_o_total(self):
        """Contra o total, a propria casa entraria no denominador e todo indice
        seria puxado para 1 - quanto maior a casa, mais achatado o sinal."""
        xp = vitrine("xp", 40, anbima="Acoes Livre")
        btg = vitrine("btg", 40, anbima="Renda Fixa Simples")
        pos = build_positioning([snapshot("xp", xp), snapshot("btg", btg)])
        por_casa = {p["platform_id"]: p for p in pos["positions"]}
        acoes = next(b for b in por_casa["xp"]["over_index"] if b["category"] == "Acoes")
        # 100% na XP contra 0% no par: exclusivo, nao 2x.
        self.assertEqual(acoes["peer_share"], 0.0)
        self.assertEqual(acoes["index"], float("inf"))

    def test_uma_vitrine_so_nao_permite_posicionamento_relativo(self):
        pos = build_positioning([snapshot("xp", vitrine("xp", 40))])
        self.assertFalse(pos["available"])
        self.assertIn("ao menos duas", pos["reason"])


class TestVitrineOnlyValues(unittest.TestCase):
    def test_valor_que_pode_ter_vindo_do_catalogo_fica_de_fora(self):
        """O caso da XP: todo minimo dela veio do registro Open Finance."""
        da_vitrine = offer("btg", "A", minimo=500.0,
                           sources=[CollectorStrategy.PLAYWRIGHT.value])
        do_catalogo = offer("xp", "B", minimo=50_000.0,
                            sources=["open-finance", CollectorStrategy.PLAYWRIGHT.value])
        self.assertEqual(vitrine_only_values([da_vitrine], "min_initial"), [500.0])
        self.assertEqual(vitrine_only_values([do_catalogo], "min_initial"), [])

    def test_baseline_sem_ticket_na_vitrine_muda_a_decisao(self):
        """Nao se afirma "ticket mais alto" com minimo de fontes diferentes.

        A XP entra com minimo vindo do Open Finance (mediana alta) e o par com
        minimo da propria vitrine (mediana baixa). A decisao correta e que a XP
        nao exibe o ticket - nao que ela cobra mais.
        """
        xp = [
            offer("xp", f"XP {i}", cnpj=f"{i:014d}", minimo=50_000.0,
                  manager="GESTORA A",
                  sources=["open-finance", CollectorStrategy.PLAYWRIGHT.value])
            for i in range(40)
        ]
        btg = [
            offer("btg", f"BTG {i}", cnpj=f"{i + 500:014d}", minimo=500.0,
                  manager="GESTORA B",
                  sources=[CollectorStrategy.PLAYWRIGHT.value])
            for i in range(40)
        ]
        dec = build_xp_decisions([snapshot("xp", xp), snapshot("btg", btg)])
        titulos = [d["titulo"] for d in dec["decisoes"]]
        self.assertTrue(any("nao exibe aplicacao minima" in t for t in titulos))
        self.assertFalse(any("Ticket de entrada acima" in t for t in titulos))


class TestIdentityGuard(unittest.TestCase):
    """A vitrine do Itau traz CNPJ em 6% dos fundos - e so o CNPJ cruza casas."""

    def _sem_cnpj(self, quantidade: int) -> list[FundOffer]:
        return [
            offer("itau", f"ITAU FUNDO {i:03d}", cnpj=None, minimo=500.0)
            for i in range(quantidade)
        ]

    def test_sem_cnpj_a_casa_nao_recebe_contagem_de_exclusivos(self):
        """Antes da guarda, o Itau saia com "4 fundos exclusivos (1%)" - que era a
        fracao dele que o monitor conseguia identificar, nao uma leitura de
        prateleira."""
        pos = build_positioning([
            snapshot("xp", vitrine("xp", 40)),
            snapshot("itau", self._sem_cnpj(40)),
        ])
        por_casa = {p["platform_id"]: p for p in pos["positions"]}
        self.assertIsNone(por_casa["itau"]["exclusive"])
        self.assertIsNone(por_casa["itau"]["exclusive_share"])
        self.assertIn("identidade por CNPJ", por_casa["itau"]["sem_dado"])
        # A XP continua medida: a guarda e por casa, nao global.
        self.assertEqual(por_casa["xp"]["exclusive"], 40)
        self.assertEqual(
            [e["platform_id"] for e in pos["sem_identidade"]], ["itau"]
        )

    def test_consenso_nao_conta_casa_que_nao_cruza_por_cnpj(self):
        """Com o Itau dentro da conta, o consenso exigido cairia para 2 de 3 e
        seria satisfeito por uma casa que quase nao participa."""
        comum = "70000000000001"
        xp = vitrine("xp", 40)
        btg = vitrine("btg", 40) + [offer("btg", "FUNDO COMUM", cnpj=comum)]
        inter = vitrine("inter", 40) + [offer("inter", "FUNDO COMUM", cnpj=comum)]
        itau = self._sem_cnpj(40)

        dec = build_xp_decisions([
            snapshot("xp", xp), snapshot("btg", btg),
            snapshot("inter", inter), snapshot("itau", itau),
        ])
        consenso = next(d for d in dec["decisoes"] if "consenso" in d["titulo"])
        self.assertIn("2 das 2 concorrentes cruzaveis", consenso["evidencia"])
        self.assertIn("Itau", consenso["evidencia"])
        # O fixture nao publica CNPJ nenhum; nos dados reais o Itau esta em 6%.
        self.assertIn("0% da vitrine", consenso["evidencia"])


class TestManagerReachCount(unittest.TestCase):
    def test_a_contagem_de_ausentes_nao_e_a_do_recorte_exibido(self):
        """O dict sai fatiado para exibicao; a contagem tem de ser a real - senao
        o console dizia 12 e a decisao dizia 16 sobre a mesma coisa."""
        xp = vitrine("xp", 40, manager="GESTORA XP")
        # 32 fundos em cada: acima do minimo de vitrine, e 20 gestoras distintas
        # compartilhadas entre as duas concorrentes.
        btg = [
            offer("btg", f"BTG {i}", cnpj=f"{1000 + i:014d}",
                  manager=f"GESTORA {i % 20:02d}")
            for i in range(32)
        ]
        inter = [
            offer("inter", f"INTER {i}", cnpj=f"{3000 + i:014d}",
                  manager=f"GESTORA {i % 20:02d}")
            for i in range(32)
        ]
        alcance = build_manager_reach(
            [snapshot("xp", xp), snapshot("btg", btg), snapshot("inter", inter)]
        )
        self.assertEqual(alcance["total_ausentes_no_baseline"], 20)
        self.assertEqual(len(alcance["ausentes_no_baseline"]), 12)


class TestMomentum(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(data_dir=self.tmp.name)
        self.config.ensure_dirs()

    def tearDown(self):
        self.tmp.cleanup()

    def test_vitrine_que_nasceu_hoje_nao_conta_como_movimento(self):
        """O caso real: XP, BTG e Itau so passaram a ter vitrine em 2026-09-19,
        porque antes a CDN devolvia 403 ao navegador headless. Comparar contra a
        vitrine vazia de ontem diria "a XP lancou 664 fundos"."""
        antigo = snapshot(
            "xp",
            [offer("xp", f"XP {i}", cnpj=f"{i:014d}",
                   sources=[CollectorStrategy.OPEN_FINANCE.value]) for i in range(40)],
            strategy=CollectorStrategy.OPEN_FINANCE,
            dia=date(2026, 9, 13),
        )
        save_shelf_snapshot(self.config, antigo)

        hoje = snapshot("xp", vitrine("xp", 40), dia=date(2026, 9, 19))
        save_shelf_snapshot(self.config, hoje)

        mom = build_momentum(self.config, [hoje])
        self.assertEqual(mom["movimentos"], [])
        self.assertEqual(len(mom["sem_base"]), 1)
        self.assertIn("nao tinha vitrine", mom["sem_base"][0]["reason"])

    def test_duas_vitrines_comparaveis_produzem_entradas_e_saidas(self):
        antes = snapshot("inter", vitrine("inter", 40), dia=date(2026, 9, 13))
        save_shelf_snapshot(self.config, antes)

        funds = vitrine("inter", 40)[:38]
        funds.append(offer("inter", "INTER FUNDO NOVO", cnpj="99999999999999",
                           asset_class=AssetClass.ACOES))
        depois = snapshot("inter", funds, dia=date(2026, 9, 19))
        save_shelf_snapshot(self.config, depois)

        mom = build_momentum(self.config, [depois])
        self.assertEqual(len(mom["movimentos"]), 1)
        movimento = mom["movimentos"][0]
        self.assertEqual(movimento["entradas"], 1)
        self.assertEqual(movimento["saidas"], 2)
        self.assertEqual(movimento["saldo_por_categoria"].get("Acoes"), 1)


class TestXpDecisions(unittest.TestCase):
    def test_consenso_separa_gestora_ja_distribuida_de_gestora_nova(self):
        """O custo de executar e o que torna a lista priorizavel."""
        xp = [
            offer("xp", f"XP {i}", cnpj=f"{i:014d}", manager="GESTORA CONHECIDA")
            for i in range(40)
        ]
        comum_conhecida = offer("x", "FUNDO CONSENSO A", cnpj="70000000000001",
                                manager="GESTORA CONHECIDA")
        comum_nova = offer("x", "FUNDO CONSENSO B", cnpj="70000000000002",
                           manager="GESTORA NOVA")
        btg = vitrine("btg", 40, manager="GESTORA BTG") + [
            offer("btg", comum_conhecida.name, cnpj=comum_conhecida.cnpj,
                  manager="GESTORA CONHECIDA"),
            offer("btg", comum_nova.name, cnpj=comum_nova.cnpj, manager="GESTORA NOVA"),
        ]
        inter = vitrine("inter", 40, manager="GESTORA INTER") + [
            offer("inter", comum_conhecida.name, cnpj=comum_conhecida.cnpj,
                  manager="GESTORA CONHECIDA"),
            offer("inter", comum_nova.name, cnpj=comum_nova.cnpj, manager="GESTORA NOVA"),
        ]
        dec = build_xp_decisions([
            snapshot("xp", xp), snapshot("btg", btg), snapshot("inter", inter)
        ])
        consenso = next(d for d in dec["decisoes"] if "consenso" in d["titulo"])
        self.assertIn("2 fundos", consenso["tamanho"])
        self.assertIn("1 cuja gestora a XP ja distribui", consenso["acao"])

    def test_sem_vitrine_do_baseline_o_bloco_e_suprimido_com_motivo(self):
        """Sem a vitrine da XP tudo pareceria gap."""
        dec = build_xp_decisions([
            snapshot("xp", [], status=SnapshotStatus.UNAVAILABLE),
            snapshot("btg", vitrine("btg", 40)),
        ])
        self.assertFalse(dec["available"])
        self.assertIn("indisponivel", dec["reason"])

    def test_exclusivos_do_baseline_saem_como_ativo_a_defender(self):
        xp = vitrine("xp", 40)
        btg = vitrine("btg", 40)
        dec = build_xp_decisions([snapshot("xp", xp), snapshot("btg", btg)])
        exclusivo = next(d for d in dec["decisoes"] if "exclusivo" in d["titulo"])
        self.assertIn("40 fundos", exclusivo["tamanho"])


if __name__ == "__main__":
    unittest.main()
