"""Leitura dos CSVs da CVM para os modelos da base de mercado.

Reusa `_decode_rows` e `_get` de collectors/cvm.py: o primeiro ja resolve o
encoding (utf-8 antes de latin-1, senao um arquivo utf-8 vira mojibake sem
erro), e o segundo casa nome de coluna tolerando caixa e acento - exatamente o
que se precisa num schema que a CVM altera em producao.
"""
from __future__ import annotations

import codecs
import csv
import io
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

_READ_CHUNK = 1 << 20

from shelf_monitor.collectors.cvm import _decode_rows, _get
from shelf_monitor.market.datasets import (
    REGISTRO_CLASSE_COLUMNS,
    REGISTRO_FUNDO_COLUMNS,
    REGISTRO_SUBCLASSE_COLUMNS,
)
from shelf_monitor.market.models import (
    MarketClass,
    MarketFund,
    MarketSubclass,
    Origin,
)
from shelf_monitor.normalize import normalize_cnpj, normalize_text, parse_number

# A CVM publica datas em ISO na maior parte dos arquivos, mas o legado ainda
# aparece em formato brasileiro em alguns campos.
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y")


def parse_date(raw: str | None) -> date | None:
    """Converte uma data da CVM. Devolve None em vez de levantar.

    Uma data ilegivel nao pode derrubar a importacao de 30 mil fundos: o campo
    fica vazio e a linha entra assim mesmo.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Alguns campos vem com hora anexada.
    text = text.split(" ")[0].split("T")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def detect_encoding(source: Path | bytes) -> str:
    """Descobre o encoding validando o arquivo INTEIRO, nao uma amostra.

    Mesma ordem de `_decode_rows`, e pelo mesmo motivo: latin-1 decodifica
    qualquer byte sem erro, entao utf-8 tem de ser tentado primeiro.

    A validacao percorre tudo porque uma amostra erra nos dois sentidos: um
    arquivo utf-8 cortado no meio de um caractere multibyte parece latin-1, e
    um arquivo latin-1 cujo inicio e ASCII puro parece utf-8. Os dois casos
    produzem mojibake em silencio. A passada extra custa I/O, nao memoria.
    """
    if isinstance(source, bytes):
        try:
            source.decode("utf-8-sig")
        except UnicodeDecodeError:
            return "iso-8859-1"
        return "utf-8-sig"

    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        with open(source, "rb") as handle:
            while chunk := handle.read(_READ_CHUNK):
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "iso-8859-1"
    return "utf-8-sig"


def iter_rows(source: Path | bytes) -> Iterator[dict]:
    """Percorre um CSV da CVM sem materializar o arquivo inteiro.

    O extrato de informacoes tem ~100 MB; carrega-lo como lista de dicts custa
    cerca de dez vezes isso em RAM. Como o consumo e linha a linha, o streaming
    mantem o uso constante.
    """
    encoding = detect_encoding(source)

    if isinstance(source, bytes):
        handle = io.StringIO(source.decode(encoding))
        yield from csv.DictReader(handle, delimiter=";")
        return

    # Sem errors="replace": o encoding ja foi validado contra o arquivo todo,
    # entao um erro aqui e sinal de problema real, nao algo a mascarar.
    with open(source, encoding=encoding, newline="") as handle:
        yield from csv.DictReader(handle, delimiter=";")


def unknown_columns(rows: list[dict], known: tuple[str, ...]) -> list[str]:
    """Colunas presentes no arquivo que o modelo nao conhece."""
    if not rows:
        return []
    known_normalized = {normalize_text(name) for name in known}
    seen = [
        key
        for key in rows[0]
        if key and normalize_text(key) not in known_normalized
    ]
    return sorted(seen)


def parse_funds(raw: bytes) -> tuple[list[MarketFund], list[str]]:
    """registro_fundo.csv -> fundos. Devolve (fundos, colunas desconhecidas)."""
    rows = _decode_rows(raw)
    funds: list[MarketFund] = []

    for row in rows:
        registro_id = _get(row, "ID_Registro_Fundo")
        name = _get(row, "Denominacao_Social")
        if not registro_id or not name:
            continue

        funds.append(
            MarketFund(
                registro_id=registro_id,
                cnpj=normalize_cnpj(_get(row, "CNPJ_Fundo")),
                name=name,
                origin=Origin.RCVM175,
                cvm_code=_get(row, "Codigo_CVM"),
                fund_type=_get(row, "Tipo_Fundo"),
                situation=_get(row, "Situacao"),
                situation_started_at=parse_date(_get(row, "Data_Inicio_Situacao")),
                registered_at=parse_date(_get(row, "Data_Registro")),
                constituted_at=parse_date(_get(row, "Data_Constituicao")),
                cancelled_at=parse_date(_get(row, "Data_Cancelamento")),
                adapted_at=parse_date(_get(row, "Data_Adaptacao_RCVM175")),
                net_worth=parse_number(_get(row, "Patrimonio_Liquido")),
                net_worth_at=parse_date(_get(row, "Data_Patrimonio_Liquido")),
                director=_get(row, "Diretor"),
                admin_name=_get(row, "Administrador"),
                admin_cnpj=normalize_cnpj(_get(row, "CNPJ_Administrador")),
                manager_name=_get(row, "Gestor"),
                manager_cnpj=normalize_cnpj(_get(row, "CPF_CNPJ_Gestor")),
                manager_person_type=_get(row, "Tipo_Pessoa_Gestor"),
            )
        )

    return funds, unknown_columns(rows, REGISTRO_FUNDO_COLUMNS)


def parse_classes(raw: bytes) -> tuple[list[MarketClass], list[str]]:
    """registro_classe.csv -> classes."""
    rows = _decode_rows(raw)
    classes: list[MarketClass] = []

    for row in rows:
        classe_id = _get(row, "ID_Registro_Classe")
        fundo_id = _get(row, "ID_Registro_Fundo")
        name = _get(row, "Denominacao_Social")
        if not classe_id or not fundo_id or not name:
            continue

        classes.append(
            MarketClass(
                classe_id=classe_id,
                fundo_id=fundo_id,
                cnpj=normalize_cnpj(_get(row, "CNPJ_Classe")),
                name=name,
                cvm_code=_get(row, "Codigo_CVM"),
                class_type=_get(row, "Tipo_Classe"),
                situation=_get(row, "Situacao"),
                situation_started_at=parse_date(_get(row, "Data_Inicio_Situacao")),
                registered_at=parse_date(_get(row, "Data_Registro")),
                constituted_at=parse_date(_get(row, "Data_Constituicao")),
                started_at=parse_date(_get(row, "Data_Inicio")),
                cvm_classification=_get(row, "Classificacao"),
                anbima_classification=_get(row, "Classificacao_Anbima"),
                benchmark=_get(row, "Indicador_Desempenho"),
                share_class=_get(row, "Classe_Cotas"),
                long_term_taxation=_get(row, "Tributacao_Longo_Prazo"),
                investment_entity=_get(row, "Entidade_Investimento"),
                full_foreign_allowed=_get(
                    row, "Permitido_Aplicacao_CemPorCento_Exterior"
                ),
                esg=_get(row, "Classe_ESG"),
                condominium=_get(row, "Forma_Condominio"),
                exclusive=_get(row, "Exclusivo"),
                target_audience=_get(row, "Publico_Alvo"),
                net_worth=parse_number(_get(row, "Patrimonio_Liquido")),
                net_worth_at=parse_date(_get(row, "Data_Patrimonio_Liquido")),
                auditor_name=_get(row, "Auditor"),
                auditor_cnpj=normalize_cnpj(_get(row, "CNPJ_Auditor")),
                custodian_name=_get(row, "Custodiante"),
                custodian_cnpj=normalize_cnpj(_get(row, "CNPJ_Custodiante")),
                controller_name=_get(row, "Controlador"),
                controller_cnpj=normalize_cnpj(_get(row, "CNPJ_Controlador")),
            )
        )

    return classes, unknown_columns(rows, REGISTRO_CLASSE_COLUMNS)


def parse_subclasses(raw: bytes) -> tuple[list[MarketSubclass], list[str]]:
    """registro_subclasse.csv -> subclasses."""
    rows = _decode_rows(raw)
    subclasses: list[MarketSubclass] = []

    for row in rows:
        subclasse_id = _get(row, "ID_Subclasse")
        classe_id = _get(row, "ID_Registro_Classe")
        name = _get(row, "Denominacao_Social")
        if not subclasse_id or not classe_id or not name:
            continue

        subclasses.append(
            MarketSubclass(
                subclasse_id=subclasse_id,
                classe_id=classe_id,
                name=name,
                cvm_code=_get(row, "Codigo_CVM"),
                situation=_get(row, "Situacao"),
                situation_started_at=parse_date(_get(row, "Data_Inicio_Situacao")),
                constituted_at=parse_date(_get(row, "Data_Constituicao")),
                started_at=parse_date(_get(row, "Data_Inicio")),
                condominium=_get(row, "Forma_Condominio"),
                exclusive=_get(row, "Exclusivo"),
                target_audience=_get(row, "Publico_Alvo"),
                pension=_get(row, "Previdenciario"),
                foreign_investor_only=_get(row, "Exclusivo_INR"),
                pension_plan_only=_get(row, "Exclusivo_Previdencia_Complementar"),
            )
        )

    return subclasses, unknown_columns(rows, REGISTRO_SUBCLASSE_COLUMNS)


def parse_legacy_funds(raw: bytes) -> list[MarketFund]:
    """cad_fi.csv -> fundos ainda nao adaptados a RCVM 175.

    Nao ha ID de registro neste arquivo: o CNPJ e a chave. Como esses fundos
    nao tem classe propria, a classificacao ANBIMA fica no proprio fundo - e
    por isso o consolidado precisa saber de onde a linha veio.
    """
    rows = _decode_rows(raw)
    funds: list[MarketFund] = []

    for row in rows:
        cnpj = normalize_cnpj(_get(row, "CNPJ_FUNDO", "CNPJ_Fundo"))
        name = _get(row, "DENOM_SOCIAL", "Denominacao_Social")
        if not cnpj or not name:
            continue

        funds.append(
            MarketFund(
                # Sem ID proprio no legado: o CNPJ vira a identidade.
                registro_id=f"legado:{cnpj}",
                cnpj=cnpj,
                name=name,
                origin=Origin.LEGACY,
                cvm_code=_get(row, "CD_CVM", "Codigo_CVM"),
                fund_type=_get(row, "TP_FUNDO", "Tipo_Fundo"),
                situation=_get(row, "SIT", "Situacao"),
                registered_at=parse_date(_get(row, "DT_REG", "Data_Registro")),
                constituted_at=parse_date(_get(row, "DT_CONST")),
                cancelled_at=parse_date(_get(row, "DT_CANCEL")),
                net_worth=parse_number(_get(row, "VL_PATRIM_LIQ")),
                net_worth_at=parse_date(_get(row, "DT_PATRIM_LIQ")),
                admin_name=_get(row, "ADMIN", "Administrador"),
                admin_cnpj=normalize_cnpj(_get(row, "CNPJ_ADMIN")),
                manager_name=_get(row, "GESTOR", "Gestor"),
                manager_cnpj=normalize_cnpj(_get(row, "CPF_CNPJ_GESTOR")),
            )
        )

    return funds


def legacy_class_fields(raw: bytes) -> dict[str, dict[str, str | None]]:
    """Campos do cad_fi que, no mundo pos-175, moraria na classe.

    Classificacao CVM e ANBIMA, condominio, exclusividade e taxas vem no mesmo
    registro do fundo legado - aqui sao extraidos por CNPJ para o consolidado
    poder apresenta-los na mesma coluna dos fundos adaptados.
    """
    rows = _decode_rows(raw)
    index: dict[str, dict[str, str | None]] = {}

    for row in rows:
        cnpj = normalize_cnpj(_get(row, "CNPJ_FUNDO", "CNPJ_Fundo"))
        if not cnpj:
            continue
        index[cnpj] = {
            "cvm_classification": _get(row, "CLASSE", "Classe"),
            "anbima_classification": _get(row, "CLASSE_ANBIMA", "Classe_Anbima"),
            "condominium": _get(row, "CONDOM"),
            "exclusive": _get(row, "FUNDO_EXCLUSIVO"),
            "share_class": _get(row, "FUNDO_COTAS"),
            "admin_fee": _get(row, "TAXA_ADM"),
            "performance_fee": _get(row, "TAXA_PERFM"),
            "custodian_name": _get(row, "CUSTODIANTE"),
            "auditor_name": _get(row, "AUDITOR"),
        }

    return index
