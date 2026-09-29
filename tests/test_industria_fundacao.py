"""Fundacao do monitor da industria: dados, universo e camada do investidor.

Cada classe de teste trava uma costura da Fase 1A:

- o CNPJ alfanumerico (desde 07/2026) sobrevive a normalizacao;
- a serie mensal nao soma duas vezes a linha que a CVM retifica, e le a
  competencia fechada do cache quando a serie termina nela (`ate=`);
- o retrato do cadastro e deterministico (os mesmos bytes a cada gravacao);
- o perfil mensal fica com a maior versao e conta a linha desalinhada;
- o universo e uma particao, na ordem certa (previdencia antes de exclusivo,
  subclasse antes de classe), com a situacao datada;
- a camada do investidor fecha: f_inv + f_fundos = captacao declarada.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import gzip
import unittest
from datetime import date
from unittest import mock

from shelf_monitor.industria.base import BaseIndustria
from shelf_monitor.industria.cadastro import (
    GERAL,
    PROFISSIONAL,
    QUALIFICADO,
    Cadastro,
    Produto,
    publico_normalizado,
)
from shelf_monitor.industria.cotistas import (
    CHAVES,
    SEGMENTOS,
    PerfilClasse,
    _gravar_compacto,
    _ler_compacto,
    carregar_perfil,
    ler_perfil,
)
from shelf_monitor.industria.painel import (
    LinhaMes,
    camadas_do_universo,
    funil_do_mes,
    linhas_do_mes,
)
from shelf_monitor.industria.universo import MOTIVOS, UNIVERSO, escopo_do_tipo, motivo
from shelf_monitor.market.historico import (
    caminho_do_retrato,
    competencias_com_retrato,
    gravar_retrato,
    ler_retrato,
)
from shelf_monitor.market.models import (
    ClassFees,
    FeeUnit,
    MarketClass,
    MarketDatabase,
    MarketFund,
    MarketSubclass,
    Origin,
)
from shelf_monitor.normalize import cnpj_root, format_cnpj, normalize_cnpj, parece_cnpj
from shelf_monitor.report.rentabilidade import (
    _CABECALHO_CACHE,
    MesSubclasse,
    SerieMensal,
)
from shelf_monitor.report.retail_flow import DailyFlow

from fixtures_comuns import temp_config


class TestCnpjAlfanumerico(unittest.TestCase):
    def test_alfanumerico_com_e_sem_mascara_vira_14_caracteres(self):
        for bruto in ("12.ABC.345/01DE-35", "12abc34501de35", "CNPJ 12.ABC.345/01DE-35 do fundo"):
            self.assertEqual(normalize_cnpj(bruto), "12ABC34501DE35", bruto)
        self.assertEqual(format_cnpj("12ABC34501DE35"), "12.ABC.345/01DE-35")
        self.assertEqual(cnpj_root("12.ABC.345/01DE-35"), "12ABC345")

    def test_numerico_segue_como_antes(self):
        self.assertEqual(normalize_cnpj("12.345.678/0001-90"), "12345678000190")
        # Feed que serializa como numero perde o zero a esquerda.
        self.assertEqual(normalize_cnpj("1234567000190"), "01234567000190")

    def test_nome_nao_vira_cnpj(self):
        """Tirar as letras e completar com zeros transformava texto em CNPJ."""
        for texto in ("SPX", "KAPITALO ZETA", ""):
            self.assertIsNone(normalize_cnpj(texto))
            self.assertFalse(parece_cnpj(texto))

    def test_parece_cnpj_exige_o_cnpj_inteiro(self):
        self.assertTrue(parece_cnpj("12.345.678/0001-90"))
        self.assertTrue(parece_cnpj("12.ABC.345/01DE-35"))
        self.assertFalse(parece_cnpj("12345"))


def _registro(cnpj: str, dia: int, captacao: float, *, sub: str = "", pl: float = 100.0) -> DailyFlow:
    return DailyFlow(
        cnpj=cnpj, dia=date(2026, 8, dia), captacao=captacao, resgates=0.0,
        pl=pl, cota=1.0 + dia / 100, cotistas=10, subclasse=sub,
    )


class TestSerieMensalRetificacao(unittest.TestCase):
    def test_linha_retificada_conta_uma_vez_e_vence_a_ultima(self):
        registros = [
            _registro("1", 3, 50.0),
            _registro("1", 4, 30.0),
            _registro("1", 4, 70.0, pl=200.0),  # retificacao do dia 4
        ]
        resumo = SerieMensal._resumir(registros)
        item = resumo[("1", "")]
        self.assertEqual(item.captacao, 120.0)  # 50 + 70, nao 50 + 30 + 70
        self.assertEqual(item.pregoes, 2)
        self.assertEqual(item.pl_fim, 200.0)

    def test_cache_de_antes_da_deduplicacao_e_refeito_quando_ha_zip(self):
        """O cache antigo continua legivel (cota e PL certos), mas `build` o
        refaz do zip: a captacao dele pode somar a retificacao duas vezes."""
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        caminho = SerieMensal._cache_path(config, "202608")
        caminho.parent.mkdir(parents=True, exist_ok=True)
        antigo = _CABECALHO_CACHE.rsplit(";", 1)[0]
        caminho.write_text(antigo + "\n1;;;1;;1;100;10;150;0;21\n", encoding="utf-8")
        self.assertEqual(SerieMensal._ler_cache(caminho)[("1", "")].captacao, 150.0)
        registros = [_registro("1", 3, 50.0), _registro("1", 4, 30.0), _registro("1", 4, 70.0)]
        with mock.patch(
            "shelf_monitor.report.rentabilidade.ler_sem_guardar", return_value=registros
        ):
            serie = SerieMensal.build(config, meses=1, ate="202608")
        item = dict(serie.por_subclasse())[("1", "")]["202608"]
        self.assertEqual(item.captacao, 120.0)
        self.assertTrue(SerieMensal._cache_deduplicado(caminho))

    def test_ate_le_a_competencia_fechada_do_cache_sem_reler_o_informe(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        resumo = {("1", ""): MesSubclasse(cota_ini=1.0, cota_fim=1.1, pl_fim=100.0, pregoes=21)}
        SerieMensal._gravar_cache(SerieMensal._cache_path(config, "202608"), resumo)
        with mock.patch("shelf_monitor.report.rentabilidade.ler_competencia") as corrente, \
                mock.patch("shelf_monitor.report.rentabilidade.ler_sem_guardar", return_value=[]):
            serie = SerieMensal.build(config, meses=1, ate="202608")
        corrente.assert_not_called()
        self.assertEqual(serie.meses, ["202608"])


def _base_de_mercado() -> MarketDatabase:
    db = MarketDatabase(imported_at=date(2026, 9, 28))
    db.funds.append(MarketFund(
        registro_id="10", cnpj="11111111000111", name="FUNDO A", manager_name="GESTORA A",
        manager_cnpj="22222222000122", situation="Em Funcionamento Normal",
    ))
    db.classes.append(MarketClass(
        classe_id="100", fundo_id="10", cnpj="33333333000133", name="CLASSE A RF",
        class_type="Classes de Cotas de Fundos FIF", situation="Em Funcionamento Normal",
        anbima_classification="Renda Fixa Duração Baixa Grau de Invest.", target_audience="",
        exclusive="", net_worth=1_000.0,
    ))
    db.subclasses.extend([
        MarketSubclass(subclasse_id="B", classe_id="100", name="SUB B", target_audience="Profissional"),
        MarketSubclass(subclasse_id="A", classe_id="100", name="SUB A", target_audience="Público Geral"),
    ])
    db.fees["33333333000133"] = ClassFees(
        cnpj="33333333000133", admin_fee=0.5, admin_fee_unit=FeeUnit.PERCENT,
    )
    db.funds.append(MarketFund(
        registro_id="legado:44444444000144", cnpj="44444444000144", name="FUNDO LEGADO",
        origin=Origin.LEGACY, fund_type="FI", situation="EM FUNCIONAMENTO NORMAL",
        legacy_fields={"anbima_classification": "Ações Livre", "target_audience": "Qualificado"},
    ))
    return db


class TestRetratoDoCadastro(unittest.TestCase):
    def setUp(self):
        self.config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)

    def test_mesma_base_grava_os_mesmos_bytes(self):
        caminho = gravar_retrato(self.config, _base_de_mercado())
        primeiro = caminho.read_bytes()
        gravar_retrato(self.config, _base_de_mercado())
        self.assertEqual(caminho.read_bytes(), primeiro)
        # gzip sem data: o cabecalho nao muda de uma execucao para outra.
        self.assertEqual(primeiro[4:8], b"\x00\x00\x00\x00")

    def test_uma_linha_por_subclasse_e_o_legado_com_os_campos_do_cad_fi(self):
        gravar_retrato(self.config, _base_de_mercado())
        self.assertEqual(competencias_com_retrato(self.config), ["202609"])
        linhas = ler_retrato(self.config, "202609")
        self.assertEqual([l["nivel"] for l in linhas], ["subclasse", "subclasse", "fundo_legado"])
        self.assertEqual([l["subclasse_id"] for l in linhas[:2]], ["A", "B"])  # ordenado
        self.assertEqual(linhas[0]["gestor_cnpj"], "22222222000122")
        self.assertEqual(linhas[0]["taxa_adm_unidade"], "percentual")
        self.assertEqual(linhas[2]["classificacao_anbima"], "Ações Livre")
        self.assertEqual(linhas[2]["classe_publico_alvo"], "Qualificado")
        with gzip.open(caminho_do_retrato(self.config, "202609"), "rt", encoding="utf-8") as h:
            self.assertTrue(h.readline().startswith("nivel;classe_cnpj;subclasse_id"))


def _cabecalho_perfil() -> list[str]:
    tipos = ("PF_PB", "PF_VAREJO", "PJ_NAO_FINANC_PB", "PJ_NAO_FINANC_VAREJO", "BANCO",
             "CORRETORA_DISTRIB", "PJ_FINANC", "INVNR", "EAPC", "EFPC", "RPPS", "SEGUR",
             "CAPITALIZ", "FI_CLUBE", "DISTRIB", "OUTRO")
    return (["TP_FUNDO_CLASSE", "CNPJ_FUNDO_CLASSE", "DENOM_SOCIAL", "DT_COMPTC", "VERSAO"]
            + [f"NR_COTST_{t}" for t in tipos] + [f"PR_PL_COTST_{t}" for t in tipos]
            + ["PR_PATRIM_LIQ_MAIOR_COTST"])


def _linha_perfil(cnpj: str, versao: int, fatias: dict[str, float], maior: float = 0.0,
                  cotistas: dict[str, int] | None = None) -> str:
    cabecalho = _cabecalho_perfil()
    valores = {c: "0" for c in cabecalho}
    valores.update({"TP_FUNDO_CLASSE": "CLASSES - FIF", "CNPJ_FUNDO_CLASSE": cnpj,
                    "DENOM_SOCIAL": "X", "DT_COMPTC": "2026-08-31", "VERSAO": str(versao),
                    "PR_PATRIM_LIQ_MAIOR_COTST": f"{maior:.1f}"})
    for tipo, fatia in fatias.items():
        valores[f"PR_PL_COTST_{tipo}"] = f"{fatia:.1f}"
    for tipo, n in (cotistas or {}).items():
        valores[f"NR_COTST_{tipo}"] = str(n)
    return ";".join(valores[c] for c in cabecalho)


def _arquivo_perfil(*linhas: str) -> bytes:
    return ("\n".join([";".join(_cabecalho_perfil()), *linhas]) + "\n").encode("latin-1")


class TestPerfilMensal(unittest.TestCase):
    def test_fica_a_maior_versao_e_a_linha_desalinhada_e_contada(self):
        bruto = _arquivo_perfil(
            _linha_perfil("00.000.000/0001-91", 4, {"PF_VAREJO": 60.0, "FI_CLUBE": 40.0}),
            _linha_perfil("00.000.000/0001-91", 3, {"PF_VAREJO": 10.0, "FI_CLUBE": 90.0}),
            "CLASSES - FIF;00.000.000/0002-72;linha;quebrada",
        )
        perfis, diagnostico = ler_perfil(bruto)
        perfil = perfis["00000000000191"]
        self.assertEqual(perfil.versao, 4)
        self.assertAlmostEqual(perfil.fatia("pf_varejo"), 0.6)
        self.assertAlmostEqual(perfil.fundos, 0.4)
        self.assertEqual(diagnostico.desalinhadas, 1)
        self.assertEqual(diagnostico.versoes_descartadas, 1)

    def test_fatias_normalizadas_e_maior_cotista_zero_e_nao_informado(self):
        bruto = _arquivo_perfil(_linha_perfil(
            "00.000.000/0001-91", 4, {"PF_VAREJO": 50.2, "DISTRIB": 30.0, "EAPC": 20.0},
            cotistas={"PF_VAREJO": 1000, "DISTRIB": 500},
        ))
        perfil = ler_perfil(bruto)[0]["00000000000191"]
        self.assertAlmostEqual(sum(perfil.fatias), 1.0)
        self.assertAlmostEqual(perfil.previdencia, 20.0 / 100.2)
        self.assertIsNone(perfil.maior_cotista)
        self.assertEqual(perfil.total_cotistas, 1500)
        self.assertAlmostEqual(perfil.pf_pj, 80.2 / 100.2)

    def test_compacto_devolve_o_mesmo_perfil(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        perfis = ler_perfil(_arquivo_perfil(_linha_perfil(
            "00.000.000/0001-91", 4, {"PF_PB": 70.0, "OUTRO": 30.0}, maior=55.0,
        )))[0]
        caminho = config.industria_cache_dir / "perfil" / "202608.csv"
        _gravar_compacto(caminho, perfis)
        lido = _ler_compacto(caminho)["00000000000191"]
        original = perfis["00000000000191"]
        self.assertEqual(lido.cotistas, original.cotistas)
        for a, b in zip(lido.fatias, original.fatias):
            self.assertAlmostEqual(a, b, places=6)
        self.assertAlmostEqual(lido.maior_cotista, 0.55)
        # Offline, o compacto basta.
        perfis_offline, aviso = carregar_perfil(config, "202608", baixar=False)
        self.assertIsNone(aviso)
        self.assertIn("00000000000191", perfis_offline)

    def test_offline_sem_nada_em_disco_avisa(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        perfis, aviso = carregar_perfil(config, "202608", baixar=False)
        self.assertIsNone(perfis)
        self.assertIn("offline", aviso)


def _perfil(**fatias: float) -> PerfilClasse:
    total = sum(fatias.values()) or 1.0
    return PerfilClasse(
        cnpj="x", versao=4,
        fatias=tuple(fatias.get(c, 0.0) / total for c in CHAVES),
        cotistas=tuple(100 for _ in CHAVES),
        previdencia=fatias.pop("_previdencia", 0.0),
        maior_cotista=None, soma_informada=1.0,
    )


def _produto(**campos) -> Produto:
    padrao = dict(
        classe="1", subclasse="", nivel="classe", nome="FUNDO RF",
        tipo="Classes de Cotas de Fundos FIF", situacao="Em Funcionamento Normal",
        publico=GERAL, exclusivo=False,
    )
    padrao.update(campos)
    return Produto(**padrao)


class TestUniverso(unittest.TestCase):
    M = "202608"

    def test_um_produto_por_motivo_na_ordem_da_cascata(self):
        casos = {
            "sem_cadastro": (None, None),
            "nao_operacional": (_produto(situacao="Fase Pré-Operacional"), None),
            "fora_do_escopo": (_produto(tipo="Classes de Cotas de Fundos FIDC"), None),
            "etf": (_produto(tipo="Classes de Cotas de Fundos FIIM"), None),
            "previdencia": (_produto(classificacao_anbima="Previdência Multimercado Livre"), None),
            "exclusivo": (_produto(exclusivo=True), None),
            "nao_residente": (_produto(exclusivo_inr=True), None),
            "veiculo_de_fundos": (_produto(), _perfil(fundos=95.0, pf_varejo=5.0)),
            "institucional": (_produto(), _perfil(institucional=96.0, pf_varejo=4.0)),
            "restrito": (_produto(nome="FUNDO RESERVADO FAMILIA"), None),
            UNIVERSO: (_produto(), _perfil(pf_varejo=70.0, fundos=30.0)),
        }
        self.assertEqual(list(casos)[:-1], [m for m, _ in MOTIVOS])
        for esperado, (produto, perfil) in casos.items():
            self.assertEqual(motivo(produto, perfil, self.M), esperado, esperado)

    def test_previdencia_vem_antes_de_exclusivo(self):
        """Todo fundo de previdencia e registrado exclusivo: a seguradora e a unica cotista."""
        produto = _produto(exclusivo=True, classificacao_anbima="Previdência RF Duração Livre Crédito Liv")
        self.assertEqual(motivo(produto, None, self.M), "previdencia")

    def test_liquidacao_iniciada_no_mes_mantem_o_mes_e_a_anterior_tira(self):
        no_mes = _produto(situacao="Em Liquidação", inicio_situacao=date(2026, 8, 20))
        antes = _produto(situacao="Em Liquidação", inicio_situacao=date(2026, 7, 31))
        self.assertEqual(motivo(no_mes, None, self.M), UNIVERSO)
        self.assertEqual(motivo(no_mes, None, "202607"), UNIVERSO)
        self.assertEqual(motivo(antes, None, self.M), "nao_operacional")

    def test_restrito_por_concentracao_e_publico_nao_exclui(self):
        concentrado = PerfilClasse(
            cnpj="x", versao=4, fatias=tuple(1.0 if c == "pf_private" else 0.0 for c in CHAVES),
            cotistas=tuple(3 if c == "pf_private" else 0 for c in CHAVES),
            previdencia=0.0, maior_cotista=0.8, soma_informada=1.0,
        )
        self.assertEqual(motivo(_produto(), concentrado, self.M), "restrito")
        self.assertEqual(motivo(_produto(publico=PROFISSIONAL), None, self.M), UNIVERSO)

    def test_tipo_do_cadastro_e_do_legado(self):
        self.assertEqual(escopo_do_tipo("Classes de Cotas de Fundos FIIM"), "etf")
        self.assertEqual(escopo_do_tipo("Classes de Cotas de Fundos FII"), "fora_do_escopo")
        self.assertEqual(escopo_do_tipo("Classes de Cotas de Fundos FIF (FAPI)"), "fora_do_escopo")
        self.assertIsNone(escopo_do_tipo("Classes de Cotas de Fundos FIF"))
        self.assertIsNone(escopo_do_tipo("FI"))

    def test_publico_normalizado(self):
        self.assertEqual(publico_normalizado("PÚBLICO EM GERAL"), GERAL)
        self.assertEqual(publico_normalizado("Investidores Qualificados"), QUALIFICADO)
        self.assertEqual(publico_normalizado("INVESTIDORES PROFISSIONAIS"), PROFISSIONAL)
        self.assertEqual(publico_normalizado(""), "")


class TestSubclasseAntesDaClasse(unittest.TestCase):
    def test_classe_sem_publico_e_subclasses_com_publico(self):
        cadastro = Cadastro("202609", {
            ("1", "A"): _produto(subclasse="A", nivel="subclasse", publico=GERAL),
            ("1", "B"): _produto(subclasse="B", nivel="subclasse", publico=PROFISSIONAL, exclusivo=True),
        })
        self.assertEqual(cadastro.produto("1", "B").publico, PROFISSIONAL)
        # Linha do informe sem subclasse numa classe que tem subclasses: a
        # classe inteira, com o publico mais aberto e exclusiva so se todas forem.
        visao = cadastro.produto("1", "")
        self.assertEqual(visao.publico, GERAL)
        self.assertFalse(visao.exclusivo)


def _mes(pl: float, captacao: float, *, cota_ini: float = 1.0, cota_fim: float = 1.0) -> MesSubclasse:
    return MesSubclasse(cota_ini=cota_ini, cota_fim=cota_fim, pl_fim=pl, cotistas_fim=10,
                        captacao=captacao, resgates=0.0, pregoes=21)


class TestCamadaDoInvestidor(unittest.TestCase):
    def _base(self) -> BaseIndustria:
        cadastro = Cadastro("202609", {
            ("1", ""): _produto(classe="1"),
            ("2", ""): _produto(classe="2", exclusivo=True),
            ("3", ""): _produto(classe="3", tipo="Classes de Cotas de Fundos FIDC", pl_cadastro=500.0),
        })
        serie = SerieMensal(["202607", "202608"], {
            ("1", ""): {"202607": _mes(1000.0, 0.0), "202608": _mes(1100.0, 100.0)},
            ("2", ""): {"202608": _mes(300.0, 10.0)},
            ("9", ""): {"202608": _mes(50.0, 5.0)},
        })
        perfil_jul = _perfil(pf_varejo=80.0, fundos=20.0)
        perfil_ago = _perfil(pf_varejo=70.0, fundos=30.0)
        return BaseIndustria(
            competencia="202608", meses=["202607", "202608"], cadastro=cadastro, serie=serie,
            perfis={"202607": {"1": perfil_jul}, "202608": {"1": perfil_ago}},
        )

    def test_fluxo_do_investidor_fecha_com_o_declarado(self):
        base = self._base()
        linha = next(l for l in linhas_do_mes(base, "202608") if l.classe == "1")
        self.assertAlmostEqual(linha.fluxo_investidor + linha.fluxo_fundos, linha.captacao_liquida)
        # f_fundos = 1100 x 0,30 - 1000 x 0,20 x (1 + 0) = 130: os fundos
        # aplicaram mais do que o total, e o investidor final resgatou 30.
        self.assertAlmostEqual(linha.fluxo_fundos, 130.0)
        self.assertAlmostEqual(linha.fluxo_investidor, -30.0)
        self.assertAlmostEqual(linha.pl_investidor, 770.0)

    def test_sem_mes_anterior_cai_no_rateio(self):
        linha = LinhaMes(
            classe="1", subclasse="", motivo=UNIVERSO, pl=100.0, pl_anterior=None,
            captacao=10.0, resgates=0.0, cotistas=1, retorno=None, fatia_fundos=0.25,
            fatia_fundos_anterior=None, fonte_fatia="perfil",
        )
        self.assertAlmostEqual(linha.fluxo_fundos, 2.5)
        self.assertAlmostEqual(linha.fluxo_investidor, 7.5)

    def test_funil_e_particao_e_camadas_so_do_universo(self):
        base = self._base()
        funil = funil_do_mes(base, "202608")
        por_motivo = {d.motivo: d for d in funil}
        self.assertEqual(sum(d.produtos for d in funil), 4)  # 3 do cadastro + 1 so no informe
        self.assertEqual(por_motivo["sem_cadastro"].pl, 50.0)
        self.assertEqual(por_motivo["exclusivo"].pl, 300.0)
        self.assertEqual(por_motivo["fora_do_escopo"].pl, 500.0)  # PL do cadastro
        self.assertEqual(por_motivo[UNIVERSO].pl, 1100.0)
        camadas = camadas_do_universo(base, linhas_do_mes(base, "202608"))
        self.assertAlmostEqual(camadas.pl_bruto, 1100.0)
        self.assertAlmostEqual(camadas.pl_investidor, 770.0)
        self.assertAlmostEqual(sum(camadas.segmentos.values()), camadas.pl_bruto)
        self.assertEqual(len(SEGMENTOS), len(camadas.segmentos))


if __name__ == "__main__":
    unittest.main()
