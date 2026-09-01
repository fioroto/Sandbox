"""Testes dos blocos de insight: sobreposicao/arbitragem e superficies."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime

from shelf_monitor.config import Config
from shelf_monitor.models import (
    CollectorStrategy,
    CvmDiff,
    CvmFund,
    CvmSnapshot,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.report.aggregate import (
    build_cvm_cross_block,
    build_dashboard_data,
    build_liquidity_block,
    overlap_block_from_snapshots,
    summarize_platform,
)
from shelf_monitor.report.dashboard import render_dashboard
from shelf_monitor.report.digest import render_html, render_markdown

CNPJ_A = "11111111000191"
CNPJ_B = "22222222000191"
CNPJ_C = "33333333000191"


def offer(
    platform: str,
    name: str,
    cnpj: str | None = None,
    fee: float | None = None,
    min_initial: float | None = None,
    **kwargs,
) -> FundOffer:
    return FundOffer(
        platform=platform,
        name=name,
        cnpj=cnpj,
        admin_fee_pct=fee,
        min_initial=min_initial,
        **kwargs,
    )


def shelf(
    platform: str,
    funds: list[FundOffer],
    status: SnapshotStatus = SnapshotStatus.OK,
) -> ShelfSnapshot:
    return ShelfSnapshot(
        platform=platform,
        collected_at=datetime(2026, 8, 31, 12, 0),
        strategy=CollectorStrategy.OPEN_FINANCE,
        status=status,
        funds=funds,
    )


def report_for(snapshots: list[ShelfSnapshot], **kwargs) -> RunReport:
    return RunReport(
        generated_at=datetime(2026, 8, 31, 12, 0), snapshots=snapshots, **kwargs
    )


def cvm_fund(
    cnpj: str,
    name: str,
    house: str = "btg",
    situation: str = "Em Funcionamento Normal",
    registered_at: str | None = None,
) -> CvmFund:
    return CvmFund(
        cnpj=cnpj,
        name=name,
        house=house,
        matched_by="test",
        situation=situation,
        registered_at=registered_at,
    )


def cvm_snapshot(
    funds: list[CvmFund], status: SnapshotStatus = SnapshotStatus.OK
) -> CvmSnapshot:
    return CvmSnapshot(
        collected_at=datetime(2026, 8, 31, 12, 0), status=status, funds=funds
    )


def temp_config() -> tuple[Config, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    config = Config(data_dir=tmp.name)
    config.ensure_dirs()
    return config, tmp


class TestOverlapBlock(unittest.TestCase):
    def test_pair_counts_core_and_diagonal(self):
        block = overlap_block_from_snapshots([
            shelf("xp", [offer("xp", "Alfa", CNPJ_A), offer("xp", "Beta", CNPJ_B)]),
            shelf("btg", [offer("btg", "Alfa", CNPJ_A), offer("btg", "Beta", CNPJ_B)]),
            shelf("nu", [offer("nu", "Alfa", CNPJ_A)]),
        ])
        pairs = {(p["a"], p["b"]): p["shared"] for p in block["pair_matrix"]}
        self.assertEqual(pairs[("xp", "btg")], 2)
        self.assertEqual(pairs[("xp", "nu")], 1)
        self.assertEqual(pairs[("btg", "nu")], 1)
        self.assertEqual(block["diagonal"], {"xp": 2, "btg": 2, "nu": 1})
        # Nucleo comum exige presenca em TODAS as casas utilizaveis.
        self.assertEqual(block["core_all"]["count"], 1)
        self.assertEqual(block["core_all"]["funds"][0]["name"], "Alfa")
        self.assertEqual(block["multi_home"], {"2": 1, "3": 1})

    def test_unavailable_snapshot_is_excluded(self):
        block = overlap_block_from_snapshots([
            shelf("xp", [offer("xp", "Alfa", CNPJ_A)]),
            shelf("btg", [], status=SnapshotStatus.UNAVAILABLE),
            shelf("nu", [offer("nu", "Alfa", CNPJ_A)]),
        ])
        self.assertEqual(block["platforms"], ["xp", "nu"])
        # Com a casa fora, o nucleo e sobre as duas restantes.
        self.assertEqual(block["core_all"]["count"], 1)

    def test_fee_spread_threshold(self):
        # 0,05 p.p. e arredondamento de fonte; 0,15 p.p. e decisao de preco.
        block = overlap_block_from_snapshots([
            shelf("xp", [
                offer("xp", "Alfa", CNPJ_A, fee=1.00),
                offer("xp", "Beta", CNPJ_B, fee=1.00),
            ]),
            shelf("nu", [
                offer("nu", "Alfa", CNPJ_A, fee=1.05),
                offer("nu", "Beta", CNPJ_B, fee=1.15),
            ]),
        ])
        fee = block["fee_arbitrage"]
        self.assertEqual(fee["universe"], 2)
        self.assertEqual(fee["significant"], 1)
        self.assertEqual(len(fee["top"]), 1)
        top = fee["top"][0]
        self.assertEqual(top["name"], "Beta")
        self.assertEqual(top["spread_pp"], 0.15)
        self.assertEqual(top["cheapest"], "xp")
        self.assertEqual(top["priciest"], "nu")

    def test_mixed_fee_scales_are_reconciled(self):
        # XP publica fracao (0.015 = 1,5%), Nu publica percentual (1.5). Sem a
        # canonicalizacao isso viraria a maior "arbitragem" do relatorio.
        block = overlap_block_from_snapshots([
            shelf("xp", [offer("xp", "Alfa", CNPJ_A, fee=0.015)]),
            shelf("nu", [offer("nu", "Alfa", CNPJ_A, fee=1.5)]),
        ])
        fee = block["fee_arbitrage"]
        self.assertEqual(fee["universe"], 1)
        self.assertEqual(fee["significant"], 0)
        self.assertEqual(fee["unit_suspect"], 0)

    def test_unit_suspect_ratio_guard(self):
        # Mesmo fundo com razao 50x entre taxas ja canonicalizadas e erro de
        # dado, nao preco - sai do top e vira contador.
        block = overlap_block_from_snapshots([
            shelf("xp", [
                offer("xp", "Alfa", CNPJ_A, fee=0.05),
                offer("xp", "Beta", CNPJ_B, fee=1.0),
                offer("xp", "Gama", CNPJ_C, fee=1.0),
            ]),
            shelf("nu", [
                offer("nu", "Alfa", CNPJ_A, fee=2.5),
                offer("nu", "Beta", CNPJ_B, fee=1.0),
                offer("nu", "Gama", CNPJ_C, fee=1.0),
            ]),
        ])
        fee = block["fee_arbitrage"]
        self.assertEqual(fee["unit_suspect"], 1)
        self.assertEqual(fee["significant"], 0)

    def test_house_without_fees_stays_in_matrix_and_out_of_arbitrage(self):
        block = overlap_block_from_snapshots([
            shelf("xp", [offer("xp", "Alfa", CNPJ_A, fee=1.0)]),
            shelf("inter", [offer("inter", "Alfa", CNPJ_A)]),
        ])
        pairs = {(p["a"], p["b"]): p["shared"] for p in block["pair_matrix"]}
        self.assertEqual(pairs[("xp", "inter")], 1)
        self.assertEqual(block["fee_arbitrage"]["universe"], 0)

    def test_min_arbitrage_floor(self):
        block = overlap_block_from_snapshots([
            shelf("xp", [
                offer("xp", "Alfa", CNPJ_A, min_initial=1000.0),
                offer("xp", "Beta", CNPJ_B, min_initial=500.0),
            ]),
            shelf("btg", [
                offer("btg", "Alfa", CNPJ_A, min_initial=25000.0),
                offer("btg", "Beta", CNPJ_B, min_initial=900.0),
            ]),
        ])
        mins = block["min_arbitrage"]
        self.assertEqual(mins["universe"], 2)
        self.assertEqual(mins["significant"], 1)
        top = mins["top"][0]
        self.assertEqual(top["name"], "Alfa")
        self.assertEqual(top["diff"], 24000.0)
        self.assertEqual(top["lowest"], "xp")

    def test_single_platform_has_no_pairs(self):
        block = overlap_block_from_snapshots([
            shelf("xp", [offer("xp", "Alfa", CNPJ_A)]),
        ])
        self.assertEqual(block["pair_matrix"], [])
        self.assertEqual(block["core_all"]["count"], 0)


class TestCvmCrossBlock(unittest.TestCase):
    def test_pre_operational_already_on_shelf(self):
        report = report_for(
            [shelf("xp", [offer("xp", "Alfa", CNPJ_A)])],
            cvm_snapshot=cvm_snapshot([
                cvm_fund(CNPJ_A, "Alfa", situation="Fase Pre-Operacional"),
                cvm_fund(CNPJ_B, "Beta", situation="Fase Pre-Operacional"),
            ]),
        )
        cross = build_cvm_cross_block(report)
        self.assertTrue(cross["available"])
        pre = cross["pre_op_on_shelf"]
        # So o CNPJ presente numa prateleira entra; o outro pre-operacional nao.
        self.assertEqual(pre["count"], 1)
        self.assertEqual(pre["funds"][0]["name"], "Alfa")
        self.assertEqual(pre["funds"][0]["platforms"], ["XP Investimentos"])
        self.assertFalse(pre["funds"][0]["on_listing"])

    def test_cancelled_still_offered_and_listing_first(self):
        vitrine = offer("xp", "Alfa", CNPJ_A)
        vitrine.sources = [CollectorStrategy.HTML_SCRAPE.value]
        catalogo = offer("xp", "Beta", CNPJ_B)
        report = report_for(
            [shelf("xp", [vitrine, catalogo])],
            cvm_snapshot=cvm_snapshot([
                cvm_fund(CNPJ_B, "Beta", situation="Cancelado"),
                cvm_fund(CNPJ_A, "Alfa", situation="Cancelado"),
                cvm_fund(CNPJ_C, "Gama", situation="Cancelado"),
            ]),
        )
        cross = build_cvm_cross_block(report)
        cancelled = cross["cancelled_on_shelf"]
        # Gama nao esta em prateleira nenhuma; Alfa (vitrine) vem antes de Beta.
        self.assertEqual(cancelled["count"], 2)
        self.assertEqual(cancelled["on_listing"], 1)
        self.assertEqual([f["name"] for f in cancelled["funds"]], ["Alfa", "Beta"])
        self.assertTrue(cancelled["funds"][0]["on_listing"])
        self.assertEqual(cancelled["by_platform"][0]["label"], "XP Investimentos")
        self.assertEqual(cancelled["by_platform"][0]["count"], 2)

    def test_unavailable_or_missing_cvm(self):
        report = report_for([shelf("xp", [offer("xp", "Alfa", CNPJ_A)])])
        self.assertFalse(build_cvm_cross_block(report)["available"])
        report.cvm_snapshot = cvm_snapshot([], status=SnapshotStatus.UNAVAILABLE)
        self.assertFalse(build_cvm_cross_block(report)["available"])


class TestCvmMovementsPayload(unittest.TestCase):
    def test_diff_items_are_listed_and_capped(self):
        funds = [
            cvm_fund(f"{i:014d}", f"Fundo {i:02d}", registered_at=f"2026-08-{i + 1:02d}")
            for i in range(20)
        ]
        report = report_for(
            [shelf("xp", [offer("xp", "Alfa", CNPJ_A)])],
            cvm_snapshot=cvm_snapshot(funds),
            cvm_diff=CvmDiff(
                prev_date=datetime(2026, 8, 29).date(),
                curr_date=datetime(2026, 8, 31).date(),
                new_registrations=funds,
            ),
        )
        payload = build_dashboard_data(report)["cvm"]["diff"]
        self.assertFalse(payload["is_first_run"])
        self.assertEqual(payload["new_registrations"], 20)
        self.assertEqual(len(payload["new_items"]), 15)
        self.assertEqual(payload["launch_items"], [])
        self.assertEqual(payload["cancelled_items"], [])

    def test_first_run_keeps_numeric_keys(self):
        report = report_for(
            [shelf("xp", [offer("xp", "Alfa", CNPJ_A)])],
            cvm_snapshot=cvm_snapshot([cvm_fund(CNPJ_A, "Alfa")]),
            cvm_diff=CvmDiff(
                prev_date=None,
                curr_date=datetime(2026, 8, 31).date(),
                new_registrations=[cvm_fund(CNPJ_A, "Alfa")],
            ),
        )
        payload = build_dashboard_data(report)["cvm"]["diff"]
        self.assertTrue(payload["is_first_run"])
        self.assertEqual(payload["new_registrations"], 1)


class TestLiquidityBlock(unittest.TestCase):
    def test_median_and_bands_need_both_redemption_fields(self):
        block = build_liquidity_block(report_for([
            shelf("xp", [
                offer("xp", "Alfa", CNPJ_A, redemption_quotation_days=1,
                      redemption_payment_days=1),
                offer("xp", "Beta", CNPJ_B, redemption_quotation_days=15,
                      redemption_payment_days=15),
                # So cotizacao: nao entra - somar metade subestimaria a espera.
                offer("xp", "Gama", CNPJ_C, redemption_quotation_days=5),
            ]),
        ]))
        platform = block["platforms"][0]
        self.assertEqual(platform["n"], 2)
        self.assertEqual(platform["median_total_days"], 16.0)
        self.assertEqual(platform["bands"]["D+0 a 1"], 0)
        self.assertEqual(platform["bands"]["D+2 a 5"], 1)
        self.assertEqual(platform["bands"]["D+31 ou mais"], 0)
        self.assertEqual(platform["bands"]["D+16 a 30"], 1)

    def test_taxation_mix_counts_sem_info(self):
        block = build_liquidity_block(report_for([
            shelf("xp", [
                offer("xp", "Alfa", CNPJ_A, redemption_quotation_days=0,
                      redemption_payment_days=1, taxation="LONGO_PRAZO"),
                offer("xp", "Beta", CNPJ_B),
            ]),
        ]))
        taxation = block["platforms"][0]["taxation"]
        self.assertEqual(taxation["LONGO_PRAZO"], 1)
        self.assertEqual(taxation["sem_info"], 1)

    def test_platform_without_deadlines_goes_to_missing(self):
        block = build_liquidity_block(report_for([
            shelf("xp", [offer("xp", "Alfa", CNPJ_A, redemption_quotation_days=0,
                                redemption_payment_days=1)]),
            shelf("inter", [offer("inter", "Beta", CNPJ_B)]),
        ]))
        self.assertEqual([p["platform_id"] for p in block["platforms"]], ["xp"])
        self.assertEqual(block["missing"], ["Banco Inter"])


class TestTopAdmins(unittest.TestCase):
    def test_summary_counts_top_admins(self):
        snapshot = shelf("xp", [
            offer("xp", "Alfa", CNPJ_A, admin_name="BTG DTVM "),
            offer("xp", "Beta", CNPJ_B, admin_name="BTG DTVM"),
            offer("xp", "Gama", CNPJ_C, admin_name="Intrag"),
            offer("xp", "Delta"),
        ])
        summary = summarize_platform(snapshot, set())
        self.assertEqual(summary.top_admins, [("BTG DTVM", 2), ("Intrag", 1)])
        self.assertEqual(
            summary.to_dict()["top_admins"][0], {"name": "BTG DTVM", "count": 2}
        )


class TestNewSurfaces(unittest.TestCase):
    def _report(self) -> RunReport:
        return report_for([
            shelf("xp", [offer("xp", "Alfa", CNPJ_A, fee=1.0)]),
            shelf("nu", [offer("nu", "Alfa", CNPJ_A, fee=2.0)]),
        ])

    def test_digest_carries_cvm_cross_blocks(self):
        report = self._report()
        vitrine = offer("xp", "Velho", CNPJ_B)
        vitrine.sources = [CollectorStrategy.HTML_SCRAPE.value]
        report.snapshots[0].funds.append(vitrine)
        report.cvm_snapshot = cvm_snapshot([
            cvm_fund(CNPJ_A, "Alfa", situation="Fase Pre-Operacional"),
            cvm_fund(CNPJ_B, "Velho", situation="Cancelado"),
        ])
        markdown = render_markdown(report)
        self.assertIn("Ja em prateleira antes do lancamento", markdown)
        self.assertIn("Higiene de catalogo", markdown)
        self.assertIn("NA VITRINE", markdown)
        html_text = render_html(report)
        self.assertIn("Higiene de catalogo", html_text)

    def test_dashboard_has_cvm_cross_and_movements_mounts(self):
        config, tmp = temp_config()
        try:
            report = self._report()
            report.cvm_snapshot = cvm_snapshot([cvm_fund(CNPJ_A, "Alfa")])
            path = render_dashboard(config, report)
            html_text = path.read_text(encoding="utf-8")
        finally:
            tmp.cleanup()
        for element_id in (
            "cvm-movements",
            "table-pre-op-shelf",
            "table-cancelled-shelf",
        ):
            self.assertIn(f'id="{element_id}"', html_text)

    def test_dashboard_has_liquidity_and_admin_mounts(self):
        config, tmp = temp_config()
        try:
            path = render_dashboard(config, self._report())
            html_text = path.read_text(encoding="utf-8")
        finally:
            tmp.cleanup()
        for element_id in ("liquidity-section", "chart-taxation", "table-top-admins"):
            self.assertIn(f'id="{element_id}"', html_text)

    def test_digest_has_competitive_highlights_in_both_renderers(self):
        report = self._report()
        markdown = render_markdown(report)
        self.assertIn("## Destaques competitivos", markdown)
        self.assertIn("Maiores diferencas de taxa", markdown)
        html_text = render_html(report)
        self.assertIn("Destaques competitivos", html_text)

    def test_digest_omits_highlights_with_single_platform(self):
        report = report_for([shelf("xp", [offer("xp", "Alfa", CNPJ_A, fee=1.0)])])
        self.assertNotIn("Destaques competitivos", render_markdown(report))

    def test_dashboard_embeds_overlap_and_json_stays_parseable(self):
        config, tmp = temp_config()
        try:
            path = render_dashboard(config, self._report())
            html_text = path.read_text(encoding="utf-8")
        finally:
            tmp.cleanup()
        self.assertIn('id="overlap-section"', html_text)
        self.assertIn('id="table-arbitrage"', html_text)
        match = re.search(
            r'<script type="application/json" id="dashboard-data">(.*?)</script>',
            html_text,
            re.S,
        )
        data = json.loads(match.group(1))
        self.assertIn("overlap", data)
        self.assertEqual(data["overlap"]["fee_arbitrage"]["significant"], 1)

    def test_dashboard_data_carries_overlap_block(self):
        data = build_dashboard_data(self._report())
        self.assertEqual(data["overlap"]["platforms"], ["xp", "nu"])


if __name__ == "__main__":
    unittest.main()
