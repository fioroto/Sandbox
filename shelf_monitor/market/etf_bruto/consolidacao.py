"""Consolidacao: uma linha por ETF, com tudo o que a captura bruta tem, numa planilha so.

Le o que o `etf-bruto` gravou em disco (nenhuma requisicao de rede) e monta,
para cada ETF do universo, as colunas dos 54 indicadores do mapeamento, na
ordem das 8 secoes:

- o que a fonte publica pronto vai como veio (ticker, ISIN, PL, cotistas...);
- o que o mapeamento chama de derivavel e calculado aqui, sobre os insumos
  capturados, com a janela no cabecalho da coluna (mediana de 63 pregoes,
  rentabilidade de 12 meses, volatilidade, premio, carteira da CVM...);
- o que so existe em PDF (regulamento, demonstracoes) sai como "pendente
  leitura pdf" - a menos que uma pessoa ja tenha conferido o valor em
  `data/etf/manual/regulamento.csv`, e ai vai o valor conferido;
- o que e da casa (classe XP, risco, formador) sai do que a pessoa preencheu
  em `data/etf/manual/`, ou "pendente cadastro".

Os calculos sao os mais simples que o mapeamento descreve, sem ajuste por
provento (a serie de proventos e da Comdinheiro): rentabilidade "preco" e
preco de fechamento contra preco de fechamento.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.etf_bruto import armazem, manual
from shelf_monitor.market.etf_bruto.b3_arquivos import caminho_incompletos, meses_antes, pasta_negocios
from shelf_monitor.market.etf_bruto.composicao import pasta_cda
from shelf_monitor.market.etf_bruto.fnet import ler_indice, pasta_informe
from shelf_monitor.market.etf_bruto.indices_b3 import pasta as pasta_indices
from shelf_monitor.market.etf_bruto.mapa import SECOES
from shelf_monitor.market.etf_bruto.universo import arquivos_datados, ler_universo, pasta_cvm_cadastro
from shelf_monitor.market.etf_competicao import rotulo_da_gestora, segmento_do_etf
from shelf_monitor.normalize import format_cnpj, normalize_cnpj, normalize_text, parse_number
from shelf_monitor.texto import competencia_br, data_br

PENDENTE_PDF = "pendente leitura pdf"
PENDENTE_CADASTRO = "pendente cadastro"
SEM_COMDINHEIRO = "sem Comdinheiro"
NAO_SE_APLICA = "não se aplica"

PREGOES_LIQUIDEZ = 63
PRESENCA_MINIMA = 0.9
# Pregoes de folga para achar o fechamento da ponta de uma janela (ETF pouco
# negociado pode nao ter negocio no dia exato).
FOLGA_DA_PONTA = 10
# Minimo de retornos diarios para volatilidade e beta de 12 meses.
MINIMO_DE_RETORNOS = 200
DIAS_DE_CAPTACAO = 30
TOP_POSICOES = 5
CATEGORIA_RF = "FIXED INCOME TRADABLE INSTRUMENT T1"
# Linhas da carteira da CVM que nao sao posicao (caixa, a pagar, a receber).
_NAO_E_POSICAO = {"VALORES A RECEBER", "VALORES A PAGAR", "DISPONIBILIDADES", "OPERACOES COMPROMISSADAS"}

_CATEGORIAS = {
    "ETF EQUITIES": "Ações",
    "ETF FOREIGN INDEX": "Índice estrangeiro",
    CATEGORIA_RF: "Renda fixa",
}


@dataclass(frozen=True)
class Coluna:
    chave: str
    titulo: str
    secao: str
    formato: str = "texto"  # texto, inteiro, dinheiro, preco, pct, pp, data, link
    indicador: str = ""     # nome do indicador no mapeamento
    como: str = ""          # de onde vem / como e calculado
    largura: int = 16


@dataclass
class Janelas:
    """As datas de cada janela, iguais para todos os ETFs - vao no cabecalho."""

    ultimo_pregao: date | None = None
    liquidez: tuple[date, date] | None = None
    doze_meses: tuple[date, date] | None = None
    ano: tuple[date, date] | None = None
    cota_12m: tuple[str, str] | None = None
    carteira: str | None = None
    captacao: tuple[date, date] | None = None


@dataclass
class Consolidado:
    colunas: list[Coluna]
    linhas: list[dict]
    janelas: Janelas
    gerado_em: datetime = field(default_factory=datetime.now)


# --- leitura do disco -----------------------------------------------------------------


def _data_iso(texto: str | None) -> date | None:
    texto = (texto or "").strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


class Insumos:
    """Tudo o que a consolidacao le, carregado uma vez."""

    def __init__(self, config: Config, hoje: date):
        self.config = config
        self.hoje = hoje
        self.universo = ler_universo(config)
        self._carregar_negocios()
        self._carregar_informes()
        self._carregar_mensal()
        self._carregar_cvm()
        self._carregar_carteira()
        self._carregar_indices()
        self._carregar_manual()
        self._carregar_documentos()

    # B3: fechamento, negocios e volume de cada ETF em cada pregao com arquivo completo.
    def _carregar_negocios(self) -> None:
        self.pregoes: list[date] = []
        self.por_ticker: dict[str, dict[date, tuple[float | None, float, float]]] = {}
        for dia, caminho in arquivos_datados(pasta_negocios(self.config), ".csv"):
            self.pregoes.append(dia)
            for linha in armazem.ler_csv(caminho):
                if linha.get("x_tipo_linha") != "padrao":
                    continue
                ticker = (linha.get("x_ticker_base") or linha.get("TckrSymb") or "").upper()
                fechamento = parse_number(linha.get("LastPric"))
                self.por_ticker.setdefault(ticker, {})[dia] = (
                    fechamento, parse_number(linha.get("TradQty")) or 0.0, parse_number(linha.get("NtlFinVol")) or 0.0,
                )
        self.incompletos = set(armazem.ler_json(caminho_incompletos(self.config), {}) or {})
        # Todo pregao da B3, com e sem arquivo completo: e a regua do "pregao
        # consecutivo". Sem os dias cujo consolidado veio sem o mercado a vista,
        # o retorno de dois dias em volta deles contava como diario (medido em
        # 2026-09-25: a volatilidade de 12 meses saia ~1% acima).
        self.calendario = sorted(set(self.pregoes) | {d for d in map(_data_iso, self.incompletos) if d})

    # FNET: o informe diario de cada ETF, do bruto e da coleta do `etf`.
    def _carregar_informes(self) -> None:
        self.informes: dict[str, dict[date, dict]] = {}

        def somar(cnpj, dia, cota, pl, cotistas, captacao, resgates):
            cnpj = normalize_cnpj(cnpj)
            if not cnpj or dia is None or (cota is None and pl is None):
                return
            self.informes.setdefault(cnpj, {})[dia] = {
                "cota": cota, "pl": pl, "cotistas": cotistas, "captacao": captacao, "resgates": resgates,
            }

        for _, caminho in arquivos_datados(self.config.etf_fnet_dir, ".csv"):
            for l in armazem.ler_csv(caminho):
                somar(l.get("cnpj"), _data_iso(l.get("data")), parse_number(l.get("cota")), parse_number(l.get("pl")),
                      parse_number(l.get("cotistas")), parse_number(l.get("captacao")), parse_number(l.get("resgates")))
        # O bruto por ultimo: versao entregue depois vence.
        for _, caminho in arquivos_datados(pasta_informe(self.config), ".csv"):
            for l in sorted(armazem.ler_csv(caminho), key=lambda x: int(x.get("doc_id") or 0)):
                somar(l.get("CNPJ_FDO") or l.get("cnpj_arquivo"), _data_iso(l.get("DT_COMPT") or l.get("data_referencia")),
                      parse_number(l.get("VL_QUOTA")), parse_number(l.get("PATRIM_LIQ")), parse_number(l.get("NR_COTST")),
                      parse_number(l.get("CAPTC_DIA")), parse_number(l.get("RESG_DIA")))

    # FNET: o fechamento de cada mes (ultimo informe do mes).
    def _carregar_mensal(self) -> None:
        self.meses: list[str] = []
        self.mensal: dict[str, dict[str, tuple[date | None, float | None]]] = {}
        pasta = self.config.etf_fnet_mensal_dir
        for caminho in sorted(pasta.glob("*.csv")) if pasta.exists() else []:
            if not caminho.stem.isdigit():
                continue
            self.meses.append(caminho.stem)
            for l in armazem.ler_csv(caminho):
                cnpj = normalize_cnpj(l.get("cnpj"))
                if cnpj:
                    self.mensal.setdefault(cnpj, {})[caminho.stem] = (_data_iso(l.get("data")), parse_number(l.get("cota")))

    # CVM: custodiante, auditor, constituicao (a do fundo guarda a original: a
    # da classe foi reiniciada na adaptacao a RCVM 175 em 71 de 252 ETFs).
    def _carregar_cvm(self) -> None:
        self.cvm: dict[str, dict] = {}
        pasta = pasta_cvm_cadastro(self.config)
        dias = sorted(p for p in pasta.iterdir() if p.is_dir()) if pasta.exists() else []
        if not dias:
            return
        fundos = {l.get("ID_Registro_Fundo"): l for l in armazem.ler_csv(dias[-1] / "registro_fundo_etf.csv")}
        for classe in armazem.ler_csv(dias[-1] / "registro_classe_etf.csv"):
            fundo = fundos.get(classe.get("ID_Registro_Fundo"), {})
            datas = [d for d in (_data_iso(classe.get("Data_Constituicao")), _data_iso(fundo.get("Data_Constituicao"))) if d]
            self.cvm[normalize_cnpj(classe.get("CNPJ_Classe"))] = {
                "custodiante": classe.get("Custodiante") or "",
                "auditor": classe.get("Auditor") or "",
                "gestor": fundo.get("Gestor") or "",
                "administrador": fundo.get("Administrador") or "",
                "constituicao": min(datas) if datas else None,
            }
        for fundo in fundos.values():
            cnpj = normalize_cnpj(fundo.get("CNPJ_Fundo"))
            if cnpj and cnpj not in self.cvm:
                self.cvm[cnpj] = {"custodiante": "", "auditor": "", "gestor": fundo.get("Gestor") or "",
                                  "administrador": fundo.get("Administrador") or "",
                                  "constituicao": _data_iso(fundo.get("Data_Constituicao"))}

    # CVM: a carteira mensal mais recente.
    def _carregar_carteira(self) -> None:
        self.carteira: dict[str, list[dict]] = {}
        self.competencia_carteira: str | None = None
        pasta = pasta_cda(self.config)
        arquivos = sorted(pasta.glob("cda_fie_etf_*.csv")) if pasta.exists() else []
        if not arquivos:
            return
        self.competencia_carteira = arquivos[-1].stem[-6:]
        for l in armazem.ler_csv(arquivos[-1]):
            cnpj = normalize_cnpj(l.get("CNPJ_FUNDO_CLASSE") or l.get("CNPJ_FUNDO"))
            if cnpj:
                self.carteira.setdefault(cnpj, []).append(l)

    # Indices B3: nivel de fechamento por dia.
    def _carregar_indices(self) -> None:
        self.indices: dict[str, dict[date, float]] = {}
        base = pasta_indices(self.config) / "serie"
        if not base.exists():
            return
        for pasta in base.iterdir():
            niveis: dict[date, float] = {}
            for arquivo in sorted(pasta.glob("*.json")):
                dados = armazem.ler_json(arquivo, {}) or {}
                ano = int(dados.get("ano") or arquivo.stem)
                for linha in ((dados.get("resposta") or {}).get("results") or []):
                    for mes in range(1, 13):
                        valor = parse_number(linha.get(f"rateValue{mes}"))
                        if valor is None:
                            continue
                        try:
                            niveis[date(ano, mes, int(linha.get("day")))] = valor
                        except (TypeError, ValueError):
                            continue
            self.indices[pasta.name] = niveis

    def _carregar_manual(self) -> None:
        def por_chave(arquivo: str) -> dict[str, dict]:
            return {(l.get("chave") or "").strip(): l for l in manual.ler(self.config, arquivo)}

        self.xp = por_chave("xp_cadastro.csv")
        self.formador = por_chave("formador.csv")
        self.indices_manual = por_chave("indices.csv")
        self.conferidos: dict[tuple[str, str], str] = {}
        for l in manual.ler(self.config, "regulamento.csv"):
            valor = (l.get("valor_conferido") or "").strip()
            if valor and (l.get("conferido_por") or "").strip():
                self.conferidos[((l.get("chave") or "").strip(), (l.get("campo") or "").strip())] = valor
        self.codigos_b3 = manual.codigos_b3(self.config, self.universo)

    # FNET: link do regulamento e da demonstracao mais recentes de cada CNPJ.
    def _carregar_documentos(self) -> None:
        self.documentos: dict[tuple[str, str], dict] = {}
        for l in ler_indice(self.config):
            cnpj = l.get("cnpj_arquivo") or l.get("cnpj_catalogo")
            if not cnpj:
                continue
            chave = (cnpj, l.get("categoria") or "")
            atual = self.documentos.get(chave)
            if atual is None or (l.get("data_entrega") or "") > (atual.get("data_entrega") or ""):
                self.documentos[chave] = l


# --- calculos ------------------------------------------------------------------------


def mediana(valores: list[float]) -> float | None:
    return statistics.median(valores) if valores else None


def fechamento_ate(serie: dict[date, tuple], dia: date, pregoes: list[date], folga: int = FOLGA_DA_PONTA) -> tuple[date, float] | None:
    """O fechamento no pregao `dia` ou, na falta de negocio, no mais recente antes dele (ate `folga` pregoes)."""
    anteriores = [p for p in pregoes if p <= dia][-(folga + 1):]
    for pregao in reversed(anteriores):
        registro = serie.get(pregao)
        if registro and registro[0]:
            return pregao, registro[0]
    return None


# Desdobramento e grupamento: salto de um fechamento para o seguinte perto de
# 1:k ou k:1. Medido em 2026-09-25 na serie de 25/08/2025 a 24/09/2026: tres
# eventos, todos a menos de 1,5% da razao simples - CHIP11 1:6 (26/12/2025),
# BITI11 1:3 e SPXI11 1:8 (22/01/2026). Sem ajuste, a rentabilidade de 12
# meses do SPXI11 saia -86% (a Comdinheiro, que ajusta, da +13%).
_FATORES_DE_EVENTO = (2, 3, 4, 5, 6, 8, 10, 20, 25, 50, 100)
_TOLERANCIA_DO_EVENTO = 0.05


def eventos_de_cota(precos: list[tuple[date, float]]) -> list[tuple[date, float, str]]:
    """(data, fator, rotulo) de cada desdobramento ou grupamento na serie de fechamentos."""
    eventos = []
    for (_, p1), (d2, p2) in zip(precos, precos[1:]):
        if not p1:
            continue
        razao = p2 / p1
        if 0.55 < razao < 1.8:
            continue
        for k in _FATORES_DE_EVENTO:
            if razao < 1 and abs(razao * k - 1) <= _TOLERANCIA_DO_EVENTO:
                eventos.append((d2, 1 / k, f"desdobramento 1:{k}"))
                break
            if razao > 1 and abs(razao / k - 1) <= _TOLERANCIA_DO_EVENTO:
                eventos.append((d2, float(k), f"grupamento {k}:1"))
                break
    return eventos


def fator_ate(eventos: list[tuple[date, float, str]], dia: date) -> float:
    """Quanto multiplicar um preco de `dia` para leva-lo a base de cotas de hoje."""
    fator = 1.0
    for data_evento, multiplicador, _ in eventos:
        if dia < data_evento:
            fator *= multiplicador
    return fator


def retornos_diarios(precos: list[tuple[date, float]], pregoes: list[date] | None = None) -> list[tuple[date, float]]:
    """Retorno de cada fechamento sobre o anterior. Com `pregoes`, so entre
    pregoes consecutivos: um ETF que passou dias sem negocio nao gera um
    "retorno diario" que na verdade cobre varios dias."""
    posicao = {p: i for i, p in enumerate(pregoes)} if pregoes else None
    retornos = []
    for (d1, p1), (d2, p2) in zip(precos, precos[1:]):
        if not p1:
            continue
        if posicao is not None and (d1 not in posicao or d2 not in posicao or posicao[d2] - posicao[d1] != 1):
            continue
        retornos.append((d2, p2 / p1 - 1))
    return retornos


def volatilidade_anual(retornos: list[float]) -> float | None:
    if len(retornos) < 2:
        return None
    return statistics.stdev(retornos) * math.sqrt(252)


def queda_maxima(precos: list[float]) -> float | None:
    """Maior queda de um pico ao vale seguinte (fracao negativa)."""
    if len(precos) < 2:
        return None
    pico, pior = precos[0], 0.0
    for preco in precos:
        pico = max(pico, preco)
        if pico:
            pior = min(pior, preco / pico - 1)
    return pior


def beta(retornos_etf: dict[date, float], retornos_indice: dict[date, float]) -> tuple[float | None, int]:
    comuns = sorted(set(retornos_etf) & set(retornos_indice))
    if len(comuns) < MINIMO_DE_RETORNOS:
        return None, len(comuns)
    x = [retornos_indice[d] for d in comuns]
    y = [retornos_etf[d] for d in comuns]
    media_x, media_y = statistics.fmean(x), statistics.fmean(y)
    variancia = sum((a - media_x) ** 2 for a in x)
    if not variancia:
        return None, len(comuns)
    return sum((a - media_x) * (b - media_y) for a, b in zip(x, y)) / variancia, len(comuns)


# Codigo SELIC dos titulos publicos na carteira da CVM (os que aparecem nos ETFs).
_TITULOS = {"210100": "LFT", "760199": "NTN-B", "760197": "NTN-B Principal", "100000": "LTN", "950199": "NTN-F",
            "770100": "NTN-C"}
_TICKER_NO_FIM = re.compile(r"([A-Z]{4}\d{1,2}[A-Z]?)\s*$")
_TAMANHO_DO_NOME = 40


def _chave_do_ativo(linha: dict) -> str:
    """Nome curto e estavel do ativo - o mesmo em todo ETF que o carrega.

    A carteira da CVM guarda o nome em campos diferentes conforme o tipo
    (medido na de 08/2026): acao e ETF estrangeiro no CD_ATIVO (ITUB4, QQQ), mas
    ETF estrangeiro tambem com codigo numerico e o nome no EMISSOR ("ISHARES
    CORE SP500"); titulo publico so pelo codigo SELIC e vencimento; cota de FII
    em DS_ATIVO com o acento perdido na origem ("A??ES - BTLG11").
    """
    codigo = (linha.get("CD_ATIVO") or "").strip().upper()
    descricao = (linha.get("DS_ATIVO") or "").strip().upper()
    emissor = (linha.get("EMISSOR") or "").strip().upper()
    selic = (linha.get("CD_SELIC") or "").strip()
    if codigo and re.search(r"[A-Z]", codigo):
        return codigo
    if selic:
        vencimento = _data_iso(linha.get("DT_VENC"))
        return f"{_TITULOS.get(selic, 'Título ' + selic)} {data_br(vencimento) if vencimento else ''}".strip()
    ticker = _TICKER_NO_FIM.search(descricao)
    if ticker:
        return ticker.group(1)
    if emissor and re.search(r"[A-Z]{3}", emissor):
        return emissor[:_TAMANHO_DO_NOME]
    return (descricao or codigo or normalize_text(linha.get("TP_APLIC")))[:_TAMANHO_DO_NOME]


def posicoes(linhas: list[dict]) -> tuple[dict[str, float], dict[str, float], float | None]:
    """(peso por ativo, peso por pais, PL) da carteira da CVM, somando o ativo
    emprestado ao da carteira e deixando de fora caixa, a pagar e a receber."""
    pl = next((parse_number(l.get("VL_PATRIM_LIQ")) for l in linhas if parse_number(l.get("VL_PATRIM_LIQ"))), None)
    if not pl:
        return {}, {}, None
    pesos: dict[str, float] = {}
    paises: dict[str, float] = {}
    for linha in linhas:
        if normalize_text(linha.get("TP_APLIC")) in _NAO_E_POSICAO:
            continue
        valor = parse_number(linha.get("VL_MERC_POS_FINAL")) or 0.0
        if valor <= 0:
            continue
        pesos[_chave_do_ativo(linha)] = pesos.get(_chave_do_ativo(linha), 0.0) + valor / pl
        pais = (linha.get("PAIS") or "").strip() or "Brasil"
        paises[pais] = paises.get(pais, 0.0) + valor / pl
    return pesos, paises, pl


def sobreposicao(a: dict[str, float], b: dict[str, float]) -> float:
    """Peso em comum entre duas carteiras (soma do menor peso de cada ativo)."""
    return sum(min(peso, b[ativo]) for ativo, peso in a.items() if ativo in b)


# --- colunas ---------------------------------------------------------------------------

_ID, _PRECO, _CUSTO, _RETORNO, _RISCO, _CARTEIRA, _PATRIMONIO, _REGRAS = SECOES


def colunas(j: Janelas) -> list[Coluna]:
    def jan(par: tuple[date, date] | None) -> str:
        return f" ({data_br(par[0])} a {data_br(par[1])})" if par else ""

    doze = jan(j.doze_meses)
    liq = jan(j.liquidez)
    cota = f" (fech. {competencia_br(j.cota_12m[0])} a {competencia_br(j.cota_12m[1])})" if j.cota_12m else ""
    carteira = f" (CVM, {competencia_br(j.carteira)})" if j.carteira else " (CVM)"
    return [
        # 1. Identificacao
        Coluna("ticker", "Ticker", _ID, indicador="Ticker, ISIN, lote", como="Cadastro de instrumentos da B3", largura=10),
        Coluna("situacao", "Situação", _ID, como="Universo: fundos listados e cadastro da B3, catálogo da CVM", largura=24),
        Coluna("isin", "ISIN", _ID, indicador="Ticker, ISIN, lote", como="Cadastro de instrumentos da B3", largura=15),
        Coluna("lote", "Lote", _ID, "inteiro", "Ticker, ISIN, lote", "Cadastro de instrumentos da B3", 7),
        Coluna("cnpj", "CNPJ", _ID, indicador="CNPJ, razão social, gestor, administrador, custodiante, auditor",
               como="B3 (fundos listados) e CVM; é a chave de deduplicação", largura=20),
        Coluna("razao_social", "Razão social", _ID, indicador="CNPJ, razão social, gestor, administrador, custodiante, auditor",
               como="Cadastro da CVM", largura=48),
        Coluna("gestora", "Gestora", _ID, indicador="CNPJ, razão social, gestor, administrador, custodiante, auditor",
               como="Cadastro da CVM (gestor do fundo)", largura=36),
        Coluna("casa", "Casa", _ID, como="Rótulo curto da gestora (ex.: XP (Trend))", largura=20),
        Coluna("administrador", "Administrador", _ID, indicador="CNPJ, razão social, gestor, administrador, custodiante, auditor",
               como="Cadastro da CVM", largura=36),
        Coluna("custodiante", "Custodiante", _ID, indicador="CNPJ, razão social, gestor, administrador, custodiante, auditor",
               como="Cadastro de classes da CVM", largura=30),
        Coluna("auditor", "Auditor", _ID, indicador="CNPJ, razão social, gestor, administrador, custodiante, auditor",
               como="Cadastro de classes da CVM", largura=30),
        Coluna("nome_pregao", "Nome de pregão", _ID, indicador="Nome de pregão", como="B3, fundos listados", largura=20),
        Coluna("tipo_b3", "Tipo B3", _ID, como="B3, fundos listados (ETF, ETF-RF, ETF-CRIPTO, ETF-FII)", largura=11),
        Coluna("categoria_b3", "Categoria B3", _ID, indicador="Categoria B3 (ações, renda fixa, índice estrangeiro)",
               como="Cadastro de instrumentos da B3 (SctyCtgyNm)", largura=18),
        Coluna("segmento", "Segmento (triagem pelo nome)", _ID, como="Leitura do nome do fundo - triagem, não dado conferido", largura=34),
        Coluna("classe_xp", "Classe XP", _ID, indicador="Classe XP (Inflação, RV Brasil, RV Internacional, Câmbio…)",
               como="data/etf/manual/xp_cadastro.csv", largura=18),
        Coluna("indice_referencia", "Índice de referência", _ID, indicador="Índice de referência",
               como="Regulamento (PDF); vale o que uma pessoa conferiu em manual/indices.csv ou manual/regulamento.csv", largura=34),
        Coluna("inicio_negociacao", "Início de negociação (B3)", _ID, "data", "Início de negociação e constituição",
               "Cadastro de instrumentos da B3; a B3 reinicia a data em renomeação", 14),
        Coluna("constituicao", "Constituição (CVM)", _ID, "data", "Início de negociação e constituição",
               "A mais antiga entre fundo e classe no cadastro da CVM", 14),
        # 2. Preco e negociacao
        Coluna("fechamento", "Preço de fechamento (R$)", _PRECO, "preco", "Preço da cota (fechamento)",
               "Negócios consolidados da B3, último pregão com negócio", 13),
        Coluna("data_fechamento", "Data do fechamento", _PRECO, "data", "Preço da cota (fechamento)", largura=12),
        Coluna("cota", "Valor patrimonial da cota (R$)", _PRECO, "preco", "Valor patrimonial da cota",
               "Informe diário do FNET, o mais recente", 13),
        Coluna("data_cota", "Data da cota", _PRECO, "data", "Valor patrimonial da cota", largura=12),
        Coluna("premio", "Prêmio ou desconto (mesmo dia)", _PRECO, "pct", "Prêmio ou desconto",
               "Preço B3 ÷ cota FNET − 1, no dia mais recente em que há os dois", 12),
        Coluna("data_premio", "Data do prêmio", _PRECO, "data", "Prêmio ou desconto", largura=12),
        Coluna("volume_mediano", f"Volume diário, mediana de 63 pregões{liq} (R$)", _PRECO, "dinheiro", "Volume diário",
               "Mediana do volume financeiro, dia sem negócio como zero, sem lote de bloco", 16),
        Coluna("negocios_medianos", f"Negócios por dia, mediana de 63 pregões{liq}", _PRECO, "inteiro", "Negócios por dia",
               "Mediana do número de negócios, dia sem negócio como zero", 14),
        Coluna("presenca", f"Presença em pregões{liq}", _PRECO, "pct", "Presença em pregões",
               "Pregões com negócio ÷ pregões da janela (desde a estreia, se o ETF é mais novo)", 12),
        Coluna("pregoes_janela", "Pregões considerados na janela de 63", _PRECO, "inteiro", "Presença em pregões",
               "63, ou menos para ETF listado depois do começo da janela", 11),
        Coluna("elegivel_presenca", "Presença ≥ 90%", _PRECO, indicador="Presença em pregões", como="Critério de elegibilidade do mapeamento", largura=10),
        Coluna("spread", "Spread garantido (formador)", _PRECO, indicador="Spread médio", como="data/etf/manual/formador.csv", largura=18),
        Coluna("formador", "Formador de mercado", _PRECO, indicador="Formador de mercado contratado",
               como="data/etf/manual/formador.csv", largura=22),
        Coluna("liquidacao", "Liquidação", _PRECO, indicador="Liquidação", como="Cadastro de instrumentos da B3 (DaysToSttlm)", largura=10),
        # 3. Custo
        Coluna("taxa_administracao", "Taxa de administração", _CUSTO, indicador="Taxa de administração", como="Regulamento (PDF)", largura=16),
        Coluna("taxa_maxima", "Taxa máxima", _CUSTO, indicador="Taxa de administração", como="Regulamento (PDF)", largura=16),
        Coluna("taxa_fundo_investido", "Taxa do fundo investido", _CUSTO, indicador="Taxa do fundo investido", como="Regulamento (PDF)", largura=16),
        Coluna("custos_extras", "Custos fora da taxa", _CUSTO, indicador="Custos fora da taxa (consultor, mínimo mensal, banco liquidante)",
               como="Regulamento (PDF)", largura=18),
        Coluna("taxa_faixa_pl", "Taxa por faixa de patrimônio", _CUSTO, indicador="Taxa por faixa de patrimônio", como="Regulamento (PDF)", largura=16),
        Coluna("custo_total", "Custo total", _CUSTO, indicador="Custo total exibido", como="Taxa + fundo investido + extras, do regulamento (PDF)", largura=16),
        Coluna("taxa_performance", "Taxa de performance", _CUSTO, indicador="Taxa de performance", como="Regulamento (PDF)", largura=16),
        Coluna("custos_transacao", "Custos de transação (% do PL)", _CUSTO, indicador="Custos de transação do fundo (% do PL)",
               como="Site do fundo, anual - não coletado", largura=16),
        Coluna("receita_aluguel", "Receita de aluguel repassada", _CUSTO, indicador="Receita de aluguel repassada",
               como="Demonstrações financeiras (PDF)", largura=16),
        # 4. Retorno
        Coluna("rent_12m", f"Rentabilidade 12 meses, preço{doze}", _RETORNO, "pct", "Rentabilidade 12 meses e no ano (preço)",
               "Fechamento B3 no fim ÷ fechamento no começo − 1, sem proventos", 13),
        Coluna("rent_ano", f"Rentabilidade no ano, preço{jan(j.ano)}", _RETORNO, "pct", "Rentabilidade 12 meses e no ano (preço)",
               "Fechamento B3 ÷ último fechamento do ano anterior − 1, sem proventos", 13),
        Coluna("rent_inicio", "Rentabilidade desde o início, preço (ETF novo)", _RETORNO, "pct", "Rentabilidade desde o início",
               "Só para ETF que estreou depois do começo da série em disco: fechamento ÷ primeiro fechamento − 1", 13),
        Coluna("inicio_serie", "Primeiro fechamento (ETF novo)", _RETORNO, "data", "Rentabilidade desde o início", largura=12),
        Coluna("eventos_cota", "Desdobramento ou grupamento na série (ajustado)", _RETORNO, indicador="Rentabilidade por ano e gráfico",
               como="Salto de um fechamento para o seguinte perto de 1:k ou k:1; as rentabilidades, a volatilidade, a "
                    "queda e o beta usam a série ajustada", largura=28),
        Coluna("rent_cota_12m", f"Rentabilidade da cota 12 meses{cota}", _RETORNO, "pct", "Rentabilidade da cota (NAV)",
               "Fechamento mensal do FNET: cota no fim ÷ cota 12 meses antes − 1", 13),
        Coluna("codigo_indice", "Índice B3 usado", _RETORNO, indicador="Retorno do índice no mesmo período",
               como="manual/indices.csv (codigo_b3) ou sugestão pelo nome", largura=10),
        Coluna("retorno_indice_12m", f"Retorno do índice 12 meses{cota}", _RETORNO, "pct", "Retorno do índice no mesmo período",
               "Série do índice B3 nas mesmas datas da cota; índice de outro provedor fica em branco", 13),
        Coluna("tracking_difference", "Diferença para o índice, 12 meses (pp)", _RETORNO, "pp", "Diferença para o índice (tracking difference)",
               "Rentabilidade da cota − retorno do índice, na mesma janela", 12),
        Coluna("erro_aderencia", "Erro de aderência (art. 27)", _RETORNO, indicador="Erro de aderência e gatilhos da norma",
               como="Exige a cota diária de 12 meses (fnet-informe com --desde); não calculado", largura=16),
        Coluna("dy", "Distribuições e DY 12 meses", _RETORNO, indicador="Distribuições e DY", como="Comdinheiro", largura=14),
        # 5. Risco
        Coluna("volatilidade_12m", f"Volatilidade 12 meses{doze}", _RISCO, "pct", "Volatilidade 12 meses",
               "Desvio-padrão dos retornos diários de fechamento × √252; só com negócio em 90% dos pregões", 13),
        Coluna("risco_xp", "Risco em escala (XP)", _RISCO, indicador="Risco em escala (pontos ou 1–7)", como="data/etf/manual/xp_cadastro.csv", largura=12),
        Coluna("drawdown_12m", f"Queda máxima 12 meses{doze}", _RISCO, "pct", "Queda máxima (drawdown)",
               "Maior queda de pico a vale dos fechamentos na janela", 13),
        Coluna("beta_12m", f"Beta contra o Ibovespa{doze}", _RISCO, "decimal", "Beta",
               "Só ETF de ações Brasil; retornos diários do ETF contra o IBOV, mínimo de 200 pares", 11),
        Coluna("duration", "Duration (renda fixa)", _RISCO, indicador="Duration (renda fixa)", como="Gestora / regulamento (PDF)", largura=14),
        Coluna("pmrc", "PMRC (renda fixa)", _RISCO, indicador="PMRC (renda fixa)", como="Regulamento (PDF)", largura=14),
        Coluna("var", "VaR", _RISCO, indicador="VaR", como="Comdinheiro", largura=12),
        # 6. Carteira
        Coluna("n_ativos", f"Número de ativos{carteira}", _CARTEIRA, "inteiro", "Número de ativos e concentração das 10 maiores",
               "Ativos distintos na carteira mensal da CVM, sem caixa, a pagar e a receber", 11),
        Coluna("top10", f"Concentração nas 10 maiores{carteira}", _CARTEIRA, "pct", "Número de ativos e concentração das 10 maiores",
               "Soma do peso (valor de mercado ÷ PL) das 10 maiores posições", 12),
        Coluna("maiores_posicoes", f"Maiores posições{carteira}", _CARTEIRA, indicador="Maiores posições",
               como="As 5 maiores posições, com o peso no PL", largura=50),
        Coluna("paises", f"Países{carteira}", _CARTEIRA, indicador="Setor e país",
               como="Peso por país na carteira da CVM (setor não vem na carteira)", largura=30),
        Coluna("sobreposicao", f"Maior sobreposição com outro ETF{carteira}", _CARTEIRA, indicador="Sobreposição entre ETFs",
               como="O ETF com mais peso em comum (soma do menor peso de cada ativo)", largura=22),
        Coluna("politica_distribuicao", "Política de distribuição", _CARTEIRA, indicador="Política de distribuição (acumula ou distribui)",
               como="Regulamento (PDF)", largura=16),
        # 7. Patrimonio
        Coluna("pl", "Patrimônio líquido (R$)", _PATRIMONIO, "dinheiro", "Patrimônio líquido", "Informe diário do FNET, o mais recente", 16),
        Coluna("cotistas", "Número de cotistas", _PATRIMONIO, "inteiro", "Número de cotistas", "Informe diário do FNET, o mais recente", 12),
        Coluna("cotas_emitidas", "Cotas emitidas", _PATRIMONIO, "inteiro", "Cotas emitidas, captação e resgate", "PL ÷ cota do informe mais recente", 14),
        Coluna("captacao_liquida", f"Captação líquida{jan(j.captacao)} (R$)", _PATRIMONIO, "dinheiro", "Cotas emitidas, captação e resgate",
               "Soma de captação − resgate dos informes diários do FNET na janela", 16),
        Coluna("idade_meses", "Idade (meses)", _PATRIMONIO, "inteiro", "Idade e \"fundo novo\"",
               "Desde a data mais antiga entre constituição (CVM) e início de negociação (B3)", 9),
        Coluna("fundo_novo", "Fundo novo (< 12 meses)", _PATRIMONIO, indicador="Idade e \"fundo novo\"", largura=10),
        # 8. Regras
        Coluna("tributacao", "Tributação (IR sobre o ganho)", _REGRAS, indicador="Tributação",
               como="Regra por categoria B3: 15% ações e índice estrangeiro; renda fixa 25/20/15% pelo PMRC (regulamento)", largura=18),
        Coluna("publico_alvo", "Público-alvo", _REGRAS, indicador="Público-alvo e qualificado", como="Regulamento (PDF)", largura=16),
        Coluna("aceita_mensal", "Aceita investimento mensal", _REGRAS, indicador="Aceita investimento mensal",
               como="data/etf/manual/xp_cadastro.csv", largura=12),
        Coluna("link_regulamento", "Regulamento (link)", _REGRAS, "link", "Documentos (regulamento, informe, composição)",
               "Fundos.NET, o mais recente", 14),
        Coluna("link_demonstracoes", "Demonstrações financeiras (link)", _REGRAS, "link", "Documentos (regulamento, informe, composição)",
               "Fundos.NET, a mais recente", 14),
    ]


# --- montagem ------------------------------------------------------------------------------


def _situacao(etf: dict) -> str:
    if etf.get("listado") == "sim" and etf.get("negocia") == "sim":
        return "negocia na B3"
    if etf.get("listado") == "sim":
        return "listado, sem negociação"
    if etf.get("chave", "").startswith("ticker:"):
        return "cancelado (só no cadastro da B3)"
    return "só na CVM (pré-operacional)"


_ORDEM_DA_SITUACAO = {"negocia na B3": 0, "listado, sem negociação": 1, "só na CVM (pré-operacional)": 2,
                      "cancelado (só no cadastro da B3)": 3}


def _pdf(ins: Insumos, chave: str, campo: str) -> str:
    """Valor conferido por uma pessoa no regulamento, ou a marca de pendente."""
    return ins.conferidos.get((chave, campo)) or PENDENTE_PDF


def _manual(linha: dict | None, coluna: str) -> str:
    valor = (linha or {}).get(coluna) or ""
    return valor.strip() or PENDENTE_CADASTRO


def _janelas(ins: Insumos) -> Janelas:
    j = Janelas()
    pregoes = ins.pregoes
    if pregoes:
        fim = pregoes[-1]
        j.ultimo_pregao = fim
        janela = pregoes[-PREGOES_LIQUIDEZ:]
        j.liquidez = (janela[0], fim)
        inicio_12m = meses_antes(fim, 12)
        anteriores = [p for p in pregoes if p <= inicio_12m]
        if anteriores and (inicio_12m - anteriores[-1]).days <= 7:
            j.doze_meses = (anteriores[-1], fim)
        fim_do_ano = [p for p in pregoes if p.year < fim.year]
        if fim_do_ano and fim_do_ano[-1].month == 12:
            j.ano = (fim_do_ano[-1], fim)
    if len(ins.meses) >= 13:
        j.cota_12m = (ins.meses[-13], ins.meses[-1])
    j.carteira = ins.competencia_carteira
    j.captacao = (ins.hoje - timedelta(days=DIAS_DE_CAPTACAO), ins.hoje)
    return j


def _estreia(ins: Insumos, etf: dict, fechamentos: list[tuple[date, float]]) -> date | None:
    """Inicio de negociacao de ETF que estreou depois do comeco da serie em disco.

    O ETF entra no cadastro da B3 antes de negociar (o AGGX11 estava la desde
    antes do inicio, 01/09/2026), entao a primeira aparicao no cadastro nao
    serve. Vale o inicio de negociacao da B3 - menos quando ele foi reiniciado:
    ETF com negocio antes da data (o BOVA11 negocia desde 2008 e estava com
    16/07/2026) ou com constituicao mais de um ano antes dela.
    """
    inicio = _data_iso(etf.get("inicio_negociacao"))
    if not inicio or inicio.year >= 9999 or not ins.pregoes or inicio <= ins.pregoes[0]:
        return None
    if fechamentos and fechamentos[0][0] < inicio - timedelta(days=3):
        return None
    constituicao = ins.cvm.get(etf.get("cnpj") or "", {}).get("constituicao")
    if constituicao and constituicao < inicio - timedelta(days=365):
        return None
    return inicio


def _linha(ins: Insumos, etf: dict, j: Janelas, carteiras: dict[str, dict[str, float]]) -> dict:
    chave = etf.get("chave", "")
    cnpj = etf.get("cnpj") or ""
    ticker = (etf.get("ticker") or "").upper()
    categoria = etf.get("categoria_b3") or ""
    renda_fixa = categoria == CATEGORIA_RF
    grupo, segmento = segmento_do_etf(etf.get("nome"))
    cvm = ins.cvm.get(cnpj, {})
    linha: dict = {
        "ticker": ticker, "situacao": _situacao(etf), "isin": etf.get("isin") or "",
        "lote": parse_number(etf.get("lote")), "cnpj": format_cnpj(cnpj) if cnpj else "",
        "razao_social": etf.get("nome") or "", "gestora": cvm.get("gestor") or etf.get("gestora") or "",
        "casa": rotulo_da_gestora(cvm.get("gestor") or etf.get("gestora"), etf.get("nome")),
        "administrador": cvm.get("administrador") or etf.get("administrador") or "",
        "custodiante": cvm.get("custodiante", ""), "auditor": cvm.get("auditor", ""),
        "nome_pregao": etf.get("nome_pregao") or "", "tipo_b3": etf.get("tipo_b3") or "",
        "categoria_b3": _CATEGORIAS.get(categoria, categoria), "segmento": f"{grupo} - {segmento}",
        "classe_xp": _manual(ins.xp.get(chave), "classe_xp"),
        "risco_xp": _manual(ins.xp.get(chave), "risco_pontos"),
        "aceita_mensal": _manual(ins.xp.get(chave), "aceita_investimento_mensal"),
        "formador": _manual(ins.formador.get(chave), "formador"),
        "liquidacao": f"D+{etf['liquidacao_dias']}" if etf.get("liquidacao_dias") else "",
        "constituicao": cvm.get("constituicao"),
    }
    inicio_b3 = _data_iso(etf.get("inicio_negociacao"))
    linha["inicio_negociacao"] = inicio_b3 if inicio_b3 and inicio_b3.year < 9999 else None
    spread = (ins.formador.get(chave) or {}).get("spread_maximo") or ""
    cotas_spread = (ins.formador.get(chave) or {}).get("ate_cotas") or ""
    linha["spread"] = f"{spread} até {cotas_spread} cotas" if spread and cotas_spread else (spread or PENDENTE_CADASTRO)
    indice_manual = ((ins.indices_manual.get(chave) or {}).get("indice_referencia") or "").strip()
    linha["indice_referencia"] = indice_manual or _pdf(ins, chave, "indice_referencia")

    # 3. Custo e demais campos do regulamento.
    for campo in ("taxa_administracao", "taxa_maxima", "taxa_fundo_investido", "custos_extras", "taxa_performance",
                  "politica_distribuicao", "publico_alvo"):
        linha[campo] = _pdf(ins, chave, campo)
    linha["taxa_faixa_pl"] = PENDENTE_PDF
    linha["custo_total"] = PENDENTE_PDF
    linha["receita_aluguel"] = PENDENTE_PDF
    linha["custos_transacao"] = "não coletado (site do fundo)"
    linha["pmrc"] = _pdf(ins, chave, "pmrc") if renda_fixa else NAO_SE_APLICA
    linha["duration"] = PENDENTE_PDF if renda_fixa else NAO_SE_APLICA
    if renda_fixa:
        linha["tributacao"] = PENDENTE_PDF
    elif categoria:
        linha["tributacao"] = "15%"
    else:
        linha["tributacao"] = ""
    linha["dy"] = SEM_COMDINHEIRO
    linha["var"] = SEM_COMDINHEIRO
    linha["erro_aderencia"] = "não calculado"

    # 2. Preco, liquidez e 4/5. retorno e risco.
    bruta = ins.por_ticker.get(ticker, {})
    fechamentos_brutos = sorted((d, r[0]) for d, r in bruta.items() if r[0])
    if fechamentos_brutos:
        linha["data_fechamento"], linha["fechamento"] = fechamentos_brutos[-1]
    # Rentabilidade, volatilidade, queda e beta sobre a serie ajustada por
    # desdobramento e grupamento; o preco exibido e o de verdade.
    eventos = eventos_de_cota(fechamentos_brutos)
    linha["eventos_cota"] = "; ".join(f"{data_br(d)}: {rotulo}" for d, _, rotulo in eventos)
    serie = {d: ((r[0] * fator_ate(eventos, d)) if r[0] else r[0], r[1], r[2]) for d, r in bruta.items()}
    fechamentos = sorted((d, r[0]) for d, r in serie.items() if r[0])
    # Informe com PL e cota zerados e de ETF pre-operacional: nao conta como o mais recente.
    informes = {d: i for d, i in ins.informes.get(cnpj, {}).items() if i.get("cota") or i.get("pl")}
    if informes:
        ultimo = max(informes)
        info = informes[ultimo]
        linha.update({"data_cota": ultimo, "cota": info.get("cota"), "pl": info.get("pl"),
                      "cotistas": info.get("cotistas")})
        if info.get("pl") and info.get("cota"):
            linha["cotas_emitidas"] = round(info["pl"] / info["cota"])
        precos = dict(fechamentos_brutos)
        comuns = sorted(d for d, i in informes.items() if d in precos and i.get("cota"))
        if comuns:
            dia = comuns[-1]
            linha["premio"] = precos[dia] / informes[dia]["cota"] - 1
            linha["data_premio"] = dia
        if j.captacao:
            na_janela = [i for d, i in informes.items() if j.captacao[0] < d <= j.captacao[1]]
            if na_janela:
                linha["captacao_liquida"] = sum((i.get("captacao") or 0) - (i.get("resgates") or 0) for i in na_janela)

    estreia = _estreia(ins, etf, fechamentos)
    if j.liquidez and etf.get("negocia") == "sim":
        janela = ins.pregoes[-PREGOES_LIQUIDEZ:]
        if estreia:
            janela = [p for p in janela if p >= estreia]
        if janela:
            registros = [serie.get(p, (None, 0.0, 0.0)) for p in janela]
            linha["volume_mediano"] = mediana([r[2] for r in registros])
            linha["negocios_medianos"] = mediana([r[1] for r in registros])
            presenca = sum(1 for r in registros if r[1] > 0) / len(janela)
            linha["presenca"] = presenca
            linha["pregoes_janela"] = len(janela)
            linha["elegivel_presenca"] = "sim" if presenca >= PRESENCA_MINIMA else "não"

    if fechamentos and j.doze_meses:
        base = fechamento_ate(serie, j.doze_meses[0], ins.pregoes)
        if base:
            linha["rent_12m"] = fechamentos[-1][1] / base[1] - 1
        janela_12m = [(d, p) for d, p in fechamentos if d >= j.doze_meses[0]]
        pregoes_12m = [p for p in ins.pregoes if p >= j.doze_meses[0]]
        liquido = base is not None and len(janela_12m) >= PRESENCA_MINIMA * len(pregoes_12m)
        retornos = retornos_diarios(janela_12m, ins.calendario) if liquido else []
        if len(retornos) >= MINIMO_DE_RETORNOS:
            linha["volatilidade_12m"] = volatilidade_anual([r for _, r in retornos])
        if base:
            linha["drawdown_12m"] = queda_maxima([p for _, p in janela_12m])
        if retornos and grupo == "Ações Brasil" and categoria == "ETF EQUITIES" and "IBOV" in ins.indices:
            niveis = sorted((d, v) for d, v in ins.indices["IBOV"].items() if d >= j.doze_meses[0])
            b, _ = beta(dict(retornos), dict(retornos_diarios(niveis)))
            linha["beta_12m"] = b
    if fechamentos and j.ano:
        base = fechamento_ate(serie, j.ano[0], ins.pregoes)
        if base:
            linha["rent_ano"] = fechamentos[-1][1] / base[1] - 1
    if fechamentos and estreia:
        primeiro = next(((d, p) for d, p in fechamentos if d >= estreia), None)
        if primeiro:
            linha["rent_inicio"] = fechamentos[-1][1] / primeiro[1] - 1
            linha["inicio_serie"] = primeiro[0]
            if linha.get("drawdown_12m") is None:
                linha["drawdown_12m"] = queda_maxima([p for d, p in fechamentos if d >= estreia])

    # Cota e indice nas mesmas datas (fechamentos mensais do FNET).
    codigo = ins.codigos_b3.get(chave, "")
    linha["codigo_indice"] = codigo
    if j.cota_12m and cnpj in ins.mensal:
        inicio, fim = ins.mensal[cnpj].get(j.cota_12m[0]), ins.mensal[cnpj].get(j.cota_12m[1])
        if inicio and fim and inicio[1] and fim[1]:
            base = inicio[1] * (fator_ate(eventos, inicio[0]) / fator_ate(eventos, fim[0]) if inicio[0] and fim[0] else 1.0)
            linha["rent_cota_12m"] = fim[1] / base - 1
            niveis = ins.indices.get(codigo, {})
            if niveis and inicio[0] and fim[0]:
                antes = [d for d in niveis if d <= inicio[0]]
                depois = [d for d in niveis if d <= fim[0]]
                if antes and depois and (inicio[0] - max(antes)).days <= 7 and (fim[0] - max(depois)).days <= 7:
                    linha["retorno_indice_12m"] = niveis[max(depois)] / niveis[max(antes)] - 1
                    linha["tracking_difference"] = (linha["rent_cota_12m"] - linha["retorno_indice_12m"]) * 100

    # 6. Carteira da CVM.
    pesos = carteiras.get(cnpj)
    if pesos:
        ordenados = sorted(pesos.items(), key=lambda item: -item[1])
        linha["n_ativos"] = len(ordenados)
        linha["top10"] = sum(p for _, p in ordenados[:10])
        linha["maiores_posicoes"] = "; ".join(f"{a} {p * 100:.1f}%".replace(".", ",") for a, p in ordenados[:TOP_POSICOES])
        _, paises, _ = posicoes(ins.carteira.get(cnpj, []))
        linha["paises"] = "; ".join(f"{pais} {peso * 100:.0f}%" for pais, peso in sorted(paises.items(), key=lambda i: -i[1])[:3])

    # 7. Idade.
    datas = [d for d in (linha.get("constituicao"), linha.get("inicio_negociacao")) if d]
    if datas:
        inicio = min(datas)
        meses = (ins.hoje.year - inicio.year) * 12 + ins.hoje.month - inicio.month - (ins.hoje.day < inicio.day)
        linha["idade_meses"] = max(meses, 0)
        linha["fundo_novo"] = "sim" if meses < 12 else "não"

    # 8. Documentos.
    for coluna, categoria_doc in (("link_regulamento", "Regulamento"), ("link_demonstracoes", "Demonstrações Financeiras")):
        doc = ins.documentos.get((cnpj, categoria_doc)) if cnpj else None
        linha[coluna] = doc.get("link") if doc else ""
    return linha


def consolidar(config: Config, hoje: date | None = None) -> Consolidado:
    """Uma linha por ETF do universo, na ordem: negocia, listado, pre-operacional, cancelado."""
    hoje = hoje or date.today()
    ins = Insumos(config, hoje)
    j = _janelas(ins)
    carteiras = {cnpj: posicoes(linhas)[0] for cnpj, linhas in ins.carteira.items()}
    carteiras = {cnpj: pesos for cnpj, pesos in carteiras.items() if pesos}
    linhas = [_linha(ins, etf, j, carteiras) for etf in ins.universo]
    # Sobreposicao: o ETF com mais peso em comum, entre os que tem carteira.
    por_cnpj = {normalize_cnpj(l["cnpj"]): l for l in linhas if l.get("cnpj")}
    for cnpj, pesos in carteiras.items():
        linha = por_cnpj.get(cnpj)
        if linha is None:
            continue
        melhor, melhor_peso = None, 0.0
        for outro, pesos_outro in carteiras.items():
            if outro == cnpj or outro not in por_cnpj:
                continue
            peso = sobreposicao(pesos, pesos_outro)
            if peso > melhor_peso:
                melhor, melhor_peso = outro, peso
        if melhor and melhor_peso >= 0.01:
            outro = por_cnpj[melhor]
            linha["sobreposicao"] = f"{outro.get('ticker') or outro.get('razao_social')[:20]} {melhor_peso * 100:.0f}%"
    linhas.sort(key=lambda l: (_ORDEM_DA_SITUACAO.get(l["situacao"], 9), l["ticker"] == "", l["ticker"], l["razao_social"]))
    return Consolidado(colunas(j), linhas, j)
