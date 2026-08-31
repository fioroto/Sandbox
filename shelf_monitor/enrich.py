"""Normalizacao das prateleiras contra a base oficial do mercado (CVM).

Cada casa descreve categoria do seu jeito ("Credito Pos" no BTG, "baixo risco"
no Itau, categoria ANBIMA no Open Finance). A base de mercado ja importada
(`python -m shelf_monitor market`) tem a classificacao ANBIMA e CVM oficial por
CNPJ de classe - e do fundo, para os legados. O join por CNPJ carimba toda
prateleira com a mesma taxonomia, e a comparacao entre casas deixa de ser
aproximacao.

Sem a base em disco o enriquecimento simplesmente nao acontece: os relatorios
continuam saindo com o dado das fontes, e a cobertura informa o que faltou.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.models import AssetClass, FundOffer, ShelfSnapshot
from shelf_monitor.normalize import classify_asset_class, normalize_cnpj, parse_number

# Chaves que o enriquecimento grava em FundOffer.extra. Consumidores (planilha,
# dashboard, deck) leem estas - nunca as variantes por casa.
KEY_ANBIMA = "classificacao_anbima"
KEY_CVM = "classificacao_cvm"
KEY_GESTOR_OFICIAL = "gestor_oficial"
KEY_CNPJ_GESTOR = "cnpj_gestor"
KEY_SITUACAO = "situacao_cvm"
KEY_PL = "pl_cvm"
KEY_PUBLICO = "publico_alvo_cvm"
KEY_ORIGEM_BASE = "base_cvm"


@dataclass
class MarketRecord:
    """O que a base oficial sabe sobre um CNPJ (classe ou fundo legado)."""

    cnpj: str
    origem: str
    anbima: str | None = None
    cvm: str | None = None
    gestor: str | None = None
    cnpj_gestor: str | None = None
    situacao: str | None = None
    pl: float | None = None
    publico_alvo: str | None = None


@dataclass
class EnrichmentStats:
    """Cobertura do join - o dado de rodape que um comite pergunta primeiro."""

    total: int = 0
    with_cnpj: int = 0
    matched: int = 0
    anbima_filled: int = 0
    by_platform: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "with_cnpj": self.with_cnpj,
            "matched": self.matched,
            "anbima_filled": self.anbima_filled,
            "by_platform": self.by_platform,
        }


class MarketLookup:
    """Indice por CNPJ do mercado.csv gerado pelo modulo market.

    Dois indices, porque as prateleiras anunciam dois CNPJs diferentes:

    - o da **classe** (e o que o informe e a distribuicao usam) - indice direto;
    - o do **fundo** (o envelope; e o que o Open Finance publica em cnpjNumber).
      Num fundo multi-classes o envelope tem CNPJ proprio que nao e o de
      nenhuma classe - sem este segundo indice, todo fundo anunciado pelo
      envelope saia "sem gestora e sem classificacao", que foi exatamente o
      sintoma observado na primeira rodada real.

    No indice por fundo, gestor e situacao valem sempre (sao do envelope);
    classificacao so quando as classes do fundo concordam - atribuir ao
    envelope a classe de uma unica filha seria inventar dado.
    """

    def __init__(
        self,
        records: dict[str, MarketRecord],
        fund_records: dict[str, MarketRecord],
        source: Path,
    ):
        self._records = records
        self._fund_records = fund_records
        self.source = source

    def __len__(self) -> int:
        return len(self._records)

    def get(self, cnpj: str | None) -> MarketRecord | None:
        if not cnpj:
            return None
        return self._records.get(cnpj) or self._fund_records.get(cnpj)

    @classmethod
    def load(cls, config: Config) -> "MarketLookup | None":
        """Carrega o consolidado; None quando a base nao foi importada."""
        path = config.market_dir / "mercado.csv"
        if not path.is_file():
            return None

        records: dict[str, MarketRecord] = {}
        by_fund: dict[str, list[dict]] = {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=";"):
                cnpj = normalize_cnpj(row.get("cnpj"))
                if not cnpj or cnpj in records:
                    # O consolidado poe as classes RCVM175 antes dos legados;
                    # em colisao, a primeira (mais oficial) fica.
                    continue
                records[cnpj] = MarketRecord(
                    cnpj=cnpj,
                    origem=row.get("origem") or "",
                    anbima=row.get("classificacao_anbima") or None,
                    cvm=row.get("classificacao_cvm") or None,
                    gestor=row.get("gestor") or None,
                    cnpj_gestor=normalize_cnpj(row.get("cnpj_gestor")),
                    situacao=row.get("situacao") or None,
                    pl=parse_number(row.get("patrimonio_liquido")),
                    publico_alvo=row.get("publico_alvo") or None,
                )
                fund_cnpj = normalize_cnpj(row.get("cnpj_fundo"))
                if fund_cnpj:
                    by_fund.setdefault(fund_cnpj, []).append(row)

        fund_records: dict[str, MarketRecord] = {}
        for fund_cnpj, rows in by_fund.items():
            if fund_cnpj in records:
                continue  # envelope com CNPJ de classe ja resolve pelo direto
            fund_records[fund_cnpj] = _fund_level_record(fund_cnpj, rows)
        return cls(records, fund_records, path)


def _unanimous(values: list[str]) -> str | None:
    distinct = {v for v in values if v}
    return distinct.pop() if len(distinct) == 1 else None


def _fund_level_record(fund_cnpj: str, rows: list[dict]) -> MarketRecord:
    """Consolida as classes de um fundo num registro do envelope."""
    return MarketRecord(
        cnpj=fund_cnpj,
        origem=(rows[0].get("origem") or "") + "_fundo",
        anbima=_unanimous([r.get("classificacao_anbima") or "" for r in rows]),
        cvm=_unanimous([r.get("classificacao_cvm") or "" for r in rows]),
        gestor=rows[0].get("gestor") or None,
        cnpj_gestor=normalize_cnpj(rows[0].get("cnpj_gestor")),
        situacao=_unanimous([r.get("situacao") or "" for r in rows]),
        pl=None,  # PL e por classe; somar aqui inventaria um numero
        publico_alvo=_unanimous([r.get("publico_alvo") or "" for r in rows]),
    )


def enrich_offer(offer: FundOffer, record: MarketRecord) -> None:
    """Carimba um fundo com o dado oficial, preservando o que a casa disse.

    A classificacao da casa nao se perde: se divergir da oficial, vai para
    `classificacao_anbima_casa` - a divergencia em si e informacao.
    """
    house_value = offer.extra.get(KEY_ANBIMA)
    if record.anbima:
        if house_value and house_value != record.anbima:
            offer.extra["classificacao_anbima_casa"] = house_value
        offer.extra[KEY_ANBIMA] = record.anbima
    if record.cvm:
        offer.extra.setdefault(KEY_CVM, record.cvm)
    if record.gestor:
        offer.extra[KEY_GESTOR_OFICIAL] = record.gestor
        if offer.manager_name is None:
            offer.manager_name = record.gestor
    if record.cnpj_gestor:
        offer.extra.setdefault(KEY_CNPJ_GESTOR, record.cnpj_gestor)
        if offer.manager_cnpj is None:
            offer.manager_cnpj = record.cnpj_gestor
    if record.situacao:
        offer.extra[KEY_SITUACAO] = record.situacao
    if record.pl is not None:
        offer.extra.setdefault(KEY_PL, record.pl)
    if record.publico_alvo:
        offer.extra.setdefault(KEY_PUBLICO, record.publico_alvo)
    offer.extra[KEY_ORIGEM_BASE] = record.origem

    # A classe de ativo derivada ganha a versao oficial quando a heuristica
    # sobre o nome nao tinha concluido nada.
    if offer.asset_class == AssetClass.OUTROS:
        official = classify_asset_class(record.anbima, record.cvm)
        if official != AssetClass.OUTROS:
            offer.asset_class = official


def enrich_snapshots(
    config: Config, snapshots: list[ShelfSnapshot]
) -> EnrichmentStats | None:
    """Enriquece todos os fundos in-place. None quando nao ha base importada."""
    lookup = MarketLookup.load(config)
    if lookup is None:
        return None

    stats = EnrichmentStats()
    for snapshot in snapshots:
        per_platform = stats.by_platform.setdefault(
            snapshot.platform, {"total": 0, "matched": 0}
        )
        for offer in snapshot.funds:
            stats.total += 1
            per_platform["total"] += 1
            if not offer.cnpj:
                continue
            stats.with_cnpj += 1
            record = lookup.get(offer.cnpj)
            if record is None:
                continue
            stats.matched += 1
            per_platform["matched"] += 1
            had_anbima = bool(offer.extra.get(KEY_ANBIMA))
            enrich_offer(offer, record)
            if not had_anbima and offer.extra.get(KEY_ANBIMA):
                stats.anbima_filled += 1
    return stats
