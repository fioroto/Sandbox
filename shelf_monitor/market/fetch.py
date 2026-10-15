"""Download dos arquivos da CVM com cache condicional.

O extrato de informacoes tem ~100 MB. Sem requisicao condicional, cada execucao
rebaixaria o arquivo inteiro para descobrir que nada mudou - o que torna o
comando lento demais para ser rodado a vontade.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import requests

from shelf_monitor.config import Config
from shelf_monitor.http import (
    SourceUnavailable,
    build_session,
    describe_error,
    is_proxy_block,
)
from shelf_monitor.market.datasets import Dataset

_CHUNK = 1 << 20
_CACHE_FILE = "_http_cache.json"


class CacheReason(str, Enum):
    """Por que o arquivo local foi reusado - sao situacoes bem diferentes.

    Sem essa distincao, "sem mudanca na origem" apareceria tambem quando a CVM
    esta fora do ar, e o operador concluiria que a base esta atual.
    """

    DOWNLOADED = "baixado"
    NOT_MODIFIED = "sem mudança na origem"
    SOURCE_DOWN = "origem indisponível"


@dataclass
class FetchResult:
    dataset: Dataset
    path: Path
    from_cache: bool
    size_bytes: int
    reason: CacheReason = CacheReason.DOWNLOADED
    detail: str | None = None

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


def _looks_like_csv(path: Path) -> bool:
    """Um CSV da CVM comeca com cabecalho separado por ';'.

    Sem esta checagem, uma pagina de erro devolvida com HTTP 200 - o modo de
    falha classico de portal publico - e gravada por cima do arquivo bom e
    parseada como se fosse dado, produzindo uma base vazia em silencio.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    if not head:
        return False
    lowered = head[:512].lower()
    if lowered.lstrip().startswith((b"<!doctype", b"<html", b"{", b"[")):
        return False
    first_line = head.split(b"\n", 1)[0]
    return b";" in first_line


def _validate(dataset: Dataset, path: Path) -> str | None:
    """Devolve o motivo da recusa, ou None se o arquivo parece integro."""
    if dataset.members:
        if not zipfile.is_zipfile(path):
            return "não é um zip válido"
        return None
    if not _looks_like_csv(path):
        return "não parece um CSV (possível página de erro ou bloqueio)"
    return None


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
        # nenhuma base. Mas o motivo e outro, e o chamador precisa distinguir.
        if dest.exists():
            return FetchResult(
                dataset, dest, True, dest.stat().st_size, CacheReason.SOURCE_DOWN,
                detail=describe_error(exc),
            )
        raise SourceUnavailable(
            f"{dataset.url}: {describe_error(exc)}",
            blocked_by_proxy=is_proxy_block(exc),
        ) from exc

    try:
        if response.status_code == 304:
            if dest.exists():
                return FetchResult(
                    dataset, dest, True, dest.stat().st_size, CacheReason.NOT_MODIFIED
                )
            # 304 sem arquivo local: gravar o corpo vazio criaria um arquivo de
            # zero byte permanente, porque o cache seguiria valido.
            raise SourceUnavailable(
                f"{dataset.url}: HTTP 304 mas o arquivo local não existe "
                "(cache inconsistente - use --force)"
            )

        if response.status_code >= 400:
            if dest.exists():
                return FetchResult(
                    dataset, dest, True, dest.stat().st_size, CacheReason.SOURCE_DOWN,
                    detail=f"HTTP {response.status_code}",
                )
            raise SourceUnavailable(f"HTTP {response.status_code} em {dataset.url}")

        # Grava num temporario e so troca no fim: uma interrupcao no meio do
        # download nao pode deixar meio arquivo no lugar do bom.
        temporary = dest.with_suffix(dest.suffix + ".part")
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK):
                    if chunk:
                        handle.write(chunk)
        except requests.exceptions.RequestException as exc:
            # A conexao caiu no meio de um arquivo de ~100 MB - o caminho de
            # falha mais provavel aqui.
            temporary.unlink(missing_ok=True)
            if dest.exists():
                return FetchResult(
                    dataset, dest, True, dest.stat().st_size, CacheReason.SOURCE_DOWN,
                    detail=describe_error(exc),
                )
            raise SourceUnavailable(
                f"{dataset.url}: download interrompido ({describe_error(exc)})"
            ) from exc

        problem = _validate(dataset, temporary)
        if problem:
            temporary.unlink(missing_ok=True)
            # O cache NAO e atualizado: gravar o validador de uma pagina de erro
            # faria toda execucao seguinte receber 304 e servir o lixo.
            if dest.exists():
                return FetchResult(
                    dataset, dest, True, dest.stat().st_size, CacheReason.SOURCE_DOWN,
                    detail=f"resposta inválida ({problem}); mantido o arquivo anterior",
                )
            raise SourceUnavailable(f"{dataset.filename} {problem}")

        temporary.replace(dest)
    finally:
        response.close()

    cache[dataset.key] = {
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "size": dest.stat().st_size,
    }
    _save_cache(config, cache)

    return FetchResult(dataset, dest, False, dest.stat().st_size, CacheReason.DOWNLOADED)


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
