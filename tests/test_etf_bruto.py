"""Testes do extrator bruto de ETF: armazem, rede, mapeamento, universo,
cobertura e orquestrador. Sem rede.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import argparse
import io
import json
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import requests
from rich.console import Console

try:  # `discover -s tests` poe tests/ no sys.path; `-m unittest tests.X` nao.
    from fixtures_comuns import temp_config
    import etf_bruto_apoio as apoio
except ImportError:  # pragma: no cover - depende de como a suite foi invocada
    from tests.fixtures_comuns import temp_config
    from tests import etf_bruto_apoio as apoio

from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market import b3_funds
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.etf_bruto import (
    armazem,
    b3_arquivos,
    cobertura,
    extrator,
    manual,
    mapa,
    rede,
    universo,
)
from shelf_monitor.market.etf_bruto.contexto import ERRO, OK, Pedido, ResumoFonte

HOJE = date(2026, 9, 24)


class _ComConfig(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        self.addCleanup(self.tmp.cleanup)


# --- armazem -----------------------------------------------------------------------


class TestArmazem(_ComConfig):
    def test_gravacao_atomica_nao_deixa_meio_arquivo(self):
        caminho = Path(self.tmp.name) / "a" / "x.csv"
        sha = armazem.gravar_atomico(caminho, b"primeiro")
        self.assertEqual(sha, armazem.sha256_de(b"primeiro"))
        with mock.patch.object(armazem.os, "replace", side_effect=OSError("disco cheio")):
            with self.assertRaises(OSError):
                armazem.gravar_atomico(caminho, b"segundo")
        self.assertEqual(caminho.read_bytes(), b"primeiro")
        self.assertEqual([p.name for p in caminho.parent.iterdir()], ["x.csv"])

    def test_csv_em_utf8_sig_com_ponto_e_virgula(self):
        caminho = Path(self.tmp.name) / "t.csv"
        _, total = armazem.gravar_csv(caminho, ("a", "b"), [{"a": "Índice", "b": None}, ["1", "2"]])
        self.assertEqual(total, 2)
        self.assertTrue(caminho.read_bytes().startswith(b"\xef\xbb\xbfa;b"))
        self.assertEqual(armazem.ler_csv(caminho), [{"a": "Índice", "b": ""}, {"a": "1", "b": "2"}])

    def test_entrada_copiada_uma_vez_por_conteudo(self):
        origem = Path(self.tmp.name) / "entrada"
        destino = Path(self.tmp.name) / "bruto"
        origem.mkdir()
        (origem / "BOVA11_carteira.csv").write_bytes(b"a;b\n1;2\n")
        (origem / "copia.csv").write_bytes(b"a;b\n1;2\n")  # mesmo conteudo, outro nome
        (origem / "~$planilha.xlsx").write_bytes(b"trava")
        manifesto = armazem.Manifesto(datetime.now(), {})
        novos = armazem.registrar_entradas(origem, destino, HOJE, manifesto, "gestoras")
        self.assertEqual([p.name for p in novos], ["BOVA11_carteira.csv"])
        self.assertEqual(armazem.registrar_entradas(origem, destino, HOJE, manifesto, "gestoras"), [])
        (origem / "BOVA11_carteira.csv").write_bytes(b"a;b\n1;3\n")  # refeito, mesmo nome
        novos = armazem.registrar_entradas(origem, destino, HOJE, manifesto, "gestoras")
        self.assertEqual(len(novos), 1)
        self.assertNotEqual(novos[0].name, "BOVA11_carteira.csv")  # nao sobrescreve o anterior

    def test_manifesto_relativo_ao_diretorio_de_dados(self):
        manifesto = armazem.Manifesto(datetime(2026, 9, 24, 22, 5, 12), {"fontes": ["b3-arquivos"]},
                                      base=self.config.data_dir)
        manifesto.arquivo("b3-arquivos", self.config.etf_bruto_dir / "x.csv", sha256="abc", linhas=3)
        caminho = manifesto.gravar(self.config.etf_bruto_dir / "_execucoes")
        self.assertEqual(caminho.name, "2026-09-24T220512.json")
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        self.assertEqual(dados["arquivos"][0]["caminho"], "etf/bruto/x.csv")


# --- rede --------------------------------------------------------------------------


class TestRede(_ComConfig):
    def test_sessao_tenta_de_novo_nos_erros_de_servidor(self):
        sessao = rede.sessao(self.config, tentativas=4, espera=1.5)
        politica = sessao.get_adapter("https://arquivos.b3.com.br").max_retries
        self.assertEqual(politica.total, 4)
        self.assertIn(503, politica.status_forcelist)
        self.assertIn("POST", politica.allowed_methods)
        self.assertEqual(sessao.headers["User-Agent"], self.config.user_agent)

    def test_pausa_entre_requisicoes_ao_mesmo_host_e_timeout_minimo(self):
        sessao = rede.SessaoEducada(intervalo=0.5, timeout_minimo=180)
        chamadas = []
        with mock.patch.object(requests.Session, "request", lambda self, m, u, *a, **kw: chamadas.append(kw) or "ok"), \
                mock.patch.object(rede.time, "sleep") as dormir, \
                mock.patch.object(rede.time, "monotonic", side_effect=[100.0, 100.1, 100.2, 100.3]):
            sessao.get("https://fnet.bmfbovespa.com.br/a", timeout=60)
            sessao.get("https://fnet.bmfbovespa.com.br/b", timeout=60)
        self.assertEqual(chamadas[0]["timeout"], 180)
        dormir.assert_called_once()
        self.assertAlmostEqual(dormir.call_args[0][0], 0.4, places=5)

    def test_obter_trata_ausente_erro_e_rede(self):
        sessao = apoio.SessaoFalsa(lambda m, u, p, d: apoio.resposta(400 if u.endswith("400") else 500))
        self.assertIsNone(rede.obter(sessao, "https://x/400", self.config, ausente=(400,)))
        with self.assertRaises(SourceUnavailable):
            rede.obter(sessao, "https://x/500", self.config, ausente=(400,))

        def cai(*_):
            raise requests.exceptions.ConnectionError("sem rota")
        with self.assertRaises(SourceUnavailable):
            rede.obter(apoio.SessaoFalsa(cai), "https://x", self.config)


# --- mapeamento ----------------------------------------------------------------------


class TestMapa(unittest.TestCase):
    def test_os_54_indicadores_nas_8_secoes(self):
        self.assertEqual(len(mapa.INDICADORES), 54)
        por_secao = [sum(1 for i in mapa.INDICADORES if i.secao == secao) for secao in mapa.SECOES]
        self.assertEqual(por_secao, [7, 10, 8, 8, 7, 6, 4, 4])
        self.assertEqual(len({i.chave for i in mapa.INDICADORES}), 54)

    def test_todo_insumo_tem_medidor_e_toda_via_tem_origem(self):
        for chave in mapa.insumos_do_mapa():
            cobertura.medidor(chave)  # KeyError se nao houver
        for item in mapa.INDICADORES:
            for via in ((item.recomendada,) if item.recomendada else ()) + item.alternativas:
                self.assertIn(via.origem, mapa.ORIGENS, item.chave)
                if not via.coletada:
                    self.assertEqual(via.insumos, (), item.chave)

    def test_regra_tem_texto_e_adaptador_existe(self):
        for item in mapa.INDICADORES:
            if item.estado == mapa.Estado.REGRA:
                self.assertTrue(item.regra, item.chave)
        for origem in mapa.ORIGENS.values():
            for adaptador in origem.adaptadores:
                self.assertIn(adaptador, extrator.ADAPTADORES)

    def test_as_fontes_do_mapeamento_estao_todas(self):
        nomes = {o.chave for o in mapa.ORIGENS.values() if not o.extra}
        self.assertTrue({"b3_arquivos", "fnet", "regulamento", "gestora", "indices_b3", "comdinheiro",
                         "xp_cadastro", "norma"} <= nomes)


# --- universo ------------------------------------------------------------------------


def _gravar_instrumentos(config, dia: str) -> None:
    _, cabecalho, linhas = b3_arquivos.ler_csv_b3(
        apoio.arquivo_b3(apoio.CABECALHO_INSTRUMENTOS, apoio.instrumentos(dia)))
    etfs = b3_arquivos.filtrar_instrumentos(cabecalho, linhas, set())
    armazem.gravar_csv(universo.pasta_instrumentos(config) / f"{dia}.csv",
                       cabecalho + list(b3_arquivos.COLUNAS_EXTRAS), etfs)


def _gravar_b3_fundos(config, dia: str) -> None:
    armazem.gravar_json(universo.pasta_b3_fundos(config) / f"{dia}.json", {"tipos": {
        "ETF": {"lista": [{"id": 990, "acronym": "BOVA", "fundName": "ISHARES IBOVESPA", "tradingName": "ISHARES BOVA"}],
                "detalhes": {"BOVA": {"idFNET": 990, "typeName": "ETF", "acronym": "BOVA", "tradingName": "ISHARES BOVA",
                                      "tradingCode": "BOVA11", "cnpj": "10.406.511/0001-61",
                                      "fundName": "ISHARES IBOVESPA CLASSE DE INDICE"}}},
        "ETF-RF": {"lista": [{"id": 5, "acronym": "IMAB"}],
                   "detalhes": {"IMAB": {"idFNET": 5, "typeName": "ETF-RF", "acronym": "IMAB", "tradingCode": "IMAB11",
                                         "cnpj": "34606480000150", "fundName": "IT NOW IMA-B FUNDO DE INDICE"},
                                "XPTO": {"erro": "HTTP 500"}}},
    }})


class TestUniverso(_ComConfig):
    def test_junta_b3_cadastro_cvm_e_tickers_csv(self):
        _gravar_b3_fundos(self.config, "2026-09-24")
        _gravar_instrumentos(self.config, "2026-09-23")
        catalogo = {
            "10406511000161": EtfMetadata("10406511000161", "ISHARES IBOVESPA CLASSE DE INDICE",
                                          manager_name="BLACKROCK BRASIL GESTORA"),
            "99999999000199": EtfMetadata("99999999000199", "ETF PRE OPERACIONAL FUNDO DE INDICE"),
        }
        b3_funds.write_tickers_csv(self.config.etf_tickers_path, [
            {"cnpj": "34606480000150", "ticker": "IMAB11", "nome_b3": "IT NOW IMAB", "fonte": "manual"},
        ])
        linhas = {l["chave"]: l for l in universo.montar_universo(self.config, catalogo, HOJE)}
        bova = linhas["10406511000161"]
        self.assertEqual((bova["ticker"], bova["isin"], bova["categoria_b3"], bova["tipo_b3"], bova["id_fnet"]),
                         ("BOVA11", "BRBOVACTF003", "ETF EQUITIES", "ETF", "990"))
        self.assertEqual((bova["listado"], bova["na_cvm"], bova["gestora"]), ("sim", "sim", "BLACKROCK BRASIL GESTORA"))
        imab = linhas["34606480000150"]
        self.assertEqual((imab["categoria_b3"], imab["liquidacao_dias"], imab["na_cvm"]),
                         ("FIXED INCOME TRADABLE INSTRUMENT T1", "1", "não"))
        # No cadastro de instrumentos, sem CNPJ conhecido: entra pelo ticker.
        self.assertIn("ticker:ACWI11", linhas)
        self.assertIn("ticker:03BK11", linhas)
        self.assertNotIn("ticker:ACWI11M", linhas)  # lote de bloco nao e outro ETF
        pre = linhas["99999999000199"]
        self.assertEqual((pre["listado"], pre["na_cvm"]), ("não", "sim"))
        self.assertEqual(len(universo.listados(list(linhas.values()))), 4)

    def test_acronimo_corrige_ticker_da_api_e_nome_igual_liga_a_cvm(self):
        """Em 2026-09-24 a API de fundos listados dava "T10R0" para o T10R11, e o
        CLOB11 so existia no cadastro de instrumentos e, sem ticker, na CVM."""
        dia = "2026-09-23"
        linhas_cadastro = apoio.instrumentos(dia) + [
            {"RptDt": dia, "TckrSymb": "T10R11", "Asst": "T10R", "SgmtNm": "FORWARD",
             "SctyCtgyNm": "FIXED INCOME TRADABLE INSTRUMENT T1", "ISIN": "BRT10RCTF003"},
            {"RptDt": dia, "TckrSymb": "CLOB11", "Asst": "CLOB", "SgmtNm": "CASH", "SctyCtgyNm": "ETF FOREIGN INDEX",
             "ISIN": "BRCLOBCTF000", "CrpnNm": "BTG PACTUAL DYNAMIC BALANCED CLO FUNDO DE ÍNDICE"},
        ]
        _, cabecalho, linhas = b3_arquivos.ler_csv_b3(apoio.arquivo_b3(apoio.CABECALHO_INSTRUMENTOS, linhas_cadastro))
        armazem.gravar_csv(universo.pasta_instrumentos(self.config) / f"{dia}.csv",
                           cabecalho + list(b3_arquivos.COLUNAS_EXTRAS),
                           b3_arquivos.filtrar_instrumentos(cabecalho, linhas, set()))
        armazem.gravar_json(universo.pasta_b3_fundos(self.config) / "2026-09-24.json", {"tipos": {"ETF-RF": {
            "lista": [{"id": 7, "acronym": "T10R"}],
            "detalhes": {"T10R": {"idFNET": 7, "typeName": "ETF-RF", "acronym": "T10R", "tradingCode": "T10R0",
                                  "cnpj": "55555555000155", "fundName": "IT NOW S&P TESOURO AMERICANO 10 ANOS"}}}}})
        catalogo = {"66666666000166": EtfMetadata("66666666000166", "BTG PACTUAL DYNAMIC BALANCED CLO FUNDO DE ÍNDICE")}
        linhas = {l["chave"]: l for l in universo.montar_universo(self.config, catalogo, HOJE)}
        self.assertEqual((linhas["55555555000155"]["ticker"], linhas["55555555000155"]["isin"]), ("T10R11", "BRT10RCTF003"))
        self.assertEqual((linhas["66666666000166"]["ticker"], linhas["66666666000166"]["listado"]), ("CLOB11", "sim"))
        self.assertNotIn("ticker:T10R11", linhas)
        self.assertNotIn("ticker:CLOB11", linhas)
        self.assertEqual((linhas["55555555000155"]["ligacao"], linhas["66666666000166"]["ligacao"]), ("acrônimo", "nome"))

    def test_cancelado_na_cvm_nao_e_listado_e_nome_abreviado_liga_so_sem_disputa(self):
        """Medido em 2026-09-24: ETF cancelado na CVM segue no cadastro da B3 com
        inicio de negociacao 9999-12-31; o cadastro abrevia e corta o nome."""
        dia = "2026-09-23"
        extra = [
            # Cancelado: fica no universo pelo ticker, mas nao conta como listado.
            {"RptDt": dia, "TckrSymb": "XBIO11", "Asst": "XBIO", "SgmtNm": "CASH", "SctyCtgyNm": "ETF FOREIGN INDEX",
             "TradgStartDt": "9999-12-31", "ISIN": "BRXBIOCTF002", "CrpnNm": "TREND ETF BIOTECHNOLOGY NASDAQ FDO INV ÍNDICE"},
            # Negocia e o nome abreviado so cabe num ETF da CVM sem ticker.
            {"RptDt": dia, "TckrSymb": "AGGX11", "Asst": "AGGX", "SgmtNm": "CASH", "SctyCtgyNm": "ETF FOREIGN INDEX",
             "TradgStartDt": "2026-09-01", "ISIN": "BRAGGXCTF012", "CrpnNm": "TREND ETF BLOOMBERG AGG US BONDS CL DE ÍND RL"},
            # Dois que negociam disputando o mesmo nome: nenhum liga.
            {"RptDt": dia, "TckrSymb": "USDB11", "Asst": "USDB", "SgmtNm": "CASH", "SctyCtgyNm": "ETF FOREIGN INDEX",
             "TradgStartDt": "2022-07-11", "ISIN": "BRUSDBCTF000", "CrpnNm": "INVESTO BLOOMBERG US BOND ETF FDO INV IND - IE"},
            {"RptDt": dia, "TckrSymb": "USDX11", "Asst": "USDX", "SgmtNm": "CASH", "SctyCtgyNm": "ETF FOREIGN INDEX",
             "TradgStartDt": "2023-01-02", "ISIN": "BRUSDXCTF000", "CrpnNm": "INVESTO BLOOMBERG US BOND ETF FDO INV IND - IE"},
        ]
        _, cabecalho, linhas = b3_arquivos.ler_csv_b3(apoio.arquivo_b3(apoio.CABECALHO_INSTRUMENTOS, extra))
        armazem.gravar_csv(universo.pasta_instrumentos(self.config) / f"{dia}.csv",
                           cabecalho + list(b3_arquivos.COLUNAS_EXTRAS),
                           b3_arquivos.filtrar_instrumentos(cabecalho, linhas, set()))
        catalogo = {
            "77777777000177": EtfMetadata("77777777000177", "TREND ETF BLOOMBERG AGGREGATE US BONDS CLASSE DE ÍNDICE - RESPONSABILIDADE LIMITADA"),
            "88888888000188": EtfMetadata("88888888000188", "INVESTO BLOOMBERG US BOND ETF CLASSE DE ÍNDICE - RESPONSABILIDADE LIMITADA"),
        }
        linhas = {l["chave"]: l for l in universo.montar_universo(self.config, catalogo, HOJE)}
        xbio = linhas["ticker:XBIO11"]
        self.assertEqual((xbio["listado"], xbio["negocia"]), ("não", "não"))
        self.assertEqual((linhas["77777777000177"]["ticker"], linhas["77777777000177"]["ligacao"]), ("AGGX11", "nome abreviado"))
        self.assertEqual(linhas["88888888000188"]["ticker"], "")
        self.assertIn("ticker:USDB11", linhas)
        self.assertIn("ticker:USDX11", linhas)


# --- cobertura -------------------------------------------------------------------------


def _gravar_negocios(config, dias: list[date], tickers_por_dia) -> None:
    cabecalho = apoio.CABECALHO_NEGOCIOS + list(b3_arquivos.COLUNAS_EXTRAS)
    for dia in dias:
        linhas = [{"RptDt": dia.isoformat(), "TckrSymb": t, "SgmtNm": "CASH", "LastPric": "10,00",
                   "x_tipo_linha": "padrao", "x_ticker_base": t} for t in tickers_por_dia(dia)]
        armazem.gravar_csv(b3_arquivos.pasta_negocios(config) / f"{dia.isoformat()}.csv", cabecalho, linhas)


class TestCobertura(_ComConfig):
    def setUp(self):
        super().setUp()
        self.universo = [
            {"chave": "10406511000161", "cnpj": "10406511000161", "ticker": "BOVA11", "nome": "ISHARES IBOVESPA",
             "listado": "sim", "tipo_b3": "ETF", "isin": "BRBOVACTF003"},
            {"chave": "22222222000122", "cnpj": "22222222000122", "ticker": "NOVO11", "nome": "NOVO ETF",
             "listado": "sim", "tipo_b3": "ETF"},
        ]
        fim = date(2026, 9, 23)
        dias = b3_arquivos.dias_uteis(fim - timedelta(days=110), fim)
        estreia = dias[9]  # NOVO11 negocia so nos ultimos 10 pregoes
        _gravar_negocios(self.config, dias, lambda d: ["BOVA11"] + (["NOVO11"] if d >= estreia else []))
        cabecalho = apoio.CABECALHO_INSTRUMENTOS + list(b3_arquivos.COLUNAS_EXTRAS)
        # A idade vem dos cadastros: o do comeco da janela ainda nao tinha o NOVO11.
        for dia, tickers in ((dias[-1], ("BOVA11",)), (estreia, ("BOVA11", "NOVO11")), (fim, ("BOVA11", "NOVO11"))):
            armazem.gravar_csv(universo.pasta_instrumentos(self.config) / f"{dia.isoformat()}.csv", cabecalho, [
                {"TckrSymb": t, "x_tipo_linha": "padrao", "x_ticker_base": t} for t in tickers])
        manual.atualizar_modelos(self.config, self.universo)
        caminho = manual.caminho(self.config, "xp_cadastro.csv")
        linhas = armazem.ler_csv(caminho)
        linhas[0]["classe_xp"] = "RV Brasil"
        armazem.gravar_csv(caminho, armazem.cabecalho_csv(caminho), linhas)

    def _linha(self, linhas, chave):
        return next(l for l in linhas if l.indicador.chave == chave)

    def test_contagens_por_indicador(self):
        linhas = cobertura.medir(self.config, self.universo, HOJE)
        self.assertEqual(len(linhas), 54)
        self.assertEqual(self._linha(linhas, "preco_fechamento").recomendada, 2)
        volume = self._linha(linhas, "volume_diario")
        self.assertEqual(volume.recomendada, 1)  # NOVO11 tem menos de 63 pregoes
        self.assertIn("63 pregões", volume.janela)
        self.assertEqual(self._linha(linhas, "classe_xp").recomendada, 1)
        self.assertEqual(self._linha(linhas, "classe_xp").cobertos_alternativa, 2)  # triagem pelo nome
        self.assertEqual(self._linha(linhas, "ticker_isin_lote").recomendada, 2)
        self.assertIsNone(self._linha(linhas, "preco_intradiario").recomendada)  # fora dos arquivos publicos
        self.assertEqual(self._linha(linhas, "valor_patrimonial").recomendada, 0)
        self.assertIn("regra: D+2", self._linha(linhas, "liquidacao").observacao)

    def _volume(self):
        return next(l for l in cobertura.medir(self.config, self.universo, HOJE) if l.indicador.chave == "volume_diario")

    def test_pregao_faltando_no_meio_da_janela_e_tolerado(self):
        dias = sorted(b3_arquivos.pasta_negocios(self.config).glob("*.csv"))
        dias[-30].unlink()  # um pregao no meio dos ultimos 63
        volume = self._volume()
        self.assertEqual(volume.recomendada, 1)
        self.assertIn("faltam 1 pregões em disco", volume.observacao)

    def test_pregao_faltando_na_ponta_da_janela_nao_e_tolerado(self):
        dias = sorted(b3_arquivos.pasta_negocios(self.config).glob("*.csv"))
        dias[-63].unlink()  # o pregao que abre a janela de 63
        volume = self._volume()
        self.assertEqual(volume.recomendada, 0)
        self.assertIn("janela incompleta", volume.observacao)

    def test_tributacao_so_pede_pmrc_de_renda_fixa(self):
        self.universo[1]["categoria_b3"] = "FIXED INCOME TRADABLE INSTRUMENT T1"
        tributacao = next(l for l in cobertura.medir(self.config, self.universo, HOJE)
                          if l.indicador.chave == "tributacao")
        self.assertEqual(tributacao.recomendada, 1)  # BOVA11 sim; NOVO11 (renda fixa) sem PMRC conferido
        self.assertIn("sem PMRC conferido", tributacao.observacao)

    def test_csv_da_cobertura(self):
        linhas = cobertura.medir(self.config, self.universo, HOJE)
        caminho = cobertura.gravar(self.config, linhas, HOJE)
        registros = armazem.ler_csv(caminho)
        self.assertEqual(list(registros[0]), list(cobertura.COLUNAS_CSV))
        volume = next(r for r in registros if r["indicador"] == "Volume diário")
        self.assertEqual((volume["cobertos_recomendada"], volume["universo"]), ("1", "2"))
        self.assertRegex(volume["janela"], r"de \d{2}/\d{2}/\d{4} a 23/09/2026")
        self.assertIn("https://", volume["links"])


# --- orquestrador e linha de comando ----------------------------------------------------


class TestOrquestrador(_ComConfig):
    def test_ordem_isolamento_e_manifesto(self):
        ordem = []

        def ok(ctx):
            ordem.append("a")
            return ResumoFonte(OK, "tudo certo", arquivos=1)

        def quebra(ctx):
            ordem.append("b")
            raise RuntimeError("fonte fora do ar")

        def depois(ctx):
            ordem.append("c")
            return ResumoFonte(OK, "rodou mesmo assim")

        adaptadores = {"a": ("A", ok), "b": ("B", quebra), "c": ("C", depois)}
        pedido = Pedido(fontes=("a", "b", "c"), dia=HOJE)
        console = Console(file=io.StringIO())
        resumos, _ = extrator.extrair(self.config, pedido, hoje=HOJE, console=console, catalogo={},
                                      adaptadores=adaptadores)
        self.assertEqual(ordem, ["a", "b", "c"])
        self.assertEqual(resumos["b"].estado, ERRO)
        self.assertIn("RuntimeError", resumos["b"].detalhe)
        manifestos = list((self.config.etf_bruto_dir / "_execucoes").glob("*.json"))
        self.assertEqual(len(manifestos), 1)
        dados = json.loads(manifestos[0].read_text(encoding="utf-8"))
        self.assertEqual(dados["fontes"]["b"]["estado"], ERRO)
        self.assertEqual(dados["pedido"]["fontes"], ["a", "b", "c"])

    def test_leitura_de_fontes(self):
        fontes, nomeadas, desconhecidas = extrator.ler_fontes("fnet-informe, b3-arquivos,xyz")
        self.assertEqual(fontes, ("b3-arquivos", "fnet-informe"))  # na ordem de execucao
        self.assertEqual(nomeadas, frozenset({"b3-arquivos", "fnet-informe"}))
        self.assertEqual(desconhecidas, ["xyz"])
        padrao, _, _ = extrator.ler_fontes(None)
        self.assertNotIn("cotahist", padrao)
        self.assertIn("comdinheiro", padrao)

    def _args(self, **kw):
        base = {"listar_fontes": False, "cobertura": False, "fontes": None, "dia": None, "desde": None, "forcar": False}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_cobertura_e_listagem_nao_usam_rede(self):
        armazem.gravar_csv(self.config.etf_universo_path, universo.COLUNAS_UNIVERSO, [
            {"chave": "1", "cnpj": "1", "ticker": "BOVA11", "nome": "ISHARES IBOVESPA", "listado": "sim"}])

        def sem_rede(*a, **kw):
            raise AssertionError("rede acessada")

        with mock.patch.object(requests.Session, "request", sem_rede), \
                mock.patch.object(extrator, "Console", lambda: Console(file=io.StringIO())):
            self.assertIsNone(extrator.cmd_etf_bruto(self.config, self._args(cobertura=True)))
            self.assertIsNone(extrator.cmd_etf_bruto(self.config, self._args(listar_fontes=True)))
        self.assertTrue(self.config.etf_cobertura_path.exists())

    def test_fonte_desconhecida_sai_com_erro(self):
        with mock.patch.object(extrator, "Console", lambda: Console(file=io.StringIO())):
            self.assertEqual(extrator.cmd_etf_bruto(self.config, self._args(fontes="b3,fnet")), 2)


if __name__ == "__main__":
    unittest.main()
