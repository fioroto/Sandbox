"""Cliente HTTP compartilhado, com deteccao explicita de bloqueio de proxy.

O ambiente de execucao pode ter uma network policy que bloqueia dominios
financeiros no CONNECT (403). Distinguir esse caso de um site fora do ar importa:
o primeiro se resolve liberando a allowlist, o segundo nao.
"""
from __future__ import annotations

import requests

from shelf_monitor.config import Config


class SourceUnavailable(Exception):
    """Fonte inacessivel: bloqueio de rede, erro HTTP ou payload invalido."""

    def __init__(self, message: str, blocked_by_proxy: bool = False):
        super().__init__(message)
        self.blocked_by_proxy = blocked_by_proxy


_PROXY_BLOCK_MARKERS = (
    "connect tunnel failed",
    "tunnel connection failed",
    "proxyerror",
    "403 forbidden",
)


def is_proxy_block(exc: BaseException) -> bool:
    """Heuristica: o erro veio da politica de rede e nao do site em si."""
    text = str(exc).lower()
    return any(marker in text for marker in _PROXY_BLOCK_MARKERS)


def describe_error(exc: BaseException) -> str:
    """Resume uma falha de rede numa linha.

    As excecoes do requests aninham o traceback inteiro do urllib3; despejado
    cru, um unico erro domina o console, o snapshot e o digest.
    """
    if is_proxy_block(exc):
        return "bloqueado pela network policy (CONNECT 403)"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "falha de TLS"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "falha de conexao"
    text = " ".join(str(exc).split())
    return text[:160] + ("..." if len(text) > 160 else "")


def build_session(config: Config) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": config.user_agent,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        }
    )
    return session


def get(
    session: requests.Session,
    url: str,
    config: Config,
    *,
    accept: str | None = None,
    params: dict | None = None,
    stream: bool = False,
) -> requests.Response:
    """GET com timeout do config; converte falhas em SourceUnavailable."""
    headers = {"Accept": accept} if accept else None
    try:
        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=config.http_timeout,
            stream=stream,
        )
    except requests.exceptions.RequestException as exc:
        raise SourceUnavailable(
            f"{url}: {describe_error(exc)}", blocked_by_proxy=is_proxy_block(exc)
        ) from exc

    if response.status_code >= 400:
        raise SourceUnavailable(f"HTTP {response.status_code} em {url}")
    return response


def get_json(
    session: requests.Session,
    url: str,
    config: Config,
    *,
    params: dict | None = None,
) -> dict | list:
    response = get(session, url, config, accept="application/json", params=params)
    try:
        return response.json()
    except ValueError as exc:
        raise SourceUnavailable(f"resposta nao-JSON em {url}: {exc}") from exc


def check_reachable(config: Config, url: str) -> tuple[bool, str]:
    """Testa uma fonte para o subcomando `doctor`. Devolve (ok, detalhe)."""
    session = build_session(config)
    try:
        response = session.get(url, timeout=min(config.http_timeout, 20), stream=True)
        response.close()
    except requests.exceptions.RequestException as exc:
        return False, describe_error(exc)

    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}"
    return True, f"HTTP {response.status_code}"
