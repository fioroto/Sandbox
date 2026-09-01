"""Perfil de distribuicao de um fundo: varejo ou restrito/exclusivo.

"Fundo exclusivo" aqui e o sentido regulatorio - veiculo desenhado para um
investidor (ou grupo restrito), nao para distribuicao. Eles inflam o catalogo
do Open Finance (e a razao de o BTG publicar ~9.950 fundos) e poluem qualquer
comparacao de prateleira: taxa mediana, profundidade de gestora e amplitude por
classe mudam de significado quando metade da amostra nunca esteve a venda.

As analises principais operam so sobre o perfil de varejo; os restritos nao
somem - vao para o relatorio auxiliar, com o motivo de cada um.

Nota deliberada: investidor QUALIFICADO nao e motivo de exclusao - plataforma
de varejo distribui fundo qualificado a alta renda normalmente. Profissional e.
"""
from __future__ import annotations

import re

from shelf_monitor.enrich import KEY_PUBLICO
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import normalize_text

# Chave gravada pelo enrich com o campo Exclusivo do registro CVM ("S"/"N").
KEY_EXCLUSIVO = "exclusivo_cvm"
# Chave gravada pelos coletores quando a propria casa marca o fundo como
# restrito/exclusivo (ex.: restrictedOrExclusive na API do BTG).
KEY_RESTRITO_CASA = "restrito_casa"

# Tokens de nome que denunciam veiculo fora de distribuicao. Fronteira de
# palavra: "EXCLUSIVO" casa, "EXCLUSIVE BRANDS" (nome de estrategia) tambem -
# e intencional; "SELECTION" ou "RESERVA" (produto de varejo) nao casam.
_NAME_RE = re.compile(
    r"(?<!\w)(EXCLUSIV\w*|RESERVAD\w*|RESTRIT\w*|FAMILY OFFICE)(?!\w)"
)


def restriction_reason(fund: FundOffer) -> str | None:
    """Por que este fundo NAO pertence a analise de varejo; None = pertence.

    A ordem vai da evidencia mais forte (registro CVM) para a mais fraca
    (token no nome) - o motivo reportado e o melhor disponivel.
    """
    exclusive = str(fund.extra.get(KEY_EXCLUSIVO) or "").strip().upper()
    if exclusive.startswith("S"):
        return "exclusivo no registro CVM"

    audience = normalize_text(str(fund.extra.get(KEY_PUBLICO) or ""))
    if "PROFISSIONAL" in audience:
        return "publico-alvo profissional (registro CVM)"

    if fund.extra.get(KEY_RESTRITO_CASA):
        return "marcado restrito/exclusivo pela propria casa"

    house_audience = normalize_text(str(fund.extra.get("publico") or ""))
    if "PROFISSIONAL" in house_audience:
        return "publico-alvo profissional (segundo a casa)"

    if _NAME_RE.search(normalize_text(fund.name)):
        return "nome indica exclusivo/reservado/restrito"

    return None


def split_by_profile(funds: list[FundOffer]) -> tuple[list[FundOffer], list[tuple[FundOffer, str]]]:
    """(varejo, [(restrito, motivo), ...]) - a particao que os relatorios usam."""
    retail: list[FundOffer] = []
    restricted: list[tuple[FundOffer, str]] = []
    for fund in funds:
        reason = restriction_reason(fund)
        if reason is None:
            retail.append(fund)
        else:
            restricted.append((fund, reason))
    return retail, restricted
