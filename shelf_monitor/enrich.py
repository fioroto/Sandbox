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
from shelf_monitor.market.flows import FlowSummary, load_summaries
from shelf_monitor.models import AssetClass, FundOffer, ShelfSnapshot
from shelf_monitor.normalize import (
    classify_asset_class,
    normalize_cnpj,
    normalize_cvm_code,
    normalize_text,
    parse_number,
)

# Chaves que o enriquecimento grava em FundOffer.extra. Consumidores (planilha,
# dashboard, deck) leem estas - nunca as variantes por casa.
KEY_ANBIMA = "classificacao_anbima"
KEY_CVM = "classificacao_cvm"
KEY_GESTOR_OFICIAL = "gestor_oficial"
KEY_CNPJ_GESTOR = "cnpj_gestor"
# O que a casa dizia antes de a CVM prevalecer - a divergencia e informacao.
KEY_GESTOR_CASA = "gestor_casa"
KEY_CNPJ_GESTOR_CASA = "cnpj_gestor_casa"
# Por qual chave o fundo casou com o cadastro: cnpj, codigo_subclasse,
# codigo_classe ou codigo_fundo.
KEY_MATCH = "match_cvm"
KEY_SITUACAO = "situacao_cvm"
KEY_PL = "pl_cvm"
KEY_PUBLICO = "publico_alvo_cvm"
KEY_ORIGEM_BASE = "base_cvm"
KEY_EXCLUSIVO = "exclusivo_cvm"
# Captacao liquida do informe diario, em janelas de meses FECHADOS. A janela
# efetivamente coberta esta em KEY_CAPT_JANELA: uma serie recem-importada com
# 4 meses nao pode se passar por 12m.
KEY_CAPT_1M = "captacao_liquida_1m"
KEY_CAPT_3M = "captacao_liquida_3m"
KEY_CAPT_12M = "captacao_liquida_12m"
KEY_CAPT_JANELA = "captacao_janela_meses"
KEY_CAPT_DATA = "captacao_data_base"  # ISO do ultimo informe visto
KEY_PL_INFORME = "pl_informe"  # PL do informe - mais fresco que o do cadastro
KEY_COTISTAS = "cotistas_informe"
# Rentabilidade pela cota, em FRACAO (0.12 = 12%), sobre meses fechados e na
# mesma serie de cota; a janela realmente coberta esta em KEY_RENT_JANELA.
KEY_RENT_1M = "rentabilidade_1m"
KEY_RENT_3M = "rentabilidade_3m"
KEY_RENT_12M = "rentabilidade_12m"
KEY_RENT_JANELA = "rentabilidade_janela_meses"
KEY_VOL_12M = "volatilidade_12m"  # anualizada, fracao
KEY_VOL_DIAS = "volatilidade_12m_dias"  # pares diarios que a sustentam
KEY_TICKET = "ticket_medio"  # PL / cotistas do informe mais fresco
KEY_COTISTAS_VAR_1M = "cotistas_var_1m"
KEY_COTISTAS_VAR_3M = "cotistas_var_3m"
KEY_COTISTAS_VAR_12M = "cotistas_var_12m"
KEY_COTISTAS_VAR_PCT_12M = "cotistas_var_pct_12m"


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
    exclusivo: str | None = None


@dataclass
class EnrichmentStats:
    """Cobertura do join - o dado de rodape que um comite pergunta primeiro."""

    total: int = 0
    with_cnpj: int = 0
    with_cvm_code: int = 0
    matched: int = 0
    matched_by_cvm_code: int = 0
    anbima_filled: int = 0
    managers_from_cvm: int = 0
    managers_inferred: int = 0
    flows_matched: int = 0
    returns_matched: int = 0
    by_platform: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "with_cnpj": self.with_cnpj,
            "with_cvm_code": self.with_cvm_code,
            "matched": self.matched,
            "matched_by_cvm_code": self.matched_by_cvm_code,
            "anbima_filled": self.anbima_filled,
            "managers_from_cvm": self.managers_from_cvm,
            "managers_inferred": self.managers_inferred,
            "flows_matched": self.flows_matched,
            "returns_matched": self.returns_matched,
            "by_platform": self.by_platform,
        }


class MarketLookup:
    """Indice do mercado.csv gerado pelo modulo market, por CNPJ e por codigo.

    Dois indices por CNPJ, porque as prateleiras anunciam dois CNPJs
    diferentes:

    - o da **classe** (e o que o informe e a distribuicao usam) - indice direto;
    - o do **fundo** (o envelope; e o que o Open Finance publica em cnpjNumber).
      Num fundo multi-classes o envelope tem CNPJ proprio que nao e o de
      nenhuma classe - sem este segundo indice, todo fundo anunciado pelo
      envelope saia "sem gestora e sem classificacao", que foi exatamente o
      sintoma observado na primeira rodada real.

    No indice por fundo, gestor e situacao valem sempre (sao do envelope);
    classificacao so quando as classes do fundo concordam - atribuir ao
    envelope a classe de uma unica filha seria inventar dado.

    O terceiro indice e por **codigo CVM**, para as fontes que identificam o
    produto sem publicar CNPJ. Ele cobre os tres niveis da RCVM 175, e em
    colisao vence o mais especifico (subclasse > classe > fundo): quem anuncia
    um codigo de subclasse esta apontando o produto comercial exato. A
    subclasse partilha CNPJ e carteira com a classe, entao ela resolve para o
    registro da classe-mae - que e onde moram gestora e classificacao.
    """

    def __init__(
        self,
        records: dict[str, MarketRecord],
        fund_records: dict[str, MarketRecord],
        cvm_codes: dict[str, tuple[MarketRecord, str]],
        source: Path,
        fund_classes: dict[str, list[str]] | None = None,
    ):
        self._records = records
        self._fund_records = fund_records
        self._cvm_codes = cvm_codes
        self.source = source
        # CNPJ do fundo (envelope) -> CNPJs das classes. E o que permite somar
        # a captacao das classes quando a oferta so publicou o envelope.
        self.fund_classes = fund_classes or {}

    def __len__(self) -> int:
        return len(self._records)

    def get(self, cnpj: str | None) -> MarketRecord | None:
        if not cnpj:
            return None
        return self._records.get(cnpj) or self._fund_records.get(cnpj)

    def get_by_cvm_code(self, code: object) -> tuple[MarketRecord, str] | None:
        """Registro e nivel do codigo (subclasse, classe ou fundo)."""
        normalized = normalize_cvm_code(code)
        if not normalized:
            return None
        return self._cvm_codes.get(normalized)

    def resolve(self, offer: FundOffer) -> tuple[MarketRecord, str] | None:
        """Acha o registro oficial da oferta e diz por qual chave casou.

        CNPJ primeiro (classe e depois envelope): e a chave mais confiavel e a
        que os feeds regulados publicam. O codigo CVM e a rede para quem nao
        publica CNPJ nenhum.
        """
        record = self.get(offer.cnpj)
        if record is not None:
            return record, "cnpj"
        hit = self.get_by_cvm_code(offer.cvm_code)
        if hit is not None:
            record, level = hit
            return record, f"codigo_{level}"
        return None

    def managers(self) -> list[str]:
        """Todos os nomes de gestor da base - materia-prima do indice de marcas."""
        names = {
            r.gestor
            for source in (self._records, self._fund_records)
            for r in source.values()
            if r.gestor
        }
        return sorted(names)

    @classmethod
    def load(cls, config: Config) -> "MarketLookup | None":
        """Carrega o consolidado; None quando a base nao foi importada."""
        path = config.market_dir / "mercado.csv"
        if not path.is_file():
            return None

        records: dict[str, MarketRecord] = {}
        by_fund: dict[str, list[dict]] = {}
        fund_classes: dict[str, list[str]] = {}
        # Materia-prima do indice por codigo, montado depois que os registros
        # de envelope existirem.
        by_classe_id: dict[str, MarketRecord] = {}
        class_codes: list[tuple[str, MarketRecord, str]] = []
        fund_codes: dict[str, str] = {}
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
                    exclusivo=row.get("exclusivo") or None,
                )
                fund_cnpj = normalize_cnpj(row.get("cnpj_fundo"))
                if fund_cnpj:
                    by_fund.setdefault(fund_cnpj, []).append(row)
                    fund_classes.setdefault(fund_cnpj, []).append(cnpj)

                # `codigo_cvm` e da classe nas linhas RCVM175 e do proprio
                # fundo nas legadas, que nao tem classe.
                classe_id = (row.get("id_classe") or "").strip()
                if classe_id:
                    by_classe_id.setdefault(classe_id, records[cnpj])
                code = normalize_cvm_code(row.get("codigo_cvm"))
                if code:
                    level = "classe" if classe_id else "fundo"
                    class_codes.append((code, records[cnpj], level))
                fund_code = normalize_cvm_code(row.get("codigo_cvm_fundo"))
                if fund_code and fund_cnpj:
                    fund_codes.setdefault(fund_cnpj, fund_code)

        fund_records: dict[str, MarketRecord] = {}
        for fund_cnpj, rows in by_fund.items():
            if fund_cnpj in records:
                continue  # envelope com CNPJ de classe ja resolve pelo direto
            fund_records[fund_cnpj] = _fund_level_record(fund_cnpj, rows)

        cvm_codes: dict[str, tuple[MarketRecord, str]] = {}
        # Ordem = precedencia: o primeiro a reivindicar o codigo fica.
        for code, record in _subclass_codes(config, by_classe_id):
            cvm_codes.setdefault(code, (record, "subclasse"))
        for code, record, level in class_codes:
            cvm_codes.setdefault(code, (record, level))
        for fund_cnpj, code in fund_codes.items():
            record = records.get(fund_cnpj) or fund_records.get(fund_cnpj)
            if record is not None:
                cvm_codes.setdefault(code, (record, "fundo"))

        return cls(records, fund_records, cvm_codes, path, fund_classes)


def _subclass_codes(
    config: Config, by_classe_id: dict[str, MarketRecord]
) -> list[tuple[str, MarketRecord]]:
    """Codigos de subclasse apontando para o registro da classe-mae.

    A subclasse nao tem CNPJ nem carteira propria - ela segrega publico-alvo e
    condicoes comerciais dentro da classe. Entao gestora e classificacao dela
    sao as da classe, e casar pelo codigo da subclasse e legitimo.

    Base antiga em disco pode nao ter `subclasses.csv`: sem ele o indice fica
    sem esse nivel e o resto do lookup segue funcionando.
    """
    path = config.market_dir / "subclasses.csv"
    if not path.is_file():
        return []

    codes: list[tuple[str, MarketRecord]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            code = normalize_cvm_code(row.get("codigo_cvm"))
            record = by_classe_id.get((row.get("id_classe") or "").strip())
            if code and record is not None:
                codes.append((code, record))
    return codes


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
        # "Exclusivo" so desce ao envelope se TODAS as classes o forem - um
        # fundo com classe exclusiva e classe aberta e distribuido.
        exclusivo=_unanimous([r.get("exclusivo") or "" for r in rows]),
    )


def enrich_offer(offer: FundOffer, record: MarketRecord) -> None:
    """Carimba um fundo com o dado oficial, preservando o que a casa disse.

    A classificacao da casa nao se perde: se divergir da oficial, vai para
    `classificacao_anbima_casa` - a divergencia em si e informacao.

    Na gestora o registro da CVM prevalece: casa a casa o mesmo fundo aparecia
    com o nome comercial, a razao social ou ate o gestor pessoa fisica, e
    contar "gestoras distintas" somava a mesma empresa varias vezes. O que a
    casa dizia fica em `gestor_casa` quando diverge. Uma politica de so
    preencher lacuna seria condicionar as duas atribuicoes abaixo a
    `offer.manager_name is None` / `offer.manager_cnpj is None`.
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
        # Grafia diferente da mesma razao social ("... LTDA" vs "... Ltda")
        # nao e divergencia - guardar isso so poluiria o relatorio.
        if offer.manager_name and normalize_text(offer.manager_name) != normalize_text(
            record.gestor
        ):
            offer.extra.setdefault(KEY_GESTOR_CASA, offer.manager_name)
        offer.manager_name = record.gestor
    if record.cnpj_gestor:
        offer.extra[KEY_CNPJ_GESTOR] = record.cnpj_gestor
        if offer.manager_cnpj and offer.manager_cnpj != record.cnpj_gestor:
            offer.extra.setdefault(KEY_CNPJ_GESTOR_CASA, offer.manager_cnpj)
        offer.manager_cnpj = record.cnpj_gestor
    if record.situacao:
        offer.extra[KEY_SITUACAO] = record.situacao
    if record.pl is not None:
        offer.extra.setdefault(KEY_PL, record.pl)
    if record.publico_alvo:
        offer.extra.setdefault(KEY_PUBLICO, record.publico_alvo)
    if record.exclusivo:
        offer.extra.setdefault(KEY_EXCLUSIVO, record.exclusivo)
    offer.extra[KEY_ORIGEM_BASE] = record.origem

    # A classe de ativo derivada ganha a versao oficial quando a heuristica
    # sobre o nome nao tinha concluido nada.
    if offer.asset_class == AssetClass.OUTROS:
        official = classify_asset_class(record.anbima, record.cvm)
        if official != AssetClass.OUTROS:
            offer.asset_class = official


def _combined_flow_summary(
    cnpjs: list[str], summaries: dict[str, FlowSummary]
) -> FlowSummary | None:
    """Soma os resumos de captacao das classes de um fundo (envelope).

    Somar janelas de classes irmas e legitimo: sao carteiras disjuntas do
    mesmo produto. Campos ausentes em todas as classes ficam ausentes; o
    as_of e o MINIMO das datas - a serie combinada so esta completa ate onde
    a classe mais atrasada informou.

    Retorno, volatilidade e cota NAO se combinam (cotas de classes diferentes
    nao se somam nem se ponderam sem inventar uma cota) e ficam None. A
    variacao de cotistas so sai quando TODAS as classes a tem: um None nao
    distingue "a classe nao existia" de "o informe faltou".
    """
    parts = [summaries[cnpj] for cnpj in cnpjs if cnpj in summaries]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]

    combined = FlowSummary(cnpj=parts[0].cnpj, window_end=parts[0].window_end)
    for name in ("net_1m", "net_3m", "net_12m", "current_partial", "net_worth"):
        values = [
            getattr(part, name) for part in parts if getattr(part, name) is not None
        ]
        setattr(combined, name, sum(values) if values else None)
    shareholders = [p.shareholders for p in parts if p.shareholders is not None]
    combined.shareholders = sum(shareholders) if shareholders else None
    combined.months_12m = max(part.months_12m for part in parts)
    dates = [part.as_of for part in parts if part.as_of is not None]
    combined.as_of = min(dates) if dates else None
    for name in (
        "shareholders_delta_1m",
        "shareholders_delta_3m",
        "shareholders_delta_12m",
        "shareholders_start_12m",
    ):
        values = [getattr(part, name) for part in parts]
        if all(value is not None for value in values):
            setattr(combined, name, sum(values))
    return combined


def _flow_summary_for(
    offer: FundOffer,
    record: MarketRecord,
    lookup: MarketLookup,
    summaries: dict[str, FlowSummary],
) -> FlowSummary | None:
    """Resumo de captacao de uma oferta ja casada com a base oficial.

    O informe reporta CNPJ de CLASSE, entao o caminho direto e o CNPJ do
    registro casado (que ja resolveu codigo CVM para a classe certa). Quando
    a oferta so publicou o envelope, a captacao do fundo e a soma das
    classes dele.
    """
    for cnpj in (offer.cnpj, record.cnpj):
        summary = summaries.get(cnpj or "")
        if summary is not None:
            return summary
    for cnpj in (offer.cnpj, record.cnpj):
        classes = lookup.fund_classes.get(cnpj or "")
        if classes:
            combined = _combined_flow_summary(classes, summaries)
            if combined is not None:
                return combined
    return None


def _stamp_flows(offer: FundOffer, summary: FlowSummary) -> bool:
    """Carimba a captacao no extra. False quando o resumo nao tinha nada."""
    stamped = False
    if summary.months_12m:
        offer.extra[KEY_CAPT_1M] = summary.net_1m
        offer.extra[KEY_CAPT_3M] = summary.net_3m
        offer.extra[KEY_CAPT_12M] = summary.net_12m
        offer.extra[KEY_CAPT_JANELA] = summary.months_12m
        stamped = True
    if summary.net_worth is not None:
        offer.extra[KEY_PL_INFORME] = summary.net_worth
        stamped = True
    if summary.shareholders is not None:
        offer.extra[KEY_COTISTAS] = summary.shareholders
    if summary.as_of is not None:
        offer.extra[KEY_CAPT_DATA] = summary.as_of.isoformat()

    # Rentabilidade e risco: so o que existe; a janela rotula o que ha.
    returns = (
        (KEY_RENT_1M, summary.return_1m),
        (KEY_RENT_3M, summary.return_3m),
        (KEY_RENT_12M, summary.return_12m),
    )
    if any(value is not None for _, value in returns):
        for key, value in returns:
            if value is not None:
                offer.extra[key] = value
        offer.extra[KEY_RENT_JANELA] = summary.quota_months
        stamped = True
    if summary.vol_12m is not None:
        offer.extra[KEY_VOL_12M] = summary.vol_12m
        offer.extra[KEY_VOL_DIAS] = summary.vol_obs

    # Cotistas e ticket.
    ticket = summary.ticket
    if ticket is not None:
        offer.extra[KEY_TICKET] = ticket
    for key, value in (
        (KEY_COTISTAS_VAR_1M, summary.shareholders_delta_1m),
        (KEY_COTISTAS_VAR_3M, summary.shareholders_delta_3m),
        (KEY_COTISTAS_VAR_12M, summary.shareholders_delta_12m),
    ):
        if value is not None:
            offer.extra[key] = value
    if summary.shareholders_delta_12m is not None and summary.shareholders_start_12m:
        offer.extra[KEY_COTISTAS_VAR_PCT_12M] = (
            summary.shareholders_delta_12m / summary.shareholders_start_12m
        )
    return stamped


def has_return(summary: FlowSummary) -> bool:
    return any(
        value is not None
        for value in (summary.return_1m, summary.return_3m, summary.return_12m)
    )


# Tokens juridicos/genericos que nao carregam a marca de uma gestora.
_LEGAL_TOKENS = {
    "LTDA", "SA", "S", "A", "DTVM", "CTVM", "CCTVM", "ASSET", "MANAGEMENT",
    "GESTAO", "GESTORA", "DE", "DO", "DA", "E", "RECURSOS", "INVESTIMENTOS",
    "INVESTIMENTO", "ADMINISTRACAO", "ADM", "ADMINISTRADORA", "VALORES",
    "MOBILIARIOS", "TITULOS", "DISTRIBUIDORA", "WEALTH", "PORTFOLIOS",
}


def _brand_tokens(manager: str) -> list[str]:
    return [
        t for t in normalize_text(manager).split() if t not in _LEGAL_TOKENS
    ]


def build_brand_index(managers: list[str]) -> dict[str, str]:
    """Mapa marca -> nome canonico de gestora, so onde a marca e inequivoca.

    Chaves: os dois primeiros tokens da marca e o primeiro sozinho. Uma chave
    que aponta para duas gestoras DIFERENTES (assinaturas de marca distintas)
    e descartada - "ITAU" some porque ha Itau Asset, Itau DTVM e Intrag, mas
    "KINEA" e "BAHIA AM" ficam. Melhor nao preencher do que preencher errado.
    """
    candidates: dict[str, dict[str, str]] = {}
    for manager in managers:
        tokens = _brand_tokens(manager)
        if not tokens:
            continue
        signature = " ".join(tokens)
        keys = {tokens[0]} if len(tokens[0]) >= 2 else set()
        if len(tokens) >= 2:
            keys.add(" ".join(tokens[:2]))
        for key in keys:
            bucket = candidates.setdefault(key, {})
            # Mesma assinatura escrita de jeitos diferentes ("... LTDA" vs
            # sem) nao e ambiguidade; fica o nome mais curto.
            if signature not in bucket or len(manager) < len(bucket[signature]):
                bucket[signature] = manager

    return {
        key: next(iter(bucket.values()))
        for key, bucket in candidates.items()
        if len(bucket) == 1
    }


def infer_manager(fund: FundOffer, brand_index: dict[str, str]) -> bool:
    """Preenche a gestora pelo prefixo do nome do fundo, quando inequivoco.

    "Bahia AM Muta Selecao" comeca com a marca da gestora - convencao quase
    universal em prateleira de terceiros. Tenta o prefixo de dois tokens antes
    do de um. Marca a proveniencia: dado inferido nunca se passa por oficial.
    """
    if fund.manager_name:
        return False
    tokens = normalize_text(fund.name).split()
    if not tokens:
        return False

    for key in (" ".join(tokens[:2]), tokens[0]):
        manager = brand_index.get(key)
        if manager:
            fund.manager_name = manager
            fund.extra["gestor_inferido"] = f"prefixo do nome ({key.title()})"
            return True
    return False


def enrich_snapshots(
    config: Config, snapshots: list[ShelfSnapshot]
) -> EnrichmentStats | None:
    """Enriquece todos os fundos in-place. None quando nao ha base importada."""
    lookup = MarketLookup.load(config)
    if lookup is None:
        return None

    # A base de captacao e opcional: sem ela o join do cadastro segue normal
    # e as chaves de captacao simplesmente nao aparecem no extra.
    flow_summaries = load_summaries(config) or {}

    stats = EnrichmentStats()
    for snapshot in snapshots:
        per_platform = stats.by_platform.setdefault(
            snapshot.platform, {"total": 0, "matched": 0, "flows": 0, "returns": 0}
        )
        for offer in snapshot.funds:
            stats.total += 1
            per_platform["total"] += 1
            if offer.cnpj:
                stats.with_cnpj += 1
            if offer.cvm_code:
                stats.with_cvm_code += 1
            resolved = lookup.resolve(offer)
            if resolved is None:
                continue
            record, matched_by = resolved
            stats.matched += 1
            per_platform["matched"] += 1
            if matched_by != "cnpj":
                stats.matched_by_cvm_code += 1
            had_anbima = bool(offer.extra.get(KEY_ANBIMA))
            enrich_offer(offer, record)
            offer.extra[KEY_MATCH] = matched_by
            if record.gestor:
                stats.managers_from_cvm += 1
            if not had_anbima and offer.extra.get(KEY_ANBIMA):
                stats.anbima_filled += 1

            if flow_summaries:
                summary = _flow_summary_for(offer, record, lookup, flow_summaries)
                if summary is not None and _stamp_flows(offer, summary):
                    stats.flows_matched += 1
                    per_platform["flows"] += 1
                    if has_return(summary):
                        stats.returns_matched += 1
                        per_platform["returns"] += 1

    # Segunda passada: fundos que sobraram sem gestora (tabela do Itau sem
    # CNPJ, por exemplo) tentam a marca no prefixo do nome. Depois do join,
    # para o indice de marcas incluir as gestoras oficiais recem-carimbadas.
    known_managers = set(lookup.managers())
    for snapshot in snapshots:
        for offer in snapshot.funds:
            if offer.manager_name:
                known_managers.add(offer.manager_name)
    brand_index = build_brand_index(sorted(known_managers))
    for snapshot in snapshots:
        for offer in snapshot.funds:
            if infer_manager(offer, brand_index):
                stats.managers_inferred += 1
    return stats
