"""Contrato dos coletores de prateleira.

Regra transversal: `collect()` nunca lanca. Uma plataforma inacessivel vira um
snapshot UNAVAILABLE e a execucao segue - o monitor precisa entregar o que
conseguiu coletar mesmo quando uma fonte cai.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from shelf_monitor.config import Config, PlatformConfig
from shelf_monitor.http import SourceUnavailable, build_session
from shelf_monitor.models import (
    CollectorStrategy,
    FundOffer,
    ShelfSnapshot,
    SnapshotStatus,
)


class BaseCollector(ABC):
    """Coleta a prateleira publica de uma plataforma."""

    def __init__(self, config: Config, platform: PlatformConfig):
        self.config = config
        self.platform = platform
        self.session = build_session(config)
        self.source_urls: list[str] = []
        # Estrategia principal - definida durante a coleta, ja que a fonte de
        # maior confianca que respondeu so se sabe no fim.
        self.strategy_used: CollectorStrategy = platform.strategies[0]
        # Preenchidos pelos coletores que consultam varias fontes.
        self.strategies_used: list[str] = []
        self.source_counts: dict[str, int] = {}
        self.partial_reason: str | None = None
        # Avisos de coleta incompleta (paginacao truncada, por exemplo). Um
        # deles ja basta para o snapshot sair PARTIAL: uma prateleira cortada
        # pela metade nao pode virar a baseline do diff de amanha.
        self.warnings: list[str] = []

    @property
    def platform_id(self) -> str:
        return self.platform.platform_id

    @abstractmethod
    def _collect_impl(self) -> list[FundOffer]:
        """Coleta de fato. Pode lancar SourceUnavailable."""

    def collect(self) -> ShelfSnapshot:
        started = datetime.now()
        try:
            funds = self._collect_impl()
        except SourceUnavailable as exc:
            return self._unavailable(started, str(exc))
        except Exception as exc:  # noqa: BLE001 - coletor nunca derruba o run
            return self._unavailable(started, f"{type(exc).__name__}: {exc}")

        if not funds:
            return self._unavailable(started, "coleta retornou zero fundos")

        notes = [n for n in (self.partial_reason, *self.warnings) if n]
        return ShelfSnapshot(
            platform=self.platform_id,
            collected_at=started,
            strategy=self.strategy_used,
            status=SnapshotStatus.PARTIAL if self.warnings else SnapshotStatus.OK,
            funds=funds,
            source_urls=list(self.source_urls),
            strategies=list(self.strategies_used),
            source_counts=dict(self.source_counts),
            # Uma fonte que caiu nao invalida a coleta, mas explica um numero
            # menor do que o esperado - fica registrada como observacao.
            error=" | ".join(notes) or None,
        )

    def _unavailable(self, started: datetime, error: str) -> ShelfSnapshot:
        return ShelfSnapshot(
            platform=self.platform_id,
            collected_at=started,
            strategy=self.strategy_used,
            status=SnapshotStatus.UNAVAILABLE,
            funds=[],
            source_urls=list(self.source_urls),
            error=error,
        )

    def partial(self, started: datetime, funds: list[FundOffer], reason: str) -> ShelfSnapshot:
        """Coleta incompleta mas util - o diff a trata com cautela."""
        return ShelfSnapshot(
            platform=self.platform_id,
            collected_at=started,
            strategy=self.strategy_used,
            status=SnapshotStatus.PARTIAL,
            funds=funds,
            source_urls=list(self.source_urls),
            error=reason,
        )


class AuthenticatedBrowserCollector(BaseCollector):
    """Contrato para coletores de area logada (v2, sem implementacao concreta).

    A visao logada de cada casa e mais completa que a publica, mas exige a sessao
    do proprio usuario. O desenho evita guardar credenciais: o usuario faz login
    manualmente uma vez, exporta o storage_state do Playwright para
    SHELF_AUTH_STATE_<PLATAFORMA>, e o coletor reusa esse estado ate expirar.

    Para implementar uma subclasse:
      1. sobrescrever `_collect_authenticated(context)` navegando na area logada;
      2. registrar a subclasse em collectors/__init__.py;
      3. apontar auth_storage_state no PlatformConfig (ou via env).
    """

    strategy = CollectorStrategy.AUTHENTICATED

    def __init__(self, config: Config, platform: PlatformConfig):
        super().__init__(config, platform)
        self.strategy_used = CollectorStrategy.AUTHENTICATED

    @property
    def storage_state_path(self) -> Path | None:
        raw = self.platform.auth_storage_state
        if not raw:
            candidate = self.config.storage_state_dir / f"{self.platform_id}.json"
            return candidate if candidate.exists() else None
        path = Path(raw)
        return path if path.exists() else None

    def _collect_impl(self) -> list[FundOffer]:
        state = self.storage_state_path
        if state is None:
            raise SourceUnavailable(
                f"sessao autenticada ausente para {self.platform_id}: exporte o "
                f"storage_state do Playwright para "
                f"{self.config.storage_state_dir / f'{self.platform_id}.json'}"
            )
        return self._collect_authenticated(state)

    def _collect_authenticated(self, storage_state: Path) -> list[FundOffer]:
        raise NotImplementedError(
            "coletor autenticado ainda nao implementado para esta plataforma"
        )
