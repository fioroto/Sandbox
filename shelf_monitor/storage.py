"""Persistencia de snapshots em JSON e resolucao do snapshot anterior."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.models import CvmSnapshot, ShelfSnapshot, SnapshotStatus

_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def _dated_files(directory: Path) -> list[Path]:
    """Arquivos <YYYY-MM-DD>.json ordenados por data crescente."""
    if not directory.exists():
        return []
    return sorted(directory.glob(_DATE_GLOB))


def _parse_date(path: Path) -> date:
    return date.fromisoformat(path.stem)


# --- prateleiras -------------------------------------------------------------


def save_shelf_snapshot(config: Config, snapshot: ShelfSnapshot) -> Path | None:
    """Grava o snapshot do dia.

    Uma segunda execucao no mesmo dia sobrescreve a primeira - o que e correto
    quando a nova coleta e igual ou melhor, e destrutivo quando nao e. Um retry
    que caiu no bloqueio nao pode apagar a coleta boa da manha, entao aqui a
    gravacao e recusada nesse caso (devolve None).
    """
    directory = config.platform_snapshots_dir(snapshot.platform)
    path = directory / f"{snapshot.snapshot_date.isoformat()}.json"

    if path.exists():
        existing = load_shelf_snapshot(path)
        if _quality_rank(existing) > _quality_rank(snapshot):
            return None

    return _write_json(path, snapshot.to_dict())


def _quality_rank(snapshot: ShelfSnapshot) -> tuple[int, int]:
    """Ordena snapshots do mesmo dia: status primeiro, contagem como desempate."""
    status_rank = {
        SnapshotStatus.OK: 2,
        SnapshotStatus.PARTIAL: 1,
        SnapshotStatus.UNAVAILABLE: 0,
    }
    return status_rank[snapshot.status], len(snapshot.funds)


def load_shelf_snapshot(path: Path) -> ShelfSnapshot:
    return ShelfSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def latest_shelf_snapshot(
    config: Config,
    platform_id: str,
    before: date | None = None,
    trusted_only: bool = False,
) -> ShelfSnapshot | None:
    """Snapshot mais recente da plataforma; com `before`, o anterior a essa data.

    Com `trusted_only`, so um snapshot OK serve. Isso importa ao escolher a
    baseline de um diff: comparar contra uma coleta parcial faz a prateleira
    reaparecer inteira como "novidade" no dia seguinte.
    """
    candidates = _dated_files(config.platform_snapshots_dir(platform_id))
    if before is not None:
        candidates = [p for p in candidates if _parse_date(p) < before]

    fallback: ShelfSnapshot | None = None
    for path in reversed(candidates):
        snapshot = load_shelf_snapshot(path)
        if not snapshot.funds:
            continue
        if snapshot.status == SnapshotStatus.OK:
            return snapshot
        if fallback is None:
            fallback = snapshot

    return None if trusted_only else fallback


def shelf_snapshot_dates(config: Config, platform_id: str) -> list[date]:
    return [_parse_date(p) for p in _dated_files(config.platform_snapshots_dir(platform_id))]


# --- CVM ---------------------------------------------------------------------


def save_cvm_snapshot(config: Config, snapshot: CvmSnapshot) -> Path:
    path = config.cvm_extract_dir / f"{snapshot.snapshot_date.isoformat()}.json"
    return _write_json(path, snapshot.to_dict())


def load_cvm_snapshot(path: Path) -> CvmSnapshot:
    return CvmSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def latest_cvm_snapshot(
    config: Config, before: date | None = None
) -> CvmSnapshot | None:
    candidates = _dated_files(config.cvm_extract_dir)
    if before is not None:
        candidates = [p for p in candidates if _parse_date(p) < before]

    for path in reversed(candidates):
        snapshot = load_cvm_snapshot(path)
        if snapshot.funds:
            return snapshot
    return None
