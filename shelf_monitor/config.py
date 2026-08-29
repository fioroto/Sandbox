"""Configuracao do monitor: plataformas, watchlist de gestoras e paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from shelf_monitor.models import CollectorStrategy

load_dotenv()


# --- Fontes de dados publicas ------------------------------------------------

CVM_REGISTRO_ZIP_URL = (
    "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip"
)
CVM_CAD_FI_URL = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv"
OPEN_FINANCE_DIRECTORY_URL = (
    "https://data.directory.openbankingbrasil.org.br/participants"
)
OPEN_FINANCE_FUNDS_PATH = "/open-banking/opendata-investments/v1/funds"


@dataclass
class PlatformConfig:
    """Uma casa monitorada e como coletar sua prateleira."""

    platform_id: str
    label: str
    strategies: list[CollectorStrategy]
    shelf_url: str | None = None
    # Marca a procurar no diretorio Open Finance (case-insensitive, substring).
    open_finance_brands: list[str] = field(default_factory=list)
    # Override manual da base URL Open Finance (env OPEN_FINANCE_BASE_URL_<ID>).
    open_finance_base_url: str | None = None
    # Caminho do storage_state Playwright para o coletor autenticado (v2).
    auth_storage_state: str | None = None
    notes: str | None = None

    @property
    def is_baseline(self) -> bool:
        return self.platform_id == "xp"


# XP entra como baseline de comparacao, nao como concorrente.
PLATFORMS: dict[str, PlatformConfig] = {
    "xp": PlatformConfig(
        platform_id="xp",
        label="XP Investimentos",
        strategies=[CollectorStrategy.OPEN_FINANCE, CollectorStrategy.PLAYWRIGHT],
        shelf_url="https://www.xpi.com.br/investimentos/fundos-de-investimento/lista/",
        open_finance_brands=["XP INVESTIMENTOS", "XP "],
        notes="Baseline. Lista e SPA; Playwright intercepta o XHR da listagem.",
    ),
    "btg": PlatformConfig(
        platform_id="btg",
        label="BTG Pactual",
        strategies=[CollectorStrategy.OPEN_FINANCE, CollectorStrategy.PLAYWRIGHT],
        shelf_url="https://investimentos.btgpactual.com/fundos-de-investimento",
        open_finance_brands=["BTG PACTUAL"],
        notes="Prateleira grande (~1.500 fundos, 350+ gestoras).",
    ),
    "itau": PlatformConfig(
        platform_id="itau",
        label="Itau (Ion)",
        strategies=[CollectorStrategy.OPEN_FINANCE, CollectorStrategy.HTML_SCRAPE],
        shelf_url="https://www.itau.com.br/personnalite/investimentos/fundos/catalogo",
        open_finance_brands=["ITAU"],
        notes="Catalogo Personnalite e a visao publica mais completa.",
    ),
    "nu": PlatformConfig(
        platform_id="nu",
        label="Nu (Nubank)",
        strategies=[CollectorStrategy.OPEN_FINANCE],
        shelf_url="https://nubank.com.br/nu/investimentos/fundos-de-investimento",
        open_finance_brands=["NU ", "NUBANK", "NU INVEST"],
        notes=(
            "App NuInvest encerrado; sem prateleira web deslogada. Se o Open Finance "
            "nao responder, fica UNAVAILABLE - e o caso de uso do coletor autenticado."
        ),
    ),
    "inter": PlatformConfig(
        platform_id="inter",
        label="Banco Inter",
        strategies=[CollectorStrategy.HTML_SCRAPE, CollectorStrategy.OPEN_FINANCE],
        shelf_url="https://inter.co/pra-voce/investimentos/fundos-de-investimento/",
        open_finance_brands=["INTER"],
        notes="Paginas estaticas por fundo, descobertas via sitemap.xml.",
    ),
}


@dataclass
class Watch:
    """Como reconhecer, no campo Gestor da CVM, as gestoras ligadas a uma casa."""

    name_patterns: list[str]
    cnpj_roots: list[str] = field(default_factory=list)


# Padroes aplicados sobre o campo Gestor normalizado (upper, sem acentos).
# cnpj_roots comeca vazio de proposito: `watchlist-discover` levanta os CNPJs
# reais do CSV da CVM e eles sao adicionados aqui - a partir dai o match por raiz
# de CNPJ e a via principal e o regex vira rede de seguranca para gestoras novas.
WATCHLIST: dict[str, Watch] = {
    "btg": Watch(
        name_patterns=[
            r"BTG PACTUAL (ASSET|GESTORA|WM|GESTAO)",
            r"BTG PACTUAL SERVICOS FINANCEIROS",
            r"\bBTG PACTUAL\b",
        ]
    ),
    "itau": Watch(
        name_patterns=[
            r"ITAU UNIBANCO ASSET",
            r"\bITAU ASSET\b",
            r"^KINEA\b",
            r"ITAU DTVM",
            r"INTRAG",
        ]
    ),
    "nu": Watch(name_patterns=[r"^NU ASSET", r"NU GESTORA", r"NU INVEST"]),
    "inter": Watch(name_patterns=[r"^INTER ASSET", r"INTER DTVM", r"INTER GESTAO"]),
    "xp": Watch(
        name_patterns=[
            r"^XP (ALLOCATION|GESTAO|VISTA|ADVISORY|PE|SELECTION|ASSET)",
            r"^XP \b",
        ]
    ),
}


# Campos do FundOffer cuja mudanca e competitivamente relevante e entra no digest.
MONITORED_FIELDS = [
    "min_initial",
    "admin_fee_pct",
    "performance_fee_pct",
    "closed_for_investment",
    "redemption_quotation_days",
    "redemption_payment_days",
    "grace_days",
]

# Se a coleta atual tiver menos que esta fracao do snapshot anterior, o diff e
# marcado como suspeito e as saidas nao viram churn no relatorio.
SUSPECT_SHRINK_RATIO = 0.5

# Similaridade minima para considerar que um fundo foi renomeado (e nao trocado).
FUZZY_RENAME_THRESHOLD = 0.92


@dataclass
class Config:
    """Configuracao de execucao, via env (.env) com override por CLI."""

    def __init__(
        self,
        data_dir: str | None = None,
        http_timeout: int | None = None,
        user_agent: str | None = None,
        directory_url: str | None = None,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        smtp_from: str | None = None,
        smtp_to: str | None = None,
    ):
        repo_root = Path(__file__).resolve().parent.parent
        self.data_dir = Path(
            data_dir or os.getenv("SHELF_DATA_DIR") or (repo_root / "data")
        )
        self.http_timeout = int(
            http_timeout or os.getenv("SHELF_HTTP_TIMEOUT", "60")
        )
        self.user_agent = user_agent or os.getenv(
            "SHELF_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        self.directory_url = (
            directory_url
            or os.getenv("OPEN_FINANCE_DIRECTORY_URL")
            or OPEN_FINANCE_DIRECTORY_URL
        )
        self.storage_state_dir = Path(
            os.getenv("SHELF_STORAGE_STATE_DIR") or (self.data_dir / "sessions")
        )
        # Ambientes com Chromium pre-instalado costumam ter um build diferente do
        # que o pacote playwright do pip espera; apontar o binario evita o
        # "playwright install" (que nem sempre e possivel).
        self.chromium_path = os.getenv("SHELF_CHROMIUM_PATH") or None
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "")
        self.smtp_port = int(smtp_port or os.getenv("SMTP_PORT", "587"))
        self.smtp_user = smtp_user or os.getenv("SMTP_USER", "")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = smtp_from or os.getenv("SMTP_FROM", "")
        self.smtp_to = smtp_to or os.getenv("SMTP_TO", "")

    # --- paths ---------------------------------------------------------------

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def cvm_raw_dir(self) -> Path:
        """Downloads brutos da CVM - gitignored (dezenas de MB)."""
        return self.data_dir / "cvm" / "raw"

    @property
    def cvm_extract_dir(self) -> Path:
        """Extrato filtrado pela watchlist - commitado."""
        return self.data_dir / "cvm" / "extract"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    def platform_snapshots_dir(self, platform_id: str) -> Path:
        return self.snapshots_dir / platform_id

    def ensure_dirs(self) -> None:
        for path in (
            self.snapshots_dir,
            self.cvm_raw_dir,
            self.cvm_extract_dir,
            self.cache_dir,
            self.reports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # --- plataformas ---------------------------------------------------------

    def platform(self, platform_id: str) -> PlatformConfig:
        cfg = PLATFORMS[platform_id]
        env_base = os.getenv(f"OPEN_FINANCE_BASE_URL_{platform_id.upper()}")
        if env_base:
            cfg.open_finance_base_url = env_base
        env_state = os.getenv(f"SHELF_AUTH_STATE_{platform_id.upper()}")
        if env_state:
            cfg.auth_storage_state = env_state
        return cfg

    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.smtp_to)

    def validate(self) -> list[str]:
        """O monitor roda so com fontes publicas - nada e obrigatorio."""
        errors: list[str] = []
        if self.http_timeout <= 0:
            errors.append("SHELF_HTTP_TIMEOUT deve ser positivo")
        return errors
