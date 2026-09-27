"""Quem e ETF: o cadastro de fundos listados da B3, o registro da CVM e o
universo que junta os dois com o cadastro de instrumentos.

Tres fontes, porque nenhuma sozinha basta (medido em 2026-09-24):
- a B3 (fundos listados) lista 223 ETFs com ticker e CNPJ, mas nao traz ISIN,
  lote nem categoria;
- o cadastro de instrumentos da B3 traz ISIN, lote e categoria, mas nao o CNPJ,
  e poe ETF de renda fixa fora das categorias "ETF" (FIXED INCOME TRADABLE
  INSTRUMENT T1);
- a CVM traz os 252 FIIM com gestor e administrador, inclusive os que ainda
  nao negociam, mas nao o ticker.

O universo e uma linha por ETF, chaveada pelo CNPJ (ou "ticker:XXXX11" quando
o CNPJ nao e conhecido), gravada em `data/etf/universo.csv`.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market import b3_funds, etf_sources
from shelf_monitor.market.datasets import REGISTRO
from shelf_monitor.market.etf_bruto import armazem
from shelf_monitor.market.etf_bruto.contexto import EM_DISCO, OK, PARCIAL, Contexto, ResumoFonte
from shelf_monitor.market.fetch import fetch, read_member
from shelf_monitor.market.parse import iter_rows
from shelf_monitor.normalize import name_slug, normalize_cnpj, normalize_text
from shelf_monitor.texto import contagem, data_br

FONTE_B3_FUNDOS = "b3-fundos"
FONTE_CVM_CADASTRO = "cvm-cadastro"

# O cadastro de fundos listados muda pouco (entra ETF novo algumas vezes por
# mes) e custa ~225 requisicoes: refeito quando a foto tem mais de 7 dias.
VALIDADE_B3_FUNDOS = 7

COLUNAS_UNIVERSO = (
    "chave", "cnpj", "ticker", "isin", "acronimo", "nome_pregao", "nome", "tipo_b3",
    "categoria_b3", "segmento_b3", "inicio_negociacao", "liquidacao_dias", "lote",
    "gestora", "administrador", "id_fnet", "listado", "negocia", "na_cvm", "ligacao", "atualizado_em",
)

# Inicio de negociacao que a B3 poe no cadastro de ETF que nao negocia: em
# 2026-09-23, 20 dos 23 ETFs que so existiam no cadastro de instrumentos
# tinham 9999-12-31 e estavam cancelados na CVM entre 2023 e 2025 (XBIO11,
# USAG11, INDA11, NOGV11...), sem nenhum negocio. Nao contam como listados.
SEM_NEGOCIACAO = "9999"

# Abreviacoes do nome no cadastro da B3 (CrpnNm, cortado em ~50 caracteres)
# e palavras que nao distinguem um ETF de outro.
_ABREVIACOES = {"FDO": "FUNDO", "INV": "INVESTIMENTO", "IND": "INDICE", "CL": "CLASSE", "RESP": "RESPONSABILIDADE",
                "LIM": "LIMITADA", "INVEST": "INVESTIMENTO", "EXT": "EXTERIOR"}
_GENERICAS = {
    "FUNDO", "FUNDOS", "INVESTIMENTO", "INVESTIMENTOS", "INDICE", "CLASSE", "RESPONSABILIDADE", "LIMITADA",
    "LTDA", "RL", "IE", "DE", "DA", "DO", "DAS", "DOS", "E", "EM", "A", "O", "NO", "NA", "EXTERIOR", "ETF", "R",
}


def pasta_b3_fundos(config: Config) -> Path:
    return config.etf_bruto_dir / "b3_fundos"


def pasta_instrumentos(config: Config) -> Path:
    return config.etf_bruto_dir / "b3_arquivos" / "instrumentos"


def pasta_cvm_cadastro(config: Config) -> Path:
    return config.etf_bruto_dir / "cvm_cadastro"


def arquivos_datados(pasta: Path, sufixo: str) -> list[tuple[date, Path]]:
    """(data, caminho) dos arquivos `AAAA-MM-DD<sufixo>` da pasta, do mais antigo ao mais novo."""
    if not pasta.exists():
        return []
    achados = []
    for caminho in pasta.glob(f"*{sufixo}"):
        nome = caminho.name[: -len(sufixo)] if sufixo else caminho.name
        try:
            achados.append((date.fromisoformat(nome), caminho))
        except ValueError:
            continue
    return sorted(achados)


def ultimo_datado(pasta: Path, sufixo: str) -> tuple[date, Path] | None:
    achados = arquivos_datados(pasta, sufixo)
    return achados[-1] if achados else None


# --- B3, fundos listados ------------------------------------------------------------


def capturar_b3_fundos(ctx: Contexto) -> ResumoFonte:
    """Lista e detalhe de cada ETF listado na B3, como a API devolve.

    Tambem atualiza `data/etf/tickers.csv` (CNPJ -> ticker) pelas mesmas regras
    do `etf --collect-sources b3`: linha escrita a mao nunca e sobrescrita.
    """
    ultimo = ultimo_datado(pasta_b3_fundos(ctx.config), ".json")
    if (
        ultimo is not None and not ctx.pedido.forcar and not ctx.explicita(FONTE_B3_FUNDOS)
        and (ctx.hoje - ultimo[0]).days <= VALIDADE_B3_FUNDOS
    ):
        return ResumoFonte(EM_DISCO, f"foto de {data_br(ultimo[0])} (refeita a cada {VALIDADE_B3_FUNDOS} dias)")

    cliente = b3_funds.B3ListedFunds(ctx.config)
    cliente.session = ctx.sessao(FONTE_B3_FUNDOS, intervalo=0.3)
    tipos: dict[str, dict] = {}
    falhas = 0
    for tipo in b3_funds.ETF_TYPES:
        lista = [linha for linha in cliente.list_funds(tipo) if isinstance(linha, dict)]
        detalhes: dict[str, object] = {}
        for linha in lista:
            acronimo = str(linha.get("acronym") or "").strip()
            if not acronimo or linha.get("id") in (None, ""):
                continue
            try:
                detalhes[acronimo] = cliente.fund_detail_raw(tipo, linha["id"], acronimo)
            except SourceUnavailable as exc:
                falhas += 1
                detalhes[acronimo] = {"erro": str(exc)[:200]}
        tipos[tipo] = {"lista": lista, "detalhes": detalhes}

    caminho = pasta_b3_fundos(ctx.config) / f"{ctx.hoje.isoformat()}.json"
    payload = {
        "capturado_em": datetime.now().isoformat(timespec="seconds"),
        "fonte": b3_funds.LISTED_URL,
        "tipos": tipos,
    }
    sha = armazem.gravar_json(caminho, payload)
    total = sum(len(t["detalhes"]) for t in tipos.values())
    ctx.manifesto.arquivo(FONTE_B3_FUNDOS, caminho, url=b3_funds.LISTED_URL, sha256=sha, linhas=total)

    fundos = fundos_do_arquivo(caminho)
    atualizados = _atualizar_tickers(ctx, fundos)
    por_tipo = ", ".join(f"{t} {len(v['detalhes'])}" for t, v in tipos.items())
    detalhe = f"{contagem(total, 'ETF', 'ETFs')} ({por_tipo}); tickers.csv: {atualizados}"
    if falhas:
        ctx.manifesto.ocorrencia(FONTE_B3_FUNDOS, "detalhe", f"{falhas} detalhes falharam")
        return ResumoFonte(PARCIAL, f"{detalhe}; {contagem(falhas, 'detalhe falhou', 'detalhes falharam')}", arquivos=1)
    return ResumoFonte(OK, detalhe, arquivos=1)


def fundos_do_arquivo(caminho: Path) -> list[b3_funds.FundoListadoB3]:
    """Os detalhes gravados, lidos pelo mesmo parser do `etf`."""
    payload = armazem.ler_json(caminho, {}) or {}
    fundos: list[b3_funds.FundoListadoB3] = []
    for tipo, bloco in (payload.get("tipos") or {}).items():
        nomes = {
            str(linha.get("acronym") or "").strip(): linha
            for linha in bloco.get("lista") or [] if isinstance(linha, dict)
        }
        for acronimo, detalhe in (bloco.get("detalhes") or {}).items():
            if not isinstance(detalhe, dict) or "erro" in detalhe:
                continue
            fundo = b3_funds.parse_listed_fund_detail(detalhe, tipo)
            if fundo is None:
                continue
            linha = nomes.get(acronimo) or {}
            fundo.trading_name = fundo.trading_name or (linha.get("tradingName") or None)
            fundo.company_name = fundo.company_name or (linha.get("fundName") or None)
            fundos.append(fundo)
    return fundos


def _atualizar_tickers(ctx: Contexto, fundos: list[b3_funds.FundoListadoB3]) -> str:
    """Grava tickers.csv com os ETFs que casam com o catalogo da CVM.

    Guarda contra captura parcial: se vier menos de 90% das linhas "b3" que o
    arquivo ja tem, ele fica como esta - trocar 220 linhas por 50 apagaria o
    ticker de 170 ETFs rio abaixo.
    """
    no_catalogo = []
    for fundo in fundos:
        cnpj = etf_sources._cnpj_no_catalogo(fundo, ctx.catalogo)
        if cnpj:
            fundo.cnpj = cnpj
            no_catalogo.append(fundo)
    existentes = b3_funds.read_tickers_csv(ctx.config.etf_tickers_path)
    antes = sum(1 for linha in existentes if (linha.get("fonte") or "").strip().lower() == "b3")
    if not no_catalogo:
        return "sem ETF casado com o catálogo da CVM, mantido"
    if antes and len(no_catalogo) < 0.9 * antes:
        return f"mantido ({len(no_catalogo)} casados contra {antes} no arquivo)"
    linhas = b3_funds.merge_tickers(existentes, no_catalogo, ctx.hoje)
    b3_funds.write_tickers_csv(ctx.config.etf_tickers_path, linhas)
    return contagem(len(no_catalogo), "ETF", "ETFs")


# --- CVM, cadastro --------------------------------------------------------------------


def capturar_cvm_cadastro(ctx: Contexto) -> ResumoFonte:
    """As linhas FIIM do registro de classes da CVM e os fundos delas, com todas as colunas.

    E a fonte gratuita de custodiante, auditor e data de constituicao, que o
    mapeamento pede ao regulamento ou a Comdinheiro.
    """
    pasta = pasta_cvm_cadastro(ctx.config) / ctx.hoje.isoformat()
    classes_path = pasta / "registro_classe_etf.csv"
    fundos_path = pasta / "registro_fundo_etf.csv"
    if classes_path.exists() and not ctx.pedido.forcar:
        return ResumoFonte(EM_DISCO, f"extrato de {data_br(ctx.hoje)} já gravado")

    resultado = fetch(ctx.config, REGISTRO)
    cnpjs = _cnpjs_conhecidos(ctx)
    classes = []
    ids_de_fundo: set[str] = set()
    for linha in iter_rows(read_member(resultado.path, REGISTRO.members["classe"])):
        tipo = (linha.get("Tipo_Classe") or "").upper()
        if "FIIM" in tipo or normalize_cnpj(linha.get("CNPJ_Classe")) in cnpjs:
            classes.append(linha)
            ids_de_fundo.add((linha.get("ID_Registro_Fundo") or "").strip())
    fundos = []
    for linha in iter_rows(read_member(resultado.path, REGISTRO.members["fundo"])):
        tipo = (linha.get("Tipo_Fundo") or "").upper()
        if (
            (linha.get("ID_Registro_Fundo") or "").strip() in ids_de_fundo
            or "FIIM" in tipo or normalize_cnpj(linha.get("CNPJ_Fundo")) in cnpjs
        ):
            fundos.append(linha)

    for caminho, linhas in ((classes_path, classes), (fundos_path, fundos)):
        cabecalho = list(linhas[0].keys()) if linhas else []
        sha, total = armazem.gravar_csv(caminho, cabecalho, linhas)
        ctx.manifesto.arquivo(FONTE_CVM_CADASTRO, caminho, url=REGISTRO.url, sha256=sha, linhas=total,
                              origem_cache=resultado.reason.value)
    return ResumoFonte(
        OK,
        f"{contagem(len(classes), 'classe', 'classes')} e {contagem(len(fundos), 'fundo', 'fundos')} "
        f"(arquivo da CVM: {resultado.reason.value})",
        arquivos=2,
    )


def _cnpjs_conhecidos(ctx: Contexto) -> set[str]:
    cnpjs = {cnpj for cnpj, meta in ctx.catalogo.items() if getattr(meta, "is_etf", False)}
    cnpjs.update(etf_sources.tickers_do_arquivo(ctx.config))
    cnpjs.update(linha.get("cnpj") for linha in ctx.universo if linha.get("cnpj"))
    cnpjs.discard(None)
    return cnpjs


# --- universo ------------------------------------------------------------------------


def _palavras(nome: str | None) -> list[str]:
    return [
        _ABREVIACOES.get(p, p) for p in normalize_text(nome).split()
        if _ABREVIACOES.get(p, p) not in _GENERICAS
    ]


def _mesmo_fundo_abreviado(nome_b3: str | None, nome_cvm: str | None) -> bool:
    """O nome do cadastro da B3 e o da CVM abreviado e cortado: toda palavra da
    B3 e comeco de alguma palavra da CVM ("AGG" de "AGGREGATE"), com pelo menos
    tres palavras que distinguem."""
    b3, cvm = _palavras(nome_b3), _palavras(nome_cvm)
    return len(b3) >= 3 and all(any(c.startswith(p) for c in cvm) for p in b3)


def _linhas_padrao_do_cadastro(config: Config) -> dict[str, dict]:
    """Ticker -> linha do cadastro de instrumentos mais recente (so o lote padrao)."""
    ultimo = ultimo_datado(pasta_instrumentos(config), ".csv")
    if ultimo is None:
        return {}
    return {
        (linha.get("TckrSymb") or "").strip().upper(): linha
        for linha in armazem.ler_csv(ultimo[1])
        if linha.get("x_tipo_linha") == "padrao" and linha.get("TckrSymb")
    }


def _tickers_escritos_a_mao(config: Config) -> dict[str, str]:
    """CNPJ -> ticker das linhas de tickers.csv que nao vieram da coleta da B3.

    As linhas "b3" repetem o que a API de fundos listados diz, e ela erra: em
    2026-09-24 dava "T10R0" para o ETF que negocia como T10R11.
    """
    return {
        normalize_cnpj(linha.get("cnpj")): (linha.get("ticker") or "").strip().upper()
        for linha in b3_funds.read_tickers_csv(config.etf_tickers_path)
        if (linha.get("fonte") or "").strip().lower() != "b3"
        and normalize_cnpj(linha.get("cnpj")) and (linha.get("ticker") or "").strip()
    }


def montar_universo(config: Config, catalogo: dict, hoje: date) -> list[dict]:
    """Uma linha por ETF, juntando B3 (fundos listados e instrumentos), CVM e tickers.csv.

    Ligacao do cadastro de instrumentos (que nao tem CNPJ) ao CNPJ, nesta ordem:
    o ticker; o acronimo (`Asst` = `acronym` da B3); o nome igual ao da CVM
    (`name_slug`); o nome abreviado da B3 contido no da CVM, so entre ETFs que
    negociam e so quando um casa com um. Nome apenas parecido nao liga: CNPJ
    errado contamina tudo rio abaixo - para esses, uma linha manual em
    tickers.csv resolve. A coluna `ligacao` diz qual regra ligou.
    """
    ultimo_b3 = ultimo_datado(pasta_b3_fundos(config), ".json")
    fundos_b3 = fundos_do_arquivo(ultimo_b3[1]) if ultimo_b3 else []
    instrumentos = _linhas_padrao_do_cadastro(config)
    tickers_csv = _tickers_escritos_a_mao(config)

    linhas: dict[str, dict] = {}

    def linha_de(chave: str) -> dict:
        if chave not in linhas:
            linhas[chave] = {coluna: "" for coluna in COLUNAS_UNIVERSO}
            linhas[chave]["chave"] = chave
        return linhas[chave]

    for fundo in fundos_b3:
        cnpj = etf_sources._cnpj_no_catalogo(fundo, catalogo) or fundo.cnpj
        if not cnpj:
            continue
        linha = linha_de(cnpj)
        linha.update({
            "cnpj": cnpj,
            "ticker": linha["ticker"] or fundo.ticker,
            "acronimo": fundo.acronym,
            "nome_pregao": fundo.trading_name or "",
            "nome": linha["nome"] or (fundo.company_name or ""),
            "tipo_b3": str(fundo.type_id or ""),
            "id_fnet": "" if fundo.id_fnet is None else str(fundo.id_fnet),
            "administrador": linha["administrador"] or (fundo.admin_name or ""),
            "listado": "sim",
            "ligacao": "b3",
        })

    for cnpj, meta in catalogo.items():
        if not getattr(meta, "is_etf", False):
            continue
        linha = linha_de(cnpj)
        linha["cnpj"] = cnpj
        linha["nome"] = meta.name or linha["nome"]
        linha["gestora"] = meta.manager_name or ""
        linha["administrador"] = meta.admin_name or linha["administrador"]
        linha["ticker"] = linha["ticker"] or (meta.ticker or "")
        linha["na_cvm"] = "sim"
        linha["ligacao"] = linha["ligacao"] or ("cvm" if linha["ticker"] else "")

    for linha in linhas.values():
        cnpj = linha["cnpj"]
        if cnpj and tickers_csv.get(cnpj):
            linha["ticker"] = tickers_csv[cnpj]
            linha["ligacao"] = "tickers.csv"

    # Ticker que nao negocia, mas cujo acronimo esta no cadastro: vale o do cadastro.
    por_acronimo = {
        (cadastro.get("Asst") or "").strip().upper(): ticker
        for ticker, cadastro in instrumentos.items() if cadastro.get("Asst")
    }
    for linha in linhas.values():
        if linha["ticker"] not in instrumentos and linha["acronimo"].upper() in por_acronimo:
            if not (linha["cnpj"] and linha["cnpj"] in tickers_csv):
                linha["ticker"] = por_acronimo[linha["acronimo"].upper()]
                linha["ligacao"] = "acrônimo"

    def negocia(cadastro: dict) -> bool:
        return not (cadastro.get("TradgStartDt") or "").startswith(SEM_NEGOCIACAO)

    vistos = {linha["ticker"] for linha in linhas.values() if linha["ticker"]}
    sem_ticker = [l for l in linhas.values() if not l["ticker"] and l["nome"]]
    por_slug = {name_slug(l["nome"]): l for l in sem_ticker}
    orfaos = []
    for ticker, cadastro in instrumentos.items():
        if ticker in vistos:
            continue
        mesmo_nome = por_slug.pop(name_slug(cadastro.get("CrpnNm")), None) if cadastro.get("CrpnNm") else None
        if mesmo_nome is not None:
            mesmo_nome.update({"ticker": ticker, "ligacao": "nome"})
            vistos.add(ticker)
        else:
            orfaos.append(ticker)
    # Nome abreviado: um ETF que negocia casando com um, e so um, sem ticker.
    candidatos: dict[str, list[dict]] = {}
    for ticker in orfaos:
        cadastro = instrumentos[ticker]
        if negocia(cadastro):
            candidatos[ticker] = [l for l in sem_ticker if not l["ticker"]
                                  and _mesmo_fundo_abreviado(cadastro.get("CrpnNm"), l["nome"])]
    disputados = {id(l) for lista in candidatos.values() for l in lista
                  if sum(1 for outra in candidatos.values() if any(x is l for x in outra)) > 1}
    for ticker in orfaos:
        achados = candidatos.get(ticker) or []
        if len(achados) == 1 and id(achados[0]) not in disputados:
            achados[0].update({"ticker": ticker, "ligacao": "nome abreviado"})
        else:
            linha_de(f"ticker:{ticker}").update({"ticker": ticker, "ligacao": "só cadastro B3"})

    for linha in linhas.values():
        cadastro = instrumentos.get(linha["ticker"])
        if cadastro:
            linha.update({
                "isin": cadastro.get("ISIN") or "",
                "acronimo": linha["acronimo"] or (cadastro.get("Asst") or ""),
                "categoria_b3": cadastro.get("SctyCtgyNm") or "",
                "segmento_b3": cadastro.get("SgmtNm") or "",
                "inicio_negociacao": cadastro.get("TradgStartDt") or "",
                "liquidacao_dias": cadastro.get("DaysToSttlm") or "",
                "lote": cadastro.get("AllcnRndLot") or "",
                "nome": linha["nome"] or (cadastro.get("CrpnNm") or ""),
                "negocia": "sim" if negocia(cadastro) else "não",
            })
            if negocia(cadastro):
                linha["listado"] = "sim"
        linha["listado"] = linha["listado"] or "não"
        linha["negocia"] = linha["negocia"] or "não"
        linha["na_cvm"] = linha["na_cvm"] or "não"
        linha["atualizado_em"] = hoje.isoformat()

    return sorted(linhas.values(), key=lambda l: (l["ticker"] == "", l["ticker"], l["chave"]))


def gravar_universo(config: Config, linhas: list[dict]) -> Path:
    armazem.gravar_csv(config.etf_universo_path, COLUNAS_UNIVERSO, linhas)
    return config.etf_universo_path


def ler_universo(config: Config) -> list[dict]:
    return armazem.ler_csv(config.etf_universo_path)


def listados(universo: list[dict]) -> list[dict]:
    """Os ETFs listados na B3 - o denominador de toda cobertura."""
    return [linha for linha in universo if linha.get("listado") == "sim"]
