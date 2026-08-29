"""Download dos arquivos da CVM com cache condicional.

O extrato de informacoes tem ~100 MB. Sem requisicao condicional, cada execucao
rebaixaria o arquivo inteiro para descobrir que nada mudou - o que torna o
comando lento demais para ser rodado a vontade.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable, build_session, describe_error
from shelf_monitor.market.datasets import Dataset

_CHUNK = 1 << 20
_CACHE_FILE = "_http_cache.json"


@dataclass
class FetchResult:
    dataset: Dataset
    path: Path
    from_cache: bool
    size_bytes: int

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


def _cache_path(config: Config) -> Path:
    return config.market_raw_dir / _CACHE_FILE


def _load_cache(config: Config) -> dict:
    path = _cache_path(config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_cache(config: Config, cache: dict) -> None:
    path = _cache_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def fetch(config: Config, dataset: Dataset, force: bool = False) -> FetchResult:
    """Baixa o dataset, reusando o arquivo local quando a origem nao mudou."""
    config.market_raw_dir.mkdir(parents=True, exist_ok=True)
    dest = config.market_raw_dir / dataset.filename
    cache = _load_cache(config)
    entry = cache.get(dataset.key, {})

    headers = {}
    if dest.exists() and not force:
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]

    session = build_session(config)
    try:
        response = session.get(
            dataset.url,
            headers=headers,
            timeout=config.http_timeout,
            stream=True,
        )
    except requests.exceptions.RequestException as exc:
        # Um arquivo baixado antes ainda serve: melhor uma base de ontem do que
        # nenhuma base.
        if dest.exists():
            return FetchResult(dataset, dest, True, dest.stat().st_size)
        raise SourceUnavailable(f"{dataset.url}: {describe_error(exc)}") from exc

    if response.status_code == 304 and dest.exists():
        response.close()
        return FetchResult(dataset, dest, True, dest.stat().st_size)

    if response.status_code >= 400:
        response.close()
        if dest.exists():
            return FetchResult(dataset, dest, True, dest.stat().st_size)
        raise SourceUnavailable(f"HTTP {response.status_code} em {dataset.url}")

    # Grava num temporario e so troca no fim: uma interrupcao no meio do
    # download nao pode deixar meio arquivo no lugar do bom.
    temporary = dest.with_suffix(dest.suffix + ".part")
    with temporary.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=_CHUNK):
            if chunk:
                handle.write(chunk)

    if dataset.members and not zipfile.is_zipfile(temporary):
        temporary.unlink(missing_ok=True)
        raise SourceUnavailable(
            f"{dataset.filename} nao e um zip valido (download corrompido ou "
            "resposta de bloqueio)"
        )

    temporary.replace(dest)
    cache[dataset.key] = {
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "size": dest.stat().st_size,
    }
    _save_cache(config, cache)

    return FetchResult(dataset, dest, False, dest.stat().st_size)


def read_member(path: Path, needle: str) -> bytes:
    """Le um CSV de dentro do zip, localizando-o por parte do nome."""
    with zipfile.ZipFile(path) as archive:
        names = {name.lower(): name for name in archive.namelist()}
        match = next(
            (
                original
                for lowered, original in names.items()
                if needle in lowered and lowered.endswith(".csv")
            ),
            None,
        )
        if not match:
            raise SourceUnavailable(
                f"{needle}.csv ausente em {path.name} "
                f"(contem: {sorted(names.values())})"
            )
        return archive.read(match)
