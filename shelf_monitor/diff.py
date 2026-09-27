"""Comparacao entre snapshots: o que entrou, saiu e mudou.

O risco central de um monitor assim e o falso churn: uma coleta parcial faz a
prateleira inteira parecer ter sumido. Por isso o diff so reporta saidas quando
confia na coleta atual (ver `SUSPECT_SHRINK_RATIO`).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from shelf_monitor.config import (
    FUZZY_RENAME_THRESHOLD,
    SUSPECT_GROWTH_MIN_FUNDS,
    SUSPECT_GROWTH_RATIO,
    SUSPECT_SHRINK_RATIO,
)
from shelf_monitor.models import (
    CvmDiff,
    CvmFund,
    CvmSituationChange,
    CvmSnapshot,
    FieldChange,
    FundChange,
    FundOffer,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import name_slug
from shelf_monitor.profile import na_vitrine

# (rotulo, extrator) - o rotulo aparece no digest.
_TRACKED = (
    ("min_initial", lambda f: f.min_initial),
    ("admin_fee_pct", lambda f: f.admin_fee_pct),
    (
        "performance_fee_pct",
        lambda f: f.performance_fee.amount_pct if f.performance_fee else None,
    ),
    ("closed_for_investment", lambda f: f.closed_for_investment),
    ("redemption_quotation_days", lambda f: f.redemption_quotation_days),
    ("redemption_payment_days", lambda f: f.redemption_payment_days),
    ("grace_days", lambda f: f.grace_days),
)


def _missing_sources(
    current: ShelfSnapshot, previous: ShelfSnapshot | None
) -> list[str]:
    """Fontes que alimentavam a baseline e nao responderam nesta coleta.

    Contar so o tamanho da prateleira nao enxerga o caso mais comum de perda de
    cobertura: a casa em que uma das fontes da uniao caiu. O BTG que sai de
    9.950 para 9.818 fundos porque o navegador falhou tem razao 0,99 - passa
    longe do limiar de encolhimento e ainda assim perdeu a vitrine comercial.

    Le `previous.strategies` em vez de `sources_used` de proposito: snapshots
    anteriores a essa coluna voltam do disco com a lista vazia, e a regra fica
    inerte para eles em vez de acusar uma perda que nao se pode verificar.
    """
    if previous is None or not previous.strategies:
        return []

    used_now = set(current.sources_used)
    missing = []
    for source in previous.strategies:
        if source in used_now:
            continue
        # Fonte que nao trouxe fundo na baseline nao e perda material.
        if previous.source_counts and not previous.source_counts.get(source):
            continue
        missing.append(source)
    return missing


def assess_quality(
    current: ShelfSnapshot,
    previous: ShelfSnapshot | None,
    espera_vitrine: bool = False,
) -> ShelfSnapshot:
    """Rebaixa para PARTIAL uma coleta sem vitrine, que encolheu ou perdeu fonte.

    A marcacao precisa viver no snapshot, nao so no diff: um snapshot truncado
    salvo como OK vira a baseline confiavel do dia seguinte, e a prateleira
    inteira reaparece como "entradas" quando a coleta volta ao normal.

    `espera_vitrine` diz que a casa declara uma fonte de vitrine. Sem nenhum
    fundo de vitrine, a coleta e parcial mesmo sem baseline: em 2026-09-13 XP,
    BTG e Itau foram gravados OK so com o catalogo do Open Finance - para um
    monitor de vitrine, a coleta que falhou justamente na vitrine.
    """
    if current.status != SnapshotStatus.OK:
        return current

    if espera_vitrine and current.funds and not any(f.on_listing for f in current.funds):
        current.status = SnapshotStatus.PARTIAL
        note = (
            f"nenhum fundo de vitrine nesta coleta - só o catálogo respondeu "
            f"({len(current.funds)} fundos); tratada como parcial"
        )
        current.error = f"{note} | {current.error}" if current.error else note
        return current

    if previous is None or not previous.funds:
        return current

    ratio = len(current.funds) / len(previous.funds)
    if ratio < SUSPECT_SHRINK_RATIO:
        current.status = SnapshotStatus.PARTIAL
        current.error = (
            f"coleta trouxe {len(current.funds)} fundos contra {len(previous.funds)} "
            f"da anterior ({ratio:.0%}) - tratada como parcial"
        )
        return current

    # Fonte perdida so e rebaixamento quando a prateleira tambem encolheu: se a
    # uniao compensou, nao ha cobertura a proteger - e e assim que a casa sai do
    # estado parcial sozinha quando a fonte volta ou outra assume.
    missing = _missing_sources(current, previous)
    if missing and len(current.funds) < len(previous.funds):
        current.status = SnapshotStatus.PARTIAL
        detail = ", ".join(
            f"{source} (antes {previous.source_counts.get(source, '?')} fundos)"
            for source in missing
        )
        note = (
            f"{'fonte' if len(missing) == 1 else 'fontes'} da coleta anterior sem "
            f"resposta nesta: {detail}; "
            f"{len(current.funds)} contra {len(previous.funds)} fundos - "
            "tratada como parcial"
        )
        # Concatena: a observacao que o coletor gravou diz por que a fonte caiu.
        current.error = f"{note} | {current.error}" if current.error else note
    return current


def _index(funds: list[FundOffer]) -> dict[str, FundOffer]:
    """Indexa por identidade, desambiguando colisoes.

    Dois fundos sem CNPJ podem colapsar no mesmo slug (formas juridicas
    diferentes da mesma familia). Sem sufixo, um sobrescreveria o outro e uma
    saida real ficaria invisivel no diff.
    """
    index: dict[str, FundOffer] = {}
    for fund in funds:
        identity = fund.identity
        if identity in index:
            suffix = 2
            while f"{identity}#{suffix}" in index:
                suffix += 1
            identity = f"{identity}#{suffix}"
        index[identity] = fund
    return index


def _field_changes(old: FundOffer, new: FundOffer) -> list[FieldChange]:
    changes: list[FieldChange] = []
    for label, extract in _TRACKED:
        old_value = extract(old)
        new_value = extract(new)
        # Um campo que passou a ser desconhecido nao e uma mudanca de oferta:
        # normalmente e a fonte que parou de publicar aquele dado.
        if new_value is None or old_value is None:
            continue
        if old_value != new_value:
            changes.append(FieldChange(field=label, old=old_value, new=new_value))
    return changes


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+", text)


def _may_be_rename(old_slug: str, new_slug: str) -> bool:
    """Decide se dois nomes parecidos sao o mesmo fundo renomeado.

    Similaridade textual sozinha nao basta: na industria o numero e justamente
    o que distingue mandatos ("Selection 30" e "Selection 60" sao 93% iguais e
    fundos diferentes). Numeros divergentes vetam a reconciliacao.
    """
    if _digits(old_slug) != _digits(new_slug):
        return False
    return _similarity(old_slug, new_slug) >= FUZZY_RENAME_THRESHOLD


def _reconcile_renames(
    added: list[FundOffer], removed: list[FundOffer]
) -> tuple[list[FundOffer], list[FundOffer], list[FundChange]]:
    """Casa entradas com saidas muito parecidas: e renomeacao, nao troca.

    So se aplica a fundos sem CNPJ - com CNPJ a identidade ja e estavel.
    """
    still_added = list(added)
    still_removed = list(removed)
    renamed: list[FundChange] = []

    candidates_added = [f for f in still_added if not f.cnpj]
    candidates_removed = [f for f in still_removed if not f.cnpj]

    for new_fund in candidates_added:
        new_slug = name_slug(new_fund.name)
        best: FundOffer | None = None
        best_score = 0.0
        for old_fund in candidates_removed:
            if old_fund not in still_removed:
                continue
            old_slug = name_slug(old_fund.name)
            if not _may_be_rename(old_slug, new_slug):
                continue
            score = _similarity(old_slug, new_slug)
            if score > best_score:
                best, best_score = old_fund, score

        if best is not None:
            still_added.remove(new_fund)
            still_removed.remove(best)
            changes = [FieldChange(field="name", old=best.name, new=new_fund.name)]
            changes.extend(_field_changes(best, new_fund))
            renamed.append(
                FundChange(identity=new_fund.identity, name=new_fund.name, changes=changes)
            )

    return still_added, still_removed, renamed


def diff_shelf(
    current: ShelfSnapshot, previous: ShelfSnapshot | None
) -> ShelfDiff:
    """Compara duas fotos da prateleira de uma plataforma."""
    diff = ShelfDiff(
        platform=current.platform,
        prev_date=previous.snapshot_date if previous else None,
        curr_date=current.snapshot_date,
    )

    if current.status == SnapshotStatus.UNAVAILABLE:
        diff.suspect = True
        diff.suspect_reason = current.error or "coleta indisponível"
        return diff

    if previous is None:
        # Primeira coleta: tudo e "novo", mas isso nao e novidade competitiva.
        diff.added = list(current.funds)
        return diff

    # O espelho do anti-churn de encolhimento, para a vitrine que aparece ou
    # cresce de escala: a coleta passou a ver mais, e o que ela ve pela
    # primeira vez nao entrou na prateleira. Em 2026-09-19 o conserto do
    # navegador levou tres vitrines de 0 a ~600 fundos e o digest publicou
    # "828 fundos entraram". Nova linha de base: nada entra, sai ou muda.
    # A vitrine de varejo, a mesma que o digest publica ao lado deste motivo:
    # contada com os restritos, a frase dizia "649 fundos na vitrine" e "a
    # vitrine passou de 0 para 660" na mesma linha (BTG, 19/09).
    vitrine_antes = sum(1 for f in previous.funds if na_vitrine(f))
    vitrine_agora = sum(1 for f in current.funds if na_vitrine(f))
    mudou_de_escala = (
        vitrine_agora / vitrine_antes >= SUSPECT_GROWTH_RATIO
        and vitrine_agora - vitrine_antes >= SUSPECT_GROWTH_MIN_FUNDS
        if vitrine_antes
        else vitrine_agora > 0
    )
    if mudou_de_escala:
        diff.nova_linha_de_base = True
        diff.suspect = True
        diff.suspect_reason = (
            f"a vitrine passou de {vitrine_antes} para {vitrine_agora} fundos: ganho "
            "de cobertura da coleta, não movimento de prateleira"
        )
        return diff

    current_index = _index(current.funds)
    previous_index = _index(previous.funds)

    added = [f for identity, f in current_index.items() if identity not in previous_index]
    removed = [f for identity, f in previous_index.items() if identity not in current_index]

    changed: list[FundChange] = []
    for identity, new_fund in current_index.items():
        old_fund = previous_index.get(identity)
        if not old_fund:
            continue
        field_changes = _field_changes(old_fund, new_fund)
        if field_changes:
            changed.append(
                FundChange(identity=identity, name=new_fund.name, changes=field_changes)
            )

    added, removed, renamed = _reconcile_renames(added, removed)
    changed.extend(renamed)

    diff.added = sorted(added, key=lambda f: f.name)
    diff.changed = sorted(changed, key=lambda c: c.name)

    shrink_ratio = (
        len(current.funds) / len(previous.funds) if previous.funds else 1.0
    )
    if current.status == SnapshotStatus.PARTIAL:
        diff.suspect = True
        diff.suspect_reason = current.error or "coleta parcial"
    elif shrink_ratio < SUSPECT_SHRINK_RATIO:
        diff.suspect = True
        diff.suspect_reason = (
            f"coleta atual tem {len(current.funds)} fundos contra "
            f"{len(previous.funds)} da anterior ({shrink_ratio:.0%})"
        )
    else:
        # Repetido aqui porque `diff`/`report` comparam snapshots lidos do disco,
        # sem passar por assess_quality.
        missing = _missing_sources(current, previous)
        if missing and len(current.funds) < len(previous.funds):
            diff.suspect = True
            uma = len(missing) == 1
            diff.suspect_reason = (
                f"{'fonte' if uma else 'fontes'} {', '.join(missing)} da coleta anterior "
                f"{'não respondeu' if uma else 'não responderam'} nesta "
                f"({len(current.funds)} contra {len(previous.funds)} fundos)"
            )

    # Coleta suspeita: as "saidas" provavelmente sao da coleta, nao da prateleira.
    diff.removed = [] if diff.suspect else sorted(removed, key=lambda f: f.name)
    return diff


def diff_cvm(current: CvmSnapshot, previous: CvmSnapshot | None) -> CvmDiff:
    """Novos registros, mudancas de situacao e cancelamentos na watchlist."""
    diff = CvmDiff(
        prev_date=previous.snapshot_date if previous else None,
        curr_date=current.snapshot_date,
    )

    if current.status == SnapshotStatus.UNAVAILABLE:
        diff.suspect = True
        diff.suspect_reason = current.error or "extrato indisponível"
        return diff

    # Uma coluna renomeada pela CVM faz a watchlist casar com nada e o extrato
    # despencar - sem esta guarda, o digest anunciaria a watchlist inteira como
    # "saiu do cadastro".
    if current.status == SnapshotStatus.PARTIAL:
        diff.suspect = True
        diff.suspect_reason = current.error or "extrato parcial"
    elif previous and previous.funds:
        ratio = len(current.funds) / len(previous.funds)
        if ratio < SUSPECT_SHRINK_RATIO:
            diff.suspect = True
            diff.suspect_reason = (
                f"extrato atual tem {len(current.funds)} fundos contra "
                f"{len(previous.funds)} do anterior ({ratio:.0%})"
            )

    if previous is None:
        # Sem baseline, o unico sinal confiavel sao os pre-operacionais abertos.
        diff.new_registrations = sorted(
            [f for f in current.funds if f.is_pre_operational],
            key=lambda f: f.registered_at or "",
            reverse=True,
        )
        return diff

    current_index: dict[str, CvmFund] = {f.cnpj: f for f in current.funds}
    previous_index: dict[str, CvmFund] = {f.cnpj: f for f in previous.funds}

    diff.new_registrations = sorted(
        [f for cnpj, f in current_index.items() if cnpj not in previous_index],
        key=lambda f: f.registered_at or "",
        reverse=True,
    )
    # Como no diff de prateleira: extrato suspeito nao reporta desaparecimentos.
    diff.cancelled = (
        []
        if diff.suspect
        else sorted(
            [f for cnpj, f in previous_index.items() if cnpj not in current_index],
            key=lambda f: f.name,
        )
    )

    changes: list[CvmSituationChange] = []
    for cnpj, new_fund in current_index.items():
        old_fund = previous_index.get(cnpj)
        if old_fund and old_fund.situation != new_fund.situation:
            changes.append(
                CvmSituationChange(
                    fund=new_fund,
                    old_situation=old_fund.situation,
                    new_situation=new_fund.situation,
                )
            )
    # Lancamentos primeiro: sao o sinal mais acionavel.
    diff.situation_changes = sorted(
        changes, key=lambda c: (not c.is_launch, c.fund.name)
    )
    return diff
