"""Testes das fontes de ETF: B3 (fundos listados), Fundos.NET e COTAHIST.

Sem rede: o que se testa aqui é o que não depende dela - codificação de
parâmetros, leitura dos payloads no formato documentado pela biblioteca de
referência (`mercados`), o parser tolerante do XML do informe, o layout de
largura fixa do COTAHIST e as agregações.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market import b3_funds, cotahist, fundosnet
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.etf_flow import aggregate_etf_weekly_flows
from shelf_monitor.market.etf_sources import last_business_day, match_informe_to_catalog


def temp_config() -> tuple[Config, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    config = Config(data_dir=tmp.name)
    config.ensure_dirs()
    return config, tmp


# --- B3 -----------------------------------------------------------------------


class TestB3Params(unittest.TestCase):
    def test_params_go_as_base64_json_in_the_path(self):
        encoded = b3_funds.encode_params({"typeFund": 7, "pageNumber": 1, "pageSize": 100})
        decoded = json.loads(base64.b64decode(encoded))
        self.assertEqual(decoded, {"typeFund": 7, "pageNumber": 1, "pageSize": 100})

    def test_double_encoded_json_string_is_unwrapped(self):
        """A API às vezes devolve o JSON dentro de uma string JSON."""
        inner = json.dumps({"results": [{"acronym": "BOVA"}]})
        self.assertEqual(b3_funds.decode_b3_json(json.dumps(inner)), {"results": [{"acronym": "BOVA"}]})
        self.assertEqual(b3_funds.decode_b3_json(inner), {"results": [{"acronym": "BOVA"}]})
        self.assertEqual(b3_funds.decode_b3_json("  "), {})


class TestB3Detail(unittest.TestCase):
    PAYLOAD = {
        "detailFund": {
            "acronym": "bova ",
            "tradingName": "ISHARES IBOV",
            "cnpj": "10.406.511/0001-61",
            "codes": [" BOVA11 "],
            "codesOther": ["BOVA11", "BOVA12"],
            "quotaCount": "12345678",
            "quotaDateApproved": "05/09/2026",
            "classification": "Índice",
            "segment": "ETF",
            "typeFNET": "ETF",
            "companyName": "ISHARES IBOVESPA FUNDO DE INDICE",
            "webSite": "www.blackrock.com/br",
        },
        "shareHolder": {"shareHolderName": "BLACKROCK BRASIL"},
    }

    def test_detail_fields_are_normalized(self):
        fund = b3_funds.parse_listed_fund_detail(self.PAYLOAD, type_id=19)
        self.assertEqual(fund.acronym, "BOVA")
        self.assertEqual(fund.cnpj, "10406511000161")
        self.assertEqual(fund.codes, ["BOVA11", "BOVA12"])
        self.assertEqual(fund.ticker, "BOVA11")
        self.assertEqual(fund.quota_count, 12345678.0)
        self.assertEqual(fund.quota_date, date(2026, 9, 5))
        self.assertEqual(fund.admin_name, "BLACKROCK BRASIL")

    def test_ticker_falls_back_to_acronym_plus_11(self):
        payload = {"detailFund": {"acronym": "IMAB", "codes": [], "codesOther": None}, "shareHolder": None}
        fund = b3_funds.parse_listed_fund_detail(payload, type_id=20)
        self.assertEqual(fund.ticker, "IMAB11")

    def test_payload_without_detail_is_none(self):
        self.assertIsNone(b3_funds.parse_listed_fund_detail({"foo": 1}, 1))


class TestTickersCsv(unittest.TestCase):
    def test_manual_rows_survive_and_b3_rows_are_replaced(self):
        existing = [
            {"cnpj": "11111111000111", "ticker": "MANU11", "fonte": "manual", "atualizado_em": "2026-01-01"},
            {"cnpj": "22222222000122", "ticker": "OLDB11", "fonte": "b3", "atualizado_em": "2026-01-01"},
        ]
        funds = [
            b3_funds.FundoListadoB3(type_id=19, acronym="NEWB", cnpj="33333333000133", codes=["NEWB11"], trading_name="NEW"),
            # Mesmo CNPJ da linha manual: a correção humana vence.
            b3_funds.FundoListadoB3(type_id=19, acronym="MANX", cnpj="11111111000111", codes=["MANX11"]),
        ]
        rows = b3_funds.merge_tickers(existing, funds, today=date(2026, 9, 15))
        by_cnpj = {r["cnpj"]: r for r in rows}
        self.assertEqual(by_cnpj["11111111000111"]["ticker"], "MANU11")
        self.assertNotIn("22222222000122", by_cnpj)
        self.assertEqual(by_cnpj["33333333000133"]["ticker"], "NEWB11")
        self.assertEqual(by_cnpj["33333333000133"]["fonte"], "b3")

    def test_roundtrip_through_the_file(self):
        config, tmp = temp_config()
        try:
            rows = [{"cnpj": "33333333000133", "ticker": "NEWB11", "nome_b3": "NEW", "segmento": "ETF",
                     "fonte": "b3", "atualizado_em": "2026-09-15"}]
            b3_funds.write_tickers_csv(config.etf_tickers_path, rows)
            back = b3_funds.read_tickers_csv(config.etf_tickers_path)
            self.assertEqual(back[0]["ticker"], "NEWB11")
            self.assertEqual(back[0]["fonte"], "b3")
        finally:
            tmp.cleanup()


# --- Fundos.NET ---------------------------------------------------------------


class TestFundosNetPage(unittest.TestCase):
    HTML = """
    <html><script>var csrf_token = "abc123";</script>
    <select id="tipoFundo" class="x">
      <option value="">Todos</option>
      <option value="1">Fundo Imobiliário</option>
      <option value="3">ETF</option>
      <option value="4">ETF RF</option>
    </select>
    <select id="categoriaDocumento"><option value="6">Informes  Periódicos</option></select>
    </html>"""

    def test_csrf_token(self):
        self.assertEqual(fundosnet.extract_csrf_token(self.HTML), "abc123")
        self.assertIsNone(fundosnet.extract_csrf_token("<html></html>"))

    def test_select_options(self):
        self.assertEqual(
            fundosnet.parse_select_options(self.HTML, "tipoFundo"),
            {"Fundo Imobiliário": 1, "ETF": 3, "ETF RF": 4},
        )
        self.assertEqual(fundosnet.parse_select_options(self.HTML, "categoriaDocumento"), {"Informes Periódicos": 6})
        self.assertEqual(fundosnet.parse_select_options(self.HTML, "naoExiste"), {})


class TestFundosNetRows(unittest.TestCase):
    def test_document_row(self):
        row = {
            "id": "1104397", "descricaoFundo": " INVESTO SMALL CAP ETF ", "nomePregao": "INVESTO",
            "categoriaDocumento": "Informes  Periódicos", "tipoDocumento": "Informe Diário",
            "especieDocumento": "", "dataEntrega": "12/09/2026 18:30", "dataReferencia": "11/09/2026",
            "formatoDataReferencia": "3", "versao": 1, "situacaoDocumento": "A", "descricaoStatus": "AC",
            "cnpjFundo": "",
        }
        doc = fundosnet.parse_document_row(row)
        self.assertEqual(doc.id, 1104397)
        self.assertEqual(doc.fundo, "INVESTO SMALL CAP ETF")
        self.assertEqual(doc.data_entrega.date(), date(2026, 9, 12))
        self.assertEqual(doc.data_referencia, date(2026, 9, 11))
        self.assertIsNone(doc.cnpj_fundo)
        self.assertTrue(doc.url.endswith("downloadDocumento?id=1104397"))

    def test_reference_date_formats(self):
        self.assertEqual(fundosnet._parse_reference_date("1", "2026"), date(2026, 1, 1))
        self.assertEqual(fundosnet._parse_reference_date("2", "09/2026"), date(2026, 9, 1))
        self.assertEqual(fundosnet._parse_reference_date("4", "11/09/2026 10:00"), date(2026, 9, 11))
        self.assertIsNone(fundosnet._parse_reference_date("3", "nada"))

    def test_row_without_id_is_ignored(self):
        self.assertIsNone(fundosnet.parse_document_row({"descricaoFundo": "x"}))


class TestInformeDiarioXml(unittest.TestCase):
    def test_cvm_standard_tags_are_recognized(self):
        """Nomes do padrão XML de informe diário da CVM, decimal com vírgula."""
        xml = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
        <DOC_ARQ>
          <INF_DIARIO>
            <CNPJ_FDO>10.406.511/0001-61</CNPJ_FDO>
            <DT_COMPTC>2026-09-11</DT_COMPTC>
            <VL_TOTAL>15000000000,55</VL_TOTAL>
            <VL_QUOTA>184,24</VL_QUOTA>
            <VL_PATRIM_LIQ>14900000000,10</VL_PATRIM_LIQ>
            <CAPTC_DIA>1000,00</CAPTC_DIA>
            <RESG_DIA>250,50</RESG_DIA>
            <NR_COTST>98035</NR_COTST>
            <ALGO_NOVO>x</ALGO_NOVO>
          </INF_DIARIO>
        </DOC_ARQ>"""
        informes = fundosnet.parse_informe_diario_xml(xml, doc_id=7, fundo="BOVA")
        self.assertEqual(len(informes), 1)
        i = informes[0]
        self.assertEqual(i.cnpj, "10406511000161")
        self.assertEqual(i.data, date(2026, 9, 11))
        self.assertAlmostEqual(i.cota, 184.24)
        self.assertAlmostEqual(i.pl, 14900000000.10)
        self.assertAlmostEqual(i.captacao, 1000.0)
        self.assertAlmostEqual(i.resgates, 250.5)
        self.assertEqual(i.cotistas, 98035)
        self.assertEqual(i.unknown_tags, ["ALGONOVO"])
        self.assertTrue(i.is_usable)

    def test_camel_case_variants_and_multiple_funds(self):
        xml = """<Informes>
          <Fundo><Cnpj>11111111000111</Cnpj><DataReferencia>11/09/2026</DataReferencia>
                 <ValorCota>100,5</ValorCota><PatrimonioLiquido>1000</PatrimonioLiquido>
                 <QtdCotasEmitidas>10</QtdCotasEmitidas></Fundo>
          <Fundo><Cnpj>22222222000122</Cnpj><DataReferencia>11/09/2026</DataReferencia>
                 <ValorCota>50</ValorCota><PatrimonioLiquido>500</PatrimonioLiquido>
                 <QtdCotasEmitidas>10</QtdCotasEmitidas></Fundo>
        </Informes>"""
        informes = fundosnet.parse_informe_diario_xml(xml)
        self.assertEqual([i.cnpj for i in informes], ["11111111000111", "22222222000122"])
        self.assertEqual(informes[0].cotas, 10.0)
        self.assertEqual(informes[0].unknown_tags, [])

    def test_broken_xml_is_empty_not_an_exception(self):
        self.assertEqual(fundosnet.parse_informe_diario_xml(b"<a><b></a>"), [])

    def test_flows_are_derived_from_quota_count_when_not_published(self):
        """Sem captação/resgate no informe, o fluxo vem de (cotas hoje - ontem) x cota."""
        base = dict(cnpj="11111111000111", cota=100.0, pl=None, cotistas=5)
        informes = [
            fundosnet.InformeDiarioEtf(data=date(2026, 9, 8), cotas=1000.0, **base),
            fundosnet.InformeDiarioEtf(data=date(2026, 9, 9), cotas=1010.0, **base),
            fundosnet.InformeDiarioEtf(data=date(2026, 9, 10), cotas=1004.0, **base),
        ]
        records = fundosnet.records_from_informes(informes)
        self.assertEqual([r.flow_source for r in records], [None, "delta_cotas", "delta_cotas"])
        self.assertEqual(records[1].captc_dia, 1000.0)  # +10 cotas x 100
        self.assertEqual(records[2].resg_dia, 600.0)   # -6 cotas x 100
        self.assertEqual(records[0].vl_patrim_liq, 100000.0)  # PL derivado: cotas x cota

        # E encaixa no pipeline semanal existente sem adaptação.
        weekly = aggregate_etf_weekly_flows({}, records)
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0].captacao_liquida, 400.0)

    def test_published_flows_take_precedence(self):
        informes = [
            fundosnet.InformeDiarioEtf(cnpj="1", data=date(2026, 9, 8), cota=10.0, pl=100.0, captacao=7.0, resgates=2.0),
        ]
        records = fundosnet.records_from_informes(informes)
        self.assertEqual(records[0].flow_source, "informe")
        self.assertEqual(records[0].net_inflow, 5.0)

    def test_csv_roundtrip(self):
        config, tmp = temp_config()
        try:
            informes = [
                fundosnet.InformeDiarioEtf(cnpj="11111111000111", fundo="X", data=date(2026, 9, 11), pl=10.0,
                                          cota=1.0, cotas=10.0, cotistas=3, doc_id=9, unknown_tags=["FOO"]),
            ]
            fundosnet.write_fnet_day_csv(config, informes, date(2026, 9, 12))
            back = fundosnet.read_fnet_csvs(config)
            self.assertEqual(len(back), 1)
            self.assertEqual(back[0].cnpj, "11111111000111")
            self.assertEqual(back[0].cotistas, 3)
            self.assertEqual(back[0].doc_id, 9)
        finally:
            tmp.cleanup()


class TestMatchInformeToCatalog(unittest.TestCase):
    def test_cnpj_first_then_name_slug(self):
        catalog = {"10406511000161": EtfMetadata(cnpj="10406511000161", name="ISHARES IBOVESPA FUNDO DE INDICE", kind="etf")}
        by_cnpj = fundosnet.InformeDiarioEtf(cnpj="10406511000161", fundo="qualquer")
        self.assertEqual(match_informe_to_catalog(by_cnpj, catalog), "10406511000161")
        by_name = fundosnet.InformeDiarioEtf(cnpj=None, fundo="Ishares Ibovespa Fundo de Índice")
        self.assertEqual(match_informe_to_catalog(by_name, catalog), "10406511000161")
        self.assertIsNone(match_informe_to_catalog(fundosnet.InformeDiarioEtf(fundo="outro"), catalog))


# --- COTAHIST -----------------------------------------------------------------


def cotahist_line(codneg: str, day: str = "20260911", especi: str = "CI", preult: int = 18424,
                  totneg: int = 5000, quatot: int = 1200000, voltot: int = 22108800000,
                  tpmerc: str = "010", codbdi: str = "02") -> str:
    """Monta uma linha de 245 colunas no layout do COTAHIST (preços em centavos)."""
    fields = [
        ("01", 2), (day, 8), (codbdi, 2), (codneg.ljust(12), 12), (tpmerc, 3),
        ("ISHARES IBOV".ljust(12), 12), (especi.ljust(10), 10), ("".ljust(3), 3), ("R$", 4),
        (str(18400).rjust(13, "0"), 13), (str(18500).rjust(13, "0"), 13), (str(18300).rjust(13, "0"), 13),
        (str(18420).rjust(13, "0"), 13), (str(preult).rjust(13, "0"), 13), (str(18423).rjust(13, "0"), 13),
        (str(18425).rjust(13, "0"), 13), (str(totneg).rjust(5, "0"), 5), (str(quatot).rjust(18, "0"), 18),
        (str(voltot).rjust(18, "0"), 18), ("0" * 13, 13), ("0", 1), ("99991231", 8), ("0000001", 7),
        ("0" * 13, 13), ("BRBOVACTF003", 12), ("100", 3),
    ]
    line = "".join(value[:width].ljust(width) for value, width in fields)
    assert len(line) == cotahist.LINE_LENGTH, len(line)
    return line


class TestCotahist(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(cotahist.url_for_day(date(2026, 9, 11)), f"{cotahist.COTAHIST_BASE}COTAHIST_D11092026.ZIP")
        self.assertEqual(cotahist.url_for_month(date(2026, 9, 1)), f"{cotahist.COTAHIST_BASE}COTAHIST_M092026.ZIP")
        self.assertEqual(cotahist.url_for_year(date(2026, 1, 1)), f"{cotahist.COTAHIST_BASE}COTAHIST_A2026.ZIP")

    def test_fixed_width_line(self):
        row = cotahist.parse_line(cotahist_line("BOVA11"))
        self.assertEqual(row.data, date(2026, 9, 11))
        self.assertEqual(row.codneg, "BOVA11")
        self.assertEqual(row.especi, "CI")
        self.assertAlmostEqual(row.fechamento, 184.24)
        self.assertEqual(row.negocios, 5000)
        self.assertEqual(row.quantidade, 1200000)
        self.assertAlmostEqual(row.volume, 221088000.0)
        self.assertEqual(row.isin, "BRBOVACTF003")
        self.assertTrue(row.is_lote_padrao_vista)

    def test_header_and_short_lines_are_skipped(self):
        self.assertIsNone(cotahist.parse_line("00COTAHIST.2026BOVESPA"))
        self.assertIsNone(cotahist.parse_line("01" + "x" * 50))

    def _zip(self, lines: list[str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("COTAHIST_D11092026.TXT", "\n".join(["00HEADER", *lines, "99TRAILER"]))
        return buffer.getvalue()

    def test_read_filters_by_ticker_or_by_fund_quota_heuristic(self):
        payload = self._zip([
            cotahist_line("BOVA11"),
            cotahist_line("PETR4", especi="ON"),
            cotahist_line("HGLG11"),
            cotahist_line("BOVA11", tpmerc="070"),  # termo, fora do lote padrão à vista
        ])
        by_ticker = cotahist.read_cotahist(payload, tickers={"bova11"})
        self.assertEqual([r.codneg for r in by_ticker], ["BOVA11"])
        heuristic = cotahist.read_cotahist(payload, tickers=None)
        self.assertEqual([r.codneg for r in heuristic], ["BOVA11", "HGLG11"])
        self.assertEqual(cotahist.read_cotahist(b"nao e zip"), [])

    def test_weekly_liquidity(self):
        rows = [
            cotahist.parse_line(cotahist_line("BOVA11", day="20260904", preult=18000, voltot=100000, totneg=10, quatot=5)),
            cotahist.parse_line(cotahist_line("BOVA11", day="20260908", preult=18500, voltot=100000, totneg=10, quatot=5)),
            cotahist.parse_line(cotahist_line("BOVA11", day="20260911", preult=19800, voltot=200000, totneg=20, quatot=7)),
            cotahist.parse_line(cotahist_line("BOVA11", day="20260911", preult=19800, voltot=200000, totneg=20, quatot=7)),  # duplicata
        ]
        weekly = {w.week_key: w for w in cotahist.aggregate_weekly_liquidity(rows)}
        self.assertEqual(weekly["2026-W37"].pregoes, 2)
        self.assertAlmostEqual(weekly["2026-W37"].volume_financeiro, 3000.0)
        self.assertEqual(weekly["2026-W37"].negocios, 30)
        self.assertAlmostEqual(weekly["2026-W37"].retorno_preco, 10.0)  # 198 / 180 - 1
        self.assertIsNone(weekly["2026-W36"].retorno_preco)

    def test_csv_roundtrip(self):
        config, tmp = temp_config()
        try:
            rows = [cotahist.parse_line(cotahist_line("BOVA11"))]
            cotahist.write_cotahist_day_csv(config, rows, date(2026, 9, 11))
            back = cotahist.read_cotahist_csvs(config)
            self.assertEqual(back[0].codneg, "BOVA11")
            self.assertAlmostEqual(back[0].fechamento, 184.24)
        finally:
            tmp.cleanup()


class TestBusinessDay(unittest.TestCase):
    def test_skips_weekend(self):
        self.assertEqual(last_business_day(date(2026, 9, 14)), date(2026, 9, 11))  # segunda -> sexta
        self.assertEqual(last_business_day(date(2026, 9, 16)), date(2026, 9, 15))


if __name__ == "__main__":
    unittest.main()
