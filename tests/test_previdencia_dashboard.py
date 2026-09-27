"""Dashboard da previdencia: o molde do de fundos, com o conteudo da previdencia.

Renderizado pelo dashboard de fundos, o da previdencia saia com duas
manchetes, tres paineis de "nao se aplica" (gaps e exclusivos por CNPJ, e um
radar CVM que a previdencia nem coleta) e o grafico de arquitetura aberta
publicando o corte produto proprio x terceiros que o modulo suprime. Estes
testes travam a pagina propria (`shelf_monitor.previdencia.dashboard`):

- o molde: tema claro, visao executiva estatica acima dos paineis, todos
  recolhidos, e todo alvo do JS presente na pagina - ou guardado por ele;
- o conteudo: manchetes e placar com a seguradora, Nu e Inter como casas sem
  vitrine publica, e cada bloco suprimido fora, com o motivo num painel so;
- o que ela reaproveita de fundos nao muda o dashboard de fundos.

Os blocos de captacao e rentabilidade vem de `fixtures/previdencia/
blocos_2026-09-23.json`, os mesmos de test_supressao.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import html
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from shelf_monitor.diff import diff_shelf
from shelf_monitor.models import (
    CollectorStrategy,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.previdencia.config import (
    MOTIVO_CNPJ,
    MOTIVO_COTISTAS,
    MOTIVO_PRODUTO_PROPRIO,
    MOTIVO_RADAR,
    ROTULOS_PROPRIOS,
    ConfigPrevidencia,
)
from shelf_monitor.previdencia.dashboard import _decimal, render_dashboard_previdencia
from shelf_monitor.previdencia.eixo import KEY_SEGURADORA
from shelf_monitor.report.dashboard import _SCRIPT
from shelf_monitor.report.shared import rotulos_sobrepostos

_BLOCOS = json.loads(
    (Path(__file__).parent / "fixtures" / "previdencia" / "blocos_2026-09-23.json")
    .read_text(encoding="utf-8")
)
AGORA = datetime(2026, 9, 23, 22, 0)
PREVIDENCIA = dict(ConfigPrevidencia.blocos_suprimidos)

# O que a pagina da previdencia nao publica (os mesmos de test_supressao).
_COTISTAS = ("Ponderada por cotistas", "PL menos cotistas", "muita gente", " cotistas -")
_CNPJ = ("Só nesta vitrine", "só ela exibe", "inclusão operacional", "faltam na XP", "só nela")

# Alvos do JS que a pagina da previdencia nao tem, e que o JS guarda: o
# painel de gaps, o saldo por corte, a arquitetura aberta, os exclusivos, os
# fundos de muita gente e o radar. Alvo novo no JS sem entrar na pagina nem
# aqui faz o teste falhar - e o que evita um null que derruba o resto do script.
_ALVOS_OPCIONAIS = {
    "gaps-section", "gaps-tiles", "matrix-gaps", "table-gaps", "gaps-coverage",
    "gaps-funil", "table-gaps-cobertos",
    "flow-composicao", "chart-arch", "legend-arch", "chart-exclusive",
    "perf-expostos", "cvm-section", "legend-cvm", "chart-cvm", "table-cvm",
    "table-pre-op",
}


def _vitrine(
    platform: str,
    seguradoras: list[str | None],
    primeiro_cnpj: int,
    *,
    quando: datetime = AGORA,
) -> ShelfSnapshot:
    fundos = []
    for i, seguradora in enumerate(seguradoras):
        fundo = FundOffer(
            platform=platform,
            name=f"{platform.upper()} Prev {i} FIC FIM",
            cnpj=f"{primeiro_cnpj + i:08d}000191",
        )
        fundo.sources = [CollectorStrategy.API_JSON.value]
        if seguradora:
            fundo.extra[KEY_SEGURADORA] = seguradora
        fundos.append(fundo)
    return ShelfSnapshot(
        platform=platform, collected_at=quando, strategy=CollectorStrategy.API_JSON,
        status=SnapshotStatus.OK, funds=fundos,
    )


def _relatorio(*extras: ShelfSnapshot) -> RunReport:
    xp = _vitrine("xp", ["XP Seguros"] * 3, 11110000)
    btg = _vitrine("btg", ["KIPREV", "KIPREV", "ICATU", "KIPREV"], 22220000)
    anterior = _vitrine("btg", ["KIPREV"] * 3, 22220000, quando=AGORA - timedelta(days=1))
    snapshots = [xp, btg, *extras]
    report = RunReport(
        generated_at=AGORA, snapshots=snapshots,
        shelf_diffs=[diff_shelf(xp, None), diff_shelf(btg, anterior)]
        + [diff_shelf(s, None) for s in extras],
        suprimidos=dict(PREVIDENCIA),
    )
    report.performance = json.loads(json.dumps(_BLOCOS["performance"]))
    report.retail_flow = json.loads(json.dumps(_BLOCOS["retail_flow"]))
    report.grade_opportunities = json.loads(json.dumps(_BLOCOS["grade_opportunities"]))
    return report


def _texto(trecho: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", trecho)))


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = ConfigPrevidencia(data_dir=self._tmp.name)
        self.config.ensure_dirs()

    def _pagina(self, report: RunReport | None = None) -> str:
        with rotulos_sobrepostos(ROTULOS_PROPRIOS):
            caminho = render_dashboard_previdencia(self.config, report or _relatorio())
        self.assertEqual(caminho, self.config.reports_dir / "dashboard.html")
        return caminho.read_text(encoding="utf-8")

    @staticmethod
    def _executiva(pagina: str) -> str:
        return pagina[pagina.index('id="visao-executiva"'):pagina.index('class="detalhes-head"')]


class TestMolde(_Base):
    def test_tema_claro_e_visao_executiva_acima_dos_paineis_recolhidos(self):
        pagina = self._pagina()
        self.assertIn("<title>Monitor de Previdência</title>", pagina)
        self.assertIn('<meta name="color-scheme" content="light">', pagina)
        self.assertNotIn("prefers-color-scheme: dark", pagina)

        paineis = re.findall(r'<section class="panel" id="([^"]+)">\s*<details( open)?>', pagina)
        self.assertGreaterEqual(len(paineis), 9)
        self.assertEqual([p for p, aberto in paineis if aberto], [], "painel aberto por padrão")
        self.assertLess(pagina.index('id="visao-executiva"'), pagina.index('class="panel"'))
        for grupo in ("Captação e desempenho", "Composição das vitrines", "Qualidade dos dados"):
            self.assertIn(f'<p class="grupo">{grupo}</p>', pagina)

    def test_todo_alvo_do_js_existe_ou_e_opcional(self):
        pagina = self._pagina()
        alvos = set(re.findall(r'getElementById\("([^"]+)"\)', _SCRIPT))
        faltando = sorted(a for a in alvos - _ALVOS_OPCIONAIS if f'id="{a}"' not in pagina)
        self.assertEqual(faltando, [])
        # E os opcionais de fato nao estao: se voltarem, saem da lista acima.
        self.assertEqual(sorted(a for a in _ALVOS_OPCIONAIS if f'id="{a}"' in pagina), [])

    def test_os_opcionais_sao_guardados_no_js(self):
        """Cada painel ausente tem a sua guarda - sem ela, o null derruba o script."""
        for guarda in (
            'if (!section) return;',
            'if (composicaoEl)',
            'if (archMount && archLegend',
            'if (!document.getElementById("cvm-section"))',
            'if (exclusiveEl)',
            'if (expostosEl)',
        ):
            self.assertIn(guarda, _SCRIPT)

    def test_dados_embutidos_sao_json_valido(self):
        pagina = self._pagina()
        inicio = pagina.index('id="dashboard-data">') + len('id="dashboard-data">')
        data = json.loads(pagina[inicio:pagina.index("</script>", inicio)])
        self.assertEqual(set(data["suprimidos"]), set(PREVIDENCIA))
        self.assertEqual(pagina.count("<script"), 2)


class TestVisaoExecutiva(_Base):
    def test_manchetes_da_previdencia(self):
        executiva = _texto(self._executiva(self._pagina()))
        for proibido in _COTISTAS + _CNPJ:
            self.assertNotIn(proibido, executiva)
        self.assertIn("Fundos na vitrine", executiva)
        self.assertIn("em planos de 1 seguradora: XP Seguros", executiva)
        self.assertIn("Maior: BTG Pactual, 4", executiva)
        self.assertIn("Captação líquida em 3 meses", executiva)
        self.assertIn("Rentabilidade em 3 meses", executiva)
        # Onde esta o dinheiro, com o prazo e a casa que tem o menor indice.
        self.assertIn("PL abaixo da categoria em 3 meses", executiva)
        self.assertIn("74% do PL da XP Investimentos, em 61% dos fundos: índice 1,21", executiva)
        self.assertIn("a seguir, BTG Pactual: índice 1,27", executiva)
        self.assertIn("de 01/07/2026 a 22/09/2026", executiva)

    def test_leituras_dizem_a_seguradora_e_desde_quando(self):
        executiva = _texto(self._executiva(self._pagina()))
        self.assertIn(
            "XP Investimentos 3 fundos, em planos da XP Seguros; BTG Pactual 4 fundos, "
            "em planos de 2 seguradoras (KIPREV 3, ICATU 1)",
            executiva,
        )
        self.assertIn("Nu (Nubank) e Banco Inter não têm vitrine pública de previdência", executiva)
        self.assertIn(f"coleta de {(AGORA - timedelta(days=1)):%d/%m/%Y}", executiva)

    def test_placar_tem_a_seguradora_e_as_casas_sem_vitrine(self):
        placar = self._executiva(self._pagina())
        self.assertIn("fundos e seguradora", placar)
        self.assertIn('<tr class="base"><th scope="row">XP Investimentos</th>', placar)
        self.assertIn("KIPREV e ICATU", placar)
        # Nu e Inter nao tem snapshot util no `report` - e nao somem do placar.
        for casa in ("Nu (Nubank)", "Banco Inter"):
            linha = re.search(rf'<th scope="row">{re.escape(casa)}</th>(.*?)</tr>', placar)
            self.assertIsNotNone(linha, casa)
            self.assertIn("sem vitrine pública", linha.group(1))

    def test_casa_sem_vitrine_nao_vira_falha_de_coleta(self):
        """No `run` o snapshot do Nu vem UNAVAILABLE: continua "sem vitrine publica"."""
        nu = ShelfSnapshot(
            platform="nu", collected_at=AGORA, strategy=CollectorStrategy.HTML_SCRAPE,
            status=SnapshotStatus.UNAVAILABLE, funds=[],
            error="Nenhuma vitrine pública de previdência encontrada (2026-09-22).",
        )
        placar = self._executiva(self._pagina(_relatorio(nu)))
        self.assertNotIn("coleta indisponível", placar)
        self.assertEqual(placar.count('<th scope="row">Nu (Nubank)</th>'), 1)

    def test_casa_sem_seguradora_publicada(self):
        itau = _vitrine("itau", [None, None], 33330000)
        pagina = self._pagina(_relatorio(itau))
        self.assertIn("seguradora não publicada", self._executiva(pagina))
        self.assertIn("Itaú Private 2 fundos, sem seguradora publicada", _texto(pagina))


class TestPaineis(_Base):
    def test_o_que_nao_se_aplica_sai_e_o_motivo_fica_num_painel(self):
        pagina = self._pagina()
        for ident in ("gaps-section", "exclusivos-section", "cvm-section", "chart-arch"):
            self.assertNotIn(f'id="{ident}"', pagina)
        # O HTML, sem o JS embutido - que traz a frase para a pagina de fundos.
        self.assertNotIn("Não se aplica", pagina[:pagina.index('<script type="application/json"')])

        limites = pagina[pagina.index('id="limites-section"'):pagina.index('id="fontes-section"')]
        for motivo in (MOTIVO_CNPJ, MOTIVO_COTISTAS, MOTIVO_PRODUTO_PROPRIO, MOTIVO_RADAR):
            self.assertIn(html.escape(motivo), limites)
        # A pagina inteira segue sem publicar o que o modulo suprime.
        texto = _texto(pagina[:pagina.index('id="limites-section"')])
        for proibido in _COTISTAS + _CNPJ:
            self.assertNotIn(proibido, texto)

    def test_seguradora_no_lugar_da_arquitetura_aberta(self):
        pagina = self._pagina()
        vitrine = _texto(pagina[pagina.index('id="vitrine-section"'):pagina.index('id="classes-section"')])
        self.assertIn("Seguradora dos planos", vitrine)
        self.assertNotIn("Arquitetura aberta", vitrine)
        self.assertIn("XP Investimentos XP Seguros 3 100%", vitrine)
        self.assertIn("BTG Pactual KIPREV 3 75% ICATU 1 25%", vitrine)

    def test_gestoras_levam_a_ressalva_da_gestora_registrada(self):
        pagina = self._pagina()
        gestoras = pagina[pagina.index('id="gestoras-section"'):pagina.index('id="ticket-section"')]
        self.assertIn("Ressalva da previdência", gestoras)
        self.assertIn(html.escape(MOTIVO_PRODUTO_PROPRIO), gestoras)

    def test_saldo_sem_os_dois_cortes_sai_do_painel_de_captacao(self):
        pagina = self._pagina()
        html_sem_js = pagina[:pagina.index('<script type="application/json"')]
        self.assertIn('id="flow-section"', html_sem_js)
        self.assertNotIn("De onde vem o saldo", html_sem_js)

    def test_resumo_da_rentabilidade_nao_promete_custo_indisponivel(self):
        report = _relatorio()
        report.custo = {"available": False, "reason": "cobertura abaixo do piso"}
        pagina = self._pagina(report)
        lead = re.search(r'id="perf-section">.*?<span class="panel-lead">(.*?)</span>', pagina, re.S)
        self.assertIn("custo e mandato indisponíveis nesta coleta", _texto(lead.group(1)))

    def test_coleta_separa_quem_nao_tem_vitrine(self):
        pagina = self._pagina()
        lead = re.search(r'id="coleta-section">.*?<span class="panel-lead">(.*?)</span>', pagina, re.S)
        self.assertEqual(
            _texto(lead.group(1)),
            "2 de 2 vitrines públicas com coleta ok; sem vitrine pública de previdência: "
            "Nu (Nubank) e Banco Inter.",
        )


class TestNumeros(unittest.TestCase):
    def test_indice_arredonda_como_o_painel(self):
        """0.475 e 0,48 no JS do painel; o f-string dava 0,47."""
        self.assertEqual(_decimal(0.475), "0,48")
        self.assertEqual(_decimal(0.4749), "0,47")
        self.assertEqual(_decimal(1.209), "1,21")


class TestGancho(unittest.TestCase):
    def test_render_outputs_usa_o_dashboard_de_fundos_por_padrao(self):
        from shelf_monitor.main import render_outputs
        from shelf_monitor.report.dashboard import render_dashboard

        self.assertIs(render_outputs.__kwdefaults__["renderizar_dashboard"], render_dashboard)

    def test_cli_da_previdencia_renderiza_a_pagina_propria(self):
        from shelf_monitor.previdencia import cli

        with tempfile.TemporaryDirectory() as tmp,                 mock.patch.object(cli, "render_outputs", return_value=[]) as render:
            cli.renderizar(ConfigPrevidencia(data_dir=tmp), _relatorio())
        self.assertIs(
            render.call_args.kwargs["renderizar_dashboard"], render_dashboard_previdencia
        )


if __name__ == "__main__":
    unittest.main()
