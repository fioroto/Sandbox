"""Custo real da vitrine, contra os pares da mesma categoria.

A metrica de custo que existia comparava a mediana da taxa de administracao da
vitrine inteira entre casas (`competitive.median_admin_fee`). Ela mede
**composicao, nao preco**: uma casa com mais renda fixa (~0,2% ao ano) aparece
"mais barata" que uma com mais acoes (~2%) sem que nenhum produto custe
diferente. Trocar a prateleira de mix muda o numero; trocar a taxa dos fundos,
quase nao.

O que este modulo faz no lugar: compara cada fundo com a **mediana da propria
categoria no universo oficial** e agrega o indice, nao a taxa. Um indice de 1,30
diz que o fundo cobra 30% acima dos pares dele - e isso e comparavel entre casas
de composicoes diferentes.

**O nivel absoluto do indice carrega um vies estrutural; a comparacao nao.**
Medido em 2026-09-20, as tres vitrines saem em 1,65x (XP), 1,50x (BTG) e 1,57x
(Inter). Nenhuma delas esta cobrando 60% a mais que o mercado: a vitrine exibe a
classe de VAREJO, que carrega a camada de distribuicao, enquanto o universo da
categoria inclui classes da mesma estrategia vendidas sem essa camada. O numero
que decide alguma coisa e a diferenca ENTRE as casas e o fundo individual muito
acima da propria categoria - nao a distancia de 1,00x.

Tres guardas que o dado exige
-----------------------------

**Taxa polimorfica.** `TAXA_ADM` carrega ora percentual ao ano, ora valor fixo em
reais (o regulamento admite texto livre para investidor qualificado). Entram no
indice apenas as classes com unidade `percentual`; as demais aparecem contadas a
parte. Sem isso, um fundo de "R$ 30.000 por ano" lidera o ranking de caro como
se cobrasse 30.000%.

**Idade da entrega.** O extrato guarda uma linha por classe por data-base, e a
data varia de 2015-10-02 a 2026-08-28. Uma taxa entregue em 2015 nao e a taxa de
hoje: acima de `_MAX_IDADE_MESES` a classe sai do indice, contada no bloco de
exclusao.

**A referencia e o universo de VAREJO, nao o universo inteiro.** Medido em
2026-09-20 sobre as 13.093 classes com taxa percentual e entrega recente: 47,2%
delas sao **exclusivas**, 21,1% cobram **taxa zero** e 1.368 sao de **publico
profissional**. As exclusivas sao veiculos de um cotista, que nao estao na
vitrine de ninguem; as de taxa zero sao masters e feeders cuja taxa e cobrada na
outra ponta da estrutura.

Com tudo dentro, a mediana de "Multimercados Livre" cai para 0,080% ao ano e
todo fundo de varejo aparece cobrando sete vezes a categoria - foi exatamente o
que a primeira execucao devolveu: indice mediano de **6,79x** e 82% da vitrine
do BTG marcada como "cara". Aplicando ao universo a mesma regra de varejo que
`profile.restriction_reason` ja aplica a vitrine, as medianas passam a 0,800% em
Multimercados Livre, 1,300% em Acoes Livre, 0,400% em Renda Fixa Duracao Livre
Credito Livre e 1,500% em Multimercados Macro - precos de prateleira
reconheciveis.

Um detalhe que a regra herda e nao inverte: **investidor qualificado nao e
motivo de corte** (`profile.py:12-13`). Cortar por qualificado levaria
Multimercados Livre a 1,000%, e seria uma decisao de produto diferente da que o
projeto ja tomou.

**Custo nao e ticket.** Taxa e propriedade da classe, identica em qualquer
distribuidor - por isso pode vir da base oficial. **Aplicacao minima nao**: o
minimo do registro e o institucional da classe, nao o que o site cobra, e
`competitive.vitrine_only_values` existe para impedir essa confusao. Aqui o
minimo oficial e usado para uma pergunta so, que e interna a base e nao envolve
o site: *existe outra classe do mesmo fundo com porta de entrada menor?*
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shelf_monitor.models import FundOffer, ShelfSnapshot
from shelf_monitor.normalize import normalize_cnpj, normalize_text, parse_number
from shelf_monitor.report.aggregate import _platform_label, categoria_comparavel
from shelf_monitor.report.competitive import identifiable_vitrines, vitrine_snapshots
from shelf_monitor.report.shared import (
    BARATO,
    CARO,
    MIN_AMOSTRA_CATEGORIA,
    MIN_COBERTURA_CUSTO,
)

# Acima disto a entrega e historico, nao condicao vigente. Dois anos cobrem
# classes que nao reentregam todo ano sem deixar passar a entrega de 2015.
_MAX_IDADE_MESES = 24

# Minimo de classes numa categoria para a mediana dela servir de referencia.
_MIN_AMOSTRA_CATEGORIA = MIN_AMOSTRA_CATEGORIA

# Acima deste indice o fundo cobra caro para a categoria dele; abaixo do outro,
# barato. A faixa no meio e ruido de arredondamento entre classes parecidas.
_CARO = CARO
_BARATO = BARATO

# Resgate ate D+1 e o que o varejo trata como liquidez imediata.
_LIQUIDEZ_IMEDIATA = 1

# Cobertura de taxa comparavel que a FONTE permite. Medido em 2026-09-20 sobre
# o universo com taxa: 13.154 classes com entrega de ate 24 meses contra 5.369
# entre 25 e 60 e 3.522 acima de 60. Cerca de 40% do mercado simplesmente nao
# reentrega o extrato, e exigir 50% de cobertura excluia toda casa - o numero
# passava a dizer "ninguem mensuravel" em vez de "custo da prateleira". O piso
# ficou onde a fonte permite, e a cobertura sai ao lado de todo indice.
_MIN_COBERTURA = MIN_COBERTURA_CUSTO


def _idade_meses(referencia: date | None, hoje: date | None = None) -> int | None:
    if referencia is None:
        return None
    hoje = hoje or date.today()
    return (hoje.year - referencia.year) * 12 + (hoje.month - referencia.month)


@dataclass(frozen=True)
class CustoClasse:
    """O que a base oficial sabe sobre o custo de uma classe."""

    cnpj: str
    categoria: str | None = None
    taxa_adm: float | None = None
    taxa_percentual: bool = False
    data_base: date | None = None
    tem_taxa_entrada: str | None = None
    tem_taxa_saida: str | None = None
    taxa_custodia_max: float | None = None
    dias_cotizacao: float | None = None
    id_fundo: str = ""
    minimo: float | None = None
    exclusivo: bool = False
    publico_alvo: str = ""

    @property
    def restrita(self) -> bool:
        """Classe que `profile.restriction_reason` tiraria da analise de varejo.

        Mesma regra do resto do monitor, aplicada do lado do universo: exclusiva
        ou de publico profissional sai; **qualificado nao** - a decisao esta em
        `profile.py:12-13` e nao deve ser desfeita aqui por conveniencia.
        """
        return self.exclusivo or "PROFISSIONAL" in self.publico_alvo

    @property
    def idade_meses(self) -> int | None:
        return _idade_meses(self.data_base)

    @property
    def vigente(self) -> bool:
        idade = self.idade_meses
        return idade is not None and idade <= _MAX_IDADE_MESES

    @property
    def comparavel(self) -> bool:
        """Entra no indice do proprio fundo: percentual, recente, com categoria."""
        return (
            self.taxa_percentual
            and self.taxa_adm is not None
            and self.vigente
            and bool(self.categoria)
        )

    @property
    def serve_de_referencia(self) -> bool:
        """Entra na MEDIANA da categoria: alem de comparavel, e produto de varejo.

        Classe exclusiva e veiculo de um cotista, e taxa zero e master ou feeder
        cobrando na outra ponta. Nenhuma das duas descreve o preco de um produto
        de prateleira - ver o docstring do modulo para o efeito medido.
        """
        return self.comparavel and not self.restrita and (self.taxa_adm or 0) > 0


@dataclass
class CustoFundo:
    """Um fundo da vitrine, com o custo dele contra os pares da categoria."""

    cnpj: str
    nome: str
    categoria: str | None
    taxa_adm: float | None
    mediana_categoria: float | None
    idade_meses: int | None
    tem_taxa_entrada: str | None
    tem_taxa_saida: str | None
    taxa_custodia_max: float | None
    dias_cotizacao: float | None
    minimo_classe: float | None = None
    minimo_do_fundo: float | None = None
    motivo_fora: str | None = None

    @property
    def indice(self) -> float | None:
        """Taxa do fundo sobre a mediana da categoria dele."""
        if self.taxa_adm is None or not self.mediana_categoria:
            return None
        return self.taxa_adm / self.mediana_categoria

    @property
    def faixa(self) -> str | None:
        indice = self.indice
        if indice is None:
            return None
        if indice >= _CARO:
            return "caro para a categoria"
        if indice <= _BARATO:
            return "barato para a categoria"
        return "na faixa da categoria"

    @property
    def porta_menor(self) -> float | None:
        """Quanto menor e a menor classe do mesmo fundo, quando existe.

        Nao e comparacao entre casas: e a mesma base olhando para si mesma. A
        leitura util seria "este fundo tem classe de entrada mais barata que a
        exibida", que resolveria sem produto novo.

        **A fonte quase nao sustenta esta pergunta, e o numero medido diz isso.**
        Das 22.027 estruturas com aplicacao minima no cadastro de 2026-09-20,
        apenas 13 tem duas ou mais classes com minimo e **5** tem minimos
        diferentes entre elas. O lugar onde as condicoes comerciais de fato
        variam e a SUBCLASSE - e `subclasses.csv` nao traz minimo nem taxa: o
        extrato e entregue por CNPJ de classe, e a subclasse partilha o CNPJ da
        classe-mae. Entao a coluna sai em zero quase sempre, e isso e um fato
        sobre o cadastro, nao um bug do calculo.
        """
        if self.minimo_classe is None or self.minimo_do_fundo is None:
            return None
        if self.minimo_do_fundo >= self.minimo_classe:
            return None
        return self.minimo_do_fundo

    @property
    def liquidez_imediata(self) -> bool | None:
        if self.dias_cotizacao is None:
            return None
        return self.dias_cotizacao <= _LIQUIDEZ_IMEDIATA

    def to_dict(self) -> dict:
        return {
            "cnpj": self.cnpj,
            "nome": self.nome,
            "categoria": self.categoria,
            "taxa_adm": self.taxa_adm,
            "mediana_categoria": self.mediana_categoria,
            "indice": None if self.indice is None else round(self.indice, 3),
            "faixa": self.faixa,
            "idade_meses": self.idade_meses,
            "tem_taxa_entrada": self.tem_taxa_entrada,
            "tem_taxa_saida": self.tem_taxa_saida,
            "taxa_custodia_max": self.taxa_custodia_max,
            "dias_cotizacao": self.dias_cotizacao,
            "liquidez_imediata": self.liquidez_imediata,
            "minimo_classe": self.minimo_classe,
            "porta_menor": self.porta_menor,
            "motivo_fora": self.motivo_fora,
        }


class IndiceCusto:
    """Custo por CNPJ e mediana por categoria, a partir do `mercado.csv`."""

    def __init__(self, classes: dict[str, CustoClasse]) -> None:
        self._classes = classes
        comparaveis: dict[str, list[float]] = {}
        por_fundo: dict[str, list[float]] = {}
        for classe in classes.values():
            if classe.serve_de_referencia:
                comparaveis.setdefault(classe.categoria, []).append(classe.taxa_adm)
            if classe.id_fundo and classe.minimo is not None:
                por_fundo.setdefault(classe.id_fundo, []).append(classe.minimo)
        self.medianas = {
            categoria: statistics.median(valores)
            for categoria, valores in comparaveis.items()
            if len(valores) >= _MIN_AMOSTRA_CATEGORIA
        }
        self.amostras = {c: len(v) for c, v in comparaveis.items()}
        self._minimo_do_fundo = {f: min(v) for f, v in por_fundo.items()}

    def __len__(self) -> int:
        return len(self._classes)

    def get(self, cnpj: str | None) -> CustoClasse | None:
        return self._classes.get(cnpj) if cnpj else None

    def minimo_do_fundo(self, id_fundo: str) -> float | None:
        return self._minimo_do_fundo.get(id_fundo)

    @classmethod
    def load(cls, market_dir: Path) -> IndiceCusto | None:
        caminho = market_dir / "mercado.csv"
        if not caminho.is_file():
            return None

        classes: dict[str, CustoClasse] = {}
        with caminho.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=";"):
                cnpj = normalize_cnpj(row.get("cnpj"))
                if not cnpj:
                    continue
                categoria = (row.get("classificacao_anbima") or "").strip() or (
                    row.get("classificacao_cvm") or ""
                ).strip()
                unidade = normalize_text(row.get("taxa_adm_unidade"))
                classes[cnpj] = CustoClasse(
                    cnpj=cnpj,
                    categoria=categoria or None,
                    taxa_adm=parse_number(row.get("taxa_adm")),
                    taxa_percentual=unidade.startswith("PERCENTUAL"),
                    data_base=_data(row.get("taxa_data_base")),
                    tem_taxa_entrada=(row.get("tem_taxa_entrada") or "").strip() or None,
                    tem_taxa_saida=(row.get("tem_taxa_saida") or "").strip() or None,
                    taxa_custodia_max=parse_number(row.get("taxa_custodia_max")),
                    dias_cotizacao=parse_number(row.get("dias_cotizacao_resgate")),
                    id_fundo=(row.get("id_fundo") or "").strip(),
                    minimo=parse_number(row.get("aplicacao_inicial_minima")),
                    exclusivo=normalize_text(row.get("exclusivo")).startswith("S"),
                    publico_alvo=normalize_text(row.get("publico_alvo")),
                )
        return cls(classes) if classes else None

    def avaliar(self, offer: FundOffer) -> CustoFundo:
        """Custo de um fundo da vitrine, com o motivo quando nao da para medir."""
        classe = self.get(offer.cnpj)
        categoria = categoria_comparavel(offer) or (classe.categoria if classe else None)
        if classe is None:
            return CustoFundo(
                cnpj=offer.cnpj or "", nome=offer.name, categoria=categoria,
                taxa_adm=None, mediana_categoria=None, idade_meses=None,
                tem_taxa_entrada=None, tem_taxa_saida=None, taxa_custodia_max=None,
                dias_cotizacao=None,
                motivo_fora="fundo nao encontrado na base oficial por CNPJ",
            )

        motivo = None
        if classe.taxa_adm is None:
            motivo = "classe sem taxa publicada no extrato"
        elif (classe.taxa_adm or 0) <= 0:
            motivo = (
                "taxa zero no extrato - master ou feeder que cobra na outra "
                "ponta da estrutura"
            )
        elif not classe.taxa_percentual:
            motivo = "taxa em reais ou texto livre - nao comparavel com percentual"
        elif not classe.vigente:
            # Motivo agrupavel: a idade exata vai no campo `idade_meses` do
            # fundo. Colocando-a no texto, cada fundo virava uma chave propria
            # e o resumo "por motivo" deixava de resumir.
            motivo = (
                f"entrega do extrato com mais de {_MAX_IDADE_MESES} meses"
                if classe.idade_meses is not None
                else "entrega do extrato sem data-base"
            )
        elif not categoria:
            motivo = "fundo sem categoria oficial"
        elif categoria not in self.medianas:
            motivo = (
                f"categoria com menos de {_MIN_AMOSTRA_CATEGORIA} classes "
                "comparaveis no mercado - sem referencia para comparar"
            )

        return CustoFundo(
            cnpj=classe.cnpj,
            nome=offer.name,
            categoria=categoria,
            taxa_adm=classe.taxa_adm if classe.taxa_percentual else None,
            mediana_categoria=None if motivo else self.medianas.get(categoria),
            idade_meses=classe.idade_meses,
            tem_taxa_entrada=classe.tem_taxa_entrada,
            tem_taxa_saida=classe.tem_taxa_saida,
            taxa_custodia_max=classe.taxa_custodia_max,
            dias_cotizacao=classe.dias_cotizacao,
            minimo_classe=classe.minimo,
            minimo_do_fundo=self.minimo_do_fundo(classe.id_fundo),
            motivo_fora=motivo,
        )


def _data(texto: str | None) -> date | None:
    try:
        return date.fromisoformat((texto or "").strip()[:10])
    except ValueError:
        return None


@dataclass
class CustoCasa:
    """O custo da vitrine de uma casa, medido contra as categorias dela."""

    platform_id: str
    label: str
    vitrine: int
    fundos: list[CustoFundo] = field(default_factory=list)

    @property
    def comparaveis(self) -> list[CustoFundo]:
        return [f for f in self.fundos if f.indice is not None]

    @property
    def cobertura(self) -> float:
        return len(self.comparaveis) / self.vitrine if self.vitrine else 0.0

    @property
    def indice_mediano(self) -> float | None:
        """Mediana dos indices, nao das taxas.

        E o que remove o efeito de composicao: cada fundo ja foi comparado com a
        propria categoria antes de a casa ser resumida num numero.
        """
        indices = [f.indice for f in self.comparaveis]
        return statistics.median(indices) if indices else None

    def _share(self, selecao, base: list[CustoFundo] | None = None) -> float | None:
        universo = base if base is not None else self.fundos
        conhecidos = [f for f in universo if selecao(f) is not None]
        if not conhecidos:
            return None
        return sum(1 for f in conhecidos if selecao(f)) / len(conhecidos)

    @property
    def share_caro(self) -> float | None:
        comparaveis = self.comparaveis
        if not comparaveis:
            return None
        return sum(1 for f in comparaveis if f.indice >= _CARO) / len(comparaveis)

    @property
    def share_barato(self) -> float | None:
        comparaveis = self.comparaveis
        if not comparaveis:
            return None
        return sum(1 for f in comparaveis if f.indice <= _BARATO) / len(comparaveis)

    @property
    def share_sem_entrada_ou_saida(self) -> float | None:
        def livre(f: CustoFundo) -> bool | None:
            if f.tem_taxa_entrada is None or f.tem_taxa_saida is None:
                return None
            return f.tem_taxa_entrada != "S" and f.tem_taxa_saida != "S"

        return self._share(livre)

    @property
    def share_liquidez_imediata(self) -> float | None:
        return self._share(lambda f: f.liquidez_imediata)

    @property
    def com_porta_menor(self) -> list[CustoFundo]:
        return sorted(
            [f for f in self.fundos if f.porta_menor is not None],
            key=lambda f: -(f.minimo_classe or 0),
        )

    def to_dict(self) -> dict:
        def r(v: float | None, casas: int = 3) -> float | None:
            return None if v is None else round(v, casas)

        return {
            "platform_id": self.platform_id,
            "label": self.label,
            "vitrine": self.vitrine,
            "comparaveis": len(self.comparaveis),
            "cobertura": r(self.cobertura),
            "indice_mediano": r(self.indice_mediano),
            "share_caro": r(self.share_caro, 4),
            "share_barato": r(self.share_barato, 4),
            "share_sem_entrada_ou_saida": r(self.share_sem_entrada_ou_saida, 4),
            "share_liquidez_imediata": r(self.share_liquidez_imediata, 4),
            "com_porta_menor": len(self.com_porta_menor),
        }


def build_custo(config, snapshots: list[ShelfSnapshot]) -> dict:
    """Custo da vitrine de cada casa, relativo a categoria de cada fundo."""
    indice = IndiceCusto.load(config.market_dir)
    if indice is None:
        return {
            "available": False,
            "reason": (
                "base de mercado nao importada - o custo comparavel sai do "
                "extrato da CVM, nao do que a casa publica"
            ),
            "excluded": [],
        }

    vitrines, fora = vitrine_snapshots(snapshots)
    cruzaveis, sem_identidade = identifiable_vitrines(vitrines)
    sem_cnpj = [
        {**item, "reason": (
            "sem CNPJ na vitrine nao ha como buscar a taxa da classe no extrato "
            f"({item['reason']})"
        )}
        for item in sem_identidade
    ]
    if not cruzaveis:
        return {
            "available": False,
            "reason": "nenhuma vitrine publica CNPJ o bastante para buscar o extrato",
            "excluded": fora + sem_cnpj,
        }

    casas: list[CustoCasa] = []
    sem_amostra: list[dict] = []
    for platform_id, funds in cruzaveis.items():
        casa = CustoCasa(
            platform_id=platform_id,
            label=_platform_label(platform_id),
            vitrine=len(funds),
            fundos=[indice.avaliar(f) for f in funds],
        )
        if casa.cobertura < _MIN_COBERTURA:
            sem_amostra.append({
                "platform_id": platform_id,
                "label": casa.label,
                "reason": (
                    f"apenas {len(casa.comparaveis)} de {casa.vitrine} fundos com "
                    "taxa comparavel (percentual, entrega recente e categoria com "
                    "referencia) - o indice descreveria a amostra, nao a vitrine"
                ),
            })
            continue
        casas.append(casa)

    if not casas:
        return {
            "available": False,
            "reason": "nenhuma vitrine com cobertura suficiente de taxa comparavel",
            "excluded": fora + sem_cnpj + sem_amostra,
        }

    motivos: dict[str, int] = {}
    for casa in casas:
        for fundo in casa.fundos:
            if fundo.motivo_fora:
                motivos[fundo.motivo_fora] = motivos.get(fundo.motivo_fora, 0) + 1

    return {
        "available": True,
        "participantes": [c.platform_id for c in casas],
        "excluded": fora + sem_cnpj + sem_amostra,
        "universo": {
            "classes_na_base": len(indice),
            "categorias_com_referencia": len(indice.medianas),
            "amostra_minima": _MIN_AMOSTRA_CATEGORIA,
            "idade_maxima_meses": _MAX_IDADE_MESES,
        },
        "motivos_fora": dict(sorted(motivos.items(), key=lambda kv: -kv[1])),
        "casas": [c.to_dict() for c in casas],
        "mais_caros": [
            f.to_dict()
            for f in sorted(
                (f for c in casas for f in c.comparaveis), key=lambda f: -f.indice
            )[:20]
        ],
        "porta_menor": {
            c.platform_id: [f.to_dict() for f in c.com_porta_menor[:15]] for c in casas
        },
        "_casas": casas,
    }


# --- renderizacao no console --------------------------------------------------


def print_custo(resultado: dict) -> None:
    """Imprime o indice de custo por casa, com quem ficou de fora e por que."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print()
    console.print(
        "[bold]Custo da vitrine[/] - taxa de cada fundo contra a mediana da "
        "categoria dele"
    )
    if not resultado.get("available"):
        console.print(f"   [yellow]{resultado.get('reason')}[/]")
        for e in resultado.get("excluded", []):
            console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
        return

    universo = resultado["universo"]
    console.print(
        f"   [dim]{universo['classes_na_base']} classes na base, "
        f"{universo['categorias_com_referencia']} categorias com amostra "
        f">= {universo['amostra_minima']}. Entram so taxas em percentual ao ano "
        f"com entrega de ate {universo['idade_maxima_meses']} meses.[/]"
    )

    def pct(valor: float | None) -> str:
        return "-" if valor is None else f"{valor:.0%}"

    table = Table(title="Indice de custo (1,00 = mediana da categoria)", title_style="bold")
    table.add_column("Casa")
    table.add_column("Comparaveis", justify="right")
    table.add_column("Indice mediano", justify="right")
    table.add_column("Caros", justify="right")
    table.add_column("Baratos", justify="right")
    table.add_column("Sem entrada/saida", justify="right")
    table.add_column("Resgate <= D+1", justify="right")
    table.add_column("Com porta menor", justify="right")
    for casa in resultado["casas"]:
        indice = casa["indice_mediano"]
        table.add_row(
            casa["label"],
            f"{casa['comparaveis']}/{casa['vitrine']}",
            "-" if indice is None else f"{indice:.2f}x",
            pct(casa["share_caro"]),
            pct(casa["share_barato"]),
            pct(casa["share_sem_entrada_ou_saida"]),
            pct(casa["share_liquidez_imediata"]),
            str(casa["com_porta_menor"]),
        )
    console.print(table)
    console.print(
        "   [dim]Indice mediano, nao taxa mediana: comparar a taxa bruta entre "
        "casas mede o MIX da prateleira, nao o preco dos produtos.[/]"
    )
    if resultado.get("motivos_fora"):
        console.print("   [dim]fundos fora do indice, por motivo:[/]")
        for motivo, quantos in resultado["motivos_fora"].items():
            console.print(f"      [dim]{quantos:5d}  {motivo}[/]")
    for e in resultado.get("excluded", []):
        console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
