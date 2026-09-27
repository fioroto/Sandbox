"""Rede do extrator: pausa entre requisicoes, nova tentativa e timeout minimo.

O resto do monitor faz uma tentativa por requisicao com 60 s de timeout. Para a
captura bruta isso nao basta - tres medicoes de 2026-09-24:

- a primeira pagina de uma busca do Fundos.NET levou de 60 a 64 s (a segunda
  pagina da mesma busca, 0,1 s); com timeout de 60 s ela cai sempre na borda;
- um ano de pregoes da B3 sao ~550 requisicoes em sequencia, e um 503 no meio
  nao pode derrubar o resto;
- ~220 detalhes de fundo na B3 e ~250 PDFs no Fundos.NET pedem uma pausa entre
  requisicoes para nao martelar servidor publico.

A sessao daqui e um `requests.Session`, entao os clientes que ja existem
(`FundosNetClient`, `B3ListedFunds`) a usam sem mudar: basta trocar o atributo
`session` deles.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests
from urllib3.util.retry import Retry

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable, build_session, describe_error, is_proxy_block

# Status que valem nova tentativa: excesso de requisicoes e erro do servidor.
_STATUS_REPETIVEIS = (429, 500, 502, 503, 504)


class SessaoEducada(requests.Session):
    """Sessao que espera `intervalo` segundos entre requisicoes ao mesmo host
    e nunca usa timeout menor que `timeout_minimo`."""

    def __init__(self, intervalo: float = 0.3, timeout_minimo: float | None = None):
        super().__init__()
        self.intervalo = intervalo
        self.timeout_minimo = timeout_minimo
        self._ultimo: dict[str, float] = {}

    def _timeout(self, valor):
        if not self.timeout_minimo:
            return valor
        if valor is None:
            return self.timeout_minimo
        if isinstance(valor, tuple):
            conexao, leitura = valor
            return conexao, max(leitura or 0, self.timeout_minimo)
        return max(valor, self.timeout_minimo)

    def request(self, method, url, *args, **kwargs):
        host = urlsplit(str(url)).netloc
        espera = self._ultimo.get(host, 0.0) + self.intervalo - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        kwargs["timeout"] = self._timeout(kwargs.get("timeout"))
        try:
            return super().request(method, url, *args, **kwargs)
        finally:
            self._ultimo[host] = time.monotonic()


def sessao(
    config: Config,
    *,
    intervalo: float = 0.3,
    tentativas: int = 3,
    espera: float = 2.0,
    timeout_minimo: float | None = None,
) -> SessaoEducada:
    """Sessao com o TLS do monitor, pausa entre requisicoes e nova tentativa.

    A nova tentativa e do urllib3: erro de conexao, timeout de leitura e os
    status de `_STATUS_REPETIVEIS`, com espera crescente (0, 2x`espera`,
    4x`espera`...) e respeito ao Retry-After. Esgotadas as tentativas, a
    ultima resposta volta como veio (`raise_on_status=False`) e quem chamou
    decide - `obter` transforma em SourceUnavailable.
    """
    nova = SessaoEducada(intervalo=intervalo, timeout_minimo=timeout_minimo)
    build_session(config, nova)
    politica = Retry(
        total=tentativas,
        connect=tentativas,
        read=tentativas,
        status=tentativas,
        backoff_factor=espera,
        status_forcelist=_STATUS_REPETIVEIS,
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    for adaptador in nova.adapters.values():
        adaptador.max_retries = politica
    return nova


def obter(
    sessao_http: requests.Session,
    url: str,
    config: Config,
    *,
    params: dict | None = None,
    accept: str | None = None,
    ausente: tuple[int, ...] = (),
) -> requests.Response | None:
    """GET que devolve None para os status de `ausente` e levanta nos demais erros.

    `ausente` e o "nao ha arquivo" de cada fonte: a B3 responde 400 no
    `requestname` de dia sem pregao, a CVM responde 404 para competencia que
    ainda nao saiu. Nenhum dos dois e falha da fonte.
    """
    headers = {"Accept": accept} if accept else None
    try:
        resposta = sessao_http.get(url, params=params, headers=headers, timeout=config.http_timeout)
    except requests.exceptions.RequestException as exc:
        raise SourceUnavailable(f"{url}: {describe_error(exc)}", blocked_by_proxy=is_proxy_block(exc)) from exc
    if resposta.status_code in ausente:
        return None
    if resposta.status_code >= 400:
        raise SourceUnavailable(f"HTTP {resposta.status_code} em {url}")
    return resposta


def enviar(sessao_http: requests.Session, url: str, config: Config, *, data: dict) -> requests.Response:
    """POST de formulario; erro de rede ou HTTP >= 400 vira SourceUnavailable.

    A mensagem de erro leva so a URL, nunca o corpo: o corpo da Comdinheiro
    carrega usuario e senha.
    """
    try:
        resposta = sessao_http.post(url, data=data, timeout=config.http_timeout)
    except requests.exceptions.RequestException as exc:
        raise SourceUnavailable(f"{url}: {describe_error(exc)}", blocked_by_proxy=is_proxy_block(exc)) from exc
    if resposta.status_code >= 400:
        raise SourceUnavailable(f"HTTP {resposta.status_code} em {url}")
    return resposta
