"""Testes do mandato declarado.

Dois riscos governam os casos aqui: transformar coluna vazia em "nao autoriza",
e comparar o share de uma casa contra o total (que inclui ela mesma) em vez de
contra as demais.

Rode com: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from shelf_monitor.config import Config
from shelf_monitor.models import (
    CollectorStrategy,
    FundOffer,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.report.mandato import (
    DIMENSOES,
    IndiceMandato,
    MandatoCasa,
    MandatoClasse,
    build_mandato,
)

from datetime import date, datetime


def mandato(cnpj: str, **flags) -> MandatoClasse:
    return MandatoClasse(
        cnpj=cnpj,
        flags={chave: flags.get(chave) for chave, _, _, _ in DIMENSOES},
        tetos={chave: flags.get(f"{chave}_teto") for chave, _, _, _ in DIMENSOES},
    )


def casa(platform_id: str, classes: list[MandatoClasse], vitrine: int | None = None) -> MandatoCasa:
    return MandatoCasa(
        platform_id=platform_id,
        label=platform_id.upper(),
        vitrine=vitrine if vitrine is not None else len(classes),
        classes=classes,
    )


class TestFlagVazio(unittest.TestCase):
    def test_coluna_vazia_nao_vira_nao_autoriza(self):
        """FUNDO_ESPELHO vem em branco em 57% das linhas do extrato. Ler isso
        como "nao e espelho" inventaria o dado."""
        self.assertIsNone(mandato("1").autoriza("espelho"))
        self.assertIs(mandato("1", espelho="N").autoriza("espelho"), False)
        self.assertIs(mandato("1", espelho="S").autoriza("espelho"), True)

    def test_share_ignora_quem_nao_respondeu(self):
        uma = casa("xp", [
            mandato("1", credito_privado="S"),
            mandato("2", credito_privado="N"),
            mandato("3"),
        ])
        # 1 de 2 respondidos, nao 1 de 3.
        self.assertAlmostEqual(uma.share("credito_privado"), 0.5)

    def test_cobertura_baixa_suprime_a_dimensao(self):
        """Com uma vitrine de 10 e duas respostas, o share descreve a amostra."""
        rala = casa("xp", [mandato("1", exterior="S"), mandato("2", exterior="N")],
                    vitrine=10)
        self.assertIsNone(rala.share("exterior"))


class TestTetoMediano(unittest.TestCase):
    def test_teto_so_conta_para_quem_autoriza(self):
        uma = casa("xp", [
            mandato("1", credito_privado="S", credito_privado_teto=50.0),
            mandato("2", credito_privado="S", credito_privado_teto=100.0),
            # Nao autoriza: o teto dele nao descreve a politica de ninguem.
            mandato("3", credito_privado="N", credito_privado_teto=0.0),
        ])
        self.assertAlmostEqual(uma.teto_mediano("credito_privado"), 75.0)

    def test_sem_teto_publicado_nao_ha_mediana(self):
        uma = casa("xp", [mandato("1", credito_privado="S")])
        self.assertIsNone(uma.teto_mediano("credito_privado"))


def _snapshot(platform_id: str, cnpjs: list[str]) -> ShelfSnapshot:
    return ShelfSnapshot(
        platform=platform_id,
        collected_at=datetime.combine(date(2026, 9, 20), datetime.min.time()),
        status=SnapshotStatus.OK,
        strategy=CollectorStrategy.PLAYWRIGHT,
        funds=[
            FundOffer(
                platform=platform_id,
                name=f"{platform_id.upper()} FUNDO {i:03d}",
                cnpj=cnpj,
                sources=[CollectorStrategy.PLAYWRIGHT.value],
            )
            for i, cnpj in enumerate(cnpjs)
        ],
    )


class TestIndiceContraAsDemais(unittest.TestCase):
    def test_indice_usa_as_concorrentes_no_denominador(self):
        """Com a propria casa no denominador, todo indice e puxado para 1 -
        quanto maior a casa, mais achatado o sinal."""
        xp = [f"{i:014d}" for i in range(40)]
        btg = [f"{1000 + i:014d}" for i in range(40)]
        classes = {c: mandato(c, exterior="S") for c in xp}
        classes.update({c: mandato(c, exterior="N") for c in btg})

        indice = IndiceMandato(classes)
        resultado = build_mandato_com(indice, [_snapshot("xp", xp), _snapshot("btg", btg)])

        linha = next(
            l for l in resultado["linhas"]
            if l["platform_id"] == "xp" and l["chave"] == "exterior"
        )
        self.assertAlmostEqual(linha["share"], 1.0)
        # 0% nas concorrentes: exclusivo, nao 2x.
        self.assertAlmostEqual(linha["share_pares"], 0.0)
        self.assertTrue(linha["so_ela"])

    def test_dimensao_que_ninguem_autoriza_nao_vira_so_ela(self):
        """0/0 nao e exclusividade.

        Quando nenhuma casa autoriza a dimensao, share e share_pares sao os dois
        zero. Dividir um pelo outro devolvia infinito, a linha saia marcada
        `so_ela` com aposta "acima", e o console imprimia "autorizado em 0% da
        vitrine contra 0% nas demais (so ela)" - uma frase que afirma o
        contrario do dado.
        """
        xp = [f"{i:014d}" for i in range(40)]
        btg = [f"{1000 + i:014d}" for i in range(40)]
        classes = {c: mandato(c, exterior="N") for c in xp + btg}

        resultado = build_mandato_com(
            IndiceMandato(classes), [_snapshot("xp", xp), _snapshot("btg", btg)]
        )

        linha = next(
            l for l in resultado["linhas"]
            if l["platform_id"] == "xp" and l["chave"] == "exterior"
        )
        self.assertAlmostEqual(linha["share"], 0.0)
        self.assertAlmostEqual(linha["share_pares"], 0.0)
        self.assertFalse(linha["so_ela"])
        self.assertEqual(linha["aposta"], "abaixo")
        self.assertNotIn(linha, resultado["apostas"])

    def test_aposta_exclusiva_vem_antes_das_demais(self):
        """`indice` e None quando e infinito - a aposta mais forte que existe.

        Ordenar por `-(indice or 99)` mandava justamente essas linhas para o fim
        da lista, o oposto da intencao, e colapsava indice 0.0 no mesmo balde.
        """
        xp = [f"{i:014d}" for i in range(40)]
        btg = [f"{1000 + i:014d}" for i in range(40)]
        # exterior: so a XP autoriza (indice infinito).
        # credito privado: a XP autoriza o dobro do BTG (indice 2,0).
        classes = {
            c: mandato(c, exterior="S", credito_privado="S") for c in xp
        }
        classes.update({
            c: mandato(c, exterior="N", credito_privado="S" if i % 2 else "N")
            for i, c in enumerate(btg)
        })

        resultado = build_mandato_com(
            IndiceMandato(classes), [_snapshot("xp", xp), _snapshot("btg", btg)]
        )

        apostas_xp = [a for a in resultado["apostas"] if a["platform_id"] == "xp"]
        self.assertEqual(apostas_xp[0]["chave"], "exterior")
        self.assertTrue(apostas_xp[0]["so_ela"])
        self.assertEqual(apostas_xp[1]["chave"], "credito_privado")

    def test_uma_vitrine_so_nao_permite_indice(self):
        xp = [f"{i:014d}" for i in range(40)]
        indice = IndiceMandato({c: mandato(c, exterior="S") for c in xp})
        resultado = build_mandato_com(indice, [_snapshot("xp", xp)])
        self.assertFalse(resultado["available"])
        self.assertIn("ao menos duas", resultado["reason"])


def build_mandato_com(indice: IndiceMandato, snapshots: list[ShelfSnapshot]) -> dict:
    """Roda o bloco com um indice ja montado, sem tocar o disco."""
    import shelf_monitor.report.mandato as modulo

    original = modulo.IndiceMandato.load
    modulo.IndiceMandato.load = classmethod(lambda cls, _dir: indice)
    try:
        return build_mandato(Config(), snapshots)
    finally:
        modulo.IndiceMandato.load = original


class TestBaseSemAsColunas(unittest.TestCase):
    def test_base_antiga_diz_para_reimportar(self):
        """Antes de o extrato exportar mandato, a base nao tem as colunas - e
        devolver a vitrine inteira com flags vazias seria pior que nao ter."""
        resultado = build_mandato_com(None, [_snapshot("xp", ["1"])])  # type: ignore[arg-type]
        self.assertFalse(resultado["available"])
        self.assertIn("reimportar", resultado["reason"])


if __name__ == "__main__":
    unittest.main()
