"""Fontes dos relatorios, com o endereco publico de cada uma.

Toda superficie diz de onde vieram os numeros e da o link para a pessoa
conferir: a pagina do conjunto de dados no portal da CVM (e nao o .zip bruto
que o monitor baixa), a area do desenvolvedor do Open Finance e a vitrine de
cada casa. Enderecos conferidos em 2026-09-24: todos respondem 200, menos as
vitrines da XP e do Itau, que devolvem 403 a cliente HTTP e abrem no navegador
(o `doctor` registra o mesmo).

Os textos daqui saem nos relatorios, entao seguem a acentuacao do portugues.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from shelf_monitor.config import Config
from shelf_monitor.models import CollectorStrategy, RunReport, SnapshotStatus


@dataclass(frozen=True)
class Fonte:
    """Uma fonte citavel: nome, endereco para consulta e o que entra no relatorio."""

    nome: str
    url: str
    uso: str
    # Endereco que o monitor le, quando difere da pagina de consulta.
    url_dados: str | None = None


CVM_CADASTRO = Fonte(
    nome="Cadastro de fundos da CVM",
    url="https://dados.cvm.gov.br/dataset/fi-cad",
    uso="classificação ANBIMA, gestora, situação e o radar de registros",
    url_dados="https://dados.cvm.gov.br/dados/FI/CAD/DADOS/",
)
# O mesmo cadastro quando a execucao nao coletou o radar - so o enriquecimento
# leu a base. A previdencia nunca roda o radar, e a fonte dizia que sim.
CVM_CADASTRO_SEM_RADAR = replace(CVM_CADASTRO, uso="classificação ANBIMA, gestora e situação")
CVM_INFORME_DIARIO = Fonte(
    nome="Informe diário de fundos da CVM",
    url="https://dados.cvm.gov.br/dataset/fi-doc-inf_diario",
    uso="captação, resgates, patrimônio, cotistas e cota (rentabilidade)",
    url_dados="https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/",
)
CVM_EXTRATO = Fonte(
    nome="Extrato de informações de fundos da CVM",
    url="https://dados.cvm.gov.br/dataset/fi-doc-extrato",
    uso="taxa de administração, mandato e condições de resgate",
    url_dados="https://dados.cvm.gov.br/dados/FI/DOC/EXTRATO/DADOS/",
)
OPEN_FINANCE = Fonte(
    nome="Open Finance Brasil - dados abertos de investimentos",
    url="https://openfinancebrasil.atlassian.net/wiki/spaces/OF/overview",
    uso="catálogo de fundos que cada instituição administra",
    url_dados="https://data.directory.openbankingbrasil.org.br/participants",
)

# Fontes do monitor de ETFs (planilha do `etf`).
FUNDOS_NET = Fonte(
    nome="Fundos.NET (B3) - informes diários dos ETFs",
    url="https://fnet.bmfbovespa.com.br/fnet/publico/abrirGerenciadorDocumentosCVM",
    uso="patrimônio, cota, cotas emitidas, captação e resgates de cada ETF",
)
CVM_BALANCETE_FIE = Fonte(
    nome="Balancete de fundos estruturados da CVM",
    url="https://dados.cvm.gov.br/dataset/fie-doc-balancete",
    uso="patrimônio mensal dos ETFs",
)
B3_COTAHIST = Fonte(
    nome="Séries históricas de cotações da B3 (COTAHIST)",
    url=(
        "https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/"
        "market-data/historico/mercado-a-vista/series-historicas/"
    ),
    uso="cotação de negociação dos ETFs",
)
B3_FUNDOS_LISTADOS = Fonte(
    nome="Fundos listados na B3 (ETFs)",
    url="https://sistemaswebb3-listados.b3.com.br/fundsListedPage/ETF",
    uso="ticker (código de negociação) e CNPJ de cada ETF listado",
)
# O monitor nao baixa nada do ANBIMA Data: os termos do portal proibem acesso
# automatizado (conferido em 2026-09-24). Le o anexo que a pessoa baixa.
ANBIMA_BOLETIM = Fonte(
    nome="Boletim de Fundos de Investimento da ANBIMA",
    url="https://data.anbima.com.br/publicacoes/boletim-de-fundos-de-investimento",
    uso="captação líquida e patrimônio de ETF da base ANBIMA, para conferência",
)
FONTES_ETF = (
    CVM_CADASTRO, CVM_INFORME_DIARIO, FUNDOS_NET, CVM_BALANCETE_FIE, B3_COTAHIST, B3_FUNDOS_LISTADOS,
)

# Fontes do extrator de dados brutos de ETF (`etf-bruto`). Enderecos conferidos
# em 2026-09-24: todos respondem 200.
B3_ARQUIVOS = Fonte(
    nome="B3 - dados públicos de produtos listados (portal de arquivos)",
    url=(
        "https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/"
        "consultas/boletim-diario/dados-publicos-de-produtos-listados-e-de-balcao/"
    ),
    uso="cadastro de instrumentos (ISIN, lote, categoria, início de negociação) e negócios consolidados do pregão",
    url_dados="https://arquivos.b3.com.br/",
)
FUNDOS_NET_DOCUMENTOS = Fonte(
    nome="Fundos.NET (B3) - documentos dos ETFs",
    url="https://fnet.bmfbovespa.com.br/fnet/publico/abrirGerenciadorDocumentosCVM",
    uso="informe diário, regulamento, demonstrações financeiras e relatório anual",
)
CVM_CDA = Fonte(
    nome="Composição da carteira (CDA) da CVM",
    url="https://dados.cvm.gov.br/dataset/fi-doc-cda",
    uso="carteira mensal dos ETFs (arquivo cda_fie)",
    url_dados="https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/",
)
# A mesma carteira, lida para o mapa de familias dos gaps (blocos cda_fi_*).
CVM_CDA_FAMILIAS = replace(
    CVM_CDA,
    uso="espelho, master e perfil de carteira das famílias de estratégia (gaps da XP)",
)
B3_INDICES = Fonte(
    nome="Índices da B3",
    url="https://www.b3.com.br/pt_br/market-data-e-indices/indices/",
    uso="série diária e carteira do dia dos índices B3",
    url_dados="https://sistemaswebb3-listados.b3.com.br/indexPage/day/IBOV?language=pt-br",
)
COMDINHEIRO = Fonte(
    nome="Comdinheiro (assinatura)",
    url="https://www.comdinheiro.com.br/",
    uso="retornos, volatilidade, beta, DY, proventos e índices estrangeiros, por consulta configurada",
)
FONTES_ETF_BRUTO = (
    B3_ARQUIVOS, B3_FUNDOS_LISTADOS, B3_COTAHIST, FUNDOS_NET_DOCUMENTOS, CVM_CADASTRO, CVM_CDA,
    B3_INDICES, COMDINHEIRO,
)


def fonte_da_vitrine(config: Config, platform_id: str, rotulo: str) -> Fonte | None:
    """A vitrine da casa como fonte - None quando ela nao tem pagina publica."""
    url = config.platform(platform_id).shelf_url
    if not url:
        return None
    return Fonte(
        nome=f"Vitrine - {rotulo}",
        url=url,
        uso="fundos que a casa exibe ao cliente e as condições publicadas",
    )


def fontes_do_relatorio(report: RunReport, config: Config) -> list[Fonte]:
    """As fontes que de fato entraram neste relatorio, na ordem em que aparecem.

    So entra o que foi usado: vitrine de casa que respondeu com fundos de
    vitrine, Open Finance quando alguma casa veio por ele, e cada conjunto da
    CVM quando o bloco que depende dele foi calculado.
    """
    from shelf_monitor.report.shared.plataformas import rotulo_da_casa

    fontes: list[Fonte] = []
    for snapshot in report.snapshots:
        if snapshot.status == SnapshotStatus.UNAVAILABLE:
            continue
        if not any(f.on_listing for f in snapshot.funds):
            continue
        fonte = fonte_da_vitrine(config, snapshot.platform, rotulo_da_casa(snapshot.platform))
        if fonte:
            fontes.append(fonte)

    usou_open_finance = any(
        CollectorStrategy.OPEN_FINANCE.value in f.sources
        for s in report.snapshots for f in s.funds
    )
    if usou_open_finance:
        fontes.append(OPEN_FINANCE)
    if report.cvm_snapshot:
        fontes.append(CVM_CADASTRO)
    elif report.enrichment:
        fontes.append(CVM_CADASTRO_SEM_RADAR)
    blocos_do_informe = (
        report.retail_flow, report.performance, report.performance_12m,
        report.rentabilidade,
    )
    if any(b and b.get("available") for b in blocos_do_informe):
        fontes.append(CVM_INFORME_DIARIO)
    if any(b and b.get("available") for b in (report.custo, report.mandato)):
        fontes.append(CVM_EXTRATO)
    if report.familias is not None:
        fontes.append(CVM_CDA_FAMILIAS)
    return fontes
