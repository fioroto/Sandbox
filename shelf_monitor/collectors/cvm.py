"""Radar CVM: fundos registrados por gestoras ligadas as casas monitoradas.

Fonte primaria (pos-CVM 175): registro_fundo_classe.zip, com registro_fundo.csv
(todos os fundos do SGF), registro_classe.csv e registro_subclasse.csv.
Fallback: cad_fi.csv (legado), que ainda traz CLASSE/CLASSE_ANBIMA.

O valor competitivo esta em `Situacao`: um fundo em FASE PRE-OPERACIONAL foi
registrado mas ainda nao lancado - e o sinal antecipado da estrategia da casa.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from shelf_monitor.config import (
    CVM_CAD_FI_URL,
    CVM_REGISTRO_ZIP_URL,
    WATCHLIST,
    Config,
    Watch,
)
from shelf_monitor.http import SourceUnavailable, build_session, get
from shelf_monitor.models import CvmFund, CvmSnapshot, SnapshotStatus
from shelf_monitor.normalize import (
    cnpj_root,
    normalize_cnpj,
    normalize_text,
    parse_number,
)

# Arquivos da CVM sao ISO-8859-1 com separador ';'. A ordem aqui importa e nao
# e a intuitiva: latin-1 decodifica *qualquer* byte sem erro, entao tentar
# utf-8 depois dele seria codigo morto. Com utf-8 primeiro, um arquivo latin-1
# falha de verdade e cai para latin-1; um arquivo utf-8 e lido corretamente em
# vez de virar mojibake silencioso (que zeraria o sinal pre-operacional).
_ENCODINGS = ("utf-8-sig", "iso-8859-1")
_DELIMITER = ";"
_CHUNK = 1 << 20


def _describe(exc: BaseException) -> str:
    if isinstance(exc, SourceUnavailable):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


class WatchlistMatcher:
    """Decide se um gestor pertence a alguma casa monitorada.

    Duas vias: raiz de CNPJ (precisa, preenchida apos `watchlist-discover`) e
    regex sobre o nome (rede de seguranca para gestoras novas da casa).
    """

    def __init__(self, watchlist: dict[str, Watch] | None = None):
        self.watchlist = watchlist or WATCHLIST
        self._patterns: dict[str, list[re.Pattern]] = {
            house: [re.compile(p) for p in watch.name_patterns]
            for house, watch in self.watchlist.items()
        }
        self._roots: dict[str, set[str]] = {
            house: set(watch.cnpj_roots) for house, watch in self.watchlist.items()
        }

    def match(
        self, manager_name: str | None, manager_cnpj: str | None
    ) -> tuple[str, str] | None:
        """Devolve (house, motivo_do_match) ou None."""
        root = cnpj_root(manager_cnpj)
        if root:
            for house, roots in self._roots.items():
                if root in roots:
                    return house, f"cnpj_root:{root}"

        normalized = normalize_text(manager_name)
        if not normalized:
            return None
        for house, patterns in self._patterns.items():
            for pattern in patterns:
                if pattern.search(normalized):
                    return house, f"name:{pattern.pattern}"
        return None


def _decode_rows(raw: bytes) -> list[dict]:
    """Le um CSV da CVM tentando os encodings conhecidos."""
    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        reader = csv.DictReader(io.StringIO(text), delimiter=_DELIMITER)
        rows = list(reader)
        if rows and len(rows[0]) > 1:
            return rows
    if last_error:
        raise SourceUnavailable(f"não foi possível decodificar o CSV: {last_error}")
    raise SourceUnavailable("CSV da CVM vazio ou com separador inesperado")


def _get(row: dict, *names: str) -> str | None:
    """Busca uma coluna tolerando variacoes de caixa/acento entre versoes."""
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name]).strip()
    lowered = {normalize_text(k): v for k, v in row.items() if k}
    for name in names:
        key = normalize_text(name)
        value = lowered.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def download(config: Config, url: str, dest: Path) -> Path:
    """Baixa em streaming para disco - os arquivos da CVM tem dezenas de MB."""
    session = build_session(config)
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = get(session, url, config, stream=True)
    with dest.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=_CHUNK):
            if chunk:
                handle.write(chunk)
    return dest


class CvmCollector:
    """Baixa o cadastro da CVM e devolve so os fundos da watchlist."""

    def __init__(self, config: Config, matcher: WatchlistMatcher | None = None):
        self.config = config
        self.matcher = matcher or WatchlistMatcher()
        self.source_urls: list[str] = []

    def collect(self, local_zip: Path | None = None) -> CvmSnapshot:
        started = datetime.now()
        try:
            funds = self._collect_registro(local_zip)
        except Exception as primary_error:  # noqa: BLE001
            # Qualquer falha na fonte primaria tenta o cadastro legado - o modo
            # de falha mais provavel e um download corrompido (BadZipFile), que
            # nao chega aqui como SourceUnavailable.
            try:
                funds = self._collect_cad_fi()
            except Exception as fallback_error:  # noqa: BLE001
                return CvmSnapshot(
                    collected_at=started,
                    status=SnapshotStatus.UNAVAILABLE,
                    source_urls=list(self.source_urls),
                    error=(
                        f"registro_fundo_classe: {_describe(primary_error)} | "
                        f"cad_fi: {_describe(fallback_error)}"
                    ),
                )

        return CvmSnapshot(
            collected_at=started,
            status=SnapshotStatus.OK if funds else SnapshotStatus.PARTIAL,
            funds=funds,
            source_urls=list(self.source_urls),
            error=None if funds else "nenhum fundo casou com a watchlist",
        )

    # --- fonte primaria: registro_fundo_classe.zip ---------------------------

    def _collect_registro(self, local_zip: Path | None = None) -> list[CvmFund]:
        downloaded = local_zip is None
        if local_zip is not None:
            path = local_zip
        else:
            path = self.config.cvm_raw_dir / "registro_fundo_classe.zip"
            download(self.config, CVM_REGISTRO_ZIP_URL, path)
            self.source_urls.append(CVM_REGISTRO_ZIP_URL)

        if not zipfile.is_zipfile(path):
            if downloaded:
                # Um portal que devolve HTTP 200 com pagina de bloqueio grava um
                # "zip" que nao e zip; guarda-lo faria a proxima execucao reusar
                # o lixo em vez de baixar de novo.
                path.unlink(missing_ok=True)
            raise SourceUnavailable(
                f"{path.name} não é um zip válido (download corrompido ou "
                "resposta de bloqueio)"
            )

        with zipfile.ZipFile(path) as archive:
            names = {n.lower(): n for n in archive.namelist()}
            fundo_name = next(
                (n for key, n in names.items() if "registro_fundo" in key and key.endswith(".csv")),
                None,
            )
            if not fundo_name:
                raise SourceUnavailable(
                    f"registro_fundo.csv ausente no zip (contém: {list(names.values())})"
                )
            fundo_rows = _decode_rows(archive.read(fundo_name))

            classe_name = next(
                (
                    n
                    for key, n in names.items()
                    if "registro_classe" in key and key.endswith(".csv")
                ),
                None,
            )
            classes = (
                self._index_classes(_decode_rows(archive.read(classe_name)))
                if classe_name
                else {}
            )

        return self._filter_rows(fundo_rows, classes)

    def _index_classes(self, rows: list[dict]) -> dict[str, list[str]]:
        """Mapeia CNPJ do fundo -> classes CVM declaradas."""
        index: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            cnpj = normalize_cnpj(_get(row, "CNPJ_Fundo", "CNPJ_Classe"))
            classe = _get(row, "Classe", "Tipo_Classe", "Classificacao")
            if cnpj and classe and classe not in index[cnpj]:
                index[cnpj].append(classe)
        return dict(index)

    def _filter_rows(
        self, rows: list[dict], classes: dict[str, list[str]]
    ) -> list[CvmFund]:
        funds: list[CvmFund] = []
        for row in rows:
            manager = _get(row, "Gestor", "GESTOR")
            manager_cnpj = _get(row, "CPF_CNPJ_Gestor", "CPF_CNPJ_GESTOR")
            matched = self.matcher.match(manager, manager_cnpj)
            if not matched:
                continue

            house, reason = matched
            cnpj = normalize_cnpj(_get(row, "CNPJ_Fundo", "CNPJ_FUNDO", "CNPJ"))
            name = _get(row, "Denominacao_Social", "DENOM_SOCIAL", "Denominacao")
            if not cnpj or not name:
                continue

            funds.append(
                CvmFund(
                    cnpj=cnpj,
                    name=name,
                    house=house,
                    matched_by=reason,
                    fund_type=_get(row, "Tipo_Fundo", "TP_FUNDO"),
                    manager_name=manager,
                    manager_cnpj=normalize_cnpj(manager_cnpj),
                    admin_name=_get(row, "Administrador", "ADMIN"),
                    situation=_get(row, "Situacao", "SIT"),
                    situation_started_at=_get(row, "Data_Inicio_Situacao", "DT_INI_SIT"),
                    registered_at=_get(row, "Data_Registro", "DT_REG"),
                    adapted_rcvm175_at=_get(row, "Data_Adaptacao_RCVM175"),
                    cvm_classes=classes.get(cnpj, []),
                    pl=parse_number(_get(row, "Patrimonio_Liquido", "VL_PATRIM_LIQ")),
                )
            )
        return funds

    # --- fallback: cad_fi.csv ------------------------------------------------

    def _collect_cad_fi(self) -> list[CvmFund]:
        path = self.config.cvm_raw_dir / "cad_fi.csv"
        download(self.config, CVM_CAD_FI_URL, path)
        self.source_urls.append(CVM_CAD_FI_URL)

        rows = _decode_rows(path.read_bytes())
        classes: dict[str, list[str]] = {}
        for row in rows:
            cnpj = normalize_cnpj(_get(row, "CNPJ_FUNDO", "CNPJ_Fundo"))
            classe = _get(row, "CLASSE", "CLASSE_ANBIMA")
            if cnpj and classe:
                classes.setdefault(cnpj, []).append(classe)
        return self._filter_rows(rows, classes)


def discover_managers(
    rows: list[dict], query: str, limit: int = 50
) -> list[tuple[str, str | None, int]]:
    """Lista gestores distintos que contem `query`, para refinar a watchlist.

    Devolve (nome, cnpj, quantidade_de_fundos) ordenado por quantidade.
    """
    needle = normalize_text(query)
    counter: dict[tuple[str, str | None], int] = defaultdict(int)
    for row in rows:
        manager = _get(row, "Gestor", "GESTOR")
        if not manager or needle not in normalize_text(manager):
            continue
        cnpj = normalize_cnpj(_get(row, "CPF_CNPJ_Gestor", "CPF_CNPJ_GESTOR"))
        counter[(manager, cnpj)] += 1

    ranked = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)
    return [(name, cnpj, count) for (name, cnpj), count in ranked[:limit]]


def load_registro_rows(config: Config, local_zip: Path | None = None) -> list[dict]:
    """Le as linhas brutas de registro_fundo.csv (usado por `watchlist-discover`)."""
    path = local_zip or (config.cvm_raw_dir / "registro_fundo_classe.zip")
    if not path.exists():
        download(config, CVM_REGISTRO_ZIP_URL, path)

    with zipfile.ZipFile(path) as archive:
        names = {n.lower(): n for n in archive.namelist()}
        fundo_name = next(
            (n for key, n in names.items() if "registro_fundo" in key and key.endswith(".csv")),
            None,
        )
        if not fundo_name:
            raise SourceUnavailable("registro_fundo.csv ausente no zip")
        return _decode_rows(archive.read(fundo_name))
