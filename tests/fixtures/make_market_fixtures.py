"""Gera as fixtures da base de mercado.

Reproduz o formato real da CVM: encoding latin-1, separador ';', decimal com
ponto, e os headers exatos de 2026-08. Os dados sao ficticios, mas cobrem os
casos que quebram a importacao no mundo real:

- fundo adaptado com duas classes, uma delas com subclasses
- fundo legado (so no cad_fi), sem classe propria
- CNPJ com mascara no extrato e sem mascara no cadastro
- taxa de administracao em reais convivendo com taxa em percentual
- coluna nova no header que o modelo ainda nao conhece

Rode com: python tests/fixtures/make_market_fixtures.py
"""
from __future__ import annotations

import zipfile
from pathlib import Path

FIXTURES = Path(__file__).parent / "market"

FUNDO_COLUMNS = [
    "ID_Registro_Fundo", "CNPJ_Fundo", "Codigo_CVM", "Data_Registro",
    "Data_Constituicao", "Tipo_Fundo", "Denominacao_Social", "Data_Cancelamento",
    "Situacao", "Data_Inicio_Situacao", "Data_Adaptacao_RCVM175",
    "Data_Inicio_Exercicio_Social", "Data_Fim_Exercicio_Social",
    "Patrimonio_Liquido", "Data_Patrimonio_Liquido", "Diretor",
    "CNPJ_Administrador", "Administrador", "Tipo_Pessoa_Gestor",
    "CPF_CNPJ_Gestor", "Gestor",
]

CLASSE_COLUMNS = [
    "ID_Registro_Fundo", "ID_Registro_Classe", "CNPJ_Classe", "Codigo_CVM",
    "Data_Registro", "Data_Constituicao", "Data_Inicio", "Tipo_Classe",
    "Denominacao_Social", "Situacao", "Data_Inicio_Situacao", "Classificacao",
    "Indicador_Desempenho", "Classe_Cotas", "Classificacao_Anbima",
    "Tributacao_Longo_Prazo", "Entidade_Investimento",
    "Permitido_Aplicacao_CemPorCento_Exterior", "Classe_ESG", "Forma_Condominio",
    "Exclusivo", "Publico_Alvo", "Patrimonio_Liquido", "Data_Patrimonio_Liquido",
    "CNPJ_Auditor", "Auditor", "CNPJ_Custodiante", "Custodiante",
    "CNPJ_Controlador", "Controlador",
    # Coluna que o modelo nao conhece - o manifesto deve denuncia-la.
    "Modalidade_Oferta",
]

SUBCLASSE_COLUMNS = [
    "ID_Registro_Classe", "ID_Subclasse", "Codigo_CVM", "Data_Constituicao",
    "Data_Inicio", "Denominacao_Social", "Situacao", "Data_Inicio_Situacao",
    "Forma_Condominio", "Exclusivo", "Publico_Alvo", "Previdenciario",
    "Exclusivo_INR", "Exclusivo_Previdencia_Complementar",
]

FUNDOS = [
    ["1001", "12345678000191", "555001", "2024-03-12", "2024-02-01", "FIF",
     "BTG PACTUAL DIGITAL FIF", "", "Em Funcionamento Normal", "2024-03-20",
     "2024-06-30", "2026-01-01", "2026-12-31", "1850000000.00", "2026-08-25",
     "MARIA DIRETORA", "59281253000123", "BEM DTVM LTDA", "PJ",
     "29650082000100", "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM"],
    ["1002", "42345678000194", "555002", "2023-01-10", "2022-12-01", "FIF",
     "KINEA IPCA DINAMICO FIF", "", "Em Funcionamento Normal", "2023-01-25",
     "2024-08-15", "2026-01-01", "2026-12-31", "7200000000.00", "2026-08-25",
     "JOAO DIRETOR", "59281253000123", "BEM DTVM LTDA", "PJ",
     "08604187000144", "KINEA INVESTIMENTOS LTDA"],
    # Registrado e ainda nao lancado.
    ["1003", "52345678000195", "555003", "2026-08-01", "2026-07-20", "FIF",
     "NU ASSET GLOBAL FIF", "", "Fase Pre-Operacional", "2026-08-01",
     "2026-08-01", "2026-01-01", "2026-12-31", "", "",
     "ANA DIRETORA", "59281253000123", "BEM DTVM LTDA", "PJ",
     "47418631000180", "NU ASSET MANAGEMENT LTDA"],
]

CLASSES = [
    ["1001", "2001", "12345678000191", "C555001", "2024-03-12", "2024-02-01",
     "2024-04-01", "Classes de Cotas de Fundos FIF", "BTG DIGITAL RF CP - CLASSE UNICA",
     "Em Funcionamento Normal", "2024-03-20", "Renda Fixa", "DI", "Nao",
     "Renda Fixa Duracao Livre Credito Livre", "S", "N", "N", "N", "Aberto",
     "N", "Investidores em Geral", "1850000000.00", "2026-08-25",
     "11111111000111", "AUDITORA X", "22222222000122", "CUSTODIANTE Y",
     "33333333000133", "CONTROLADOR Z", "Registro"],
    ["1002", "2002", "42345678000194", "C555002", "2023-01-10", "2022-12-01",
     "2023-02-01", "Classes de Cotas de Fundos FIF", "KINEA IPCA - CLASSE SENIOR",
     "Em Funcionamento Normal", "2023-01-25", "Renda Fixa", "IPCA", "Nao",
     "Renda Fixa Indexados Indice de Precos", "S", "N", "N", "S", "Aberto",
     "N", "Investidores Qualificados", "5000000000.00", "2026-08-25",
     "11111111000111", "AUDITORA X", "22222222000122", "CUSTODIANTE Y",
     "33333333000133", "CONTROLADOR Z", "Registro"],
    # Segunda classe do mesmo fundo: e isso que a RCVM 175 permite.
    ["1002", "2003", "62345678000196", "C555003", "2023-01-10", "2022-12-01",
     "2023-02-01", "Classes de Cotas de Fundos FIF", "KINEA IPCA - CLASSE SUBORDINADA",
     "Em Funcionamento Normal", "2023-01-25", "Renda Fixa", "IPCA", "Nao",
     "Renda Fixa Indexados Indice de Precos", "S", "N", "N", "S", "Fechado",
     "S", "Investidores Profissionais", "2200000000.00", "2026-08-25",
     "11111111000111", "AUDITORA X", "22222222000122", "CUSTODIANTE Y",
     "33333333000133", "CONTROLADOR Z", "Registro"],
    ["1003", "2004", "52345678000195", "C555004", "2026-08-01", "2026-07-20",
     "", "Classes de Cotas de Fundos FIF", "NU GLOBAL - CLASSE UNICA",
     "Fase Pre-Operacional", "2026-08-01", "Multimercado", "CDI", "Nao",
     "Multimercado Investimento no Exterior", "S", "N", "S", "N", "Aberto",
     "N", "Investidores em Geral", "", "",
     "11111111000111", "AUDITORA X", "22222222000122", "CUSTODIANTE Y",
     "33333333000133", "CONTROLADOR Z", "Registro"],
]

SUBCLASSES = [
    ["2001", "3001", "S001", "2024-02-01", "2024-04-01", "BTG DIGITAL RF - VAREJO",
     "Em Funcionamento Normal", "2024-03-20", "Aberto", "N",
     "Investidores em Geral", "N", "N", "N"],
    ["2001", "3002", "S002", "2024-02-01", "2024-04-01", "BTG DIGITAL RF - PREVIDENCIA",
     "Em Funcionamento Normal", "2024-03-20", "Aberto", "N",
     "Investidores em Geral", "S", "N", "S"],
    ["2002", "3003", "S003", "2022-12-01", "2023-02-01", "KINEA IPCA - INR",
     "Em Funcionamento Normal", "2023-01-25", "Aberto", "N",
     "Investidores Qualificados", "N", "S", "N"],
]

CAD_FI_COLUMNS = [
    "TP_FUNDO", "CNPJ_FUNDO", "DENOM_SOCIAL", "DT_REG", "SIT", "CLASSE",
    "CLASSE_ANBIMA", "CONDOM", "FUNDO_COTAS", "FUNDO_EXCLUSIVO", "TAXA_PERFM",
    "TAXA_ADM", "VL_PATRIM_LIQ", "DT_PATRIM_LIQ", "ADMIN", "CNPJ_ADMIN",
    "GESTOR", "CUSTODIANTE", "AUDITOR",
]

CAD_FI_ROWS = [
    # Fundo que ainda nao migrou: so existe aqui, nao no registro.
    ["FI", "70345678000170", "VERDE AM SCENA FIC FIM", "2020-02-02",
     "EM FUNCIONAMENTO NORMAL", "Fundo Multimercado", "Multimercados Livre",
     "Aberto", "S", "N", "20.00", "1.90", "9100000000.00", "2026-08-25",
     "BEM DTVM LTDA", "59281253000123", "VERDE ASSET MANAGEMENT S.A.",
     "CUSTODIANTE Y", "AUDITORA X"],
    ["FI", "80345678000180", "SPX NIMITZ FIC FIM", "2019-08-08",
     "EM FUNCIONAMENTO NORMAL", "Fundo Multimercado", "Multimercados Macro",
     "Aberto", "S", "N", "20.00", "2.00", "8300000000.00", "2026-08-25",
     "BEM DTVM LTDA", "59281253000123", "SPX GESTAO DE RECURSOS LTDA",
     "CUSTODIANTE Y", "AUDITORA X"],
    # Ja adaptado: aparece nos dois arquivos e nao pode ser contado duas vezes.
    ["FIF", "12345678000191", "BTG PACTUAL DIGITAL FIF", "2024-03-12",
     "EM FUNCIONAMENTO NORMAL", "Fundo de Renda Fixa", "Renda Fixa Duracao Livre",
     "Aberto", "N", "N", "", "0.50", "1850000000.00", "2026-08-25",
     "BEM DTVM LTDA", "59281253000123", "BTG PACTUAL ASSET MANAGEMENT S.A. DTVM",
     "CUSTODIANTE Y", "AUDITORA X"],
]

EXTRATO_COLUMNS = [
    "TP_FUNDO_CLASSE", "CNPJ_FUNDO_CLASSE", "DENOM_SOCIAL", "DT_COMPTC",
    "POLIT_INVEST", "PUBLICO_ALVO", "TRIB_LPRAZO", "EXISTE_TAXA_PERFM",
    "TAXA_PERFM", "PARAM_TAXA_PERFM", "TAXA_ADM", "VL_TAXA_ENTRADA",
    "VL_TAXA_SAIDA", "INVEST_INICIAL_MIN", "VL_SALDO_MIN",
    "QT_DIA_CONVER_COTA", "QT_DIA_PAGTO_RESGATE",
]

EXTRATO_ROWS = [
    # CNPJ COM mascara - o cadastro traz sem. E a causa n.1 de join vazio.
    ["CLASSES - FIF", "12.345.678/0001-91", "BTG DIGITAL RF CP", "2026-06-30",
     "Renda fixa com credito privado", "Investidores em Geral", "S", "N",
     "", "", "0.50", "0", "0", "100.00", "100.00", "1", "1"],
    # Entrega mais recente da mesma classe: e esta que deve valer.
    ["CLASSES - FIF", "12.345.678/0001-91", "BTG DIGITAL RF CP", "2026-08-25",
     "Renda fixa com credito privado", "Investidores em Geral", "S", "N",
     "", "", "0.45", "0", "0", "100.00", "100.00", "1", "1"],
    ["CLASSES - FIF", "42.345.678/0001-94", "KINEA IPCA SENIOR", "2026-08-25",
     "Titulos indexados ao IPCA", "Investidores Qualificados", "S", "S",
     "20.00", "IPCA + 5%", "0.60", "0", "0", "5000.00", "1000.00", "1", "3"],
    # Taxa em REAIS no mesmo campo que normalmente traz percentual.
    ["CLASSES - FIF", "62.345.678/0001-96", "KINEA IPCA SUBORDINADA", "2026-08-25",
     "Titulos indexados ao IPCA", "Investidores Profissionais", "S", "S",
     "20.00", "IPCA + 6%", "30000.00", "0", "0", "1000000.00", "0", "1", "30"],
    # Texto livre em vez de numero - permitido para investidor qualificado.
    ["CLASSES - FIF", "52.345.678/0001-95", "NU GLOBAL", "2026-08-25",
     "Investimento no exterior", "Investidores em Geral", "S", "N",
     "", "", "conforme regulamento", "0", "0", "500.00", "0", "1", "5"],
]


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    lines = [";".join(header)]
    lines.extend(";".join(row) for row in rows)
    return "\n".join(lines).encode("iso-8859-1")


def build(directory: Path = FIXTURES) -> Path:
    directory.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(
        directory / "registro_fundo_classe.zip", "w", zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("registro_fundo.csv", _csv(FUNDO_COLUMNS, FUNDOS))
        archive.writestr("registro_classe.csv", _csv(CLASSE_COLUMNS, CLASSES))
        archive.writestr("registro_subclasse.csv", _csv(SUBCLASSE_COLUMNS, SUBCLASSES))

    (directory / "cad_fi.csv").write_bytes(_csv(CAD_FI_COLUMNS, CAD_FI_ROWS))
    (directory / "extrato_fi.csv").write_bytes(_csv(EXTRATO_COLUMNS, EXTRATO_ROWS))
    return directory


if __name__ == "__main__":
    print("gerado em:", build())
