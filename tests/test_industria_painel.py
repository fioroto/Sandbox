"""Painel interativo da industria (Fase 1D): a pagina, o payload e o cubo.

Trava:

- o molde: tema claro, visao executiva acima dos paineis recolhidos,
  exatamente duas tags de script e nenhuma com `src`, JSON valido com
  `</script>` escapado e todo alvo do JS presente na pagina;
- a neutralidade: sem marca de casa na barra, sem chave `foco`, e o JS nao
  usa as cores de casa do dashboard das vitrines;
- o prazo: toda janela com rotulo, datas dd/mm/aaaa e base;
- o cubo dos filtros: somado, devolve os numeros do bloco (PL, captacao por
  janela, contas, conta e ordem, PF/PJ) - o recorte no navegador parte dos
  mesmos numeros da pagina sem filtro;
- o tamanho: com 10 mil produtos no explorador, a pagina fica abaixo de 2,5 MB.

A base sintetica e a de test_industria_bloco, com o dinheiro multiplicado por
um milhao: o cubo guarda milhares de reais, e PL de R$ 1.000 sumiria no
arredondamento.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import dataclasses
import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from shelf_monitor.industria.bloco import TOPICOS_DA_LEITURA, montar_bloco
from shelf_monitor.industria.cli import publicar
from shelf_monitor.report.dashboard import _GRAFICOS, _LOGO
from shelf_monitor.report.industria_painel import (
    _SCRIPT_INDUSTRIA,
    ARQUIVO,
    contas,
    dados_do_painel,
    nome_curto,
    render_painel_industria,
    ticket,
)
from shelf_monitor.report.rentabilidade import SerieMensal

from fixtures_comuns import temp_config
from test_industria_bloco import _base as _base_sintetica

ESCALA = 1e6
AGORA = datetime(2026, 9, 28, 10, 0)
LIMITE_DA_PAGINA = 2_500_000


def _base():
    base = _base_sintetica()
    dados = {
        chave: {
            mes: dataclasses.replace(m, pl_fim=m.pl_fim * ESCALA, captacao=m.captacao * ESCALA,
                                     resgates=m.resgates * ESCALA)
            for mes, m in meses.items()
        }
        for chave, meses in base.serie._dados.items()
    }
    base.serie = SerieMensal(list(base.serie.meses), dados)
    return base


def _json_da_pagina(pagina: str) -> dict:
    inicio = pagina.index('id="industria-data">') + len('id="industria-data">')
    return json.loads(pagina[inicio:pagina.index("</script>", inicio)])


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, cls._tmp = temp_config()
        cls.bloco = montar_bloco(_base(), cls.config)
        cls.caminho = render_painel_industria(cls.config, cls.bloco, AGORA)
        cls.pagina = cls.caminho.read_text(encoding="utf-8")
        cls.dados = _json_da_pagina(cls.pagina)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestMolde(_Base):
    def test_arquivo_titulo_e_tema_claro(self):
        self.assertEqual(self.caminho, self.config.reports_dir / ARQUIVO)
        self.assertIn("<title>Monitor da indústria de fundos</title>", self.pagina)
        self.assertIn('<meta name="color-scheme" content="light">', self.pagina)
        self.assertNotIn("prefers-color-scheme: dark", self.pagina)
        self.assertIn('<html lang="pt-BR">', self.pagina)

    def test_visao_executiva_acima_dos_paineis_recolhidos(self):
        paineis = re.findall(r'<section class="panel" id="([^"]+)">\s*<details( open)?>', self.pagina)
        self.assertGreaterEqual(len(paineis), 9)
        self.assertEqual([p for p, aberto in paineis if aberto], [], "painel aberto por padrão")
        self.assertLess(self.pagina.index('id="visao-executiva"'), self.pagina.index('class="panel"'))
        self.assertLess(self.pagina.index('id="filtros"'), self.pagina.index('id="visao-executiva"'))
        for grupo in ("O mercado", "Competição e canal", "Explorar e conferir"):
            self.assertIn(f'<p class="grupo">{grupo}</p>', self.pagina)

    def test_duas_tags_de_script_e_nenhuma_com_src(self):
        self.assertEqual(self.pagina.count("<script"), 2)
        self.assertEqual(re.findall(r"<script[^>]*\bsrc=", self.pagina), [])
        self.assertIn('<script type="application/json" id="industria-data">', self.pagina)

    def test_todo_alvo_do_js_existe_na_pagina(self):
        alvos = set(re.findall(r'getElementById\("([^"]+)"\)', _SCRIPT_INDUSTRIA + _GRAFICOS))
        self.assertIn("kpis", alvos)
        faltando = sorted(a for a in alvos if f'id="{a}"' not in self.pagina)
        self.assertEqual(faltando, [])
        for campo in ("publico", "n1", "papel", "tipo_gestora"):
            self.assertIn(f'data-campo="{campo}"', self.pagina)

    def test_links_internos_apontam_para_paineis_que_existem(self):
        # So o HTML: o JS embutido tem `href="#${alvo}"` nos seus moldes.
        html = self.pagina[:self.pagina.index('<script type="application/json"')]
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        destinos = set(re.findall(r'href="#([^"]+)"', html))
        self.assertIn("fontes-section", destinos)
        self.assertEqual(sorted(destinos - ids), [])

    def test_script_da_industria_mora_numa_funcao(self):
        """Um `const` no topo que repita um global de graficos.js derruba o script inteiro."""
        codigo = re.sub(r"/\*.*?\*/", "", _SCRIPT_INDUSTRIA, flags=re.S).strip()
        self.assertTrue(codigo.startswith("(function painelDaIndustria() {"), codigo[:60])
        self.assertTrue(codigo.endswith("})();"))


class TestNeutralidade(_Base):
    def test_sem_marca_de_casa_nem_foco(self):
        self.assertNotIn(_LOGO, self.pagina)
        self.assertNotIn('class="marca"', self.pagina)
        self.assertNotIn("foco", self.bloco)
        self.assertNotIn("foco", self.dados)
        for global_das_vitrines in ("platformColor", "corDaCasa", "BASE_ID", "legendaDeCasas", "colunasPorCasa"):
            self.assertNotIn(global_das_vitrines, _SCRIPT_INDUSTRIA)


class TestPrazo(_Base):
    def test_toda_janela_com_datas_e_base(self):
        data = r"\d{2}/\d{2}/\d{4}"
        for rotulo in ("1 mês", "3 meses", "no ano", "12 meses"):
            self.assertRegex(self.pagina, rf"{rotulo}: de {data} a {data}; base no fechamento de {data}")
        for janela in self.dados["meta"]["janelas"].values():
            self.assertRegex(janela["texto"], rf"^{janela['rotulo']}, de {data} a {data}; base no fechamento de {data}$")

    def test_destaques_trazem_o_prazo(self):
        executiva = self.pagina[self.pagina.index('id="visao-executiva"'):self.pagina.index('class="detalhes-head"')]
        self.assertEqual(executiva.count('class="kpi"'), 5)
        self.assertEqual(executiva.count('class="kpi-prazo"'), 5)
        self.assertIn("12 meses, de ", executiva)
        self.assertIn("fechamento de 28/08/2026", executiva)

    def test_leitura_com_topico_e_prazo_por_frase(self):
        self.assertEqual(len(self.bloco["leitura"]), len(self.bloco["leitura_topicos"]))
        self.assertTrue(set(self.bloco["leitura_topicos"]) <= set(TOPICOS_DA_LEITURA))
        leituras = self.pagina[self.pagina.index('<table class="leituras">'):]
        leituras = leituras[:leituras.index("</table>")]
        self.assertEqual(leituras.count('<small class="prazo">'), len(self.bloco["leitura"]))
        self.assertNotIn("**", leituras)
        self.assertIn("<strong>", leituras)


class TestCubo(_Base):
    def _somas(self) -> dict:
        cubo = self.dados["cubo"]
        n = len(cubo["dimensoes"])
        somas = {}
        for i, medida in enumerate(cubo["medidas"]):
            fator = 1e3 if medida in cubo["em_mil"] else 1
            somas[medida] = sum(linha[n + i] for linha in cubo["linhas"]) * fator
        return somas

    def assertPerto(self, a, b, msg=None):
        # Uma celula arredonda para o milhar: erro de ate R$ 500 por celula.
        self.assertLessEqual(abs(a - b), 500 * len(self.dados["cubo"]["linhas"]) + 1, msg)

    def test_somado_devolve_os_numeros_do_bloco(self):
        somas = self._somas()
        universo = self.bloco["mercado"]["janelas"]
        self.assertPerto(somas["pl"], self.bloco["camadas"]["pl_investidor"])
        for janela in ("1m", "3m", "ano", "12m"):
            self.assertPerto(somas[f"fluxo_{janela}"], universo[janela]["fluxo_investidor"], janela)
            self.assertPerto(somas[f"pl_abertura_{janela}"], universo[janela]["pl_investidor_abertura"], janela)
        self.assertEqual(somas["cotistas"], self.bloco["camadas"]["cotistas"])
        self.assertEqual(somas["produtos"], self.bloco["camadas"]["produtos"])
        self.assertPerto(somas["pl_pf_pj"], self.bloco["dinheiro_pf_pj"])

    def test_conta_e_ordem_do_cubo_e_a_do_bloco(self):
        somas = self._somas()
        total = self.bloco["plataformizacao"]["total"]
        self.assertAlmostEqual(somas["pl_conta_e_ordem"] / somas["pl_perfil"], total["conta_e_ordem"], places=3)
        antes = somas["pl_conta_e_ordem_12m_antes"] / somas["pl_perfil_12m_antes"]
        self.assertAlmostEqual((somas["pl_conta_e_ordem"] / somas["pl_perfil"] - antes) * 100,
                               total["variacao_pp"], places=1)

    def test_dimensoes_vem_do_dicionario(self):
        cubo, dic = self.dados["cubo"], self.dados["dic"]
        n1s = {dic["n1"][linha[0]] for linha in cubo["linhas"]}
        self.assertEqual(n1s, {i["n1"] for i in self.bloco["estrategias"]["n1"]})
        publicos = {dic["publico"][linha[2]] for linha in cubo["linhas"]}
        self.assertEqual(publicos, {"geral"})


class TestExplorador(_Base):
    def test_uma_linha_por_produto_do_universo_em_colunas(self):
        explorador, dic = self.dados["explorador"], self.dados["dic"]
        self.assertEqual(len(explorador["linhas"]), len(self.bloco["_explorador"]))
        colunas = explorador["colunas"]
        for linha, original in zip(explorador["linhas"], self.bloco["_explorador"]):
            self.assertEqual(len(linha), len(colunas))
            registro = dict(zip(colunas, linha))
            self.assertEqual(dic["n2"][registro["n2"]], original["n2"])
            self.assertEqual(dic["papel"][registro["papel"]], original["papel"])
            self.assertAlmostEqual(registro["pl_investidor"] * 1e3, original["pl_investidor"], delta=500)
            self.assertEqual(registro["cnpj"], original["cnpj"])

    def test_nome_com_fecha_script_sai_escapado(self):
        bloco = dict(self.bloco)
        bloco["_explorador"] = [dict(l) for l in self.bloco["_explorador"]]
        bloco["_explorador"][0]["nome"] = "FUNDO </script><script>alert(1)</script> RENDA FIXA"
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        pagina = render_painel_industria(config, bloco, AGORA).read_text(encoding="utf-8")
        self.assertEqual(pagina.count("<script"), 2)
        self.assertEqual(pagina.count("</script>"), 2)
        dados = _json_da_pagina(pagina)
        nomes = [l[dados["explorador"]["colunas"].index("nome")] for l in dados["explorador"]["linhas"]]
        self.assertIn("FUNDO </script><script>alert(1)</script> RF", nomes)

    def test_dez_mil_produtos_cabem_no_limite(self):
        modelo = self.bloco["_explorador"][0]
        linhas = []
        for i in range(10_000):
            linhas.append({
                **modelo,
                "cnpj": f"{i:02d}.{i % 1000:03d}.{i % 997:03d}/0001-{i % 100:02d}",
                "subclasse": "" if i % 3 else f"SUB{i:06d}",
                "nome": f"GESTORA {i % 800} MACRO FUNDO DE INVESTIMENTO EM COTAS DE FUNDOS DE INVESTIMENTO "
                        f"MULTIMERCADO CRÉDITO PRIVADO RESPONSABILIDADE LIMITADA {i}",
                "raiz_nome": "" if i % 2 else f"GESTORA {i % 800} MASTER FUNDO DE INVESTIMENTO MULTIMERCADO {i}",
                "gestora": f"Grupo {i % 800}",
                "n2": f"Peer group {i % 45}",
                "casa_dedicada": "" if i % 5 else f"Casa {i % 17}",
                "taxa_adm": 1.2345678, "pl_investidor": 123_456_789.123 + i, "cotistas": 12_345 + i,
                "fluxo_12m": -9_876_543.21 - i, "sobre_pl_12m": 0.123456789, "retorno_12m": 0.134567891,
                "quartil_12m": 1 + i % 4, "conta_e_ordem": 0.456789123,
            })
        bloco = dict(self.bloco)
        bloco["_explorador"] = linhas
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        tamanho = render_painel_industria(config, bloco, AGORA).stat().st_size
        self.assertLess(tamanho, LIMITE_DA_PAGINA, f"{tamanho:,} bytes")


class TestTextos(unittest.TestCase):
    def test_nome_curto(self):
        self.assertEqual(
            nome_curto("XP MACRO FUNDO DE INVESTIMENTO EM COTAS DE FUNDOS DE INVESTIMENTO MULTIMERCADO "
                       "RESPONSABILIDADE LIMITADA"),
            "XP MACRO FIC FI MM",
        )
        self.assertEqual(nome_curto("ITAÚ PRIVILÈGE RENDA FIXA REFERENCIADO DI FIF - RESP LIMITADA"),
                         "ITAÚ PRIVILÈGE RF REF DI FIF")
        self.assertEqual(nome_curto("SPARTA TOP CRÉDITO PRIVADO FUNDO DE INVESTIMENTO FINANCEIRO"), "SPARTA TOP CP FIF")
        self.assertEqual(nome_curto(None), "")
        # Palavra que so contem o trecho nao e cortada.
        self.assertEqual(nome_curto("RENDA FIXAMENTE"), "RENDA FIXAMENTE")

    def test_sufixo_juridico_como_o_cadastro_escreve(self):
        """Espaco duplo, abreviado, com erro de digitacao, colado ou cortado em 100 caracteres."""
        casos = {
            "JEFER FUNDO DE INVESTIMENTO MULTIMERCADO CRÉDITO PRIVADO RESPONSABILIDADE  LIMITADA": "JEFER FI MM CP",
            "BB RF TÍTULOS PÚBLICOS VÉRTICE 2026 FIF RESP LIM": "BB RF TÍTULOS PÚBLICOS VÉRTICE 2026 FIF",
            "XPTO FIC FIM CP RL": "XPTO FIC FIM CP",
            "ABC FIF MM - RESPONSABILIDADE LI": "ABC FIF MM",
            "ABC FIF MM RESPONSABILI": "ABC FIF MM",
            "CSHG VERDE AM BETA 14 FIC FIF - AÇÕES REP LIMITADA": "CSHG VERDE AM BETA 14 FIC FIF - AÇÕES",
            "ABSOLUTE PACE FIF AÇÕESRESPONSABILIDADE LIMITADA": "ABSOLUTE PACE FIF AÇÕES",
            "BANRISUL FLEX CP FI RF DE LP RESPONS LIMITADA": "BANRISUL FLEX CP FI RF DE LP",
            "GARDE D'ARTAGNAN FIC FIM RESP. LTDA.": "GARDE D'ARTAGNAN FIC FIM",
            "SCORPIO FIF MULTIMERCADO CRÉDITO PRIVADO INVESTIMENTO NO EXTERIOR": "SCORPIO FIF MM CP IE",
        }
        for nome, esperado in casos.items():
            self.assertEqual(nome_curto(nome), esperado, nome)
        # O que parece sufixo e nao e fica.
        for nome in ("XYZ AÇÕES INVESTIMENTO RESPONSÁVEL", "XYZ AÇÕES INVESTIMENTO RESPONSAVEL",
                     "RESPIRO MASTER FIF MM", "FUNDO RL CAPITAL FIM", "ITAÚ LIMITED EDITION FIC FIM"):
            self.assertEqual(nome_curto(nome), nome)

    def test_contas_e_ticket(self):
        self.assertEqual(contas(23_456_789), "23,5 mi")
        self.assertEqual(contas(345_400), "345 mil")
        self.assertEqual(contas(8_700), "8.700")
        self.assertEqual(contas(870), "870")
        self.assertEqual(contas(0), "-")
        self.assertEqual(ticket(170_400.0), "R$ 170 mil")
        self.assertEqual(ticket(1_234_567.0), "R$ 1,2 mi")
        self.assertEqual(ticket(None), "-")


class TestPublicar(unittest.TestCase):
    def test_publicar_grava_o_painel_junto(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        bloco = montar_bloco(_base(), config)
        with mock.patch("shelf_monitor.industria.cli.console"):
            gravados = publicar(config, bloco)
        self.assertEqual(set(gravados), {"historico", "planilha", "painel"})
        self.assertEqual(gravados["painel"], Path(config.reports_dir) / ARQUIVO)
        self.assertTrue(gravados["painel"].is_file())


class TestDadosDoPainel(unittest.TestCase):
    def test_payload_serializa_sem_as_chaves_privadas(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        bloco = montar_bloco(_base(), config)
        dados = dados_do_painel(bloco, AGORA)
        self.assertFalse([k for k in dados if k.startswith("_")])
        json.dumps(dados, ensure_ascii=False)
        self.assertEqual(dados["meta"]["gerado_em"], "28/09/2026 10:00")


if __name__ == "__main__":
    unittest.main()
