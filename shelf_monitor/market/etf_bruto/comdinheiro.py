"""Comdinheiro (assinatura): a API, com as consultas que a pessoa configura, e a
pasta de entrada, para o que ela exporta a mao.

O mapeamento recomenda a Comdinheiro para retorno ajustado por proventos, DY,
volatilidade, beta, drawdown e indices estrangeiros (RET, PRECO_AJ, DY_12M,
PROVENTOS, MDD, vol_ano_12m, INFO_INDICE, PESO_INDICE, VOLUME_MEDIO...). Cada
consulta e uma URL de ferramenta da propria Comdinheiro, copiada da tela
("HistoricoCotacao002.php?&x=BOVA11+IVVB11&data_ini=...") para
`data/etf/manual/comdinheiro_consultas.csv` (colunas nome, url_ferramenta,
ativo, observacao). `{dia}` e `{desde}` na URL viram a data em ddmmaaaa.

A chamada e um POST de formulario com usuario, senha, a URL da consulta e o
formato (json3). O endpoint padrao e o da documentacao da API
(Clientes/API/EndPoint001.php?code=import_data) - NAO conferido contra a
fonte, porque exige assinatura: se o trecho de codigo que a Comdinheiro gera
no botao de API mostrar outro, basta `COMDINHEIRO_API_URL` no .env. A resposta
e gravada como veio; o que nao for JSON, ou for JSON de erro, vai para
`<nome>.erro.txt` e nao conta como capturado.

Usuario e senha so vao no corpo do POST: nunca em URL, mensagem ou manifesto.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market.etf_bruto import armazem, manual, rede
from shelf_monitor.market.etf_bruto.contexto import EM_DISCO, OK, PARCIAL, SEM_CONFIGURACAO, Contexto, ResumoFonte
from shelf_monitor.texto import contagem

FONTE = "comdinheiro"
URL_PADRAO = "https://www.comdinheiro.com.br/Clientes/API/EndPoint001.php?code=import_data"
FORMATO = "json3"
# Chaves que, no primeiro nivel do JSON, indicam erro da API e nao dado.
_CHAVES_DE_ERRO = ("erro", "error", "mensagem_erro", "msg_erro")
_NOME_SEGURO = re.compile(r"[^A-Za-z0-9_.-]+")


def pasta(config: Config) -> Path:
    return config.etf_bruto_dir / "comdinheiro"


def credenciais() -> tuple[str, str] | None:
    usuario = (os.getenv("COMDINHEIRO_USUARIO") or "").strip()
    senha = os.getenv("COMDINHEIRO_SENHA") or ""
    return (usuario, senha) if usuario and senha else None


def endpoint() -> str:
    return (os.getenv("COMDINHEIRO_API_URL") or "").strip() or URL_PADRAO


def expandir(url_ferramenta: str, dia: date, desde: date) -> str:
    """{dia} e {desde} -> ddmmaaaa, o formato de data das ferramentas da Comdinheiro."""
    return url_ferramenta.replace("{dia}", dia.strftime("%d%m%Y")).replace("{desde}", desde.strftime("%d%m%Y"))


def resposta_de_erro(corpo: bytes) -> str | None:
    """Motivo, quando o corpo nao e dado: nao e JSON, ou e JSON de erro."""
    try:
        dados = json.loads(corpo.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return "resposta não é JSON"
    if isinstance(dados, dict):
        for chave in _CHAVES_DE_ERRO:
            if dados.get(chave):
                return f"a API respondeu {chave}: {str(dados[chave])[:120]}"
    return None


def capturar(ctx: Contexto) -> ResumoFonte:
    config = ctx.config
    manual.garantir_cabecalho(config, *manual.COMDINHEIRO_CONSULTAS)
    entradas = armazem.registrar_entradas(
        config.etf_entrada_dir / "comdinheiro", pasta(config) / "entrada", ctx.hoje, ctx.manifesto, FONTE,
    )
    consultas = [linha for linha in manual.ler(config, manual.COMDINHEIRO_CONSULTAS[0])
                 if (linha.get("url_ferramenta") or "").strip() and manual.ativo(linha)]
    acesso = credenciais()
    partes = [f"pasta de entrada: {contagem(len(entradas), 'arquivo novo', 'arquivos novos')}"]
    if acesso is None:
        partes.insert(0, "API sem usuário e senha (COMDINHEIRO_USUARIO e COMDINHEIRO_SENHA no .env)")
        return ResumoFonte(SEM_CONFIGURACAO if not entradas else OK, "; ".join(partes), arquivos=len(entradas))
    if not consultas:
        partes.insert(0, "nenhuma consulta ativa em manual/comdinheiro_consultas.csv")
        return ResumoFonte(SEM_CONFIGURACAO if not entradas else OK, "; ".join(partes), arquivos=len(entradas))

    url = endpoint()
    if not url.lower().startswith("https://"):
        partes.insert(0, "COMDINHEIRO_API_URL precisa ser https (a senha vai no corpo)")
        return ResumoFonte(SEM_CONFIGURACAO, "; ".join(partes), arquivos=len(entradas))

    sessao = ctx.sessao(FONTE, intervalo=1.0, timeout_minimo=120)
    fim = min(ctx.pedido.dia, ctx.hoje)
    desde = ctx.pedido.desde or (fim - timedelta(days=366))
    destino = pasta(config) / "api" / ctx.hoje.isoformat()
    gravadas = em_disco = erros = 0
    for linha in consultas:
        nome = _NOME_SEGURO.sub("_", (linha.get("nome") or "").strip()) or f"consulta_{gravadas + erros + em_disco + 1}"
        alvo = destino / f"{nome}.json"
        if alvo.exists() and not ctx.pedido.forcar:
            em_disco += 1
            continue
        consulta = expandir(linha["url_ferramenta"].strip(), fim, desde)
        try:
            resposta = rede.enviar(sessao, url, config, data={
                "username": acesso[0], "password": acesso[1], "URL": consulta, "format": FORMATO,
            })
        except SourceUnavailable as exc:
            erros += 1
            ctx.manifesto.ocorrencia(FONTE, nome, str(exc)[:200])
            continue
        motivo = resposta_de_erro(resposta.content)
        if motivo:
            erros += 1
            erro_path = destino / f"{nome}.erro.txt"
            armazem.gravar_atomico(erro_path, resposta.content)
            ctx.manifesto.ocorrencia(FONTE, nome, motivo)
            continue
        sha = armazem.gravar_atomico(alvo, resposta.content)
        # No manifesto vai a consulta (URL da ferramenta), nunca o corpo do POST.
        ctx.manifesto.arquivo(FONTE, alvo, url=url, referencia=consulta, sha256=sha, bytes=len(resposta.content))
        gravadas += 1
    partes.insert(0, f"API: {contagem(len(consultas), 'consulta ativa', 'consultas ativas')}, "
                     f"{gravadas} gravadas" + (f", {em_disco} já gravadas hoje" if em_disco else ""))
    if erros:
        partes.append(contagem(erros, "erro", "erros"))
    if not gravadas and not erros and not entradas:
        return ResumoFonte(EM_DISCO, "; ".join(partes))
    return ResumoFonte(PARCIAL if erros else OK, "; ".join(partes), arquivos=gravadas + len(entradas))
