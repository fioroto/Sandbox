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
# Fontes do monitor da industria (`industria`). Paginas conferidas em
# 28/09/2026: todas respondem 200.
CVM_CADASTRO_INDUSTRIA = replace(
    CVM_CADASTRO,
    uso="tipo, situação, público-alvo, exclusividade, gestora e classificação ANBIMA de cada classe e subclasse",
)
CVM_INFORME_DIARIO_INDUSTRIA = replace(
    CVM_INFORME_DIARIO,
    uso="patrimônio, captação, resgates, cotistas e cota de cada classe e subclasse",
)
CVM_PERFIL_MENSAL = Fonte(
    nome="Perfil mensal de fundos da CVM",
    url="https://dados.cvm.gov.br/dataset/fi-doc-perfil_mensal",
    uso=(
        "de quem é o dinheiro de cada classe: PF e PJ de varejo e private, conta e ordem, "
        "fundos, previdência e institucionais"
    ),
    url_dados="https://dados.cvm.gov.br/dados/FI/DOC/PERFIL_MENSAL/DADOS/",
)
CVM_CDA_INDUSTRIA = replace(
    CVM_CDA,
    uso="master, feeder, espelho e FoF: em que fundos cada classe investe",
)
CVM_EXTRATO_INDUSTRIA = replace(
    CVM_EXTRATO,
    uso="prazo de resgate e o flag de fundo espelho declarado",
)
FONTES_INDUSTRIA = (
    CVM_CADASTRO_INDUSTRIA, CVM_INFORME_DIARIO_INDUSTRIA, CVM_PERFIL_MENSAL,
    CVM_CDA_INDUSTRIA, CVM_EXTRATO_INDUSTRIA,
)


def fontes_da_industria(com_carteira: bool = True) -> list[Fonte]:
    """As fontes do monitor da industria; sem a carteira quando ela nao entrou."""
    return [f for f in FONTES_INDUSTRIA if com_carteira or f is not CVM_CDA_INDUSTRIA]


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
