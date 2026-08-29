"""Persistencia de snapshots em JSON e resolucao do snapshot anterior."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.models import CvmSnapshot, ShelfSnapshot

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


def save_shelf_snapshot(config: Config, snapshot: ShelfSnapshot) -> Path:
    directory = config.platform_snapshots_dir(snapshot.platform)
    path = directory / f"{snapshot.snapshot_date.isoformat()}.json"
    return _write_json(path, snapshot.to_dict())


def load_shelf_snapshot(path: Path) -> ShelfSnapshot:
    return ShelfSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def latest_shelf_snapshot(
    config: Config, platform_id: str, before: date | None = None
) -> ShelfSnapshot | None:
    """Snapshot mais recente da plataforma; com `before`, o mais recente anterior a ele.

    Snapshots UNAVAILABLE sao ignorados como baseline: comparar contra uma coleta
    que falhou produziria uma prateleira inteira como "novidade".
    """
    candidates = _dated_files(config.platform_snapshots_dir(platform_id))
    if before is not None:
        candidates = [p for p in candidates if _parse_date(p) < before]

    for path in reversed(candidates):
        snapshot = load_shelf_snapshot(path)
        if snapshot.funds:
            return snapshot
    return None


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
