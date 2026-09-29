"""Quem entra no universo disponivel para PF/PJ, e por que cada um sai.

Cada produto recebe o PRIMEIRO motivo que se aplicar, na ordem de MOTIVOS. O
funil e uma particao: cada produto cai em exatamente um degrau, e a soma dos
degraus e a base. Medido em 28/09/2026 (cadastro de 09/2026, perfil e carteira
de 08/2026), por classe: 3.082 nao operacionais, 8.310 fora do escopo, 4.587 de
previdencia, 8.090 exclusivas, 3.893 masters, 451 institucionais, nao
residentes e restritas - e 8.369 classes no universo, com R$ 3,95 tri na
camada do investidor.

Tres decisoes que o numero carrega:

- **Previdencia antes de exclusivo.** Todo fundo de previdencia e registrado
  como exclusivo, porque o unico cotista e a seguradora. Na ordem inversa, a
  camada de previdencia sairia com R$ 1,1 tri em vez de R$ 3,8 tri.
- **Atributos fixos, situacao no tempo.** Publico, exclusividade e o que vem
  do perfil sao do ultimo estado conhecido e valem para a janela toda - a CVM
  nao guarda historico de classe, e reclassificar mes a mes faria a classe
  entrar e sair do universo por ruido de composicao. A situacao e a unica que
  tem data: a classe em liquidacao conta nos meses anteriores ao inicio da
  liquidacao, e os resgates dela nao somem da serie.
- **Publico e filtro, nao exclusao.** Profissional entra (decisao do usuario
  em 28/09/2026), ao contrario da vitrine, que o exclui.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from shelf_monitor.industria.cadastro import Produto
from shelf_monitor.industria.cotistas import PerfilClasse
from shelf_monitor.normalize import normalize_text
from shelf_monitor.profile import nome_indica_restricao
from shelf_monitor.report.shared.limiares import (
    MAX_COTISTAS_RESTRITO,
    MIN_FATIA_DOMINANTE,
    MIN_FATIA_FUNDOS_MASTER,
    MIN_FATIA_MAIOR_COTISTA_RESTRITO,
)

UNIVERSO = "universo"

# (chave, rotulo) na ordem em que a cascata avalia.
MOTIVOS: tuple[tuple[str, str], ...] = (
    ("sem_cadastro", "Sem cadastro na CVM"),
    ("nao_operacional", "Não operacional"),
    ("fora_do_escopo", "Estruturados (FIDC, FII, FIP, Fiagro)"),
    ("etf", "ETF"),
    ("previdencia", "Previdência"),
    ("exclusivo", "Exclusivo"),
    ("nao_residente", "Exclusivo de não residente"),
    ("veiculo_de_fundos", "Master (veículo de outros fundos)"),
    ("institucional", "Institucional"),
    ("restrito", "Restrito"),
)
ROTULOS: dict[str, str] = {**dict(MOTIVOS), UNIVERSO: "Universo disponível"}
# Camadas que ficam ao lado do universo: aparecem no painel, nunca na soma.
CAMADAS_AO_LADO = ("previdencia", "etf")

_ESTRUTURADOS = ("FIDC", "FIP", "FIAGRO", "FUNCINE", "FICART", "FMIEE", "FAPI")


def fim_do_mes(competencia: str) -> date:
    ano, mes = int(competencia[:4]), int(competencia[4:6])
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def inicio_do_mes(competencia: str) -> date:
    return date(int(competencia[:4]), int(competencia[4:6]), 1)


def escopo_do_tipo(tipo: str | None) -> str | None:
    """'etf', 'fora_do_escopo' ou None (fundo liquido, dentro do escopo).

    O tipo vem do cadastro ("Classes de Cotas de Fundos FIF", "... FIIM") ou do
    legado ("FI", "FIDC"). FIIM e fundo de indice: ETF. FII casa por palavra
    inteira para nao pegar o FIIM.
    """
    texto = f" {normalize_text(tipo)} "
    if " FIIM " in texto:
        return "etf"
    if " FII " in texto or any(f" {t} " in texto for t in _ESTRUTURADOS):
        return "fora_do_escopo"
    return None


def _em_funcionamento(situacao: str) -> bool:
    return "FUNCIONAMENTO NORMAL" in normalize_text(situacao)


def _operacional_no_mes(produto: Produto, competencia: str) -> bool:
    """A classe funcionava no mes? Situacao diferente de normal so tira o mes
    se comecou antes dele: a liquidacao iniciada no dia 20 ainda deixa a
    classe no universo naquele mes, com os resgates."""
    if _em_funcionamento(produto.situacao):
        return True
    # Pre-operacional ainda nao abriu: a data da situacao e a do registro, e
    # nao marca uma mudanca que deixe meses anteriores em funcionamento.
    if "PRE OPERACIONAL" in normalize_text(produto.situacao):
        return False
    inicio = produto.inicio_situacao or produto.cancelamento
    if inicio is None:
        return False
    return inicio >= inicio_do_mes(competencia)


def _eh_previdencia(produto: Produto, perfil: PerfilClasse | None) -> bool:
    if normalize_text(produto.classificacao_anbima).startswith("PREVIDENCIA"):
        return True
    if produto.previdenciario or produto.exclusivo_previdencia:
        return True
    if "PREVIDENCI" in normalize_text(produto.extrato_publico):
        return True
    return perfil is not None and perfil.informado and perfil.previdencia >= MIN_FATIA_DOMINANTE


def motivo(
    produto: Produto | None,
    perfil: PerfilClasse | None,
    competencia: str,
) -> str:
    """O motivo de exclusao do produto na competencia, ou UNIVERSO."""
    if produto is None:
        return "sem_cadastro"
    if not _operacional_no_mes(produto, competencia):
        return "nao_operacional"
    escopo = escopo_do_tipo(produto.tipo or produto.fundo_tipo)
    if escopo:
        return escopo
    if _eh_previdencia(produto, perfil):
        return "previdencia"
    if produto.exclusivo or "EXCLUSIV" in normalize_text(f"{produto.nome} {produto.nome_subclasse}"):
        return "exclusivo"
    informado = perfil is not None and perfil.informado
    if produto.exclusivo_inr or (informado and perfil.fatia("nao_residente") >= MIN_FATIA_DOMINANTE):
        return "nao_residente"
    if informado and perfil.fundos >= MIN_FATIA_FUNDOS_MASTER:
        return "veiculo_de_fundos"
    if informado and perfil.fatia("institucional") >= MIN_FATIA_DOMINANTE:
        return "institucional"
    if nome_indica_restricao(f"{produto.nome} {produto.nome_subclasse}"):
        return "restrito"
    if (
        perfil is not None
        and perfil.maior_cotista is not None
        and perfil.maior_cotista >= MIN_FATIA_MAIOR_COTISTA_RESTRITO
        and 0 < perfil.total_cotistas <= MAX_COTISTAS_RESTRITO
    ):
        return "restrito"
    return UNIVERSO


@dataclass
class Degrau:
    """Um degrau do funil: quantos produtos e quanto PL sairam por um motivo."""

    motivo: str
    produtos: int = 0
    classes: int = 0
    pl: float = 0.0

    def to_dict(self) -> dict:
        return {
            "motivo": self.motivo,
            "rotulo": ROTULOS[self.motivo],
            "produtos": self.produtos,
            "classes": self.classes,
            "pl": round(self.pl, 2),
        }


def montar_funil(itens: list[tuple[str, str, float]]) -> list[Degrau]:
    """(motivo, cnpj da classe, PL) de cada produto -> degraus na ordem.

    Uma classe com subclasses em degraus diferentes conta como classe em cada
    um deles; o numero de produtos e que fecha com a base.
    """
    degraus = {chave: Degrau(chave) for chave, _ in MOTIVOS}
    degraus[UNIVERSO] = Degrau(UNIVERSO)
    classes: dict[str, set[str]] = {chave: set() for chave in degraus}
    for chave, cnpj, pl in itens:
        degrau = degraus[chave]
        degrau.produtos += 1
        degrau.pl += pl
        classes[chave].add(cnpj)
    for chave, degrau in degraus.items():
        degrau.classes = len(classes[chave])
    return list(degraus.values())
