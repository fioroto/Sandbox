"""Monitoramento de captacao liquida dos fundos das vitrines dos players.

Responde a terceira pergunta que faltava ao monitor: "o que cada casa oferta"
(shelf_monitor/README.md) e "o que esta prestes a lancar" (radar CVM) ja
existiam; esta e "para onde o dinheiro esta indo nos fundos que cada vitrine
oferece". A fonte e o informe diario da CVM (INF_DIARIO), lido por
`market/inf_diario.py` - o mesmo parser que ja alimenta o fluxo de ETF.

Ressalva estrutural, valida em toda superficie que consome este modulo: o
INF_DIARIO reporta a captacao do FUNDO, somando todos os distribuidores. Este
monitor mede "captacao dos fundos que a vitrine de cada casa oferece", nao
"captacao pela plataforma" - um fundo presente em tres vitrines mostra o mesmo
numero nas tres. Ver `NOTA_FUNDO_NIVEL`.

O universo de analise e a vitrine de varejo (`FundOffer.on_listing` e
`profile.split_by_profile`), na mesma regra que `report/aggregate.py` usa para
gaps e listagem - deliberadamente reimplementada aqui, e nao importada de
`report.aggregate`, para este modulo poder ser importado por `aggregate.py`
sem criar um ciclo.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.enrich import KEY_ANBIMA, KEY_CVM, MarketLookup
from shelf_monitor.market.inf_diario import (
    DailyRecord,
    dedupe_by_day,
    fetch_inf_diario_month,
    get_recent_months,
    months_for_ytd,
    read_daily_records,
)
from shelf_monitor.models import ASSET_CLASS_LABELS, FundOffer, ShelfSnapshot, SnapshotStatus
from shelf_monitor.normalize import format_cnpj
from shelf_monitor.profile import split_by_profile

NOTA_FUNDO_NIVEL = (
    "Captacao no nivel do fundo (INF_DIARIO da CVM, todos os distribuidores "
    "somados): e a captacao dos fundos que a vitrine oferece, NAO a captacao "
    "pela plataforma. Um fundo presente em 3 vitrines mostra o mesmo numero "
    "nas 3 - o numero muda por fundo, nunca por quem o distribui."
)

# Rotulo do universo de analise - vai no rodape de toda superficie.
_ROTULOS_UNIVERSO = {
    "vitrine": "vitrine",
    "catalogo": "catalogo (nao vitrine)",
}

# Quantas semanas ISO completas olhar para momentum/sangria.
_JANELAS_MOMENTUM = 4


# --- estruturas de dado -------------------------------------------------------


@dataclass
class JanelaCaptacao:
    """Um recorte de tempo (semana/mes/ano) sobre o qual as metricas somam."""

    chave: str  # "semana" | "mes" | "mes_anterior" | "ano"
    rotulo: str
    inicio: date
    fim: date
    completa: bool
    # Quando o download foi limitado (--months) e o inicio pedido nao tem
    # dado, este e o primeiro dia realmente coberto - a janela "ano" vira
    # "YTD parcial desde <data>" em vez de fingir cobrir o ano inteiro.
    dados_desde: date | None = None

    def to_dict(self) -> dict:
        return {
            "chave": self.chave,
            "rotulo": self.rotulo,
            "inicio": self.inicio.isoformat(),
            "fim": self.fim.isoformat(),
            "completa": self.completa,
            "dados_desde": self.dados_desde.isoformat() if self.dados_desde else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JanelaCaptacao":
        return cls(
            chave=data["chave"],
            rotulo=data["rotulo"],
            inicio=date.fromisoformat(data["inicio"]),
            fim=date.fromisoformat(data["fim"]),
            completa=data["completa"],
            dados_desde=date.fromisoformat(data["dados_desde"]) if data.get("dados_desde") else None,
        )


@dataclass
class MetricasJanela:
    """Captacao de um fundo numa janela especifica."""

    captacao_bruta: float = 0.0
    resgates: float = 0.0
    captacao_liquida: float = 0.0
    pl_inicio: float | None = None
    pl_fim: float | None = None
    captacao_liquida_pct_pl: float | None = None
    pl_inicio_aproximado: bool = False
    dias_com_dado: int = 0

    def to_dict(self) -> dict:
        return {
            "captacao_bruta": self.captacao_bruta,
            "resgates": self.resgates,
            "captacao_liquida": self.captacao_liquida,
            "pl_inicio": self.pl_inicio,
            "pl_fim": self.pl_fim,
            "captacao_liquida_pct_pl": self.captacao_liquida_pct_pl,
            "pl_inicio_aproximado": self.pl_inicio_aproximado,
            "dias_com_dado": self.dias_com_dado,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MetricasJanela":
        return cls(**data)


@dataclass
class FluxoFundo:
    """Captacao consolidada de um fundo, atraves de todas as vitrines onde aparece."""

    # CNPJ normalizado (14 digitos, sem mascara) usado como chave de
    # consolidacao - classe quando a oferta anuncia uma classe, envelope
    # quando `agregado_por_fundo` e True. Formatado so na hora de exibir
    # (`format_cnpj`, ver `_fundo_dict`).
    cnpj: str
    cnpjs_classe: list[str]
    agregado_por_fundo: bool
    nome: str
    gestor: str | None
    classificacao: str
    nivel_classificacao: str  # "anbima" | "cvm" | "heuristica"
    plataformas: list[str] = field(default_factory=list)
    janelas: dict[str, MetricasJanela] = field(default_factory=dict)
    # week_key ("2026-W36") -> captacao liquida da semana, para as ultimas
    # _JANELAS_MOMENTUM semanas ISO completas - insumo de momentum/sangria.
    semanas_recentes: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "cnpj": self.cnpj,
            "cnpjs_classe": self.cnpjs_classe,
            "agregado_por_fundo": self.agregado_por_fundo,
            "nome": self.nome,
            "gestor": self.gestor,
            "classificacao": self.classificacao,
            "nivel_classificacao": self.nivel_classificacao,
            "plataformas": self.plataformas,
            "janelas": {k: v.to_dict() for k, v in self.janelas.items()},
            "semanas_recentes": self.semanas_recentes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FluxoFundo":
        return cls(
            cnpj=data["cnpj"],
            cnpjs_classe=data["cnpjs_classe"],
            agregado_por_fundo=data["agregado_por_fundo"],
            nome=data["nome"],
            gestor=data.get("gestor"),
            classificacao=data["classificacao"],
            nivel_classificacao=data["nivel_classificacao"],
            plataformas=data.get("plataformas") or [],
            janelas={k: MetricasJanela.from_dict(v) for k, v in (data.get("janelas") or {}).items()},
            semanas_recentes=data.get("semanas_recentes") or {},
        )


@dataclass
class CoberturaCaptacao:
    """O que o INF_DIARIO realmente cobriu do universo analisado."""

    total_fundos: int = 0
    total_com_fluxo: int = 0
    por_plataforma: dict[str, dict] = field(default_factory=dict)
    meses_lidos: list[str] = field(default_factory=list)
    meses_faltantes: list[str] = field(default_factory=list)
    ultima_data: date | None = None
    base_mercado: bool = False

    def to_dict(self) -> dict:
        return {
            "total_fundos": self.total_fundos,
            "total_com_fluxo": self.total_com_fluxo,
            "por_plataforma": self.por_plataforma,
            "meses_lidos": self.meses_lidos,
            "meses_faltantes": self.meses_faltantes,
            "ultima_data": self.ultima_data.isoformat() if self.ultima_data else None,
            "base_mercado": self.base_mercado,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CoberturaCaptacao":
        return cls(
            total_fundos=data.get("total_fundos", 0),
            total_com_fluxo=data.get("total_com_fluxo", 0),
            por_plataforma=data.get("por_plataforma") or {},
            meses_lidos=data.get("meses_lidos") or [],
            meses_faltantes=data.get("meses_faltantes") or [],
            ultima_data=date.fromisoformat(data["ultima_data"]) if data.get("ultima_data") else None,
            base_mercado=data.get("base_mercado", False),
        )


@dataclass
class ResultadoCaptacao:
    """Saida completa do pipeline - o que e persistido e o que as superficies leem."""

    gerado_em: datetime
    universo: str  # "vitrine" | "catalogo"
    rotulo_universo: str
    janelas: list[JanelaCaptacao] = field(default_factory=list)
    fundos: list[FluxoFundo] = field(default_factory=list)
    cobertura: CoberturaCaptacao = field(default_factory=CoberturaCaptacao)
    excluded: list[dict] = field(default_factory=list)
    plataformas: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "gerado_em": self.gerado_em.isoformat(),
            "universo": self.universo,
            "rotulo_universo": self.rotulo_universo,
            "janelas": [j.to_dict() for j in self.janelas],
            "fundos": [f.to_dict() for f in self.fundos],
            "cobertura": self.cobertura.to_dict(),
            "excluded": self.excluded,
            "plataformas": self.plataformas,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResultadoCaptacao":
        return cls(
            gerado_em=datetime.fromisoformat(data["gerado_em"]),
            universo=data["universo"],
            rotulo_universo=data["rotulo_universo"],
            janelas=[JanelaCaptacao.from_dict(j) for j in data.get("janelas") or []],
            fundos=[FluxoFundo.from_dict(f) for f in data.get("fundos") or []],
            cobertura=CoberturaCaptacao.from_dict(data.get("cobertura") or {}),
            excluded=data.get("excluded") or [],
            plataformas=data.get("plataformas") or [],
        )


# --- universo -------------------------------------------------------------


def selecionar_universo(snapshot: ShelfSnapshot, universo: str) -> tuple[list[FundOffer], dict | None]:
    """(ofertas, motivo_exclusao) - motivo e None quando a casa entra no universo.

    Replica a regra de pertinencia de `report.aggregate.build_gap_block`
    (coleta indisponivel / sem vitrine publica) sem importar `aggregate`, para
    este modulo poder ser importado por ele.
    """
    label = PLATFORMS[snapshot.platform].label if snapshot.platform in PLATFORMS else snapshot.platform

    if snapshot.status == SnapshotStatus.UNAVAILABLE or not snapshot.funds:
        return [], {
            "platform_id": snapshot.platform,
            "label": label,
            "reason": "coleta indisponivel nesta execucao",
        }

    retail, _ = split_by_profile(snapshot.funds)

    if universo == "catalogo":
        if not retail:
            return [], {
                "platform_id": snapshot.platform,
                "label": label,
                "reason": "nenhum fundo de varejo nesta coleta",
            }
        return retail, None

    listing = [f for f in retail if f.on_listing]
    if not listing:
        return [], {
            "platform_id": snapshot.platform,
            "label": label,
            "reason": (
                "sem vitrine publica coletada nesta execucao "
                f"(estrategia: {snapshot.strategy.value}); o catalogo Open "
                "Finance nao conta como vitrine"
            ),
        }
    return listing, None


# --- resolucao de CNPJ ------------------------------------------------------


def resolver_cnpjs(offer: FundOffer, lookup: MarketLookup | None) -> tuple[list[str], bool]:
    """(cnpjs_de_classe, agregado_por_fundo) para somar o INF_DIARIO desta oferta.

    O informe diario reporta a CLASSE (RCVM175) ou o proprio fundo (legado).
    Uma oferta pode anunciar o CNPJ do envelope (o que o Open Finance publica
    em cnpjNumber) - sem expandir para as classes, a soma sairia zerada porque
    o envelope nunca recebe informe diario proprio.
    """
    cnpj = offer.cnpj
    if not cnpj:
        if lookup is not None:
            hit = lookup.resolve(offer)
            if hit is not None:
                record, _level = hit
                return [record.cnpj], False
        return [], False

    if lookup is None:
        return [cnpj], False

    if lookup.is_class(cnpj):
        return [cnpj], False

    classes = lookup.classes_of_fund(cnpj)
    if classes:
        return sorted(classes), True

    # CNPJ desconhecido da base (fundo legado fora do registro importado, ou
    # base ausente/desatualizada): usa o proprio, na esperanca de que seja um
    # fundo legado (que reporta com o proprio CNPJ).
    return [cnpj], False


def classificar(offers: list[FundOffer]) -> tuple[str, str]:
    """(classificacao, nivel) - o melhor rotulo disponivel entre os exemplares.

    Mesma cadeia de `report.aggregate.anbima_category`: ANBIMA oficial (join
    com a CVM) antes de CVM antes da heuristica de nome/categoria.
    """
    for offer in offers:
        anbima = offer.extra.get(KEY_ANBIMA)
        if anbima:
            return anbima, "anbima"
    for offer in offers:
        cvm = offer.extra.get(KEY_CVM)
        if cvm:
            return cvm, "cvm"
    for offer in offers:
        if offer.asset_class.value != "outros":
            return ASSET_CLASS_LABELS[offer.asset_class], "heuristica"
    return "Sem classificacao", "heuristica"


# --- janelas de tempo --------------------------------------------------------


def _last_complete_iso_week(ultima_data: date) -> tuple[date, date]:
    """(inicio, fim) da ultima semana ISO (seg-dom) que termina em ou antes de `ultima_data`."""
    last_sunday = ultima_data - timedelta(days=ultima_data.isoweekday() % 7)
    return last_sunday - timedelta(days=6), last_sunday


def calcular_janelas(ultima_data: date, primeira_data: date | None) -> list[JanelaCaptacao]:
    """As quatro janelas do monitor, ancoradas em `ultima_data` (o dia mais
    recente com dado no INF_DIARIO lido).

    `primeira_data` e o primeiro dia realmente coberto pelos meses baixados;
    quando uma janela comeca antes dele (tipicamente o YTD com `--months`
    curto), `dados_desde` registra o corte real.
    """
    def _clip(inicio: date) -> date | None:
        if primeira_data is not None and inicio < primeira_data:
            return primeira_data
        return None

    semana_inicio, semana_fim = _last_complete_iso_week(ultima_data)
    semana_completa = semana_fim <= ultima_data

    mes_inicio = ultima_data.replace(day=1)

    if mes_inicio.month == 1:
        mes_anterior_inicio = date(mes_inicio.year - 1, 12, 1)
        mes_anterior_fim = mes_inicio - timedelta(days=1)
    else:
        mes_anterior_fim = mes_inicio - timedelta(days=1)
        mes_anterior_inicio = mes_anterior_fim.replace(day=1)

    ano_inicio = date(ultima_data.year, 1, 1)

    janelas = [
        JanelaCaptacao(
            chave="semana",
            rotulo=f"Semana {semana_inicio.isoformat()} a {semana_fim.isoformat()}",
            inicio=semana_inicio,
            fim=min(semana_fim, ultima_data),
            completa=semana_completa,
            dados_desde=_clip(semana_inicio),
        ),
        JanelaCaptacao(
            chave="mes",
            rotulo=f"Mes corrente ({mes_inicio.strftime('%m/%Y')}, ate {ultima_data.isoformat()})",
            inicio=mes_inicio,
            fim=ultima_data,
            completa=False,
            dados_desde=_clip(mes_inicio),
        ),
        JanelaCaptacao(
            chave="mes_anterior",
            rotulo=f"Mes anterior ({mes_anterior_inicio.strftime('%m/%Y')})",
            inicio=mes_anterior_inicio,
            fim=mes_anterior_fim,
            completa=True,
            dados_desde=_clip(mes_anterior_inicio),
        ),
        JanelaCaptacao(
            chave="ano",
            rotulo=f"YTD {ultima_data.year} (ate {ultima_data.isoformat()})",
            inicio=ano_inicio,
            fim=ultima_data,
            completa=False,
            dados_desde=_clip(ano_inicio),
        ),
    ]
    return janelas


def _agregar(
    recs_por_cnpj: dict[str, list[DailyRecord]],
    cnpjs: list[str],
    janela: JanelaCaptacao,
) -> MetricasJanela:
    """Soma captacao/resgate das classes de `cnpjs` dentro de [inicio, fim].

    `pl_inicio` e o ultimo PL conhecido de cada classe ANTES do inicio da
    janela (somado entre classes); quando nenhuma classe tem dado anterior, a
    janela comeca no primeiro dia com dado e o PL de abertura e aproximado por
    `pl_no_primeiro_dia - captacao_liquida_do_primeiro_dia` (marcado
    `pl_inicio_aproximado`).
    """
    bruta = resgates = 0.0
    pl_inicio_total = 0.0
    pl_fim_total = 0.0
    pl_inicio_conhecido = False
    aproximado = False
    dias: set[date] = set()

    for cnpj in cnpjs:
        recs = sorted(recs_por_cnpj.get(cnpj, ()), key=lambda r: r.dt_comptc)
        if not recs:
            continue

        anteriores = [r for r in recs if r.dt_comptc < janela.inicio]
        na_janela = [r for r in recs if janela.inicio <= r.dt_comptc <= janela.fim]

        if anteriores:
            pl_inicio_total += anteriores[-1].vl_patrim_liq
            pl_inicio_conhecido = True
        elif na_janela:
            primeiro = na_janela[0]
            pl_inicio_total += primeiro.vl_patrim_liq - primeiro.net_inflow
            aproximado = True

        if na_janela:
            pl_fim_total += na_janela[-1].vl_patrim_liq
            for r in na_janela:
                bruta += r.captc_dia
                resgates += r.resg_dia
                dias.add(r.dt_comptc)

    liquida = bruta - resgates
    pct = None
    if pl_inicio_conhecido or aproximado:
        if pl_inicio_total and pl_inicio_total > 0:
            pct = round((liquida / pl_inicio_total) * 100.0, 4)

    return MetricasJanela(
        captacao_bruta=round(bruta, 2),
        resgates=round(resgates, 2),
        captacao_liquida=round(liquida, 2),
        pl_inicio=round(pl_inicio_total, 2) if (pl_inicio_conhecido or aproximado) else None,
        pl_fim=round(pl_fim_total, 2) if dias else None,
        captacao_liquida_pct_pl=pct,
        pl_inicio_aproximado=aproximado,
        dias_com_dado=len(dias),
    )


def _semanas_recentes(
    recs_por_cnpj: dict[str, list[DailyRecord]], cnpjs: list[str], ultima_data: date
) -> dict[str, float]:
    """captacao liquida por semana ISO completa, as ultimas `_JANELAS_MOMENTUM`."""
    por_semana: dict[tuple[int, int], float] = defaultdict(float)
    for cnpj in cnpjs:
        for rec in recs_por_cnpj.get(cnpj, ()):
            iso_year, iso_week, _ = rec.dt_comptc.isocalendar()
            por_semana[(iso_year, iso_week)] += rec.net_inflow

    _, semana_fim_atual = _last_complete_iso_week(ultima_data)
    chaves_completas = sorted(
        k for k in por_semana if date.fromisocalendar(k[0], k[1], 7) <= semana_fim_atual
    )
    recentes = chaves_completas[-_JANELAS_MOMENTUM:]
    return {f"{y}-W{w:02d}": round(por_semana[(y, w)], 2) for y, w in recentes}


# --- pipeline principal -------------------------------------------------------


def run_captacao_pipeline(
    config: Config,
    snapshots: list[ShelfSnapshot],
    *,
    universo: str = "vitrine",
    months: int | None = None,
    force: bool = False,
    ref_date: date | None = None,
    download: bool = True,
    lookup: MarketLookup | None = None,
) -> ResultadoCaptacao:
    """Executa o pipeline completo: universo -> resolucao de CNPJ -> download
    (ou leitura do cache) -> agregacao por janela -> ResultadoCaptacao.

    `download=False` so aceita zips ja presentes em `config.market_raw_dir` -
    o mes que faltar entra em `cobertura.meses_faltantes` em vez de disparar
    uma requisicao de rede.
    """
    ref = ref_date or date.today()
    gerado_em = datetime.now()
    rotulo_universo = _ROTULOS_UNIVERSO.get(universo, universo)

    if lookup is None:
        lookup = MarketLookup.load(config)

    # --- universo: quem entra, quem sai e por que -----------------------
    excluded: list[dict] = []
    ofertas_por_cnpj: dict[str, list[FundOffer]] = defaultdict(list)
    plataformas_ativas: list[str] = []

    for snapshot in snapshots:
        ofertas, motivo = selecionar_universo(snapshot, universo)
        if motivo is not None:
            excluded.append(motivo)
            continue
        plataformas_ativas.append(snapshot.platform)
        for offer in ofertas:
            cnpjs, agregado = resolver_cnpjs(offer, lookup)
            if not cnpjs:
                continue
            chave = cnpjs[0] if not agregado else (offer.cnpj or cnpjs[0])
            ofertas_por_cnpj[chave].append(offer)

    if not plataformas_ativas:
        return ResultadoCaptacao(
            gerado_em=gerado_em,
            universo=universo,
            rotulo_universo=rotulo_universo,
            excluded=excluded,
            plataformas=[],
        )

    # --- alvo de CNPJs de classe, materia-prima do download filtrado ----
    resolucao: dict[str, tuple[list[str], bool]] = {}
    alvo_classes: set[str] = set()
    for chave, offers in ofertas_por_cnpj.items():
        cnpjs, agregado = resolver_cnpjs(offers[0], lookup)
        resolucao[chave] = (cnpjs, agregado)
        alvo_classes.update(cnpjs)

    # --- meses a ler: YTD por padrao, --months corta -----------------
    if months is not None:
        meses = get_recent_months(count=months, ref_date=ref)
    else:
        meses = months_for_ytd(ref)

    meses_lidos: list[str] = []
    meses_faltantes: list[str] = []
    all_records: list[DailyRecord] = []
    for yyyymm in meses:
        if download:
            zip_path = fetch_inf_diario_month(config, yyyymm, force=force)
        else:
            candidate = config.market_raw_dir / f"inf_diario_fi_{yyyymm}.zip"
            zip_path = candidate if candidate.exists() else None
        if zip_path is None:
            meses_faltantes.append(yyyymm)
            continue
        meses_lidos.append(yyyymm)
        all_records.extend(read_daily_records(zip_path, alvo_classes))

    deduped = dedupe_by_day(all_records)
    recs_por_cnpj: dict[str, list[DailyRecord]] = defaultdict(list)
    for rec in deduped:
        recs_por_cnpj[rec.cnpj].append(rec)

    ultima_data = max((r.dt_comptc for r in deduped), default=None)
    primeira_data = min((r.dt_comptc for r in deduped), default=None)

    # Sem UM registro sequer no INF_DIARIO para todo o universo (base de
    # mercado ausente e tudo anunciado por envelope, por exemplo), nao ha data
    # real para ancorar as janelas - usa a data de referencia da run para
    # ainda assim listar os fundos com fluxo zerado, em vez de devolver uma
    # lista vazia que esconderia "cobri 0 de N fundos".
    ancora = ultima_data or ref
    janelas = calcular_janelas(ancora, primeira_data)

    fundos: list[FluxoFundo] = []
    total_com_fluxo = 0

    for chave, offers in ofertas_por_cnpj.items():
        cnpjs, agregado = resolucao[chave]
        tem_fluxo = any(recs_por_cnpj.get(c) for c in cnpjs)
        if tem_fluxo:
            total_com_fluxo += 1

        classificacao, nivel = classificar(offers)
        nome = offers[0].name
        gestor = next((o.manager_name for o in offers if o.manager_name), None)
        plataformas = sorted({o.platform for o in offers})

        metricas = {j.chave: _agregar(recs_por_cnpj, cnpjs, j) for j in janelas}
        semanas = _semanas_recentes(recs_por_cnpj, cnpjs, ancora)

        fundos.append(
            FluxoFundo(
                cnpj=chave,
                cnpjs_classe=cnpjs,
                agregado_por_fundo=agregado,
                nome=nome,
                gestor=gestor,
                classificacao=classificacao,
                nivel_classificacao=nivel,
                plataformas=plataformas,
                janelas=metricas,
                semanas_recentes=semanas,
            )
        )

    por_plataforma: dict[str, dict] = {}
    for snapshot in snapshots:
        if snapshot.platform not in plataformas_ativas:
            continue
        ofertas, _ = selecionar_universo(snapshot, universo)
        com_cnpj = sum(1 for o in ofertas if o.cnpj)
        com_fluxo = sum(
            1
            for f in fundos
            if snapshot.platform in f.plataformas and any(recs_por_cnpj.get(c) for c in f.cnpjs_classe)
        )
        por_plataforma[snapshot.platform] = {
            "label": PLATFORMS[snapshot.platform].label if snapshot.platform in PLATFORMS else snapshot.platform,
            "vitrine_total": len(ofertas),
            "com_cnpj": com_cnpj,
            "com_fluxo": com_fluxo,
        }

    cobertura = CoberturaCaptacao(
        total_fundos=len(fundos),
        total_com_fluxo=total_com_fluxo,
        por_plataforma=por_plataforma,
        meses_lidos=meses_lidos,
        meses_faltantes=meses_faltantes,
        ultima_data=ultima_data,
        base_mercado=lookup is not None,
    )

    return ResultadoCaptacao(
        gerado_em=gerado_em,
        universo=universo,
        rotulo_universo=rotulo_universo,
        janelas=janelas,
        fundos=fundos,
        cobertura=cobertura,
        excluded=excluded,
        plataformas=plataformas_ativas,
    )


# --- rankings e analise cruzada ----------------------------------------------


def _fundo_dict(f: FluxoFundo, chave: str) -> dict | None:
    metricas = f.janelas.get(chave)
    if metricas is None:
        return None
    return {
        "cnpj": f.cnpj,
        "cnpj_display": format_cnpj(f.cnpj),
        "nome": f.nome,
        "gestor": f.gestor,
        "classificacao": f.classificacao,
        "nivel_classificacao": f.nivel_classificacao,
        "plataformas": f.plataformas,
        "n_vitrines": len(f.plataformas),
        "agregado_por_fundo": f.agregado_por_fundo,
        **metricas.to_dict(),
    }


def ranking_por_plataforma(res: ResultadoCaptacao, chave: str, limite: int = 10) -> dict[str, dict]:
    """{platform_id: {"top": [...], "bottom": [...]}} - bottom so lista fundos
    com captacao liquida negativa (sangria), nunca preenche com fundos neutros."""
    por_plataforma: dict[str, list[FluxoFundo]] = defaultdict(list)
    for fundo in res.fundos:
        if fundo.janelas.get(chave) is None:
            continue
        for platform in fundo.plataformas:
            por_plataforma[platform].append(fundo)

    resultado: dict[str, dict] = {}
    for platform, fundos in por_plataforma.items():
        ordenados = sorted(fundos, key=lambda f: f.janelas[chave].captacao_liquida, reverse=True)
        negativos = [f for f in ordenados if f.janelas[chave].captacao_liquida < 0]
        resultado[platform] = {
            "top": [_fundo_dict(f, chave) for f in ordenados[:limite]],
            "bottom": [_fundo_dict(f, chave) for f in negativos[-limite:][::-1]],
        }
    return resultado


def ranking_consolidado(res: ResultadoCaptacao, chave: str, limite: int = 10) -> dict:
    """Top/bottom sobre todas as vitrines, sem repetir o mesmo dinheiro.

    Quando dois `FluxoFundo` compartilham uma classe (a mesma classe anunciada
    por CNPJ de classe numa casa e por envelope noutra em outra oferta), so o
    de mais vitrines entra no ranking - o outro e descartado aqui (a lista
    completa, sem dedupe, continua em `res.fundos` e nas abas por plataforma).
    """
    candidatos = [f for f in res.fundos if f.janelas.get(chave) is not None]
    candidatos.sort(key=lambda f: len(f.plataformas), reverse=True)

    vistos: set[str] = set()
    unicos: list[FluxoFundo] = []
    for fundo in candidatos:
        classes = set(fundo.cnpjs_classe)
        if classes & vistos:
            continue
        vistos.update(classes)
        unicos.append(fundo)

    ordenados = sorted(unicos, key=lambda f: f.janelas[chave].captacao_liquida, reverse=True)
    negativos = [f for f in ordenados if f.janelas[chave].captacao_liquida < 0]
    return {
        "top": [_fundo_dict(f, chave) for f in ordenados[:limite]],
        "bottom": [_fundo_dict(f, chave) for f in negativos[-limite:][::-1]],
    }


def consolidar_por_classificacao(res: ResultadoCaptacao, chave: str) -> list[dict]:
    """Plataforma x classificacao: bruta/resgates/liquida/qtd_fundos/qtd_com_fluxo.

    Inclui uma linha "Todas as vitrines" por classificacao, com dedupe de
    classe identico ao de `ranking_consolidado`.
    """
    linhas: dict[tuple[str, str], dict] = {}

    def _bump(platform_key: str, label: str, classificacao: str, metricas: MetricasJanela, tem_fluxo: bool) -> None:
        key = (platform_key, classificacao)
        row = linhas.setdefault(
            key,
            {
                "platform_id": platform_key,
                "label": label,
                "classificacao": classificacao,
                "captacao_bruta": 0.0,
                "resgates": 0.0,
                "captacao_liquida": 0.0,
                "qtd_fundos": 0,
                "qtd_com_fluxo": 0,
            },
        )
        row["captacao_bruta"] += metricas.captacao_bruta
        row["resgates"] += metricas.resgates
        row["captacao_liquida"] += metricas.captacao_liquida
        row["qtd_fundos"] += 1
        if tem_fluxo:
            row["qtd_com_fluxo"] += 1

    for fundo in res.fundos:
        metricas = fundo.janelas.get(chave)
        if metricas is None:
            continue
        tem_fluxo = metricas.dias_com_dado > 0
        for platform in fundo.plataformas:
            label = PLATFORMS[platform].label if platform in PLATFORMS else platform
            _bump(platform, label, fundo.classificacao, metricas, tem_fluxo)

    vistos: set[str] = set()
    for fundo in sorted(res.fundos, key=lambda f: len(f.plataformas), reverse=True):
        metricas = fundo.janelas.get(chave)
        if metricas is None:
            continue
        classes = set(fundo.cnpjs_classe)
        if classes & vistos:
            continue
        vistos.update(classes)
        _bump("__todas__", "Todas as vitrines", fundo.classificacao, metricas, metricas.dias_com_dado > 0)

    for row in linhas.values():
        row["captacao_bruta"] = round(row["captacao_bruta"], 2)
        row["resgates"] = round(row["resgates"], 2)
        row["captacao_liquida"] = round(row["captacao_liquida"], 2)

    return sorted(linhas.values(), key=lambda r: (r["platform_id"] != "__todas__", r["platform_id"], -r["captacao_liquida"]))


def analise_cruzada(res: ResultadoCaptacao, chave: str = "semana", baseline_id: str | None = None, limite: int = 10) -> dict:
    """Leituras cruzadas entre players: compartilhados, exclusivos, concentracao,
    momentum/sangria e o hook XP-centrico de "captador fora da baseline"."""
    if baseline_id is None:
        baseline_id = next((p.platform_id for p in PLATFORMS.values() if p.is_baseline), None)

    com_janela = [f for f in res.fundos if f.janelas.get(chave) is not None]
    positivos = [f for f in com_janela if f.janelas[chave].captacao_liquida > 0]
    positivos.sort(key=lambda f: f.janelas[chave].captacao_liquida, reverse=True)

    compartilhados = [f for f in positivos if len(f.plataformas) >= 2][:limite]
    exclusivos = [f for f in positivos if len(f.plataformas) == 1][:limite]

    # Concentracao: por classificacao, share do fluxo positivo por plataforma.
    fluxo_por_classificacao_plataforma: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for fundo in positivos:
        liquida = fundo.janelas[chave].captacao_liquida
        for platform in fundo.plataformas:
            fluxo_por_classificacao_plataforma[fundo.classificacao][platform] += liquida

    concentracao = []
    for classificacao, por_platform in fluxo_por_classificacao_plataforma.items():
        total = sum(por_platform.values())
        if total <= 0:
            continue
        lideres = sorted(por_platform.items(), key=lambda kv: kv[1], reverse=True)
        platform_id, valor = lideres[0]
        concentracao.append(
            {
                "classificacao": classificacao,
                "platform_id": platform_id,
                "label": PLATFORMS[platform_id].label if platform_id in PLATFORMS else platform_id,
                "share_pct": round((valor / total) * 100.0, 1),
                "captacao_liquida_total": round(total, 2),
            }
        )
    concentracao.sort(key=lambda c: c["captacao_liquida_total"], reverse=True)

    # Momentum / sangria: 4 ultimas semanas ISO completas, todas no mesmo sinal.
    momentum_positivo = []
    sangria = []
    for fundo in res.fundos:
        semanas = list(fundo.semanas_recentes.values())
        if len(semanas) < _JANELAS_MOMENTUM:
            continue
        entry = {
            "cnpj": fundo.cnpj,
            "nome": fundo.nome,
            "classificacao": fundo.classificacao,
            "plataformas": fundo.plataformas,
            "semanas_recentes": fundo.semanas_recentes,
        }
        if all(v > 0 for v in semanas):
            momentum_positivo.append(entry)
        elif all(v < 0 for v in semanas):
            sangria.append(entry)
    momentum_positivo.sort(key=lambda e: sum(e["semanas_recentes"].values()), reverse=True)
    sangria.sort(key=lambda e: sum(e["semanas_recentes"].values()))

    # Hook XP-centrico: top captadores presentes em concorrente e ausentes da baseline.
    if baseline_id is None:
        ausentes_na_baseline: dict = {"available": False, "reason": "nenhuma casa baseline configurada"}
    elif baseline_id not in res.plataformas:
        label = PLATFORMS[baseline_id].label if baseline_id in PLATFORMS else baseline_id
        ausentes_na_baseline = {
            "available": False,
            "reason": f"{label} sem vitrine nesta execucao; toda captacao pareceria ausente dela",
            "baseline_id": baseline_id,
        }
    else:
        fora = [f for f in positivos if baseline_id not in f.plataformas][:limite]
        ausentes_na_baseline = {
            "available": True,
            "baseline_id": baseline_id,
            "baseline_label": PLATFORMS[baseline_id].label if baseline_id in PLATFORMS else baseline_id,
            "fundos": [_fundo_dict(f, chave) for f in fora],
        }

    return {
        "chave": chave,
        "compartilhados_top": [_fundo_dict(f, chave) for f in compartilhados],
        "exclusivos_top": [_fundo_dict(f, chave) for f in exclusivos],
        "concentracao_por_classificacao": concentracao[:limite],
        "momentum_positivo": momentum_positivo[:limite],
        "sangria": sangria[:limite],
        "ausentes_na_baseline": ausentes_na_baseline,
    }
