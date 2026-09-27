"""Vitrines de previdencia e as pastas proprias do modulo.

As casas mantem o id do modulo de fundos (xp, btg, itau, nu, inter): e o que
preserva a cor de cada uma nos graficos, a ordem canonica e a XP como baseline.
O que muda e o canal - e o rotulo, quando o canal e outro produto: a vitrine de
previdencia do Itau e o catalogo do Private Bank, nao a prateleira Ion, e sai
rotulada assim (ver `ROTULOS_PROPRIOS`).

URLs levantadas em 2026-09-22 e sondadas no navegador antes de virar coletor:

- XP: a lista em `institucional.xpi.com.br/.../lista-de-fundos/#/`. A rota
  `#/` nao e decorativa - sem ela a URL entrega a pagina de marketing.
- BTG: `/previdencia-privada/produtos`, alimentada por um endpoint publico
  proprio (`pension-management/funds/public`), diferente do de fundos.
- Itau: o catalogo de rentabilidade do Private Bank, por decisao do usuario em
  2026-09-22 - o bloco de previdencia dele, nao a tabela inteira.
- Nu e Inter: nenhuma vitrine publica de previdencia foi encontrada.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from shelf_monitor.config import PLATFORMS, Config, PlatformConfig
from shelf_monitor.models import (
    BLOCO_CNPJ,
    BLOCO_COTISTAS,
    BLOCO_PRODUTO_PROPRIO,
    CollectorStrategy,
)

URL_XP = "https://institucional.xpi.com.br/previdencia-privada/lista-de-fundos/#/"
URL_BTG = "https://investimentos.btgpactual.com/previdencia-privada/produtos"
URL_ITAU = (
    "https://ww15.itau.com.br/privatebank/hotsite/catalogofundos2/"
    "tabela_rentabilidade/default.aspx"
)

PLATAFORMAS: dict[str, PlatformConfig] = {
    "xp": PlatformConfig(
        platform_id="xp",
        label=PLATFORMS["xp"].label,
        strategies=[CollectorStrategy.PLAYWRIGHT],
        shelf_url=URL_XP,
        notes=(
            "Baseline. A página desenha 50 cards, mas chama uma API que devolve "
            "a vitrine inteira (223 fundos com CNPJ em 2026-09-22); o coletor "
            "intercepta essa resposta. HTTP simples leva 403."
        ),
    ),
    "btg": PlatformConfig(
        platform_id="btg",
        label=PLATFORMS["btg"].label,
        strategies=[CollectorStrategy.API_JSON],
        shelf_url=URL_BTG,
        notes=(
            "API pública pension-management/funds/public: array plano, sem "
            "páginação, 226 fundos com CNPJ em 2026-09-22."
        ),
    ),
    "itau": PlatformConfig(
        platform_id="itau",
        label="Itaú Private",
        strategies=[CollectorStrategy.HTML_SCRAPE],
        shelf_url=URL_ITAU,
        notes=(
            "Catálogo do Private Bank, não a prateleira de varejo (íon). HTML "
            "servido pronto, sem CNPJ; só o bloco de previdência entra."
        ),
    ),
    "nu": PlatformConfig(
        platform_id="nu",
        label=PLATFORMS["nu"].label,
        # Uma estrategia declarada e obrigatoria (BaseCollector a le para o
        # snapshot); o coletor sem vitrine sai UNAVAILABLE sem tocar a rede.
        strategies=[CollectorStrategy.HTML_SCRAPE],
        notes="Nenhuma vitrine pública de previdência encontrada (2026-09-22).",
    ),
    "inter": PlatformConfig(
        platform_id="inter",
        label=PLATFORMS["inter"].label,
        # Uma estrategia declarada e obrigatoria (BaseCollector a le para o
        # snapshot); o coletor sem vitrine sai UNAVAILABLE sem tocar a rede.
        strategies=[CollectorStrategy.HTML_SCRAPE],
        notes="Nenhuma vitrine pública de previdência encontrada (2026-09-22).",
    ),
}

# Rotulos que diferem do modulo de fundos para o mesmo id. Aplicados durante a
# execucao do modulo por `rotulos_sobrepostos` - nunca escritos em PLATFORMS.
ROTULOS_PROPRIOS: dict[str, str] = {
    platform_id: cfg.label
    for platform_id, cfg in PLATAFORMAS.items()
    if platform_id in PLATFORMS and PLATFORMS[platform_id].label != cfg.label
}


# Por que as superficies da previdencia nao contam cotistas nem cruzam CNPJ.
# O texto e o que o leitor ve no lugar do bloco; os numeros sao de 23/09.
MOTIVO_COTISTAS = (
    "em previdência o cotista do fundo (FIE) é a seguradora, não o participante: "
    "438 dos 439 fundos de vitrine com informe têm 1 cotista (XP 213 de 214, BTG "
    "226 de 226, informe de 23/09). Contar cotistas não mede gente"
)
MOTIVO_CNPJ = (
    "cada seguradora distribui pelo próprio veículo (FIE), então o mesmo gestor e "
    "a mesma estratégia têm CNPJ diferente em cada casa - em 23/09 as vitrines de "
    "XP e BTG dividiam 1 CNPJ (217 só na XP, 225 só no BTG). Gap e exclusividade "
    "por CNPJ medem a seguradora, não a oferta; a comparação que vale é por "
    "gestora e categoria"
)

MOTIVO_PRODUTO_PROPRIO = (
    "o gestor registrado do fundo que a casa vende é a seguradora ou a asset dela, "
    "não o dono da estratégia: em 23/09, 88 dos 118 fundos que a regra chamaria de "
    "produto próprio da XP tinham como gestor a XP Vida e Previdência (Kinea, AZ "
    "Quest, Kapitalo), e os FICs de Absolute e Kapitalo no BTG, a BTG Pactual "
    "Gestão e Consultoria"
)


# Por que o dashboard da previdencia nao tem o radar CVM. Nao e bloco
# suprimido - o `build_report` da previdencia nem coleta o cadastro (ver cli.py).
MOTIVO_RADAR = (
    "o radar acompanha os registros de fundos das gestoras ligadas às casas e roda "
    "no monitor de fundos (python -m shelf_monitor run)"
)


class ConfigPrevidencia(Config):
    """Config do modulo de previdencia: pastas proprias, base CVM compartilhada.

    `snapshots/` e `reports/` ficam sob `data/previdencia/`, entao o diff compara
    previdencia com previdencia e o dashboard nao sobrescreve o de fundos. A base
    de mercado, o cache do informe diario e os downloads da CVM continuam os do
    modulo de fundos: e a mesma base oficial, e duplica-la custaria centenas de
    MB para nenhum ganho.
    """

    titulo_relatorio = "Monitor de Previdência"
    descricao_relatorio = "Previdência privada - concorrentes da XP"
    descricao_fontes = "vitrines públicas de previdência"
    capa_deck = "Previdência privada"
    titulo_planilha = "Previdência privada - resumo"
    prefixo_arquivo = "previdencia"
    rotulo_assunto = "Monitor de previdência"
    mensagem_commit = "data: snapshot do monitor de previdência"
    blocos_suprimidos = (
        (BLOCO_COTISTAS, MOTIVO_COTISTAS),
        (BLOCO_CNPJ, MOTIVO_CNPJ),
        (BLOCO_PRODUTO_PROPRIO, MOTIVO_PRODUTO_PROPRIO),
    )

    @property
    def previdencia_dir(self) -> Path:
        return self.data_dir / "previdencia"

    @property
    def snapshots_dir(self) -> Path:
        return self.previdencia_dir / "snapshots"

    @property
    def reports_dir(self) -> Path:
        return self.previdencia_dir / "reports"

    def platform(self, platform_id: str) -> PlatformConfig:
        """Copia, pelo mesmo motivo do Config de fundos: nao vazar estado."""
        return replace(PLATAFORMAS[platform_id])
