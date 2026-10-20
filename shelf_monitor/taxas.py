"""Taxa de administracao em pontos percentuais, qualquer que seja a fonte.

O campo do monitor, `FundOffer.admin_fee_pct`, guarda pontos percentuais (2,0 para
2% ao ano): a vitrine do site e o extrato da CVM ja vem assim. O `maxAdminFee` do
Open Finance nao vem num formato so. Medido nas coletas de 28/09 e 14/10/2026,
contando as taxas positivas que o catalogo de cada casa publicou:

- em fracao (0,02 para 2%) na XP (626 de 626 ate 0,1), no BTG (9.036 de 9.044) e
  no Itau (1.418 de 1.418);
- em percentual na Nu (455 de 462 acima de 0,1).

Misturadas, as duas escalas faziam a taxa da maioria dos fundos sair 100 vezes
menor em toda superficie que a lia - "Taxa adm (%)" na planilha, a mediana de taxa
por casa no painel, a menor taxa dos gaps. A escala e decidida por catalogo, pela
mediana das taxas positivas: ate `LIMIAR_FRACAO`, o catalogo esta em fracao, e cada
taxa ate o limiar vira percentual. Acima do limiar a taxa ja e percentual: fracao
acima de 10% ao ano nao e taxa de fundo (no BTG, 8 das 9.044).

A taxa que a vitrine publica vence a do catalogo no merge (`merge.taxa_da_vitrine_vence`):
alem da escala, o `maxAdminFee` traz em parte dos fundos so a taxa de
administracao, sem a gestao - o Itau Small Cap Valuation tinha 0,0005 no Open
Finance e 2,0% no site do Itau (14/10/2026). A do site so vale ate `TETO_PCT`, o
teto do extrato da CVM: o site do BTG publicava 100,0 no Brasil Plural Debentures
Incentivadas e 90,0 no Brave I (28/09/2026) - os 6 acima de 5% nas tres casas, num
universo de 1.354 taxas de site.
"""
from __future__ import annotations

from collections.abc import Iterable
from statistics import median

LIMIAR_FRACAO = 0.1
# O teto do extrato da CVM (`market.fees._PERCENT_CEILING`), repetido aqui: o
# import puxaria os coletores, que importam este modulo.
TETO_PCT = 20.0


def plausivel(taxa: float | None) -> bool:
    """Taxa de fundo em pontos percentuais: de zero ao teto do extrato."""
    return taxa is not None and 0 <= taxa <= TETO_PCT


def catalogo_em_fracao(taxas: Iterable[float | None]) -> bool:
    """O catalogo publica a taxa em fracao: a mediana das positivas ate o limiar."""
    positivas = [t for t in taxas if t is not None and t > 0]
    return bool(positivas) and median(positivas) <= LIMIAR_FRACAO


def em_percentual(taxa: float | None, em_fracao: bool) -> float | None:
    """A taxa em pontos percentuais; so muda a de catalogo em fracao, ate o limiar."""
    if taxa is None or not em_fracao or taxa > LIMIAR_FRACAO:
        return taxa
    return round(taxa * 100, 6)


def catalogo_em_percentual(ofertas: list) -> int:
    """Converte, no lugar, as taxas de um catalogo do Open Finance; devolve
    quantas mudaram."""
    if not catalogo_em_fracao(o.admin_fee_pct for o in ofertas):
        return 0
    mudaram = 0
    for oferta in ofertas:
        convertida = em_percentual(oferta.admin_fee_pct, True)
        if convertida != oferta.admin_fee_pct:
            oferta.admin_fee_pct = convertida
            mudaram += 1
    return mudaram


def migrar_snapshot_v1(fundos: list) -> None:
    """A taxa de um snapshot gravado antes de 20/10/2026, corrigida no lugar.

    Tres passos, na ordem do merge de hoje: a taxa da vitrine, que o merge antigo
    so guardava como divergencia, volta para `KEY_TAXA_VITRINE`; a do catalogo
    (o valor mesclado, quando o Open Finance e a primeira fonte) vai para pontos
    percentuais pela escala do catalogo; e a da vitrine vence. O fundo cuja taxa
    so a vitrine trazia, sem divergencia, e indistinguivel da do catalogo: abaixo
    do limiar, num catalogo em fracao, ele e convertido junto - uma taxa de
    vitrine ate 0,1% ao ano, rara.
    """
    from shelf_monitor.merge import taxa_da_vitrine_vence
    from shelf_monitor.models import KEY_TAXA_VITRINE, LISTING_SOURCES, CollectorStrategy

    open_finance = CollectorStrategy.OPEN_FINANCE.value
    for fundo in fundos:
        divergencia = (fundo.extra.get("divergencias") or {}).get("admin_fee_pct") or {}
        if (
            divergencia.get("fonte_alternativa") in LISTING_SOURCES
            and plausivel(divergencia.get("valor_alternativo"))
        ):
            fundo.extra.setdefault(KEY_TAXA_VITRINE, divergencia["valor_alternativo"])
    do_catalogo = [f for f in fundos if f.sources and f.sources[0] == open_finance]
    em_fracao = catalogo_em_fracao(f.admin_fee_pct for f in do_catalogo)
    for fundo in do_catalogo:
        fundo.admin_fee_pct = em_percentual(fundo.admin_fee_pct, em_fracao)
        # A divergencia gravada com a fracao: convertida, a do catalogo pode
        # bater com a da vitrine (o JGP Hedge, 2% nas duas), e ai nao diverge.
        divergencias = fundo.extra.get("divergencias") or {}
        antiga = divergencias.get("admin_fee_pct")
        if antiga and antiga.get("fonte_usada") == open_finance:
            antiga["valor_usado"] = fundo.admin_fee_pct
            if antiga.get("valor_alternativo") == fundo.admin_fee_pct:
                del divergencias["admin_fee_pct"]
    for fundo in fundos:
        taxa_da_vitrine_vence(fundo)
