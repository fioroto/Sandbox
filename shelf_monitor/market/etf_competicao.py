"""Competicao em ETFs: quem ganha dinheiro onde, e onde a XP compete.

O catalogo (`market/etf.py`) responde "quantos ETFs existem" e o fluxo
(`market/etf_flow.py`) "quanto entrou". Este modulo responde a pergunta
competitiva: em cada mercado de ETF, quem lidera, onde a XP esta, onde ela nao
esta, quem esta ganhando participacao e quem esta lancando o que.

Segmento, nao categoria
-----------------------

Dois ETFs competem quando replicam a mesma coisa: BOVA11, BOVV11 e o Ibovespa da
Trend disputam o mesmo investidor; um ETF de Ibovespa e um de dividendos nao. As
cinco categorias do catalogo (Acoes Brasil, Renda Fixa...) sao largas demais
para isso, e o benchmark que deveria separar os segmentos nao existe na origem:
medido em 2026-09-24, `Indicador_Desempenho`, `Classificacao` e
`Classificacao_Anbima` vem VAZIOS nas 252 classes FIIM do cadastro da CVM. O que
cada ETF replica esta no nome ("IT NOW IMA-B5 P2", "TREND ETF LBMA OURO"), e
`segmento_do_etf` le dali, com regras em ordem - a primeira que casa decide.

Participacao dentro do segmento isola o fluxo
---------------------------------------------

O balancete de FIE da o PL mensal de cada ETF, mas nao separa captacao de
valorizacao (ver `market/fie_balancete.py`). Dentro de um segmento, porem, os
ETFs replicam o mesmo indice e tem a mesma valorizacao: se a participacao da XP
no segmento cai, o dinheiro novo foi para os concorrentes. A variacao de
participacao no segmento e a leitura de fluxo que o balancete permite. Vale
menos em segmento que junta indices diferentes (Inflacao mistura IMA-B e
NTN-B de prazos distintos), e o bloco diz isso junto do numero.

A captacao de verdade, mes a mes, esta em `etf_captacao_mensal`: a variacao de
cotas entre os fechamentos mensais do Fundos.NET, nas mesmas visoes deste bloco.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from shelf_monitor.config import WATCHLIST
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.fundosnet import ajustes_do_informe
from shelf_monitor.normalize import normalize_text
from shelf_monitor.report.shared.numeros import pct, pp, reais  # noqa: F401 - reexportados
from shelf_monitor.texto import contagem

# --- segmento ---------------------------------------------------------------

# (grupo, segmento, regex sobre o nome normalizado - maiusculas, sem acento, sem
# pontuacao: "S&P 500" vira "S P 500", "B3 BR+" vira "B3 BR"). A ordem decide:
# opcoes antes de tudo (um "Nasdaq-100 high income" nao compete com o Nasdaq),
# cripto antes de Nasdaq ("HASHDEX NASDAQ BITCOIN"), credito antes de IPCA
# ("DEBENTURES INCENTIVADAS IPCA"), duration-alvo antes de Selic e de Treasury
# ("BRAZIL TREASURY 760 DAY TARGET"), S&P 500 antes de dolar ("S&P 500 FUTURES
# USD-BRL"), temas internacionais antes de small caps ("S&P SMALLCAP 600"),
# fatores antes de Ibovespa ("NU IBOV SMART LOW VOLATILITY"), o hibrido de
# infraestrutura antes de credito ("TEVA INFRAESTRUTURA HIBRIDO") e a familia
# IDEX B antes de credito ("TREND ETF IDEX B35", que e NTN-B).
_REGRAS: tuple[tuple[str, str, str], ...] = (
    ("Internacional", "Renda com opções (high income)",
     r"\bHIGH INCOME\b|\bCOVERED CALL\b|\bBOOSTED\b"),
    ("Cripto", "Cesta e temáticos cripto",
     r"\bGOLD BITCOIN\b|\bCRYPTO\b|\bCRIPTO|\bDEFI\b|\bSMART CONTRACT|\bMETAVERSE\b"
     r"|\bHASHDEX MOMENTUM\b|\bBITCOIN TREASURY\b|\bFEAR ARBITRAGE\b"),
    ("Cripto", "Ethereum", r"\bETHER"),
    ("Cripto", "Solana e XRP", r"\bSOLANA\b|\bXRP\b"),
    ("Cripto", "Bitcoin", r"\bBITCOIN\b|\bBTC\b"),
    ("Ações Brasil", "Ibovespa alavancado", r"\bALAVANCAD"),
    # 80% em acoes de energia, saneamento e concessoes e 20% em debenture
    # incentivada ou Tesouro IPCA+ (BTG Pactual Teva Infraestrutura Hibrido,
    # lancado em 02/09/2026, segundo o Seu Dinheiro). "INFRAESTRUTURA" sozinho o
    # levava a credito privado.
    ("Ações Brasil", "Setoriais e temáticos Brasil", r"\bINFRAESTRUTURA HIBRIDO\b"),
    # A familia IDEX B da XP replica a NTN-B de um vencimento: na lista de
    # fundos listados da B3 (24/09/2026), "TREND ETF IDEX B35" e o XB3511, com
    # nome de pregao "TREND NTNB35". "IDEX" sozinho continua credito (IDEX CDI,
    # IDEX Infra).
    ("Renda Fixa", "Inflação (IMA-B e Tesouro IPCA)", r"\bIDEX B\d\d\b"),
    # "INFRAESTRUTURA" sozinho e credito: a B3 chama de ETF de infraestrutura o
    # que carrega debenture incentivada (regra de listagem de 03/2025).
    ("Renda Fixa", "Crédito privado (debêntures e LF)",
     r"\bDEBENTURE|\bLETRAS? FINANCEIRAS?\b|\bIDEX\b|\bIDEB\b|\bCDI PLUS\b"
     r"|\bCDI MARGEM\b|\bINFRAESTRUTURA\b|\bINFRA TOP\b"),
    ("Renda Fixa", "Tesouro com duration-alvo", r"\bTARGET\b|\bSELIC IPCA\b"),
    ("Renda Fixa", "Inflação (IMA-B e Tesouro IPCA)",
     r"\bIMA ?B|\bNTN ?B\b|\bIPCA\b|\bDAP\d"),
    ("Renda Fixa", "Prefixado (IRF-M e Tesouro pré)",
     r"\bIRF ?M\b|\bIRFM\b|\bPRE\b|\bPREFIXADO\b|\bTAXAS DE JUROS\b"),
    ("Renda Fixa", "Tesouro Selic e CDI", r"\bSELIC\b|\bLFT\b|\bCDI\b|\bITBR LIQUIDEZ\b"),
    ("Internacional", "Renda fixa internacional",
     r"\bTREASURY\b|\bBONDS?\b|\bTESOURO AMERICANO\b|\bIBOXX\b|\bT BILL\b"
     r"|\bLEVERAGED LOAN\b|\bCLO\b|\bAGGREGATE\b"),
    ("Commodities e moedas", "Ouro", r"\bOURO\b|\bGOLD\b"),
    ("Commodities e moedas", "Prata", r"\bPRATA\b|\bSILVER\b"),
    ("Internacional", "S&P 500", r"\bS ?P ?500\b"),
    ("Commodities e moedas", "Dólar", r"\bDOLAR\b|\bCAMBIAL\b"),
    ("Commodities e moedas", "Agro (futuros)", r"\bBOI GORDO\b|\bMILHO\b"),
    ("Imobiliário", "FII (IFIX) e REITs Brasil", r"\bIFIX\b|\bBRASIL EQUITY REITS\b"),
    ("Internacional", "Nasdaq e tecnologia",
     r"\bNASDAQ\b|\bFANG|\bTECHNOLOGY\b|\bSEMICONDUCTOR\b|\bMAGNIFICENT\b"
     r"|\bDIGITAL\b|\bAI GLOBAL\b|\bVIDEO GAMING\b"),
    ("Internacional", "China e emergentes",
     r"\bCHINA\b|\bCHINEXT\b|\bCSI 300\b|\bA50\b|\bEMERGING\b|\bARGENTINA\b"),
    ("Internacional", "Ações globais",
     r"\bALL ?WORLD\b|\bALL COUNTRIES\b|\bGLOBAL EQUITIES\b|\bGLOBAL ALL CAP\b"
     r"|\bACWI\b|\bUS TOTAL MARKET\b"),
    ("Internacional", "Temáticos internacionais",
     r"\bURANIUM\b|\bHEALTHCARE\b|\bOIL\b|\bRARE EARTH\b|\bMETALS\b"
     r"|\bBUSINESS DEVELOPMENT\b|\bALTERNATIVE ASSET\b|\bBILLIONAIRES\b"
     r"|\bGREEN REVENUES\b|\bSMALLCAP 600\b|\bMINERAIS CRITICOS\b|\bREAL ESTATE\b"
     r"|\bRUSSELL\b"),
    ("Ações Brasil", "Dividendos", r"\bDIVIDEND|\bIDIV\b"),
    ("Ações Brasil", "Small caps", r"\bSMALL ?CAPS?\b|\bSMLL\b|\bMICRO CAP\b"),
    ("Ações Brasil", "Ibovespa BR+ e variações", r"\bBR\b|\bESTATAIS\b|\bPRIVADAS\b"),
    ("Ações Brasil", "Fatores e smart beta",
     r"\bSMART\b|\bLOW VOLATILITY\b|\bHIGH BETA\b|\bQUALITY\b|\bMOMENTO\b"
     r"|\bPESOS IGUAIS\b|\bEQUAL WEIGHT\b|\bFUNDAMENTOS\b|\bFATORES\b|\bMULTIFACTOR\b"
     r"|\bDEFENSIV|\bCICLIC|\bBESST\b|\bMARKET NEUTRAL\b|\bGIANT VALOR\b|\bSELECTOR\b"
     r"|\bINGENIUS\b"),
    ("Ações Brasil", "Ibovespa", r"\bIBOVESPA\b|\bIBOV\b"),
    ("Ações Brasil", "IBrX e Brasil amplo", r"\bIBRX|\bINDICE BRASIL\b|\bPIBB\b|\bMSCI BRAZIL\b"),
    ("Ações Brasil", "ESG e sustentabilidade",
     r"\bISE\b|\bIGCT\b|\bICO2\b|\bCARBONO\b|\bDIVERSIDADE\b|\bESG\b|\bMULHERES\b"
     r"|\bSUSTENTAV|\bAMAZONIA\b|\bENERGIA LIMPA\b"),
    ("Ações Brasil", "Setoriais e temáticos Brasil",
     r"\bIFNC\b|\bIMAT\b|\bUTILIDADE PUBLICA\b|\bBANCOS\b|\bCOMMODITIES\b|\bIAGRO"
     r"|\bSETORES\b|\bATIVOS REAIS\b|\bDOMESTIC EXPOSURE\b|\bGLOBAL EXPOSURE\b"),
    ("Ações Brasil", "Outras ações Brasil", r"\bACOES\b|\bACAO\b"),
)
_REGRAS_COMPILADAS = tuple((g, s, re.compile(p)) for g, s, p in _REGRAS)
SEM_SEGMENTO = ("Outros", "Sem segmento identificado")


def segmento_do_etf(nome: str | None) -> tuple[str, str]:
    """(grupo, segmento) do que o ETF replica, lido do nome.

    "TREND ETF LBMA OURO CLASSE DE INDICE..." -> ("Commodities e moedas", "Ouro").
    """
    texto = normalize_text(nome)
    for grupo, segmento, regra in _REGRAS_COMPILADAS:
        if regra.search(texto):
            return grupo, segmento
    return SEM_SEGMENTO


# --- gestora ----------------------------------------------------------------

# Rotulo curto por gestora, com a marca dos ETFs entre parenteses quando ela
# e a forma pela qual o investidor conhece o produto. Casado sobre o gestor
# normalizado; o nome do ETF entra quando o cadastro nao traz gestor.
_GESTORAS: tuple[tuple[str, str], ...] = (
    (r"^ITAU UNIBANCO ASSET|^IT NOW\b", "Itaú Asset (It Now)"),
    (r"^BLACKROCK|^ISHARES\b", "BlackRock (iShares)"),
    (r"^BTG PACTUAL", "BTG Pactual"),
    (r"^BANCO BRADESCO|^BRAM\b|^BRADESCO|^B ?INDEX\b|^ETF BRADESCO", "Bradesco (B-Index)"),
    (r"^XP |^TREND ETF\b", "XP (Trend)"),
    (r"^INVESTO\b", "Investo"),
    (r"^HASHDEX\b", "Hashdex"),
    (r"^BB GESTAO|^BB ETF\b|^BB ASSET", "BB Asset"),
    (r"^NU ASSET|^NU\b", "Nu Asset"),
    (r"^BUENA VISTA", "Buena Vista"),
    (r"^QR ASSET|^QR\b", "QR Asset"),
    (r"^GALAPAGOS", "Galápagos"),
    (r"^PHRONESIS", "Phronesis"),
    (r"^GRAO\b|^CLASSE UNICA GRAO", "Grão"),
    (r"^V8 CAPITAL", "V8 Capital"),
    (r"^EMPIRICUS", "Empiricus"),
    (r"^RIO BRAVO", "Rio Bravo"),
    (r"^PLURAL|^GENIAL", "Plural (Genial)"),
    (r"^SAFRA", "Safra"),
    (r"^TRIGONO|^ETF TRIGONO", "Trígono"),
    (r"^CAIXA", "Caixa"),
    (r"^HEDGE INVESTMENTS|^HEDGE BRASIL", "Hedge"),
    (r"^3R GESTORA|^ORANJEBTC", "3R (OranjeBTC)"),
    (r"^LETO\b", "Leto"),
)
_GESTORAS_COMPILADAS = tuple((re.compile(p), rotulo) for p, rotulo in _GESTORAS)
GESTORA_DESCONHECIDA = "Gestora não informada"


def rotulo_da_gestora(gestor: str | None, nome_etf: str | None = None) -> str:
    """"XP ALLOCATION ASSET MANAGEMENT LTDA" -> "XP (Trend)".

    Sem gestor no cadastro (1 dos 252 ETFs em 2026-09-24), tenta a marca no
    nome do ETF - "QR BLOOMBERG DEFI" e da QR Asset.
    """
    for texto in (normalize_text(gestor), normalize_text(nome_etf)):
        if not texto:
            continue
        for regra, rotulo in _GESTORAS_COMPILADAS:
            if regra.search(texto):
                return rotulo
    if gestor:
        return " ".join(p.capitalize() for p in normalize_text(gestor).split()[:2])
    return GESTORA_DESCONHECIDA


def casa_da_gestora(gestor: str | None) -> str | None:
    """A casa monitorada (xp, btg, itau, nu, inter) a que a gestora pertence."""
    texto = normalize_text(gestor)
    if not texto:
        return None
    for casa, watch in WATCHLIST.items():
        if any(re.search(p, texto) for p in watch.name_patterns):
            return casa
    return None


# --- o bloco competitivo ----------------------------------------------------

# Segmento com pelo menos este PL entra na lista de oportunidades quando a casa
# em foco tem menos de _SHARE_PEQUENA dele. Abaixo disso o segmento inteiro e
# menor que o ETF medio da XP (R$ 276 mi em 2026-09-24): nao e prioridade.
_PL_OPORTUNIDADE = 500_000_000.0
_SHARE_PEQUENA = 0.10


@dataclass
class _Etf:
    cnpj: str
    nome: str
    gestora: str
    casa: str | None
    ticker: str | None
    grupo: str
    segmento: str
    situacao: str
    pl: float = 0.0
    pl_inicio: float = 0.0
    cotistas: int | None = None
    fluxo: float | None = None
    pre_operacional: bool = False
    estreia: bool = False
    # Comecou a operar depois do ultimo balancete: o PL so existe no informe
    # do Fundos.NET, de outra data, e fica fora das somas.
    recente: bool = False
    pl_informe: float | None = None
    data_informe: date | None = None

    @property
    def operando(self) -> bool:
        return self.pl > 0


@dataclass
class _Soma:
    pl: float = 0.0
    pl_inicio: float = 0.0
    etfs: int = 0
    pre_operacionais: int = 0
    recentes: int = 0
    cotistas: int = 0
    fluxo: float = 0.0
    com_fluxo: int = 0
    por_gestora: dict[str, float] = field(default_factory=dict)
    por_gestora_inicio: dict[str, float] = field(default_factory=dict)

    def somar(self, etf: _Etf) -> None:
        self.pl += etf.pl
        self.pl_inicio += etf.pl_inicio
        if etf.pre_operacional:
            self.pre_operacionais += 1
        elif etf.operando:
            self.etfs += 1
        elif etf.recente:
            self.recentes += 1
        self.cotistas += etf.cotistas or 0
        if etf.fluxo is not None:
            self.fluxo += etf.fluxo
            self.com_fluxo += 1
        self.por_gestora[etf.gestora] = self.por_gestora.get(etf.gestora, 0.0) + etf.pl
        self.por_gestora_inicio[etf.gestora] = (
            self.por_gestora_inicio.get(etf.gestora, 0.0) + etf.pl_inicio
        )


def _share(parte: float, total: float) -> float | None:
    return parte / total if total else None


def _cotistas_e_fluxo(
    informes: list,
) -> tuple[dict[str, int], dict[str, float], date | None, date | None]:
    """Por CNPJ: cotistas e fluxo publicado na janela de informes, e a janela.

    Cotistas e o MAIOR valor do ETF na janela. O informe do Fundos.NET traz erro
    de preenchimento que derruba o numero num dia (IT NOW IDIV: 854 contra
    52.587 do dia anterior, em 2026-09-15, com o PL parado); o maximo atravessa
    isso, e numa semana a saida real de cotistas e pequena perto dele.

    O fluxo soma captacao menos resgate dos informes DEPOIS do primeiro: a
    captacao do dia que serve de base ja esta dentro do PL daquele dia - a
    correcao que o agregador semanal documenta em `etf_flow`. Cada dia confere
    contra a variacao de cotas (`fundosnet.ajustes_do_informe`): o Itau Asset,
    que declara zero em parte dos ETFs, entra pela diferenca de PL.
    """
    por_cnpj: dict[str, list] = {}
    for informe in informes or []:
        if informe.cnpj and informe.data:
            por_cnpj.setdefault(informe.cnpj, []).append(informe)
    cotistas: dict[str, int] = {}
    fluxo: dict[str, float] = {}
    datas = [i.data for lista in por_cnpj.values() for i in lista]
    for cnpj, lista in por_cnpj.items():
        lista.sort(key=lambda i: i.data)
        valores = [i.cotistas for i in lista if i.cotistas]
        if valores:
            cotistas[cnpj] = max(valores)
        ajustes = ajustes_do_informe(lista)
        publicados = [
            (i.captacao or 0.0) - (i.resgates or 0.0) + ajuste
            for i, (ajuste, _valeu) in list(zip(lista, ajustes))[1:]
            if i.captacao is not None or i.resgates is not None
        ]
        if publicados:
            fluxo[cnpj] = sum(publicados)
    return cotistas, fluxo, (min(datas) if datas else None), (max(datas) if datas else None)


def _pl_informado(informes: list) -> dict[str, tuple[float, date]]:
    """Por CNPJ: o PL e a data do informe mais recente do Fundos.NET."""
    ultimo: dict[str, tuple[float, date]] = {}
    for informe in informes or []:
        if informe.cnpj and informe.data and informe.pl:
            atual = ultimo.get(informe.cnpj)
            if atual is None or informe.data > atual[1]:
                ultimo[informe.cnpj] = (informe.pl, informe.data)
    return ultimo


def build_etf_competicao(
    catalog: dict[str, EtfMetadata],
    pl_series: list,
    informes: list | None = None,
    casa_foco: str = "xp",
) -> dict:
    """Quem lidera cada mercado de ETF, e onde a casa em foco compete.

    `pl_series` e a serie do balancete de FIE (`fie_balancete.monthly_pl_series`):
    o PL e o da competencia mais recente, e a comparacao e contra a mais antiga
    da serie. `informes` sao os do Fundos.NET (`fundosnet.read_fnet_csvs`), de
    onde saem cotistas e o fluxo publicado; sem eles o bloco sai sem essas duas
    leituras.
    """
    etfs_catalogo = {c: m for c, m in catalog.items() if m.is_etf}
    competencias = sorted({s.competencia for s in pl_series if s.cnpj in etfs_catalogo})
    if not etfs_catalogo or not competencias:
        return {
            "available": False,
            "reason": (
                "sem PL de ETF no balancete de FIE - rode `etf` com a rede liberada "
                "para dados.cvm.gov.br"
            ),
        }
    ultima, primeira = competencias[-1], competencias[0]
    pl_fim = {s.cnpj: s.pl for s in pl_series if s.competencia == ultima}
    pl_ini = {s.cnpj: s.pl for s in pl_series if s.competencia == primeira}
    data_fim = max(
        (s.dt_comptc for s in pl_series if s.competencia == ultima and s.dt_comptc), default=None
    )
    data_ini = max(
        (s.dt_comptc for s in pl_series if s.competencia == primeira and s.dt_comptc),
        default=None,
    )
    cotistas, fluxo, janela_de, janela_ate = _cotistas_e_fluxo(informes or [])
    informado = _pl_informado(informes or [])
    com_pl_no_balancete = {s.cnpj for s in pl_series if s.pl}

    etfs: list[_Etf] = []
    for cnpj, meta in etfs_catalogo.items():
        grupo, segmento = segmento_do_etf(meta.name)
        situacao = meta.situation or ""
        etf = _Etf(
            cnpj=cnpj, nome=meta.name, ticker=meta.ticker,
            gestora=rotulo_da_gestora(meta.manager_name, meta.name),
            casa=casa_da_gestora(meta.manager_name),
            grupo=grupo, segmento=segmento, situacao=situacao,
            pl=pl_fim.get(cnpj, 0.0) or 0.0, pl_inicio=pl_ini.get(cnpj, 0.0) or 0.0,
            cotistas=cotistas.get(cnpj), fluxo=fluxo.get(cnpj),
            pre_operacional="PRE OPERACIONAL" in normalize_text(situacao),
        )
        # Estreia: PL na competencia mais recente e nenhum na primeira. A data
        # de registro nao serve para isso: a migracao para a RCVM 175 a
        # reescreveu - o iShares Ibovespa, de 2008, aparece registrado em
        # 2025-04-23 no cadastro de 2026-09-24.
        etf.estreia = primeira != ultima and etf.pl > 0 and not etf.pl_inicio
        # Comecou depois do balancete: nenhum PL na serie e PL num informe
        # posterior a data-base. Medido em 2026-09-24: 8 ETFs, R$ 266 mi no
        # Fundos.NET em setembro, 2 da XP, sumiam do radar de lancamentos.
        pl_informe, data_informe = informado.get(cnpj, (None, None))
        if (
            cnpj not in com_pl_no_balancete and pl_informe and data_informe
            and (data_fim is None or data_informe > data_fim)
        ):
            etf.recente, etf.pl_informe, etf.data_informe = True, pl_informe, data_informe
            # O informe mostra o ETF operando: vale mais que a situacao do cadastro.
            etf.pre_operacional = False
        etfs.append(etf)

    foco_rotulo = next((e.gestora for e in etfs if e.casa == casa_foco), None)
    total = _Soma()
    por_gestora: dict[str, _Soma] = {}
    por_segmento: dict[tuple[str, str], _Soma] = {}
    for etf in etfs:
        total.somar(etf)
        por_gestora.setdefault(etf.gestora, _Soma()).somar(etf)
        por_segmento.setdefault((etf.grupo, etf.segmento), _Soma()).somar(etf)

    segmentos = [
        _linha_de_segmento(chave, soma, etfs, foco_rotulo)
        for chave, soma in por_segmento.items()
        if soma.pl > 0 or soma.pre_operacionais or soma.recentes
    ]
    segmentos.sort(key=lambda s: -s["pl"])
    lideres: dict[str, list[str]] = {}
    for seg in segmentos:
        if seg["lider"]:
            lideres.setdefault(seg["lider"], []).append(seg["segmento"])

    gestoras = []
    for rotulo, soma in por_gestora.items():
        share = _share(soma.pl, total.pl)
        share_inicio = _share(soma.pl_inicio, total.pl_inicio)
        gestoras.append({
            "gestora": rotulo,
            "casa": next((e.casa for e in etfs if e.gestora == rotulo and e.casa), None),
            "foco": rotulo == foco_rotulo,
            "etfs": soma.etfs,
            "pre_operacionais": soma.pre_operacionais,
            "recentes": soma.recentes,
            "pl": round(soma.pl, 2),
            "share": share,
            "pl_inicio": round(soma.pl_inicio, 2),
            "share_inicio": share_inicio,
            "delta_share_pp": (
                (share - share_inicio) * 100
                if share is not None and share_inicio is not None else None
            ),
            "variacao_pl": round(soma.pl - soma.pl_inicio, 2),
            "variacao_pct": (soma.pl / soma.pl_inicio - 1) if soma.pl_inicio else None,
            "cotistas": soma.cotistas or None,
            "share_cotistas": _share(soma.cotistas, total.cotistas) if soma.cotistas else None,
            "pl_por_cotista": (soma.pl / soma.cotistas) if soma.cotistas else None,
            "fluxo": round(soma.fluxo, 2) if soma.com_fluxo else None,
            "lidera": lideres.get(rotulo, []),
        })
    gestoras.sort(key=lambda g: -g["pl"])
    for posicao, gestora in enumerate(gestoras, start=1):
        gestora["posicao"] = posicao

    base_fim = _data(data_fim.isoformat() if data_fim else None)
    base_ini = _data(data_ini.isoformat() if data_ini else None)
    lancamentos = []
    for e in etfs:
        if e.pre_operacional:
            chave, tipo, pl, data_pl = "pre_operacional", "pré-operacional", 0.0, None
        elif e.recente:
            chave = "recente"
            tipo = f"começou a operar depois do balancete de {base_fim} (Fundos.NET)"
            pl, data_pl = e.pl_informe or 0.0, e.data_informe
        elif e.estreia:
            chave = "estreia"
            tipo = f"começou a operar entre os balancetes de {base_ini} e {base_fim}"
            pl, data_pl = e.pl, data_fim
        else:
            continue
        lancamentos.append({
            "gestora": e.gestora, "foco": e.gestora == foco_rotulo, "nome": e.nome, "ticker": e.ticker,
            "grupo": e.grupo, "segmento": e.segmento, "chave": chave, "tipo": tipo,
            "pl": round(pl, 2), "data_pl": data_pl.isoformat() if data_pl else None,
        })
    lancamentos.sort(key=lambda l: (
        not l["foco"], l["chave"] != "pre_operacional", -l["pl"], l["gestora"], l["nome"]
    ))

    participacao_mensal = _participacao_mensal(etfs, pl_series, competencias, foco_rotulo)

    oportunidades = [
        s for s in segmentos
        if s["pl"] >= _PL_OPORTUNIDADE
        and (s["foco_share"] is None or s["foco_share"] < _SHARE_PEQUENA)
    ]
    defesa = [s for s in segmentos if s["foco_status"] == "lidera"]

    bloco = {
        "available": True,
        "casa_foco": casa_foco,
        "foco": foco_rotulo,
        "competencia": ultima,
        "competencia_inicio": primeira,
        "data_pl": data_fim.isoformat() if data_fim else None,
        "data_pl_inicio": data_ini.isoformat() if data_ini else None,
        "pl_total": round(total.pl, 2),
        "pl_total_inicio": round(total.pl_inicio, 2),
        "etfs_catalogo": len(etfs_catalogo),
        "etfs_com_pl": sum(1 for e in etfs if e.pl > 0),
        "pre_operacionais": sum(1 for e in etfs if e.pre_operacional),
        "limiares": {"pl_oportunidade": _PL_OPORTUNIDADE, "share_pequena": _SHARE_PEQUENA},
        "cotistas": {
            "etfs": len(cotistas),
            "total": total.cotistas or None,
            "de": janela_de.isoformat() if janela_de else None,
            "ate": janela_ate.isoformat() if janela_ate else None,
        },
        "fluxo": {
            "etfs": len(fluxo),
            "total": round(total.fluxo, 2) if total.com_fluxo else None,
            "de": janela_de.isoformat() if janela_de else None,
            "ate": janela_ate.isoformat() if janela_ate else None,
        },
        "gestoras": gestoras,
        "participacao_mensal": participacao_mensal,
        "segmentos": segmentos,
        "oportunidades": oportunidades,
        "defesa": defesa,
        "lancamentos": lancamentos,
        "etfs": [
            {
                "cnpj": e.cnpj, "nome": e.nome, "ticker": e.ticker, "gestora": e.gestora,
                "foco": e.gestora == foco_rotulo, "grupo": e.grupo, "segmento": e.segmento,
                "situacao": e.situacao, "pl": round(e.pl, 2),
                "pl_inicio": round(e.pl_inicio, 2), "cotistas": e.cotistas,
                "fluxo": None if e.fluxo is None else round(e.fluxo, 2),
                "pre_operacional": e.pre_operacional, "estreia": e.estreia,
                "recente": e.recente,
                "pl_informe": None if e.pl_informe is None else round(e.pl_informe, 2),
                "data_informe": e.data_informe.isoformat() if e.data_informe else None,
            }
            for e in sorted(etfs, key=lambda e: (e.grupo, e.segmento, -e.pl, e.nome))
        ],
    }
    bloco["leitura"] = leitura_etf_competicao(bloco)
    return bloco


def _participacao_mensal(
    etfs: list[_Etf], pl_series: list, competencias: list[str], foco: str | None
) -> dict:
    """Participacao de cada gestora no PL de ETFs, competencia a competencia.

    A comparacao do bloco usa so as pontas; esta e a trajetoria entre elas, para
    separar tendencia de um mes atipico.
    """
    gestora_do = {e.cnpj: e.gestora for e in etfs}
    pl: dict[str, dict[str, float]] = {}
    total: dict[str, float] = {c: 0.0 for c in competencias}
    for s in pl_series:
        gestora = gestora_do.get(s.cnpj)
        if gestora is None or s.competencia not in total:
            continue
        pl.setdefault(gestora, {}).setdefault(s.competencia, 0.0)
        pl[gestora][s.competencia] += s.pl
        total[s.competencia] += s.pl
    ultima = competencias[-1]
    linhas = [
        {
            "gestora": gestora,
            "foco": gestora == foco,
            "pl": {c: round(valores.get(c, 0.0), 2) for c in competencias},
            "share": {c: _share(valores.get(c, 0.0), total[c]) for c in competencias},
        }
        for gestora, valores in pl.items()
    ]
    linhas.sort(key=lambda linha: -(linha["pl"].get(ultima) or 0.0))
    return {"competencias": list(competencias), "gestoras": linhas}


def _linha_de_segmento(
    chave: tuple[str, str], soma: _Soma, etfs: list[_Etf], foco: str | None
) -> dict:
    grupo, segmento = chave
    ranking = sorted(
        ((g, pl) for g, pl in soma.por_gestora.items() if pl > 0), key=lambda kv: -kv[1]
    )
    posicoes = {g: i for i, (g, _) in enumerate(ranking, start=1)}
    lider, pl_lider = ranking[0] if ranking else (None, 0.0)
    segundo, pl_segundo = ranking[1] if len(ranking) > 1 else (None, 0.0)
    foco_pl = soma.por_gestora.get(foco, 0.0) if foco else 0.0
    foco_pl_inicio = soma.por_gestora_inicio.get(foco, 0.0) if foco else 0.0
    do_segmento = [e for e in etfs if (e.grupo, e.segmento) == chave]
    foco_pre = [e.nome for e in do_segmento if e.gestora == foco and e.pre_operacional]
    foco_recentes = [e.nome for e in do_segmento if e.gestora == foco and e.recente]
    if foco_pl > 0:
        status = "lidera" if posicoes.get(foco) == 1 else "presente"
    elif foco_pre or foco_recentes:
        status = "entrando"
    else:
        status = "ausente"
    foco_share = _share(foco_pl, soma.pl)
    foco_share_inicio = _share(foco_pl_inicio, soma.pl_inicio)
    return {
        "grupo": grupo,
        "segmento": segmento,
        "pl": round(soma.pl, 2),
        "pl_inicio": round(soma.pl_inicio, 2),
        "variacao_pct": (soma.pl / soma.pl_inicio - 1) if soma.pl_inicio else None,
        "etfs": soma.etfs,
        "gestoras": len(ranking),
        "lider": lider,
        "share_lider": _share(pl_lider, soma.pl),
        "segundo": segundo,
        "share_segundo": _share(pl_segundo, soma.pl),
        "foco_pl": round(foco_pl, 2),
        "foco_share": foco_share if foco_pl > 0 else None,
        "foco_share_inicio": foco_share_inicio if foco_pl_inicio > 0 else None,
        # So com a casa nas duas pontas: estrear no segmento nao e "ganhar
        # participacao", e sair dele aparece como ausencia.
        "foco_delta_pp": (
            (foco_share - foco_share_inicio) * 100
            if foco_pl > 0 and foco_pl_inicio > 0 else None
        ),
        "foco_posicao": posicoes.get(foco) if foco else None,
        "foco_etfs": sum(1 for e in do_segmento if e.gestora == foco and e.operando),
        "foco_status": status,
        "foco_pre_operacionais": foco_pre,
        "foco_recentes": foco_recentes,
        "pre_operacionais": [
            {"gestora": e.gestora, "nome": e.nome, "ticker": e.ticker}
            for e in do_segmento if e.pre_operacional
        ],
        "estreias": [
            {"gestora": e.gestora, "nome": e.nome, "ticker": e.ticker, "pl": round(e.pl, 2)}
            for e in do_segmento if e.estreia
        ],
        "recentes": [
            {
                "gestora": e.gestora, "nome": e.nome, "ticker": e.ticker,
                "pl": round(e.pl_informe or 0.0, 2),
                "data": e.data_informe.isoformat() if e.data_informe else None,
            }
            for e in do_segmento if e.recente
        ],
        # Quem esta chegando ao segmento: pre-operacional ou recem-estreado.
        "lancando": [
            {"gestora": e.gestora, "nome": e.nome, "ticker": e.ticker}
            for e in do_segmento if e.pre_operacional or e.recente
        ],
        "cotistas": soma.cotistas or None,
        "fluxo": round(soma.fluxo, 2) if soma.com_fluxo else None,
    }


# --- leitura ----------------------------------------------------------------

_MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def _competencia(yyyymm: str | None) -> str:
    """"202608" -> "ago/2026"."""
    if not yyyymm or len(yyyymm) != 6 or not yyyymm.isdigit():
        return yyyymm or "-"
    return f"{_MESES[int(yyyymm[4:]) - 1]}/{yyyymm[:4]}"


# `reais`, `pct` e `pp` moram em report/shared/numeros.py desde que a
# industria passou a usa-los; o import acima os mantem acessiveis por aqui.


def _lista(itens: list[str], separador: str = ", ") -> str:
    if len(itens) < 2:
        return "".join(itens)
    return separador.join(itens[:-1]) + (" e " if separador == ", " else f"{separador}e ") + itens[-1]


def curto(gestora: str | None) -> str:
    """"Itaú Asset (It Now)" -> "Itaú Asset": o rotulo sem a marca, para prosa."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", gestora or "")


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _data(iso: str | None) -> str:
    return "-" if not iso else f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"


def prazos_etf(bloco: dict) -> list[str]:
    """O prazo de cada leitura do bloco, para sair junto dos numeros."""
    if not bloco.get("available"):
        return []
    linhas = [
        f"PL: balancete de FIE de {_competencia(bloco['competencia'])} "
        f"(data-base {_data(bloco.get('data_pl'))})",
    ]
    if bloco["competencia_inicio"] != bloco["competencia"]:
        linhas.append(
            f"comparação: de {_competencia(bloco['competencia_inicio'])} "
            f"(data-base {_data(bloco.get('data_pl_inicio'))}) a "
            f"{_competencia(bloco['competencia'])}"
        )
    janela = bloco.get("cotistas") or {}
    if janela.get("de"):
        linhas.append(
            f"cotistas e fluxo: informes diários do Fundos.NET de {_data(janela['de'])} "
            f"a {_data(janela['ate'])}"
        )
    if any(l["chave"] == "recente" for l in bloco.get("lancamentos") or []):
        linhas.append(
            "ETFs que começaram a operar depois do balancete: PL do último informe de "
            "cada um no Fundos.NET, com a data ao lado"
        )
    return linhas


def leitura_etf_competicao(bloco: dict) -> list[str]:
    """As frases que abrem o bloco em toda superficie - uma fonte de verdade.

    Cada frase carrega o prazo do numero: o PL e o do balancete da competencia
    mais recente, a comparacao e contra a primeira da serie, e cotistas saem da
    janela de informes do Fundos.NET.
    """
    if not bloco.get("available"):
        return []
    foco = bloco.get("foco")
    gestoras = bloco["gestoras"]
    lider = gestoras[0] if gestoras else None
    da_casa = next((g for g in gestoras if g["foco"]), None)
    comp = _competencia(bloco["competencia"])
    comp_ini = _competencia(bloco["competencia_inicio"])
    frases = []

    abertura = (
        f"No PL de ETFs de {comp} ({reais(bloco['pl_total'])} em "
        f"{bloco['etfs_com_pl']} ETFs, balancete da CVM)"
    )
    if da_casa and lider:
        frases.append(
            f"{abertura}, a {foco} é a {da_casa['posicao']}ª gestora, com "
            f"{reais(da_casa['pl'])} ({pct(da_casa['share'])} do mercado) em "
            f"{da_casa['etfs']} ETFs; a líder é {lider['gestora']}, com "
            f"{pct(lider['share'])}."
        )
    elif lider:
        frases.append(f"{abertura}, a líder é {lider['gestora']}, com {pct(lider['share'])}.")
    if not foco:
        return frases

    lidera = [s for s in bloco["segmentos"] if s["foco_status"] == "lidera"]
    if lidera:
        frases.append(
            f"Lidera {len(lidera)} segmento{'s' if len(lidera) > 1 else ''}: "
            + _lista([f"{s['segmento']} ({pct(s['foco_share'], 0)} do segmento)" for s in lidera[:5]])
            + "."
        )
    ausentes = [
        s for s in bloco["segmentos"]
        if s["foco_status"] in ("ausente", "entrando") and s["pl"] >= _PL_OPORTUNIDADE
    ]
    if ausentes:
        frases.append(
            f"Não tem ETF em {len(ausentes)} segmento{'s' if len(ausentes) > 1 else ''} "
            f"acima de {reais(_PL_OPORTUNIDADE)}, que somam "
            f"{reais(sum(s['pl'] for s in ausentes))}. Os maiores: "
            + _lista(
                [f"{s['segmento']}, {reais(s['pl'])} com {curto(s['lider'])} na liderança"
                 for s in ausentes[:4]],
                separador="; ",
            )
            + "."
        )
    pequena = [
        s for s in bloco["segmentos"][:4]
        if s["foco_status"] == "presente" and (s["foco_share"] or 0) < _SHARE_PEQUENA
    ]
    if pequena:
        frases.append(
            "Nos maiores segmentos em que está, é pequena: "
            + _lista(
                [f"{s['segmento']} ({reais(s['pl'])}), {pct(s['foco_share'])} e "
                 f"{s['foco_posicao']}ª de {s['gestoras']} gestoras" for s in pequena],
                separador="; ",
            )
            + "."
        )
    if da_casa and da_casa.get("delta_share_pp") is not None and comp_ini != comp:
        movidos = sorted(
            (s for s in bloco["segmentos"] if s["foco_delta_pp"] is not None),
            key=lambda s: -abs(s["foco_delta_pp"]),
        )[:3]
        frase = (
            f"De {comp_ini} a {comp}, a participação no PL de ETFs foi de "
            f"{pct(da_casa['share_inicio'])} para {pct(da_casa['share'])}"
        )
        if movidos:
            frase += (
                "; dentro dos segmentos, onde a valorização é a mesma para todos e a "
                "mudança de participação é fluxo: "
                + _lista([f"{s['segmento']} {pp(s['foco_delta_pp'])}" for s in movidos], "; ")
            )
        frases.append(frase + ".")
    pre = [l for l in bloco["lancamentos"] if l["chave"] == "pre_operacional"]
    if pre:
        da_casa_pre = [l for l in pre if l["foco"]]
        por_segmento: dict[str, set[str]] = {}
        for lancamento in pre:
            if not lancamento["foco"]:
                por_segmento.setdefault(lancamento["segmento"], set()).add(
                    curto(lancamento["gestora"])
                )
        quentes = sorted(por_segmento.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:2]
        frase = f"Em fase pré-operacional estão {len(pre)} ETFs"
        if da_casa_pre:
            frase += f", {len(da_casa_pre)} da {foco}"
        if quentes:
            frase += (
                "; os concorrentes preparam lançamentos sobretudo em "
                + _lista(
                    [f"{seg}, com {_lista(sorted(gs))}" for seg, gs in quentes],
                    separador="; ",
                )
            )
        frases.append(frase + ".")
    recentes = [l for l in bloco["lancamentos"] if l["chave"] == "recente"]
    if recentes:
        frase = (
            f"Depois do balancete de {_data(bloco.get('data_pl'))}, "
            f"{contagem(len(recentes), 'ETF começou', 'ETFs começaram')} a operar, com "
            f"{reais(sum(l['pl'] for l in recentes))} no último informe do Fundos.NET"
        )
        da_casa_recentes = [l for l in recentes if l["foco"]]
        if da_casa_recentes:
            frase += (
                f"; {len(da_casa_recentes)} da {foco}, em "
                + _lista(sorted({l["segmento"] for l in da_casa_recentes}))
            )
        frases.append(frase + ".")
    janela = bloco.get("cotistas") or {}
    if da_casa and da_casa.get("cotistas") and janela.get("total"):
        ticket_mercado = bloco["pl_total"] / janela["total"] if janela["total"] else None
        frase = (
            f"Nos informes do Fundos.NET de {_data(janela.get('de'))} a "
            f"{_data(janela.get('ate'))}, os ETFs da {foco} somam "
            f"{_milhar(da_casa['cotistas'])} cotistas - {pct(da_casa['share_cotistas'])} "
            f"dos cotistas de ETF, contra {pct(da_casa['share'])} do PL"
        )
        if da_casa.get("pl_por_cotista") and ticket_mercado:
            frase += (
                f": {reais_mil(da_casa['pl_por_cotista'])} por cotista, contra "
                f"{reais_mil(ticket_mercado)} no mercado"
            )
        frases.append(
            frase + " (cotista contado uma vez por ETF: quem tem dois conta duas vezes)."
        )
    return frases


def nome_com_ticker(nome: str, ticker: str | None) -> str:
    """"BOVA11 - ISHARES IBOVESPA ..." - o ticker e como o investidor conhece o ETF."""
    return f"{ticker} - {nome}" if ticker else nome


def reais_mil(valor: float | None) -> str:
    """"R$ 29 mil" - para PL por cotista; "R$ 2,0 mi" a partir de um milhao."""
    if valor is None:
        return "-"
    if abs(valor) >= 1e6:
        return f"R$ {valor / 1e6:.1f} mi".replace(".", ",")
    return f"R$ {valor / 1e3:,.0f} mil".replace(",", ".")
