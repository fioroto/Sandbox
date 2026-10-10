"""Catalogo dos datasets da CVM usados pela base de mercado.

Escopo atual: cadastro (hierarquia fundo/classe/subclasse), taxas, a carteira
mensal (CDA) das ultimas competencias, que alimenta o mapa de familias, e o
perfil mensal, que diz de quem e o dinheiro de cada classe (monitor da
industria). O informe diario e lido pelos blocos de captacao e rentabilidade;
a lamina esta mapeada no fim do arquivo mas nao e baixada.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

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
    description="Cadastro pós-RCVM 175: fundo, classe e subclasse",
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
    description="Cadastro legado: fundos não adaptados à RCVM 175",
    approx_mb=20,
)

# Arquivo unico com ~5 anos de entregas: uma linha por classe por data-base.
# O estado atual e a maior DT_COMPTC de cada classe.
EXTRATO = Dataset(
    key="extrato",
    url=f"{CVM_BASE}/FI/DOC/EXTRATO/DADOS/extrato_fi.csv",
    filename="extrato_fi.csv",
    description="Extrato de informações: taxas, política, prazos e mínimos",
    approx_mb=100,
)

CADASTRO_DATASETS = (REGISTRO, CAD_FI)
ALL_DATASETS = (REGISTRO, CAD_FI, EXTRATO)


# --- carteira mensal (CDA) ----------------------------------------------------
# Um zip por competencia (~22 MB), com um membro por bloco de ativo mais o PL e
# as posicoes sob sigilo. Aqui ele serve ao mapa de familias (membros cda_fi_*,
# em market/carteira); a carteira dos ETFs (membro cda_fie) e lida pelo
# repositorio etf-monitor.
#
# Quantas competencias fechadas considerar, e quantos dias depois do fim do mes
# a CVM ja costuma ter publicado (08/2026 estava no ar em 24/09/2026; 09/2026
# respondia 404 na mesma data).
COMPETENCIAS_CDA = 3
PRAZO_DE_PUBLICACAO_CDA_DIAS = 15


def competencias_fechadas(hoje: date, quantas: int = COMPETENCIAS_CDA) -> list[str]:
    """AAAAMM das ultimas `quantas` competencias com prazo de publicacao vencido."""
    competencias = []
    ano, mes = hoje.year, hoje.month
    while len(competencias) < quantas:
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        fim = date(ano, mes, calendar.monthrange(ano, mes)[1])
        if fim + timedelta(days=PRAZO_DE_PUBLICACAO_CDA_DIAS) <= hoje:
            competencias.append(f"{ano}{mes:02d}")
    return competencias


def dataset_cda(competencia: str) -> Dataset:
    return Dataset(
        key=f"cda_fi_{competencia}",
        url=f"{CVM_BASE}/FI/DOC/CDA/DADOS/cda_fi_{competencia}.zip",
        filename=f"cda_fi_{competencia}.zip",
        description=f"Carteira (CDA) {competencia}",
        members={
            "cda_fie": f"cda_fie_{competencia}",
            "pl": f"cda_fi_pl_{competencia}",
            **{f"blc_{n}": f"cda_fi_blc_{n}_{competencia}" for n in range(1, 9)},
            "confid": f"cda_fi_confid_{competencia}",
        },
        approx_mb=22,
    )


# --- perfil mensal ------------------------------------------------------------
# Um CSV por competencia (~13 MB), com a fatia do PL e o numero de cotistas de
# cada classe por tipo de investidor: PF varejo e private, PJ, clientes de
# distribuidor por conta e ordem, fundos, previdencia, institucionais. E a unica
# fonte publica que separa o dinheiro de PF/PJ do dinheiro de outros fundos.
# Sai com a carteira: 08/2026 estava no ar em 28/09/2026, e 09/2026 dava 404.
#
# A CVM regenera arquivos antigos (o de 09/2025 tinha Last-Modified de
# 26/09/2026): revalidar o historico inteiro a cada execucao rebaixaria ~13 MB
# por mes sem mudar nada que importe. Por isso so as competencias mais recentes
# sao revalidadas; as antigas ficam no que ja esta em disco.
COMPETENCIAS_PERFIL_REVALIDADAS = 3


def dataset_perfil_mensal(competencia: str) -> Dataset:
    return Dataset(
        key=f"perfil_mensal_fi_{competencia}",
        url=f"{CVM_BASE}/FI/DOC/PERFIL_MENSAL/DADOS/perfil_mensal_fi_{competencia}.csv",
        filename=f"perfil_mensal_fi_{competencia}.csv",
        description=f"Perfil mensal {competencia}: cotistas e PL por tipo de investidor",
        approx_mb=13,
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
# O informe diario e baixado por `market/informe_diario.fetch_inf_diario_month`, fora
# deste catalogo. Mapeados e ainda nao baixados:
#   lamina          /FI/DOC/LAMINA/DADOS/lamina_fi_{YYYYMM}.zip          (~10 MB/mes)
#   historico       /FI/CAD/DADOS/cad_fi_hist.zip (gestor, classe, publico e
#                   situacao com datas, so para fundos nao adaptados; ~18 MB)
# Estruturados tem datasets proprios com esquema diferente (FII, FIDC, FIP,
# FIAGRO) - e nao ha cadastro separado: o cadastro deles vem do registro acima.
