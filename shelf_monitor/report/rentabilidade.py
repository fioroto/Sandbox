"""Rentabilidade por fundo em janelas: 1 mes, 3 meses, YTD, 12 e 24 meses.

O bloco de `performance` responde pela vitrine inteira numa janela so. Este
responde **fundo a fundo, em varias janelas** - que e a pergunta que se faz
olhando uma linha da planilha: *este fundo rendeu quanto no ano? e em doze
meses?*

Como a serie e montada
----------------------

O informe diario da CVM sai num zip por competencia. Ler 24 competencias inteiras
a cada execucao custa minutos, entao o modulo guarda um **resumo mensal por
subclasse** em `data/cache/serie/<AAAAMM>.csv`: primeira e ultima cota do mes, PL
e cotistas do ultimo dia, fluxo somado e quantos pregoes distintos apareceram.
Competencia ja resumida nao e relida; a corrente e sempre refeita, porque ela
muda todo dia.

O que uma janela significa aqui
-------------------------------

**A base e a cota do fim do mes ANTERIOR a janela**, nao a do primeiro dia dela.
"12 meses ate setembro" compara a cota de 30/09 com a de 30/09 do ano passado -
usar a cota de 01/10 como base perderia o primeiro pregao e daria um numero
ligeiramente diferente do que qualquer outra fonte publica. Quando o mes anterior
nao existe na serie (comeco do historico, ou fundo que nasceu depois), a base
passa a ser a primeira cota observada dentro da janela, e o campo `meses`
registra sobre quantas competencias o numero foi de fato calculado.

**O fundo precisa ter sido observado em quase toda a janela** (ver
`_MIN_COBERTURA`). Sem isso, um fundo que so aparece nos dois ultimos meses
entraria com retorno de duas competencias ao lado de outro com doze, e qualquer
media entre os dois somaria periodos diferentes.

**Uma linha do informe e uma subclasse.** O retorno da classe e a media dos
retornos das subclasses ponderada pelo PL de cada uma - ver
`retail_flow.agrupar_por_classe` para o efeito medido de ignorar isso.

Tres coisas que o numero nao e
------------------------------

**Nao e retorno total.** A cota patrimonial e ex-provento: fundo que distribui
aparece abaixo do que o investidor recebeu. Reincorporar exigiria uma fonte de
proventos que nao esta implementada.

**Nao e o retorno do cliente da casa.** PL e cotistas sao do fundo inteiro,
somando todos os distribuidores.

**Nao e comparavel entre janelas de tamanhos diferentes sem anualizar.** 28% em
24 meses e menos, ao ano, que 12% em 12 - e o modulo publica o numero do periodo,
nao o anualizado, porque e o que a planilha precisa exibir ao lado da captacao do
mesmo periodo.

Vies de sobrevivencia
---------------------

Quanto mais longa a janela, mais fundos ficam de fora por nao existirem no
comeco dela. Medido em 2026-09-20 na vitrine da XP: 570 fundos em 3 meses contra
**507** em 24. Os que sumiram no caminho nao aparecem, entao o agregado de 24
meses e de sobreviventes e esta para cima. O campo `fundos` de cada janela existe
para que essa diferenca seja visivel em vez de suposta.
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.models import ShelfSnapshot
from shelf_monitor.normalize import parse_number
from shelf_monitor.report.aggregate import _platform_label, categoria_comparavel
from shelf_monitor.report.competitive import identifiable_vitrines, vitrine_snapshots
from shelf_monitor.report.retail_flow import months_back, read_daily_flows

# Fracao das competencias da janela em que a subclasse precisa aparecer. Ver o
# docstring: abaixo disso a media somaria periodos de tamanhos diferentes.
_MIN_COBERTURA = 0.8

# Janelas publicadas, na ordem em que saem. YTD e resolvido em tempo de execucao
# porque depende do ano corrente.
JANELAS: tuple[tuple[str, int | None], ...] = (
    ("1 mes", 1),
    ("3 meses", 3),
    ("YTD", None),
    ("12 meses", 12),
    ("24 meses", 24),
)

_CABECALHO_CACHE = (
    "cnpj;subclasse;dia_ini;cota_ini;dia_fim;cota_fim;pl_fim;cotistas_fim;"
    "captacao;resgates;pregoes"
)


@dataclass
class MesSubclasse:
    """Resumo de uma subclasse numa competencia."""

    cota_ini: float = 0.0
    cota_fim: float = 0.0
    pl_fim: float = 0.0
    cotistas_fim: int = 0
    captacao: float = 0.0
    resgates: float = 0.0
    pregoes: int = 0


@dataclass
class RetornoJanela:
    """O retorno de uma classe numa janela, com o que sustenta o numero."""

    retorno: float
    pl: float
    cotistas: int
    subclasses: int
    meses: int
    completa: bool

    def to_dict(self) -> dict:
        return {
            "retorno": round(self.retorno, 4),
            "pl": round(self.pl, 2),
            "cotistas": self.cotistas,
            "subclasses": self.subclasses,
            "meses": self.meses,
            "completa": self.completa,
        }


class SerieMensal:
    """Resumo mensal por subclasse, com cache em disco por competencia."""

    def __init__(self, meses: list[str], dados: dict[tuple[str, str], dict[str, MesSubclasse]]):
        self.meses = meses
        self._dados = dados
        self._indice = {m: i for i, m in enumerate(meses)}
        self._por_cnpj: dict[str, list[tuple[str, str]]] = {}
        for chave in dados:
            self._por_cnpj.setdefault(chave[0], []).append(chave)

    def __len__(self) -> int:
        return len(self._por_cnpj)

    @property
    def periodo(self) -> tuple[str, str] | None:
        return (self.meses[0], self.meses[-1]) if self.meses else None

    # --- construcao -----------------------------------------------------------

    @staticmethod
    def _cache_path(config: Config, yyyymm: str) -> Path:
        return config.cache_dir / "serie" / f"{yyyymm}.csv"

    @classmethod
    def _resumir(cls, caminho: Path) -> dict[tuple[str, str], MesSubclasse]:
        """Le uma competencia inteira e devolve o resumo por subclasse."""
        resumo: dict[tuple[str, str], MesSubclasse] = {}
        primeiro: dict[tuple[str, str], date] = {}
        ultimo: dict[tuple[str, str], date] = {}
        dias: dict[tuple[str, str], set[date]] = {}
        for registro in read_daily_flows(caminho, None):
            chave = (registro.cnpj, registro.subclasse)
            item = resumo.get(chave)
            if item is None:
                item = resumo[chave] = MesSubclasse()
                primeiro[chave] = ultimo[chave] = registro.dia
                dias[chave] = set()
                item.cota_ini = registro.cota
            if registro.dia < primeiro[chave]:
                primeiro[chave] = registro.dia
                item.cota_ini = registro.cota
            if registro.dia >= ultimo[chave]:
                ultimo[chave] = registro.dia
                item.cota_fim = registro.cota
                item.pl_fim = registro.pl
                item.cotistas_fim = registro.cotistas
            item.captacao += registro.captacao
            item.resgates += registro.resgates
            dias[chave].add(registro.dia)
        for chave, item in resumo.items():
            item.pregoes = len(dias[chave])
        return resumo

    @classmethod
    def _gravar_cache(
        cls, caminho: Path, resumo: dict[tuple[str, str], MesSubclasse]
    ) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with caminho.open("w", encoding="utf-8", newline="") as handle:
            handle.write(_CABECALHO_CACHE + "\n")
            writer = csv.writer(handle, delimiter=";")
            for (cnpj, sub), m in resumo.items():
                writer.writerow([
                    cnpj, sub, "", f"{m.cota_ini:.12f}", "", f"{m.cota_fim:.12f}",
                    f"{m.pl_fim:.2f}", m.cotistas_fim,
                    f"{m.captacao:.2f}", f"{m.resgates:.2f}", m.pregoes,
                ])

    @classmethod
    def _ler_cache(cls, caminho: Path) -> dict[tuple[str, str], MesSubclasse] | None:
        if not caminho.is_file():
            return None
        resumo: dict[tuple[str, str], MesSubclasse] = {}
        with caminho.open(encoding="utf-8", newline="") as handle:
            primeira = handle.readline().strip()
            if primeira != _CABECALHO_CACHE:
                # Cache de uma versao anterior do resumo: refazer e mais barato
                # que adivinhar o que mudou.
                return None
            for linha in csv.reader(handle, delimiter=";"):
                if len(linha) < 11:
                    continue
                resumo[(linha[0], linha[1])] = MesSubclasse(
                    cota_ini=parse_number(linha[3]) or 0.0,
                    cota_fim=parse_number(linha[5]) or 0.0,
                    pl_fim=parse_number(linha[6]) or 0.0,
                    cotistas_fim=int(parse_number(linha[7]) or 0),
                    captacao=parse_number(linha[8]) or 0.0,
                    resgates=parse_number(linha[9]) or 0.0,
                    pregoes=int(parse_number(linha[10]) or 0),
                )
        return resumo or None

    @classmethod
    def build(
        cls,
        config: Config,
        meses: int = 24,
        force: bool = False,
        progresso=None,
    ) -> SerieMensal:
        """Monta a serie, reusando o cache por competencia quando existe."""
        from shelf_monitor.market.etf_flow import fetch_inf_diario_month

        competencias = months_back(meses)
        dados: dict[tuple[str, str], dict[str, MesSubclasse]] = {}
        lidas: list[str] = []
        for i, yyyymm in enumerate(competencias):
            # A competencia corrente muda todo dia: o cache dela nao vale.
            corrente = i == len(competencias) - 1
            cache = cls._cache_path(config, yyyymm)
            resumo = None if (force or corrente) else cls._ler_cache(cache)
            if resumo is None:
                caminho = fetch_inf_diario_month(config, yyyymm, force=corrente)
                if caminho is None:
                    if progresso:
                        progresso(yyyymm, None)
                    continue
                resumo = cls._resumir(caminho)
                if resumo:
                    cls._gravar_cache(cache, resumo)
            if not resumo:
                if progresso:
                    progresso(yyyymm, None)
                continue
            for chave, item in resumo.items():
                dados.setdefault(chave, {})[yyyymm] = item
            lidas.append(yyyymm)
            if progresso:
                progresso(yyyymm, len(resumo))
        return cls(lidas, dados)

    # --- leitura --------------------------------------------------------------

    def janela(self, nome: str, tamanho: int | None) -> list[str]:
        """Competencias de uma janela nomeada, dentro do que a serie tem."""
        if not self.meses:
            return []
        if tamanho is None:  # YTD: do inicio do ano da ultima competencia
            ano = self.meses[-1][:4]
            return [m for m in self.meses if m.startswith(ano)]
        return self.meses[-tamanho:]

    def retorno(self, cnpj: str, competencias: list[str]) -> RetornoJanela | None:
        """Retorno da classe na janela: subclasses ponderadas pelo PL."""
        if not competencias:
            return None
        inicio = competencias[0]
        posicao = self._indice.get(inicio)
        anterior = self.meses[posicao - 1] if posicao else None
        minimo = max(1, int(len(competencias) * _MIN_COBERTURA))

        retornos: list[tuple[float, float]] = []
        pl = 0.0
        cotistas = 0
        observados: set[str] = set()
        for chave in self._por_cnpj.get(cnpj, ()):
            serie = self._dados[chave]
            presentes = [m for m in competencias if m in serie]
            if len(presentes) < minimo:
                continue
            base = 0.0
            if anterior and anterior in serie and serie[anterior].cota_fim > 0:
                base = serie[anterior].cota_fim
            elif serie[presentes[0]].cota_ini > 0:
                base = serie[presentes[0]].cota_ini
            fim = serie[presentes[-1]].cota_fim
            if base <= 0 or fim <= 0:
                continue
            retornos.append(((fim / base - 1.0) * 100.0, max(serie[presentes[-1]].pl_fim, 0.0)))
            pl += serie[presentes[-1]].pl_fim
            cotistas += serie[presentes[-1]].cotistas_fim
            observados.update(presentes)

        if not retornos:
            return None
        peso = sum(p for _, p in retornos)
        valor = (
            sum(r * p for r, p in retornos) / peso
            if peso > 0
            else statistics.mean(r for r, _ in retornos)
        )
        return RetornoJanela(
            retorno=valor,
            pl=pl,
            cotistas=cotistas,
            subclasses=len(retornos),
            meses=len(observados),
            completa=len(observados) >= len(competencias),
        )


@dataclass
class FundoRentabilidade:
    """Um fundo da vitrine, com o retorno em cada janela."""

    cnpj: str
    nome: str
    categoria: str | None
    gestora: str | None
    vitrines: list[str] = field(default_factory=list)
    janelas: dict[str, RetornoJanela] = field(default_factory=dict)

    @property
    def pl(self) -> float | None:
        for nome, _ in JANELAS:
            if nome in self.janelas:
                return self.janelas[nome].pl
        return None

    @property
    def cotistas(self) -> int | None:
        for nome, _ in JANELAS:
            if nome in self.janelas:
                return self.janelas[nome].cotistas
        return None

    def to_dict(self) -> dict:
        return {
            "cnpj": self.cnpj,
            "nome": self.nome,
            "categoria": self.categoria,
            "gestora": self.gestora,
            "vitrines": self.vitrines,
            "pl": self.pl,
            "cotistas": self.cotistas,
            "janelas": {k: v.to_dict() for k, v in self.janelas.items()},
        }


def _agregar(fundos: list[FundoRentabilidade], nome: str) -> dict | None:
    """Mediana e ponderadas por PL e por cotistas, numa janela."""
    valores = [(f.janelas[nome], f) for f in fundos if nome in f.janelas]
    if not valores:
        return None
    pl_total = sum(j.pl for j, _ in valores)
    cot_total = sum(j.cotistas for j, _ in valores)
    por_pl = (
        sum(j.retorno * max(j.pl, 0.0) for j, _ in valores) / pl_total
        if pl_total > 0 else None
    )
    por_cot = (
        sum(j.retorno * max(j.cotistas, 0) for j, _ in valores) / cot_total
        if cot_total > 0 else None
    )
    return {
        "fundos": len(valores),
        "mediana": round(statistics.median(j.retorno for j, _ in valores), 4),
        "por_pl": None if por_pl is None else round(por_pl, 4),
        "por_cotista": None if por_cot is None else round(por_cot, 4),
        "gap": (
            None if (por_pl is None or por_cot is None) else round(por_pl - por_cot, 4)
        ),
        "pl": round(pl_total, 2),
        "cotistas": cot_total,
    }


def build_rentabilidade(
    config: Config,
    snapshots: list[ShelfSnapshot],
    meses: int = 24,
    force_download: bool = False,
    progresso=None,
) -> dict:
    """Rentabilidade por fundo e por casa, nas janelas de `JANELAS`."""
    vitrines, fora = vitrine_snapshots(snapshots)
    cruzaveis, sem_identidade = identifiable_vitrines(vitrines)
    sem_cnpj = [
        {**item, "reason": (
            "sem CNPJ na vitrine nao ha como buscar a serie de cota no informe "
            f"diario ({item['reason']})"
        )}
        for item in sem_identidade
    ]
    if not cruzaveis:
        return {
            "available": False,
            "reason": (
                "nenhuma vitrine publica CNPJ o bastante para buscar a serie de cota"
            ),
            "excluded": fora + sem_cnpj,
        }

    serie = SerieMensal.build(config, meses=meses, force=force_download, progresso=progresso)
    if not serie.meses:
        return {
            "available": False,
            "reason": (
                "nenhuma competencia do informe diario disponivel - rode com a "
                "rede liberada para dados.cvm.gov.br"
            ),
            "excluded": fora + sem_cnpj,
        }

    janelas = [(nome, serie.janela(nome, tam)) for nome, tam in JANELAS]
    janelas = [(nome, comps) for nome, comps in janelas if comps]

    from shelf_monitor.report.aggregate import _offer_manager

    catalogo: dict[str, tuple] = {}
    presenca: dict[str, list[str]] = {}
    for platform_id, funds in cruzaveis.items():
        for fund in funds:
            if not fund.cnpj:
                continue
            catalogo.setdefault(
                fund.cnpj, (fund.name, categoria_comparavel(fund), _offer_manager(fund))
            )
            presenca.setdefault(fund.cnpj, []).append(platform_id)

    fundos: list[FundoRentabilidade] = []
    for cnpj, (nome, categoria, gestora) in catalogo.items():
        item = FundoRentabilidade(
            cnpj=cnpj, nome=nome, categoria=categoria, gestora=gestora,
            vitrines=presenca.get(cnpj, []),
        )
        for rotulo, comps in janelas:
            resultado = serie.retorno(cnpj, comps)
            if resultado:
                item.janelas[rotulo] = resultado
        if item.janelas:
            fundos.append(item)

    por_casa: dict[str, dict] = {}
    for platform_id, funds in cruzaveis.items():
        cnpjs = {f.cnpj for f in funds if f.cnpj}
        da_casa = [f for f in fundos if f.cnpj in cnpjs]
        if not da_casa:
            continue
        por_casa[platform_id] = {
            "platform_id": platform_id,
            "label": _platform_label(platform_id),
            "vitrine": len(funds),
            "com_serie": len(da_casa),
            "janelas": {
                rotulo: _agregar(da_casa, rotulo) for rotulo, _ in janelas
            },
        }

    return {
        "available": bool(fundos),
        "reason": None if fundos else "nenhum fundo de vitrine com serie utilizavel",
        "periodo": {
            "de": serie.meses[0],
            "ate": serie.meses[-1],
            "competencias": len(serie.meses),
        },
        "janelas": [
            {
                "rotulo": rotulo,
                "de": comps[0],
                "ate": comps[-1],
                "competencias": len(comps),
            }
            for rotulo, comps in janelas
        ],
        "participantes": list(por_casa),
        "excluded": fora + sem_cnpj,
        "universo": {"cnpjs_na_serie": len(serie)},
        "casas": list(por_casa.values()),
        "fundos": [f.to_dict() for f in sorted(fundos, key=lambda f: -(f.pl or 0))],
        "_fundos": fundos,
        "_serie": serie,
    }


# --- renderizacao no console --------------------------------------------------


def print_rentabilidade(resultado: dict) -> None:
    """Imprime o resumo por casa e janela, com o periodo de cada uma."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print()
    console.print("[bold]Rentabilidade por janela[/] - cota patrimonial, ex-provento")
    if not resultado.get("available"):
        console.print(f"   [yellow]{resultado.get('reason')}[/]")
        for e in resultado.get("excluded", []):
            console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
        return

    def comp(texto: str) -> str:
        return f"{texto[4:]}/{texto[:4]}"

    console.print(
        "   [dim]"
        + " · ".join(
            f"{j['rotulo']}: {comp(j['de'])} a {comp(j['ate'])} "
            f"({j['competencias']} {'mes' if j['competencias'] == 1 else 'meses'})"
            for j in resultado["janelas"]
        )
        + "[/]"
    )

    for casa in resultado["casas"]:
        table = Table(
            title=f"{casa['label']} - {casa['com_serie']} de {casa['vitrine']} fundos com serie",
            title_style="bold",
        )
        table.add_column("Janela")
        table.add_column("Fundos", justify="right")
        table.add_column("Mediana", justify="right")
        table.add_column("Por PL", justify="right")
        table.add_column("Por cotista", justify="right")
        table.add_column("Gap", justify="right")
        for rotulo, dados in casa["janelas"].items():
            if not dados:
                continue
            table.add_row(
                rotulo, str(dados["fundos"]),
                f"{dados['mediana']:.2f}%",
                "-" if dados["por_pl"] is None else f"{dados['por_pl']:.2f}%",
                "-" if dados["por_cotista"] is None else f"{dados['por_cotista']:.2f}%",
                "-" if dados["gap"] is None else f"{dados['gap']:+.2f}pp",
            )
        console.print(table)
    console.print(
        "   [dim]Janela mais longa tem menos fundos: quem nao existia no comeco dela "
        "fica de fora, entao o agregado longo e de sobreviventes.[/]"
    )
    for e in resultado.get("excluded", []):
        console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
