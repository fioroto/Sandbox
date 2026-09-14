"""Gera a fixture de cadastro usada pelos testes de catálogo/fluxo de ETF.

Isolada de tests/fixtures/market/ de propósito: aquela fixture já sustenta
asserts de contagem em test_market.py (ex: `manifest["counts"]["classes"] == 4`),
e somar fundos de ETF ali quebraria testes que não têm nada a ver com ETF.
Mesmo layout de colunas do registro pós-RCVM 175 (ver
tests/fixtures/make_market_fixtures.py); dado próprio, pensado para os três
casos que a v1 do catálogo de ETF errava:

- ETF de ações achado pelo TIPO estrutural (FIIM), com nome que não contém
  "ETF" nem "índice" - prova que a detecção não depende de palavra-chave.
- ETF de renda fixa, também por tipo - é a lacuna que motivou a mudança.
- ETF cancelado - não pode aparecer no catálogo.
- Feeder (FIF comum que só replica um ETF): tem "ETF" no nome mas o tipo é
  FIF, e o benchmark é "S&P 500" - cobre a categorização por benchmark e o
  caso de normalização que quebrava (S&P vira "S P", sem colar em "SP500").

Rode com: python tests/fixtures/etf/make_etf_fixtures.py
"""
from __future__ import annotations

import zipfile
from pathlib import Path

FIXTURES = Path(__file__).parent

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
]

FUNDOS = [
    # ETF de acoes, achado pelo tipo (FIIM) - nome nao tem "ETF" nem "indice".
    ["9001", "10298765000111", "955001", "2025-06-25", "2025-06-01", "FIIM",
     "ITAU IBOVESPA FUNDO DE INDICE", "", "Em Funcionamento Normal", "2025-06-25",
     "2025-06-25", "2026-01-01", "2026-12-31", "3508040394.52", "2026-08-25",
     "MARIA DIRETORA", "59281253000123", "BEM DTVM LTDA", "PJ",
     "40430971000196", "ITAU UNIBANCO ASSET MANAGEMENT LTDA."],
    # ETF de renda fixa (FIIM) cancelado - nao pode entrar no catalogo.
    ["9002", "10298765000122", "955002", "2025-06-25", "2025-06-01", "FIIM",
     "ITAU IMA-B FUNDO DE INDICE RENDA FIXA", "2026-05-01", "Cancelado",
     "2026-05-01", "2025-06-25", "2026-01-01", "2026-12-31", "0.00",
     "2026-05-01", "MARIA DIRETORA", "59281253000123", "BEM DTVM LTDA", "PJ",
     "40430971000196", "ITAU UNIBANCO ASSET MANAGEMENT LTDA."],
    # Feeder (FIF) que replica um ETF - "ETF" no nome, mas o tipo e FIF.
    ["9003", "10298765000133", "955003", "2024-01-10", "2023-12-01", "FIF",
     "SANTANDER S&P 500 ETF FIC FIF", "", "Em Funcionamento Normal", "2024-01-25",
     "2024-08-15", "2026-01-01", "2026-12-31", "50000000.00", "2026-08-25",
     "JOAO DIRETOR", "59281253000123", "BEM DTVM LTDA", "PJ",
     "08604187000144", "SANTANDER BRASIL GESTAO DE RECURSOS LTDA"],
]

CLASSES = [
    ["9001", "8001", "10298765000111", "C955001", "2025-06-25", "2025-06-01",
     "2025-07-01", "Classes de Cotas de Fundos FIIM", "ITAU IBOVESPA - CLASSE UNICA",
     "Em Funcionamento Normal", "2025-06-25", "Renda Variavel", "IBOVESPA", "Nao",
     "Acoes Indice Ativo Ibovespa", "N", "N", "N", "N", "Aberto",
     "N", "Investidores em Geral", "3508040394.52", "2026-08-25",
     "11111111000111", "AUDITORA X", "22222222000122", "CUSTODIANTE Y",
     "33333333000133", "CONTROLADOR Z"],
    ["9002", "8002", "10298765000122", "C955002", "2025-06-25", "2025-06-01",
     "2025-07-01", "Classes de Cotas de Fundos FIIM", "ITAU IMA-B - CLASSE UNICA",
     "Cancelado", "2026-05-01", "Renda Fixa", "IMA-B", "Nao",
     "Renda Fixa Duracao Livre Soberano", "N", "N", "N", "N", "Aberto",
     "N", "Investidores em Geral", "0.00", "2026-05-01",
     "11111111000111", "AUDITORA X", "22222222000122", "CUSTODIANTE Y",
     "33333333000133", "CONTROLADOR Z"],
    ["9003", "8003", "10298765000133", "C955003", "2024-01-10", "2023-12-01",
     "2024-02-01", "Classes de Cotas de Fundos FIF", "SANTANDER S&P 500 - CLASSE UNICA",
     "Em Funcionamento Normal", "2024-01-25", "Acoes", "S&P 500", "Nao",
     "Acoes Investimento no Exterior", "S", "N", "N", "N", "Aberto",
     "N", "Investidores em Geral", "50000000.00", "2026-08-25",
     "11111111000111", "AUDITORA X", "22222222000122", "CUSTODIANTE Y",
     "33333333000133", "CONTROLADOR Z"],
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
    return directory


if __name__ == "__main__":
    print("gerado em:", build())
