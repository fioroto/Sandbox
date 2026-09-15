"""Catalogo dos datasets da CVM usados pela base de mercado.

Escopo atual: cadastro (hierarquia fundo/classe/subclasse) e taxas. As series
temporais (informe diario, carteira, lamina) estao mapeadas no fim do arquivo
mas nao sao baixadas - sao dezenas de GB e o uso hoje nao pede.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CVM_BASE = "https://dados.cvm.gov.br/dados"


@dataclass(frozen=True)
class Dataset:
    """Um arquivo baixavel da CVM e o que se espera dele."""

    key: str
    url: str
    filename: str
    description: str
    # Arquivos dentro do zip, por chave logica. Vazio quando nao e zip.
    members: dict[str, str] = field(default_factory=dict)
    approx_mb: int = 0


REGISTRO = Dataset(
    key="registro",
    url=f"{CVM_BASE}/FI/CAD/DADOS/registro_fundo_classe.zip",
    filename="registro_fundo_classe.zip",
    description="Cadastro pos-RCVM 175: fundo, classe e subclasse",
    members={
        "fundo": "registro_fundo",
        "classe": "registro_classe",
        "subclasse": "registro_subclasse",
    },
    approx_mb=6,
)

# Desde out/2024 este arquivo cobre apenas os fundos que AINDA NAO migraram
# para a RCVM 175 - os adaptados sairam dele. E complemento do registro, nao
# alternativa: quem le so um dos dois perde parte do mercado.
CAD_FI = Dataset(
    key="cad_fi",
    url=f"{CVM_BASE}/FI/CAD/DADOS/cad_fi.csv",
    filename="cad_fi.csv",
    description="Cadastro legado: fundos nao adaptados a RCVM 175",
    approx_mb=20,
)

# Arquivo unico com ~5 anos de entregas: uma linha por classe por data-base.
# O estado atual e a maior DT_COMPTC de cada classe.
EXTRATO = Dataset(
    key="extrato",
    url=f"{CVM_BASE}/FI/DOC/EXTRATO/DADOS/extrato_fi.csv",
    filename="extrato_fi.csv",
    description="Extrato de informacoes: taxas, politica, prazos e minimos",
    approx_mb=100,
)

CADASTRO_DATASETS = (REGISTRO, CAD_FI)
ALL_DATASETS = (REGISTRO, CAD_FI, EXTRATO)


# Informe diario: uma linha por classe/fundo legado por data-base, com
# CAPTC_DIA/RESG_DIA/VL_PATRIM_LIQ/VL_QUOTA/NR_COTST. Arquivo mensal, ~30 MB;
# baixado sob demanda (um Dataset por mes) por market/inf_diario.py, nunca por
# import_market - series temporais nao entram na base de cadastro.
INF_DIARIO_URL = f"{CVM_BASE}/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{{yyyymm}}.zip"


def inf_diario_dataset(yyyymm: str) -> Dataset:
    """Dataset do informe diario de um mes (`YYYYMM`)."""
    filename = f"inf_diario_fi_{yyyymm}.zip"
    return Dataset(
        key=f"inf_diario_{yyyymm}",
        url=INF_DIARIO_URL.format(yyyymm=yyyymm),
        filename=filename,
        description=f"Informe diario CVM {yyyymm}",
        members={"inf_diario": f"inf_diario_fi_{yyyymm}.csv"},
        approx_mb=30,
    )


# --- colunas conhecidas -------------------------------------------------------
# Lidas por NOME, nunca por posicao: a CVM adiciona e renomeia colunas em
# producao (cinco novas em 2025). Colunas do arquivo que nao estao aqui sao
# registradas no manifesto - e assim que se descobre que o schema mudou.

REGISTRO_FUNDO_COLUMNS = (
    "ID_Registro_Fundo", "CNPJ_Fundo", "Codigo_CVM", "Data_Registro",
    "Data_Constituicao", "Tipo_Fundo", "Denominacao_Social", "Data_Cancelamento",
    "Situacao", "Data_Inicio_Situacao", "Data_Adaptacao_RCVM175",
    "Data_Inicio_Exercicio_Social", "Data_Fim_Exercicio_Social",
    "Patrimonio_Liquido", "Data_Patrimonio_Liquido", "Diretor",
    "CNPJ_Administrador", "Administrador", "Tipo_Pessoa_Gestor",
    "CPF_CNPJ_Gestor", "Gestor",
)

REGISTRO_CLASSE_COLUMNS = (
    "ID_Registro_Fundo", "ID_Registro_Classe", "CNPJ_Classe", "Codigo_CVM",
    "Data_Registro", "Data_Constituicao", "Data_Inicio", "Tipo_Classe",
    "Denominacao_Social", "Situacao", "Data_Inicio_Situacao", "Classificacao",
    "Indicador_Desempenho", "Classe_Cotas", "Classificacao_Anbima",
    "Tributacao_Longo_Prazo", "Entidade_Investimento",
    "Permitido_Aplicacao_CemPorCento_Exterior", "Classe_ESG", "Forma_Condominio",
    "Exclusivo", "Publico_Alvo", "Patrimonio_Liquido", "Data_Patrimonio_Liquido",
    "CNPJ_Auditor", "Auditor", "CNPJ_Custodiante", "Custodiante",
    "CNPJ_Controlador", "Controlador",
)

REGISTRO_SUBCLASSE_COLUMNS = (
    "ID_Registro_Classe", "ID_Subclasse", "Codigo_CVM", "Data_Constituicao",
    "Data_Inicio", "Denominacao_Social", "Situacao", "Data_Inicio_Situacao",
    "Forma_Condominio", "Exclusivo", "Publico_Alvo", "Previdenciario",
    "Exclusivo_INR", "Exclusivo_Previdencia_Complementar",
)


# --- fora do escopo atual -----------------------------------------------------
# O informe diario tem Dataset proprio acima (`inf_diario_dataset`), consumido
# por market/inf_diario.py - fora do cadastro por ser serie temporal, nao por
# estar fora de escopo. O que segue continua mapeado e nao baixado:
#   carteira (CDA)  /FI/DOC/CDA/DADOS/cda_fi_{YYYYMM}.zip                (~22 MB/mes)
#   lamina          /FI/DOC/LAMINA/DADOS/lamina_fi_{YYYYMM}.zip          (~10 MB/mes)
#   perfil mensal   /FI/DOC/PERFIL_MENSAL/DADOS/perfil_mensal_fi_{YYYYMM}.csv
# Estruturados tem datasets proprios com esquema diferente (FII, FIDC, FIP,
# FIAGRO) - e nao ha cadastro separado: o cadastro deles vem do registro acima.
