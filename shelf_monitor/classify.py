"""Classificacao oficial para os fundos que a vitrine nao identifica.

O `enrich` casa a prateleira com o cadastro da CVM por identidade: CNPJ da
classe, CNPJ do fundo ou codigo CVM. Quando nenhuma das tres existe, o fundo
fica sem classificacao - e sem classificacao ele sai de toda comparacao entre
casas, porque o eixo comparavel e a categoria ANBIMA.

Medido em 2026-09-20 nas vitrines coletadas: 432 dos 459 fundos do Itau (94,1%)
nao publicam CNPJ nem codigo CVM. A tabela de rentabilidade dele traz nome,
risco, aplicacao, taxa e resgate, e nada mais. Sem este modulo o Itau entra em
qualquer leitura competitiva com 3,3% da prateleira classificada, que nao e uma
leitura de prateleira: e a fracao dela que o monitor consegue casar.

O caminho pelo XHR esta fechado, nao pendente. O README registrava o parser do
gateway do Itau como "pendente de evidencia parseavel". A evidencia capturada
em `data/cache/debug/itau/` diz outra coisa: os 51 payloads sao respostas
`400 Bad Request` de `apicd.cloud.itau.com.br/charon/...` e os 51 "invalidos"
sao desafios cifrados do mesmo `charon`. Nenhum traz dado de fundo, e nenhum
traz CNPJ. E anti-bot, nao formato desconhecido.

A virada que resolve
--------------------

Para classificar nao e preciso identificar. Se todos os candidatos oficiais
plausiveis concordam na classificacao, a classificacao esta resolvida mesmo com
o CNPJ em aberto - e o monitor nao precisa afirmar identidade que nao tem.
Master e feeder da mesma estrategia quase sempre partilham a classe; no
universo FIF, apenas 0,9% dos slugs tem classificacao ambigua entre as classes
que os partilham.

Por isso nenhuma via deste modulo preenche CNPJ. Elas carimbam categoria e
declaram por onde passaram, em `KEY_VIA`.

Quatro sinais, nao um
---------------------

A tabela do Itau publica, em 100% dos fundos sem CNPJ, taxa de administracao,
aplicacao minima e prazo de pagamento de resgate. A base oficial tem os tres
por classe (preenchidos em 81,6%). Usar os numeros junto com o nome e o que
separa homonimos: `absolute-alpha-global` sozinho casa com `-financeiro`,
`-master-multi` e `-advisory`, e so a taxa e o minimo dizem qual deles a
vitrine vende.

Calibracoes medidas, a nao desfazer
-----------------------------------

- Prefixo, nao exato. O nome comercial e prefixo do nome de registro
  (`absolute-creta-selecao` -> `absolute-creta-selecao-cic`). Match exato
  resolve 11% do Itau; prefixo com consenso resolve 64%.
- Cortar token generico demais piora. Acrescentar `master`, `prev`, `selecao` e
  `cic` ao descarte do slug levou o Itau de 64% para 52%: colapsa fundos
  distintos no mesmo slug e multiplica conflitos.
- Contencao de tokens e similaridade de sequencia pegam coisas diferentes.
  Sozinhas dao 86,8% e 91%; o maximo das duas, com os sinais numericos, passa
  de 99%.
- Candidato de previdencia tem de sair quando o fundo da vitrine nao e de
  previdencia. Os ultimos conflitos irredutiveis eram versoes PGBL/VGBL do
  mesmo nome disputando com a versao de varejo - e uma vitrine aberta nao vende
  a versao de previdencia. Foi essa guarda que fechou o Itau em 100%.

Cobertura resultante, vitrines de 2026-09-20: Itau 100,00%, XP 99,40%,
BTG 99,24%, Inter 94,21% (o Inter sobe com a base reimportada - ele publica
CNPJ em 99,4% da vitrine, mas 21% desses CNPJs nao existem na base de
2026-08-29). O Nu fica fora por outro motivo: nao tem vitrine deslogada.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from shelf_monitor.models import AssetClass, FundOffer
from shelf_monitor.normalize import (
    name_slug,
    normalize_cnpj,
    normalize_text,
    parse_number,
)
from shelf_monitor.mercado_csv import categoria_da_linha

# Chaves gravadas em FundOffer.extra pelas vias deste modulo.
KEY_CATEGORIA = "categoria_oficial"
KEY_VIA = "categoria_via"

# Piso de semelhanca para um registro oficial ser sequer considerado candidato.
# Abaixo disto o par deixa de ser "o mesmo fundo escrito de outro jeito" e vira
# coincidencia de marca.
_MIN_SEMELHANCA = 0.62

# Quanto cada numero que bate (taxa, minimo, prazo) vale na pontuacao. Tres
# acertos somam 0,18 - o bastante para desempatar nomes igualmente parecidos,
# pouco para promover um nome distante.
_PESO_SINAL = 0.06

# Classe exclusiva nao esta na vitrine de varejo de ninguem. Penalidade em vez
# de exclusao: quando ela e o unico candidato, ainda classifica.
_PENALIDADE_EXCLUSIVO = 0.04

# Tolerancias de comparacao numerica. A taxa vem com casas decimais variaveis
# entre as fontes; o prazo e inteiro em ambas.
_TOL_TAXA = 0.011
_TOL_VALOR = 0.01
_TOL_DIAS = 0.5

# Quantos tokens mais raros do nome entram na busca invertida. Tres cobrem o
# nome comercial inteiro na maioria dos casos e mantem o conjunto de candidatos
# pequeno o bastante para a comparacao rodar em segundos.
_TOKENS_BUSCA = 3

# Tokens que marcam a versao previdenciaria de um fundo.
_TOKENS_PREVIDENCIA = frozenset({"prev", "previdencia", "fie", "fife", "vgbl", "pgbl"})

# Tipos que nao tem classificacao ANBIMA nem CVM no cadastro - medido: 0% em
# todos eles, para todas as casas. Nao sao lacuna de join: e produto de outra
# natureza, e a prateleira do Itau tem alguns. Balde proprio, declarado.
_TIPOS_ESTRUTURADOS = ("FIDC", "FII", "FIP", "FIAGRO", "FUNCINE", "FICART")
CATEGORIA_ESTRUTURADO = "Estruturado (sem classificacao ANBIMA)"

# Prefixo do rotulo ANBIMA -> nivel 1, na ordem em que sao testados.
#
# Previdencia vem primeiro porque "Previdencia Multimercado Livre" comeca por
# ela - testar Multimercado antes classificaria um fundo de previdencia como
# multimercado comum.
#
# "Fechados de Acoes" (132 classes) e "Fundos de Mono Acao" (65) sao rotulos de
# nivel 3 da ANBIMA cujo nivel 1 e Acoes; sem mapea-los, cada um virava um nivel
# proprio e aparecia no ranking de apostas como categoria que "so uma casa tem".
_PREFIXOS_NIVEL = (
    ("PREVIDENCIA", "PREVIDENCIA"),
    ("RENDA FIXA", "RENDA FIXA"),
    ("MULTIMERCADO", "MULTIMERCADO"),
    ("ACOES", "ACOES"),
    ("FECHADOS DE ACOES", "ACOES"),
    ("FUNDOS DE MONO ACAO", "ACOES"),
    ("FMP", "ACOES"),
    ("CAMBIAL", "CAMBIAL"),
    ("EXTERIOR", "EXTERIOR"),
)

# Rotulo legivel de cada nivel 1. Existe porque `nivel1` devolve o prefixo
# normalizado (maiusculo, sem acento), e publicar isso ao lado de "Renda Fixa
# Simples" mostraria duas grafias da mesma taxonomia na mesma tabela.
_NIVEL_LABEL = {
    "RENDA FIXA": "Renda Fixa",
    "MULTIMERCADO": "Multimercados",
    "ACOES": "Acoes",
    "CAMBIAL": "Cambial",
    "PREVIDENCIA": "Previdencia",
    "EXTERIOR": "Exterior",
}


def label_nivel1(categoria: str | None) -> str:
    """Rotulo legivel do nivel 1 de uma categoria."""
    nivel = nivel1(categoria)
    return _NIVEL_LABEL.get(nivel, nivel)


def so_nivel1(via: str | None) -> bool:
    """A via resolveu apenas o nivel 1, nao a categoria fina.

    Quem so tem nivel 1 pertence ao eixo grosso e NAO ao fino. Deixa-lo no fino
    reintroduz o artefato que o eixo unico veio corrigir: medido em 2026-09-20,
    o Itau aparecia com "RENDA FIXA 22,20x" e "MULTIMERCADO so ela" - baldes
    que so ele usava, porque so nele havia fundo resolvido no nivel grosso.
    """
    return bool(via) and via.endswith("-n1")


# Palavras com que a propria casa declara a categoria no nome comercial. Elas
# nao sobrevivem ao `name_slug` (sao tokens genericos la), entao o desempate le
# o nome normalizado, nao o slug.
_ROTULOS = (
    ("MULTIMERCADO", ("MULTIMERCADO",)),
    ("ACOES", ("ACOES", "ACAO", "EQUITY")),
    ("RENDA FIXA", ("RENDA FIXA", "RF ")),
    ("CAMBIAL", ("CAMBIAL",)),
)

# AssetClass heuristica -> nivel 1 da ANBIMA, para o penultimo desempate.
_CLASSE_PARA_NIVEL = {
    AssetClass.RENDA_FIXA: "RENDA FIXA",
    AssetClass.MULTIMERCADO: "MULTIMERCADO",
    AssetClass.ACOES: "ACOES",
    AssetClass.CAMBIAL: "CAMBIAL",
    AssetClass.PREVIDENCIA: "PREVIDENCIA",
    AssetClass.INTERNACIONAL: "EXTERIOR",
}


def nivel1(categoria: str | None) -> str:
    """Nivel 1 da ANBIMA a partir do rotulo completo."""
    texto = normalize_text(categoria)
    for prefixo, nivel in _PREFIXOS_NIVEL:
        if texto.startswith(prefixo):
            return nivel
    return texto.split(" ")[0] if texto else ""


def eh_estruturado(nome: str | None) -> bool:
    """O nome anuncia um produto que o cadastro nao classifica."""
    texto = f" {normalize_text(nome)} "
    return any(f" {tipo} " in texto for tipo in _TIPOS_ESTRUTURADOS)


@dataclass(frozen=True)
class ClasseOficial:
    """Uma classe do cadastro, nos campos que o casamento usa."""

    cnpj: str | None
    slug: str
    tokens: frozenset[str]
    categoria: str
    taxa_adm: float | None = None
    minimo: float | None = None
    dias_pagamento: float | None = None
    exclusivo: bool = False

    @property
    def previdenciaria(self) -> bool:
        if self.tokens & _TOKENS_PREVIDENCIA:
            return True
        return nivel1(self.categoria) == "PREVIDENCIA"


@dataclass
class Resolucao:
    """A categoria de um fundo e por qual via ela foi obtida."""

    categoria: str
    via: str
    candidatos: int = 1

    def to_dict(self) -> dict:
        return {
            "categoria": self.categoria,
            "via": self.via,
            "candidatos": self.candidatos,
        }


def _bate(a: float | None, b: float | None, tolerancia: float) -> bool:
    return a is not None and b is not None and abs(a - b) < tolerancia


class ClassificationIndex:
    """Indice do universo oficial, para classificar por nome quando falta CNPJ."""

    def __init__(self, classes: list[ClasseOficial]) -> None:
        self._classes = classes
        self._por_token: dict[str, list[int]] = defaultdict(list)
        self._por_inicial: dict[str, list[int]] = defaultdict(list)
        for indice, classe in enumerate(classes):
            for token in classe.tokens:
                self._por_token[token].append(indice)
            self._por_inicial[classe.slug.split("-")[0]].append(indice)

    def __len__(self) -> int:
        return len(self._classes)

    @classmethod
    def load(cls, market_dir: Path) -> ClassificationIndex | None:
        """Le `mercado.csv`. Sem a base em disco nao ha o que indexar."""
        caminho = market_dir / "mercado.csv"
        if not caminho.is_file():
            return None

        classes: list[ClasseOficial] = []
        with caminho.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=";"):
                categoria = categoria_da_linha(row)
                if not categoria:
                    continue
                # Classe cancelada continua no cadastro e nao esta na vitrine de
                # ninguem; mante-la so aumenta a chance de conflito.
                if "CANCELAD" in normalize_text(row.get("situacao")):
                    continue
                slug = name_slug(row.get("denominacao"))
                if not slug:
                    continue
                classes.append(
                    ClasseOficial(
                        cnpj=normalize_cnpj(row.get("cnpj")),
                        slug=slug,
                        tokens=frozenset(slug.split("-")),
                        categoria=categoria,
                        taxa_adm=parse_number(row.get("taxa_adm")),
                        minimo=parse_number(row.get("aplicacao_inicial_minima")),
                        dias_pagamento=parse_number(row.get("dias_pagamento_resgate")),
                        exclusivo=normalize_text(row.get("exclusivo")).startswith("S"),
                    )
                )
        return cls(classes) if classes else None

    # --- casamento ------------------------------------------------------------

    def _candidatos(self, slug: str, tokens: set[str]) -> set[int]:
        """Conjunto de busca: bloco do primeiro token mais os tokens mais raros.

        Os dois caminhos existem porque falham em casos diferentes. O bloco pela
        inicial pega o nome que so acrescenta sufixos (`absolute-creta-selecao`
        -> `absolute-creta-selecao-cic`); os tokens raros pegam o nome cuja
        primeira palavra difere mas cujo miolo e identico.
        """
        raros = sorted(tokens, key=lambda t: len(self._por_token.get(t, ())))
        indices = set(self._por_inicial.get(slug.split("-")[0], ()))
        for token in raros[:_TOKENS_BUSCA]:
            indices.update(self._por_token.get(token, ()))
        return indices

    def _pontuar(
        self, offer: FundOffer, slug: str, tokens: set[str], classe: ClasseOficial
    ) -> float | None:
        contencao = len(tokens & classe.tokens) / len(tokens)
        sequencia = SequenceMatcher(None, slug, classe.slug).ratio()
        if classe.slug.startswith(slug) or slug.startswith(classe.slug):
            # Prefixo e o padrao dominante entre nome comercial e nome de
            # registro; sem este piso ele perde para o nome mais curto.
            sequencia = max(sequencia, 0.93)
        base = max(contencao, sequencia)
        if base < _MIN_SEMELHANCA:
            return None

        sinais = sum(
            (
                _bate(classe.taxa_adm, offer.admin_fee_pct, _TOL_TAXA),
                _bate(classe.minimo, offer.min_initial, _TOL_VALOR),
                _bate(classe.dias_pagamento, offer.redemption_payment_days, _TOL_DIAS),
            )
        )
        penalidade = _PENALIDADE_EXCLUSIVO if classe.exclusivo else 0.0
        return base + sinais * _PESO_SINAL - penalidade

    def _decidir(self, grupo: list[ClasseOficial], via: str) -> Resolucao | None:
        """Resolve um grupo de candidatos empatados, se eles concordarem."""
        categorias = {c.categoria for c in grupo}
        if len(categorias) == 1:
            return Resolucao(grupo[0].categoria, via, len(grupo))
        niveis = {nivel1(c) for c in categorias}
        if len(niveis) == 1:
            return Resolucao(
                label_nivel1(grupo[0].categoria), f"{via}-n1", len(grupo)
            )
        return None

    def resolve(self, offer: FundOffer) -> Resolucao | None:
        """Categoria oficial de um fundo que o join por identidade nao alcancou."""
        if eh_estruturado(offer.name):
            return Resolucao(CATEGORIA_ESTRUTURADO, "estruturado", 1)

        slug = name_slug(offer.name)
        tokens = set(slug.split("-")) if slug else set()
        if not tokens:
            return None

        nome = normalize_text(offer.name)
        previdenciario = bool(tokens & _TOKENS_PREVIDENCIA) or (
            offer.asset_class == AssetClass.PREVIDENCIA
        )

        pontuados: list[tuple[float, ClasseOficial]] = []
        for indice in self._candidatos(slug, tokens):
            classe = self._classes[indice]
            if not previdenciario and classe.previdenciaria:
                continue
            pontuacao = self._pontuar(offer, slug, tokens, classe)
            if pontuacao is not None:
                pontuados.append((pontuacao, classe))
        if not pontuados:
            return None

        melhor = max(p for p, _ in pontuados)
        topo = [c for p, c in pontuados if p >= melhor - 1e-9]

        resolucao = self._decidir(topo, "nome")
        if resolucao:
            return resolucao

        # Desempate 1: a casa declara a categoria no proprio nome comercial.
        for nivel, palavras in _ROTULOS:
            if any(palavra in nome for palavra in palavras):
                reduzido = [c for c in topo if nivel1(c.categoria) == nivel]
                if reduzido:
                    resolucao = self._decidir(reduzido, "nome-rotulo")
                    if resolucao:
                        return resolucao
                break

        # Desempate 2: a classe heuristica do proprio fundo. Mais fraca que o
        # rotulo porque e inferida do nome, nao declarada nele.
        nivel = _CLASSE_PARA_NIVEL.get(offer.asset_class)
        if nivel:
            reduzido = [c for c in topo if nivel1(c.categoria) == nivel]
            if reduzido:
                resolucao = self._decidir(reduzido, "nome-classe")
                if resolucao:
                    return resolucao

        # Ultimo recurso: a categoria modal, e so quando ha maioria de fato.
        # Medido em 2026-09-20 esta via nunca disparou - as anteriores resolvem
        # tudo. Fica como rede, declarada como tal em `via`.
        modal = Counter(c.categoria for c in topo).most_common()
        if len(modal) > 1 and modal[0][1] > modal[1][1]:
            return Resolucao(modal[0][0], "nome-maioria", len(topo))
        return None


@dataclass
class ClassificationStats:
    """Quanto da vitrine de cada casa ficou classificada, e por qual via."""

    por_casa: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    def registrar(self, platform: str, via: str) -> None:
        self.por_casa[platform][via] += 1

    def total(self, platform: str) -> int:
        return sum(self.por_casa.get(platform, Counter()).values())

    def to_dict(self) -> dict:
        return {casa: dict(vias) for casa, vias in self.por_casa.items()}
