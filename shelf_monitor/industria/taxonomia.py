"""A estrategia de cada classe: macroclasse (N1) e peer group (N2).

O N1 segue a forma como as prateleiras de distribuicao se organizam
(pos-fixado e caixa, credito privado, juros e inflacao, multimercado, acoes,
internacional, cambial). O N2 e o grupo onde a comparacao de retorno, risco e
taxa e justa.

A cascata, da evidencia mais forte para a mais fraca, e cada classe guarda a
origem e a confianca do que recebeu:

1. **override** curado (`overrides_estrategia.csv`);
2. **raiz**: feeder e espelho herdam o rotulo da classe onde a carteira mora;
3. **anbima**: o rotulo ANBIMA do cadastro, mapeado em `taxonomia.csv` (vem
   truncado em 40 caracteres, entao o casamento e por prefixo); "Indexados"
   se resolve pelo indicador de desempenho;
4. **carteira**: Multimercados Livre com a carteira da raiz em credito (ao
   menos 60%, e menos de 10% em acoes) e credito com outro nome; rotulo de
   duracao media e livre (grau de investimento e soberano) vai para juros
   quando pre e inflacao passam de metade da carteira da raiz; e a classe sem
   rotulo usa a carteira para achar a macroclasse;
5. **nome**: credito incentivado (Lei 12.431) nao tem rotulo proprio na
   ANBIMA - 942 das 1.096 classes com a marca no nome estao sob "RF Duracao
   Livre Credito Livre" (08/2026) -, entao a marca no nome decide;
6. **cvm**: so a classe CVM, com confianca baixa.

Medido em 08/2026 no universo: o rotulo ANBIMA cobre 97,6% do PL; cerca de
19% esta em rotulos largos (RF Duracao Livre GI, Multimercados Livre, Acoes
Livre, sem rotulo, Estrategia Especifica e Dinamico).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shelf_monitor.familias import MapaDeFamilias
from shelf_monitor.industria.cadastro import Cadastro, Produto
from shelf_monitor.industria.curadoria import Curadoria
from shelf_monitor.industria.estrutura import PAPEIS_DE_VEICULO, Estrutura
from shelf_monitor.market.carteira import (
    ACOES,
    CREDITO_BANCARIO,
    CREDITO_CORPORATIVO,
    EXTERIOR,
    SOBERANO_POS,
    SOBERANO_PRE_INFLACAO,
)
from shelf_monitor.normalize import normalize_text
from shelf_monitor.report.shared.limiares import MAX_ACOES_MM_CREDITO, MIN_CREDITO_MM_LIVRE

POS = "Pós-fixado e caixa"
CREDITO = "Crédito privado"
JUROS = "Juros e inflação"
MULTIMERCADO = "Multimercado"
ACOES_N1 = "Ações"
INTERNACIONAL = "Internacional"
CAMBIAL = "Cambial"
NAO_CLASSIFICADO = "Não classificado"
# A ordem de exibicao das macroclasses do universo.
N1_ORDEM: tuple[str, ...] = (
    POS, CREDITO, JUROS, MULTIMERCADO, ACOES_N1, INTERNACIONAL, CAMBIAL, NAO_CLASSIFICADO,
)

INCENTIVADO = "Crédito incentivado (isento)"
CREDITO_VIA_MM = "Crédito via multimercado"
MULTIGESTOR = "Multigestor (FoF)"
JUROS_ATIVO = "Juros ativo (pré e inflação)"
# Rotulos de duracao media e livre que a ANBIMA nao separa por indexador. A
# carteira de 08/2026 era pos na maioria (LFT, compromissada e credito
# bancario), entao ficam em pos-fixado - e viram juros quando pre e inflacao
# passam de metade da carteira da raiz.
_POS_QUE_PODEM_SER_JUROS = (
    "Grau de investimento, duração média e livre",
    "Soberano, duração média e livre",
)

# Marca de credito incentivado de infraestrutura no nome. "INFRA" so como
# palavra: "INFRAESTRUTURA" tambem aparece em fundo de acoes do setor.
_INCENTIVADO = re.compile(
    r"INCENTIVAD|\bINFRA\b|FI INFRA|12 ?431|DEBENTURES INCENTIVADAS|INVESTIMENTO EM INFRAESTRUTURA"
)
_N1_QUE_ACEITAM_INCENTIVADO = (POS, CREDITO, JUROS)
# Macroclasses que dizem o involucro, e nao a estrategia: nao se herdam.
_N1_DE_INVOLUCRO = ("Previdência", "Estruturados")


@dataclass(frozen=True)
class Estrategia:
    n1: str
    n2: str
    origem: str
    confianca: str

    def to_dict(self) -> dict:
        return {"n1": self.n1, "n2": self.n2, "origem": self.origem, "confianca": self.confianca}


def indexado_pelo_indicador(indicador: str | None) -> tuple[str, str]:
    """O peer group de um fundo "Indexados" de renda fixa, pelo indicador."""
    texto = normalize_text(indicador)
    if any(t in texto for t in ("NTN B", "IPCA", "IMA B")):
        return JUROS, "Indexado inflação"
    if any(t in texto for t in ("IRF M", "PREFIXAD", "TAXA DE JURO PREFIXADA")):
        return JUROS, "Indexado pré"
    if any(t in texto for t in ("DI DE UM DIA", "SELIC", "CDI", "IMA S")):
        return POS, "Indexado DI/Selic"
    return JUROS, "Indexado (outros)"


def _pelo_rotulo(curadoria: Curadoria, produto: Produto) -> tuple[str, str, str] | None:
    """(n1, n2, confianca) pelo rotulo ANBIMA; None sem rotulo conhecido."""
    linha = curadoria.linha_da_taxonomia(produto.classificacao_anbima)
    if linha is None:
        return None
    if linha.n2 == "Indexado" and linha.n1 == JUROS:
        n1, n2 = indexado_pelo_indicador(produto.indicador)
        return n1, n2, linha.confianca
    return linha.n1, linha.n2, linha.confianca


def _pela_carteira(perfil: dict[str, float] | None) -> tuple[str, str] | None:
    """A macroclasse de uma classe sem rotulo, pela carteira (look-through)."""
    if not perfil:
        return None
    credito = perfil.get(CREDITO_BANCARIO, 0.0) + perfil.get(CREDITO_CORPORATIVO, 0.0)
    if credito >= 0.5:
        return CREDITO, "Crédito (sem rótulo ANBIMA)"
    if perfil.get(ACOES, 0.0) >= 0.67:
        return ACOES_N1, "Ações (sem rótulo ANBIMA)"
    if perfil.get(EXTERIOR, 0.0) >= 0.4:
        return INTERNACIONAL, "Internacional (sem rótulo ANBIMA)"
    if perfil.get(SOBERANO_POS, 0.0) >= 0.5:
        return POS, "Pós-fixado (sem rótulo ANBIMA)"
    if perfil.get(SOBERANO_PRE_INFLACAO, 0.0) >= 0.5:
        return JUROS, "Juros (sem rótulo ANBIMA)"
    return None


def _pela_cvm(produto: Produto) -> tuple[str, str] | None:
    texto = normalize_text(produto.classificacao_cvm)
    if "RENDA FIXA" in texto:
        return POS, "Renda fixa (sem rótulo ANBIMA)"
    if "MULTIMERCADO" in texto:
        return MULTIMERCADO, "Multimercado (sem rótulo ANBIMA)"
    if "ACOES" in texto or "FMP" in texto:
        return ACOES_N1, "Ações (sem rótulo ANBIMA)"
    if "CAMBIAL" in texto:
        return CAMBIAL, "Cambial"
    return None


class Taxonomia:
    """A estrategia de cada classe, calculada sob demanda e memorizada."""

    def __init__(
        self,
        cadastro: Cadastro,
        estrutura,
        mapa: MapaDeFamilias | None,
        curadoria: Curadoria,
    ) -> None:
        self.cadastro = cadastro
        self.estrutura = estrutura
        self.mapa = mapa
        self.curadoria = curadoria
        self._cache: dict[str, Estrategia] = {}

    def de(self, cnpj: str) -> Estrategia:
        if cnpj not in self._cache:
            self._cache[cnpj] = self._calcular(cnpj)
        return self._cache[cnpj]

    def _produto(self, cnpj: str) -> Produto | None:
        produtos = self.cadastro.classes.get(cnpj)
        return produtos[0] if produtos else None

    def _calcular(self, cnpj: str) -> Estrategia:
        if cnpj in self.curadoria.overrides:
            n1, n2 = self.curadoria.overrides[cnpj]
            return Estrategia(n1, n2, "override", "alta")
        estrutura: Estrutura = self.estrutura.de(cnpj)
        produto = self._produto(cnpj)
        fonte = produto
        origem = "anbima"
        if estrutura.papel in PAPEIS_DE_VEICULO and estrutura.raiz != cnpj:
            raiz = self._produto(estrutura.raiz)
            rotulo_raiz = _pelo_rotulo(self.curadoria, raiz) if raiz is not None else None
            # A raiz empresta a estrategia, nao o involucro: feeder de um master
            # de previdencia ou de FIDC continua sendo o que o proprio rotulo diz.
            if rotulo_raiz is not None and rotulo_raiz[0] not in _N1_DE_INVOLUCRO:
                fonte, origem = raiz, "raiz"

        resultado: tuple[str, str, str] | None = None
        if fonte is not None:
            pelo_rotulo = _pelo_rotulo(self.curadoria, fonte)
            if pelo_rotulo is not None:
                resultado = (*pelo_rotulo[:2], pelo_rotulo[2])

        perfil_raiz = self.mapa.perfil_determinado(estrutura.raiz) if self.mapa else None
        if (
            resultado is not None and resultado[0] == POS
            and resultado[1] in _POS_QUE_PODEM_SER_JUROS and perfil_raiz
            and perfil_raiz.get(SOBERANO_PRE_INFLACAO, 0.0) >= 0.5
        ):
            resultado, origem = (JUROS, JUROS_ATIVO, "media"), "carteira"
        if resultado is not None and resultado[:2] == (MULTIMERCADO, "Livre") and perfil_raiz:
            credito = perfil_raiz.get(CREDITO_BANCARIO, 0.0) + perfil_raiz.get(CREDITO_CORPORATIVO, 0.0)
            if credito >= MIN_CREDITO_MM_LIVRE and perfil_raiz.get(ACOES, 0.0) < MAX_ACOES_MM_CREDITO:
                resultado, origem = (CREDITO, CREDITO_VIA_MM, "media"), "carteira"

        if resultado is None:
            pela_carteira = _pela_carteira(perfil_raiz)
            if pela_carteira is not None:
                resultado, origem = (*pela_carteira, "media"), "carteira"
            elif produto is not None and (pela_cvm := _pela_cvm(produto)) is not None:
                resultado, origem = (*pela_cvm, "baixa"), "cvm"
            else:
                return Estrategia(NAO_CLASSIFICADO, NAO_CLASSIFICADO, "sem_dado", "baixa")

        n1, n2, confianca = resultado
        nomes = " ".join(
            normalize_text(f"{p.nome} {p.nome_subclasse}")
            for p in filter(None, (produto, fonte))
        )
        if n1 in _N1_QUE_ACEITAM_INCENTIVADO and _INCENTIVADO.search(nomes):
            return Estrategia(CREDITO, INCENTIVADO, "nome", "alta")
        if estrutura.papel == "fof_multigestor" and n1 == MULTIMERCADO:
            return Estrategia(MULTIMERCADO, MULTIGESTOR, "estrutura", confianca)
        return Estrategia(n1, n2, origem, confianca)
