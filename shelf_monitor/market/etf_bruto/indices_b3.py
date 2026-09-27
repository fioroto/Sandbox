"""Indices da B3: serie diaria (por ano) e carteira do dia.

API publica, sem autenticacao e sem cookie, com os parametros em base64 no
path, como a de fundos listados (medido em 2026-09-24, 0,1 s por chamada):

    indexStatisticsProxy/IndexCall/GetPortfolioDay/<b64 {"index":"IBOV","language":"pt-br","year":"2026"}>
        -> fechamento de cada dia do ano: linhas por dia do mes, rateValue1..12 por mes
    indexProxy/indexCall/GetPortfolioDay/<b64 {"language":"pt-br","pageNumber":1,"pageSize":120,"index":"IBOV","segment":"1"}>
        -> carteira do dia: header (data, redutor, quantidade teorica) e results (cod, asset, part...)

Indice de outro provedor (IMA-B da ANBIMA, S&P) volta com tudo nulo - nao e
erro, e fica registrado. A B3 so da a carteira do dia: `--dia` no passado nao
tem carteira. Guarda-se o JSON como veio; a leitura dos numeros (formato
brasileiro, "160.538,69") e da etapa seguinte.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market.b3_funds import decode_b3_json, encode_params
from shelf_monitor.market.etf_bruto import armazem, manual, rede
from shelf_monitor.market.etf_bruto.contexto import EM_DISCO, OK, PARCIAL, Contexto, ResumoFonte
from shelf_monitor.texto import contagem

FONTE = "indices-b3"
URL_SERIE = "https://sistemaswebb3-listados.b3.com.br/indexStatisticsProxy/IndexCall/GetPortfolioDay/"
URL_CARTEIRA = "https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/"

# Os indices que a regua de ETF de acoes Brasil usa, capturados mesmo sem ETF
# apontado para eles em indices.csv (sao a referencia de beta e de categoria).
PADRAO = ("IBOV", "IBXX", "IBXL", "SMLL", "IDIV", "IFIX")
_TAMANHO_DA_PAGINA = 120
_MAX_PAGINAS = 20


def pasta(config: Config) -> Path:
    return config.etf_bruto_dir / "indices_b3"


def codigos(ctx: Contexto) -> list[str]:
    """Os indices padrao, os que a pessoa apontou em indices.csv e as sugestoes do nome."""
    achados = set(PADRAO)
    achados.update(manual.codigos_b3(ctx.config, ctx.universo).values())
    return sorted(c for c in achados if c)


def _json(resposta) -> object:
    try:
        return decode_b3_json(resposta.text)
    except ValueError:
        return None


def _data_do_header(header: dict | None) -> date | None:
    texto = str((header or {}).get("date") or "").strip()
    for formato in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _serie_vazia(dados: object) -> bool:
    """Serie de indice de outro provedor: sem nenhum valor preenchido."""
    if not isinstance(dados, dict):
        return True
    linhas = dados.get("results") or []
    return not any(
        str(valor or "").strip()
        for linha in linhas if isinstance(linha, dict)
        for chave, valor in linha.items() if chave.startswith("rateValue")
    )


def capturar(ctx: Contexto) -> ResumoFonte:
    config = ctx.config
    sessao = ctx.sessao(FONTE, intervalo=0.2)
    fim = min(ctx.pedido.dia, ctx.hoje)
    inicio = ctx.pedido.desde or date(fim.year - 1, 1, 1)
    anos = list(range(inicio.year, fim.year + 1))
    lista = codigos(ctx)
    series = carteiras = em_disco = 0
    sem_dado: list[str] = []
    falhas = 0
    for codigo in lista:
        # Serie: ano fechado uma vez; o ano corrente uma vez por dia.
        for ano in anos:
            destino = pasta(config) / "serie" / codigo / f"{ano}.json"
            if destino.exists() and not ctx.pedido.forcar:
                capturado = armazem.ler_json(destino, {}) or {}
                if ano < fim.year or str(capturado.get("capturado_em", ""))[:10] == ctx.hoje.isoformat():
                    em_disco += 1
                    continue
            url = URL_SERIE + encode_params({"index": codigo, "language": "pt-br", "year": str(ano)})
            try:
                resposta = rede.obter(sessao, url, config, accept="application/json")
            except SourceUnavailable as exc:
                falhas += 1
                ctx.manifesto.ocorrencia(FONTE, f"{codigo} {ano}", str(exc)[:200])
                continue
            dados = _json(resposta)
            if _serie_vazia(dados):
                sem_dado.append(f"{codigo} {ano}")
                continue
            sha = armazem.gravar_json(destino, {
                "capturado_em": datetime.now().isoformat(timespec="seconds"),
                "indice": codigo, "ano": ano, "url": url, "resposta": dados,
            })
            ctx.manifesto.arquivo(FONTE, destino, url=url, sha256=sha)
            series += 1
        # Carteira: so a do dia existe na B3.
        if fim != ctx.hoje:
            continue
        paginas, total_paginas, pagina = [], 1, 1
        try:
            while pagina <= min(total_paginas, _MAX_PAGINAS):
                url = URL_CARTEIRA + encode_params({
                    "language": "pt-br", "pageNumber": pagina, "pageSize": _TAMANHO_DA_PAGINA,
                    "index": codigo, "segment": "1",
                })
                dados = _json(rede.obter(sessao, url, config, accept="application/json"))
                if not isinstance(dados, dict):
                    break
                paginas.append(dados)
                total_paginas = int((dados.get("page") or {}).get("totalPages") or 1)
                pagina += 1
        except SourceUnavailable as exc:
            falhas += 1
            ctx.manifesto.ocorrencia(FONTE, f"carteira {codigo}", str(exc)[:200])
            continue
        header = paginas[0].get("header") if paginas else None
        dia_da_carteira = _data_do_header(header)
        if not paginas or dia_da_carteira is None:
            sem_dado.append(f"carteira {codigo}")
            continue
        destino = pasta(config) / "carteira" / codigo / f"{dia_da_carteira.isoformat()}.json"
        if destino.exists() and not ctx.pedido.forcar:
            em_disco += 1
            continue
        sha = armazem.gravar_json(destino, {
            "capturado_em": datetime.now().isoformat(timespec="seconds"),
            "indice": codigo, "url": URL_CARTEIRA, "paginas": paginas,
        })
        ctx.manifesto.arquivo(FONTE, destino, url=URL_CARTEIRA, sha256=sha,
                              linhas=sum(len(p.get("results") or []) for p in paginas))
        carteiras += 1

    partes = [
        f"{contagem(len(lista), 'índice', 'índices')} ({', '.join(lista)})",
        f"{contagem(series, 'série nova', 'séries novas')}, {contagem(carteiras, 'carteira nova', 'carteiras novas')}",
    ]
    if em_disco:
        partes.append(f"{em_disco} em disco")
    if sem_dado:
        partes.append(f"sem dado na B3 (outro provedor?): {', '.join(sem_dado[:8])}")
    if fim != ctx.hoje:
        partes.append("carteira só existe para o dia de hoje")
    if falhas:
        partes.append(contagem(falhas, "falha", "falhas"))
    novos = series + carteiras
    estado = PARCIAL if falhas else (OK if novos else EM_DISCO)
    return ResumoFonte(estado, "; ".join(partes), janela=f"{anos[0]} a {anos[-1]}", arquivos=novos)
