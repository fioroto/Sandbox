"""Testes dos adaptadores do extrator bruto de ETF, sem rede.

B3 (portal de arquivos e COTAHIST), FNET (informe e documentos), carteira da
CVM, indices B3, Comdinheiro e gestoras. Cada um recebe uma sessao falsa que
responde como a fonte respondeu nas medicoes de 2026-09-24.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import base64
import json
import os
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest import mock

try:  # `discover -s tests` poe tests/ no sys.path; `-m unittest tests.X` nao.
    from fixtures_comuns import temp_config
    import etf_bruto_apoio as apoio
except ImportError:  # pragma: no cover - depende de como a suite foi invocada
    from tests.fixtures_comuns import temp_config
    from tests import etf_bruto_apoio as apoio

from shelf_monitor.market import cotahist, fundosnet
from shelf_monitor.market.etf_bruto import (
    armazem,
    b3_arquivos,
    comdinheiro,
    composicao,
    fnet,
    indices_b3,
    manual,
)
from shelf_monitor.market.etf_bruto.contexto import EM_DISCO, OK, PARCIAL, SEM_CONFIGURACAO
from shelf_monitor.market.fetch import CacheReason, FetchResult

FIXTURES = Path(__file__).parent / "fixtures" / "etf_bruto"
HOJE = date(2026, 9, 24)  # quinta-feira


class _ComConfig(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        self.addCleanup(self.tmp.cleanup)


# --- B3, portal de arquivos ------------------------------------------------------


def _arquivos_b3(*dias: str, status: str = "Final") -> dict:
    arquivos = {}
    for dia in dias:
        arquivos[(b3_arquivos.INSTRUMENTOS, dia)] = apoio.arquivo_b3(
            apoio.CABECALHO_INSTRUMENTOS, apoio.instrumentos(dia), status)
        arquivos[(b3_arquivos.NEGOCIOS, dia)] = apoio.arquivo_b3(apoio.CABECALHO_NEGOCIOS, apoio.negocios(dia), status)
    return arquivos


class TestB3Download(_ComConfig):
    def test_token_e_depois_download(self):
        sessao = apoio.SessaoFalsa(apoio.tratador_b3(_arquivos_b3("2026-09-23")))
        conteudo = b3_arquivos.baixar(sessao, self.config, b3_arquivos.NEGOCIOS, date(2026, 9, 23))
        self.assertIn(b"TckrSymb", conteudo)
        (m1, url1, p1, _), (m2, url2, p2, _) = sessao.chamadas
        self.assertEqual((url1, p1), (b3_arquivos.URL_TOKEN,
                                      {"fileName": b3_arquivos.NEGOCIOS, "date": "2026-09-23"}))
        self.assertEqual((url2, p2), (b3_arquivos.URL_DOWNLOAD, {"token": f"{b3_arquivos.NEGOCIOS}|2026-09-23"}))

    def test_400_e_dia_sem_arquivo_e_nao_erro(self):
        sessao = apoio.SessaoFalsa(apoio.tratador_b3({}))
        self.assertIsNone(b3_arquivos.baixar(sessao, self.config, b3_arquivos.NEGOCIOS, date(2026, 9, 20)))
        self.assertEqual(len(sessao.chamadas), 1)

    def test_500_levanta(self):
        sessao = apoio.SessaoFalsa(lambda *a: apoio.resposta(503, b"fora"))
        with self.assertRaises(b3_arquivos.SourceUnavailable):
            b3_arquivos.baixar(sessao, self.config, b3_arquivos.NEGOCIOS, date(2026, 9, 23))

    def test_status_e_cabecalho(self):
        status, cabecalho, linhas = b3_arquivos.ler_csv_b3(
            apoio.arquivo_b3(apoio.CABECALHO_NEGOCIOS, apoio.negocios("2026-09-23")))
        self.assertEqual(status, "Final")
        self.assertEqual(cabecalho[:2], ["RptDt", "TckrSymb"])
        self.assertEqual(len(list(linhas)), 5)


class TestB3Filtros(unittest.TestCase):
    def _instrumentos(self, universo=frozenset()):
        _, cabecalho, linhas = b3_arquivos.ler_csv_b3(
            apoio.arquivo_b3(apoio.CABECALHO_INSTRUMENTOS, apoio.instrumentos("2026-09-23")))
        return {l["TckrSymb"]: l for l in b3_arquivos.filtrar_instrumentos(cabecalho, linhas, set(universo))}

    def test_etf_de_renda_fixa_esta_fora_das_categorias_etf_e_entra(self):
        etfs = self._instrumentos()
        self.assertEqual(
            set(etfs), {"BOVA11", "ACWI11", "ACWI11M", "ACWI11Q", "IMAB11", "03BK11", "03BKETF11H"},
        )
        self.assertEqual(etfs["IMAB11"]["x_criterio"], "renda_fixa")
        self.assertEqual(etfs["IMAB11"]["DaysToSttlm"], "1")
        self.assertEqual(etfs["BOVA11"]["x_criterio"], "categoria")

    def test_lote_de_bloco_e_mercado_primario_ficam_marcados(self):
        etfs = self._instrumentos()
        self.assertEqual((etfs["ACWI11M"]["x_tipo_linha"], etfs["ACWI11M"]["x_ticker_base"]), ("bloco", "ACWI11"))
        self.assertEqual((etfs["ACWI11Q"]["x_tipo_linha"], etfs["ACWI11Q"]["x_ticker_base"]), ("bloco", "ACWI11"))
        primario = etfs["03BKETF11H"]
        self.assertEqual((primario["x_tipo_linha"], primario["x_ticker_base"]), ("mercado_primario", "03BK11"))
        self.assertEqual(etfs["BOVA11"]["x_tipo_linha"], "padrao")
        # A coluna original fica como veio, \xa0 inclusive.
        self.assertIn("\xa0", primario["SctyCtgyNm"])

    def test_negocios_por_ticker(self):
        _, cabecalho, linhas = b3_arquivos.ler_csv_b3(
            apoio.arquivo_b3(apoio.CABECALHO_NEGOCIOS, apoio.negocios("2026-09-23")))
        tickers = {"BOVA11", "BOVA11Q", "BOVA11M", "IMAB11"}
        etfs = {l["TckrSymb"]: l for l in b3_arquivos.filtrar_negocios(cabecalho, linhas, tickers)}
        self.assertEqual(set(etfs), {"BOVA11", "BOVA11Q", "IMAB11"})
        self.assertEqual((etfs["BOVA11Q"]["x_tipo_linha"], etfs["BOVA11Q"]["x_ticker_base"]), ("bloco", "BOVA11"))
        self.assertEqual(etfs["IMAB11"]["SgmtNm"], "FORWARD")

    def test_dias_uteis_pulam_fim_de_semana(self):
        dias = b3_arquivos.dias_uteis(date(2026, 9, 18), date(2026, 9, 21))
        self.assertEqual(dias, [date(2026, 9, 21), date(2026, 9, 18)])

    def test_meses_antes(self):
        self.assertEqual(b3_arquivos.meses_antes(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(b3_arquivos.meses_antes(date(2026, 9, 24), 13), date(2025, 8, 24))


class TestB3Captura(_ComConfig):
    def _capturar(self, arquivos, desde=date(2026, 9, 18), forcar=False):
        sessao = apoio.SessaoFalsa(apoio.tratador_b3(arquivos))
        ctx = apoio.contexto(self.config, hoje=HOJE, desde=desde, forcar=forcar, sessao=sessao)
        return b3_arquivos.capturar(ctx), sessao, ctx

    def test_janela_hoje_pendente_e_idempotente(self):
        arquivos = _arquivos_b3("2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23")
        resumo, sessao, ctx = self._capturar(arquivos)
        pasta = b3_arquivos.pasta_negocios(self.config)
        self.assertEqual(sorted(p.name for p in pasta.glob("*.csv")),
                         ["2026-09-18.csv", "2026-09-21.csv", "2026-09-22.csv", "2026-09-23.csv"])
        self.assertIn("1 dia pendente", resumo.detalhe)  # 24/09: o arquivo de hoje ainda nao saiu
        self.assertEqual(resumo.estado, OK)
        # Fim de semana nao e pedido.
        datas = {p.get("date") for _, url, p, _ in sessao.chamadas if url == b3_arquivos.URL_TOKEN}
        self.assertNotIn("2026-09-19", datas)
        self.assertNotIn("2026-09-20", datas)
        # O cadastro do ultimo pregao foi gravado com as colunas x_.
        cadastro = armazem.ler_csv(b3_arquivos.pasta_instrumentos(self.config) / "2026-09-23.csv")
        self.assertIn("x_tipo_linha", cadastro[0])
        negocios = armazem.ler_csv(pasta / "2026-09-23.csv")
        self.assertEqual({l["TckrSymb"] for l in negocios}, {"BOVA11", "IMAB11"} | {"BOVA11Q"})
        manifesto = ctx.manifesto.to_dict()
        self.assertTrue(any(a["caminho"].endswith("negocios/2026-09-23.csv") for a in manifesto["arquivos"]))
        self.assertNotIn("token", json.dumps(manifesto["arquivos"]).lower().replace("url", ""))

        # Segunda execucao: nada novo, so o dia de hoje e tentado de novo.
        resumo2, sessao2, _ = self._capturar(arquivos)
        self.assertEqual(resumo2.estado, EM_DISCO)
        urls = [url for _, url, _, _ in sessao2.chamadas]
        self.assertNotIn(b3_arquivos.URL_DOWNLOAD, urls)

    def test_feriado_vira_sem_pregao_e_forcar_baixa_de_novo(self):
        arquivos = _arquivos_b3("2026-09-21", "2026-09-23")  # 22/09 "feriado"
        resumo, _, _ = self._capturar(arquivos, desde=date(2026, 9, 21))
        sem_pregao = armazem.ler_json(self.config.etf_bruto_dir / "b3_arquivos" / "_sem_pregao.json")
        self.assertEqual(sem_pregao, ["2026-09-22"])
        _, sessao, _ = self._capturar(arquivos, desde=date(2026, 9, 21))
        self.assertNotIn("2026-09-22", {p.get("date") for _, u, p, _ in sessao.chamadas if u == b3_arquivos.URL_TOKEN})
        _, sessao, _ = self._capturar(arquivos, desde=date(2026, 9, 21), forcar=True)
        self.assertIn("2026-09-22", {p.get("date") for _, u, p, _ in sessao.chamadas if u == b3_arquivos.URL_TOKEN})

    def test_cinco_400_seguidos_sao_o_comeco_do_historico(self):
        arquivos = _arquivos_b3("2026-09-23")
        resumo, sessao, ctx = self._capturar(arquivos, desde=date(2026, 8, 1))
        self.assertIn("histórico da B3 indisponível antes de 16/09/2026", resumo.detalhe)
        # Os cinco dias do bloco nao ficam como "sem pregao".
        sem_pregao = armazem.ler_json(self.config.etf_bruto_dir / "b3_arquivos" / "_sem_pregao.json")
        self.assertEqual(sem_pregao, [])
        negocios = [(p["date"]) for _, u, p, _ in sessao.chamadas
                    if u == b3_arquivos.URL_TOKEN and p["fileName"] == b3_arquivos.NEGOCIOS]
        self.assertNotIn("2026-09-15", negocios)

    def test_arquivo_preliminar_nao_conta_como_capturado(self):
        arquivos = _arquivos_b3("2026-09-23", status="Parcial")
        resumo, _, _ = self._capturar(arquivos, desde=date(2026, 9, 23))
        self.assertIn("cadastro de 23/09/2026 (preliminar)", resumo.detalhe)
        pasta = b3_arquivos.pasta_negocios(self.config)
        self.assertTrue((pasta / "2026-09-23.preliminar.csv").exists())
        self.assertFalse((pasta / "2026-09-23.csv").exists())

    def test_arquivo_so_com_derivativos_e_incompleto_e_nao_volta_sem_forcar(self):
        """Medido em 2026-09-25: em 6 de 266 pregoes o consolidado da B3 so tinha derivativos."""
        arquivos = _arquivos_b3("2026-09-23")
        arquivos[(b3_arquivos.NEGOCIOS, "2026-09-23")] = apoio.arquivo_b3(apoio.CABECALHO_NEGOCIOS, [
            {"RptDt": "2026-09-23", "TckrSymb": "BOVAA1", "SgmtNm": "EQUITY CALL"},
            {"RptDt": "2026-09-23", "TckrSymb": "DOLV26", "SgmtNm": "FINANCIAL"},
        ])
        resumo, _, ctx = self._capturar(arquivos, desde=date(2026, 9, 23))
        self.assertEqual(resumo.estado, OK)  # e da B3, nao falha do extrator
        self.assertIn("arquivo incompleto na B3", resumo.detalhe)
        incompletos = armazem.ler_json(b3_arquivos.caminho_incompletos(self.config))
        self.assertIn("2026-09-23", incompletos)
        self.assertFalse((b3_arquivos.pasta_negocios(self.config) / "2026-09-23.csv").exists())
        _, sessao, _ = self._capturar(arquivos, desde=date(2026, 9, 23))
        pedidos = [p for _, u, p, _ in sessao.chamadas if u == b3_arquivos.URL_TOKEN and p["fileName"] == b3_arquivos.NEGOCIOS]
        self.assertNotIn("2026-09-23", [p["date"] for p in pedidos])
        _, sessao, _ = self._capturar(arquivos, desde=date(2026, 9, 23), forcar=True)
        pedidos = [p for _, u, p, _ in sessao.chamadas if u == b3_arquivos.URL_TOKEN and p["fileName"] == b3_arquivos.NEGOCIOS]
        self.assertIn("2026-09-23", [p["date"] for p in pedidos])

    def test_rptdt_de_outro_dia_e_ocorrencia(self):
        arquivos = _arquivos_b3("2026-09-23")
        arquivos[(b3_arquivos.NEGOCIOS, "2026-09-23")] = apoio.arquivo_b3(
            apoio.CABECALHO_NEGOCIOS, apoio.negocios("2026-09-22"))
        resumo, _, ctx = self._capturar(arquivos, desde=date(2026, 9, 23))
        self.assertEqual(resumo.estado, PARCIAL)
        self.assertFalse((b3_arquivos.pasta_negocios(self.config) / "2026-09-23.csv").exists())
        self.assertTrue(any("RptDt" in o["motivo"] for o in ctx.manifesto.ocorrencias))


def _linha_cotahist(codneg: str, codbdi: str = "14", tpmerc: str = "010", especi: str = "CI") -> str:
    campos = {"tipreg": "01", "data": "20250102", "codbdi": codbdi, "codneg": codneg, "tpmerc": tpmerc,
              "especi": especi, "preult": "0000000018424", "codisi": "BRBOVACTF003"}
    linha = [" "] * cotahist.LINE_LENGTH
    for nome, valor in campos.items():
        inicio, fim = cotahist.LAYOUT[nome]
        linha[inicio:fim] = list(valor.ljust(fim - inicio)[: fim - inicio])
    return "".join(linha)


class TestCotahistAnual(_ComConfig):
    def test_linhas_brutas_de_etf(self):
        caminho = Path(self.tmp.name) / "COTAHIST_A2025.ZIP"
        with zipfile.ZipFile(caminho, "w") as arquivo:
            arquivo.writestr("COTAHIST_A2025.TXT", "\n".join([
                "00COTAHIST.2025BOVESPA",
                _linha_cotahist("BOVA11"),
                _linha_cotahist("NOVO11"),  # ticker fora do universo, BDI 14 e CI: fica (renomeado?)
                _linha_cotahist("PETR4", codbdi="02", especi="ON"),
                _linha_cotahist("BOVA11", tpmerc="070"),  # termo
                "99COTAHIST.2025BOVESPA",
            ]))
        linhas = list(b3_arquivos.linhas_etf_do_cotahist(caminho, {"BOVA11"}))
        self.assertEqual([(l["codneg"], l["x_criterio"]) for l in linhas], [("BOVA11", "universo"), ("NOVO11", "bdi14")])
        self.assertEqual(linhas[0]["preult"], "0000000018424")  # bruto: centavos implicitos
        self.assertEqual(set(linhas[0]) - {"x_criterio"}, set(cotahist.LAYOUT))


# --- FNET --------------------------------------------------------------------------


class TestFnetXml(unittest.TestCase):
    def test_cnpj_vem_do_nome_do_arquivo(self):
        self.assertEqual(
            fnet.cnpj_do_content_disposition('attachment; filename="69131602000184-REG17092026V01-001322547.pdf"'),
            "69131602000184",
        )
        self.assertIsNone(fnet.cnpj_do_content_disposition('attachment; filename="documento.pdf"'))
        self.assertIsNone(fnet.cnpj_do_content_disposition(None))

    def test_informe_real_vira_uma_linha_com_as_tags_originais(self):
        linhas = fnet.achatar_xml((FIXTURES / "informe_diario.xml").read_bytes())
        self.assertEqual(len(linhas), 1)
        linha = linhas[0]
        for tag in ("DT_COMPT", "VERSAO", "CNPJ_FDO", "VL_QUOTA", "PATRIM_LIQ", "CAPTC_DIA", "RESG_DIA",
                    "NR_COTST", "VL_TOTAL_ATV", "DATA_PROX_PL", "VL_TOTAL_SAI"):
            self.assertIn(tag, linha)
        self.assertEqual(linha["VL_QUOTA"], "20,726198170000")
        self.assertEqual(linha["LISTA_COTST_SIGNIF"], "")

    def test_cnpj_no_cabecalho_e_lista_aninhada(self):
        xml = (
            '<?xml version="1.0" encoding="utf-8"?><DOC_ARQ xmlns="urn:infdiario"><CAB_INFORM>'
            "<CNPJ_FDO>11111111000111</CNPJ_FDO><DT_COMPT>18/09/2026</DT_COMPT></CAB_INFORM><LISTA_INFORM>"
            "<INFORM><VL_QUOTA>1,5</VL_QUOTA><LISTA_COTST_SIGNIF><COTST_SIGNIF><TP_PESSOA>PJ</TP_PESSOA>"
            "</COTST_SIGNIF></LISTA_COTST_SIGNIF></INFORM></LISTA_INFORM></DOC_ARQ>"
        ).encode()
        linha = fnet.achatar_xml(xml)[0]
        self.assertEqual(linha["CNPJ_FDO"], "11111111000111")
        self.assertEqual(json.loads(linha["LISTA_COTST_SIGNIF"]), [{"TP_PESSOA": "PJ"}])
        self.assertEqual(fnet.achatar_xml(b"<quebrado"), [])


class _ClienteFnet:
    """Fundos.NET falso: busca por (tipo de fundo, categoria, tipo de documento)."""

    def __init__(self, documentos: list[tuple[tuple[int, int, int], fundosnet.DocumentoFnet]],
                 conteudos: dict[int, tuple[bytes, dict]]):
        self.documentos = documentos
        self.conteudos = conteudos
        self.baixados: list[int] = []
        self.session = None
        self._main_page = None

    def search(self, fund_type_id, doc_type_id=64, category_id=6, start=None, end=None, **_):
        return [doc for chave, doc in self.documentos if chave == (fund_type_id, category_id, doc_type_id)]

    def download_with_headers(self, doc_id):
        self.baixados.append(doc_id)
        return self.conteudos[doc_id]


def _doc(doc_id: int, fundo: str, referencia: date, entrega: datetime, versao: int = 1) -> fundosnet.DocumentoFnet:
    return fundosnet.DocumentoFnet(id=doc_id, fundo=fundo, data_referencia=referencia, data_entrega=entrega,
                                   versao=versao, situacao="A", status="Ativo")


class TestFnetInforme(_ComConfig):
    def test_informes_novos_vao_para_zip_e_csv_e_nao_sao_rebaixados(self):
        xml = (FIXTURES / "informe_diario.xml").read_bytes()
        doc = _doc(1324333, "ETF TESTE", date(2026, 9, 18), datetime(2026, 9, 19, 9, 0))
        cliente = _ClienteFnet(
            [((3, 6, 64), doc)],
            {1324333: (xml, {"Content-Disposition": 'attachment; filename="60419043000138-IFP19092026V01-001324333.xml"'})},
        )
        ctx = apoio.contexto(self.config, hoje=date(2026, 9, 19))
        with mock.patch.object(fnet.fundosnet, "FundosNetClient", lambda config: cliente):
            resumo = fnet.capturar_informes(ctx)
            self.assertEqual(resumo.estado, OK)
            self.assertEqual(cliente._main_page, "")  # a pagina lenta nao e pedida
            pasta = fnet.pasta_informe(self.config)
            with zipfile.ZipFile(pasta / "xml" / "2026-09-18.zip") as arquivo:
                self.assertEqual(arquivo.read("1324333.xml"), xml)
            linhas = armazem.ler_csv(pasta / "2026-09-18.csv")
            self.assertEqual(linhas[0]["cnpj_arquivo"], "60419043000138")
            self.assertEqual(linhas[0]["PATRIM_LIQ"], "11606670,98")
            self.assertEqual(linhas[0]["doc_id"], "1324333")
            # Segunda execucao: o documento ja esta no indice.
            resumo2 = fnet.capturar_informes(apoio.contexto(self.config, hoje=date(2026, 9, 19)))
        self.assertEqual(cliente.baixados, [1324333])
        self.assertEqual(resumo2.estado, EM_DISCO)


class TestFnetDocumentos(_ComConfig):
    def test_os_regulamentos_mais_recentes_de_cada_fundo(self):
        nome = "TREND ETF IBOVESPA FUNDO DE ÍNDICE"
        versoes = [_doc(i, nome, date(2020 + n, 1, 1), datetime(2020 + n, 1, 2, 10, 0)) for n, i in enumerate((5, 10, 15, 20))]
        nao_pdf = _doc(30, "OUTRO ETF FUNDO DE INDICE", date(2026, 9, 1), datetime(2026, 9, 3, 10, 0))
        fora = _doc(40, "FIDC QUALQUER RESPONSABILIDADE LIMITADA", date(2026, 9, 1), datetime(2026, 9, 3, 10, 0))
        demonstracao = _doc(50, nome, date(2026, 3, 31), datetime(2026, 6, 1, 10, 0))
        disposicao = {"Content-Disposition": 'attachment; filename="40155573000109-REG.pdf"'}
        cliente = _ClienteFnet(
            [((3, 5, 0), v) for v in versoes] + [((3, 5, 0), nao_pdf), ((3, 5, 0), fora),
                                                 ((3, 6, fnet.TIPO_DEMONSTRACOES), demonstracao)],
            {**{i: (f"%PDF-1.7 versao {i}".encode(), disposicao) for i in (10, 15, 20)},
             30: (b"<html>erro</html>", {"Content-Type": "text/html"})},
        )
        ctx = apoio.contexto(self.config, hoje=HOJE)
        with mock.patch.object(fnet.fundosnet, "FundosNetClient", lambda config: cliente):
            resumo = fnet.capturar_documentos(ctx)
        # As tres versoes mais novas (a de 2020 fica de fora) e o que nao era PDF.
        self.assertEqual(sorted(cliente.baixados), [10, 15, 20, 30])
        pdf = fnet.pasta_regulamentos(self.config) / "40155573000109" / "20.pdf"
        self.assertTrue(pdf.exists())
        self.assertEqual(resumo.estado, PARCIAL)  # o que nao era PDF
        indice = {l["doc_id"]: l for l in fnet.ler_indice(self.config)}
        self.assertEqual(indice["20"]["arquivo"], "fnet_documentos/regulamento/40155573000109/20.pdf")
        self.assertEqual(indice["50"]["categoria"], "Demonstrações Financeiras")
        self.assertTrue(indice["50"]["link"].endswith("downloadDocumento?id=50"))

    def test_nomes_que_so_diferem_em_palavra_generica_sao_fundos_diferentes(self):
        """Regressao de 2026-09-24: "RENDA" sai do name_slug, e o regulamento do
        NU IBOV SMART DIVIDENDOS tomava o lugar do NU RENDA IBOV SMART DIVIDENDOS (NDIV11)."""
        um = _doc(1120192, "NU IBOV SMART DIVIDENDOS CLASSE DE ÍNDICE - RESPONSABILIDADE LIMITADA",
                  date(2026, 2, 20), datetime(2026, 2, 24, 18, 0))
        outro = _doc(1120186, "NU RENDA IBOV SMART DIVIDENDOS CLASSE DE ÍNDICE - RESPONSABILIDADE LIMITADA",
                     date(2026, 2, 20), datetime(2026, 2, 24, 17, 0))
        cliente = _ClienteFnet(
            [((3, 5, 0), um), ((3, 5, 0), outro)],
            {
                1120192: (b"%PDF um", {"Content-Disposition": 'attachment; filename="52116361000100-REG.pdf"'}),
                1120186: (b"%PDF outro", {"Content-Disposition": 'attachment; filename="52116337000162-REG.pdf"'}),
            },
        )
        with mock.patch.object(fnet.fundosnet, "FundosNetClient", lambda config: cliente):
            fnet.capturar_documentos(apoio.contexto(self.config, hoje=HOJE))
        self.assertEqual(sorted(cliente.baixados), [1120186, 1120192])
        self.assertTrue((fnet.pasta_regulamentos(self.config) / "52116337000162" / "1120186.pdf").exists())


# --- CVM, carteira mensal ----------------------------------------------------------


class TestCda(_ComConfig):
    def test_competencias_fechadas(self):
        self.assertEqual(composicao.competencias_fechadas(HOJE), ["202608", "202607", "202606"])
        # Em 10/09 a de agosto ainda nao passou do prazo.
        self.assertEqual(composicao.competencias_fechadas(date(2026, 9, 10))[0], "202607")

    def _zip(self, competencia: str) -> Path:
        cabecalho = "TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;DENOM_SOCIAL;DT_COMPTC;TP_APLIC;CD_ATIVO;VL_MERC_POS_FINAL"
        linhas = [
            "CLASSES FIIM;40.155.573/0001-09;TREND ETF IBOVESPA;2026-08-31;Ações;PETR4;1000",
            "CLASSES FIIM;40.155.573/0001-09;TREND ETF IBOVESPA;2026-08-31;Ações;VALE3;2000",
            "CLASSES FIDC;11.111.111/0001-11;FIDC QUALQUER;2026-08-31;Direitos;X;10",
        ]
        caminho = Path(self.tmp.name) / f"cda_fi_{competencia}.zip"
        with zipfile.ZipFile(caminho, "w") as arquivo:
            arquivo.writestr(f"cda_fie_{competencia}.csv", "\n".join([cabecalho, *linhas]).encode("latin-1"))
            arquivo.writestr(f"cda_fie_CONFID_{competencia}.csv",
                             "\n".join([cabecalho, "CLASSES FIIM;40.155.573/0001-09;TREND;2026-08-31;X;Y;1"]).encode("latin-1"))
        return caminho

    def test_so_linhas_de_etf_e_sigilo_junto(self):
        def falso_fetch(config, dataset, force=False):
            competencia = dataset.filename[len("cda_fi_"):-len(".zip")]
            if competencia != "202608":
                raise composicao.SourceUnavailable(f"HTTP 404 em {dataset.url}")
            return FetchResult(dataset, self._zip(competencia), False, 1, CacheReason.DOWNLOADED)

        ctx = apoio.contexto(self.config, hoje=HOJE)
        with mock.patch.object(composicao, "fetch", falso_fetch):
            resumo = composicao.capturar_cda(ctx)
            linhas = armazem.ler_csv(composicao.pasta_cda(self.config) / "cda_fie_etf_202608.csv")
            self.assertEqual({l["CD_ATIVO"] for l in linhas}, {"PETR4", "VALE3"})
            self.assertTrue((composicao.pasta_cda(self.config) / "cda_fie_confid_etf_202608.csv").exists())
            self.assertIn("ainda não publicada", resumo.detalhe)
            self.assertIn("ago/2026: 1 ETF, 2 posições", resumo.detalhe)
            resumo2 = composicao.capturar_cda(apoio.contexto(self.config, hoje=HOJE))
        self.assertIn("em disco: ago/2026", resumo2.detalhe)


# --- indices B3 ----------------------------------------------------------------------


def _parametros(url: str, prefixo: str) -> dict:
    return json.loads(base64.b64decode(url[len(prefixo):]).decode())


class TestIndicesB3(_ComConfig):
    def _tratador(self):
        def tratar(metodo, url, params, data):
            if url.startswith(indices_b3.URL_SERIE):
                p = _parametros(url, indices_b3.URL_SERIE)
                if p["index"] == "IMAB":
                    return apoio.resposta(200, '{"min":null,"max":null,"results":null}')
                return apoio.resposta(200, json.dumps({"results": [{"day": 1, "rateValue1": "160.538,69"}]}))
            if url.startswith(indices_b3.URL_CARTEIRA):
                p = _parametros(url, indices_b3.URL_CARTEIRA)
                if p["index"] == "IMAB":
                    return apoio.resposta(200, '{"page":null,"header":null,"results":null}')
                return apoio.resposta(200, json.dumps({
                    "page": {"pageNumber": p["pageNumber"], "totalPages": 2},
                    "header": {"date": "24/09/26", "theoricalQty": "93.803.750.400"},
                    "results": [{"cod": f"ATIVO{p['pageNumber']}", "part": "1,0"}],
                }))
            raise AssertionError(url)
        return tratar

    def test_serie_carteira_e_indice_de_outro_provedor(self):
        sessao = apoio.SessaoFalsa(self._tratador())
        ctx = apoio.contexto(self.config, hoje=HOJE, desde=date(2026, 1, 1), sessao=sessao)
        with mock.patch.object(indices_b3, "PADRAO", ("IBOV", "IMAB")):
            resumo = indices_b3.capturar(ctx)
            pasta = indices_b3.pasta(self.config)
            serie = armazem.ler_json(pasta / "serie" / "IBOV" / "2026.json")
            self.assertEqual(serie["resposta"]["results"][0]["rateValue1"], "160.538,69")
            carteira = armazem.ler_json(pasta / "carteira" / "IBOV" / "2026-09-24.json")
            self.assertEqual([p["results"][0]["cod"] for p in carteira["paginas"]], ["ATIVO1", "ATIVO2"])
            self.assertFalse((pasta / "serie" / "IMAB").exists())
            self.assertIn("IMAB 2026", resumo.detalhe)
            # Dia no passado: nao ha carteira a pedir.
            sessao2 = apoio.SessaoFalsa(self._tratador())
            indices_b3.capturar(apoio.contexto(self.config, hoje=HOJE, dia=date(2026, 9, 1), sessao=sessao2))
        self.assertFalse(any(u.startswith(indices_b3.URL_CARTEIRA) for _, u, _, _ in sessao2.chamadas))


# --- Comdinheiro ------------------------------------------------------------------------

_SENHA = "senha-secreta-123"


class TestComdinheiro(_ComConfig):
    def setUp(self):
        super().setUp()
        armazem.gravar_csv(
            manual.caminho(self.config, "comdinheiro_consultas.csv"), ("nome", "url_ferramenta", "ativo", "observacao"),
            [{"nome": "precos etf", "url_ferramenta": "HistoricoCotacao002.php?&x=BOVA11&data_ini={desde}&data_fim={dia}",
              "ativo": "sim"}],
        )

    def _rodar(self, env: dict, corpo: bytes = b'{"tables": {"tab0": {"lin0": {"col0": "BOVA11"}}}}'):
        sessao = apoio.SessaoFalsa(lambda *a: apoio.resposta(200, corpo))
        ctx = apoio.contexto(self.config, hoje=HOJE, desde=date(2025, 9, 24), sessao=sessao)
        with mock.patch.dict(os.environ, env, clear=False):
            for chave in ("COMDINHEIRO_USUARIO", "COMDINHEIRO_SENHA", "COMDINHEIRO_API_URL"):
                if chave not in env:
                    os.environ.pop(chave, None)
            resumo = comdinheiro.capturar(ctx)
        return resumo, sessao, ctx

    def test_post_com_formulario_e_senha_fora_do_manifesto(self):
        resumo, sessao, ctx = self._rodar({"COMDINHEIRO_USUARIO": "usuario", "COMDINHEIRO_SENHA": _SENHA})
        self.assertEqual(resumo.estado, OK)
        metodo, url, _, dados = sessao.chamadas[0]
        self.assertEqual((metodo, url), ("POST", comdinheiro.URL_PADRAO))
        self.assertEqual(dados["format"], "json3")
        self.assertEqual(dados["URL"], "HistoricoCotacao002.php?&x=BOVA11&data_ini=24092025&data_fim=24092026")
        self.assertEqual((dados["username"], dados["password"]), ("usuario", _SENHA))
        gravado = comdinheiro.pasta(self.config) / "api" / HOJE.isoformat() / "precos_etf.json"
        self.assertTrue(gravado.exists())
        self.assertNotIn(_SENHA, json.dumps(ctx.manifesto.to_dict()))

    def test_sem_credencial_nao_chama_a_api(self):
        resumo, sessao, _ = self._rodar({})
        self.assertEqual(resumo.estado, SEM_CONFIGURACAO)
        self.assertEqual(sessao.chamadas, [])

    def test_endpoint_sem_https_e_recusado(self):
        resumo, sessao, _ = self._rodar({"COMDINHEIRO_USUARIO": "u", "COMDINHEIRO_SENHA": _SENHA,
                                         "COMDINHEIRO_API_URL": "http://exemplo/api"})
        self.assertEqual(resumo.estado, SEM_CONFIGURACAO)
        self.assertEqual(sessao.chamadas, [])

    def test_resposta_de_erro_nao_conta(self):
        resumo, _, _ = self._rodar({"COMDINHEIRO_USUARIO": "u", "COMDINHEIRO_SENHA": _SENHA},
                                   corpo=b'{"erro": "usuario ou senha invalidos"}')
        self.assertEqual(resumo.estado, PARCIAL)
        pasta = comdinheiro.pasta(self.config) / "api" / HOJE.isoformat()
        self.assertTrue((pasta / "precos_etf.erro.txt").exists())
        self.assertFalse((pasta / "precos_etf.json").exists())

    def test_pasta_de_entrada_entra_uma_vez_por_conteudo(self):
        entrada = self.config.etf_entrada_dir / "comdinheiro"
        entrada.mkdir(parents=True)
        (entrada / "export.csv").write_text("ticker;dy\nBOVA11;0\n", encoding="utf-8")
        (entrada / "~$export.xlsx").write_bytes(b"trava do excel")
        resumo, _, _ = self._rodar({})
        self.assertIn("1 arquivo novo", resumo.detalhe)
        resumo2, _, _ = self._rodar({})
        self.assertIn("0 arquivos novos", resumo2.detalhe)


# --- gestoras ------------------------------------------------------------------------------


class TestGestoras(_ComConfig):
    def test_url_bloqueada_vira_ocorrencia_e_a_outra_e_gravada(self):
        armazem.gravar_csv(manual.caminho(self.config, "gestoras_urls.csv"), manual.GESTORAS_URLS[1], [
            {"ticker": "BOVA11", "gestora": "BlackRock", "url": "https://exemplo/bova.csv", "ativo": "sim"},
            {"ticker": "GOLD11", "gestora": "XP", "url": "https://exemplo/gold", "ativo": ""},
            {"ticker": "XINA11", "gestora": "XP", "url": "https://exemplo/xina", "ativo": "não"},
        ])

        def tratar(metodo, url, params, data):
            if url.endswith("bova.csv"):
                return apoio.resposta(403, b"Access Denied")
            return apoio.resposta(200, b"ativo;peso\nX;1\n", {"Content-Type": "text/csv"})

        sessao = apoio.SessaoFalsa(tratar)
        ctx = apoio.contexto(self.config, hoje=HOJE, sessao=sessao)
        resumo = composicao.capturar_gestoras(ctx)
        self.assertEqual(resumo.estado, PARCIAL)
        self.assertEqual(len(sessao.chamadas), 2)  # XINA11 esta desligada
        self.assertTrue((composicao.pasta_gestoras(self.config) / "GOLD11" / f"{HOJE}.csv").exists())
        self.assertTrue(any("403" in o["motivo"] for o in ctx.manifesto.ocorrencias))


if __name__ == "__main__":
    unittest.main()
