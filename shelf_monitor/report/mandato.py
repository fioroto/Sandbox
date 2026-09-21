"""Mandato declarado das vitrines: o que o regulamento autoriza.

Das 117 colunas do extrato de informacoes da CVM, o parser consumia 17. O bloco
maior das que sobravam descreve **o que cada classe pode carregar**: credito
privado, ativo no exterior, derivativos, exposicao acima do patrimonio e a
condicao de fundo espelho. Sao ~100 colunas em disco que nenhuma superficie
lia - a maior massa de dado ocioso do repositorio.

A palavra que governa tudo aqui e *autorizado*
----------------------------------------------

Um fundo pode ter autorizacao para 100% de credito privado e carregar 10%. Este
modulo mede o **mandato**, nunca a carteira. Medir exposicao realizada exige a
CDA (`cda_fi_{YYYYMM}.zip`, mapeada em `market/datasets.py` e nao baixada), e
qualquer rotulo que diga "carrega" onde o dado diz "pode carregar" esta errado.

Por que isso e leitura competitiva, e nao curiosidade regulatoria: mandato e
escolha de produto. Uma vitrine em que 80% das classes autorizam credito privado
e outra coisa que uma em que 40% autorizam - a primeira montou prateleira de
risco de credito, a segunda nao. E, ao contrario da taxa, o mandato nao depende
de a casa publicar nada: sai do registro, igual para todo mundo.

Indice, nao share bruto
-----------------------

Pela mesma razao do posicionamento por categoria: "62% da vitrine da casa X
autoriza exterior" nao diz nada sozinho; "X autoriza exterior 1,6x mais que a
media das concorrentes" diz onde ela apostou. O denominador sao as **demais**
casas, nao o total - com a propria dentro, todo indice e puxado para 1.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from shelf_monitor.models import ShelfSnapshot
from shelf_monitor.normalize import normalize_cnpj, parse_number
from shelf_monitor.report.aggregate import _platform_label
from shelf_monitor.report.competitive import identifiable_vitrines, vitrine_snapshots

# Minimo de fundos com o campo preenchido para a casa entrar naquela dimensao.
# Mesma regra do resto do monitor: com meia vitrine respondida, o share descreve
# a amostra que casou.
_MIN_COBERTURA = 0.5

# Fora desta faixa a diferenca e aposta declarada, nao arredondamento.
_SOBRE_INDICE = 1.35
_SUB_INDICE = 0.7

# Quantas apostas o bloco publica, e quantas o console imprime. Nomeados
# porque sao decisao de leitura, nao detalhe: a lista existe para caber numa
# tela, e um numero solto no meio de um `sorted` nao diz isso.
_MAX_APOSTAS = 12
_MAX_APOSTAS_CONSOLE = 6

# As dimensoes do mandato, na ordem em que saem no relatorio.
# (chave, rotulo, coluna do flag S/N, coluna do teto em %)
DIMENSOES: tuple[tuple[str, str, str, str | None], ...] = (
    ("credito_privado", "Credito privado autorizado",
     "credito_privado_autorizado", "credito_privado_max_pct"),
    ("exterior", "Ativo no exterior autorizado",
     "exterior_autorizado", "exterior_max_pct"),
    ("derivativos", "Derivativos autorizados", "derivativos_autorizado", None),
    ("alavancagem", "Exposicao acima do patrimonio autorizada",
     "alavancagem_autorizada", "alavancagem_fator"),
    ("espelho", "Fundo espelho", "fundo_espelho", None),
)


@dataclass(frozen=True)
class MandatoClasse:
    """O mandato declarado de uma classe, direto do extrato."""

    cnpj: str
    flags: dict[str, str | None] = field(default_factory=dict)
    tetos: dict[str, float | None] = field(default_factory=dict)

    def autoriza(self, chave: str) -> bool | None:
        """True/False quando a CVM respondeu; None quando a coluna veio vazia."""
        valor = self.flags.get(chave)
        if valor not in ("S", "N"):
            return None
        return valor == "S"


class IndiceMandato:
    """Mandato por CNPJ, a partir do `mercado.csv`."""

    def __init__(self, classes: dict[str, MandatoClasse]) -> None:
        self._classes = classes

    def __len__(self) -> int:
        return len(self._classes)

    def get(self, cnpj: str | None) -> MandatoClasse | None:
        return self._classes.get(cnpj) if cnpj else None

    @classmethod
    def load(cls, market_dir: Path) -> IndiceMandato | None:
        caminho = market_dir / "mercado.csv"
        if not caminho.is_file():
            return None
        classes: dict[str, MandatoClasse] = {}
        with caminho.open(encoding="utf-8-sig", newline="") as handle:
            leitor = csv.DictReader(handle, delimiter=";")
            if not leitor.fieldnames or not any(
                coluna in leitor.fieldnames for _, _, coluna, _ in DIMENSOES
            ):
                # Base anterior a exportacao do mandato: melhor dizer que nao ha
                # dado do que devolver uma vitrine inteira com flags vazias.
                return None
            for row in leitor:
                cnpj = normalize_cnpj(row.get("cnpj"))
                if not cnpj:
                    continue
                classes[cnpj] = MandatoClasse(
                    cnpj=cnpj,
                    flags={
                        chave: (row.get(coluna) or "").strip().upper()[:1] or None
                        for chave, _, coluna, _ in DIMENSOES
                    },
                    tetos={
                        chave: parse_number(row.get(teto)) if teto else None
                        for chave, _, _, teto in DIMENSOES
                    },
                )
        return cls(classes) if classes else None


@dataclass
class MandatoCasa:
    """O mandato da vitrine de uma casa, dimensao a dimensao."""

    platform_id: str
    label: str
    vitrine: int
    classes: list[MandatoClasse] = field(default_factory=list)

    @property
    def casados(self) -> int:
        return len(self.classes)

    def share(self, chave: str) -> float | None:
        respondidos = [
            c.autoriza(chave) for c in self.classes if c.autoriza(chave) is not None
        ]
        if len(respondidos) < _MIN_COBERTURA * max(self.vitrine, 1):
            return None
        return sum(1 for v in respondidos if v) / len(respondidos)

    def teto_mediano(self, chave: str) -> float | None:
        valores = sorted(
            v for c in self.classes
            if c.autoriza(chave) and (v := c.tetos.get(chave)) is not None
        )
        if not valores:
            return None
        meio = len(valores) // 2
        if len(valores) % 2:
            return valores[meio]
        return (valores[meio - 1] + valores[meio]) / 2

    def to_dict(self) -> dict:
        return {
            "platform_id": self.platform_id,
            "label": self.label,
            "vitrine": self.vitrine,
            "casados": self.casados,
            "dimensoes": {
                chave: {
                    "rotulo": rotulo,
                    "share": (
                        None if self.share(chave) is None
                        else round(self.share(chave), 4)
                    ),
                    "teto_mediano": self.teto_mediano(chave),
                }
                for chave, rotulo, _, _ in DIMENSOES
            },
        }


def build_mandato(config, snapshots: list[ShelfSnapshot]) -> dict:
    """Mandato declarado de cada vitrine, com o indice contra as concorrentes."""
    indice = IndiceMandato.load(config.market_dir)
    if indice is None:
        return {
            "available": False,
            "reason": (
                "base de mercado sem as colunas de mandato - rode "
                "'python -m shelf_monitor market --force' para reimportar o "
                "extrato com elas"
            ),
            "excluded": [],
        }

    vitrines, fora = vitrine_snapshots(snapshots)
    cruzaveis, sem_identidade = identifiable_vitrines(vitrines)
    sem_cnpj = [
        {**item, "reason": (
            "sem CNPJ na vitrine nao ha como buscar o mandato da classe no "
            f"extrato ({item['reason']})"
        )}
        for item in sem_identidade
    ]
    if len(cruzaveis) < 2:
        return {
            "available": False,
            "reason": (
                f"{len(cruzaveis)} vitrine(s) cruzavel(is): o indice de mandato "
                "precisa de ao menos duas para haver com o que comparar"
            ),
            "excluded": fora + sem_cnpj,
        }

    casas: list[MandatoCasa] = []
    for platform_id, funds in cruzaveis.items():
        classes = [
            classe for f in funds if (classe := indice.get(f.cnpj)) is not None
        ]
        casas.append(MandatoCasa(
            platform_id=platform_id,
            label=_platform_label(platform_id),
            vitrine=len(funds),
            classes=classes,
        ))

    # Indice contra as DEMAIS casas: com a propria no denominador, uma casa
    # grande achata o proprio sinal.
    linhas: list[dict] = []
    for casa in casas:
        pares = [c for c in casas if c.platform_id != casa.platform_id]
        for chave, rotulo, _, _ in DIMENSOES:
            share = casa.share(chave)
            if share is None:
                continue
            classes_pares = [c for par in pares for c in par.classes]
            respondidos = [
                v for c in classes_pares if (v := c.autoriza(chave)) is not None
            ]
            if not respondidos:
                continue
            share_pares = sum(1 for v in respondidos if v) / len(respondidos)
            if not share_pares:
                # Ninguem mais autoriza a dimensao. Se a casa tambem nao
                # autoriza, 0/0 nao e "so ela tem": e dimensao que nenhuma casa
                # usa, e publicar "so ela" com share 0% era uma frase falsa.
                indice_rel = float("inf") if share else 0.0
            else:
                indice_rel = share / share_pares
            linhas.append({
                "platform_id": casa.platform_id,
                "label": casa.label,
                "chave": chave,
                "rotulo": rotulo,
                "share": round(share, 4),
                "share_pares": round(share_pares, 4),
                "indice": (
                    None if indice_rel == float("inf") else round(indice_rel, 2)
                ),
                "so_ela": indice_rel == float("inf"),
                "teto_mediano": casa.teto_mediano(chave),
                "aposta": (
                    "acima" if indice_rel >= _SOBRE_INDICE
                    else "abaixo" if indice_rel <= _SUB_INDICE
                    else "na media"
                ),
            })

    if not linhas:
        return {
            "available": False,
            "reason": "nenhuma dimensao de mandato com cobertura suficiente",
            "excluded": fora + sem_cnpj,
        }

    return {
        "available": True,
        "participantes": [c.platform_id for c in casas],
        "excluded": fora + sem_cnpj,
        "universo": {"classes_na_base": len(indice)},
        "nota": (
            "Mandato AUTORIZADO pelo regulamento, nunca exposicao carregada. "
            "Medir o que a carteira de fato tem exigiria a CDA, fora do escopo."
        ),
        "casas": [c.to_dict() for c in casas],
        "linhas": sorted(linhas, key=lambda l: (l["platform_id"], l["chave"])),
        "apostas": sorted(
            [linha for linha in linhas if linha["aposta"] == "acima"],
            # `indice` vem None quando e infinito (so esta casa autoriza), que e
            # a aposta mais forte: ela vai para o topo. Um `or` aqui mandava
            # justamente essas linhas para o fim, e colapsava indice 0.0 no
            # mesmo balde.
            key=lambda linha: (
                0 if linha["so_ela"] else 1,
                -(linha["indice"] if linha["indice"] is not None else 0.0),
            ),
        )[:_MAX_APOSTAS],
        "_casas": casas,
    }


# --- renderizacao no console --------------------------------------------------


def print_mandato(resultado: dict) -> None:
    """Imprime o mandato por casa, sempre rotulado como autorizado."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print()
    console.print(
        "[bold]Mandato declarado[/] - o que o regulamento de cada classe AUTORIZA"
    )
    if not resultado.get("available"):
        console.print(f"   [yellow]{resultado.get('reason')}[/]")
        for e in resultado.get("excluded", []):
            console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
        return

    console.print(f"   [dim]{resultado['nota']}[/]")
    por_casa: dict[str, dict] = {}
    for linha in resultado["linhas"]:
        por_casa.setdefault(linha["platform_id"], {})[linha["chave"]] = linha

    table = Table(title="Share da vitrine que autoriza cada coisa", title_style="bold")
    table.add_column("Casa")
    for _, rotulo, _, _ in DIMENSOES:
        table.add_column(rotulo.replace(" autorizado", "").replace(" autorizada", ""),
                         justify="right")
    for casa in resultado["casas"]:
        celulas = []
        for chave, _, _, _ in DIMENSOES:
            linha = por_casa.get(casa["platform_id"], {}).get(chave)
            if linha is None:
                celulas.append("s/dado")
                continue
            marca = (
                " *" if linha["aposta"] == "acima"
                else " v" if linha["aposta"] == "abaixo" else ""
            )
            celulas.append(f"{linha['share']:.0%}{marca}")
        table.add_row(casa["label"], *celulas)
    console.print(table)
    console.print("   [dim]* acima das concorrentes, v abaixo (indice 1,35 / 0,70)[/]")

    for linha in resultado["apostas"][:_MAX_APOSTAS_CONSOLE]:
        indice = "so ela" if linha["so_ela"] else f"{linha['indice']:.2f}x"
        teto = (
            f", teto mediano {linha['teto_mediano']:.0f}%"
            if linha["teto_mediano"] is not None else ""
        )
        console.print(
            f"   [green]{linha['label']}[/]: {linha['rotulo'].lower()} em "
            f"{linha['share']:.0%} da vitrine contra {linha['share_pares']:.0%} "
            f"nas demais ({indice}{teto})"
        )
    for e in resultado.get("excluded", []):
        console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
