"""Retrato mensal do cadastro de fundos, gravado a cada importacao da base.

A CVM nao guarda historico de classe pos-RCVM 175: o `registro_classe` e
reescrito a cada publicacao. Medido em 28/09/2026, ele mantem 37 classes
canceladas, contra 52.713 fundos cancelados no `registro_fundo`. Troca de
gestora, de classificacao ou de publico-alvo, encerramento de classe e
lancamento nao se reconstroem depois: o mes sem retrato e historico perdido.
Por isso o retrato sai de toda importacao da base (`market`, e a importacao
automatica do `run`), e nao so do monitor da industria.

Uma linha por subclasse - ou pela classe, quando ela nao tem subclasse, ou pelo
fundo legado, que nao tem classe -, com os campos do fundo, da classe, da
subclasse e do extrato lado a lado. O CNPJ vai normalizado (sem mascara): o
arquivo e lido por codigo, para juntar com o informe e a carteira.

O arquivo e `data/historico/cadastro/AAAAMM.csv.gz`, versionado, porque e dado
primario. Sai deterministico de proposito: linhas ordenadas e gzip com
`mtime=0`, entao reimportar a mesma base no mesmo mes grava os mesmos bytes e o
git nao ve mudanca. A ultima importacao do mes vence.
"""
from __future__ import annotations

import csv
import gzip
import io
import os
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.models import (
    MarketClass,
    MarketDatabase,
    MarketFund,
    MarketSubclass,
    Origin,
)

COLUNAS = (
    "nivel",
    "classe_cnpj", "subclasse_id", "fundo_cnpj", "fundo_id", "classe_id",
    "fundo_tipo", "fundo_situacao", "fundo_inicio_situacao", "fundo_data_registro",
    "fundo_data_cancelamento", "fundo_adaptado_rcvm175",
    "gestor", "gestor_cnpj", "administrador", "administrador_cnpj",
    "classe_denominacao", "classe_tipo", "classe_situacao", "classe_inicio_situacao",
    "classe_data_registro", "classe_data_inicio",
    "classificacao_cvm", "classificacao_anbima", "indicador_desempenho",
    "classe_cotas", "tributacao_longo_prazo", "classe_esg", "permite_100pc_exterior",
    "entidade_investimento",
    "classe_publico_alvo", "classe_exclusivo", "classe_condominio",
    "subclasse_denominacao", "subclasse_situacao", "subclasse_inicio_situacao",
    "subclasse_publico_alvo", "subclasse_exclusivo", "subclasse_condominio",
    "subclasse_previdenciario", "subclasse_exclusivo_inr",
    "subclasse_exclusivo_previdencia",
    "classe_patrimonio_liquido", "classe_data_patrimonio_liquido",
    "taxa_adm", "taxa_adm_unidade", "taxa_performance", "aplicacao_inicial_minima",
    "dias_cotizacao_resgate", "dias_pagamento_resgate", "extrato_data_base",
    "extrato_publico_alvo", "fundo_espelho_declarado",
)


def pasta_do_cadastro(config: Config) -> Path:
    return config.historico_dir / "cadastro"


def caminho_do_retrato(config: Config, competencia: str) -> Path:
    """`competencia` em AAAAMM."""
    return pasta_do_cadastro(config) / f"{competencia}.csv.gz"


def competencias_com_retrato(config: Config) -> list[str]:
    """AAAAMM dos retratos gravados, do mais antigo ao mais novo."""
    pasta = pasta_do_cadastro(config)
    if not pasta.is_dir():
        return []
    return sorted(
        p.name[:6] for p in pasta.glob("*.csv.gz") if p.name[:6].isdigit()
    )


def _texto(valor: object) -> str:
    if valor is None:
        return ""
    if isinstance(valor, date):
        return valor.isoformat()
    if hasattr(valor, "value"):  # Enum
        return str(valor.value)
    if isinstance(valor, float):
        return repr(valor)
    return str(valor).strip()


def _linha_do_fundo(fundo: MarketFund | None) -> dict[str, str]:
    if fundo is None:
        return {}
    return {
        "fundo_cnpj": _texto(fundo.cnpj),
        "fundo_id": _texto(fundo.registro_id),
        "fundo_tipo": _texto(fundo.fund_type),
        "fundo_situacao": _texto(fundo.situation),
        "fundo_inicio_situacao": _texto(fundo.situation_started_at),
        "fundo_data_registro": _texto(fundo.registered_at),
        "fundo_data_cancelamento": _texto(fundo.cancelled_at),
        "fundo_adaptado_rcvm175": _texto(fundo.adapted_at),
        "gestor": _texto(fundo.manager_name),
        "gestor_cnpj": _texto(fundo.manager_cnpj),
        "administrador": _texto(fundo.admin_name),
        "administrador_cnpj": _texto(fundo.admin_cnpj),
    }


def _linha_da_classe(classe: MarketClass) -> dict[str, str]:
    return {
        "classe_cnpj": _texto(classe.cnpj),
        "classe_id": _texto(classe.classe_id),
        "classe_denominacao": _texto(classe.name),
        "classe_tipo": _texto(classe.class_type),
        "classe_situacao": _texto(classe.situation),
        "classe_inicio_situacao": _texto(classe.situation_started_at),
        "classe_data_registro": _texto(classe.registered_at),
        "classe_data_inicio": _texto(classe.started_at),
        "classificacao_cvm": _texto(classe.cvm_classification),
        "classificacao_anbima": _texto(classe.anbima_classification),
        "indicador_desempenho": _texto(classe.benchmark),
        "classe_cotas": _texto(classe.share_class),
        "tributacao_longo_prazo": _texto(classe.long_term_taxation),
        "classe_esg": _texto(classe.esg),
        "permite_100pc_exterior": _texto(classe.full_foreign_allowed),
        "entidade_investimento": _texto(classe.investment_entity),
        "classe_publico_alvo": _texto(classe.target_audience),
        "classe_exclusivo": _texto(classe.exclusive),
        "classe_condominio": _texto(classe.condominium),
        "classe_patrimonio_liquido": _texto(classe.net_worth),
        "classe_data_patrimonio_liquido": _texto(classe.net_worth_at),
    }


def _linha_da_subclasse(sub: MarketSubclass) -> dict[str, str]:
    return {
        "subclasse_id": _texto(sub.subclasse_id),
        "subclasse_denominacao": _texto(sub.name),
        "subclasse_situacao": _texto(sub.situation),
        "subclasse_inicio_situacao": _texto(sub.situation_started_at),
        "subclasse_publico_alvo": _texto(sub.target_audience),
        "subclasse_exclusivo": _texto(sub.exclusive),
        "subclasse_condominio": _texto(sub.condominium),
        "subclasse_previdenciario": _texto(sub.pension),
        "subclasse_exclusivo_inr": _texto(sub.foreign_investor_only),
        "subclasse_exclusivo_previdencia": _texto(sub.pension_plan_only),
    }


def _linha_do_extrato(database: MarketDatabase, cnpj: str | None) -> dict[str, str]:
    taxas = database.fees.get(cnpj) if cnpj else None
    if taxas is None:
        return {}
    return {
        "taxa_adm": _texto(taxas.admin_fee),
        "taxa_adm_unidade": _texto(taxas.admin_fee_unit),
        "taxa_performance": _texto(taxas.performance_fee),
        "aplicacao_inicial_minima": _texto(taxas.min_initial),
        "dias_cotizacao_resgate": _texto(taxas.redemption_quotation_days),
        "dias_pagamento_resgate": _texto(taxas.redemption_payment_days),
        "extrato_data_base": _texto(taxas.reference_date),
        "extrato_publico_alvo": _texto(taxas.target_audience),
        "fundo_espelho_declarado": _texto(taxas.mirror_fund),
    }


def linhas_do_retrato(database: MarketDatabase) -> list[dict[str, str]]:
    """As linhas do retrato, ja ordenadas."""
    fundos = {f.registro_id: f for f in database.funds}
    subclasses = database.subclasses_by_class()
    linhas: list[dict[str, str]] = []

    for classe in database.classes:
        base = {
            **_linha_do_fundo(fundos.get(classe.fundo_id)),
            **_linha_da_classe(classe),
            **_linha_do_extrato(database, classe.cnpj),
        }
        filhas = subclasses.get(classe.classe_id, [])
        if not filhas:
            linhas.append({**base, "nivel": "classe"})
            continue
        for sub in filhas:
            linhas.append({**base, **_linha_da_subclasse(sub), "nivel": "subclasse"})

    for fundo in database.funds:
        if fundo.origin != Origin.LEGACY:
            continue
        extra = fundo.legacy_fields or {}
        linhas.append({
            **_linha_do_fundo(fundo),
            **_linha_do_extrato(database, fundo.cnpj),
            "nivel": "fundo_legado",
            "classe_cnpj": _texto(fundo.cnpj),
            "classe_denominacao": _texto(fundo.name),
            "classe_tipo": _texto(fundo.fund_type),
            "classe_situacao": _texto(fundo.situation),
            "classificacao_cvm": _texto(extra.get("cvm_classification")),
            "classificacao_anbima": _texto(extra.get("anbima_classification")),
            "indicador_desempenho": _texto(extra.get("benchmark")),
            "classe_cotas": _texto(extra.get("share_class")),
            "tributacao_longo_prazo": _texto(extra.get("long_term_taxation")),
            "classe_publico_alvo": _texto(extra.get("target_audience")),
            "classe_exclusivo": _texto(extra.get("exclusive")),
            "classe_condominio": _texto(extra.get("condominium")),
            "classe_patrimonio_liquido": _texto(fundo.net_worth),
            "classe_data_patrimonio_liquido": _texto(fundo.net_worth_at),
        })

    linhas.sort(key=lambda l: (
        l.get("classe_cnpj", ""), l.get("subclasse_id", ""), l["nivel"], l.get("fundo_id", ""),
    ))
    return linhas


def gravar_retrato(config: Config, database: MarketDatabase) -> Path:
    """Grava o retrato da competencia da importacao e devolve o caminho."""
    competencia = database.imported_at.strftime("%Y%m")
    destino = caminho_do_retrato(config, competencia)
    destino.parent.mkdir(parents=True, exist_ok=True)

    texto = io.StringIO()
    writer = csv.DictWriter(
        texto, fieldnames=list(COLUNAS), delimiter=";", lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    for linha in linhas_do_retrato(database):
        writer.writerow({c: linha.get(c, "") for c in COLUNAS})

    bruto = io.BytesIO()
    # filename="" e mtime=0: o cabecalho do gzip nao carrega nome nem hora, e
    # o mesmo conteudo vira os mesmos bytes.
    with gzip.GzipFile(filename="", mode="wb", fileobj=bruto, mtime=0) as gz:
        gz.write(texto.getvalue().encode("utf-8"))
    conteudo = bruto.getvalue()
    if destino.is_file() and destino.read_bytes() == conteudo:
        return destino
    temporario = destino.with_suffix(destino.suffix + ".part")
    temporario.write_bytes(conteudo)
    os.replace(temporario, destino)
    return destino


def ler_retrato(config: Config, competencia: str) -> list[dict[str, str]]:
    """As linhas de um retrato gravado; lista vazia se ele nao existe."""
    caminho = caminho_do_retrato(config, competencia)
    if not caminho.is_file():
        return []
    with gzip.open(caminho, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))
