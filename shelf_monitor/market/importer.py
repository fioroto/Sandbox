"""Orquestracao da importacao da base de mercado.

Um comando, tres downloads, cinco tabelas. Idempotente: rodar de novo sem
mudanca na origem nao rebaixa nada e reescreve as mesmas tabelas.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market.datasets import CAD_FI, EXTRATO, REGISTRO, Dataset
from shelf_monitor.market.export import write_database
from shelf_monitor.market.fees import parse_fees
from shelf_monitor.market.fetch import fetch, read_member
from shelf_monitor.market.parse import (
    legacy_class_fields,
    parse_classes,
    parse_funds,
    parse_legacy_funds,
    parse_subclasses,
)
from shelf_monitor.market.models import MarketDatabase


def _resolve(
    config: Config,
    dataset: Dataset,
    local_dir: Path | None,
    force: bool,
    database: MarketDatabase,
) -> Path | None:
    """Arquivo local, se houver; senao baixa. None quando a fonte falhou."""
    if local_dir is not None:
        path = local_dir / dataset.filename
        if not path.exists():
            database.notes.append(f"{dataset.key}: ausente em {local_dir}")
            return None
        database.sources[dataset.key] = str(path)
        return path

    try:
        result = fetch(config, dataset, force=force)
    except SourceUnavailable as exc:
        database.notes.append(f"{dataset.key}: {exc}")
        return None

    database.sources[dataset.key] = dataset.url
    if result.from_cache:
        database.notes.append(f"{dataset.key}: sem mudanca na origem, reusado do disco")
    return result.path


def import_market(
    config: Config,
    local_dir: Path | None = None,
    force: bool = False,
    skip_fees: bool = False,
) -> MarketDatabase:
    """Importa cadastro (+ taxas) e devolve a base em memoria."""
    database = MarketDatabase(imported_at=date.today())

    # --- hierarquia RCVM 175 -------------------------------------------------
    registro_path = _resolve(config, REGISTRO, local_dir, force, database)
    if registro_path is not None:
        funds, unknown = parse_funds(read_member(registro_path, "registro_fundo"))
        database.funds.extend(funds)
        if unknown:
            database.unknown_columns["fundos"] = unknown

        classes, unknown = parse_classes(read_member(registro_path, "registro_classe"))
        database.classes.extend(classes)
        if unknown:
            database.unknown_columns["classes"] = unknown

        try:
            raw = read_member(registro_path, "registro_subclasse")
        except SourceUnavailable as exc:
            # Nem todo zip traz subclasse; a base continua util sem ela.
            database.notes.append(f"subclasses: {exc}")
        else:
            subclasses, unknown = parse_subclasses(raw)
            database.subclasses.extend(subclasses)
            if unknown:
                database.unknown_columns["subclasses"] = unknown

    # --- fundos ainda nao adaptados ------------------------------------------
    # Uniao, nao fallback: desde out/2024 os adaptados sairam do cad_fi, entao
    # os dois arquivos cobrem partes diferentes do mercado.
    legacy_path = _resolve(config, CAD_FI, local_dir, force, database)
    if legacy_path is not None:
        raw = legacy_path.read_bytes()
        adapted = {fund.cnpj for fund in database.funds if fund.cnpj}
        extra = legacy_class_fields(raw)
        added = 0
        for fund in parse_legacy_funds(raw):
            if fund.cnpj in adapted:
                # Ja veio pelo registro: o cadastro novo e a fonte melhor.
                continue
            fund.legacy_fields = extra.get(fund.cnpj, {})
            database.funds.append(fund)
            added += 1
        database.notes.append(f"cad_fi: {added} fundos nao adaptados adicionados")

    # --- taxas ---------------------------------------------------------------
    if not skip_fees:
        extrato_path = _resolve(config, EXTRATO, local_dir, force, database)
        if extrato_path is not None:
            # Streaming: o extrato nao cabe confortavelmente em memoria.
            database.fees.update(parse_fees(extrato_path))
    else:
        database.notes.append("extrato: pulado por --skip-fees")

    if not database.funds and not database.classes:
        database.notes.append(
            "nenhuma fonte respondeu - a base nao foi gerada"
        )

    return database


def run_import(
    config: Config,
    local_dir: Path | None = None,
    force: bool = False,
    skip_fees: bool = False,
) -> tuple[MarketDatabase, dict[str, Path]]:
    """Importa e grava. Devolve (base, caminhos escritos)."""
    database = import_market(config, local_dir, force, skip_fees)
    if not database.funds and not database.classes:
        return database, {}
    return database, write_database(config, database)
