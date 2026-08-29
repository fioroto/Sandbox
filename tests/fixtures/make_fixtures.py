"""Gera as fixtures usadas pelos testes.

As fontes reais estao bloqueadas pela network policy deste ambiente, entao as
fixtures reproduzem o formato documentado: CSV da CVM em ISO-8859-1 com
separador ';' e colunas de registro_fundo.csv, e o envelope JSON do endpoint
/funds do Open Finance.

Rode com: python tests/fixtures/make_fixtures.py
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

FIXTURES = Path(__file__).parent

_FUNDO_COLUMNS = [
    "ID_Registro_Fundo", "CNPJ_Fundo", "Codigo_CVM", "Data_Registro",
    "Data_Constituicao", "Tipo_Fundo", "Denominacao_Social", "Data_Cancelamento",
    "Situacao", "Data_Inicio_Situacao", "Data_Adaptacao_RCVM175",
    "Patrimonio_Liquido", "Data_Patrimonio_Liquido", "CNPJ_Administrador",
    "Administrador", "Tipo_Pessoa_Gestor", "CPF_CNPJ_Gestor", "Gestor",
]

# (id, cnpj, registro, tipo, denominacao, situacao, inicio_situacao, pl, gestor_cnpj, gestor)
_FUNDOS = [
    ("1001", "12345678000191", "2024-03-12", "FIF", "BTG PACTUAL DIGITAL RENDA FIXA CP FIC FIF",
     "EM FUNCIONAMENTO NORMAL", "2024-03-20", "1850000000.00", "29650082000100", "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM"),
    ("1002", "22345678000192", "2026-07-28", "FIF", "BTG PACTUAL INFRA DEBENTURES INCENTIVADAS FIF",
     "FASE PRE-OPERACIONAL", "2026-07-28", "", "29650082000100", "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM"),
    ("1003", "32345678000193", "2026-08-14", "FIF", "BTG PACTUAL GLOBAL EQUITIES FIC FIF IE",
     "FASE PRE-OPERACIONAL", "2026-08-14", "", "29650082000100", "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM"),
    ("1004", "42345678000194", "2023-01-10", "FIF", "KINEA IPCA DINAMICO FIF RENDA FIXA LP",
     "EM FUNCIONAMENTO NORMAL", "2023-01-25", "7200000000.00", "08604187000144", "KINEA INVESTIMENTOS LTDA"),
    ("1005", "52345678000195", "2026-08-01", "FIF", "KINEA PRIVATE EQUITY IV FIP MULTIESTRATEGIA",
     "FASE PRE-OPERACIONAL", "2026-08-01", "", "08604187000144", "KINEA INVESTIMENTOS LTDA"),
    ("1006", "62345678000196", "2022-05-30", "FIF", "ITAU ACOES IBOVESPA ATIVO FIC FIF",
     "EM FUNCIONAMENTO NORMAL", "2022-06-10", "3100000000.00", "40430971000102", "ITAU UNIBANCO ASSET MANAGEMENT LTDA"),
    ("1007", "72345678000197", "2025-11-19", "FIF", "NU ASSET RENDA FIXA SIMPLES FIF",
     "EM FUNCIONAMENTO NORMAL", "2025-12-02", "980000000.00", "47418631000180", "NU ASSET MANAGEMENT LTDA"),
    ("1008", "82345678000198", "2026-08-20", "FIF", "NU ASSET MULTIMERCADO GLOBAL FIC FIF",
     "FASE PRE-OPERACIONAL", "2026-08-20", "", "47418631000180", "NU ASSET MANAGEMENT LTDA"),
    ("1009", "92345678000199", "2024-09-05", "FIF", "INTER CONSERVADOR RENDA FIXA REFERENCIADO DI FIF",
     "EM FUNCIONAMENTO NORMAL", "2024-09-18", "540000000.00", "18945670000146", "INTER ASSET GESTAO DE RECURSOS LTDA"),
    ("1010", "10345678000110", "2021-04-14", "FIF", "XP ALLOCATION MULTIMERCADO FIC FIF",
     "EM FUNCIONAMENTO NORMAL", "2021-04-30", "2400000000.00", "37882549000102", "XP ALLOCATION ASSET MANAGEMENT LTDA"),
    ("1011", "11345678000111", "2026-08-11", "FIF", "XP VISTA ACOES LONG BIASED FIC FIF",
     "FASE PRE-OPERACIONAL", "2026-08-11", "", "37882549000102", "XP ALLOCATION ASSET MANAGEMENT LTDA"),
    # Fora da watchlist - devem ser filtrados
    ("2001", "20345678000120", "2020-02-02", "FIF", "VERDE AM SCENA FIC FIF MULTIMERCADO",
     "EM FUNCIONAMENTO NORMAL", "2020-02-20", "9100000000.00", "16478810000160", "VERDE ASSET MANAGEMENT S.A."),
    ("2002", "21345678000121", "2019-08-08", "FIF", "SPX NIMITZ FIC FIF MULTIMERCADO",
     "EM FUNCIONAMENTO NORMAL", "2019-08-22", "8300000000.00", "12345098000155", "SPX GESTAO DE RECURSOS LTDA"),
]

_CLASSES = [
    ("12345678000191", "Renda Fixa"),
    ("22345678000192", "Renda Fixa"),
    ("32345678000193", "Acoes"),
    ("42345678000194", "Renda Fixa"),
    ("52345678000195", "Multimercado"),
    ("62345678000196", "Acoes"),
    ("72345678000197", "Renda Fixa"),
    ("82345678000198", "Multimercado"),
    ("92345678000199", "Renda Fixa"),
    ("10345678000110", "Multimercado"),
    ("11345678000111", "Acoes"),
    ("20345678000120", "Multimercado"),
    ("21345678000121", "Multimercado"),
]


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    lines = [";".join(header)]
    lines.extend(";".join(row) for row in rows)
    return "\n".join(lines).encode("iso-8859-1")


def build_cvm_zip(path: Path) -> Path:
    fundo_rows = []
    for (fund_id, cnpj, registro, tipo, denom, situacao, ini_sit, pl,
         gestor_cnpj, gestor) in _FUNDOS:
        fundo_rows.append([
            fund_id, cnpj, fund_id, registro, registro, tipo, denom, "",
            situacao, ini_sit, "2024-12-31" if situacao != "FASE PRE-OPERACIONAL" else "",
            pl, "2026-08-28", "59281253000123", "BEM DTVM LTDA",
            "PJ", gestor_cnpj, gestor,
        ])

    classe_rows = [
        [f"C{i}", cnpj, classe, "Classe Unica"] for i, (cnpj, classe) in enumerate(_CLASSES, 1)
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("registro_fundo.csv", _csv(_FUNDO_COLUMNS, fundo_rows))
        archive.writestr(
            "registro_classe.csv",
            _csv(["ID_Registro_Classe", "CNPJ_Fundo", "Classe", "Tipo_Classe"], classe_rows),
        )
    return path


# Envelope /funds do Open Finance, no formato agrupado por marca.
def _fund(name: str, cnpj: str, category: str, manager: str, manager_cnpj: str,
          minimum: str, admin_fee: str, quotation: int, payment: int) -> dict:
    return {
        "name": name,
        "cnpjNumber": cnpj,
        "isinCode": f"BRTEST{cnpj[:6]}CTF",
        "anbimaCategory": category,
        "admin": {"name": "BEM DTVM LTDA", "cnpjNumber": "59281253000123"},
        "fundManager": {"name": manager, "cnpjNumber": manager_cnpj},
        "fees": {
            "maxAdminFee": admin_fee,
            "performanceFee": {"method": "ABSOLUTE", "benchmark": "CDI", "amount": "20.00"},
        },
        "generalConditions": {
            "minimumAmount": minimum,
            "redemption": {"quotationDays": quotation, "paymentDays": payment, "graceDays": 0},
            "fundQuotaType": "OPEN",
        },
        "taxation": "LONGO_PRAZO",
    }


_OPEN_FINANCE_FUNDS = [
    _fund("BTG PACTUAL DIGITAL RENDA FIXA CP FIC FIF", "12345678000191", "Renda Fixa Credito Livre",
          "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM", "29650082000100", "100.00", "0.50", 1, 1),
    _fund("KINEA IPCA DINAMICO FIF RENDA FIXA LP", "42345678000194", "Renda Fixa Indexados",
          "KINEA INVESTIMENTOS LTDA", "08604187000144", "5000.00", "0.60", 1, 3),
    _fund("VERDE AM SCENA FIC FIF MULTIMERCADO", "20345678000120", "Multimercado Livre",
          "VERDE ASSET MANAGEMENT S.A.", "16478810000160", "5000.00", "1.90", 1, 30),
    _fund("SPX NIMITZ FIC FIF MULTIMERCADO", "21345678000121", "Multimercado Macro",
          "SPX GESTAO DE RECURSOS LTDA", "12345098000155", "25000.00", "2.00", 1, 30),
    _fund("BTG PACTUAL ACOES SMALL CAPS FIC FIF", "30345678000130", "Acoes Small Caps",
          "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM", "29650082000100", "1000.00", "2.00", 5, 30),
    _fund("ABSOLUTE VERTEX FIC FIF MULTIMERCADO", "31345678000131", "Multimercado Livre",
          "ABSOLUTE GESTAO DE INVESTIMENTOS LTDA", "17632972000180", "10000.00", "2.00", 1, 30),
    _fund("BTG PACTUAL GLOBAL BONDS FIC FIF IE", "32345678000132", "Renda Fixa Investimento no Exterior",
          "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM", "29650082000100", "500.00", "0.80", 1, 4),
]


def build_open_finance(path: Path) -> Path:
    payload = {
        "data": [
            {
                "brand": {"name": "BTG Pactual"},
                "companies": [
                    {
                        "name": "BTG PACTUAL SERVICOS FINANCEIROS S.A. DTVM",
                        "cnpjNumber": "59281253000123",
                        "funds": _OPEN_FINANCE_FUNDS,
                    }
                ],
            }
        ],
        "links": {"self": "https://example.test/open-banking/opendata-investments/v1/funds"},
        "meta": {"totalRecords": len(_OPEN_FINANCE_FUNDS), "totalPages": 1},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    zip_path = build_cvm_zip(FIXTURES / "cvm" / "registro_fundo_classe.zip")
    of_path = build_open_finance(FIXTURES / "open_finance" / "funds_page1.json")
    print(f"gerado: {zip_path}")
    print(f"gerado: {of_path}")
