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
    MarketClass,
    MarketDatabase,
    MarketFund,
    Origin,
)
from shelf_monitor.market.fees import resolve_admin_fee
from shelf_monitor.normalize import format_cnpj, parse_number

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
    "has_entry_fee": "tem_taxa_entrada",
    "entry_fee_pct": "taxa_entrada_pct",
    "entry_fee_amount": "taxa_entrada_reais",
    "has_exit_fee": "tem_taxa_saida",
    "exit_fee_pct": "taxa_saida_pct",
    "exit_fee_amount": "taxa_saida_reais",
    "custody_fee_max": "taxa_custodia_max",
    "min_initial": "aplicacao_inicial_minima",
    "redemption_quotation_days": "dias_cotizacao_resgate",
    "redemption_payment_days": "dias_pagamento_resgate",
    "target_audience": "publico_alvo",
    "investment_policy": "politica_investimento",
    "long_term_taxation": "tributacao_longo_prazo",
    "credit_private": "credito_privado_autorizado",
    "credit_private_max": "credito_privado_max_pct",
    "foreign": "exterior_autorizado",
    "foreign_max": "exterior_max_pct",
    "derivatives": "derivativos_autorizado",
    "leverage": "alavancagem_autorizada",
    "leverage_factor": "alavancagem_fator",
    "mirror_fund": "fundo_espelho",
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


def _fee_columns(fees) -> dict:
    """Colunas de custo e mandato de uma classe, para o consolidado.

    Existe como helper porque as duas pernas de `_consolidated_rows` (classe
    RCVM 175 e fundo legado) precisam das mesmas colunas, e manter dois blocos
    iguais a mao foi como `codigo_cvm` acabou numa perna so.

    Mandato e o que o regulamento AUTORIZA, nunca o que a carteira carrega:
    medir exposicao realizada exigiria a CDA, que nao esta no escopo.
    """
    return {
        "tem_taxa_entrada": _cell(fees.has_entry_fee if fees else None),
        "taxa_entrada_pct": _cell(fees.entry_fee_pct if fees else None),
        "taxa_entrada_reais": _cell(fees.entry_fee_amount if fees else None),
        "tem_taxa_saida": _cell(fees.has_exit_fee if fees else None),
        "taxa_saida_pct": _cell(fees.exit_fee_pct if fees else None),
        "taxa_saida_reais": _cell(fees.exit_fee_amount if fees else None),
        "taxa_custodia_max": _cell(fees.custody_fee_max if fees else None),
        "parametro_performance": _cell(
            fees.performance_fee_benchmark if fees else None
        ),
        "taxa_data_base": _cell(fees.reference_date if fees else None),
        "credito_privado_autorizado": _cell(fees.credit_private if fees else None),
        "credito_privado_max_pct": _cell(fees.credit_private_max if fees else None),
        "exterior_autorizado": _cell(fees.foreign if fees else None),
        "exterior_max_pct": _cell(fees.foreign_max if fees else None),
        "derivativos_autorizado": _cell(fees.derivatives if fees else None),
        "alavancagem_autorizada": _cell(fees.leverage if fees else None),
        "alavancagem_fator": _cell(fees.leverage_factor if fees else None),
        "fundo_espelho": _cell(fees.mirror_fund if fees else None),
    }


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
                # Codigo CVM da classe - a chave que a distribuicao usa quando
                # anuncia o produto sem CNPJ.
                "codigo_cvm": item.cvm_code or "",
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
                "codigo_cvm_fundo": (fund.cvm_code if fund else "") or "",
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
                **_fee_columns(fees),
                "data_registro": _cell(item.registered_at or (fund.registered_at if fund else None)),
            }
        )

    for fund in database.funds:
        if fund.origin != Origin.LEGACY:
            continue
        fees = database.fees.get(fund.cnpj) if fund.cnpj else None
        extra = fund.legacy_fields or {}
        legacy_fee, legacy_unit, _ = resolve_admin_fee(extra.get("admin_fee"))
        rows.append(
            {
                "origem": Origin.LEGACY.value,
                "cnpj": format_cnpj(fund.cnpj) if fund.cnpj else "",
                "denominacao": fund.name,
                # No legado nao ha classe: o codigo do proprio fundo ocupa a
                # coluna, e `codigo_cvm_fundo` fica vazio para nao duplica-lo.
                "codigo_cvm": fund.cvm_code or "",
                "tipo": fund.fund_type or "",
                "situacao": fund.situation or "",
                "classificacao_cvm": extra.get("cvm_classification") or "",
                "classificacao_anbima": extra.get("anbima_classification") or "",
                "indicador_desempenho": extra.get("benchmark") or "",
                "publico_alvo": extra.get("target_audience") or "",
                "exclusivo": extra.get("exclusive") or "",
                "forma_condominio": extra.get("condominium") or "",
                "tributacao_longo_prazo": extra.get("long_term_taxation") or "",
                "classe_esg": "",
                "patrimonio_liquido": _cell(fund.net_worth),
                "data_patrimonio_liquido": _cell(fund.net_worth_at),
                "gestor": fund.manager_name or "",
                "cnpj_gestor": fund.manager_cnpj or "",
                "administrador": fund.admin_name or "",
                "custodiante": extra.get("custodian_name") or "",
                "fundo_denominacao": fund.name,
                "cnpj_fundo": format_cnpj(fund.cnpj) if fund.cnpj else "",
                "codigo_cvm_fundo": "",
                "id_fundo": fund.registro_id,
                "id_classe": "",
                "subclasses": 0,
                # O legado nunca passa pelo extrato, entao a taxa dele tambem
                # precisa ser resolvida aqui - senao a coluna mistura float com
                # texto cru e nao da nem para ordenar.
                "taxa_adm": _cell(legacy_fee if fees is None else fees.admin_fee),
                "taxa_adm_unidade": _cell(
                    legacy_unit if fees is None else fees.admin_fee_unit
                ),
                "taxa_performance": _cell(
                    fees.performance_fee
                    if fees
                    else parse_number(extra.get("performance_fee"))
                ),
                "aplicacao_inicial_minima": _cell(fees.min_initial if fees else None),
                "dias_cotizacao_resgate": _cell(
                    fees.redemption_quotation_days if fees else None
                ),
                "dias_pagamento_resgate": _cell(
                    fees.redemption_payment_days if fees else None
                ),
                **_fee_columns(fees),
                "data_registro": _cell(fund.registered_at),
            }
        )

    return rows


def _hierarchy_rows(database: MarketDatabase) -> list[dict]:
    """Uma linha por subclasse, descendo a hierarquia inteira.

    O consolidado para na classe, mas e na subclasse que moram publico-alvo e
    condicoes comerciais distintas - duas subclasses da mesma classe partilham
    carteira e CNPJ e ainda assim sao produtos diferentes na prateleira.

    Onde a classe nao tem subclasse, a linha e a propria classe (a coluna
    `nivel` diz qual e o caso), para que a tabela cubra o mercado inteiro sem
    exigir um union do lado de quem consome.
    """
    funds = {f.registro_id: f for f in database.funds}
    subclasses = database.subclasses_by_class()
    rows: list[dict] = []

    def base_row(fund: MarketFund | None, item: MarketClass | None) -> dict:
        fees = database.fees.get(item.cnpj) if item and item.cnpj else None
        return {
            "nivel": "",
            "cnpj_negociado": format_cnpj(item.cnpj) if item and item.cnpj else "",
            "denominacao": "",
            "fundo_id": fund.registro_id if fund else "",
            "fundo_cnpj": format_cnpj(fund.cnpj) if fund and fund.cnpj else "",
            "fundo_denominacao": fund.name if fund else "",
            "fundo_tipo": (fund.fund_type if fund else "") or "",
            "fundo_situacao": (fund.situation if fund else "") or "",
            "fundo_data_registro": _cell(fund.registered_at if fund else None),
            "fundo_adaptado_rcvm175": _cell(
                fund.adapted_at if fund and fund.adapted_at else None
            ),
            "gestor": (fund.manager_name if fund else "") or "",
            "gestor_cnpj": (fund.manager_cnpj if fund else "") or "",
            "administrador": (fund.admin_name if fund else "") or "",
            "classe_id": item.classe_id if item else "",
            "classe_cnpj": format_cnpj(item.cnpj) if item and item.cnpj else "",
            "classe_denominacao": item.name if item else "",
            "classe_tipo": (item.class_type if item else "") or "",
            "classe_situacao": (item.situation if item else "") or "",
            "classificacao_cvm": (item.cvm_classification if item else "") or "",
            "classificacao_anbima": (item.anbima_classification if item else "") or "",
            "indicador_desempenho": (item.benchmark if item else "") or "",
            "classe_publico_alvo": (item.target_audience if item else "") or "",
            "classe_exclusivo": (item.exclusive if item else "") or "",
            "classe_condominio": (item.condominium if item else "") or "",
            "classe_tributacao_longo_prazo": (item.long_term_taxation if item else "") or "",
            "classe_esg": (item.esg if item else "") or "",
            "classe_patrimonio_liquido": _cell(item.net_worth if item else None),
            "custodiante": (item.custodian_name if item else "") or "",
            "subclasse_id": "",
            "subclasse_denominacao": "",
            "subclasse_situacao": "",
            "subclasse_publico_alvo": "",
            "subclasse_exclusivo": "",
            "subclasse_condominio": "",
            "subclasse_previdenciario": "",
            "subclasse_exclusivo_inr": "",
            "subclasse_exclusivo_previdencia": "",
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
        }

    for item in database.classes:
        fund = funds.get(item.fundo_id)
        children = subclasses.get(item.classe_id, [])

        if not children:
            row = base_row(fund, item)
            row["nivel"] = "classe"
            row["denominacao"] = item.name
            rows.append(row)
            continue

        for child in children:
            row = base_row(fund, item)
            row.update(
                {
                    "nivel": "subclasse",
                    "denominacao": child.name,
                    "subclasse_id": child.subclasse_id,
                    "subclasse_denominacao": child.name,
                    "subclasse_situacao": child.situation or "",
                    # A subclasse pode restringir o publico da classe; quando
                    # nao diz nada, vale o da classe.
                    "subclasse_publico_alvo": child.target_audience or "",
                    "subclasse_exclusivo": child.exclusive or "",
                    "subclasse_condominio": child.condominium or "",
                    "subclasse_previdenciario": child.pension or "",
                    "subclasse_exclusivo_inr": child.foreign_investor_only or "",
                    "subclasse_exclusivo_previdencia": child.pension_plan_only or "",
                }
            )
            rows.append(row)

    # Fundos legados nao tem classe nem subclasse: a unidade e o proprio fundo.
    for fund in database.funds:
        if fund.origin != Origin.LEGACY:
            continue
        extra = fund.legacy_fields or {}
        legacy_fee, legacy_unit, _ = resolve_admin_fee(extra.get("admin_fee"))
        row = base_row(fund, None)
        row.update(
            {
                "nivel": "fundo_legado",
                "cnpj_negociado": format_cnpj(fund.cnpj) if fund.cnpj else "",
                "denominacao": fund.name,
                "classificacao_cvm": extra.get("cvm_classification") or "",
                "classificacao_anbima": extra.get("anbima_classification") or "",
                "classe_condominio": extra.get("condominium") or "",
                "classe_exclusivo": extra.get("exclusive") or "",
                "classe_patrimonio_liquido": _cell(fund.net_worth),
                "custodiante": extra.get("custodian_name") or "",
                "taxa_adm": _cell(legacy_fee),
                "taxa_adm_unidade": _cell(legacy_unit),
                "taxa_performance": _cell(parse_number(extra.get("performance_fee"))),
            }
        )
        rows.append(row)

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

    hierarchy = _hierarchy_rows(database)
    path = target / "hierarquia.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(hierarchy[0]) if hierarchy else ["nivel"],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(hierarchy)
    written["hierarquia"] = path
    counts["hierarquia"] = len(hierarchy)

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
