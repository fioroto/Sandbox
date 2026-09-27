"""B3, portal de arquivos: cadastro de instrumentos e negocios consolidados.

Duas chamadas por arquivo (medido em 2026-09-24):

    GET https://arquivos.b3.com.br/api/download/requestname?fileName=<Nome>&date=AAAA-MM-DD
        -> {"redirectUrl": "~/download?token=...", "token": "...", "file": {"name": ..., "extension": ".csv"}}
    GET https://arquivos.b3.com.br/api/download/?token=<token>
        -> CSV `;`, primeira linha "Status do Arquivo: Final", depois o cabecalho

- InstrumentsConsolidatedFile: ~20 MB, 89 mil linhas (3 s). ETF de acoes e de
  indice estrangeiro estao nas categorias "ETF EQUITIES" e "ETF FOREIGN INDEX";
  ETF de renda fixa NAO: e "FIXED INCOME TRADABLE INSTRUMENT T1", segmento
  FORWARD, liquidacao D+1 (68 tickers, IMAB11, B5P211, LFTS11...). O lote de
  bloco e um ticker a parte (ACWI11M, ACWI11Q; segmento "Equity Block Trading
  Lot") e o mercado primario de ETF de renda fixa tambem (03BKETF11H).
- TradeInformationConsolidatedFile: ~5 MB, 82 mil linhas (0,3 s). Os ETFs de
  renda fixa estao aqui (segmento FORWARD) - no COTAHIST nao estao.
- Dia sem pregao (fim de semana, feriado) e dia futuro: HTTP 400 no
  requestname. Historico: ha token para 2024-01-10.

Guarda-se so as linhas de ETF, com todas as colunas como publicadas e tres
colunas `x_` do extrator: o tipo da linha (padrao, bloco, fracionario,
mercado_primario), o ticker-base e o criterio que a manteve. Nada e somado nem
excluido: a mediana de 63 pregoes "sem lotes de bloco" e a etapa seguinte.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market import cotahist
from shelf_monitor.market.datasets import Dataset
from shelf_monitor.market.etf_bruto import armazem, rede
from shelf_monitor.market.etf_bruto.contexto import EM_DISCO, OK, PARCIAL, Contexto, ResumoFonte
from shelf_monitor.market.etf_bruto.universo import arquivos_datados, pasta_instrumentos
from shelf_monitor.market.fetch import fetch
from shelf_monitor.texto import contagem, data_br, periodo_br

FONTE = "b3-arquivos"
FONTE_COTAHIST = "cotahist"

URL_TOKEN = "https://arquivos.b3.com.br/api/download/requestname"
URL_DOWNLOAD = "https://arquivos.b3.com.br/api/download/"
INSTRUMENTOS = "InstrumentsConsolidatedFile"
NEGOCIOS = "TradeInformationConsolidatedFile"

# 13 meses: cobre os 63 pregoes da liquidez e os 12 meses de rentabilidade,
# inclusive dos ETFs de renda fixa, que so existem neste arquivo.
JANELA_PADRAO_MESES = 13
# Quantos dias uteis seguidos com 400 marcam o comeco do historico da B3 (a
# maior sequencia de feriados em dia util e 2 - Carnaval).
MAX_400_SEGUIDOS = 5
# Quantos dias uteis para tras procurar o cadastro de instrumentos mais recente.
RECUO_DO_CADASTRO = 5

CATEGORIA_RF = "FIXED INCOME TRADABLE INSTRUMENT T1"
COLUNAS_EXTRAS = ("x_tipo_linha", "x_ticker_base", "x_criterio")
_VARIANTE = re.compile(r"^(?P<base>[A-Z0-9]{4}\d{2})(?P<sufixo>[MQF])$")


def pasta_negocios(config: Config) -> Path:
    return config.etf_bruto_dir / "b3_arquivos" / "negocios"


def _caminho_sem_pregao(config: Config) -> Path:
    return config.etf_bruto_dir / "b3_arquivos" / "_sem_pregao.json"


def caminho_incompletos(config: Config) -> Path:
    """Pregoes cujo arquivo a B3 publicou sem o mercado a vista (dia -> motivo)."""
    return config.etf_bruto_dir / "b3_arquivos" / "_incompletos.json"


# Segmentos em que ETF negocia: acoes e indice estrangeiro no CASH, renda fixa
# no FORWARD. Medido em 2026-09-25: em 6 dos 266 pregoes de 25/08/2025 a
# 24/09/2026 (12/12/2025, 23/12/2025, 11/02/2026, 09/04/2026, 14/04/2026 e
# 14/05/2026) o arquivo consolidado da B3 so tinha derivativos - nenhuma linha
# de CASH nem de FORWARD. Gravar esse dia seria dizer que nenhum ETF negociou.
SEGMENTOS_A_VISTA = frozenset({"CASH", "FORWARD"})


def _norm(texto: str | None) -> str:
    """Maiusculas e espacos colapsados - a B3 poe \\xa0 no meio de uma categoria."""
    return " ".join((texto or "").replace("\xa0", " ").split()).upper()


def meses_antes(dia: date, meses: int) -> date:
    indice = dia.year * 12 + dia.month - 1 - meses
    ano, mes = divmod(indice, 12)
    mes += 1
    return date(ano, mes, min(dia.day, calendar.monthrange(ano, mes)[1]))


def dias_uteis(de: date, ate: date) -> list[date]:
    """Dias de semana de `de` a `ate`, do mais recente ao mais antigo (feriado se descobre pelo 400)."""
    dias = []
    dia = ate
    while dia >= de:
        if dia.weekday() < 5:
            dias.append(dia)
        dia -= timedelta(days=1)
    return dias


# --- download --------------------------------------------------------------------


def pedir_token(sessao, config: Config, nome: str, dia: date) -> str | None:
    """Token do arquivo do dia, ou None quando a B3 diz que ele nao existe (400)."""
    resposta = rede.obter(
        sessao, URL_TOKEN, config, params={"fileName": nome, "date": dia.isoformat()},
        accept="application/json", ausente=(400, 404),
    )
    if resposta is None:
        return None
    try:
        dados = resposta.json()
    except ValueError as exc:
        raise SourceUnavailable(f"resposta não JSON em {URL_TOKEN} ({nome}, {dia})") from exc
    token = dados.get("token") if isinstance(dados, dict) else None
    if not token:
        raise SourceUnavailable(f"sem token na resposta de {URL_TOKEN} ({nome}, {dia})")
    return str(token)


def baixar(sessao, config: Config, nome: str, dia: date) -> bytes | None:
    """O arquivo do dia, ou None em dia sem pregao."""
    token = pedir_token(sessao, config, nome, dia)
    if token is None:
        return None
    resposta = rede.obter(sessao, URL_DOWNLOAD, config, params={"token": token})
    return resposta.content


def _texto(conteudo: bytes) -> str:
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return conteudo.decode("latin-1")


def ler_csv_b3(conteudo: bytes) -> tuple[str | None, list[str], Iterator[list[str]]]:
    """(status do arquivo, cabecalho, linhas como listas) sem materializar o arquivo."""
    handle = io.StringIO(_texto(conteudo))
    primeira = handle.readline()
    status = None
    if primeira.lower().startswith("status do arquivo"):
        status = primeira.split(":", 1)[1].strip() or None
        primeira = handle.readline()
    cabecalho = next(csv.reader([primeira], delimiter=";"), [])
    return status, [c.strip() for c in cabecalho], csv.reader(handle, delimiter=";")


# --- filtros ---------------------------------------------------------------------


def tipo_da_linha(segmento: str | None, categoria: str | None = None) -> str:
    seg, cat = _norm(segmento), _norm(categoria)
    if cat.startswith("ETF PRIMARY MARKET") or seg.startswith("ETF PRIMARY MARKET"):
        return "mercado_primario"
    if "BLOCK TRADING" in seg:
        return "bloco"
    if seg == "ODD LOT":
        return "fracionario"
    return "padrao"


def ticker_base(ticker: str, acronimo: str | None = None, por_acronimo: dict[str, str] | None = None) -> str:
    """BOVA11M -> BOVA11; 03BKETF11H (acronimo 03BK) -> 03BK11."""
    ticker = (ticker or "").strip().upper()
    if acronimo and por_acronimo and acronimo.strip().upper() in por_acronimo:
        return por_acronimo[acronimo.strip().upper()]
    variante = _VARIANTE.match(ticker)
    return variante.group("base") if variante else ticker


def _isin_de_cota(isin: str | None) -> bool:
    """ISIN de cota de fundo: BR + emissor + "CTF" (BRIMABCTF004)."""
    isin = (isin or "").strip().upper()
    return len(isin) == 12 and isin[6:9] == "CTF"


def filtrar_instrumentos(
    cabecalho: list[str], linhas: Iterable[list[str]], tickers_universo: set[str]
) -> list[dict]:
    """Linhas de ETF do cadastro, com as colunas `x_`.

    Criterios, nesta ordem: categoria "ETF ..."; renda fixa negociavel (T1)
    com ISIN de cota de fundo ou ticker do universo; ou o ticker (ou o
    ticker-base, para bloco e fracionario) no universo.
    """
    posicao = {nome: i for i, nome in enumerate(cabecalho)}

    def campo(linha: list[str], nome: str) -> str:
        i = posicao.get(nome)
        return linha[i] if i is not None and i < len(linha) else ""

    mantidas: list[tuple[dict, str]] = []
    for linha in linhas:
        if not linha:
            continue
        ticker = campo(linha, "TckrSymb").strip().upper()
        categoria = _norm(campo(linha, "SctyCtgyNm"))
        if categoria.startswith("ETF "):
            criterio = "categoria"
        elif categoria == CATEGORIA_RF and (_isin_de_cota(campo(linha, "ISIN")) or ticker in tickers_universo):
            criterio = "renda_fixa"
        elif ticker in tickers_universo or ticker_base(ticker) in tickers_universo:
            criterio = "universo"
        else:
            continue
        registro = {nome: (linha[i] if i < len(linha) else "") for i, nome in enumerate(cabecalho)}
        mantidas.append((registro, criterio))

    por_acronimo = {
        (r.get("Asst") or "").strip().upper(): (r.get("TckrSymb") or "").strip().upper()
        for r, _ in mantidas
        if tipo_da_linha(r.get("SgmtNm"), r.get("SctyCtgyNm")) == "padrao" and r.get("Asst")
    }
    resultado = []
    for registro, criterio in mantidas:
        registro["x_tipo_linha"] = tipo_da_linha(registro.get("SgmtNm"), registro.get("SctyCtgyNm"))
        registro["x_ticker_base"] = ticker_base(registro.get("TckrSymb", ""), registro.get("Asst"), por_acronimo)
        registro["x_criterio"] = criterio
        resultado.append(registro)
    return resultado


def filtrar_negocios(cabecalho: list[str], linhas: Iterable[list[str]], tickers_etf: set[str]) -> list[dict]:
    """Linhas de negocio dos tickers de ETF (o arquivo nao tem categoria: o filtro e o ticker)."""
    posicao = {nome: i for i, nome in enumerate(cabecalho)}
    i_ticker = posicao.get("TckrSymb")
    if i_ticker is None:
        return []
    resultado = []
    for linha in linhas:
        if len(linha) <= i_ticker:
            continue
        ticker = linha[i_ticker].strip().upper()
        if ticker not in tickers_etf:
            continue
        registro = {nome: (linha[i] if i < len(linha) else "") for i, nome in enumerate(cabecalho)}
        registro["x_tipo_linha"] = tipo_da_linha(registro.get("SgmtNm"))
        registro["x_ticker_base"] = ticker_base(ticker)
        registro["x_criterio"] = "ticker"
        resultado.append(registro)
    return resultado


def tickers_etf(config: Config, universo: list[dict]) -> set[str]:
    """Todo ticker de ETF conhecido: os de cada cadastro em disco (o que pega
    ticker renomeado ou deslistado dentro da janela), os do universo e as
    variantes de bloco (M, Q) e fracionario (F) de cada um."""
    tickers: set[str] = set()
    pasta = pasta_instrumentos(config)
    # O cadastro preliminar tambem vale aqui: e so para reconhecer ticker.
    for caminho in sorted(pasta.glob("*.csv")) if pasta.exists() else []:
        for linha in armazem.ler_csv(caminho):
            for coluna in ("TckrSymb", "x_ticker_base"):
                valor = (linha.get(coluna) or "").strip().upper()
                if valor:
                    tickers.add(valor)
    tickers.update((linha.get("ticker") or "").strip().upper() for linha in universo)
    tickers.discard("")
    variantes = {f"{t}{s}" for t in tickers if not _VARIANTE.match(t) for s in "MQF"}
    return tickers | variantes


# --- captura ---------------------------------------------------------------------


def _ler_sem_pregao(config: Config) -> set[str]:
    return set(armazem.ler_json(_caminho_sem_pregao(config), []) or [])


def _gravar_sem_pregao(config: Config, dias: set[str]) -> None:
    armazem.gravar_json(_caminho_sem_pregao(config), sorted(dias))


def _validar(nome: str, dia: date, status: str | None, cabecalho: list[str], linhas: list[dict]) -> str | None:
    """Motivo de recusa do arquivo, ou None.

    Zero linhas de ETF num arquivo de pregao e sinal de formato novo (coluna
    renomeada, categoria trocada), nao de mercado: todo pregao tem ETF no
    cadastro e ETF negociado.
    """
    if "TckrSymb" not in cabecalho or "RptDt" not in cabecalho:
        return f"cabeçalho inesperado em {nome} ({cabecalho[:5]})"
    if not linhas:
        return f"nenhuma linha de ETF em {nome} de {data_br(dia)} (o formato mudou?)"
    datas = {(l.get("RptDt") or "")[:10] for l in linhas[:50]}
    if datas != {dia.isoformat()}:
        return f"{nome} de {data_br(dia)} veio com RptDt {sorted(datas)}"
    return None


def _gravar_dia(ctx: Contexto, pasta: Path, nome: str, dia: date, status: str | None,
                cabecalho: list[str], linhas: list[dict]) -> tuple[Path, bool]:
    """Grava o CSV do dia; arquivo com status diferente de Final vai para
    `<dia>.preliminar.csv` e nao conta como capturado (sera baixado de novo)."""
    final = (status or "").strip().lower() == "final"
    caminho = pasta / (f"{dia.isoformat()}.csv" if final else f"{dia.isoformat()}.preliminar.csv")
    sha, total = armazem.gravar_csv(caminho, list(cabecalho) + list(COLUNAS_EXTRAS), linhas)
    ctx.manifesto.arquivo(FONTE, caminho, url=URL_TOKEN, referencia=f"{nome} {dia.isoformat()}", sha256=sha,
                          linhas=total, status_origem=status)
    return caminho, final


def _capturar_instrumentos(ctx: Contexto, sessao, dia: date, tickers_universo: set[str]) -> str:
    """Um cadastro de instrumentos: 'gravado', 'em disco', 'sem pregao' ou 'pendente'."""
    pasta = pasta_instrumentos(ctx.config)
    if (pasta / f"{dia.isoformat()}.csv").exists() and not ctx.pedido.forcar:
        return "em disco"
    conteudo = baixar(sessao, ctx.config, INSTRUMENTOS, dia)
    if conteudo is None:
        return "pendente" if dia >= ctx.hoje else "sem pregao"
    status, cabecalho, linhas = ler_csv_b3(conteudo)
    etfs = filtrar_instrumentos(cabecalho, linhas, tickers_universo)
    motivo = _validar(INSTRUMENTOS, dia, status, cabecalho, etfs)
    if motivo:
        _guardar_evidencia(ctx, f"b3-{INSTRUMENTOS}-{dia.isoformat()}.csv", conteudo)
        raise SourceUnavailable(motivo)
    _, final = _gravar_dia(ctx, pasta, INSTRUMENTOS, dia, status, cabecalho, etfs)
    # De manha a B3 publica o cadastro do dia como "Parcial" (medido em
    # 2026-09-25 as 06:56): vai para .preliminar.csv e o final vem na proxima captura.
    return "gravado" if final else "preliminar"


def _guardar_evidencia(ctx: Contexto, nome: str, conteudo: bytes) -> None:
    armazem.gravar_atomico(ctx.config.etf_sources_debug_dir / nome, conteudo[: 4 * 1024 * 1024])


def capturar(ctx: Contexto) -> ResumoFonte:
    """Cadastro de instrumentos do ultimo pregao (e do primeiro de cada mes da
    janela) e negocios consolidados de todo pregao da janela que falta em disco."""
    config = ctx.config
    sessao = ctx.sessao(FONTE, intervalo=0.2, timeout_minimo=120)
    fim = min(ctx.pedido.dia, ctx.hoje)
    inicio = ctx.pedido.desde or meses_antes(fim, JANELA_PADRAO_MESES)
    sem_pregao = _ler_sem_pregao(config)
    incompletos: dict[str, str] = armazem.ler_json(caminho_incompletos(config), {}) or {}
    if ctx.pedido.forcar:
        sem_pregao = {d for d in sem_pregao if not inicio.isoformat() <= d <= fim.isoformat()}
        incompletos = {d: m for d, m in incompletos.items() if not inicio.isoformat() <= d <= fim.isoformat()}

    tickers_universo = {(l.get("ticker") or "").strip().upper() for l in ctx.universo} - {""}
    ocorrencias = 0
    cadastros_gravados = 0

    # 1. cadastro de instrumentos: o do ultimo pregao ate `fim`.
    cadastro = None
    for dia in dias_uteis(fim - timedelta(days=RECUO_DO_CADASTRO * 2), fim)[:RECUO_DO_CADASTRO + 1]:
        if dia.isoformat() in sem_pregao:
            continue
        try:
            estado = _capturar_instrumentos(ctx, sessao, dia, tickers_universo)
        except SourceUnavailable as exc:
            ocorrencias += 1
            ctx.manifesto.ocorrencia(FONTE, f"{INSTRUMENTOS} {dia}", str(exc)[:200])
            break
        if estado == "sem pregao":
            sem_pregao.add(dia.isoformat())
            continue
        if estado == "pendente":
            continue
        cadastro = (dia, estado)
        cadastros_gravados += estado in ("gravado", "preliminar")
        break

    # 2. um cadastro por mes da janela (o primeiro pregao do mes), para o
    # filtro de negocios reconhecer ticker que mudou ou saiu de bolsa no meio.
    # Do mes mais recente para tras: cinco 400 seguidos sao o comeco do
    # historico, e ai os meses mais antigos nem sao tentados.
    ja_tem = {d.strftime("%Y-%m") for d, _ in arquivos_datados(pasta_instrumentos(config), ".csv")}
    por_mes: dict[str, list[date]] = {}
    for dia in reversed(dias_uteis(inicio, fim)):
        por_mes.setdefault(dia.strftime("%Y-%m"), []).append(dia)
    for mes in sorted(por_mes, reverse=True):
        if mes in ja_tem:
            continue
        falhas_do_mes: list[str] = []
        for dia in por_mes[mes][:MAX_400_SEGUIDOS]:
            if dia.isoformat() in sem_pregao:
                continue
            try:
                estado = _capturar_instrumentos(ctx, sessao, dia, tickers_universo)
            except SourceUnavailable as exc:
                ocorrencias += 1
                ctx.manifesto.ocorrencia(FONTE, f"{INSTRUMENTOS} {dia}", str(exc)[:200])
                break
            if estado == "sem pregao":
                falhas_do_mes.append(dia.isoformat())
                continue
            if estado in ("gravado", "em disco"):
                ja_tem.add(mes)
                cadastros_gravados += estado == "gravado"
                # Os 400 antes do primeiro pregao do mes eram feriado.
                sem_pregao.update(falhas_do_mes)
            break
        if mes not in ja_tem and len(falhas_do_mes) >= MAX_400_SEGUIDOS:
            break

    # 3. negocios consolidados, do mais recente ao mais antigo.
    tickers = tickers_etf(config, ctx.universo)
    pasta = pasta_negocios(config)
    gravados = em_disco = pendentes = preliminares = 0
    seguidos_400: list[str] = []
    parou_em: date | None = None
    dias = dias_uteis(inicio, fim)
    for n, dia in enumerate(dias, start=1):
        if (pasta / f"{dia.isoformat()}.csv").exists() and not ctx.pedido.forcar:
            em_disco += 1
            seguidos_400 = []
            continue
        if dia.isoformat() in sem_pregao or dia.isoformat() in incompletos:
            continue
        try:
            conteudo = baixar(sessao, config, NEGOCIOS, dia)
        except SourceUnavailable as exc:
            ocorrencias += 1
            ctx.manifesto.ocorrencia(FONTE, f"{NEGOCIOS} {dia}", str(exc)[:200])
            continue
        if conteudo is None:
            if dia >= ctx.hoje:
                pendentes += 1
                continue
            sem_pregao.add(dia.isoformat())
            seguidos_400.append(dia.isoformat())
            if len(seguidos_400) >= MAX_400_SEGUIDOS:
                # Cinco dias uteis seguidos sem arquivo nao sao feriado: e o
                # comeco do historico. Esses dias nao sao "sem pregao" - saem da
                # lista, e uma janela futura os pede de novo (5 requisicoes).
                sem_pregao.difference_update(seguidos_400)
                parou_em = dia
                break
            continue
        seguidos_400 = []
        status, cabecalho, linhas = ler_csv_b3(conteudo)
        linhas = list(linhas)
        i_segmento = cabecalho.index("SgmtNm") if "SgmtNm" in cabecalho else None
        segmentos = {_norm(l[i_segmento]) for l in linhas if i_segmento is not None and len(l) > i_segmento}
        if i_segmento is not None and linhas and not segmentos & SEGMENTOS_A_VISTA:
            motivo = f"arquivo da B3 sem o mercado à vista (só {', '.join(sorted(segmentos)[:4])})"
            incompletos[dia.isoformat()] = motivo
            ctx.manifesto.ocorrencia(FONTE, f"{NEGOCIOS} {dia}", motivo)
            continue
        etfs = filtrar_negocios(cabecalho, linhas, tickers)
        motivo = _validar(NEGOCIOS, dia, status, cabecalho, etfs)
        if motivo:
            ocorrencias += 1
            _guardar_evidencia(ctx, f"b3-{NEGOCIOS}-{dia.isoformat()}.csv", conteudo)
            ctx.manifesto.ocorrencia(FONTE, f"{NEGOCIOS} {dia}", motivo)
            continue
        _, final = _gravar_dia(ctx, pasta, NEGOCIOS, dia, status, cabecalho, etfs)
        if final:
            gravados += 1
        else:
            preliminares += 1
        if gravados and gravados % 25 == 0:
            ctx.detalhe(f"negócios: {contagem(gravados, 'pregão gravado', 'pregões gravados')} "
                        f"({n} de {len(dias)} dias úteis da janela)")

    if parou_em is not None:
        ctx.manifesto.ocorrencia(FONTE, NEGOCIOS, f"histórico indisponível antes de {data_br(parou_em)}")
    _gravar_sem_pregao(config, sem_pregao)
    armazem.gravar_json(caminho_incompletos(config), dict(sorted(incompletos.items())))
    na_janela = sorted(d for d in incompletos if inicio.isoformat() <= d <= fim.isoformat())

    janela = periodo_br(inicio, fim)
    partes = [
        f"negócios: {contagem(gravados, 'pregão novo', 'pregões novos')}, {em_disco} em disco",
    ]
    if pendentes:
        partes.append(f"{contagem(pendentes, 'dia pendente', 'dias pendentes')} (arquivo ainda não publicado)")
    if preliminares:
        partes.append(f"{contagem(preliminares, 'arquivo preliminar', 'arquivos preliminares')}")
    if cadastro:
        partes.append(f"cadastro de {data_br(cadastro[0])} ({cadastro[1]})")
    if parou_em is not None:
        partes.append(f"histórico da B3 indisponível antes de {data_br(parou_em)}")
    if na_janela:
        partes.append(f"{contagem(len(na_janela), 'pregão', 'pregões')} com arquivo incompleto na B3, sem o "
                      f"mercado à vista ({', '.join(data_br(d) for d in na_janela)}); só --forcar os baixa de novo")
    if ocorrencias:
        partes.append(contagem(ocorrencias, "ocorrência", "ocorrências"))
    novos = gravados + preliminares + cadastros_gravados
    estado = OK if not ocorrencias else PARCIAL
    if not novos and not ocorrencias:
        estado = EM_DISCO
    return ResumoFonte(estado, "; ".join(partes), janela=janela, arquivos=novos)


# --- COTAHIST (serie longa, opcional) -------------------------------------------------


def _dataset_do_ano(ano: int) -> Dataset:
    return Dataset(
        key=f"cotahist_a{ano}",
        url=cotahist.url_for_year(date(ano, 1, 1)),
        filename=f"COTAHIST_A{ano}.ZIP",
        description=f"COTAHIST anual {ano}",
        members={"txt": f"COTAHIST_A{ano}"},
        approx_mb=90,
    )


def linhas_etf_do_cotahist(caminho: Path, tickers: set[str]) -> Iterator[dict]:
    """Linhas brutas (os 26 campos do layout, como texto) de ETF no mercado a vista.

    Fica a linha cujo ticker e de ETF conhecido ou que tem cara de cota de
    fundo com BDI 14 (ETF, e tambem FIAGRO e FINAM/FINOR): ticker renomeado
    antes da janela dos cadastros nao some da serie. Precos com duas casas
    implicitas, como no arquivo.
    """
    with zipfile.ZipFile(caminho) as arquivo:
        nome = arquivo.namelist()[0]
        with arquivo.open(nome) as handle:
            for bruta in io.TextIOWrapper(handle, encoding="iso-8859-1"):
                linha = bruta.rstrip("\r\n")
                if len(linha) < cotahist.LINE_LENGTH or linha[0:2] != "01" or linha[24:27] != "010":
                    continue
                codneg = linha[12:24].strip().upper()
                if codneg in tickers:
                    criterio = "universo"
                elif linha[10:12] == "14" and linha[39:49].strip().upper().startswith("CI"):
                    criterio = "bdi14"
                else:
                    continue
                registro = {campo: linha[a:b].strip() for campo, (a, b) in cotahist.LAYOUT.items()}
                registro["x_criterio"] = criterio
                yield registro


def capturar_cotahist(ctx: Contexto) -> ResumoFonte:
    """Ano fechado do COTAHIST, para a serie longa dos ETFs de acoes e de indice
    estrangeiro (o de renda fixa nao esta nele). So com a fonte pedida."""
    fim = ctx.hoje.year - 1
    anos = list(range(ctx.pedido.desde.year, fim + 1)) if ctx.pedido.desde else [fim]
    tickers = tickers_etf(ctx.config, ctx.universo)
    pasta = ctx.config.etf_bruto_dir / "cotahist"
    feitos, em_disco = [], []
    for ano in anos:
        destino = pasta / f"{ano}.csv"
        if destino.exists() and not ctx.pedido.forcar:
            em_disco.append(ano)
            continue
        dataset = _dataset_do_ano(ano)
        resultado = fetch(ctx.config, dataset)
        cabecalho = list(cotahist.LAYOUT) + ["x_criterio"]
        sha, total = armazem.gravar_csv(destino, cabecalho, linhas_etf_do_cotahist(resultado.path, tickers))
        ctx.manifesto.arquivo(FONTE_COTAHIST, destino, url=dataset.url, sha256=sha, linhas=total,
                              origem_cache=resultado.reason.value)
        feitos.append(f"{ano} ({contagem(total, 'linha', 'linhas')})")
    partes = []
    if feitos:
        partes.append("gravado: " + ", ".join(feitos))
    if em_disco:
        partes.append("em disco: " + ", ".join(str(a) for a in em_disco))
    estado = OK if feitos else EM_DISCO
    return ResumoFonte(estado, "; ".join(partes) or "nenhum ano fechado pedido",
                       janela=f"{anos[0]} a {anos[-1]}" if anos else None, arquivos=len(feitos))
