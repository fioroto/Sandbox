"""O que a previdencia muda no enriquecimento: eixo, particao e resolvedor.

Tres adaptacoes, cada uma para um erro medido em 2026-09-22 que o pipeline de
fundos cometeria sobre uma vitrine de previdencia.

1. O eixo grosso colapsava
--------------------------

As 4.609 classes com rotulo ANBIMA de previdencia tem um unico nivel 1:
PREVIDENCIA. O eixo grosso do relatorio competitivo e exatamente esse nivel 1
(`competitive.coarse_category`), entao toda casa aparecia com 100% num balde so
e indice 1,00 - nenhuma aposta, nenhum gap por categoria.

A taxonomia ja traz a resposta dentro do rotulo: tirado o prefixo, o que sobra
e a familia do ativo. Medido sobre as 4.609 classes: MULTIMERCADO 2.676, RF
1.412, ACOES 306, BALANCEADOS 215. Os 23 rotulos finos continuam distintos.

O rotulo completo NAO e reescrito. `categoria_oficial` segue "Previdencia
Multimercado Livre", porque o custo busca a mediana do mercado por essa chave
(`custo.IndiceCusto`) - e comparar um fundo de previdencia com os pares de
previdencia e o certo; com o prefixo cortado ele buscaria "Multimercado Livre",
que nao existe no cadastro. So o eixo grosso muda, por uma chave propria
(`KEY_CATEGORIA_GROSSA`) que `coarse_category` le quando existe.

2. A particao varejo/restrito esvaziava a vitrine
-------------------------------------------------

Todo FIE e registrado como exclusivo: o cotista unico e a seguradora, e o
cliente compra o PLANO, nao cotas do fundo. So com o carimbo da CVM, a regra de
varejo do modulo de fundos (`profile.restriction_reason`) marcava como restrito
226 de 226 fundos da vitrine do BTG e 219 de 223 da XP - o relatorio sairia
vazio. O mesmo vale para o publico-alvo "profissional" do veiculo.

Aqui essas duas marcas descrevem o veiculo, nao o acesso do cliente. Elas saem
das chaves que a regra le e ficam guardadas em chaves proprias
(`exclusivo_veiculo_cvm`, `publico_veiculo_cvm`), para quem quiser inspecionar.
Restricao de verdade na previdencia e a do plano - publico profissional
declarado pela casa, ou "exclusivo"/"restrito" no nome - e essas continuam
valendo.

3. O resolvedor por nome errava de familia
------------------------------------------

Para quem nao publica CNPJ (o Itau Private), a categoria vem do nome. O
resolvedor do modulo de fundos aceita candidato de qualquer familia, e sobre
nomes de previdencia ele escolhia uma classe de VAREJO em 20% dos nomes da XP
e 18% dos do Itau - "Itau Flexprev Platinum III Renda Fixa" saia como
"Multimercados Invest. no Exterior". Quando os candidatos discordavam, o nivel 1
de consolo era sempre "Previdencia", que nao diz nada.

`IndicePrevidencia` restringe os candidatos a classes previdenciarias e decide
os empates pela familia sem o prefixo. A acuracia dele, medida contra o
gabarito do BTG (que publica CNPJ, entao a categoria verdadeira e conhecida),
esta em `IndicePrevidencia`.
"""
from __future__ import annotations

from collections import Counter

from shelf_monitor.classify import (
    CATEGORIA_ESTRUTURADO,
    KEY_CATEGORIA,
    ClassificationIndex,
    Resolucao,
    eh_estruturado,
    label_nivel1,
)
from shelf_monitor.enrich import KEY_EXCLUSIVO, KEY_PUBLICO
from shelf_monitor.models import AssetClass, FundOffer, ShelfSnapshot
from shelf_monitor.normalize import classify_asset_class, name_slug, normalize_text
from shelf_monitor.report.competitive import KEY_CATEGORIA_GROSSA

# Nome de registro do fundo, quando a vitrine o publica ao lado do comercial (o
# catalogo do Itau lista o mesmo veiculo com os dois nomes). Casa melhor com a
# denominacao da CVM que o nome comercial curto.
KEY_NOME_REGISTRO = "nome_registro"

# Seguradora que emite o plano em que o fundo e vendido, como a casa a publica:
# XP pelos `plans[].insurer` (varias, separadas por ", "), BTG pelo
# `pension.partner`. O Itau Private nao publica.
KEY_SEGURADORA = "seguradora"

# Para onde vao as marcas de VEICULO que a regra de varejo nao deve ler aqui.
KEY_EXCLUSIVO_VEICULO = "exclusivo_veiculo_cvm"
KEY_PUBLICO_VEICULO = "publico_veiculo_cvm"

_PREFIXO = "PREVIDENCIA"

# Abreviacoes que a ANBIMA usa so nos rotulos de previdencia. "RF" nao esta nos
# prefixos de `classify.nivel1` - sem expandir, o balde sairia "RF" cru, ao lado
# do "Renda Fixa" dos fundos FIE que o cadastro rotula sem o prefixo.
_EXPANSOES = {"RF": "Renda Fixa"}

# Rotulo legivel dos niveis que `classify._NIVEL_LABEL` nao conhece.
_ROTULOS_EXTRA = {"BALANCEADOS": "Balanceados"}


def eh_rotulo_previdencia(categoria: str | None) -> bool:
    primeira = (categoria or "").split(None, 1)
    return bool(primeira) and normalize_text(primeira[0]) == _PREFIXO


def sem_prefixo_previdencia(categoria: str | None) -> str | None:
    """'Previdencia RF Duracao Livre ...' -> 'Renda Fixa Duracao Livre ...'.

    Corta a primeira palavra em vez de um indice fixo: `normalize_text` pode
    mexer em espaco e pontuacao, e um indice calculado no texto normalizado
    nao vale no original.
    """
    if not eh_rotulo_previdencia(categoria):
        return categoria
    partes = categoria.split(None, 1)
    if len(partes) < 2:
        return categoria
    resto = partes[1].strip()
    cabeca, _, cauda = resto.partition(" ")
    expandida = _EXPANSOES.get(cabeca.upper())
    if expandida:
        resto = f"{expandida} {cauda}".strip()
    return resto


def rotulo_grosso(categoria: str | None) -> str | None:
    """Rotulo do eixo grosso: o nivel 1 da familia do ativo, sem o prefixo."""
    if not categoria:
        return None
    rotulo = label_nivel1(sem_prefixo_previdencia(categoria))
    return _ROTULOS_EXTRA.get(rotulo, rotulo)


# --- o passo pos-enriquecimento ----------------------------------------------


def ajustar_previdencia(snapshots: list[ShelfSnapshot]) -> dict[str, int]:
    """Aplica as adaptacoes 1 e 2 (e a classe de ativo) aos fundos enriquecidos.

    Roda depois do `enrich_snapshots`, que e quem carimba categoria e marcas da
    CVM, e antes de qualquer bloco do relatorio - e o gancho `pos_enriquecimento`
    de `build_report`. Devolve quantos fundos cada passo tocou.
    """
    contagem = Counter()
    for snapshot in snapshots:
        for oferta in snapshot.funds:
            if _neutralizar_marcas_de_veiculo(oferta):
                contagem["marcas_de_veiculo_neutralizadas"] += 1
            categoria = oferta.extra.get(KEY_CATEGORIA)
            if not categoria or categoria == CATEGORIA_ESTRUTURADO:
                continue
            # Todo fundo categorizado, nao so os de rotulo prefixado: o nivel 1
            # que o resolvedor devolve num empate ("Balanceados") precisa sair
            # com a mesma grafia do rotulo prefixado que cai na mesma familia.
            oferta.extra[KEY_CATEGORIA_GROSSA] = rotulo_grosso(categoria)
            contagem["eixo_grosso_marcado"] += 1
            # A classe de ativo segue a categoria oficial sem o prefixo: com ele,
            # `classify_asset_class` devolve PREVIDENCIA para todo fundo e o
            # grafico por classe vira uma barra so.
            classe = classify_asset_class(sem_prefixo_previdencia(categoria))
            if classe != AssetClass.OUTROS and classe != oferta.asset_class:
                oferta.asset_class = classe
                contagem["classe_de_ativo_revista"] += 1
    return dict(contagem)


def _neutralizar_marcas_de_veiculo(oferta: FundOffer) -> bool:
    """Move exclusivo/publico do registro para as chaves de veiculo."""
    moveu = False
    for origem, destino in (
        (KEY_EXCLUSIVO, KEY_EXCLUSIVO_VEICULO),
        (KEY_PUBLICO, KEY_PUBLICO_VEICULO),
    ):
        if origem in oferta.extra:
            oferta.extra[destino] = oferta.extra.pop(origem)
            moveu = True
    return moveu


# --- o resolvedor por nome ----------------------------------------------------

# Palavras do nome comercial que declaram a familia, para o desempate. Lidas do
# nome NORMALIZADO, nao do slug: `name_slug` descarta justamente RF, MM, FIM,
# FIA, ACOES e RENDA FIXA como tokens genericos. Palavra inteira, nao
# substring - "RF" casaria dentro de outras palavras.
_FAMILIAS_NO_NOME = (
    ("Renda Fixa", frozenset({"RF", "FIRF", "FICFIRF"}), ("RENDA FIXA",)),
    ("Multimercados", frozenset({"MM", "FIM", "FICFIM", "MULTIMERCADO", "MULTI"}), ()),
    ("Ações", frozenset({"ACOES", "FIA", "FICFIA", "IBOV"}), ()),
    ("Balanceados", frozenset({"BALANCEADO", "BALANCEADOS"}), ()),
)


def familias_declaradas(nome: str) -> list[str]:
    """Familias que o nome comercial declara ('Private Prev RF' -> Renda Fixa)."""
    texto = normalize_text(nome)
    palavras = set(texto.split())
    return [
        familia
        for familia, tokens, frases in _FAMILIAS_NO_NOME
        if palavras & tokens or any(frase in texto for frase in frases)
    ]


class IndicePrevidencia(ClassificationIndex):
    """Resolvedor por nome que so aceita classe previdenciaria.

    Mesmos candidatos e mesma pontuacao do modulo de fundos (`_candidatos`,
    `_pontuar`); mudam o filtro e o desempate.

    Validado contra o gabarito do BTG em 2026-09-22: os 226 fundos da vitrine
    dele tem CNPJ, entao a categoria verdadeira de cada um e conhecida, e o nome
    comercial foi resolvido como se o CNPJ nao existisse.

        resolvedor         categoria exata   eixo grosso   familia de varejo
        de fundos               75%              89%            36 fundos
        de previdencia          88%              94%             1 fundo

    A categoria exata nao chega a 100% pelo motivo que o docstring de
    `shelf_monitor.classify` ja mede: o nome comercial cai numa classe irma da
    mesma familia. A familia, que e o que o eixo grosso usa, acerta 94%.
    """

    def resolve(self, offer: FundOffer) -> Resolucao | None:
        if eh_estruturado(offer.name):
            return Resolucao(CATEGORIA_ESTRUTURADO, "estruturado", 1)
        for nome in _nomes_para_casar(offer):
            resolucao = self._resolver_nome(offer, nome)
            if resolucao is not None:
                return resolucao
        return None

    def _resolver_nome(self, offer: FundOffer, nome: str) -> Resolucao | None:
        slug = name_slug(nome)
        tokens = set(slug.split("-")) if slug else set()
        if not tokens:
            return None

        pontuados = []
        for indice in self._candidatos(slug, tokens):
            classe = self._classes[indice]
            if not classe.previdenciaria:
                continue
            pontuacao = self._pontuar(offer, slug, tokens, classe)
            if pontuacao is not None:
                pontuados.append((pontuacao, classe))
        if not pontuados:
            return None

        melhor = max(p for p, _ in pontuados)
        topo = [c for p, c in pontuados if p >= melhor - 1e-9]

        resolucao = _decidir(topo, "nome")
        if resolucao:
            return resolucao

        # Desempate: a familia que o proprio nome declara, se for uma so.
        familias = familias_declaradas(nome)
        if len(familias) == 1:
            reduzido = [c for c in topo if rotulo_grosso(c.categoria) == familias[0]]
            if reduzido:
                resolucao = _decidir(reduzido, "nome-rotulo")
                if resolucao:
                    return resolucao

        modal = Counter(c.categoria for c in topo).most_common()
        if len(modal) > 1 and modal[0][1] > modal[1][1]:
            return Resolucao(modal[0][0], "nome-maioria", len(topo))
        return None


def _nomes_para_casar(offer: FundOffer) -> list[str]:
    """Nome de registro primeiro (mais proximo da CVM), depois o comercial."""
    nomes = [offer.extra.get(KEY_NOME_REGISTRO), offer.name]
    vistos: list[str] = []
    for nome in nomes:
        if nome and nome not in vistos:
            vistos.append(nome)
    return vistos


def _decidir(grupo, via: str) -> Resolucao | None:
    """Empate resolvido se os candidatos concordam - na categoria ou na familia."""
    categorias = {c.categoria for c in grupo}
    if len(categorias) == 1:
        return Resolucao(grupo[0].categoria, via, len(grupo))
    familias = {rotulo_grosso(c) for c in categorias}
    if len(familias) == 1:
        return Resolucao(familias.pop(), f"{via}-n1", len(grupo))
    return None
