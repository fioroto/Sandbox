"""Uniao dos resultados de varias fontes numa prateleira so.

Cada casa e consultada por todas as estrategias disponiveis, nao apenas pela
primeira que responde. Isso muda o que o monitor enxerga em dois sentidos:

- **Cobertura**: a prateleira final e a uniao, entao um fundo que so o site
  publica deixa de ficar invisivel.
- **Divergencia**: quando o mesmo fundo aparece em duas fontes, saber em quais
  ele aparece e um dado por si so. Um fundo no site e ausente do Open Finance
  costuma indicar oferta nova ainda nao refletida no dado regulatorio.

Quando duas fontes discordam de um campo, vence a de maior confianca para dados
estruturados - e o Open Finance, que e padronizado por regulacao, enquanto o
scraping le o que a pagina resolveu exibir.
"""
from __future__ import annotations

from shelf_monitor.cnpj_por_nome import KEY_CNPJ_PENDENTE
from shelf_monitor.models import (
    KEY_MINIMO_VITRINE,
    KEY_TAXA_VITRINE,
    LISTING_SOURCES,
    AssetClass,
    CollectorStrategy,
    FundOffer,
)
from shelf_monitor.taxas import plausivel

# Da mais confiavel para a menos, para efeito de resolucao de conflito.
# A area logada ve a oferta real do cliente, mas o Open Finance e padronizado e
# obrigatorio - para taxa, minimo e prazo, e a fonte com menos interpretacao no
# meio do caminho.
_SOURCE_PRECEDENCE: list[str] = [
    CollectorStrategy.OPEN_FINANCE.value,
    CollectorStrategy.AUTHENTICATED.value,
    CollectorStrategy.API_JSON.value,
    CollectorStrategy.PLAYWRIGHT.value,
    CollectorStrategy.HTML_SCRAPE.value,
]

# Campos escalares resolvidos por precedencia de fonte.
_MERGED_FIELDS = (
    "cnpj", "isin", "cvm_code", "manager_name", "manager_cnpj", "admin_name",
    "raw_category",
    "min_initial", "admin_fee_pct", "performance_fee", "redemption_quotation_days",
    "redemption_payment_days", "grace_days", "taxation", "closed_for_investment",
    "detail_url",
)


def source_rank(source: str) -> int:
    """Posicao na ordem de confianca; fontes desconhecidas vao para o fim."""
    try:
        return _SOURCE_PRECEDENCE.index(source)
    except ValueError:
        return len(_SOURCE_PRECEDENCE)


def _serialize(value: object) -> object:
    """Converte para um tipo seguro para JSON (extra dict)."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def _best_source(offer: FundOffer) -> int:
    if not offer.sources:
        return len(_SOURCE_PRECEDENCE)
    return min(source_rank(s) for s in offer.sources)


def merge_offers(groups: dict[str, list[FundOffer]]) -> list[FundOffer]:
    """Une os fundos de varias estrategias numa lista sem duplicatas.

    `groups` mapeia o nome da estrategia para os fundos que ela trouxe. O fundo
    resultante carrega em `sources` todas as fontes em que apareceu.
    """
    merged: dict[str, FundOffer] = {}

    # Processa da fonte mais confiavel para a menos: assim o primeiro valor a
    # ocupar um campo e o melhor disponivel, e as demais so preenchem lacunas.
    for source in sorted(groups, key=source_rank):
        for offer in groups[source]:
            offer.sources = [source]
            # O minimo da vitrine e guardado a parte ANTES da precedencia agir:
            # no campo, o Open Finance vence e deixa o minimo institucional da
            # classe (ver `FundOffer.minimo_vitrine`). O `_absorb` leva a chave
            # para o fundo unido, porque o Open Finance nunca a escreve.
            if source in LISTING_SOURCES and offer.min_initial is not None:
                offer.extra.setdefault(KEY_MINIMO_VITRINE, offer.min_initial)
            # A taxa da vitrine, pelo mesmo motivo (`FundOffer.taxa_da_vitrine`),
            # ate o teto (`taxas.TETO_PCT`).
            if source in LISTING_SOURCES and plausivel(offer.admin_fee_pct):
                offer.extra.setdefault(KEY_TAXA_VITRINE, offer.admin_fee_pct)
            existing = merged.get(offer.identity)
            if existing is None:
                merged[offer.identity] = offer
                continue
            _absorb(existing, offer, source)

    _reconcile_unidentified(merged)

    for offer in merged.values():
        offer.sources.sort(key=source_rank)
        taxa_da_vitrine_vence(offer)
    return list(merged.values())


def taxa_da_vitrine_vence(offer: FundOffer) -> None:
    """A taxa que o site publica vence a do catalogo (20/10/2026).

    O `maxAdminFee` do Open Finance e, em parte dos fundos, so a taxa de
    administracao (`shelf_monitor.taxas`); a da vitrine e a que o cliente ve. A
    do catalogo fica na divergencia, e o fundo sem taxa na vitrine segue com ela.
    """
    vitrine = offer.extra.get(KEY_TAXA_VITRINE)
    if vitrine is None or vitrine == offer.admin_fee_pct:
        return
    if offer.admin_fee_pct is not None:
        anterior = (offer.extra.get("divergencias") or {}).get("admin_fee_pct") or {}
        offer.extra.setdefault("divergencias", {})["admin_fee_pct"] = {
            "valor_usado": vitrine,
            "fonte_usada": next(
                (s for s in offer.sources if s in LISTING_SOURCES), "vitrine"
            ),
            "valor_alternativo": offer.admin_fee_pct,
            "fonte_alternativa": anterior.get("fonte_usada") or (
                offer.sources[0] if offer.sources else "?"
            ),
        }
    offer.admin_fee_pct = vitrine


# Fuzzy so acima disto, com candidato unico por margem. Abaixo, dois nomes
# parecidos podem ser dois produtos.
_FUZZY_RATIO = 0.95
_FUZZY_MARGIN = 0.04
# Tokens que distinguem veiculos irmaos: se um lado tem e o outro nao, NUNCA
# fundir por semelhanca - master/feeder e espelhos tem nomes quase iguais.
_DISTINGUISHING_TOKENS = {
    "MASTER", "ESPELHO", "MIRROR", "SENIOR", "SUBORDINADA", "JUNIOR",
    "PREV", "PREVIDENCIA", "II", "III", "IV", "V",
}


def _fuzzy_target(slug: str, candidates: dict[str, str]) -> str | None:
    """Slug com CNPJ mais parecido, se for inequivoco e sem token distintivo."""
    import difflib

    scored = sorted(
        (
            (difflib.SequenceMatcher(None, slug, candidate).ratio(), candidate)
            for candidate in candidates
        ),
        reverse=True,
    )
    if not scored or scored[0][0] < _FUZZY_RATIO:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < _FUZZY_MARGIN:
        return None  # dois candidatos plausiveis = ambiguo, nao fundir

    best = scored[0][1]
    difference = set(slug.upper().split("-")) ^ set(best.upper().split("-"))
    if difference & _DISTINGUISHING_TOKENS:
        return None
    if any(token.isdigit() for token in difference):
        return None  # "D30" vs "D60", "II" numerico etc.
    return best


def _reconcile_unidentified(merged: dict[str, FundOffer]) -> None:
    """Casa fundos sem CNPJ com o gemeo identificado, pelo nome.

    A identidade e o CNPJ quando existe e o slug quando nao - entao o mesmo
    fundo lido do site (sem CNPJ, caso da tabela do Itau) e do Open Finance
    (com CNPJ) ganha duas chaves e apareceria duas vezes na prateleira.

    Duas rodadas: slug exato (seguro) e depois fuzzy conservador - razao
    >= 0.95, candidato unico por margem e bloqueio de tokens que distinguem
    master/feeder/espelho. O que nao casar continua como registro proprio, e
    todo casamento carrega a proveniencia em extra["reconciliacao"].
    """
    from shelf_monitor.normalize import name_slug

    by_slug: dict[str, str] = {}
    for identity, offer in merged.items():
        if not offer.cnpj:
            continue
        by_slug.setdefault(name_slug(offer.name), identity)
        for variant in offer.extra.get("name_variants", []):
            by_slug.setdefault(name_slug(variant), identity)

    for identity in [i for i, o in merged.items() if not o.cnpj]:
        offer = merged[identity]
        if KEY_CNPJ_PENDENTE in offer.extra:
            # O resolvedor por nome (shelf_monitor.cnpj_por_nome) ja examinou
            # este fundo e nao achou CNPJ inequivoco - empate, conflito ou
            # nenhum candidato. Casar aqui pelo slug desfaria a decisao com uma
            # regra mais frouxa: o slug junta "Real Investor Ações" e "Real
            # Investor Multimercado", e o fuzzy escolhe sem olhar o empate.
            offer.extra.setdefault(
                "reconciliacao", "não reconciliado (sem CNPJ correspondente)"
            )
            continue
        slug = name_slug(offer.name)

        target_identity = by_slug.get(slug)
        provenance = "slug exato"
        if not target_identity:
            fuzzy_slug = _fuzzy_target(slug, by_slug)
            if fuzzy_slug:
                target_identity = by_slug[fuzzy_slug]
                provenance = f"nome aproximado ({slug} ~ {fuzzy_slug})"
        if not target_identity or target_identity == identity:
            offer.extra.setdefault(
                "reconciliacao", "não reconciliado (sem CNPJ correspondente)"
            )
            continue

        # O registro com CNPJ absorve o sem-CNPJ, que sai da prateleira:
        # o fundo final e um so, carregando as duas fontes.
        target = merged[target_identity]
        for source in offer.sources:
            _absorb(target, offer, source)
        target.extra.setdefault("reconciliacao", provenance)
        del merged[identity]


def _absorb(target: FundOffer, other: FundOffer, source: str) -> None:
    """Incorpora `other` em `target`, preenchendo apenas o que falta.

    Quando ambas as fontes tem um valor para o mesmo campo e eles divergem,
    registra em extra["divergencias"] - e informacao competitiva que o merge
    antigo descartava em silencio.
    """
    if source not in target.sources:
        target.sources.append(source)

    for name in _MERGED_FIELDS:
        target_value = getattr(target, name)
        other_value = getattr(other, name)
        if target_value is None:
            if other_value is not None:
                setattr(target, name, other_value)
        elif other_value is not None and target_value != other_value:
            divergencias = target.extra.setdefault("divergencias", {})
            divergencias[name] = {
                "valor_usado": _serialize(target_value),
                "fonte_usada": target.sources[0] if target.sources else "?",
                "valor_alternativo": _serialize(other_value),
                "fonte_alternativa": source,
            }

    # A classe nunca e None (tem default OUTROS), entao a regra e outra: uma
    # classificacao concreta vinda de qualquer fonte vale mais que "outros".
    if target.asset_class == AssetClass.OUTROS and other.asset_class != AssetClass.OUTROS:
        target.asset_class = other.asset_class

    # O nome vem da fonte mais confiavel, mas guardamos as variacoes: e como o
    # analista reconhece o fundo no site da casa.
    if other.name and other.name != target.name:
        variants = target.extra.setdefault("name_variants", [])
        if other.name not in variants:
            variants.append(other.name)

    for key, value in (other.extra or {}).items():
        if key != "name_variants":
            target.extra.setdefault(key, value)


def source_summary(groups: dict[str, list[FundOffer]], merged: list[FundOffer]) -> dict:
    """Cobertura de cada fonte: quantos fundos trouxe e quantos so ela tem."""
    exclusive: dict[str, int] = {source: 0 for source in groups}
    for offer in merged:
        if len(offer.sources) == 1 and offer.sources[0] in exclusive:
            exclusive[offer.sources[0]] += 1

    return {
        "counts": {source: len(offers) for source, offers in groups.items()},
        "exclusive": exclusive,
        "merged_total": len(merged),
    }
