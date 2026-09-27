"""O que todo adaptador recebe (Contexto) e devolve (ResumoFonte).

Separado do orquestrador para os adaptadores nao importarem `extrator` (que
importa todos eles). A sessao HTTP vem de uma fabrica injetavel: os testes
passam uma sessao falsa e nenhuma requisicao sai da maquina.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import requests
from rich.console import Console

from shelf_monitor.config import Config
from shelf_monitor.market.etf_bruto import rede
from shelf_monitor.market.etf_bruto.armazem import Manifesto

# Estados de uma fonte ao fim da execucao - saem na tela e no manifesto.
OK = "ok"
PARCIAL = "parcial"
EM_DISCO = "em disco"
SEM_CONFIGURACAO = "sem configuração"
INDISPONIVEL = "indisponível"
ERRO = "erro"


@dataclass(frozen=True)
class Pedido:
    """O que a pessoa pediu na linha de comando."""

    fontes: tuple[str, ...]
    dia: date
    desde: date | None = None
    forcar: bool = False
    # Fontes nomeadas em --fontes (e nao so herdadas do padrao): algumas so
    # fazem o trabalho caro quando pedidas de proposito.
    explicitas: frozenset[str] = frozenset()


@dataclass
class ResumoFonte:
    estado: str
    detalhe: str
    janela: str | None = None
    arquivos: int = 0

    @property
    def falhou(self) -> bool:
        return self.estado == ERRO


FabricaDeSessao = Callable[..., requests.Session]


@dataclass
class Contexto:
    config: Config
    pedido: Pedido
    hoje: date
    manifesto: Manifesto
    fabrica_sessao: FabricaDeSessao | None = None
    console: Console = field(default_factory=Console)
    # Preenchidos pelo orquestrador: o catalogo FIIM da CVM (lazy) e o universo.
    _catalogo: dict | None = None
    universo: list[dict] = field(default_factory=list)
    _sessoes: dict[str, requests.Session] = field(default_factory=dict)

    @property
    def bruto(self) -> Path:
        return self.config.etf_bruto_dir

    def explicita(self, fonte: str) -> bool:
        return fonte in self.pedido.explicitas

    def sessao(self, chave: str, **opcoes) -> requests.Session:
        """Uma sessao por fonte (cookies e pausa sao por host de qualquer jeito)."""
        if chave not in self._sessoes:
            fabrica = self.fabrica_sessao or (lambda **kw: rede.sessao(self.config, **kw))
            self._sessoes[chave] = fabrica(**opcoes)
        return self._sessoes[chave]

    @property
    def catalogo(self) -> dict:
        """Catalogo FIIM da CVM (`build_etf_catalog`), montado so quando alguem pede."""
        if self._catalogo is None:
            from shelf_monitor.market.etf import build_etf_catalog

            self._catalogo = build_etf_catalog(self.config)
        return self._catalogo

    @catalogo.setter
    def catalogo(self, valor: dict) -> None:
        self._catalogo = valor

    def detalhe(self, texto: str) -> None:
        self.console.print(f"  [dim]{texto}[/]")
