"""Vitrine de previdencia do Itau Private: a pagina fora do ar e o CNPJ dos planos.

Trava o passo de 2026-10-14:

- desde 28/09/2026 a pagina do catalogo responde "Pagina indisponivel no
  momento" (erro no servidor do Itau); o coletor diz isso, e grava a evidencia
  com SHELF_BROWSER_DEBUG=1;
- o CNPJ vem primeiro da cota do dia no informe diario da CVM e, no que ela nao
  identifica, do nome - restrito a classes previdenciarias, exclusivas
  incluidas (todo FIE e exclusivo no cadastro);
- os parametros novos do resolvedor por nome nao mudam o modulo de fundos.

As fixtures em tests/fixtures/previdencia/itau/ sao recortes reais: o HTML que
a pagina devolveu em 14/10/2026, 12 classes do cadastro (data/market/,
07/10/2026) e as linhas delas no informe diario de 09 e 10/09/2026. Os planos,
cotas e datas da tabela sao os da vitrine de 25/09/2026.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from shelf_monitor.cnpj_por_nome import (
    KEY_CNPJ_PENDENTE,
    KEY_CNPJ_POR_NOME,
    MOTIVO_SEM_CANDIDATO,
    ResolvedorPorNome,
    eh_previdenciaria,
    palavras_do_nome,
)
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer, SnapshotStatus
from shelf_monitor.previdencia.coletores import construir_coletor
from shelf_monitor.previdencia.coletores import itau as coletor_itau
from shelf_monitor.previdencia.config import ConfigPrevidencia
from shelf_monitor.previdencia.cota import (
    KEY_CNPJ_PELA_COTA,
    carregar_indice,
    data_da_cota,
    identificar_pela_cota,
)
from shelf_monitor.previdencia.eixo import KEY_NOME_REGISTRO

FIXTURES = Path(__file__).parent / "fixtures" / "previdencia" / "itau"
MARKET = FIXTURES / "market"
COLETA = date(2026, 9, 25)


def _linha(nome: str, numero: int, cota: str, data: str) -> str:
    """Uma linha do catalogo, na estrutura medida em 22/09/2026."""
    retornos = "".join(
        f"<td>{v}</td>" for v in (cota[:4], "1,14", "8,66", "12,85", "26,06", "38,13")
    )
    return (
        f'<tr><td class="tabLinhaBrancae">{nome}<sup>({numero})</sup></td>'
        f"<td>{data}</td><td>{cota}</td><td>-0,0207</td>"
        f"<td><table><tr>{retornos}</tr></table></td>"
        f"<td>-</td><td>0,5%</td><td>A</td></tr>"
    )


def _secao(titulo: str) -> str:
    return f'<tr><td colspan="15" class="tabLinhaTitulo">{titulo}</td></tr>'


# Bloco 1 (catalogo geral, com os gemeos de nome de registro) e bloco 2 (a
# sequencia de secoes recomeca - previdencia), com os planos de 25/09/2026.
_CATALOGO = (
    "<html><body><table>"
    + _secao("Juros Prefixados")
    + _linha("Itaú Flexprev Platinum III Renda Fixa", 1, "35,899427", "10/09")
    + _linha("Itaú Flexprev Excellence RF FIC", 2, "42,831539", "10/09")
    + _secao("Multimercados")
    + _linha("Kinea Chronos Multimercado", 3, "21,804627", "9/09")
    + _secao("Juros Prefixados")
    + _linha("Platinum III", 4, "35,899427", "10/09")
    + _secao("PREVIDÊNCIA - Renda Fixa")
    + _linha("Private Prev RF", 5, "42,831539", "10/09")
    + _linha("Private Prev NTN-B 2030", 6, "32,335024", "9/09")
    + _secao("Inflação")
    + _linha("Private Juro Real Target 2030 II", 7, "20,567297", "9/09")
    + _secao("Multimercados")
    + _linha("Itau Private Prev Kinea XTR", 8, "23,973946", "9/09")
    + _linha("Private Prev Vision Plus MM", 9, "185,977719", "9/09")
    + _linha("IBIUNA PREV MM", 10, "185,977719", "9/09")
    + _linha("Kinea XTR II Prev PVT MM", 11, "-", "-")
    + "</table></body></html>"
).encode("cp1252")


def _resolvedor(**kwargs) -> ResolvedorPorNome:
    return ResolvedorPorNome.carregar(MARKET, [], marcas=("ITAU", "FLEXPREV"), **kwargs)


def _previdencia() -> ResolvedorPorNome:
    return _resolvedor(incluir_exclusivos=True, so_previdencia=True)


class TestPaginaForaDoAr(unittest.TestCase):
    """28/09 a 14/10/2026: HTTP 200, e o servidor do Itau em erro."""

    def setUp(self):
        bruto = (FIXTURES / "indisponivel-2026-10-14.html").read_bytes()
        self.html = coletor_itau.decodificar(bruto, "iso-8859-1")

    def test_diz_que_o_catalogo_esta_fora_do_ar(self):
        with self.assertRaises(SourceUnavailable) as erro:
            coletor_itau.parse_catalogo(self.html)
        mensagem = str(erro.exception)
        self.assertIn("catálogo do Private Bank fora do ar", mensagem)
        self.assertIn("Exception has been thrown by the target of an invocation", mensagem)

    def test_pagina_sem_tabela_e_sem_aviso_continua_o_motivo_antigo(self):
        with self.assertRaises(SourceUnavailable) as erro:
            coletor_itau.parse_catalogo("<html><body><p>nada</p></body></html>")
        self.assertEqual(str(erro.exception), "tabela de rentabilidade não encontrada na página")

    def test_coleta_sai_indisponivel_e_grava_a_evidencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigPrevidencia(data_dir=tmp)
            config.browser_debug = True
            coletor = construir_coletor(config, "itau")
            resposta = mock.Mock(
                content=(FIXTURES / "indisponivel-2026-10-14.html").read_bytes(),
                encoding="iso-8859-1",
                url=config.platform("itau").shelf_url,
                status_code=200,
                headers={"Content-Type": "text/html", "Set-Cookie": "TS01=segredo"},
            )
            with mock.patch.object(coletor_itau, "get", return_value=resposta):
                snapshot = coletor.collect()

            self.assertEqual(snapshot.status, SnapshotStatus.UNAVAILABLE)
            self.assertIn("fora do ar", snapshot.error)
            pasta = config.debug_dir / "itau-previdencia"
            self.assertIn("indisponivel", (pasta / "pagina-http.html").read_text(encoding="utf-8"))
            resposta_gravada = json.loads((pasta / "resposta-http.json").read_text(encoding="utf-8"))
            self.assertEqual(resposta_gravada["status"], 200)
            self.assertNotIn("Set-Cookie", resposta_gravada["cabecalho"])


class TestDataDaCota(unittest.TestCase):
    def test_dia_e_mes_viram_a_data_mais_recente_ate_a_coleta(self):
        self.assertEqual(data_da_cota("9/09", COLETA), date(2026, 9, 9))
        self.assertEqual(data_da_cota("20/11", COLETA), date(2025, 11, 20))
        self.assertEqual(data_da_cota("10/09/2026", COLETA), date(2026, 9, 10))

    def test_sem_data_ou_data_impossivel(self):
        self.assertIsNone(data_da_cota("-", COLETA))
        self.assertIsNone(data_da_cota(None, COLETA))
        self.assertIsNone(data_da_cota("29/02", COLETA))  # 2026 nao e bissexto


class TestResolvedorNaPrevidencia(unittest.TestCase):
    """Os parametros novos do resolvedor por nome."""

    def test_fie_exclusivo_so_concorre_quando_pedido(self):
        """Todo FIE e exclusivo no cadastro; o padrao (fundos) continua sem ele."""
        plano = [FundOffer(platform="itau", name="Itaú Flexprev Platinum III Renda Fixa")]
        _resolvedor().resolver(plano)
        self.assertEqual(plano[0].extra[KEY_CNPJ_PENDENTE]["motivo"], MOTIVO_SEM_CANDIDATO)

        plano = [FundOffer(platform="itau", name="Itaú Flexprev Platinum III Renda Fixa")]
        _previdencia().resolver(plano)
        self.assertEqual(plano[0].cnpj, "14096240000146")

    def test_prev_e_flexprev_somem_do_nome_do_plano(self):
        plano = [FundOffer(platform="itau", name="Private Juro Real Target 2030 II")]
        _previdencia().resolver(plano)
        self.assertEqual(plano[0].cnpj, "28046819000109")
        self.assertIn("marca da casa", plano[0].extra[KEY_CNPJ_POR_NOME]["regra"])

    def test_nome_de_registro_vem_antes_do_comercial(self):
        plano = [FundOffer(
            platform="itau", name="Platinum III",
            extra={KEY_NOME_REGISTRO: "Itaú Flexprev Platinum III Renda Fixa"},
        )]
        _previdencia().resolver(plano, nomes=lambda o: [o.extra.get(KEY_NOME_REGISTRO), o.name])
        origem = plano[0].extra[KEY_CNPJ_POR_NOME]
        self.assertEqual(plano[0].cnpj, "14096240000146")
        self.assertEqual(origem["nome_tentado"], "Itaú Flexprev Platinum III Renda Fixa")

    def test_o_nome_sozinho_erra_o_veiculo_da_seguradora(self):
        """O erro medido em 25/09: o nome acha o Ibiuna Previdencia generico."""
        plano = [FundOffer(platform="itau", name="IBIUNA PREV MM")]
        _previdencia().resolver(plano)
        self.assertEqual(plano[0].cnpj, "09342159000169")  # o do Itau e 32162733000119

    def test_so_classe_previdenciaria_concorre(self):
        self.assertTrue(eh_previdenciaria("KINEA PREV XTR FIF CIC MULTIMERCADO"))
        self.assertTrue(eh_previdenciaria("ITAÚ FLEXPREV PLATINUM III FIF CIC RF"))
        self.assertTrue(eh_previdenciaria("ADAM I FIF CIC MULTIMERCADO", "Previdência Multimercado Livre"))
        self.assertFalse(eh_previdenciaria("KINEA CHRONOS FIF MULTIMERCADO", "Multimercados Livre"))

    def test_pvt_e_private(self):
        self.assertEqual(
            palavras_do_nome("ITAÚ FLEXPREV PVT JURO REAL TARGET 2026 II"),
            palavras_do_nome("Itaú Flexprev Private Juro Real Target 2026 II"),
        )


class TestCnpjPelaCota(unittest.TestCase):
    """A cota do dia no informe diario identifica o veiculo do plano."""

    def setUp(self):
        self.indice = carregar_indice(
            ConfigPrevidencia(data_dir=str(FIXTURES)), {date(2026, 9, 9), date(2026, 9, 10)},
            baixar=False,
        )

    def _plano(self, nome: str, cota: float, data: str) -> FundOffer:
        return FundOffer(platform="itau", name=nome, extra={"cota": cota, "data_cota": data})

    def test_cota_do_dia_acha_o_veiculo_do_itau(self):
        planos = [self._plano("Private Prev Vision Plus MM", 185.977719, "9/09")]
        resumo = identificar_pela_cota(planos, self.indice, _previdencia(), COLETA)
        self.assertEqual(planos[0].cnpj, "32162733000119")
        self.assertEqual(planos[0].extra[KEY_CNPJ_PELA_COTA]["dia"], "2026-09-09")
        self.assertIn("IBIUNA I PREVIDÊNCIA ITAÚ", planos[0].extra[KEY_CNPJ_PELA_COTA]["nome"])
        self.assertEqual(resumo.identificados, 1)

    def test_cota_separa_o_par_fic_master_que_o_nome_empata(self):
        planos = [self._plano("Private Prev NTN-B 2030", 32.335024, "9/09")]
        identificar_pela_cota(planos, self.indice, _previdencia(), COLETA)
        self.assertEqual(planos[0].cnpj, "20194039000102")

    def test_classe_fora_do_universo_nao_conta(self):
        """Mesma cota no mesmo dia, mas de classe que o resolvedor nao aceita."""
        planos = [self._plano("Private Prev RF", 42.831539, "10/09")]
        resumo = identificar_pela_cota(planos, self.indice, _resolvedor(), COLETA)
        self.assertIsNone(planos[0].cnpj)  # sem exclusivos, o FIE nao concorre
        self.assertEqual(resumo.sem_par, 1)

    def test_dia_que_o_informe_nao_tem(self):
        planos = [self._plano("Private Prev RF", 42.831539, "11/09")]
        resumo = identificar_pela_cota(planos, self.indice, _previdencia(), COLETA)
        self.assertIsNone(planos[0].cnpj)
        self.assertEqual(resumo.sem_informe, 1)


class TestColetorItauPrivate(unittest.TestCase):
    """A coleta inteira, com a pagina de 25/09/2026 e a base das fixtures."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = ConfigPrevidencia(data_dir=self.tmp.name)
        shutil.copytree(MARKET, self.config.market_dir)
        self.coletor = construir_coletor(self.config, "itau")
        self.coletor.hoje = COLETA

    def _coletar(self):
        resposta = mock.Mock(
            content=_CATALOGO, encoding="ISO-8859-1", url="u", status_code=200, headers={}
        )
        # O informe vem do disco das fixtures; nada vai para a rede.
        local = self.config.market_raw_dir / "inf_diario_fi_202609.zip"
        with mock.patch.object(coletor_itau, "get", return_value=resposta), mock.patch(
            "shelf_monitor.market.informe_diario.fetch_inf_diario_month",
            side_effect=lambda config, mes, force=False: local if mes == "202609" else None,
        ):
            return self.coletor.collect()

    def test_cota_primeiro_nome_depois(self):
        snapshot = self._coletar()
        self.assertEqual(snapshot.status, SnapshotStatus.OK)
        cnpj = {f.name: f.cnpj for f in snapshot.funds}
        self.assertEqual(cnpj, {
            "Platinum III": "14096240000146",
            "Private Prev RF": "10475116000130",
            "Private Prev NTN-B 2030": "20194039000102",
            "Private Juro Real Target 2030 II": "28046819000109",
            "Itau Private Prev Kinea XTR": "26218435000109",
            "Private Prev Vision Plus MM": "32162733000119",
            "Kinea XTR II Prev PVT MM": None,
        })
        por_nome = {f.name for f in snapshot.funds if KEY_CNPJ_POR_NOME in f.extra}
        self.assertEqual(por_nome, {"Private Juro Real Target 2030 II"})

    def test_observacao_conta_o_que_casou_e_por_onde(self):
        snapshot = self._coletar()
        self.assertEqual(
            snapshot.error,
            "CNPJ em 6 de 7 fundos da vitrine (5 pela cota do dia no informe diário, "
            "1 pelo nome); sem CNPJ: 0 empates, 1 sem candidato, 0 conflitos",
        )

    def test_sem_base_de_mercado_sai_sem_cnpj_e_diz_por_que(self):
        shutil.rmtree(self.config.market_dir)
        snapshot = self._coletar()
        self.assertEqual(snapshot.status, SnapshotStatus.OK)
        self.assertTrue(all(f.cnpj is None for f in snapshot.funds))
        self.assertIn("base de mercado ausente", snapshot.error)


if __name__ == "__main__":
    unittest.main()
