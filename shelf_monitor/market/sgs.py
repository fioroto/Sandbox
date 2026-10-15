"""Series do SGS do Banco Central: CDI, Selic, IPCA e dolar, com cache local.

A API do SGS e aberta e sem restricao de uso automatizado
(https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados): devolve uma
lista de {"data": "dd/mm/aaaa", "valor": "0.050788"}. Serie diaria aceita ate
10 anos por consulta; o monitor pede no maximo a janela mais um respiro.

Cache em `data/cache/sgs/<codigo>.json` (fora do git: o numero que importa sai
no bloco publicado), com a data desde a qual a serie foi pedida. A rede so e
consultada quando o cache nao foi pedido desde o comeco necessario, ou quando a
ultima observacao e anterior ao fim pedido e o cache nao foi atualizado hoje -
competencia ja fechada nao volta a rede.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable, build_session, get_json

URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
PAGINA = "https://www3.bcb.gov.br/sgspub/"

# Folga antes do comeco pedido: o CDI do primeiro dia util da janela e a Selic
# vigente na base precisam de uma observacao anterior a ela.
_FOLGA = timedelta(days=15)


@dataclass(frozen=True)
class Serie:
    chave: str
    codigo: int
    rotulo: str
    unidade: str


SERIES: dict[str, Serie] = {
    s.chave: s
    for s in (
        Serie("cdi", 12, "CDI", "% ao dia"),
        Serie("selic_meta", 432, "Meta Selic", "% ao ano"),
        Serie("ipca", 433, "IPCA", "% no mês"),
        Serie("dolar", 1, "Dólar (PTAX venda)", "R$ por US$"),
    )
}

Observacoes = list[tuple[date, float]]


def _caminho(config: Config, serie: Serie):
    return config.cache_dir / "sgs" / f"{serie.codigo}.json"


@dataclass
class _Cache:
    observacoes: Observacoes
    desde: date | None = None
    atualizado: date | None = None


def _ler_cache(config: Config, serie: Serie) -> _Cache:
    caminho = _caminho(config, serie)
    if not caminho.is_file():
        return _Cache([])
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return _Cache(
            observacoes=[(date.fromisoformat(d), float(v)) for d, v in dados["observacoes"]],
            desde=date.fromisoformat(dados["desde"]),
            atualizado=date.fromisoformat(dados["atualizado_em"]),
        )
    except (ValueError, KeyError, TypeError):
        return _Cache([])


def _gravar_cache(config: Config, serie: Serie, cache: _Cache) -> None:
    caminho = _caminho(config, serie)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps({
        "codigo": serie.codigo,
        "rotulo": serie.rotulo,
        "unidade": serie.unidade,
        "desde": cache.desde.isoformat() if cache.desde else None,
        "atualizado_em": cache.atualizado.isoformat() if cache.atualizado else None,
        "observacoes": [[d.isoformat(), v] for d, v in cache.observacoes],
    }, ensure_ascii=False) + "\n", encoding="utf-8")


def ler_resposta(dados: list) -> Observacoes:
    """A lista do SGS em (data, valor), em ordem; valor vazio fica de fora."""
    saida: dict[date, float] = {}
    for item in dados or []:
        try:
            dia = datetime.strptime(str(item["data"]), "%d/%m/%Y").date()
            valor = float(str(item["valor"]).replace(",", "."))
        except (KeyError, ValueError, TypeError):
            continue
        saida[dia] = valor
    return sorted(saida.items())


def _baixar(config: Config, serie: Serie, de: date, ate: date, session=None) -> Observacoes:
    session = session or build_session(config)
    dados = get_json(session, URL.format(codigo=serie.codigo), config, params={
        "formato": "json",
        "dataInicial": de.strftime("%d/%m/%Y"),
        "dataFinal": ate.strftime("%d/%m/%Y"),
    })
    if not isinstance(dados, list):
        raise SourceUnavailable(f"SGS {serie.codigo}: resposta sem a lista de observações")
    return ler_resposta(dados)


def _precisa_baixar(cache: _Cache, inicio: date, ate: date, hoje: date) -> bool:
    if not cache.observacoes or cache.desde is None or cache.desde > inicio:
        return True
    return cache.observacoes[-1][0] < ate and cache.atualizado != hoje


def carregar(
    config: Config,
    chaves: tuple[str, ...],
    de: date,
    ate: date,
    *,
    baixar: bool = True,
    hoje: date | None = None,
    session=None,
) -> tuple[dict[str, Observacoes], list[str]]:
    """({chave: observacoes}, avisos) das series pedidas, de `de` a `ate`.

    Sem rede (ou com a rede falhando), vale o que o cache tiver, com aviso.
    """
    hoje = hoje or date.today()
    saida: dict[str, Observacoes] = {}
    avisos: list[str] = []
    for chave in chaves:
        serie = SERIES[chave]
        cache = _ler_cache(config, serie)
        inicio = de - _FOLGA
        if baixar and _precisa_baixar(cache, inicio, ate, hoje):
            comeco = min(inicio, cache.desde) if cache.desde else inicio
            try:
                novas = _baixar(config, serie, comeco, max(ate, hoje), session=session)
            except SourceUnavailable as exc:
                avisos.append(f"SGS do Banco Central ({serie.rotulo}) indisponível: {exc}")
            else:
                juntas = dict(cache.observacoes)
                juntas.update(novas)
                cache = _Cache(sorted(juntas.items()), desde=comeco, atualizado=hoje)
                _gravar_cache(config, serie, cache)
        saida[chave] = [(d, v) for d, v in cache.observacoes if inicio <= d <= ate]
        if not saida[chave]:
            avisos.append(f"sem a série {serie.rotulo} do SGS entre {de:%d/%m/%Y} e {ate:%d/%m/%Y}")
    return saida, avisos
