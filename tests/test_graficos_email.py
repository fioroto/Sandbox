"""Graficos do e-mail do digest: tabela, celula e cor em linha.

O e-mail nao roda script, o Gmail tira `<svg>` e `<style>` e o Outlook ignora
largura de `div`. Os graficos do digest sao tabelas (report/shared/
graficos_email.py). Os testes travam:

- a geometria de cada componente (largura da barra em %, as duas metades da
  barra divergente, segmentos que somam 100, altura das colunas, pontos
  pintados) e o que ele devolve sem dado: nada, nunca moldura vazia;
- o escape de todo rotulo (nome de fundo e categoria vem de fonte externa);
- que nenhum componente, e o digest inteiro, carregue `<svg>`, `<style>`,
  `<script>` ou `<img>`;
- no digest: a figura sai quando ha dado e some quando o bloco esta vazio, com
  titulo que afirma a leitura e a cor da casa-base na captacao;
- na previdencia, figura nenhuma reintroduz o que a supressao tirou.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import re
import unittest
from datetime import datetime
from pathlib import Path

from shelf_monitor.diff import diff_shelf
from shelf_monitor.models import (
    CollectorStrategy,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.previdencia.config import ConfigPrevidencia
from shelf_monitor.report.shared.estilo import (
    ETIQUETA,
    GRADE,
    ZERO,
    cor_da_casa,
    passo_da_rampa,
)
from shelf_monitor.report.shared.graficos_email import (
    MARCA_DA_FIGURA,
    Celula,
    Linha,
    LinhaDeCalor,
    Oposta,
    Segmento,
    Serie,
    barra_segmentada,
    barras,
    barras_divergentes,
    barras_opostas,
    colunas_por_casa,
    figura,
    grade_de_pontos,
    grades_por_casa,
    mapa_de_calor,
)

_PROIBIDOS = ("<svg", "<style", "<script", "<img", "<div")


def _larguras_das_barras(html: str) -> list[int]:
    """As larguras (%) das tabelas de barra, na ordem em que aparecem."""
    return [
        int(w) for w in re.findall(
            r'width="(\d+)%"(?: align="right")?><tr><td height="12"', html
        )
    ]


class TestFigura(unittest.TestCase):
    def test_anatomia_etiqueta_titulo_subtitulo_corpo_fonte(self):
        html = figura("XP capta menos", "R$ milhões, de 01/07/2026 a 21/09/2026.",
                      "<table><tr><td>corpo</td></tr></table>", "Fonte: CVM.")
        self.assertIn(MARCA_DA_FIGURA, html)
        self.assertIn(f'bgcolor="{ETIQUETA}"', html)
        # Na ordem da linguagem visual: etiqueta, titulo, subtitulo, corpo, fonte.
        posicoes = [html.index(t) for t in (MARCA_DA_FIGURA, "XP capta menos",
                                            "de 01/07/2026", "corpo", "Fonte: CVM.")]
        self.assertEqual(posicoes, sorted(posicoes))

    def test_sem_corpo_nao_ha_moldura(self):
        self.assertEqual(figura("Título", "Subtítulo", ""), "")
        self.assertEqual(figura("Título", "Subtítulo", barras([])), "")

    def test_titulo_e_subtitulo_sao_escapados(self):
        html = figura("<b>injetado</b>", "a < b", "x")
        self.assertNotIn("<b>injetado", html)
        self.assertIn("&lt;b&gt;injetado", html)
        self.assertIn("a &lt; b", html)


class TestBarras(unittest.TestCase):
    def test_largura_proporcional_ao_maior_valor(self):
        html = barras([
            Linha("BTG", 200, "200", cor_da_casa("btg")),
            Linha("XP", 100, "100", cor_da_casa("xp"), destaque=True),
            Linha("Inter", 50, "50", cor_da_casa("inter")),
        ])
        self.assertEqual(_larguras_das_barras(html), [100, 50, 25])
        # Cor da casa na barra; valor em tinta, nunca na cor da serie.
        self.assertIn(f'bgcolor="{cor_da_casa("xp")}"', html)
        self.assertNotIn(f'color:{cor_da_casa("xp")}', html)
        # A casa-base em negrito.
        self.assertIn("<strong>XP</strong>", html)

    def test_valor_minusculo_ainda_aparece(self):
        html = barras([Linha("A", 1000, "1000"), Linha("B", 1, "1")])
        self.assertEqual(_larguras_das_barras(html), [100, 1])

    def test_zero_e_vazio(self):
        self.assertEqual(barras([]), "")
        self.assertEqual(barras([Linha("A", 0, "0")]), "")
        # Uma linha zerada entre outras: a linha fica, a barra nao.
        html = barras([Linha("A", 10, "10"), Linha("B", 0, "0")])
        self.assertEqual(_larguras_das_barras(html), [100])
        self.assertIn(">B<", html)

    def test_rotulo_e_escapado(self):
        html = barras([Linha("Fundo <script>alert(1)</script>", 1, "<1>")])
        self.assertNotIn("<script", html)
        self.assertIn("Fundo &lt;script&gt;", html)
        self.assertIn("&lt;1&gt;", html)

    def test_rotulo_em_cima_para_nome_longo(self):
        html = barras([Linha("Renda Fixa Duração Livre Crédito Livre", 5, "5")],
                      rotulo_em_cima=True)
        self.assertIn('colspan="2"', html)


class TestDivergentes(unittest.TestCase):
    def test_metades_na_proporcao_dos_extremos(self):
        html = barras_divergentes([
            Linha("BTG", 300, "+300"), Linha("XP", -100, "-100"),
        ])
        # Area de 55% (100 - rotulo 27 - valor 18): 300 contra 100 -> 14% e 41%.
        self.assertIn('<td width="14%" align="right"', html)
        self.assertIn('<td width="41%" style="border-left:2px solid', html)
        # Negativo vai para a esquerda, alinhado ao zero; positivo para a direita.
        self.assertIn('width="100%" align="right"', html)
        self.assertEqual(_larguras_das_barras(html), [100, 100])
        self.assertIn(f"border-left:2px solid {ZERO}", html)

    def test_so_positivos_nao_desenham_metade_vazia(self):
        html = barras_divergentes([Linha("A", 10, "+10"), Linha("B", 5, "+5")])
        self.assertNotIn('align="right" valign="middle" style="padding:3px 0;"', html)
        self.assertEqual(_larguras_das_barras(html), [100, 50])

    def test_vazio(self):
        self.assertEqual(barras_divergentes([]), "")
        self.assertEqual(barras_divergentes([Linha("A", 0, "0")]), "")


class TestOpostas(unittest.TestCase):
    def test_dois_lados_na_mesma_linha(self):
        html = barras_opostas([
            Oposta("BTG", 2, 4, "-2", "+4"), Oposta("XP", 0, 1, "0", "+1"),
        ])
        self.assertIn(">-2<", html)
        self.assertIn(">+4<", html)
        self.assertEqual(_larguras_das_barras(html), [100, 100, 25])

    def test_lado_todo_zerado_sai(self):
        # Nenhuma saida: sem metade esquerda e sem a coluna de "0" contra o zero.
        html = barras_opostas([Oposta("XP", 0, 1, "0", "+1")])
        self.assertNotIn(">0<", html)
        self.assertIn(">+1<", html)
        self.assertEqual(barras_opostas([Oposta("XP", 0, 0, "0", "0")]), "")


class TestSegmentada(unittest.TestCase):
    def _segmentos(self):
        return [
            Segmento("Já cobertos", 60, "60"),
            Segmento("Outra liquidez", 14, "14"),
            Segmento("Reais: estratégia", 179, "179"),
            Segmento("Reais: gestora", 148, "148"),
            Segmento("Reais: sem carteira", 220, "220"),
        ]

    def test_segmentos_somam_100(self):
        html = barra_segmentada(self._segmentos())
        faixa = html.split("</table>")[0]
        larguras = [int(w) for w in re.findall(r'<td width="(\d+)%" height="16"', faixa)]
        self.assertEqual(sum(larguras), 100)
        self.assertEqual(len(larguras), 5)

    def test_rotulo_que_nao_cabe_desce_para_a_legenda(self):
        html = barra_segmentada(self._segmentos())
        rotulos, legenda = html.split("</table>")[1], html.split("</table>")[2]
        # 60 de 621 (~10%) e 14 (~2%) nao cabem embaixo do segmento no telefone.
        self.assertNotIn("Já cobertos", rotulos)
        self.assertIn("Já cobertos", legenda)
        self.assertIn("Outra liquidez", legenda)
        self.assertIn("Reais: sem carteira", rotulos)

    def test_parte_minuscula_nao_some(self):
        html = barra_segmentada([Segmento("A", 1000, "1000"), Segmento("B", 1, "1")])
        larguras = [int(w) for w in re.findall(r'<td width="(\d+)%" height="16"', html)]
        self.assertEqual(larguras, [99, 1])

    def test_vazio(self):
        self.assertEqual(barra_segmentada([]), "")
        self.assertEqual(barra_segmentada([Segmento("A", 0, "0")]), "")


class TestMapaDeCalor(unittest.TestCase):
    def test_celula_pintada_na_rampa_e_contagem_sem_cor(self):
        html = mapa_de_calor(["Captando", "Saída"], [
            LinhaDeCalor("XP", (Celula(26, "26%"), Celula(67, "67%")), True, "559 fundos"),
            LinhaDeCalor("BTG", (Celula(31, "31%"), Celula(None, "-")), False),
        ])
        fundo, tinta = passo_da_rampa(67, 67)
        self.assertIn(f'bgcolor="{fundo}"', html)
        self.assertIn(f"color:{tinta}", html)
        self.assertIn("559 fundos", html)
        self.assertIn("<strong>XP</strong>", html)

    def test_vazio(self):
        self.assertEqual(mapa_de_calor(["A"], []), "")
        self.assertEqual(mapa_de_calor(["A"], [LinhaDeCalor("X", (Celula(0, "0%"),))]), "")


class TestColunasPorCasa(unittest.TestCase):
    def test_mesma_escala_e_zero_vermelho(self):
        html = colunas_por_casa([
            Serie("XP", (-10.0, -30.0), cor_da_casa("xp"), True, "set/2026: -30"),
            Serie("BTG", (30.0, None), cor_da_casa("btg")),
        ], ["ago", "set"], altura=60)
        # Metade de cima e de baixo na proporcao dos extremos: 30 e 30.
        alturas = [int(h) for h in re.findall(r'<td height="(\d+)" bgcolor="#', html)]
        self.assertIn(30, alturas)   # BTG +30 e XP -30 ocupam a metade inteira
        self.assertIn(10, alturas)   # XP -10, um terco
        self.assertIn(f'bgcolor="{ZERO}"', html)
        self.assertIn("set/2026: -30", html)

    def test_vazio(self):
        self.assertEqual(colunas_por_casa([], ["ago"]), "")
        self.assertEqual(colunas_por_casa([Serie("XP", (None, 0.0), "#000")], ["a", "b"]), "")


class TestGradeDePontos(unittest.TestCase):
    @staticmethod
    def _pintados(html: str, cor: str) -> int:
        """Quadrados na cor: a celula pinta a si mesma ou herda da linha."""
        total = 0
        for fundo, celulas in re.findall(r'<tr bgcolor="([^"]+)">(.*?)</tr>', html):
            for celula in re.findall(r"<td([^>]*)></td>", celulas):
                propria = re.search(r'bgcolor="([^"]+)"', celula)
                total += (propria.group(1) if propria else fundo) == cor
        return total

    def test_cem_quadrados_e_os_pintados_sao_a_parcela(self):
        html = grade_de_pontos(0.23, cor_da_casa("xp"))
        self.assertEqual(html.count("<td"), 100)
        self.assertEqual(self._pintados(html, cor_da_casa("xp")), 23)
        self.assertEqual(self._pintados(html, GRADE), 77)
        # Enche de baixo para cima: a ultima linha e toda da cor.
        self.assertTrue(html.split("<tr")[-1].startswith(f' bgcolor="{cor_da_casa("xp")}"'))
        # E cabe no orcamento do e-mail: 100 celulas em menos de 2 KB.
        self.assertLess(len(html), 2000)

    def test_limites(self):
        self.assertEqual(grade_de_pontos(None, "#000"), "")
        self.assertEqual(self._pintados(grade_de_pontos(1.4, "#000"), "#000"), 100)
        self.assertEqual(self._pintados(grade_de_pontos(0, "#000"), "#000"), 0)
        self.assertEqual(self._pintados(grade_de_pontos(0.05, "#000"), "#000"), 5)

    def test_uma_grade_por_casa(self):
        html = grades_por_casa([
            Linha("XP", 0.23, "23% do PL", cor_da_casa("xp"), True),
            Linha("BTG", 0.20, "20% do PL", cor_da_casa("btg")),
        ])
        self.assertIn("23% do PL", html)
        self.assertEqual(self._pintados(html, cor_da_casa("btg")), 20)
        self.assertEqual(grades_por_casa([]), "")


class TestNadaProibido(unittest.TestCase):
    def test_nenhum_componente_usa_tag_que_o_e_mail_descarta(self):
        pecas = [
            barras([Linha("<A>", 1, "1")]),
            barras_divergentes([Linha("A", -1, "-1"), Linha("B", 2, "2")]),
            barras_opostas([Oposta("A", 1, 2, "-1", "+2")]),
            barra_segmentada([Segmento("A", 1, "1"), Segmento("B", 3, "3")]),
            mapa_de_calor(["c"], [LinhaDeCalor("A", (Celula(1, "1"),))]),
            colunas_por_casa([Serie("A", (1.0, -1.0), "#000")], ["a", "b"]),
            grades_por_casa([Linha("A", 0.5, "50%")]),
            figura("t", "s", "c", "f"),
        ]
        for peca in pecas:
            self.assertTrue(peca)
            for proibido in _PROIBIDOS:
                self.assertNotIn(proibido, peca)
            # Toda tabela com texto e de apresentacao: leitor de tela nao anuncia
            # grade. So a barra (uma celula vazia) dispensa o atributo.
            barras_sem_texto = peca.count('<table cellpadding="0" cellspacing="0" width=')
            self.assertEqual(
                peca.count("<table") - barras_sem_texto, peca.count('role="presentation"')
            )


# --- no digest ---------------------------------------------------------------

_BLOCOS = json.loads(
    (Path(__file__).parent / "fixtures" / "previdencia" / "blocos_2026-09-23.json")
    .read_text(encoding="utf-8")
)
_AGORA = datetime(2026, 9, 23, 22, 0)


def _vitrine(platform: str, cnpjs: list[str], quando: datetime = _AGORA) -> ShelfSnapshot:
    fundos = []
    for i, cnpj in enumerate(cnpjs):
        fundo = FundOffer(
            platform=platform, name=f"{platform.upper()} Fundo {i} FIC FIM", cnpj=cnpj
        )
        fundo.sources = [CollectorStrategy.API_JSON.value]
        fundos.append(fundo)
    return ShelfSnapshot(
        platform=platform, collected_at=quando, strategy=CollectorStrategy.API_JSON,
        status=SnapshotStatus.OK, funds=fundos,
    )


def _relatorio(suprimidos: dict | None = None, com_blocos: bool = True) -> RunReport:
    xp = _vitrine("xp", ["11111111000111", "22222222000122", "33333333000133"])
    btg = _vitrine("btg", ["11111111000111", "44444444000144", "55555555000155"])
    report = RunReport(
        generated_at=_AGORA, snapshots=[xp, btg],
        shelf_diffs=[diff_shelf(xp, None), diff_shelf(btg, None)],
        suprimidos=dict(suprimidos or {}),
    )
    if com_blocos:
        report.performance = json.loads(json.dumps(_BLOCOS["performance"]))
        report.retail_flow = json.loads(json.dumps(_BLOCOS["retail_flow"]))
        report.grade_opportunities = json.loads(json.dumps(_BLOCOS["grade_opportunities"]))
    return report


def _texto(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class TestDigest(unittest.TestCase):
    def test_html_do_digest_nao_tem_tag_proibida(self):
        from shelf_monitor.report.digest import render_html

        for suprimidos in ({}, dict(ConfigPrevidencia.blocos_suprimidos)):
            html = render_html(_relatorio(suprimidos))
            for proibido in ("<svg", "<style", "<script", "<img"):
                self.assertNotIn(proibido, html)

    def test_casca_de_e_mail(self):
        from shelf_monitor.report.digest import _frase_principal, render_html
        from shelf_monitor.report.digest_visao import montar_visao

        report = _relatorio()
        html = render_html(report)
        self.assertIn('<meta name="color-scheme" content="light">', html)
        self.assertIn('<meta name="supported-color-schemes" content="light">', html)
        self.assertIn('max-width:640px', html)
        self.assertIn('<!--[if mso]><table role="presentation" width="640"', html)
        # A previa escondida e a frase de abertura, sem o negrito do markdown.
        previa = re.search(r'<div style="display:none;[^"]*">([^<]*)', html).group(1)
        frase = _frase_principal(montar_visao(report))[0].replace("**", "")
        self.assertTrue(previa.startswith(frase[:40]))
        self.assertIn("<title>Monitor de Prateleiras de Fundos</title>", html)

    def test_sem_bloco_nao_ha_figura(self):
        from shelf_monitor.report.digest import (
            _html_performance_section,
            _html_ritmo,
            render_html,
        )
        from shelf_monitor.report.digest_visao import montar_visao

        vazio = _relatorio(com_blocos=False)
        self.assertEqual(_html_ritmo(montar_visao(vazio)), "")
        self.assertEqual(_html_performance_section(vazio), "")
        # Sem captacao, radar nem rentabilidade, nenhuma figura - e nenhuma
        # moldura vazia. Os gaps saem em tabela, pelo esforco.
        for edicao in ("semanal", "mensal"):
            html = render_html(vazio, None, edicao)
            self.assertEqual(html.count(MARCA_DA_FIGURA), 0)
            self.assertIn("a captação deles não foi medida", html)

    def test_cada_secao_com_dado_ganha_figura(self):
        from shelf_monitor.report.digest import _html_performance_section

        html = _html_performance_section(_relatorio())
        # Rentabilidade da janela e PL abaixo da mediana: duas figuras.
        self.assertEqual(html.count(MARCA_DA_FIGURA), 2)
        self.assertIn("tem a maior rentabilidade ponderada por PL em 3 meses", html)
        self.assertIn("1 quadrado = 1%", html)

    def test_previdencia_nao_ganha_figura_de_gap_nem_de_cotista(self):
        from shelf_monitor.report.digest import _html_oportunidade, render_html
        from shelf_monitor.report.digest_visao import montar_visao

        report = _relatorio(dict(ConfigPrevidencia.blocos_suprimidos))
        gaps = _html_oportunidade(montar_visao(report))
        self.assertNotIn(MARCA_DA_FIGURA, gaps)
        self.assertIn("Não se aplica a este monitor", gaps)
        # As mesmas proibicoes de test_supressao, agora tambem sobre os titulos
        # e rodapes das figuras - na edicao mensal, que tem a rentabilidade.
        texto = _texto(render_html(report, None, "mensal"))
        for proibido in ("Ponderada por cotistas", "ponderada por cotistas",
                         "PL menos cotistas", "muita gente", " cotistas -",
                         "Só nesta vitrine", "só ela exibe", "inclusão operacional",
                         "faltam na XP", "Produto próprio"):
            self.assertNotIn(proibido, texto)

    def test_o_ritmo_e_o_mapa_de_calor_e_a_propria_tabela(self):
        from shelf_monitor.report.digest import _html_ritmo
        from shelf_monitor.report.digest_visao import montar_visao
        from test_captacao import _relatorio as relatorio_de_captacao

        visao = montar_visao(relatorio_de_captacao())
        html = _html_ritmo(visao)
        self.assertEqual(html.count(MARCA_DA_FIGURA), 1)
        self.assertIn("dos fundos em saída persistente", html)
        # O mapa de calor substitui a tabela de ritmo, com as mesmas celulas; a
        # contagem de fundos desce para baixo do nome da casa.
        self.assertNotIn(">Fundos</th>", html)
        self.assertIn(" fundos</span>", html)
        # Sem a figura (orcamento), a tabela volta.
        sem_ritmo = _html_ritmo(visao, frozenset({"ritmo"}))
        self.assertIn(">Fundos</th>", sem_ritmo)
        self.assertEqual(sem_ritmo.count(MARCA_DA_FIGURA), 0)

    def test_cor_de_saldo_e_uma_so_e_vem_com_sinal(self):
        from shelf_monitor.report.digest import _cor_do_valor
        from shelf_monitor.report.shared.estilo import BOM_TEXTO, CRITICO_TEXTO

        self.assertEqual(_cor_do_valor("+1,0 MM"), f"color:{BOM_TEXTO};")
        self.assertEqual(_cor_do_valor("-1,0 MM"), f"color:{CRITICO_TEXTO};")
        self.assertEqual(_cor_do_valor("-"), "")
        self.assertEqual(_cor_do_valor("12%"), "")

    def test_markdown_nao_ganha_grafico(self):
        from shelf_monitor.report.digest import render_markdown

        md = render_markdown(_relatorio())
        self.assertNotIn("<table", md)
        self.assertNotIn(ETIQUETA, md)


class TestOrcamentoDoEmail(unittest.TestCase):
    """O Gmail corta o e-mail acima de ~102 KB: passado o orcamento, as figuras
    saem (as tabelas ficam) e o rodape diz quais."""

    def test_dentro_do_orcamento_nada_sai(self):
        from shelf_monitor.report import digest

        html = digest.render_html(_relatorio())
        self.assertLess(len(html.encode("utf-8")), digest._ORCAMENTO_DO_EMAIL)
        self.assertNotIn("limite de tamanho do Gmail", html)

    def test_acima_do_orcamento_as_figuras_saem_na_ordem(self):
        from unittest import mock

        from shelf_monitor.report import digest

        # Na edicao mensal, que tem a rentabilidade e as figuras dela.
        report = _relatorio()
        inteiro = digest.render_html(report, None, "mensal")
        figuras = inteiro.count(MARCA_DA_FIGURA)
        with mock.patch.object(digest, "_ORCAMENTO_DO_EMAIL", len(inteiro.encode()) - 1):
            cortado = digest.render_html(report, None, "mensal")
        # A primeira a sair e a grade de pontos do PL abaixo da mediana.
        self.assertEqual(cortado.count(MARCA_DA_FIGURA), figuras - 1)
        self.assertNotIn("1 quadrado = 1%", cortado)
        self.assertIn("1 gráfico ficou de fora (PL abaixo da mediana)", cortado)
        # A tabela que a figura desenhava continua.
        self.assertIn("Fundos abaixo", cortado)

        with mock.patch.object(digest, "_ORCAMENTO_DO_EMAIL", 1):
            sem_nenhuma = digest.render_html(report, None, "mensal")
        self.assertEqual(sem_nenhuma.count(MARCA_DA_FIGURA), 0)
        self.assertIn("gráficos ficaram de fora", sem_nenhuma)


class TestMenosTexto(unittest.TestCase):
    """Secao 5 da linguagem visual: no e-mail, lista de fundos vira tabela e a
    frase que narra o grafico sai. O markdown (parte em texto) nao muda."""

    def test_nenhuma_lista_em_frase_no_e_mail(self):
        from shelf_monitor.report.digest import _html_ritmo, render_html
        from shelf_monitor.report.digest_visao import montar_visao
        from test_captacao import _relatorio as relatorio_de_captacao

        captacao = _html_ritmo(montar_visao(relatorio_de_captacao()))
        for html in (render_html(_relatorio()), captacao):
            self.assertNotIn("<li", html)
            self.assertNotIn("<ul", html)

    def test_listas_de_captacao_em_tabela_com_a_semana_e_os_3_meses(self):
        from shelf_monitor.report.digest import _html_ritmo, _mm
        from shelf_monitor.report.digest_visao import montar_visao
        from test_captacao import _relatorio as relatorio_de_captacao

        visao = montar_visao(relatorio_de_captacao())
        html = _html_ritmo(visao)
        texto = _texto(html)
        # Cada lista vira tabela: Fundo | Semana | 3 meses | Cotistas | Casas,
        # com o nome curto da casa.
        for coluna in ("Semana", "3 meses", "Cotistas", "Casas"):
            self.assertIn(f">{coluna}</th>", html)
        fundo = visao.report.retail_flow["top_captacao"][0]
        self.assertIn(fundo["nome"], texto)
        self.assertIn(_mm(fundo["captacao_liquida"]), texto)
        self.assertIn("XP, BTG", texto)

    def test_rentabilidade_em_colunas_de_numero(self):
        from shelf_monitor.report.digest import _html_performance_section

        html = _html_performance_section(_relatorio())
        texto = _texto(html)
        # "124 de 203 (61%) carregam 74% do PL - indice 1,21" virou quatro colunas.
        self.assertNotIn("carregam", texto)
        for coluna in ("Fundos abaixo", "% fundos", "% PL", "Índice"):
            self.assertIn(f">{coluna}</th>", html)
        casa = _BLOCOS["performance"]["casas"][0]["alocacao_abaixo_da_mediana"]
        self.assertIn(f"{casa['fundos']} de {casa['de']}", texto)
        # A introducao longa sai; a ressalva vira nota do rodape da figura.
        self.assertNotIn("Prazos:", texto)
        self.assertIn("não é o retorno dos clientes da casa", texto)

    def test_mudancas_de_vitrine_numa_tabela_por_casa(self):
        from datetime import timedelta

        from shelf_monitor.report.digest import render_html

        antes = _vitrine("btg", ["11111111000111", "22222222000122"], _AGORA - timedelta(days=1))
        depois = _vitrine("btg", ["11111111000111", "33333333000133"])
        report = RunReport(
            generated_at=_AGORA, snapshots=[depois],
            shelf_diffs=[diff_shelf(depois, antes)],
        )
        html = render_html(report)
        texto = _texto(html)
        self.assertIn(">Movimento</th>", html)
        # A casa e cabecalho de grupo; cada movimento, uma linha com o CNPJ.
        self.assertIn('<td colspan="', html)
        self.assertIn("Entrou BTG Fundo 1 FIC FIM 33.333.333/0001-33", texto)
        self.assertIn("Saiu BTG Fundo 1 FIC FIM 22.222.222/0001-22", texto)
        self.assertNotIn("1 entrada, 1 saída", texto)


class TestPadraoXP(unittest.TestCase):
    """Padrao executivo da XP (estilo.py): branco, preto e amarelo. O amarelo da
    XP fica a 1,6:1 do branco, entao toda marca amarela leva o fio preto."""

    def test_marca_da_xp_tem_contorno_e_a_mesma_espessura(self):
        from shelf_monitor.report.shared.estilo import CONTORNO_XP, contorno_da_casa

        html = barras([
            Linha("XP", 10, "10", cor_da_casa("xp"), True, contorno_da_casa("xp")),
            Linha("BTG", 5, "5", cor_da_casa("btg"), contorno=contorno_da_casa("btg")),
        ])
        # O fio entra dentro da altura: 10 + 2 = 12, a espessura das outras.
        self.assertIn(
            f'<td height="10" bgcolor="{cor_da_casa("xp")}" '
            f'style="border:1px solid {CONTORNO_XP};">', html
        )
        self.assertIn(f'<td height="12" bgcolor="{cor_da_casa("btg")}"></td>', html)
        self.assertEqual(html.count("border:1px solid"), 1)

    def test_grade_da_xp_com_contorno_so_nos_quadrados_pintados(self):
        from shelf_monitor.report.shared.estilo import CONTORNO_XP

        html = grade_de_pontos(0.23, cor_da_casa("xp"), contorno=CONTORNO_XP)
        self.assertEqual(html.count(f"border:1px solid {CONTORNO_XP}"), 23)
        self.assertEqual(TestGradeDePontos._pintados(html, cor_da_casa("xp")), 23)
        self.assertEqual(html.count("<td"), 100)

    def test_etiqueta_e_o_traco_amarelo(self):
        self.assertIn('<td width="24" height="3"', MARCA_DA_FIGURA)
        self.assertIn(f'bgcolor="{ETIQUETA}"', MARCA_DA_FIGURA)
        html = figura("t", "s", "c")
        self.assertIn(MARCA_DA_FIGURA, html)
        # O fio preto de 3 px no topo da figura e o titulo em peso 800.
        self.assertIn("border-top:3px solid", html)
        self.assertIn("font-weight:800", html)

    def test_numero_chave_marcado(self):
        from shelf_monitor.report.shared.graficos_email import MARCA_XP, numeros_em_destaque

        html = numeros_em_destaque([("52", "transferíveis", True), ("13", "abrir gestora")])
        self.assertEqual(html.count(MARCA_XP), 1)
        self.assertIn(f'<span style="{MARCA_XP}">52</span>', html)

    def test_digest_no_padrao_executivo(self):
        from shelf_monitor.report.digest import render_html
        from shelf_monitor.report.shared.estilo import (
            CONTORNO_XP,
            DESTAQUE_SUAVE,
            TINTA,
        )

        html = render_html(_relatorio(), None, "mensal")
        # A faixa preta do topo com o rotulo do monitor.
        self.assertIn(f'<tr><td bgcolor="{TINTA}"', html)
        self.assertIn(">Monitor de fundos</td>", html)
        # A linha da XP no amarelo claro, com o traco amarelo.
        self.assertIn(f'bgcolor="{DESTAQUE_SUAVE}"', html)
        self.assertIn(f"border-left:4px solid {ETIQUETA}", html)
        # A marca amarela da XP sempre com o fio preto; texto nunca em amarelo.
        self.assertIn(f"border:1px solid {CONTORNO_XP}", html)
        self.assertNotIn(f"color:{cor_da_casa('xp')}", html.replace("background:", "fundo:"))
        # Os titulos de secao testados continuam la, como retranca.
        for titulo in ("Oportunidade de grade e gaps", "Ritmo de captação",
                       "Vitrines: entradas e saídas", "Rentabilidade e custo · edição mensal"):
            self.assertIn(f"{titulo}</h2>", html)

    def test_chamada_amarela_no_bloco_que_nao_se_aplica(self):
        from shelf_monitor.report.digest import render_html
        from shelf_monitor.report.shared.estilo import SUPERFICIE_2

        html = render_html(_relatorio(dict(ConfigPrevidencia.blocos_suprimidos)))
        self.assertIn(
            f'border-left:4px solid {ETIQUETA};background:{SUPERFICIE_2};', html
        )
        self.assertIn("Não se aplica a este monitor", html)


if __name__ == "__main__":
    unittest.main()
