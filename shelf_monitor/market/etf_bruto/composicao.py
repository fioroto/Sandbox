"""Carteira dos ETFs: a mensal da CVM (padronizada) e o arquivo da gestora (como vier).

CVM: o arquivo de carteira (`cda_fi_AAAAMM.zip`, ~22 MB) traz os fundos
estruturados no membro `cda_fie_AAAAMM.csv` - e ali estao os ETFs. Medido em
2026-09-24 no de 08/2026: `TP_FUNDO_CLASSE == "CLASSES FIIM"` da 7.774
posicoes de 205 ETFs, CNPJ em `CNPJ_FUNDO_CLASSE`. A competencia sai algumas
semanas depois do fim do mes (09/2026 respondia 404). Guarda-se so as linhas
de ETF, com todas as colunas; o membro `_CONFID` (posicao sob sigilo), quando
existe, vai junto.

Gestora: cada uma publica a carteira do dia num formato, e os sites testados
(XP Asset, BlackRock) respondem 403 a cliente HTTP. Duas portas de entrada:
uma lista de URLs em `data/etf/manual/gestoras_urls.csv` (baixadas como vierem)
e a pasta `data/etf/entrada/gestoras/`, onde a pessoa solta o arquivo baixado no
navegador - de preferencia com o ticker no comeco do nome (BOVA11_carteira.xlsx).
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
# A definicao do zip e o calendario de publicacao moram no catalogo de
# datasets: o mesmo download serve a carteira dos ETFs (membro cda_fie) e o
# mapa de familias (membros cda_fi_*). Reexportados aqui porque este modulo e
# os testes dele ja os chamavam por este nome.
from shelf_monitor.market.datasets import (  # noqa: F401 - reexportados
    COMPETENCIAS_CDA,
    PRAZO_DE_PUBLICACAO_CDA_DIAS as PRAZO_DE_PUBLICACAO_DIAS,
    competencias_fechadas,
    dataset_cda,
)
from shelf_monitor.market.etf_bruto import armazem, manual, rede
from shelf_monitor.market.etf_bruto.contexto import EM_DISCO, OK, PARCIAL, Contexto, ResumoFonte
from shelf_monitor.market.fetch import fetch, read_member
from shelf_monitor.market.parse import iter_rows
from shelf_monitor.normalize import normalize_cnpj
from shelf_monitor.texto import competencia_br, contagem

FONTE_CDA = "cvm-cda"
FONTE_GESTORAS = "gestoras"


def pasta_cda(config: Config) -> Path:
    return config.etf_bruto_dir / "cvm_cda"


def pasta_gestoras(config: Config) -> Path:
    return config.etf_bruto_dir / "gestoras"


def _linhas_etf(conteudo: bytes, cnpjs: set[str]) -> list[dict]:
    linhas = []
    for linha in iter_rows(conteudo):
        tipo = (linha.get("TP_FUNDO_CLASSE") or linha.get("TP_FUNDO") or "").upper()
        cnpj = normalize_cnpj(linha.get("CNPJ_FUNDO_CLASSE") or linha.get("CNPJ_FUNDO"))
        if "FIIM" in tipo or (cnpj and cnpj in cnpjs):
            linhas.append(linha)
    return linhas


def capturar_cda(ctx: Contexto) -> ResumoFonte:
    """As linhas de ETF da carteira mensal da CVM, uma competencia por arquivo."""
    cnpjs = {linha.get("cnpj") for linha in ctx.universo if linha.get("cnpj")}
    pasta = pasta_cda(ctx.config)
    competencias = competencias_fechadas(ctx.hoje)
    feitas, em_disco, nao_publicadas = [], [], []
    for competencia in competencias:
        destino = pasta / f"cda_fie_etf_{competencia}.csv"
        if destino.exists() and not ctx.pedido.forcar:
            em_disco.append(competencia)
            continue
        dataset = dataset_cda(competencia)
        try:
            resultado = fetch(ctx.config, dataset)
        except SourceUnavailable as exc:
            if "HTTP 404" in str(exc):
                nao_publicadas.append(competencia)
                continue
            raise
        linhas = _linhas_etf(read_member(resultado.path, f"cda_fie_{competencia}"), cnpjs)
        cabecalho = list(linhas[0].keys()) if linhas else []
        sha, total = armazem.gravar_csv(destino, cabecalho, linhas)
        ctx.manifesto.arquivo(FONTE_CDA, destino, url=dataset.url, sha256=sha, linhas=total,
                              origem_cache=resultado.reason.value)
        etfs = {normalize_cnpj(l.get("CNPJ_FUNDO_CLASSE") or l.get("CNPJ_FUNDO")) for l in linhas}
        confidencial = ""
        try:
            sigilo = _linhas_etf(read_member(resultado.path, f"cda_fie_confid_{competencia}"), cnpjs)
        except SourceUnavailable:
            sigilo = []
        if sigilo:
            destino_sigilo = pasta / f"cda_fie_confid_etf_{competencia}.csv"
            sha, total_sigilo = armazem.gravar_csv(destino_sigilo, list(sigilo[0].keys()), sigilo)
            ctx.manifesto.arquivo(FONTE_CDA, destino_sigilo, url=dataset.url, sha256=sha, linhas=total_sigilo)
            confidencial = f", {total_sigilo} sob sigilo"
        feitas.append(f"{competencia_br(competencia)}: {contagem(len(etfs - {None}), 'ETF', 'ETFs')}, "
                      f"{contagem(total, 'posição', 'posições')}{confidencial}")
    partes = []
    if feitas:
        partes.append("; ".join(feitas))
    if em_disco:
        partes.append("em disco: " + ", ".join(competencia_br(c) for c in em_disco))
    if nao_publicadas:
        partes.append("ainda não publicada: " + ", ".join(competencia_br(c) for c in nao_publicadas))
    estado = OK if feitas else EM_DISCO
    return ResumoFonte(estado, "; ".join(partes), janela=", ".join(competencia_br(c) for c in competencias),
                       arquivos=len(feitas))


# --- gestoras --------------------------------------------------------------------------


def _extensao(url: str, content_type: str | None, formato: str | None) -> str:
    if formato:
        return "." + formato.strip().lower().lstrip(".")
    sufixo = Path(urlsplit(url).path).suffix.lower()
    if sufixo and len(sufixo) <= 5:
        return sufixo
    tipo = (content_type or "").split(";")[0].strip()
    return mimetypes.guess_extension(tipo) or ".bin"


def capturar_gestoras(ctx: Contexto) -> ResumoFonte:
    """Arquivo de carteira de cada URL ativa da planilha e o que estiver na pasta de entrada."""
    config = ctx.config
    manual.garantir_cabecalho(config, *manual.GESTORAS_URLS)
    urls = [linha for linha in manual.ler(config, manual.GESTORAS_URLS[0])
            if (linha.get("url") or "").strip() and manual.ativo(linha)]
    sessao = ctx.sessao(FONTE_GESTORAS, intervalo=1.0) if urls else None
    baixados = bloqueados = falhas = em_disco = 0
    for linha in urls:
        ticker = (linha.get("ticker") or "").strip().upper() or "SEM_TICKER"
        url = linha["url"].strip()
        pasta = pasta_gestoras(config) / ticker
        if any(pasta.glob(f"{ctx.hoje.isoformat()}.*")) and not ctx.pedido.forcar:
            em_disco += 1
            continue
        try:
            resposta = rede.obter(sessao, url, config, ausente=(401, 403))
        except SourceUnavailable as exc:
            falhas += 1
            ctx.manifesto.ocorrencia(FONTE_GESTORAS, ticker, str(exc)[:200])
            continue
        if resposta is None:
            bloqueados += 1
            ctx.manifesto.ocorrencia(
                FONTE_GESTORAS, ticker,
                "bloqueado para cliente HTTP (403): baixe no navegador e solte em data/etf/entrada/gestoras/",
            )
            continue
        destino = pasta / f"{ctx.hoje.isoformat()}{_extensao(url, resposta.headers.get('Content-Type'), linha.get('formato'))}"
        sha = armazem.gravar_atomico(destino, resposta.content)
        ctx.manifesto.arquivo(FONTE_GESTORAS, destino, url=url, sha256=sha, bytes=len(resposta.content))
        baixados += 1
    entradas = armazem.registrar_entradas(
        config.etf_entrada_dir / "gestoras", pasta_gestoras(config) / "_entrada", ctx.hoje, ctx.manifesto, FONTE_GESTORAS,
    )
    partes = [
        f"URLs: {contagem(len(urls), 'ativa', 'ativas')}, {baixados} baixadas",
        f"pasta de entrada: {contagem(len(entradas), 'arquivo novo', 'arquivos novos')}",
    ]
    if em_disco:
        partes.append(f"{em_disco} já baixadas hoje")
    if bloqueados:
        partes.append(f"{bloqueados} bloqueadas (403)")
    if falhas:
        partes.append(contagem(falhas, "falha", "falhas"))
    if not urls and not entradas:
        return ResumoFonte(EM_DISCO, "nenhuma URL em manual/gestoras_urls.csv e nada novo em entrada/gestoras/")
    estado = PARCIAL if (bloqueados or falhas) else OK
    return ResumoFonte(estado, "; ".join(partes), arquivos=baixados + len(entradas))
