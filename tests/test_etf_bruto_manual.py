"""Testes das planilhas que gente preenche e da conferencia do regulamento.

A regra que nao pode quebrar: o codigo refaz as colunas de sistema e nunca
escreve numa coluna humana; ETF que sai do universo fica marcado, nao some.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

try:  # `discover -s tests` poe tests/ no sys.path; `-m unittest tests.X` nao.
    from fixtures_comuns import temp_config
    import etf_bruto_apoio as apoio
except ImportError:  # pragma: no cover - depende de como a suite foi invocada
    from tests.fixtures_comuns import temp_config
    from tests import etf_bruto_apoio as apoio

from shelf_monitor.market.etf_bruto import armazem, manual, regulamento
from shelf_monitor.market.etf_bruto.contexto import INDISPONIVEL, OK
from shelf_monitor.market.etf_bruto.fnet import pasta_regulamentos

FIXTURES = Path(__file__).parent / "fixtures" / "etf_bruto"
HOJE = date(2026, 9, 24)


def _paginas_da_amostra() -> list[str]:
    """A amostra em tres paginas, cortada nos capitulos."""
    texto = (FIXTURES / "regulamento_amostra.txt").read_text(encoding="utf-8")
    primeira, resto = texto.split("CAPÍTULO V -", 1)
    segunda, terceira = ("CAPÍTULO V -" + resto).split("CAPÍTULO VIII", 1)
    return [primeira, segunda, "CAPÍTULO VIII" + terceira]


def _universo(*tickers: str) -> list[dict]:
    nomes = {"BOVA11": "ISHARES IBOVESPA FUNDO DE INDICE", "GOLD11": "TREND ETF LBMA OURO",
             "IMAB11": "IT NOW IMA-B FUNDO DE INDICE", "SPXI11": "IT NOW S&P500 TRN FUNDO DE INDICE"}
    return [
        {"chave": f"{i:014d}", "cnpj": f"{i:014d}", "ticker": t, "nome": nomes.get(t, t), "listado": "sim",
         "gestora": "", "tipo_b3": "ETF"}
        for i, t in enumerate(tickers, start=1)
    ]


class _ComConfig(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        self.addCleanup(self.tmp.cleanup)


class TestPlanilhas(_ComConfig):
    def test_colunas_humanas_e_extras_sobrevivem(self):
        manual.atualizar_modelos(self.config, _universo("BOVA11", "GOLD11", "SPXI11"))
        caminho = manual.caminho(self.config, "xp_cadastro.csv")
        linhas = armazem.ler_csv(caminho)
        self.assertEqual([l["ticker"] for l in linhas], ["BOVA11", "GOLD11", "SPXI11"])
        self.assertEqual(linhas[0]["sugestao_classe_xp"], "RV Brasil")
        self.assertEqual(linhas[1]["sugestao_classe_xp"], "Commodities")
        # A pessoa preenche e acrescenta uma coluna propria.
        cabecalho = armazem.cabecalho_csv(caminho) + ["minha_nota"]
        linhas[0]["classe_xp"] = "RV Brasil"
        linhas[0]["risco_pontos"] = "14"
        linhas[0]["minha_nota"] = "conferido com o comitê"
        linhas[1]["observacao"] = "encerrando"
        armazem.gravar_csv(caminho, cabecalho, linhas)

        # Nova execucao: GOLD11 e SPXI11 sairam do universo, IMAB11 entrou, o nome do BOVA11 mudou.
        universo = _universo("BOVA11", "IMAB11")
        universo[0]["nome"] = "ISHARES IBOVESPA CLASSE DE INDICE"
        universo[1]["chave"] = universo[1]["cnpj"] = "00000000000009"
        resultados = manual.atualizar_modelos(self.config, universo)
        linhas = {l["ticker"]: l for l in armazem.ler_csv(caminho)}
        self.assertEqual(linhas["BOVA11"]["classe_xp"], "RV Brasil")
        self.assertEqual(linhas["BOVA11"]["risco_pontos"], "14")
        self.assertEqual(linhas["BOVA11"]["minha_nota"], "conferido com o comitê")
        self.assertEqual(linhas["BOVA11"]["nome"], "ISHARES IBOVESPA CLASSE DE INDICE")
        # Saiu mas tinha observacao escrita: fica, marcado. Saiu sem nada humano: sai.
        self.assertEqual((linhas["GOLD11"]["no_universo"], linhas["GOLD11"]["observacao"]), ("não", "encerrando"))
        self.assertNotIn("SPXI11", linhas)
        self.assertEqual(linhas["IMAB11"]["no_universo"], "sim")
        self.assertEqual(linhas["IMAB11"]["sugestao_classe_xp"], "Inflação")
        xp = next(r for r in resultados if r.arquivo == "xp_cadastro.csv")
        self.assertEqual((xp.novas, xp.atualizadas, xp.fora_do_universo), (1, 1, 1))

    def test_planilhas_so_de_cabecalho_nao_sao_reescritas(self):
        manual.atualizar_modelos(self.config, _universo("BOVA11"))
        urls = manual.caminho(self.config, "gestoras_urls.csv")
        self.assertEqual(armazem.cabecalho_csv(urls), list(manual.GESTORAS_URLS[1]))
        armazem.gravar_csv(urls, manual.GESTORAS_URLS[1], [{"ticker": "BOVA11", "url": "https://x"}])
        manual.atualizar_modelos(self.config, _universo("BOVA11"))
        self.assertEqual(armazem.ler_csv(urls)[0]["url"], "https://x")

    def test_csv_salvo_pelo_excel_em_cp1252(self):
        caminho = manual.caminho(self.config, "formador.csv")
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes("chave;ticker;formador\n00000000000001;BOVA11;Itaú BBA\n".encode("cp1252"))
        self.assertEqual(armazem.ler_csv(caminho)[0]["formador"], "Itaú BBA")
        manual.atualizar_modelos(self.config, _universo("BOVA11"))
        self.assertEqual(armazem.ler_csv(caminho)[0]["formador"], "Itaú BBA")

    def test_planilha_aberta_no_excel_fica_como_estava(self):
        with mock.patch.object(manual.armazem, "gravar_csv", side_effect=PermissionError("travado")):
            resultado = manual.atualizar_planilha(manual.caminho(self.config, "x.csv"), ("chave",), ("nota",),
                                                  [{"chave": "1"}])
        self.assertTrue(resultado.travada)
        self.assertIn("aberta em outro programa", resultado.texto())

    def test_codigo_b3_humano_vence_a_sugestao(self):
        universo = _universo("BOVA11", "SPXI11")
        manual.atualizar_modelos(self.config, universo)
        self.assertEqual(manual.codigos_b3(self.config, universo), {"00000000000001": "IBOV"})
        caminho = manual.caminho(self.config, "indices.csv")
        linhas = armazem.ler_csv(caminho)
        linhas[0]["codigo_b3"] = "ibxx"
        armazem.gravar_csv(caminho, armazem.cabecalho_csv(caminho), linhas)
        self.assertEqual(manual.codigos_b3(self.config, universo)["00000000000001"], "IBXX")


class TestRegrasDoRegulamento(unittest.TestCase):
    def test_candidato_de_cada_campo_com_pagina(self):
        achados = regulamento.candidatos(_paginas_da_amostra())
        primeiro = {campo: (lista[0].valor, lista[0].pagina) for campo, lista in achados.items() if lista}
        self.assertEqual(primeiro["taxa_administracao"], ("0,30%", 2))
        self.assertEqual(primeiro["taxa_maxima"], ("0,55%", 2))
        self.assertEqual(primeiro["publico_alvo"], ("em geral", 1))
        self.assertEqual(primeiro["pmrc"], ("720 dias", 3))
        self.assertEqual(primeiro["politica_distribuicao"], ("reinveste", 3))
        self.assertEqual(primeiro["taxa_performance"], ("não cobra", 2))
        self.assertTrue(primeiro["indice_referencia"][0].startswith("S&P 500"))
        self.assertIn("0,25%", [c.valor for c in achados["taxa_fundo_investido"]])
        extras = [c.valor for c in achados["custos_extras"]]
        self.assertIn("consultoria 0,05%", extras)
        self.assertIn("mínimo mensal R$ 10.000,00", extras)

    def test_trecho_mostra_o_contexto(self):
        candidato = regulamento.candidatos(_paginas_da_amostra())["taxa_administracao"][0]
        self.assertIn("0,30%", candidato.trecho)
        self.assertIn("taxa de administração", candidato.trecho)

    def test_palavra_quebrada_no_fim_da_linha_e_religada(self):
        self.assertEqual(regulamento.limpar("taxa de adminis-\ntração de 0,20%"), "taxa de administração de 0,20%")

    def test_espaco_que_o_pypdf_poe_no_meio_da_palavra(self):
        """Medido no regulamento do LTBX11 em 2026-09-24: "T AXA GLOBAL Valor da Taxa: 0,14%"."""
        achados = regulamento.candidatos(["5.1. T AXA GLOBAL Valor da Taxa: 0,14% (quatorze centésimos por cen to) ao ano."])
        self.assertEqual(achados["taxa_administracao"][0].valor, "0,14%")

    def test_secao_indice_de_referencia_e_a_melhor_fonte_do_nome(self):
        """Como nos regulamentos da Trend e da BlackRock medidos em 2026-09-24."""
        achados = regulamento.candidatos([
            "3. POLÍTICA DE INVESTIMENTOS 3.1. ÍNDICE DE REFERÊNCIA O índice Bovespa - Ibovespa, registrado sob o "
            "código ISIN BRIBOVINDM18(“Índice”), é um índice de mercado.",
        ])["indice_referencia"]
        self.assertEqual(achados[0].valor, "Bovespa - Ibovespa")
        self.assertEqual(achados[0].regra, "seção ÍNDICE DE REFERÊNCIA")

    def test_indice_de_referencia_seguido_de_em_nao_e_definicao(self):
        """"indice de referencia em decorrencia de custos" nao define o indice (SPXI11, 2026-09-24)."""
        achados = regulamento.candidatos([
            "A classe pode ter desempenho diferente do índice de referência em decorrência de custos operacionais.",
        ])["indice_referencia"]
        self.assertEqual(achados, [])

    def test_indice_definido_entre_parenteses_vence_mencao_solta(self):
        paginas = [
            "É vedado operar derivativos, exceto para replicar o índice de referência: - posições em futuros.",
            "O Fundo busca refletir a variação do índice NASDAQ 100 (“Índice”), provido pela NASDAQ.",
        ]
        achados = regulamento.candidatos(paginas)["indice_referencia"]
        self.assertEqual(achados[0].valor, "NASDAQ 100")
        self.assertEqual(achados[0].pagina, 2)

    def test_sem_pypdf_a_mensagem_diz_o_que_fazer(self):
        with mock.patch.dict(sys.modules, {"pypdf": None}):
            with self.assertRaises(regulamento.PypdfAusente) as erro:
                regulamento.texto_do_pdf(Path("qualquer.pdf"))
        self.assertIn("pip install pypdf", str(erro.exception))


class TestPlanilhaDoRegulamento(_ComConfig):
    def _pdf(self, cnpj: str, doc_id: str) -> Path:
        caminho = pasta_regulamentos(self.config) / cnpj / f"{doc_id}.pdf"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(b"%PDF-1.7 falso")
        return caminho

    def _extrair(self, universo):
        ctx = apoio.contexto(self.config, hoje=HOJE, universo=universo)
        with mock.patch.object(regulamento, "texto_em_cache", return_value=_paginas_da_amostra()):
            return regulamento.extrair(ctx)

    def test_candidatos_vao_para_a_planilha_e_a_conferencia_fica(self):
        universo = _universo("BOVA11", "GOLD11")
        self._pdf("00000000000001", "100")
        resumo = self._extrair(universo)
        self.assertEqual(resumo.estado, OK)
        caminho = manual.caminho(self.config, "regulamento.csv")
        linhas = {(l["ticker"], l["campo"]): l for l in armazem.ler_csv(caminho)}
        self.assertEqual(len(linhas), 2 * len(regulamento.CAMPOS))
        taxa = linhas[("BOVA11", "taxa_administracao")]
        self.assertEqual((taxa["candidato"], taxa["pagina"], taxa["status"], taxa["doc_id"]),
                         ("0,30%", "2", "com candidato", "100"))
        self.assertEqual(linhas[("GOLD11", "taxa_administracao")]["status"], "sem regulamento")
        # Nenhuma coluna humana foi preenchida pelo codigo.
        for linha in linhas.values():
            for coluna in regulamento.HUMANO:
                self.assertEqual(linha[coluna], "")

        # A pessoa confere; sai um regulamento novo; a conferencia fica e o aviso aparece.
        todas = armazem.ler_csv(caminho)
        for linha in todas:
            if linha["ticker"] == "BOVA11" and linha["campo"] == "taxa_administracao":
                linha.update({"valor_conferido": "0,30%", "conferido_por": "Ana", "doc_id_conferido": "100"})
        armazem.gravar_csv(caminho, armazem.cabecalho_csv(caminho), todas)
        self._pdf("00000000000001", "200")
        self._extrair(universo)
        taxa = {(l["ticker"], l["campo"]): l for l in armazem.ler_csv(caminho)}[("BOVA11", "taxa_administracao")]
        self.assertEqual((taxa["valor_conferido"], taxa["conferido_por"]), ("0,30%", "Ana"))
        self.assertEqual((taxa["doc_id"], taxa["regulamento_mudou"]), ("200", "sim"))

    def test_campo_vem_do_regulamento_mais_novo_que_o_tem(self):
        """O regulamento de 05/2026 do GPUS11 era so a parte geral; a taxa estava no de 05/2025."""
        self._pdf("00000000000001", "100")  # antigo, completo
        self._pdf("00000000000001", "200")  # novo, so a parte geral
        paginas = {"100": _paginas_da_amostra(), "200": ["Parte geral do regulamento. " * 20]}
        ctx = apoio.contexto(self.config, hoje=HOJE, universo=_universo("BOVA11"))
        with mock.patch.object(regulamento, "texto_em_cache", side_effect=lambda pdf: paginas[pdf.stem]):
            regulamento.extrair(ctx)
        linhas = {l["campo"]: l for l in armazem.ler_csv(manual.caminho(self.config, "regulamento.csv"))}
        self.assertEqual((linhas["taxa_administracao"]["candidato"], linhas["taxa_administracao"]["doc_id"]), ("0,30%", "100"))

    def test_sem_pypdf_a_fonte_fica_indisponivel(self):
        self._pdf("00000000000001", "100")
        ctx = apoio.contexto(self.config, hoje=HOJE, universo=_universo("BOVA11"))
        with mock.patch.object(regulamento, "texto_em_cache", side_effect=regulamento.PypdfAusente("pypdf não instalado")):
            resumo = regulamento.extrair(ctx)
        self.assertEqual(resumo.estado, INDISPONIVEL)

    def test_pdf_digitalizado_sem_texto(self):
        self._pdf("00000000000001", "100")
        ctx = apoio.contexto(self.config, hoje=HOJE, universo=_universo("BOVA11"))
        with mock.patch.object(regulamento, "texto_em_cache", return_value=["", "   "]):
            regulamento.extrair(ctx)
        linha = armazem.ler_csv(manual.caminho(self.config, "regulamento.csv"))[0]
        self.assertTrue(linha["status"].startswith("sem texto"))


if __name__ == "__main__":
    unittest.main()
