"""E-mail mensal, flash semanal e o ciclo automatico da industria (Fase 1E).

Trava:

- o e-mail do mes: HTML de e-mail (sem `<svg>`, `<style>`, `<script>` nem
  `<img>`), dentro do orcamento de 100 KB do Gmail, os mesmos fatos no md e no
  html (leitura, destaques, macroclasses, prazos) e, passado o orcamento, as
  figuras saem e as tabelas ficam;
- o flash: a semana do ultimo dia completo (com ao menos 3 dias), o dia que
  ainda esta chegando fora e dito, a comparacao por dia util com as 12
  semanas anteriores, a fatia dos fundos tirada do fluxo, e as duas recusas -
  sem retrato do universo e retrato com mais de 45 dias;
- o `--ciclo auto`: mensal quando a competencia nao saiu e a carteira ja esta
  na CVM; senao flash, se o ultimo tem 7 dias ou mais; senao nada.

A base do mes e a de test_industria_painel (a de test_industria_bloco com o
dinheiro em milhoes).

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import html as html_lib
import json
import re
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from shelf_monitor.industria.bloco import montar_bloco
from shelf_monitor.industria.ciclo import decidir_ciclo
from shelf_monitor.industria.flash import (
    caminho_do_retrato,
    gravar_retrato_do_universo,
    montar_flash,
    ultimo_flash,
)
from shelf_monitor.report import industria_email
from shelf_monitor.report.industria_email import (
    gravar_email,
    gravar_flash,
    render_email_html,
    render_email_markdown,
    render_flash_html,
    render_flash_markdown,
)
from shelf_monitor.report.retail_flow import DailyFlow
from shelf_monitor.report.shared.graficos_email import MARCA_DA_FIGURA
from shelf_monitor.report.shared.numeros import reais_com_sinal

from fixtures_comuns import temp_config
from test_industria_painel import _base

AGORA = datetime(2026, 9, 28, 10, 0)
_PROIBIDAS = ("<svg", "<style", "<script", "<img")


def _texto(html: str) -> str:
    """O texto que o leitor ve: tag de texto (negrito, link) some sem deixar
    espaco - "**Acoes**, a que" continua "Acoes, a que" -, e as outras viram
    espaco."""
    sem_inline = re.sub(r"</?(?:strong|b|a|span|small)\b[^>]*>", "", html)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", sem_inline)))


class _Mes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, cls._tmp = temp_config()
        cls.bloco = montar_bloco(_base(), cls.config)
        cls.md = render_email_markdown(cls.bloco, cls.config, AGORA)
        cls.html = render_email_html(cls.bloco, cls.config, AGORA)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestEmailDoMes(_Mes):
    def test_html_de_e_mail_dentro_do_orcamento(self):
        for proibida in _PROIBIDAS:
            self.assertNotIn(proibida, self.html)
        self.assertLess(len(self.html.encode("utf-8")), industria_email._ORCAMENTO_DO_EMAIL)
        self.assertIn("max-width:640px", self.html)
        self.assertIn('<meta name="color-scheme" content="light">', self.html)
        self.assertIn("<title>A indústria de fundos em ago/2026</title>", self.html)
        self.assertNotIn("limite de tamanho do Gmail", self.html)
        self.assertGreaterEqual(self.html.count(MARCA_DA_FIGURA), 3)

    def test_mesmos_fatos_no_md_e_no_html(self):
        texto = _texto(self.html)
        for frase in self.bloco["leitura"]:
            self.assertIn(frase, self.md)
            self.assertIn(frase.replace("**", ""), texto)
        for item in self.bloco["estrategias"]["n1"]:
            fluxo = reais_com_sinal(item["janelas"]["12m"]["fluxo_investidor"])
            self.assertIn(f"| {item['n1']} |", self.md)
            self.assertIn(fluxo, self.md)
            self.assertIn(item["n1"], texto)
            self.assertIn(fluxo, texto)
        for prazo in self.bloco["prazos"]:
            self.assertIn(prazo, self.md)
            self.assertIn(prazo, texto)
        for rotulo, valor, prazo in industria_email._destaques(self.bloco):
            for dito in (rotulo, valor, prazo):
                self.assertIn(dito, self.md)
                self.assertIn(dito, texto)

    def test_neutro_e_com_fontes(self):
        self.assertNotIn("foco", self.bloco)
        self.assertIn("sem casa em foco", self.md)
        self.assertIn("https://dados.cvm.gov.br/dataset/fi-doc-perfil_mensal", self.md)
        self.assertIn('href="https://dados.cvm.gov.br/dataset/fi-doc-perfil_mensal"', self.html)
        self.assertIn("industria_dashboard.html", self.md)
        self.assertIn("industria_dashboard.html", self.html)

    def test_acima_do_orcamento_as_figuras_saem_e_as_tabelas_ficam(self):
        inteiro = self.html
        figuras = inteiro.count(MARCA_DA_FIGURA)
        with mock.patch.object(industria_email, "_ORCAMENTO_DO_EMAIL", len(inteiro.encode()) - 1):
            cortado = render_email_html(self.bloco, self.config, AGORA)
        self.assertEqual(cortado.count(MARCA_DA_FIGURA), figuras - 1)
        self.assertIn("ficou de fora pelo limite de tamanho do Gmail (tipo de gestora)", cortado)
        with mock.patch.object(industria_email, "_ORCAMENTO_DO_EMAIL", 1):
            sem_nenhuma = render_email_html(self.bloco, self.config, AGORA)
        self.assertEqual(sem_nenhuma.count(MARCA_DA_FIGURA), 0)
        self.assertIn("ficaram de fora", sem_nenhuma)
        self.assertIn("MACROCLASSE", _texto(sem_nenhuma).upper())

    def test_grava_md_e_html_com_a_data_da_execucao(self):
        caminho = gravar_email(self.config, self.bloco, AGORA)
        self.assertEqual(caminho.name, "industria-2026-09-28.html")
        self.assertTrue(caminho.with_suffix(".md").is_file())

    def test_bloco_indisponivel_diz_o_motivo(self):
        bloco = {"available": False, "reason": "perfil mensal de 09/2026 indisponível"}
        self.assertIn("perfil mensal de 09/2026 indisponível", render_email_markdown(bloco, None, AGORA))
        self.assertIn("perfil mensal de 09/2026 indisponível", render_email_html(bloco, None, AGORA))


# --- o flash ------------------------------------------------------------------------

HOJE = date(2026, 9, 28)
PRODUTOS = [
    # cnpj, subclasse, n1, n2, fatia de fundos, nome, gestora
    ["11111111000111", "", "Pós-fixado e caixa", "DI Soberano", 0.2, "FUNDO A RENDA FIXA", "Gestora X"],
    ["22222222000122", "", "Ações", "Livre", 0.0, "FUNDO B AÇÕES", "Gestora Y"],
]


def _gravar_retrato(config, data_base: str = "2026-08-31") -> None:
    caminho = caminho_do_retrato(config)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps({
        "competencia": "202608", "data_base": data_base, "gravado_em": "2026-09-16",
        "colunas": ["cnpj", "subclasse", "n1", "n2", "fatia_fundos", "nome", "gestora"],
        "produtos": PRODUTOS,
    }), encoding="utf-8")


def _leitor(ultimo: date = date(2026, 9, 25), so_a_no_ultimo: bool = True):
    """A capta 10 por dia (100 na semana de 21/09) e B resgata 5; o ultimo dia
    tem so A - metade dos produtos, um dia que ainda esta chegando."""
    def ler(mes: str) -> list:
        ano, numero = int(mes[:4]), int(mes[4:])
        dia = date(ano, numero, 1)
        registros = []
        while dia.month == numero and dia <= ultimo:
            if dia.weekday() < 5:
                captacao_a = 100.0 if date(2026, 9, 21) <= dia else 10.0
                registros.append(DailyFlow(cnpj="11111111000111", dia=dia, captacao=captacao_a))
                if not (so_a_no_ultimo and dia == ultimo):
                    registros.append(DailyFlow(cnpj="22222222000122", dia=dia, resgates=5.0))
            dia += timedelta(days=1)
        return registros
    return ler


class TestFlash(unittest.TestCase):
    def setUp(self):
        self.config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)

    def test_semana_do_ultimo_dia_completo_contra_12_semanas(self):
        _gravar_retrato(self.config)
        flash = montar_flash(self.config, hoje=HOJE, leitor=_leitor())
        self.assertTrue(flash["available"], flash.get("reason"))
        self.assertEqual((flash["semana"]["de"], flash["semana"]["ate"], flash["semana"]["dias"]),
                         ("2026-09-21", "2026-09-24", 4))
        # A sexta 25/09 so tem metade dos produtos: fica de fora, e o flash diz.
        self.assertEqual(flash["dias_incompletos"], [{"dia": "2026-09-25", "cobertura": 0.5}])
        # 12 semanas antes da segunda 21/09: de 29/06 a sexta 18/09.
        self.assertEqual((flash["comparacao"]["de"], flash["comparacao"]["ate"]), ("2026-06-29", "2026-09-18"))
        # Camada do investidor: A tem 20% de fundos. Semana: 4 x (100 x 0,8 - 5).
        self.assertAlmostEqual(flash["total"]["semana"], 300.0)
        self.assertAlmostEqual(flash["total"]["por_dia"], 75.0)
        self.assertAlmostEqual(flash["total"]["por_dia_12_semanas"], 3.0)
        por_n1 = {i["n1"]: i for i in flash["por_n1"]}
        self.assertAlmostEqual(por_n1["Ações"]["semana"], -20.0)
        self.assertEqual(flash["maiores_entradas"][0]["nome"], "FUNDO A RENDA FIXA")
        self.assertEqual(flash["maiores_saidas"][0]["nome"], "FUNDO B AÇÕES")
        self.assertIn("semana: de 21/09/2026 a 24/09/2026 (4 dias úteis com informe)", flash["prazos"])

    def test_semana_com_menos_de_tres_dias_cede_a_anterior(self):
        _gravar_retrato(self.config)
        # Ultimo dia completo: terca 22/09 (a quarta 23/09 chega pela metade).
        flash = montar_flash(self.config, hoje=HOJE, leitor=_leitor(ultimo=date(2026, 9, 23)))
        self.assertEqual((flash["semana"]["de"], flash["semana"]["ate"]), ("2026-09-14", "2026-09-18"))

    def test_sem_retrato_ou_com_retrato_velho_recusa(self):
        flash = montar_flash(self.config, hoje=HOJE, leitor=_leitor())
        self.assertFalse(flash["available"])
        self.assertIn("ciclo mensal", flash["reason"])
        _gravar_retrato(self.config, data_base="2026-08-13")
        flash = montar_flash(self.config, hoje=HOJE, leitor=_leitor())
        self.assertFalse(flash["available"])
        self.assertIn("46 dias, acima dos 45", flash["reason"])

    def test_e_mail_do_flash(self):
        _gravar_retrato(self.config)
        flash = montar_flash(self.config, hoje=HOJE, leitor=_leitor())
        md, html = render_flash_markdown(flash, AGORA), render_flash_html(flash, AGORA)
        for proibida in _PROIBIDAS:
            self.assertNotIn(proibida, html)
        texto = _texto(html)
        for frase in flash["leitura"]:
            self.assertIn(frase, md)
            self.assertIn(frase.replace("**", ""), texto)
        for dito in ("25/09/2026 (50% dos produtos)", "Pós-fixado e caixa", "FUNDO A RF"):
            self.assertIn(dito, md)
            self.assertIn(dito, texto)
        caminho = gravar_flash(self.config, flash, AGORA)
        self.assertEqual(caminho.name, "industria-flash-2026-09-28.html")
        self.assertEqual(ultimo_flash(self.config), date(2026, 9, 28))

    def test_retrato_gravado_pelo_ciclo_mensal(self):
        bloco = montar_bloco(_base(), self.config)
        caminho = gravar_retrato_do_universo(self.config, bloco, hoje=HOJE)
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        self.assertEqual(dados["competencia"], "202608")
        self.assertEqual(dados["data_base"], bloco["informe"]["ate"])
        linhas_do_universo = [l for l in bloco["_linhas"] if l.motivo == "universo"]
        self.assertEqual(len(dados["produtos"]), len(linhas_do_universo))


class TestCicloAutomatico(unittest.TestCase):
    def setUp(self):
        self.config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)

    def _flash_em(self, dia: date) -> None:
        self.config.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.config.reports_dir / f"industria-flash-{dia:%Y-%m-%d}.md").write_text("x", encoding="utf-8")

    def _publicar(self, competencia: str) -> None:
        destino = self.config.historico_dir / "industria" / f"{competencia}.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text("{}", encoding="utf-8")

    def test_mensal_quando_a_competencia_nao_saiu_e_a_carteira_ja(self):
        ciclo, motivo = decidir_ciclo(self.config, hoje=HOJE, competencia="202608", carteira=lambda c: True)
        self.assertEqual(ciclo, "mensal")
        self.assertIn("08/2026", motivo)

    def test_sem_carteira_o_mensal_espera_e_sai_o_flash(self):
        ciclo, motivo = decidir_ciclo(self.config, hoje=HOJE, competencia="202608", carteira=lambda c: False)
        self.assertEqual(ciclo, "flash")
        self.assertIn("carteira de 08/2026 ainda não publicada", motivo)

    def test_publicada_flash_a_cada_sete_dias(self):
        self._publicar("202608")
        nunca = lambda c: self.fail("carteira consultada com a competência já publicada")  # noqa: E731
        self._flash_em(HOJE - timedelta(days=3))
        self.assertEqual(decidir_ciclo(self.config, hoje=HOJE, competencia="202608", carteira=nunca)[0], "nada")
        self._flash_em(HOJE - timedelta(days=10))
        (self.config.reports_dir / f"industria-flash-{HOJE - timedelta(days=3):%Y-%m-%d}.md").unlink()
        self.assertEqual(decidir_ciclo(self.config, hoje=HOJE, competencia="202608", carteira=nunca)[0], "flash")


if __name__ == "__main__":
    unittest.main()
