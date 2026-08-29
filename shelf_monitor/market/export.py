"""Escrita da base de mercado em CSV.

Cinco arquivos: tres tabelas normalizadas espelhando a hierarquia da RCVM 175,
uma de taxas, e um consolidado. O consolidado existe porque as tabelas
normalizadas sao corretas mas ruins de usar sozinhas - e ele que responde
"todos os fundos do mercado com classificacao e taxa" sem exigir um join.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.models import (
    ClassFees,
    MarketClass,
    MarketDatabase,
    MarketFund,
    MarketSubclass,
    Origin,
)
from shelf_monitor.normalize import format_cnpj

# Cabecalhos em portugues: a planilha e lida por gente, nao so por codigo.
_FUND_HEADERS = {
    "registro_id": "id_fundo",
    "cnpj": "cnpj_fundo",
    "name": "denominacao",
    "origin": "origem",
    "cvm_code": "codigo_cvm",
    "fund_type": "tipo_fundo",
    "situation": "situacao",
    "situation_started_at": "inicio_situacao",
    "registered_at": "data_registro",
    "constituted_at": "data_constituicao",
    "cancelled_at": "data_cancelamento",
    "adapted_at": "data_adaptacao_rcvm175",
    "net_worth": "patrimonio_liquido",
    "net_worth_at": "data_patrimonio_liquido",
    "director": "diretor",
    "admin_name": "administrador",
    "admin_cnpj": "cnpj_administrador",
    "manager_name": "gestor",
    "manager_cnpj": "cnpj_gestor",
    "manager_person_type": "tipo_pessoa_gestor",
}

_CLASS_HEADERS = {
    "classe_id": "id_classe",
    "fundo_id": "id_fundo",
    "cnpj": "cnpj_classe",
    "name": "denominacao",
    "cvm_code": "codigo_cvm",
    "class_type": "tipo_classe",
    "situation": "situacao",
    "situation_started_at": "inicio_situacao",
    "registered_at": "data_registro",
    "constituted_at": "data_constituicao",
    "started_at": "data_inicio",
    "cvm_classification": "classificacao_cvm",
    "anbima_classification": "classificacao_anbima",
    "benchmark": "indicador_desempenho",
    "share_class": "classe_cotas",
    "long_term_taxation": "tributacao_longo_prazo",
    "investment_entity": "entidade_investimento",
    "full_foreign_allowed": "permite_100pc_exterior",
    "esg": "classe_esg",
    "condominium": "forma_condominio",
    "exclusive": "exclusivo",
    "target_audience": "publico_alvo",
    "net_worth": "patrimonio_liquido",
    "net_worth_at": "data_patrimonio_liquido",
    "auditor_name": "auditor",
    "auditor_cnpj": "cnpj_auditor",
    "custodian_name": "custodiante",
    "custodian_cnpj": "cnpj_custodiante",
    "controller_name": "controlador",
    "controller_cnpj": "cnpj_controlador",
}

_SUBCLASS_HEADERS = {
    "subclasse_id": "id_subclasse",
    "classe_id": "id_classe",
    "name": "denominacao",
    "cvm_code": "codigo_cvm",
    "situation": "situacao",
    "situation_started_at": "inicio_situacao",
    "constituted_at": "data_constituicao",
    "started_at": "data_inicio",
    "condominium": "forma_condominio",
    "exclusive": "exclusivo",
    "target_audience": "publico_alvo",
    "pension": "previdenciario",
    "foreign_investor_only": "exclusivo_inr",
    "pension_plan_only": "exclusivo_previdencia_complementar",
}

_FEE_HEADERS = {
    "cnpj": "cnpj_classe",
    "reference_date": "data_base",
    "admin_fee": "taxa_adm",
    "admin_fee_unit": "taxa_adm_unidade",
    "admin_fee_raw": "taxa_adm_original",
    "performance_fee": "taxa_performance",
    "performance_fee_benchmark": "parametro_performance",
    "has_performance_fee": "tem_taxa_performance",
    "entry_fee": "taxa_entrada",
    "exit_fee": "taxa_saida",
    "min_initial": "aplicacao_inicial_minima",
    "min_balance": "saldo_minimo",
    "redemption_quotation_days": "dias_cotizacao_resgate",
    "redemption_payment_days": "dias_pagamento_resgate",
    "target_audience": "publico_alvo",
    "investment_policy": "politica_investimento",
    "long_term_taxation": "tributacao_longo_prazo",
}


def _cell(value: object) -> str:
    """Serializa um campo para CSV sem inventar formato."""
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "S" if value else "N"
    if hasattr(value, "value"):  # Enum
        return str(value.value)
    return str(value)


def _write_table(path: Path, headers: dict[str, str], rows: list) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        # utf-8-sig: sem o BOM o Excel abre acentuacao quebrada no Windows.
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(headers.values())
        for row in rows:
            writer.writerow([_cell(getattr(row, name)) for name in headers])
    return len(rows)


def _consolidated_rows(database: MarketDatabase) -> list[dict]:
    """Uma linha por classe; fundos legados entram com a propria linha.

    Um fundo adaptado nao vira linha: quem tem CNPJ, PL e politica e a classe.
    Um fundo legado nao tem classe, entao ele mesmo e a unidade.
    """
    funds = {f.registro_id: f for f in database.funds}
    subclass_count = {
        key: len(value) for key, value in database.subclasses_by_class().items()
    }
    rows: list[dict] = []

    for item in database.classes:
        fund = funds.get(item.fundo_id)
        fees = database.fees.get(item.cnpj) if item.cnpj else None
        rows.append(
            {
                "origem": Origin.RCVM175.value,
                "cnpj": format_cnpj(item.cnpj) if item.cnpj else "",
                "denominacao": item.name,
                "tipo": item.class_type or "",
                "situacao": item.situation or "",
                "classificacao_cvm": item.cvm_classification or "",
                "classificacao_anbima": item.anbima_classification or "",
                "indicador_desempenho": item.benchmark or "",
                "publico_alvo": item.target_audience or "",
                "exclusivo": item.exclusive or "",
                "forma_condominio": item.condominium or "",
                "tributacao_longo_prazo": item.long_term_taxation or "",
                "classe_esg": item.esg or "",
                "patrimonio_liquido": _cell(item.net_worth),
                "data_patrimonio_liquido": _cell(item.net_worth_at),
                "gestor": (fund.manager_name if fund else "") or "",
                "cnpj_gestor": (fund.manager_cnpj if fund else "") or "",
                "administrador": (fund.admin_name if fund else "") or "",
                "custodiante": item.custodian_name or "",
                "fundo_denominacao": (fund.name if fund else "") or "",
                "cnpj_fundo": format_cnpj(fund.cnpj) if fund and fund.cnpj else "",
                "id_fundo": item.fundo_id,
                "id_classe": item.classe_id,
                "subclasses": subclass_count.get(item.classe_id, 0),
                "taxa_adm": _cell(fees.admin_fee if fees else None),
                "taxa_adm_unidade": _cell(fees.admin_fee_unit if fees else None),
                "taxa_performance": _cell(fees.performance_fee if fees else None),
                "aplicacao_inicial_minima": _cell(fees.min_initial if fees else None),
                "dias_cotizacao_resgate": _cell(
                    fees.redemption_quotation_days if fees else None
                ),
                "dias_pagamento_resgate": _cell(
                    fees.redemption_payment_days if fees else None
                ),
                "data_registro": _cell(item.registered_at or (fund.registered_at if fund else None)),
            }
        )

    for fund in database.funds:
        if fund.origin != Origin.LEGACY:
            continue
        fees = database.fees.get(fund.cnpj) if fund.cnpj else None
        extra = fund.legacy_fields or {}
        rows.append(
            {
                "origem": Origin.LEGACY.value,
                "cnpj": format_cnpj(fund.cnpj) if fund.cnpj else "",
                "denominacao": fund.name,
                "tipo": fund.fund_type or "",
                "situacao": fund.situation or "",
                "classificacao_cvm": extra.get("cvm_classification") or "",
                "classificacao_anbima": extra.get("anbima_classification") or "",
                "indicador_desempenho": "",
                "publico_alvo": "",
                "exclusivo": extra.get("exclusive") or "",
                "forma_condominio": extra.get("condominium") or "",
                "tributacao_longo_prazo": "",
                "classe_esg": "",
                "patrimonio_liquido": _cell(fund.net_worth),
                "data_patrimonio_liquido": _cell(fund.net_worth_at),
                "gestor": fund.manager_name or "",
                "cnpj_gestor": fund.manager_cnpj or "",
                "administrador": fund.admin_name or "",
                "custodiante": extra.get("custodian_name") or "",
                "fundo_denominacao": fund.name,
                "cnpj_fundo": format_cnpj(fund.cnpj) if fund.cnpj else "",
                "id_fundo": fund.registro_id,
                "id_classe": "",
                "subclasses": 0,
                "taxa_adm": _cell(fees.admin_fee if fees else extra.get("admin_fee")),
                "taxa_adm_unidade": _cell(fees.admin_fee_unit if fees else None),
                "taxa_performance": _cell(
                    fees.performance_fee if fees else extra.get("performance_fee")
                ),
                "aplicacao_inicial_minima": _cell(fees.min_initial if fees else None),
                "dias_cotizacao_resgate": _cell(
                    fees.redemption_quotation_days if fees else None
                ),
                "dias_pagamento_resgate": _cell(
                    fees.redemption_payment_days if fees else None
                ),
                "data_registro": _cell(fund.registered_at),
            }
        )

    return rows


def write_database(config: Config, database: MarketDatabase) -> dict[str, Path]:
    """Grava as cinco tabelas e o manifesto. Devolve os caminhos por chave."""
    target = config.market_dir
    target.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    counts: dict[str, int] = {}

    for key, headers, rows, filename in (
        ("fundos", _FUND_HEADERS, database.funds, "fundos.csv"),
        ("classes", _CLASS_HEADERS, database.classes, "classes.csv"),
        ("subclasses", _SUBCLASS_HEADERS, database.subclasses, "subclasses.csv"),
        ("taxas", _FEE_HEADERS, list(database.fees.values()), "taxas.csv"),
    ):
        path = target / filename
        counts[key] = _write_table(path, headers, rows)
        written[key] = path

    consolidated = _consolidated_rows(database)
    path = target / "mercado.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(consolidated[0]) if consolidated else ["cnpj"],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(consolidated)
    written["mercado"] = path
    counts["mercado"] = len(consolidated)

    manifest = {
        "imported_at": database.imported_at.isoformat(),
        "counts": {**database.counts(), **{f"linhas_{k}": v for k, v in counts.items()}},
        "sources": database.sources,
        # Coluna nova no arquivo da CVM que o modelo ainda nao le: e assim que
        # se descobre uma mudanca de schema sem quebrar a importacao.
        "unknown_columns": database.unknown_columns,
        "notes": database.notes,
    }
    manifest_path = target / "_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    written["manifest"] = manifest_path

    return written
