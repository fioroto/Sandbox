"""Configuracao do monitor: plataformas, watchlist de gestoras e paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

from shelf_monitor.models import LISTING_SOURCES, CollectorStrategy

load_dotenv()


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "sim", "yes")


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

    @property
    def declara_vitrine(self) -> bool:
        """A casa tem fonte de vitrine configurada - coleta sem vitrine e parcial.

        O Nu so declara Open Finance (nao ha vitrine deslogada), e para ele
        catalogo sozinho e a coleta completa.
        """
        return any(s.value in LISTING_SOURCES for s in self.strategies)


# XP entra como baseline de comparacao, nao como concorrente.
PLATFORMS: dict[str, PlatformConfig] = {
    "xp": PlatformConfig(
        platform_id="xp",
        label="XP Investimentos",
        strategies=[CollectorStrategy.OPEN_FINANCE, CollectorStrategy.PLAYWRIGHT],
        shelf_url="https://www.xpi.com.br/investimentos/fundos-de-investimento/lista/",
        open_finance_brands=["XP INVESTIMENTOS", "XP "],
        notes="Baseline. Lista é SPA; Playwright intercepta o XHR da listagem.",
    ),
    "btg": PlatformConfig(
        platform_id="btg",
        label="BTG Pactual",
        strategies=[
            CollectorStrategy.OPEN_FINANCE,
            CollectorStrategy.API_JSON,
            CollectorStrategy.PLAYWRIGHT,
        ],
        shelf_url="https://investimentos.btgpactual.com/fundos-de-investimento/produtos",
        open_finance_brands=["BTG PACTUAL"],
        notes=(
            "A página /produtos é alimentada pela API pública funds/list/v2 "
            "(~693 fundos na prateleira comercial, medido em 2026-08-31). "
            "Open Finance publica ~9.950 - tudo que a casa administra."
        ),
    ),
    "itau": PlatformConfig(
        platform_id="itau",
        label="Itaú",
        strategies=[CollectorStrategy.OPEN_FINANCE, CollectorStrategy.PLAYWRIGHT],
        shelf_url="https://www.itau.com.br/investimentos/fundos/rentabilidade",
        open_finance_brands=["ITAU"],
        notes=(
            "Prateleira de varejo íon; o rótulo é só \"Itaú\" desde 2026-10-15. "
            "Lista de rentabilidade paginada em ~10 fundos (48 páginas, 477 "
            "fundos em 2026-10-14). Akamai barra HTTP simples e o headless shell "
            "do Playwright, mas não o Chromium completo em --headless=new - que é "
            "o que o coletor usa desde 2026-09-19, sem exigir janela. Nem a "
            "tabela nem o XHR (cifrado) trazem CNPJ: ele vem pelo nome, contra o "
            "catálogo Open Finance e o cadastro da CVM (shelf_monitor.cnpj_por_nome)."
        ),
    ),
    "nu": PlatformConfig(
        platform_id="nu",
        label="Nu (Nubank)",
        strategies=[CollectorStrategy.OPEN_FINANCE],
        shelf_url="https://nubank.com.br/nu/investimentos/fundos-de-investimento",
        open_finance_brands=["NU ", "NUBANK", "NU INVEST"],
        notes=(
            "App NuInvest encerrado; sem prateleira web deslogada. Se o Open Finance "
            "não responder, fica UNAVAILABLE - é o caso de uso do coletor autenticado."
        ),
    ),
    "inter": PlatformConfig(
        platform_id="inter",
        label="Banco Inter",
        strategies=[CollectorStrategy.HTML_SCRAPE, CollectorStrategy.OPEN_FINANCE],
        shelf_url="https://inter.co/pra-voce/investimentos/fundos-de-investimento/",
        open_finance_brands=["INTER"],
        notes="Páginas estáticas por fundo, descobertas via sitemap.xml.",
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
#
# Empiricus (BTG) e o Itau Unibanco S.A. como gestor entraram em 14/10/2026,
# alinhados ao grupo gestor de `data/industria/grupos_gestores.csv`: o extrato
# de 28/09 deixava de fora 28 e 13 classes deles, e o radar do digest passou a
# ser "o que as gestoras do grupo registraram".
WATCHLIST: dict[str, Watch] = {
    "btg": Watch(
        name_patterns=[
            r"BTG PACTUAL (ASSET|GESTORA|WM|GESTAO)",
            r"BTG PACTUAL SERVICOS FINANCEIROS",
            r"\bBTG PACTUAL\b",
            r"^EMPIRICUS ASSET",
        ]
    ),
    "itau": Watch(
        name_patterns=[
            r"ITAU UNIBANCO ASSET",
            r"\bITAU ASSET\b",
            r"^ITAU UNIBANCO S A\b",
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


# Os campos do FundOffer acompanhados pelo diff estao em diff._TRACKED, junto da
# funcao que extrai cada um. Uma segunda lista aqui seria uma fonte de verdade
# paralela, livre para divergir sem que nada quebre.

# Se a coleta atual tiver menos que esta fracao do snapshot anterior, o diff e
# marcado como suspeito e as saidas nao viram churn no relatorio.
SUSPECT_SHRINK_RATIO = 0.5

# O espelho do de cima, para a VITRINE: se ela cresce esta razao ou mais (ou
# aparece onde nao havia), o que mudou foi a cobertura da coleta, nao a
# prateleira - e o diff vira nova linha de base, sem entradas. Medido em
# 2026-09-19: o conserto do navegador levou as vitrines de XP, BTG e Itau de 0
# a 668, 649 e 458 fundos, e o digest publicou "828 fundos entraram".
SUSPECT_GROWTH_RATIO = 2.0
# ...desde que o salto tambem seja grande em fundos: vitrine pequena dobrando
# (3 -> 6) e movimento plausivel. O maior crescimento real medido entre duas
# coletas com vitrine foi do Inter, 399 -> 466 (+67, +17%) de 31/08 a 13/09;
# os ganhos de cobertura foram todos de 0 para centenas. Vitrine que aparece
# onde nao havia nenhuma e nova linha de base em qualquer tamanho.
SUSPECT_GROWTH_MIN_FUNDS = 50

# Similaridade minima para considerar que um fundo foi renomeado (e nao trocado).
FUZZY_RENAME_THRESHOLD = 0.92


@dataclass
class Config:
    """Configuracao de execucao, via env (.env) com override por CLI."""

    # Identidade das superficies - titulo, capa, prefixo de arquivo, assunto do
    # e-mail e mensagem de commit. Eram literais nos renderers; viraram atributo
    # para o modulo de previdencia (shelf_monitor.previdencia) trocar na
    # subclasse dele. Os valores aqui sao exatamente os literais de antes.
    titulo_relatorio = "Monitor de Prateleiras de Fundos"
    descricao_relatorio = "Concorrentes da XP"
    descricao_fontes = "prateleiras públicas e radar de registros na CVM"
    capa_deck = "Prateleiras de fundos"
    titulo_planilha = "Prateleiras de fundos - resumo"
    prefixo_arquivo = "prateleiras"
    rotulo_assunto = "Monitor de fundos"
    mensagem_commit = "data: snapshot do monitor de prateleiras"
    # Blocos analiticos sem sentido no dominio do modulo: (chave, motivo), com
    # as chaves de models (BLOCO_COTISTAS, BLOCO_CNPJ). No monitor de fundos,
    # nenhum; a previdencia declara os dela.
    blocos_suprimidos: tuple[tuple[str, str], ...] = ()
    # As casas do digest semanal, a base primeiro. Decidido em 14/10/2026 com
    # a area: os concorrentes que importam acompanhar sao BTG e Itau. O resto
    # da execucao (coleta, dashboard, planilha e deck) segue com todas as casas
    # de PLATFORMS; so o digest e o assunto do e-mail recortam.
    casas_do_digest: tuple[str, ...] = ("xp", "btg", "itau")
    # Casas que nunca saem das analises por falha de coleta: sem vitrine
    # utilizavel no dia, entra a ultima valida, com a data dita
    # (`shelf_monitor.reserva`). Pedido em 14/10/2026: o Itau em todas as
    # analises.
    vitrine_de_reserva: tuple[str, ...] = ("itau",)

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
        # Com isso ligado, toda renderizacao grava HTML, XHRs e payloads em
        # data/cache/debug/ - e o que permite escrever o parser com a evidencia
        # na mao quando uma listagem muda de layout.
        self.browser_debug = _env_flag("SHELF_BROWSER_DEBUG")
        # Navegador com janela, usando o Chrome instalado do usuario
        # (SHELF_BROWSER_CHANNEL=chrome). Nao e necessario para XP e Itau: o que
        # aquelas CDNs barram e o headless shell, nao o Chromium completo sem
        # janela. Fica como recurso para pagina que exija interacao visivel ou a
        # sessao logada do proprio navegador.
        self.browser_headed = _env_flag("SHELF_BROWSER_HEADED")
        self.browser_channel = os.getenv("SHELF_BROWSER_CHANNEL", "").strip() or None
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
    def market_dir(self) -> Path:
        """Base geral do mercado - tabelas CSV, gitignored (centenas de MB)."""
        return self.data_dir / "market"

    @property
    def market_raw_dir(self) -> Path:
        """Downloads brutos da CVM para a base de mercado - gitignored."""
        return self.market_dir / "raw"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def debug_dir(self) -> Path:
        """Evidencia bruta das sondagens e das renderizacoes - gitignored."""
        return self.cache_dir / "debug"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def etf_captacao_csv(self) -> Path:
        """A captacao mensal de ETF que a camada ao lado do monitor da industria le.

        Gravada pelo etf-monitor (repositorio a parte desde 07/10/2026) em
        `data/reports/etf_captacao_mensal.csv` dele: `SHELF_ETF_CAPTACAO_CSV`
        aponta o arquivo; sem a variavel, vale o de `data/reports/` daqui.
        """
        apontado = os.getenv("SHELF_ETF_CAPTACAO_CSV", "").strip()
        return Path(apontado) if apontado else self.reports_dir / "etf_captacao_mensal.csv"

    @property
    def distribuivel_dir(self) -> Path:
        """Onde a pessoa solta os CSVs dos fundos que a XP distribui ou pode
        distribuir - gitignored: dado comercial interno."""
        return self.data_dir / "distribuivel" / "entrada"

    @property
    def distribuivel_modelo_path(self) -> Path:
        """O CSV so com o cabecalho da lista distribuivel - versionado."""
        return self.data_dir / "distribuivel" / "modelo.csv"

    @property
    def historico_dir(self) -> Path:
        """Dado primario que a CVM nao guarda - o retrato mensal do cadastro e
        os blocos publicados do monitor da industria. Versionado: nao se refaz
        depois."""
        return self.data_dir / "historico"

    @property
    def industria_dir(self) -> Path:
        """Curadoria do monitor da industria (taxonomia, grupos de gestora,
        marcas de distribuidor, overrides) - versionada, editada a mao."""
        return self.data_dir / "industria"

    @property
    def industria_cache_dir(self) -> Path:
        """Perfis mensais compactos e o retrato do universo do ultimo ciclo -
        gitignored (fica em data/cache)."""
        return self.cache_dir / "industria"

    def platform_snapshots_dir(self, platform_id: str) -> Path:
        return self.snapshots_dir / platform_id

    def ensure_dirs(self) -> None:
        for path in (
            self.snapshots_dir,
            self.cvm_raw_dir,
            self.cvm_extract_dir,
            self.cache_dir,
            self.reports_dir,
            self.market_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # --- plataformas ---------------------------------------------------------

    def platform(self, platform_id: str) -> PlatformConfig:
        """PlatformConfig com os overrides de ambiente aplicados.

        Devolve uma copia: PLATFORMS e estado de modulo, e escrever nele faria
        um override vazar para outras instancias de Config.
        """
        overrides: dict[str, object] = {}
        env_base = os.getenv(f"OPEN_FINANCE_BASE_URL_{platform_id.upper()}")
        if env_base:
            overrides["open_finance_base_url"] = env_base
        env_state = os.getenv(f"SHELF_AUTH_STATE_{platform_id.upper()}")
        if env_state:
            overrides["auth_storage_state"] = env_state
        return replace(PLATFORMS[platform_id], **overrides)

    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.smtp_to)

    def validate(self) -> list[str]:
        """O monitor roda so com fontes publicas - nada e obrigatorio."""
        errors: list[str] = []
        if self.http_timeout <= 0:
            errors.append("SHELF_HTTP_TIMEOUT deve ser positivo")
        return errors
