"""A curadoria do monitor da industria: as tabelas que gente mantem.

Moram em `data/industria/`, versionadas, e sao lidas aqui uma vez:

- `grupos_gestores.csv`: raiz do CNPJ da gestora -> grupo economico e tipo. O
  mesmo grupo aparece com raizes diferentes (duas entidades BTG, quatro
  Safra), e separar feeder de espelho pela raiz errava: a Caixa DTVM
  "espelharia" R$ 211 bi que sao feeders do proprio grupo (08/2026). O
  universo tem 840 raizes; as 112 maiores cobrem 95% do PL;
- `padroes_de_grupo.csv`: quando a raiz nao esta na tabela, o nome da gestora
  ainda pode dizer o conglomerado ("CAIXA ASSET", "BRADESCO ..."). A raiz
  sem grupo e sem padrao vira um grupo proprio, "nao classificado" - nunca
  "independente", que seria uma afirmacao;
- `marcas_de_distribuidor.csv`: marca no nome do veiculo que diz para qual
  casa ele foi feito ("ADVISORY" na XP, "ACCESS" no Inter);
- `taxonomia.csv`: rotulo ANBIMA (prefixo normalizado) -> macroclasse (N1)
  e peer group (N2), com a confianca do rotulo;
- `overrides_estrategia.csv`: a correcao manual da estrategia de uma classe.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.normalize import normalize_cnpj, normalize_text

TIPOS_DE_GESTORA: dict[str, str] = {
    "banco": "Bancária",
    "plataforma": "Plataforma",
    "independente": "Independente",
    "estrangeira": "Estrangeira",
    "cooperativa": "Cooperativa",
    "seguradora": "Seguradora",
    "fundo_de_pensao": "Fundo de pensão",
    "nao_classificado": "Não classificada",
}
# Grupos que distribuem para cliente final: o espelho que um deles cria e um
# veiculo de distribuicao dele.
TIPOS_DISTRIBUIDORES = ("banco", "plataforma", "cooperativa")


@dataclass(frozen=True)
class Grupo:
    nome: str
    tipo: str

    @property
    def rotulo_tipo(self) -> str:
        return TIPOS_DE_GESTORA.get(self.tipo, self.tipo)


@dataclass(frozen=True)
class LinhaTaxonomia:
    prefixo: str
    n1: str
    n2: str
    confianca: str


def _ler(caminho: Path) -> list[dict[str, str]]:
    if not caminho.is_file():
        return []
    with caminho.open(encoding="utf-8-sig", newline="") as handle:
        return [
            {k: (v or "").strip() for k, v in linha.items() if k}
            for linha in csv.DictReader(handle, delimiter=";")
        ]


def _nome_curto(nome: str) -> str:
    """"SPX GESTAO DE RECURSOS LTDA" -> "SPX GESTAO DE RECURSOS": para rotulo."""
    texto = re.sub(r"\b(LTDA|S\.?A\.?|S/A|EIRELI|ME)\b\.?", "", nome or "", flags=re.I)
    return re.sub(r"\s+", " ", texto).strip(" .-,") or nome


@dataclass
class Curadoria:
    grupos_por_raiz: dict[str, Grupo]
    padroes: list[tuple[re.Pattern, Grupo]]
    marcas: list[tuple[re.Pattern, str]]
    taxonomia: list[LinhaTaxonomia]
    overrides: dict[str, tuple[str, str]]

    @property
    def casas(self) -> set[str]:
        return {casa for _, casa in self.marcas}

    def grupo(self, cnpj_gestor: str | None, nome_gestor: str | None) -> Grupo:
        """O grupo economico de uma gestora, pela raiz, pelo nome ou por ela mesma."""
        cnpj = normalize_cnpj(cnpj_gestor) if cnpj_gestor else None
        raiz = cnpj[:8] if cnpj else ""
        if raiz in self.grupos_por_raiz:
            return self.grupos_por_raiz[raiz]
        nome = normalize_text(nome_gestor)
        for padrao, grupo in self.padroes:
            if nome and padrao.search(nome):
                return grupo
        rotulo = _nome_curto(nome_gestor or "") or (raiz or "gestora sem cadastro")
        return Grupo(nome=rotulo, tipo="nao_classificado")

    def marca_no_nome(self, nome: str | None) -> list[str]:
        """As casas cujas marcas aparecem no nome, na ordem da tabela."""
        texto = normalize_text(nome)
        return [casa for padrao, casa in self.marcas if padrao.search(texto)]

    def linha_da_taxonomia(self, rotulo_anbima: str | None) -> LinhaTaxonomia | None:
        """A linha de prefixo mais longo que casa com o rotulo (o rotulo da CVM
        vem truncado em 40 caracteres)."""
        texto = normalize_text(rotulo_anbima)
        if not texto:
            return None
        melhor = None
        for linha in self.taxonomia:
            if texto.startswith(linha.prefixo) or linha.prefixo.startswith(texto):
                if melhor is None or len(linha.prefixo) > len(melhor.prefixo):
                    melhor = linha
        return melhor


def carregar_curadoria(config: Config) -> Curadoria:
    pasta = config.industria_dir
    grupos = {}
    for linha in _ler(pasta / "grupos_gestores.csv"):
        raiz = re.sub(r"\D", "", linha.get("raiz", "")).zfill(8)
        if raiz.strip("0"):
            grupos[raiz] = Grupo(linha.get("grupo") or raiz, linha.get("tipo") or "nao_classificado")
    padroes = [
        (re.compile(linha["padrao"]), Grupo(linha["grupo"], linha.get("tipo") or "nao_classificado"))
        for linha in _ler(pasta / "padroes_de_grupo.csv")
        if linha.get("padrao") and linha.get("grupo")
    ]
    marcas = [
        (re.compile(rf"(?<![A-Z0-9]){re.escape(normalize_text(linha['token']))}(?![A-Z0-9])"),
         linha["casa"])
        for linha in _ler(pasta / "marcas_de_distribuidor.csv")
        if linha.get("token") and linha.get("casa")
    ]
    taxonomia = [
        LinhaTaxonomia(
            prefixo=normalize_text(linha["prefixo_anbima"]),
            n1=linha["n1"], n2=linha["n2"], confianca=linha.get("confianca") or "alta",
        )
        for linha in _ler(pasta / "taxonomia.csv")
        if linha.get("prefixo_anbima") and linha.get("n1")
    ]
    overrides = {}
    for linha in _ler(pasta / "overrides_estrategia.csv"):
        cnpj = normalize_cnpj(linha.get("cnpj_classe"))
        if cnpj and linha.get("n1") and linha.get("n2"):
            overrides[cnpj] = (linha["n1"], linha["n2"])
    return Curadoria(grupos, padroes, marcas, taxonomia, overrides)
