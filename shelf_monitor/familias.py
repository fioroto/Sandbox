"""Mapa de familias de estrategia: master, espelhos, classes paralelas e liquidez.

A analise de gap media "este CNPJ nao esta na vitrine da XP". A pergunta do
comite e outra: "o cliente da XP tem acesso a esta estrategia?". As gestoras
vendem a mesma estrategia com CNPJs diferentes em cada distribuidor - um master
e um espelho por casa (as versoes "Advisory" da XP, "Access" do Inter, a marca
propria do BTG) - e versoes paralelas da mesma carteira com liquidez diferente.
Medido em 26/09/2026 (vitrines de 25/09, CDA de 08/2026): dos 636 gaps por CNPJ,
62 eram espelho do mesmo master que um fundo da vitrine XP.

Tres relacoes, da mais forte para a mais fraca:

- **espelho**: a classe tem ao menos `LIMIAR_ESPELHO` do PL em cotas de uma
  unica classe. A cadeia e seguida ate o master, que e onde a carteira mora.
  Ligacao estrutural, lida da carteira - o flag declarado do extrato aparece
  como informacao e nao decide nada;
- **classe paralela**: masters da mesma gestora (raiz do CNPJ do gestor), da
  mesma categoria ANBIMA, com indicador de desempenho compativel e carteira a
  no maximo `LIMIAR_DISTANCIA_CARTEIRA` de distancia. Agrupados por ligacao
  completa (todo par dentro do limiar), para a familia nao se esticar em
  cadeia;
- **correlato**: outra versao da mesma estrategia da mesma gestora (raiz do
  CNPJ do gestor) que a carteira nao ligou - o feeder de cada distribuidor
  ("Advisory" na XP, "Seleção" no Itau e no BTG, "Access", "IU"), o clone
  ("II") e a faixa ("Plus", "D30"). Decide pelo nome da estrategia
  (`palavras_da_estrategia`) e, ao contrario das outras heuristicas,
  reclassifica: pedido de 15/10/2026, gap real e so o fundo que a XP nao tem
  de forma nenhuma. No digest de 14/10/2026, 51 dos 504 gaps reais eram isso
  (o Kapitalo Tarkus FIC do BTG contra o Tarkus Advisory da XP);
- **possivel equivalente**: mesma gestora e mesma categoria, sem carteira que
  permita decidir e com outro nome de estrategia. Informa, nunca reclassifica -
  a regra da casa para heuristica.

Uma familia de estrategia reune as versoes (um master e seus espelhos cada)
que entregam a mesma exposicao. A liquidez e de cada classe: um espelho pode
ter D+30 sobre um master D+0.

O modulo so sabe de CVM - cadastro, extrato e carteira. Vitrine e lista
distribuivel chegam como conjuntos de CNPJ em `cobertura`.
"""
from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from shelf_monitor.cnpj_por_nome import palavras_do_nome
from shelf_monitor.config import Config
from shelf_monitor.market.carteira import (
    ATIVOS_OPACOS,
    COTAS_NAO_RESOLVIDAS,
    FAIXAS,
    ROTULOS_FAIXA,
    SOB_SIGILO,
    CarteiraClasse,
    ResumoCarteira,
    carregar_competencia,
)
from shelf_monitor.market.datasets import COMPETENCIAS_CDA, competencias_fechadas
from shelf_monitor.normalize import (
    normalize_cnpj,
    normalize_cvm_code,
    normalize_text,
    parece_cnpj,
    parse_number,
)

# Fracao minima do PL em cotas de uma unica classe para ser espelho dela.
# Usado na medicao de 26/09/2026: 201 dos 636 gaps passavam.
LIMIAR_ESPELHO = 0.95
# Quantos saltos a cadeia espelho -> master (e o look-through) segue.
PROFUNDIDADE_MAXIMA = 4
# Distancia maxima entre perfis de carteira para duas classes da mesma gestora
# e categoria serem a mesma estrategia. A distancia e a fracao da carteira que
# teria de mudar de faixa (0 = identica, 1 = disjunta). Conservador de
# proposito; calibrado em 26/09/2026 (ver README, "Gaps de prateleira").
LIMIAR_DISTANCIA_CARTEIRA = 0.15
# Fracao minima da carteira em ativos comuns (mesmo emissor, mesma acao, mesmo
# fundo offshore) para duas classes paralelas serem a mesma estrategia. Sem
# ela, toda classe "100% exterior" de um gestor local era equivalente a toda
# outra - Schroder GAIA, MAN AHL e Pearl Diver saiam na mesma familia.
# Calibrado em 26/09/2026 sobre os 225 pares gap x vitrine XP da mesma gestora
# e categoria: com 0,5 entravam fundos irmaos de credito da mesma casa com
# metade da carteira em comum (ARX Vinson x ARX Everest, 0,51); com 0,6 ficam
# os pares com carteira de fato partilhada (Giant Darius, Kapitalo Zeta,
# Bogari Value, Absolute Hidra: 0,74 a 1,00).
LIMIAR_SOBREPOSICAO = 0.6
# Acima desta fracao sob sigilo ou em cotas sem carteira, o perfil e
# indeterminado e nao entra em comparacao.
LIMIAR_SIGILO = 0.5

# Faixas de liquidez: (teto em dias, rotulo). A ultima nao tem teto.
FAIXAS_LIQUIDEZ: tuple[tuple[int | None, str], ...] = (
    (1, "até D+1"),
    (5, "D+2 a D+5"),
    (30, "D+6 a D+30"),
    (90, "D+31 a D+90"),
    (180, "D+91 a D+180"),
    (None, "acima de D+180"),
)

# Vereditos de cobertura, do mais forte ao mais fraco.
PRESENTE = "presente"
COBERTO_ESPELHO = "coberto_espelho"
COBERTO_EQUIVALENTE = "coberto_equivalente"
COBERTO_CORRELATO = "coberto_correlato"
OUTRA_LIQUIDEZ = "outra_liquidez"
GAP_ESTRATEGIA = "gap_estrategia"
GAP_GESTORA = "gap_gestora"
SEM_DADO = "sem_dado_carteira"

ROTULOS_VEREDITO: dict[str, str] = {
    PRESENTE: "mesma classe na base",
    COBERTO_ESPELHO: "coberto por espelho",
    COBERTO_EQUIVALENTE: "coberto por equivalente",
    COBERTO_CORRELATO: "coberto por correlato da mesma gestora",
    OUTRA_LIQUIDEZ: "coberto em outra liquidez",
    GAP_ESTRATEGIA: "gap de estratégia",
    GAP_GESTORA: "gap de gestora",
    SEM_DADO: "sem dado de carteira",
}
COBERTOS = (PRESENTE, COBERTO_ESPELHO, COBERTO_EQUIVALENTE, COBERTO_CORRELATO)
GAPS_REAIS = (GAP_ESTRATEGIA, GAP_GESTORA, SEM_DADO)

ALTA, MEDIA, BAIXA = "alta", "média", "baixa"

# Indicador de desempenho que nao diz nada sobre a estrategia.
_INDICADOR_VAZIO = {"", "NAO SE APLICA", "OUTROS"}
# Situacoes em que a classe nao cobre nada: nao capta, ou deixou de existir.
_INATIVA = ("CANCEL", "INCORPORA", "LIQUIDA")

# Correlato: o que o nome carrega alem da estrategia. Marcas de versao - o
# feeder de cada distribuidor ("Advisory" na XP, "Seleção"/"Selection" no Itau
# e no BTG, "Access", "IU" do Itau Unibanco, o nome da casa que distribui), o
# veiculo para um publico ("Institucional", "Alocadores", "Dist.") e o papel
# na estrutura ("Feeder", "Master") -, e a faixa da mesma estrategia ("Plus",
# "Premium", "NTNB35"). Numero, letra de classe e romano (clone "II") saem por
# regra, em `palavras_da_estrategia`.
_MARCAS_DE_VERSAO = frozenset({
    "ADVISORY", "SELECAO", "SELECTION", "ACCESS", "IU", "XP", "ITAU", "UNIBANCO",
    "BTG", "BTGP", "PACTUAL", "INTER", "ORAMA", "PRIVATE", "PERSONNALITE",
    "UNICLASS", "INSTITUCIONAL", "ALOCADORES", "DIST", "DISTRIBUIDORES",
    "FEEDER", "MASTER", "PLUS", "PREMIUM", "NTNB",
})
# Classe de ativo e rotulos regulatorios, ja na forma canonica de
# `cnpj_por_nome.palavras_do_nome`: dizem a categoria, nao a estrategia.
_CLASSE_DE_ATIVO_NO_NOME = frozenset(
    {"RF", "MM", "ACOES", "CP", "LP", "REFERENCIADO", "IE", "IS", "FIDC"}
)
_ROMANO = re.compile(r"^[IVX]+$")


def palavras_da_estrategia(
    nome: str | None, marca: frozenset[str] = frozenset()
) -> frozenset[str]:
    """O nome da estrategia: o que sobra sem forma juridica, classe de ativo,
    marca da gestora (`marca`), marcas de versao, numero, letra e romano.

    "KAPITALO TARKUS ADVISORY FIF EM COTAS DE FIA" e "KAPITALO TARKUS SELEÇÃO
    FIF CIC EM AÇÕES", com a marca KAPITALO, dao os dois {"TARKUS"}. Nome que
    e so marca e forma juridica ("TB SELEÇÃO FIF CIC EM AÇÕES") da vazio.
    """
    return frozenset(
        palavra
        for palavra in palavras_do_nome(nome) - _CLASSE_DE_ATIVO_NO_NOME - marca
        if not (
            palavra.isdigit() or len(palavra) == 1 or _ROMANO.match(palavra)
            or palavra in _MARCAS_DE_VERSAO
        )
    )


def faixa_de_liquidez(dias: int | None) -> str | None:
    if dias is None:
        return None
    for teto, rotulo in FAIXAS_LIQUIDEZ:
        if teto is None or dias <= teto:
            return rotulo
    return FAIXAS_LIQUIDEZ[-1][1]


def _inteiro(valor: object) -> int | None:
    numero = parse_number(valor)
    return None if numero is None else int(round(numero))


@dataclass(frozen=True)
class Classe:
    """O que o cadastro e o extrato dizem de uma classe (uma linha do mercado.csv)."""

    cnpj: str
    nome: str = ""
    cnpj_fundo: str | None = None
    gestor: str | None = None
    cnpj_gestor: str | None = None
    anbima: str | None = None
    indicador: str | None = None
    pl: float | None = None
    dias_cotizacao: int | None = None
    dias_pagamento: int | None = None
    espelho_declarado: bool | None = None
    situacao: str | None = None

    @property
    def dmais(self) -> int | None:
        """D+ total do resgate: cotizacao mais pagamento."""
        if self.dias_cotizacao is None:
            return None
        return self.dias_cotizacao + (self.dias_pagamento or 0)

    @property
    def raiz_gestor(self) -> str | None:
        return self.cnpj_gestor[:8] if self.cnpj_gestor else None

    @property
    def ativa(self) -> bool:
        situacao = normalize_text(self.situacao)
        return not any(marca in situacao for marca in _INATIVA)

    @property
    def indicador_informativo(self) -> str | None:
        valor = normalize_text(self.indicador)
        return None if valor in _INDICADOR_VAZIO else valor

    @classmethod
    def da_linha(cls, linha: Mapping) -> "Classe | None":
        cnpj = normalize_cnpj(linha.get("cnpj"))
        if not cnpj:
            return None
        espelho = (linha.get("fundo_espelho") or "").strip().upper()
        return cls(
            cnpj=cnpj,
            nome=(linha.get("denominacao") or "").strip(),
            cnpj_fundo=normalize_cnpj(linha.get("cnpj_fundo")),
            gestor=(linha.get("gestor") or "").strip() or None,
            cnpj_gestor=normalize_cnpj(linha.get("cnpj_gestor")),
            anbima=(linha.get("classificacao_anbima") or "").strip() or None,
            indicador=(linha.get("indicador_desempenho") or "").strip() or None,
            pl=parse_number(linha.get("patrimonio_liquido")),
            dias_cotizacao=_inteiro(linha.get("dias_cotizacao_resgate")),
            dias_pagamento=_inteiro(linha.get("dias_pagamento_resgate")),
            espelho_declarado={"S": True, "N": False}.get(espelho),
            situacao=(linha.get("situacao") or "").strip() or None,
        )


@dataclass(frozen=True)
class Membro:
    """Uma classe dentro de uma versao: o master ou um espelho dele."""

    cnpj: str
    nome: str
    papel: str  # "master" | "espelho"
    dmais: int | None
    pl: float | None
    espelho_declarado: bool | None = None

    @property
    def faixa_liquidez(self) -> str | None:
        return faixa_de_liquidez(self.dmais)

    def to_dict(self) -> dict:
        return {
            "cnpj": self.cnpj, "nome": self.nome, "papel": self.papel,
            "dmais": self.dmais, "faixa_liquidez": self.faixa_liquidez,
            "pl": self.pl, "espelho_declarado": self.espelho_declarado,
        }


@dataclass
class Versao:
    """Um master e os espelhos que chegam a ele."""

    master: str
    nome: str
    pl: float | None
    competencia: str | None
    membros: list[Membro] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "master": self.master, "nome": self.nome, "pl": self.pl,
            "competencia": self.competencia,
            "membros": [m.to_dict() for m in self.membros],
        }


@dataclass
class Familia:
    """Uma estrategia de uma gestora: as versoes que entregam a mesma exposicao."""

    id: str
    nome: str
    gestora: str | None
    raiz_gestora: str | None
    anbima: str | None
    indicador: str | None
    perfil: dict[str, float] | None
    versoes: list[Versao] = field(default_factory=list)

    @property
    def pl(self) -> float:
        return sum(v.pl or 0.0 for v in self.versoes)

    @property
    def cnpjs(self) -> list[str]:
        return [m.cnpj for v in self.versoes for m in v.membros]

    def resumo_perfil(self, maximo: int = 3) -> str:
        return resumo_do_perfil(self.perfil, maximo)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "nome": self.nome, "gestora": self.gestora,
            "raiz_gestora": self.raiz_gestora, "anbima": self.anbima,
            "indicador": self.indicador, "pl": self.pl,
            "perfil": self.perfil, "perfil_resumo": self.resumo_perfil(),
            "versoes": [v.to_dict() for v in self.versoes],
        }


def resumo_do_perfil(perfil: Mapping[str, float] | None, maximo: int = 3) -> str:
    """"82% crédito bancário, 15% soberano pós e caixa" - as maiores faixas."""
    if not perfil:
        return "sem carteira publicada"
    maiores = sorted(
        ((f, v) for f, v in perfil.items() if v >= 0.005),
        key=lambda kv: (-kv[1], FAIXAS.index(kv[0]) if kv[0] in FAIXAS else 99),
    )[:maximo]
    return ", ".join(f"{round(v * 100):d}% {ROTULOS_FAIXA.get(f, f)}" for f, v in maiores)


def distancia_de_carteira(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Fracao da carteira que teria de mudar de faixa: 0 identica, 1 disjunta."""
    return 0.5 * sum(abs(a.get(f, 0.0) - b.get(f, 0.0)) for f in FAIXAS)


def sobreposicao_de_ativos(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Fracao da carteira em ativos comuns: 1 identica, 0 nenhum ativo em comum."""
    menor, maior = (a, b) if len(a) <= len(b) else (b, a)
    return sum(
        min(peso, maior.get(ativo, 0.0))
        for ativo, peso in menor.items()
        if ativo not in ATIVOS_OPACOS
    )


@dataclass(frozen=True)
class Veredito:
    """Como um gap aparente se resolve contra uma base de cobertura."""

    tipo: str
    confianca: str | None = None
    cobre: str | None = None
    nome_cobre: str | None = None
    ligacao: str | None = None  # "mesma classe" | "espelho" | "equivalente" | "correlato"
    master: str | None = None
    nome_master: str | None = None
    master_cobre: str | None = None
    distancia: float | None = None
    sobreposicao: float | None = None
    dmais: int | None = None
    dmais_cobre: int | None = None
    competencia: str | None = None
    gestora: str | None = None
    raiz_gestora: str | None = None
    familia_id: str | None = None
    familia_nome: str | None = None
    perfil_resumo: str | None = None
    possivel_equivalente: str | None = None
    nome_possivel_equivalente: str | None = None

    @property
    def coberto(self) -> bool:
        return self.tipo in COBERTOS

    @property
    def gap_real(self) -> bool:
        return self.tipo in GAPS_REAIS

    @property
    def rotulo(self) -> str:
        return ROTULOS_VEREDITO.get(self.tipo, self.tipo)

    def to_dict(self) -> dict:
        return {
            "tipo": self.tipo, "rotulo": self.rotulo, "coberto": self.coberto,
            "gap_real": self.gap_real, "confianca": self.confianca,
            "cobre": self.cobre, "nome_cobre": self.nome_cobre,
            "ligacao": self.ligacao, "master": self.master,
            "nome_master": self.nome_master, "master_cobre": self.master_cobre,
            "distancia": None if self.distancia is None else round(self.distancia, 3),
            "sobreposicao": (
                None if self.sobreposicao is None else round(self.sobreposicao, 3)
            ),
            "dmais": self.dmais, "faixa": faixa_de_liquidez(self.dmais),
            "dmais_cobre": self.dmais_cobre,
            "faixa_cobre": faixa_de_liquidez(self.dmais_cobre),
            "competencia": self.competencia, "gestora": self.gestora,
            "raiz_gestora": self.raiz_gestora, "familia_id": self.familia_id,
            "familia_nome": self.familia_nome, "perfil_resumo": self.perfil_resumo,
            "possivel_equivalente": self.possivel_equivalente,
            "nome_possivel_equivalente": self.nome_possivel_equivalente,
        }


@dataclass
class BaseIndexada:
    """Uma base de cobertura pronta para consulta: classes, masters e familias."""

    classes: frozenset[str]
    por_master: dict[str, list[str]]
    por_familia: dict[str, list[str]]
    raizes: frozenset[str]
    # Raiz da gestora da estrategia -> classes da base: os candidatos a correlato.
    por_raiz: dict[str, list[str]] = field(default_factory=dict)


class MapaDeFamilias:
    """Familias de estrategia do mercado inteiro, a partir de cadastro e carteira."""

    def __init__(
        self,
        classes: dict[str, Classe],
        envelopes: dict[str, list[str]],
        codigos: dict[str, list[str]],
        carteiras: dict[str, list[CarteiraClasse]],
        competencias: list[str],
        avisos: list[str] | None = None,
    ) -> None:
        self._classes = classes
        self._envelopes = envelopes
        self._codigos = codigos
        # Da competencia mais recente para a mais antiga. O perfil usa a mais
        # recente; a ligacao espelho -> master recua quando a recente nao a
        # mostra (a cota do master pode estar sob sigilo naquele mes).
        self._historico = carteiras
        self._carteiras = {c: lista[0] for c, lista in carteiras.items() if lista}
        self.competencias = competencias
        self.avisos = list(avisos or [])
        self._master: dict[str, str] = {}
        self._perfil: dict[str, dict[str, float] | None] = {}
        self._ativos: dict[str, dict[str, float] | None] = {}
        self._familias: dict[str, Familia] = {}
        self._familia_do_master: dict[str, str] = {}
        # Indice invertido do bloco de cotas (investido -> investidores), montado
        # sob demanda por `investidores_de`.
        self._investidores: dict[str, dict[str, float]] | None = None
        self._construir_familias()

    # --- montagem -------------------------------------------------------------

    @classmethod
    def montar(
        cls,
        classes: Iterable[Mapping],
        carteiras: Sequence[ResumoCarteira] = (),
        subclasses: Iterable[Mapping] = (),
        avisos: Sequence[str] = (),
    ) -> "MapaDeFamilias":
        """Monta o mapa a partir das linhas do mercado.csv e das competencias da CDA.

        `carteiras` vem da mais recente para a mais antiga: cada classe usa a
        competencia mais recente em que aparece, e a registra.
        `subclasses` (linhas do subclasses.csv) so servem para resolver codigo
        CVM de subclasse na lista distribuivel.
        """
        por_cnpj: dict[str, Classe] = {}
        envelopes: dict[str, list[str]] = {}
        codigos: dict[str, list[str]] = {}
        por_id_classe: dict[str, str] = {}
        for linha in classes:
            classe = Classe.da_linha(linha)
            if classe is None or classe.cnpj in por_cnpj:
                continue
            por_cnpj[classe.cnpj] = classe
            if classe.cnpj_fundo and classe.cnpj_fundo != classe.cnpj:
                envelopes.setdefault(classe.cnpj_fundo, []).append(classe.cnpj)
            codigo = normalize_cvm_code(linha.get("codigo_cvm"))
            if codigo:
                codigos.setdefault(codigo, []).append(classe.cnpj)
            codigo_fundo = normalize_cvm_code(linha.get("codigo_cvm_fundo"))
            if codigo_fundo and codigo_fundo != codigo:
                codigos.setdefault(codigo_fundo, []).append(classe.cnpj)
            id_classe = (linha.get("id_classe") or "").strip()
            if id_classe:
                por_id_classe[id_classe] = classe.cnpj
        for linha in subclasses:
            codigo = normalize_cvm_code(linha.get("codigo_cvm"))
            cnpj = por_id_classe.get((linha.get("id_classe") or "").strip())
            if codigo and cnpj:
                codigos.setdefault(codigo, [cnpj])

        por_classe: dict[str, list[CarteiraClasse]] = {}
        usadas: list[str] = []
        for resumo in carteiras:
            usadas.append(resumo.competencia)
            for cnpj, carteira in resumo.classes.items():
                por_classe.setdefault(cnpj, []).append(carteira)
        avisos_montagem = list(avisos)
        for resumo in carteiras:
            if resumo.desconhecidos:
                total = sum(resumo.desconhecidos.values())
                avisos_montagem.append(
                    f"carteira de {resumo.competencia[4:]}/{resumo.competencia[:4]}: "
                    f"{total} posições de tipo desconhecido entraram como outros "
                    f"({', '.join(list(resumo.desconhecidos)[:3])})"
                )
        return cls(por_cnpj, envelopes, codigos, por_classe, usadas, avisos_montagem)

    @classmethod
    def carregar(
        cls,
        config: Config,
        hoje: date | None = None,
        *,
        baixar: bool = True,
    ) -> "MapaDeFamilias | None":
        """Le mercado.csv e a CDA das ultimas competencias; None sem a base.

        Sem nenhuma competencia da carteira em disco ou na CVM, devolve None
        tambem: sem carteira nao ha espelho nem equivalencia para medir, e o
        bloco de gaps volta a comparar CNPJ contra CNPJ, com o aviso.
        """
        mercado = config.market_dir / "mercado.csv"
        if not mercado.is_file():
            return None
        resumos: list[ResumoCarteira] = []
        avisos: list[str] = []
        for competencia in competencias_fechadas(hoje or date.today(), COMPETENCIAS_CDA):
            resumo, aviso = carregar_competencia(config, competencia, baixar=baixar)
            if aviso:
                avisos.append(aviso)
            if resumo is not None:
                resumos.append(resumo)
        if not resumos:
            return None
        with mercado.open(encoding="utf-8-sig", newline="") as handle:
            linhas = list(csv.DictReader(handle, delimiter=";"))
        subclasses: list[dict] = []
        caminho_sub = config.market_dir / "subclasses.csv"
        if caminho_sub.is_file():
            with caminho_sub.open(encoding="utf-8-sig", newline="") as handle:
                subclasses = list(csv.DictReader(handle, delimiter=";"))
        return cls.montar(linhas, resumos, subclasses, avisos)

    # --- identidade -----------------------------------------------------------

    def classe(self, cnpj: str | None) -> Classe | None:
        return self._classes.get(cnpj) if cnpj else None

    def nome(self, cnpj: str | None) -> str:
        if not cnpj:
            return ""
        classe = self._classes.get(cnpj)
        if classe and classe.nome:
            return classe.nome
        carteira = self._carteiras.get(cnpj)
        return carteira.nome if carteira else ""

    def classes_de(self, cnpj: str | None) -> list[str]:
        """As classes que um CNPJ designa: a propria, ou as do envelope."""
        cnpj = normalize_cnpj(cnpj)
        if not cnpj:
            return []
        if cnpj in self._classes or cnpj in self._carteiras:
            return [cnpj]
        return list(self._envelopes.get(cnpj, []))

    def classes_do_codigo(self, codigo: object) -> list[str]:
        normalizado = normalize_cvm_code(codigo)
        return list(self._codigos.get(normalizado, [])) if normalizado else []

    def resolver(self, cnpj: str | None, codigo: object = None) -> list[str]:
        """CNPJ de classe, CNPJ de fundo ou codigo CVM -> as classes designadas.

        A mesma ordem do enriquecimento: CNPJ primeiro, codigo como rede. E o
        resolvedor da lista distribuivel.
        """
        classes = self.classes_de(cnpj) if cnpj else []
        if not classes and codigo is not None:
            classes = self.classes_do_codigo(codigo)
        return classes

    def _canonico(self, cnpj: str) -> str:
        """A classe que o CNPJ designa; envelope de varias classes fica como esta."""
        classes = self.classes_de(cnpj)
        return classes[0] if len(classes) == 1 else cnpj

    def _classe_principal(self, cnpj: str) -> str:
        """Para envelope de varias classes, a de maior PL - a que um gap anuncia."""
        classes = self.classes_de(cnpj)
        if not classes:
            return normalize_cnpj(cnpj) or cnpj
        return min(classes, key=lambda c: (-(self._pl(c) or 0.0), c))

    def tem_carteira(self, cnpj: str | None) -> bool:
        return bool(cnpj) and self._canonico(cnpj) in self._carteiras

    def competencia_de(self, cnpj: str | None) -> str | None:
        carteira = self._carteiras.get(self._canonico(cnpj)) if cnpj else None
        return carteira.competencia if carteira else None

    def _pl(self, cnpj: str) -> float | None:
        classe = self._classes.get(cnpj)
        if classe and classe.pl:
            return classe.pl
        carteira = self._carteiras.get(cnpj)
        return carteira.pl if carteira else None

    def _dmais(self, cnpj: str) -> int | None:
        classe = self._classes.get(cnpj)
        return classe.dmais if classe else None

    def carteira(self, cnpj: str | None) -> CarteiraClasse | None:
        """A carteira mais recente da classe; a competencia fica nela."""
        return self._carteiras.get(self._canonico(cnpj)) if cnpj else None

    def investidores_de(self, cnpj: str | None) -> dict[str, float]:
        """Quem tem cotas da classe, e quanto: CNPJ investidor -> R$.

        E a leitura da carteira pelo lado de quem e investido - a que diz se a
        classe e master. O monitor da industria usa o perfil mensal como fonte
        principal (a carteira nao ve clubes, sigilo nem os fundos de
        previdencia) e isto como conferencia.
        """
        if not cnpj:
            return {}
        if self._investidores is None:
            indice: dict[str, dict[str, float]] = {}
            for investidor, carteira in self._carteiras.items():
                for alvo, valor in carteira.cotas.items():
                    indice.setdefault(self._canonico(alvo), {})[investidor] = valor
            self._investidores = indice
        return self._investidores.get(self._canonico(cnpj), {})

    # --- espelho e master -----------------------------------------------------

    def _investido_dominante(self, cnpj: str) -> str | None:
        """A classe onde esta ao menos LIMIAR_ESPELHO do PL, na competencia mais
        recente que a mostra. Medido em 26/09/2026: 8 dos gaps sem ligacao em
        08/2026 a mostravam em 06 ou 07."""
        for carteira in self._historico.get(cnpj, ()):
            if not carteira.pl or carteira.pl <= 0 or not carteira.cotas:
                continue
            alvo, valor = min(carteira.cotas.items(), key=lambda kv: (-kv[1], kv[0]))
            if valor / carteira.pl >= LIMIAR_ESPELHO:
                return self._canonico(alvo)
        return None

    def master_de(self, cnpj: str) -> str:
        """O fim da cadeia de espelhos. Classe que nao e espelho e master de si."""
        inicio = self._canonico(cnpj)
        if inicio in self._master:
            return self._master[inicio]
        cadeia = [inicio]
        while len(cadeia) <= PROFUNDIDADE_MAXIMA:
            proximo = self._investido_dominante(cadeia[-1])
            if proximo is None or proximo in cadeia:
                break
            cadeia.append(proximo)
        self._master[inicio] = cadeia[-1]
        return cadeia[-1]

    # --- perfil de carteira ---------------------------------------------------

    def perfil(self, cnpj: str) -> dict[str, float] | None:
        """Fracao da carteira por faixa, com look-through das cotas de fundos."""
        return self._vetor(self._canonico(cnpj), ())

    def _vetor(self, cnpj: str, pilha: tuple[str, ...]) -> dict[str, float] | None:
        if cnpj in self._perfil:
            return self._perfil[cnpj]
        carteira = self._carteiras.get(cnpj)
        if carteira is None:
            return None
        valores = dict(carteira.faixas)
        for alvo, valor in sorted(carteira.cotas.items()):
            alvo_c = self._canonico(alvo)
            sub = None
            if len(pilha) < PROFUNDIDADE_MAXIMA and alvo_c not in pilha and alvo_c != cnpj:
                sub = self._vetor(alvo_c, pilha + (cnpj,))
            if sub:
                for faixa, fracao in sub.items():
                    valores[faixa] = valores.get(faixa, 0.0) + valor * fracao
            else:
                faixa = carteira.faixa_das_cotas.get(alvo, COTAS_NAO_RESOLVIDAS)
                valores[faixa] = valores.get(faixa, 0.0) + valor
        total = sum(valores.values())
        vetor = (
            {f: v / total for f, v in valores.items() if v > 0} if total > 0 else None
        )
        # Memoriza sempre. Dentro de um ciclo (raro: A investe em B que investe
        # em A) o resultado depende de por onde se entrou nele, e a entrada e
        # determinada pela ordem de CNPJ da montagem - reprodutivel entre
        # execucoes, que e o que importa para o relatorio.
        self._perfil[cnpj] = vetor
        return vetor

    def ativos(self, cnpj: str) -> dict[str, float] | None:
        """Fracao da carteira identificada por ativo, com look-through das cotas.

        Cota de fundo sem carteira conta como um ativo (o proprio fundo): duas
        classes que investem no mesmo fundo sem carteira publicada partilham
        esse ativo.
        """
        return self._vetor_de_ativos(self._canonico(cnpj), ())

    def _vetor_de_ativos(
        self, cnpj: str, pilha: tuple[str, ...]
    ) -> dict[str, float] | None:
        if cnpj in self._ativos:
            return self._ativos[cnpj]
        carteira = self._carteiras.get(cnpj)
        if carteira is None:
            return None
        valores = dict(carteira.ativos)
        for alvo, valor in sorted(carteira.cotas.items()):
            alvo_c = self._canonico(alvo)
            sub = None
            if len(pilha) < PROFUNDIDADE_MAXIMA and alvo_c not in pilha and alvo_c != cnpj:
                sub = self._vetor_de_ativos(alvo_c, pilha + (cnpj,))
            if sub:
                for ativo, fracao in sub.items():
                    valores[ativo] = valores.get(ativo, 0.0) + valor * fracao
            else:
                chave = f"cota:{alvo_c}"
                valores[chave] = valores.get(chave, 0.0) + valor
        total = sum(valores.values())
        vetor = (
            {a: v / total for a, v in valores.items() if v > 0} if total > 0 else None
        )
        self._ativos[cnpj] = vetor
        return vetor

    def perfil_determinado(self, cnpj: str) -> dict[str, float] | None:
        """O perfil, ou None quando o sigilo e as cotas sem carteira passam do limite."""
        vetor = self.perfil(cnpj)
        if not vetor:
            return None
        opaco = vetor.get(SOB_SIGILO, 0.0) + vetor.get(COTAS_NAO_RESOLVIDAS, 0.0)
        # O sigilo pode esconder so a identidade (a CVM publica o tipo da
        # posicao): o perfil por faixa sai, mas nao ha ativo para comparar.
        ativos = self.ativos(cnpj) or {}
        sem_nome = sum(ativos.get(chave, 0.0) for chave in ATIVOS_OPACOS)
        return None if max(opaco, sem_nome) > LIMIAR_SIGILO else vetor

    # --- familias -------------------------------------------------------------

    def _gestora_da_versao(self, master: str, membros: list[str]) -> Classe | None:
        classe = self._classes.get(master)
        if classe and classe.raiz_gestor:
            return classe
        candidatas = [self._classes[m] for m in membros if m in self._classes]
        candidatas = [c for c in candidatas if c.raiz_gestor]
        if not candidatas:
            return None
        return min(candidatas, key=lambda c: (-(c.pl or 0.0), c.cnpj))

    def _construir_familias(self) -> None:
        por_master: dict[str, list[str]] = {}
        for cnpj in sorted(self._classes):
            if not self._classes[cnpj].ativa:
                continue
            por_master.setdefault(self.master_de(cnpj), []).append(cnpj)

        versoes: list[tuple[Versao, Classe | None]] = []
        for master, membros in por_master.items():
            referencia = self._gestora_da_versao(master, membros)
            # O master entra mesmo fora do cadastro ativo (o nome vem da
            # carteira): e ele que diz qual carteira a versao entrega.
            lista = sorted(
                {master, *membros},
                key=lambda c: (c != master, -(self._pl(c) or 0.0), c),
            )
            versao = Versao(
                master=master,
                nome=self.nome(master),
                pl=self._pl(master),
                competencia=self._carteiras[master].competencia
                if master in self._carteiras else None,
                membros=[
                    Membro(
                        cnpj=c, nome=self.nome(c),
                        papel="master" if c == master else "espelho",
                        dmais=self._dmais(c), pl=self._pl(c),
                        espelho_declarado=(
                            self._classes[c].espelho_declarado if c in self._classes else None
                        ),
                    )
                    for c in lista
                ],
            )
            versoes.append((versao, referencia))

        # Agrupamento das versoes em estrategias: por gestora e categoria, com
        # ligacao completa sobre a distancia de carteira.
        grupos: dict[tuple[str, str], list[tuple[Versao, Classe]]] = {}
        sozinhas: list[tuple[Versao, Classe | None]] = []
        for versao, referencia in versoes:
            categoria = normalize_text(self._anbima(versao.master, referencia))
            if referencia is None or not categoria:
                sozinhas.append((versao, referencia))
                continue
            grupos.setdefault((referencia.raiz_gestor, categoria), []).append(
                (versao, referencia)
            )

        for chave in sorted(grupos):
            itens = sorted(
                grupos[chave], key=lambda vr: (-(vr[0].pl or 0.0), vr[0].master)
            )
            clusters: list[list[tuple[Versao, Classe]]] = []
            for versao, referencia in itens:
                perfil = self.perfil_determinado(versao.master)
                destino = None
                if perfil is not None:
                    for cluster in clusters:
                        if all(self._compativeis(versao, referencia, perfil, outra, ref)
                               for outra, ref in cluster):
                            destino = cluster
                            break
                if destino is None:
                    clusters.append([(versao, referencia)])
                else:
                    destino.append((versao, referencia))
            for cluster in clusters:
                self._registrar_familia(cluster)
        for versao, referencia in sorted(sozinhas, key=lambda vr: vr[0].master):
            self._registrar_familia([(versao, referencia)])

    def _anbima(self, master: str, referencia: Classe | None) -> str | None:
        classe = self._classes.get(master)
        if classe and classe.anbima:
            return classe.anbima
        return referencia.anbima if referencia else None

    def _indicador(self, master: str, referencia: Classe | None) -> str | None:
        classe = self._classes.get(master)
        if classe and classe.indicador_informativo:
            return classe.indicador_informativo
        return referencia.indicador_informativo if referencia else None

    def _compativeis(
        self,
        versao: Versao,
        referencia: Classe,
        perfil: dict[str, float],
        outra: Versao,
        referencia_outra: Classe,
    ) -> bool:
        outro_perfil = self.perfil_determinado(outra.master)
        if outro_perfil is None:
            return False
        a = self._indicador(versao.master, referencia)
        b = self._indicador(outra.master, referencia_outra)
        if a and b and a != b:
            return False
        if distancia_de_carteira(perfil, outro_perfil) > LIMIAR_DISTANCIA_CARTEIRA:
            return False
        ativos_a, ativos_b = self.ativos(versao.master), self.ativos(outra.master)
        if not ativos_a or not ativos_b:
            return False
        return sobreposicao_de_ativos(ativos_a, ativos_b) >= LIMIAR_SOBREPOSICAO

    def _registrar_familia(self, cluster: list[tuple[Versao, Classe | None]]) -> None:
        principal, referencia = cluster[0]
        familia = Familia(
            id=principal.master,
            nome=principal.nome,
            gestora=referencia.gestor if referencia else None,
            raiz_gestora=referencia.raiz_gestor if referencia else None,
            anbima=self._anbima(principal.master, referencia),
            indicador=self._indicador(principal.master, referencia),
            perfil=self.perfil(principal.master),
            versoes=[v for v, _ in cluster],
        )
        self._familias[familia.id] = familia
        for versao, _ in cluster:
            self._familia_do_master[versao.master] = familia.id

    def familia_de(self, cnpj: str | None) -> Familia | None:
        if not cnpj:
            return None
        identificador = self._familia_do_master.get(self.master_de(cnpj))
        return self._familias.get(identificador) if identificador else None

    def gestoras(self, consulta: str) -> list[tuple[str, str]]:
        """(raiz, nome) das gestoras que casam com o nome ou o CNPJ consultado."""
        digitos = "".join(ch for ch in consulta if ch.isdigit())
        so_digitos = not normalize_text(consulta).replace(" ", "").replace(digitos, "")
        cnpj = normalize_cnpj(consulta) if parece_cnpj(consulta) else None
        alfanumerico = cnpj if cnpj and not cnpj.isdigit() else None
        encontradas: dict[str, str] = {}
        if (so_digitos and len(digitos) in (8, 14)) or alfanumerico:
            raiz = alfanumerico[:8] if alfanumerico else digitos[:8]
            for classe in self._classes.values():
                if classe.raiz_gestor == raiz:
                    encontradas.setdefault(raiz, classe.gestor or raiz)
        else:
            alvo = normalize_text(consulta)
            for classe in self._classes.values():
                if classe.raiz_gestor and alvo and alvo in normalize_text(classe.gestor):
                    encontradas.setdefault(classe.raiz_gestor, classe.gestor or "")
        return sorted(encontradas.items(), key=lambda kv: (kv[1], kv[0]))

    def familias_da_gestora(self, consulta: str) -> list[Familia]:
        """Todas as familias das gestoras que casam com a consulta, maior PL primeiro."""
        raizes = {raiz for raiz, _ in self.gestoras(consulta)}
        familias = [f for f in self._familias.values() if f.raiz_gestora in raizes]
        return sorted(familias, key=lambda f: (f.gestora or "", -f.pl, f.id))

    # --- cobertura ------------------------------------------------------------

    def preparar_base(self, cnpjs: Iterable[str]) -> BaseIndexada:
        """Indexa uma base (vitrine ou lista distribuivel) para `cobertura`."""
        classes: set[str] = set()
        for cnpj in cnpjs:
            normalizado = normalize_cnpj(cnpj)
            if normalizado:
                # CNPJ fora do cadastro continua na base: cobre a si mesmo.
                classes.update(self.classes_de(normalizado) or [normalizado])
        por_master: dict[str, list[str]] = {}
        por_familia: dict[str, list[str]] = {}
        por_raiz: dict[str, list[str]] = {}
        raizes: set[str] = set()
        for cnpj in sorted(classes):
            master = self.master_de(cnpj)
            por_master.setdefault(master, []).append(cnpj)
            familia = self._familia_do_master.get(master)
            if familia:
                por_familia.setdefault(familia, []).append(cnpj)
            for dona in (self._classes.get(cnpj), self._classes.get(master)):
                if dona and dona.raiz_gestor:
                    raizes.add(dona.raiz_gestor)
            referencia = self._referencia_da_estrategia(cnpj, master)
            if referencia is not None:
                por_raiz.setdefault(referencia.raiz_gestor, []).append(cnpj)
        return BaseIndexada(
            frozenset(classes), por_master, por_familia, frozenset(raizes), por_raiz
        )

    def _referencia_da_estrategia(self, gap: str, master: str) -> Classe | None:
        """A classe que diz de quem e a estrategia: o master, senao o proprio gap."""
        for cnpj in (master, gap):
            classe = self._classes.get(cnpj)
            if classe and classe.raiz_gestor:
                return classe
        return None

    def cobertura(self, cnpj: str, base: BaseIndexada | Iterable[str]) -> Veredito:
        """O veredito de um gap aparente contra uma base de cobertura."""
        if not isinstance(base, BaseIndexada):
            base = self.preparar_base(base)
        gap = self._classe_principal(cnpj)
        master = self.master_de(gap)
        familia = self.familia_de(gap)
        referencia = self._referencia_da_estrategia(gap, master)
        dmais = self._dmais(gap)
        comum = {
            "master": master,
            "nome_master": self.nome(master),
            "dmais": dmais,
            "competencia": self.competencia_de(master) or self.competencia_de(gap),
            "gestora": referencia.gestor if referencia else None,
            "raiz_gestora": referencia.raiz_gestor if referencia else None,
            "familia_id": familia.id if familia else None,
            "familia_nome": familia.nome if familia else None,
            "perfil_resumo": resumo_do_perfil(self.perfil(master)),
        }

        if gap in base.classes:
            return Veredito(
                tipo=PRESENTE, confianca=ALTA, cobre=gap, nome_cobre=self.nome(gap),
                ligacao="mesma classe", master_cobre=master, dmais_cobre=dmais, **comum,
            )

        espelhos = [c for c in base.por_master.get(master, []) if c != gap]
        equivalentes = []
        if familia is not None:
            equivalentes = [
                c for c in base.por_familia.get(familia.id, [])
                if self.master_de(c) != master
            ]
        faixa = faixa_de_liquidez(dmais)

        def mesma_faixa(c: str) -> bool:
            outra = faixa_de_liquidez(self._dmais(c))
            return faixa is None or outra is None or outra == faixa

        def ordem(c: str) -> tuple:
            outro = self._dmais(c)
            distancia = abs(outro - dmais) if outro is not None and dmais is not None else 0
            return (distancia, -(self._pl(c) or 0.0), c)

        def coberto_por(c: str, tipo: str, ligacao: str) -> Veredito:
            master_cobre = self.master_de(c)
            perfil_a = self.perfil(master)
            perfil_b = self.perfil(master_cobre)
            ativos_a = self.ativos(master)
            ativos_b = self.ativos(master_cobre)
            equivalente = ligacao == "equivalente"
            distancia = (
                distancia_de_carteira(perfil_a, perfil_b)
                if equivalente and perfil_a and perfil_b else None
            )
            sobreposicao = (
                sobreposicao_de_ativos(ativos_a, ativos_b)
                if equivalente and ativos_a and ativos_b else None
            )
            confianca = ALTA if ligacao == "espelho" else MEDIA
            return Veredito(
                tipo=tipo, confianca=confianca, cobre=c, nome_cobre=self.nome(c),
                ligacao=ligacao, master_cobre=master_cobre, distancia=distancia,
                sobreposicao=sobreposicao, dmais_cobre=self._dmais(c), **comum,
            )

        for grupo, tipo, ligacao in (
            (espelhos, COBERTO_ESPELHO, "espelho"),
            (equivalentes, COBERTO_EQUIVALENTE, "equivalente"),
        ):
            na_faixa = sorted((c for c in grupo if mesma_faixa(c)), key=ordem)
            if na_faixa:
                return coberto_por(na_faixa[0], tipo, ligacao)
        for grupo, ligacao in ((espelhos, "espelho"), (equivalentes, "equivalente")):
            if grupo:
                return coberto_por(sorted(grupo, key=ordem)[0], OUTRA_LIQUIDEZ, ligacao)

        # Sem cobertura: de quem e a estrategia decide entre gap de gestora e de
        # estrategia; sem carteira que decida, o dado declarado so sugere.
        raiz = referencia.raiz_gestor if referencia else None
        if raiz is None:
            return Veredito(tipo=SEM_DADO, **comum)
        if raiz not in base.raizes:
            return Veredito(tipo=GAP_GESTORA, **comum)
        # A mesma estrategia em outra versao, que a carteira nao ligou: a XP ja
        # a tem (pedido de 15/10/2026).
        correlatos = sorted(self._correlatos(gap, master, referencia, base), key=ordem)
        if correlatos:
            cobre = correlatos[0]
            return Veredito(
                tipo=COBERTO_CORRELATO, confianca=MEDIA, cobre=cobre,
                nome_cobre=self.nome(cobre), ligacao="correlato",
                master_cobre=self.master_de(cobre), dmais_cobre=self._dmais(cobre),
                **comum,
            )
        declarados = self._equivalentes_declarados(master, referencia, base)
        if not declarados:
            return Veredito(tipo=GAP_ESTRATEGIA, **comum)
        decidivel = self.perfil_determinado(master) is not None and all(
            self.perfil_determinado(self.master_de(c)) is not None for c in declarados
        )
        if decidivel:
            return Veredito(tipo=GAP_ESTRATEGIA, **comum)
        possivel = sorted(declarados, key=ordem)[0]
        return Veredito(
            tipo=SEM_DADO, confianca=BAIXA, possivel_equivalente=possivel,
            nome_possivel_equivalente=self.nome(possivel), **comum,
        )

    def _correlatos(
        self, gap: str, master: str, referencia: Classe, base: BaseIndexada
    ) -> list[str]:
        """Classes da base da mesma gestora com o mesmo nome de estrategia.

        O nome e o de `palavras_da_estrategia`, sem a marca da gestora. Quando
        dos dois lados ele e so marca e forma juridica ("TB SELEÇÃO FIF CIC EM
        AÇÕES" e "TB ADVISORY FIC DE FIA"), decide a mesma categoria ANBIMA.
        """
        candidatos = base.por_raiz.get(referencia.raiz_gestor) or []
        if not candidatos:
            return []
        marca = palavras_do_nome(referencia.gestor)
        alvo = palavras_da_estrategia(self.nome(gap) or self.nome(master), marca)
        categoria = normalize_text(self._anbima(master, referencia))
        achados = []
        for cnpj in candidatos:
            outro_master = self.master_de(cnpj)
            palavras = palavras_da_estrategia(self.nome(cnpj) or self.nome(outro_master), marca)
            if alvo or palavras:
                if alvo == palavras:
                    achados.append(cnpj)
                continue
            outra = self._referencia_da_estrategia(cnpj, outro_master)
            if categoria and normalize_text(self._anbima(outro_master, outra)) == categoria:
                achados.append(cnpj)
        return achados

    def _equivalentes_declarados(
        self, master: str, referencia: Classe, base: BaseIndexada
    ) -> list[str]:
        """Classes da base da mesma gestora e categoria, com indicador compativel."""
        categoria = normalize_text(self._anbima(master, referencia))
        if not categoria:
            return []
        indicador = self._indicador(master, referencia)
        encontrados = []
        for cnpj in sorted(base.classes):
            outro_master = self.master_de(cnpj)
            outra = self._referencia_da_estrategia(cnpj, outro_master)
            if outra is None or outra.raiz_gestor != referencia.raiz_gestor:
                continue
            if normalize_text(self._anbima(outro_master, outra)) != categoria:
                continue
            outro_indicador = self._indicador(outro_master, outra)
            if indicador and outro_indicador and indicador != outro_indicador:
                continue
            encontrados.append(cnpj)
        return encontrados
