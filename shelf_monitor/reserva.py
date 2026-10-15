"""Vitrine de reserva: a ultima vitrine valida da casa quando a coleta do dia falha.

Pedido em 14/10/2026: o Itau tem de aparecer em todas as analises. Ate ali, a
casa cuja coleta falhava saia de tudo o que cruza vitrines - em 28/09/2026 a
tabela do ion nao carregou, e o Itau sumiu dos gaps, da captacao e da
rentabilidade com "sem vitrine publica coletada". O mesmo acontecia com a
vitrine que voltava sem CNPJ o bastante (menos de metade identificada).

Agora, para as casas de `Config.vitrine_de_reserva`, uma coleta sem vitrine
utilizavel e substituida, nas analises, pela ultima vitrine utilizavel da
casa, de ate `IDADE_MAXIMA_DA_RESERVA_DIAS` dias - e toda superficie diz de
que dia ela e. O que nao muda:

- o snapshot gravado continua o da coleta do dia, falha inclusive;
- o diff (entradas e saidas) continua sobre o que a coleta trouxe: a vitrine
  de reserva nao inventa movimento;
- a linha final da execucao conta a casa como nao coletada.

Vitrine utilizavel e a que os blocos que cruzam casas aceitam: coleta nao
indisponivel, ao menos `MIN_VITRINE` fundos de varejo na vitrine e CNPJ em ao
menos metade deles (`competitive._MIN_CNPJ_COVERAGE`).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from shelf_monitor.config import Config
from shelf_monitor.models import ShelfSnapshot, SnapshotStatus
from shelf_monitor.report.aggregate import listing_funds
from shelf_monitor.report.competitive import _MIN_CNPJ_COVERAGE, cnpj_coverage
from shelf_monitor.report.shared.limiares import MIN_VITRINE
from shelf_monitor.storage import shelf_snapshots_before
from shelf_monitor.texto import contagem, data_br

# A vitrine de reserva mais velha que se aceita. Um mes cobre a falha de uma
# coleta semanal e da seguinte; passado isso, a vitrine ja nao descreve a casa.
IDADE_MAXIMA_DA_RESERVA_DIAS = 30


def problema_da_vitrine(snapshot: ShelfSnapshot) -> str | None:
    """Por que a vitrine nao serve aos blocos que cruzam casas; None quando serve."""
    if snapshot.status == SnapshotStatus.UNAVAILABLE or not snapshot.funds:
        return f"coleta indisponível ({snapshot.error})" if snapshot.error else "coleta indisponível"
    vitrine = listing_funds(snapshot)
    if not vitrine:
        return "sem vitrine coletada, só o catálogo Open Finance"
    if len(vitrine) < MIN_VITRINE:
        return f"vitrine com {contagem(len(vitrine), 'fundo', 'fundos')}, abaixo do mínimo de {MIN_VITRINE}"
    cobertura = cnpj_coverage(vitrine)
    if cobertura < _MIN_CNPJ_COVERAGE:
        return f"CNPJ em só {cobertura:.0%} da vitrine"
    return None


def ultima_vitrine_utilizavel(
    config: Config, snapshot: ShelfSnapshot
) -> tuple[ShelfSnapshot | None, bool]:
    """(a vitrine utilizavel mais recente da casa antes da coleta do dia, se o
    CNPJ dela foi identificado agora).

    Vitrine guardada sem CNPJ ganha uma tentativa de identificacao, quando o
    modulo sabe fazer isso (`config.identificar_vitrine`): o Itau Private
    guardado antes de 14/10/2026 tem a cota de cada plano, e a cota acha o
    veiculo no informe diario. O candidato vem do disco a cada leitura, entao
    preenche-lo nao toca o arquivo.
    """
    identificar = getattr(config, "identificar_vitrine", None)
    for candidato in shelf_snapshots_before(config, snapshot.platform, snapshot.snapshot_date):
        idade = (snapshot.snapshot_date - candidato.snapshot_date).days
        if idade > IDADE_MAXIMA_DA_RESERVA_DIAS:
            return None, False
        problema = problema_da_vitrine(candidato)
        identificado = False
        if problema and identificar is not None and _pode_identificar(candidato):
            identificado = identificar(candidato) is not None
            problema = problema_da_vitrine(candidato)
        if problema is None:
            return candidato, identificado
    return None, False


def _pode_identificar(snapshot: ShelfSnapshot) -> bool:
    return (
        bool(snapshot.funds) and snapshot.status != SnapshotStatus.UNAVAILABLE
        and bool(listing_funds(snapshot))
    )


def _identificada(config: Config, snapshot: ShelfSnapshot) -> ShelfSnapshot | None:
    """Uma copia da vitrine com o CNPJ identificado, se isso a torna utilizavel."""
    identificar = getattr(config, "identificar_vitrine", None)
    if identificar is None or not _pode_identificar(snapshot):
        return None
    copia = deepcopy(snapshot)
    if identificar(copia) is None or problema_da_vitrine(copia) is not None:
        return None
    return copia


def com_vitrine_de_reserva(
    config: Config, snapshots: list[ShelfSnapshot]
) -> tuple[list[ShelfSnapshot], list[ShelfSnapshot]]:
    """(snapshots das analises, os da coleta que foram substituidos).

    Na lista das analises, a casa sem vitrine utilizavel vem com a ultima que
    serviu, marcada em `reserva` (e em `error`, que coleta, planilha, deck e
    dashboard ja mostram). Sem vitrine utilizavel recente, fica a da coleta,
    com o motivo de sempre.
    """
    casas = set(getattr(config, "vitrine_de_reserva", ()) or ())
    analise: list[ShelfSnapshot] = []
    substituidos: list[ShelfSnapshot] = []
    for snapshot in snapshots:
        problema = (
            problema_da_vitrine(snapshot) if snapshot.platform in casas else None
        )
        # A vitrine do dia que so falha no CNPJ tenta a identificacao antes de
        # ceder a vez: numa copia, porque o diff continua sobre o que a coleta
        # trouxe.
        identificada_hoje = _identificada(config, snapshot) if problema else None
        if identificada_hoje is not None:
            analise.append(identificada_hoje)
            substituidos.append(snapshot)
            continue
        reserva, identificada = (
            ultima_vitrine_utilizavel(config, snapshot) if problema else (None, False)
        )
        if reserva is None:
            analise.append(snapshot)
            continue
        nota = (
            f"vitrine de reserva: a coleta de {data_br(snapshot.snapshot_date)} não trouxe "
            f"vitrine utilizável ({problema}); as análises usam a de "
            f"{data_br(reserva.snapshot_date)}, a última válida"
            + (", com o CNPJ identificado na hora do relatório" if identificada else "")
        )
        analise.append(replace(reserva, reserva=nota, error=nota))
        substituidos.append(snapshot)
    return analise, substituidos
