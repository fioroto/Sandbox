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

from shelf_monitor.models import AssetClass, CollectorStrategy, FundOffer

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
    "cnpj", "isin", "manager_name", "manager_cnpj", "admin_name", "raw_category",
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
            existing = merged.get(offer.identity)
            if existing is None:
                merged[offer.identity] = offer
                continue
            _absorb(existing, offer, source)

    for offer in merged.values():
        offer.sources.sort(key=source_rank)
    return list(merged.values())


def _absorb(target: FundOffer, other: FundOffer, source: str) -> None:
    """Incorpora `other` em `target`, preenchendo apenas o que falta."""
    if source not in target.sources:
        target.sources.append(source)

    for name in _MERGED_FIELDS:
        if getattr(target, name) is None:
            value = getattr(other, name)
            if value is not None:
                setattr(target, name, value)

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
