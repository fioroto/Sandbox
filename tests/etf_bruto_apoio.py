"""Apoio dos testes do extrator bruto de ETF: sessao HTTP falsa, contexto e
arquivos no formato da B3. Nao e arquivo de teste (nao comeca com test_), entao
o discover so o importa.
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime

import requests
from rich.console import Console

from shelf_monitor.config import Config
from shelf_monitor.market.etf_bruto.armazem import Manifesto
from shelf_monitor.market.etf_bruto.contexto import Contexto, Pedido

# Cabecalhos reais (medidos em 2026-09-24 nos arquivos de 23/09/2026).
CABECALHO_INSTRUMENTOS = (
    "RptDt;TckrSymb;Asst;AsstDesc;SgmtNm;MktNm;SctyCtgyNm;XprtnDt;XprtnCd;TradgStartDt;TradgEndDt;BaseCd;"
    "ConvsCritNm;MtrtyDtTrgtPt;ReqrdConvsInd;ISIN;CFICd;DlvryNtceStartDt;DlvryNtceEndDt;OptnTp;CtrctMltplr;"
    "AsstQtnQty;AllcnRndLot;TradgCcy;DlvryTpNm;WdrwlDays;WrkgDays;ClnrDays;RlvrBasePricNm;OpngFutrPosDay;"
    "SdTpCd1;UndrlygTckrSymb1;SdTpCd2;UndrlygTckrSymb2;PureGoldWght;ExrcPric;OptnStyle;ValTpNm;PrmUpfrntInd;"
    "OpngPosLmtDt;DstrbtnId;PricFctr;DaysToSttlm;SrsTpNm;PrtcnFlg;AutomtcExrcInd;SpcfctnCd;CrpnNm;"
    "CorpActnStartDt;CtdyTrtmntTpNm;MktCptlstn;CorpGovnLvlNm"
).split(";")
CABECALHO_NEGOCIOS = (
    "RptDt;TckrSymb;ISIN;SgmtNm;MinPric;MaxPric;TradAvrgPric;LastPric;OscnPctg;AdjstdQt;AdjstdQtTax;RefPric;"
    "TradQty;FinInstrmQty;NtlFinVol"
).split(";")


def instrumentos(dia: str) -> list[dict]:
    """Uma amostra do cadastro: ETF de acoes, de indice estrangeiro com lotes de
    bloco, de renda fixa (T1/FORWARD), mercado primario e o que nao e ETF."""
    base = {"RptDt": dia, "TradgEndDt": "9999-12-31", "TradgCcy": "BRL", "AllcnRndLot": "1", "PricFctr": "1"}
    return [
        {**base, "TckrSymb": "BOVA11", "Asst": "BOVA", "SgmtNm": "CASH", "MktNm": "EQUITY-CASH",
         "SctyCtgyNm": "ETF EQUITIES", "TradgStartDt": "2026-07-16", "ISIN": "BRBOVACTF003", "DaysToSttlm": "2",
         "SpcfctnCd": "CI", "CrpnNm": "ISHARES IBOVESPA FUNDO DE ÍNDICE"},
        {**base, "TckrSymb": "ACWI11", "Asst": "ACWI", "SgmtNm": "CASH", "MktNm": "EQUITY-CASH",
         "SctyCtgyNm": "ETF FOREIGN INDEX", "TradgStartDt": "2021-06-18", "ISIN": "BRACWICTF003", "DaysToSttlm": "2"},
        {**base, "TckrSymb": "ACWI11M", "Asst": "ACWI", "SgmtNm": "Equity Block Trading Lot", "MktNm": "EQUITY-CASH",
         "SctyCtgyNm": "ETF FOREIGN INDEX", "ISIN": "BRACWICTF003"},
        {**base, "TckrSymb": "ACWI11Q", "Asst": "ACWI", "SgmtNm": "Equity Block Trading Lot", "MktNm": "EQUITY-CASH",
         "SctyCtgyNm": "ETF FOREIGN INDEX", "ISIN": "BRACWICTF003"},
        {**base, "TckrSymb": "IMAB11", "Asst": "IMAB", "SgmtNm": "FORWARD", "MktNm": "FIXED INCOME",
         "SctyCtgyNm": "FIXED INCOME TRADABLE INSTRUMENT T1", "TradgStartDt": "2020-06-29", "ISIN": "BRIMABCTF004",
         "DaysToSttlm": "1"},
        {**base, "TckrSymb": "03BK11", "Asst": "03BK", "SgmtNm": "FORWARD", "MktNm": "FIXED INCOME",
         "SctyCtgyNm": "FIXED INCOME TRADABLE INSTRUMENT T1", "ISIN": "BR03BKCTF019", "DaysToSttlm": "1"},
        {**base, "TckrSymb": "03BKETF11H", "Asst": "03BK", "SgmtNm": "ETF PRIMARY MARKET", "MktNm": "FIXED INCOME",
         "SctyCtgyNm": "ETF PRIMARY MARKET\xa0 GROSS SETTLEMENT", "ISIN": "BR03BKCTF019", "DaysToSttlm": "1"},
        # Nao sao ETF: debenture negociavel (T1 sem ISIN de cota), acao, FII e opcao sobre ETF.
        {**base, "TckrSymb": "DEBX11", "Asst": "DEBX", "SgmtNm": "FORWARD", "MktNm": "FIXED INCOME",
         "SctyCtgyNm": "FIXED INCOME TRADABLE INSTRUMENT T1", "ISIN": "BRDEBXDBS001"},
        {**base, "TckrSymb": "PETR4", "Asst": "PETR", "SgmtNm": "CASH", "SctyCtgyNm": "SHARES", "ISIN": "BRPETRACNPR6"},
        {**base, "TckrSymb": "HGLG11", "Asst": "HGLG", "SgmtNm": "CASH", "SctyCtgyNm": "FUNDS", "ISIN": "BRHGLGCTF004"},
        {**base, "TckrSymb": "BOVAA1", "Asst": "BOVA", "SgmtNm": "EQUITY CALL", "SctyCtgyNm": "OPTION ON EQUITIES"},
    ]


def negocios(dia: str) -> list[dict]:
    return [
        {"RptDt": dia, "TckrSymb": "BOVA11", "ISIN": "BRBOVACTF003", "SgmtNm": "CASH", "LastPric": "182,86",
         "TradQty": "29464", "FinInstrmQty": "3053965", "NtlFinVol": "558129805,04"},
        {"RptDt": dia, "TckrSymb": "BOVA11Q", "ISIN": "BRBOVACTF003", "SgmtNm": "Equity Block Trading Lot",
         "LastPric": "183,00", "TradQty": "1", "FinInstrmQty": "500000", "NtlFinVol": "91500000"},
        {"RptDt": dia, "TckrSymb": "IMAB11", "ISIN": "BRIMABCTF004", "SgmtNm": "FORWARD", "LastPric": "118,05",
         "TradQty": "2444", "FinInstrmQty": "38672", "NtlFinVol": "4544604,54"},
        {"RptDt": dia, "TckrSymb": "PETR4", "ISIN": "BRPETRACNPR6", "SgmtNm": "CASH", "LastPric": "30,00"},
        {"RptDt": dia, "TckrSymb": "HGLG11Q", "ISIN": "BRHGLGCTF004", "SgmtNm": "Equity Block Trading Lot"},
    ]


def arquivo_b3(cabecalho: list[str], linhas: list[dict], status: str | None = "Final") -> bytes:
    saida = io.StringIO()
    if status:
        saida.write(f"Status do Arquivo: {status}\n")
    saida.write(";".join(cabecalho) + "\n")
    for linha in linhas:
        saida.write(";".join(linha.get(coluna, "") for coluna in cabecalho) + "\n")
    return saida.getvalue().encode("latin-1")


def resposta(status: int = 200, conteudo: bytes | str = b"", headers: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = conteudo.encode("utf-8") if isinstance(conteudo, str) else conteudo
    r.headers.update(headers or {})
    r.encoding = "utf-8"
    return r


class SessaoFalsa:
    """Sessao que responde por uma funcao (metodo, url, params, data) e anota as chamadas."""

    def __init__(self, tratador):
        self.tratador = tratador
        self.chamadas: list[tuple] = []
        self.headers: dict = {}

    def get(self, url, params=None, headers=None, timeout=None, **_):
        self.chamadas.append(("GET", url, dict(params or {}), None))
        return self.tratador("GET", url, dict(params or {}), None)

    def post(self, url, data=None, timeout=None, **_):
        self.chamadas.append(("POST", url, {}, dict(data or {})))
        return self.tratador("POST", url, {}, dict(data or {}))


def tratador_b3(arquivos: dict[tuple[str, str], bytes]):
    """Portal de arquivos da B3: token para os (arquivo, dia) conhecidos, 400 no resto."""
    from shelf_monitor.market.etf_bruto import b3_arquivos

    def tratar(metodo, url, params, data):
        if url == b3_arquivos.URL_TOKEN:
            chave = (params["fileName"], params["date"])
            if chave not in arquivos:
                return resposta(400, b'{"title":"Bad Request","status":400}')
            corpo = {"redirectUrl": "~/download?token=x", "token": f"{chave[0]}|{chave[1]}",
                     "file": {"name": f"{chave[0]}_{chave[1].replace('-', '')}_1", "extension": ".csv"}}
            return resposta(200, json.dumps(corpo))
        if url == b3_arquivos.URL_DOWNLOAD:
            nome, dia = params["token"].split("|")
            return resposta(200, arquivos[(nome, dia)])
        raise AssertionError(f"URL inesperada: {url}")

    return tratar


def contexto(
    config: Config,
    *,
    hoje: date,
    dia: date | None = None,
    desde: date | None = None,
    forcar: bool = False,
    explicitas: tuple[str, ...] = (),
    sessao=None,
    universo: list[dict] | None = None,
    catalogo: dict | None = None,
) -> Contexto:
    pedido = Pedido(fontes=explicitas, dia=dia or hoje, desde=desde, forcar=forcar, explicitas=frozenset(explicitas))
    ctx = Contexto(
        config, pedido, hoje, Manifesto(datetime.now(), {}, base=config.data_dir),
        fabrica_sessao=(lambda **_: sessao) if sessao is not None else (lambda **_: _sem_rede()),
        console=Console(file=io.StringIO()),
    )
    ctx.catalogo = catalogo or {}
    ctx.universo = universo or []
    return ctx


def _sem_rede():
    def tratar(metodo, url, params, data):
        raise AssertionError(f"rede acessada num teste sem rede: {url}")
    return SessaoFalsa(tratar)
