"""Rentabilidade por fundo em janelas: 1 mes, 3 meses, YTD, 12 e 24 meses.

O bloco de `performance` responde pela vitrine inteira numa janela so. Este
responde **fundo a fundo, em varias janelas** - que e a pergunta que se faz
olhando uma linha da planilha: *este fundo rendeu quanto no ano? e em doze
meses?*

Como a serie e montada
----------------------

O informe diario da CVM sai num zip por competencia. Ler 24 competencias inteiras
a cada execucao custa minutos, entao o modulo guarda um **resumo mensal por
subclasse** em `data/cache/serie/<AAAAMM>.csv`: primeira e ultima cota do mes, o
dia de cada uma, PL e cotistas do ultimo dia, fluxo somado e quantos pregoes
distintos apareceram. Competencia ja resumida nao e relida; a corrente e sempre
refeita, porque ela muda todo dia.

Os dias entraram no cache em 2026-09-24, para a janela poder dizer "de
30/09/2025 a 23/09/2026" em vez de "12 competencias". Cache gravado antes fica
com os dias vazios e continua valendo para o retorno; quando uma janela precisa
da data do fechamento de uma competencia assim, `garantir_datas` refaz so ela a
partir do zip local.

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
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.models import ShelfSnapshot
from shelf_monitor.report.aggregate import _platform_label, categoria_comparavel
from shelf_monitor.report.competitive import identifiable_vitrines, vitrine_snapshots
from shelf_monitor.report.retail_flow import months_back
from shelf_monitor.report.shared import (
    MIN_COBERTURA_JANELA,
    ler_competencia,
)
from shelf_monitor.report.shared.informe import ler_sem_guardar

# Fracao das competencias da janela em que a subclasse precisa aparecer. Ver o
# docstring: abaixo disso a media somaria periodos de tamanhos diferentes.
_MIN_COBERTURA = MIN_COBERTURA_JANELA

# Janelas publicadas, na ordem em que saem. YTD e resolvido em tempo de execucao
# porque depende do ano corrente.
JANELAS: tuple[tuple[str, int | None], ...] = (
    ("1 mês", 1),
    ("3 meses", 3),
    ("YTD", None),
    ("12 meses", 12),
    ("24 meses", 24),
)

# O cabecalho e tambem a versao do cache. A coluna final `retificacao` marca o
# resumo que deduplica as linhas que a CVM retifica (ver `_resumir`); o gravado
# antes de 28/09/2026 somava as duas versoes do mesmo dia na captacao e nos
# resgates. Esse cache antigo continua legivel - a cota e o PL dele estao
# certos, e sem o zip nao haveria como refaze-lo -, mas `build` o refaz sempre
# que o zip da competencia estiver ao alcance.
_CABECALHO_CACHE = (
    "cnpj;subclasse;dia_ini;cota_ini;dia_fim;cota_fim;pl_fim;cotistas_fim;"
    "captacao;resgates;pregoes;retificacao"
)
_CABECALHO_SEM_DEDUPLICACAO = _CABECALHO_CACHE.rsplit(";", 1)[0]


@dataclass(slots=True)
class MesSubclasse:
    """Resumo de uma subclasse numa competencia.

    `slots` porque a janela de 12 meses carrega ~340 mil destes a cada `run`:
    medido em 2026-09-24, 93 MB sem slots contra 82 MB com.
    """

    cota_ini: float = 0.0
    cota_fim: float = 0.0
    pl_fim: float = 0.0
    cotistas_fim: int = 0
    captacao: float = 0.0
    resgates: float = 0.0
    pregoes: int = 0
    # Dia da primeira e da ultima cota da subclasse na competencia. None no
    # cache gravado antes de 2026-09-24, que deixava as colunas vazias.
    dia_ini: date | None = None
    dia_fim: date | None = None


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


def _mes_anterior(yyyymm: str) -> str:
    """"202601" -> "202512": a competencia de calendario anterior."""
    ano, mes = int(yyyymm[:4]), int(yyyymm[4:])
    return f"{ano - 1:04d}12" if mes == 1 else f"{ano:04d}{mes - 1:02d}"


def _media_por_pl(retornos: list[tuple[float, float]]) -> float:
    """Retorno das subclasses ponderado pelo PL; media simples se o PL somar zero."""
    peso = sum(p for _, p in retornos)
    if peso > 0:
        return sum(r * p for r, p in retornos) / peso
    return statistics.mean(r for r, _ in retornos)


class SerieMensal:
    """Resumo mensal por subclasse, com cache em disco por competencia."""

    def __init__(self, meses: list[str], dados: dict[tuple[str, str], dict[str, MesSubclasse]]):
        self.meses = meses
        self._dados = dados
        self._indice = {m: i for i, m in enumerate(meses)}
        self._por_cnpj: dict[str, list[tuple[str, str]]] = {}
        for chave in dados:
            self._por_cnpj.setdefault(chave[0], []).append(chave)
        # competencia -> (primeiro pregao, ultimo pregao, pregoes). Sob demanda:
        # e uma varredura das ~28 mil subclasses por competencia perguntada.
        self._calendario: dict[str, tuple[date | None, date | None, int]] = {}

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
    def _resumir(cls, registros: list) -> dict[tuple[str, str], MesSubclasse]:
        """Resume uma competencia ja lida, por subclasse.

        Recebe os registros em vez do caminho porque `retail_flow` e
        `performance` ja leram a mesma competencia nesta execucao: o leitor
        compartilhado devolve a lista parseada e isto nao toca o disco.

        **A CVM retifica o informe**: o mesmo (CNPJ, subclasse, dia) pode vir
        duas vezes no arquivo do mes, e vale a ultima ocorrencia - o criterio de
        `retail_flow.agrupar_por_classe`. Somar as duas contava a captacao e os
        resgates daquele dia em dobro. Medido: 1,03% dos pares em 20/09/2026;
        nenhum em 28/09/2026 nos arquivos de 08 e 09/2026. A cota, o PL e os
        cotistas ja saiam certos, porque o ultimo registro do dia vencia.
        """
        unicos: dict[tuple[str, str, date], object] = {}
        for registro in registros:
            unicos[(registro.cnpj, registro.subclasse, registro.dia)] = registro

        resumo: dict[tuple[str, str], MesSubclasse] = {}
        primeiro: dict[tuple[str, str], date] = {}
        ultimo: dict[tuple[str, str], date] = {}
        dias: dict[tuple[str, str], set[date]] = {}
        for registro in unicos.values():
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
            item.dia_ini = primeiro[chave]
            item.dia_fim = ultimo[chave]
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
                    cnpj, sub,
                    m.dia_ini.isoformat() if m.dia_ini else "", f"{m.cota_ini:.12f}",
                    m.dia_fim.isoformat() if m.dia_fim else "", f"{m.cota_fim:.12f}",
                    f"{m.pl_fim:.2f}", m.cotistas_fim,
                    f"{m.captacao:.2f}", f"{m.resgates:.2f}", m.pregoes, "ultima",
                ])

    @classmethod
    def _ler_cache(cls, caminho: Path) -> dict[tuple[str, str], MesSubclasse] | None:
        """Le o resumo gravado por `_gravar_cache`.

        `float` direto em vez de `parse_number`: o arquivo e escrito aqui, sempre
        com ponto decimal, e o parser generico - que resolve "1.234,56" - levava
        4,2 s para ler as 12 competencias fechadas que a janela de 12 meses usa,
        contra 1,5 s assim (medido em 2026-09-24). A janela roda em todo `run`.
        """
        if not caminho.is_file():
            return None
        resumo: dict[tuple[str, str], MesSubclasse] = {}
        # Poucas datas distintas por competencia: parseia cada uma uma vez.
        datas: dict[str, date] = {}

        def dia(texto: str) -> date | None:
            if not texto:
                return None
            valor = datas.get(texto)
            if valor is None:
                valor = datas[texto] = date.fromisoformat(texto)
            return valor

        with caminho.open(encoding="utf-8", newline="") as handle:
            primeira = handle.readline().strip()
            if primeira not in (_CABECALHO_CACHE, _CABECALHO_SEM_DEDUPLICACAO):
                # Cache de uma versao anterior do resumo: refazer e mais barato
                # que adivinhar o que mudou.
                return None
            for linha in csv.reader(handle, delimiter=";"):
                if len(linha) < 11:
                    continue
                try:
                    item = MesSubclasse(
                        cota_ini=float(linha[3] or 0),
                        cota_fim=float(linha[5] or 0),
                        pl_fim=float(linha[6] or 0),
                        cotistas_fim=int(float(linha[7] or 0)),
                        captacao=float(linha[8] or 0),
                        resgates=float(linha[9] or 0),
                        pregoes=int(float(linha[10] or 0)),
                        dia_ini=dia(linha[2]),
                        dia_fim=dia(linha[4]),
                    )
                except ValueError:
                    continue
                resumo[(linha[0], linha[1])] = item
        return resumo or None

    @staticmethod
    def _cache_deduplicado(caminho: Path) -> bool:
        """O cache foi gravado ja deduplicando a retificacao da CVM?"""
        try:
            with caminho.open(encoding="utf-8") as handle:
                return handle.readline().strip() == _CABECALHO_CACHE
        except OSError:
            return False

    @classmethod
    def build(
        cls,
        config: Config,
        meses: int = 24,
        force: bool = False,
        progresso=None,
        ate: str | None = None,
    ) -> SerieMensal:
        """Monta a serie, reusando o cache por competencia quando existe.

        So a competencia corrente passa pelo leitor compartilhado, que o fluxo e
        a janela curta ja usaram nesta execucao. As fechadas sem cache sao lidas
        sem ficar guardadas no processo (`ler_sem_guardar`): ninguem mais as le,
        e guardar cada uma ate o fim da execucao somaria ~91 MB por competencia.

        `ate` (AAAAMM) termina a serie numa competencia fechada. E o que o ciclo
        mensal do monitor da industria usa: sem ele, a ultima competencia era
        sempre tratada como corrente - rebaixada e relida a cada execucao, e
        fora do cache. Com `ate`, so o mes de hoje continua sendo corrente.
        """

        if ate:
            referencia = date(int(ate[:4]), int(ate[4:6]), 1)
            competencias = months_back(meses, referencia=referencia)
            mes_de_hoje = date.today().strftime("%Y%m")
        else:
            competencias = months_back(meses)
            mes_de_hoje = competencias[-1] if competencias else ""
        dados: dict[tuple[str, str], dict[str, MesSubclasse]] = {}
        lidas: list[str] = []
        for yyyymm in competencias:
            # A competencia corrente muda todo dia: o cache dela nao vale.
            corrente = yyyymm == mes_de_hoje
            cache = cls._cache_path(config, yyyymm)
            resumo = None if (force or corrente) else cls._ler_cache(cache)
            if resumo is not None and not cls._cache_deduplicado(cache):
                # Cache de antes da deduplicacao: refeito do zip quando da.
                registros = ler_sem_guardar(config, yyyymm)
                if registros:
                    resumo = cls._resumir(registros)
                    cls._gravar_cache(cache, resumo)
            if resumo is None:
                registros = (
                    ler_competencia(config, yyyymm, force=True) if corrente
                    else ler_sem_guardar(config, yyyymm)
                )
                if not registros:
                    if progresso:
                        progresso(yyyymm, None)
                    continue
                resumo = cls._resumir(registros)
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

    def garantir_datas(self, config: Config, yyyymm: str) -> bool:
        """Refaz do zip local o resumo de uma competencia gravada sem os dias.

        Devolve se a competencia tem os dias agora. So a competencia pedida e
        refeita, e uma vez: o resumo novo volta ao cache ja com eles. E o caso
        da competencia-base de uma janela lida de cache anterior a 2026-09-24.
        """
        if yyyymm not in self._indice:
            return False
        if self.calendario(yyyymm)[1] is not None:
            return True
        registros = ler_sem_guardar(config, yyyymm)
        if not registros:
            return False
        resumo = self._resumir(registros)
        self._gravar_cache(self._cache_path(config, yyyymm), resumo)
        for chave, item in resumo.items():
            if chave not in self._dados:
                self._dados[chave] = {}
                self._por_cnpj.setdefault(chave[0], []).append(chave)
            self._dados[chave][yyyymm] = item
        self._calendario.pop(yyyymm, None)
        return self.calendario(yyyymm)[1] is not None

    # --- leitura --------------------------------------------------------------

    def por_subclasse(self):
        """(CNPJ, subclasse) -> {AAAAMM: MesSubclasse}, o universo inteiro.

        Leitura publica do que a serie guarda, para quem monta o proprio painel
        (o monitor da industria) em vez de pedir retornos por janela.
        """
        return self._dados.items()

    def janela(self, nome: str, tamanho: int | None) -> list[str]:
        """Competencias de uma janela nomeada, dentro do que a serie tem."""
        if not self.meses:
            return []
        if tamanho is None:  # YTD: do inicio do ano da ultima competencia
            ano = self.meses[-1][:4]
            return [m for m in self.meses if m.startswith(ano)]
        return self.meses[-tamanho:]

    def janela_de_calendario(self, meses: int) -> list[str]:
        """As `meses` competencias de calendario que terminam na ultima da serie.

        `janela` pega as ultimas N que a serie TEM: com uma competencia faltando
        no meio, ela atravessaria 13 meses de calendario sob o rotulo de 12.
        Aqui a competencia que falta continua na janela, e a cobertura minima de
        cada fundo decide se ele entra.
        """
        if not self.meses:
            return []
        ultima = self.meses[-1]
        return months_back(meses, referencia=date(int(ultima[:4]), int(ultima[4:]), 1))

    def calendario(self, yyyymm: str) -> tuple[date | None, date | None, int]:
        """Primeiro e ultimo pregao da competencia no universo, e quantos houve.

        As datas sao os extremos entre as subclasses. Os pregoes sao a contagem
        mais comum entre as que informaram ate o ultimo dia. Medido em
        2026-09-24 na janela de 12 meses: 246 assim, exatamente os dias uteis de
        01/10/2025 a 23/09/2026, contra 252 pelo maximo, que contava fundo que
        publica cota em dia sem pregao. Sem o filtro do ultimo dia, a
        competencia corrente saia com um pregao a menos (15 em vez de 16),
        porque parte dos fundos ainda nao informou o dia mais recente. Dias ausentes
        (cache antigo) devolvem None - ver `garantir_datas`.
        """
        if yyyymm not in self._calendario:
            primeiro: date | None = None
            ultimo: date | None = None
            itens: list[MesSubclasse] = []
            for serie in self._dados.values():
                item = serie.get(yyyymm)
                if item is None:
                    continue
                itens.append(item)
                if item.dia_ini and (primeiro is None or item.dia_ini < primeiro):
                    primeiro = item.dia_ini
                if item.dia_fim and (ultimo is None or item.dia_fim > ultimo):
                    ultimo = item.dia_fim
            completos = [i.pregoes for i in itens if ultimo is None or i.dia_fim == ultimo]
            contagens = Counter(completos or [i.pregoes for i in itens])
            pregoes = contagens.most_common(1)[0][0] if contagens else 0
            self._calendario[yyyymm] = (primeiro, ultimo, pregoes)
        return self._calendario[yyyymm]

    def _subclasses(self, cnpj: str, competencias: list[str]):
        """Cada subclasse elegivel na janela: (serie, presentes, base, mes_base).

        Elegivel e a que aparece em `_MIN_COBERTURA` das competencias e tem cota
        de base e de fim utilizaveis. A base e a cota do fechamento da
        competencia de calendario anterior a janela (`mes_base`); sem ela -
        comeco do historico, fundo que nasceu depois - a primeira cota observada
        dentro da janela, e `mes_base` vem None.
        """
        anterior = _mes_anterior(competencias[0])
        minimo = max(1, int(len(competencias) * _MIN_COBERTURA))
        for chave in self._por_cnpj.get(cnpj, ()):
            serie = self._dados[chave]
            presentes = [m for m in competencias if m in serie]
            if len(presentes) < minimo:
                continue
            mes_base = serie.get(anterior)
            if mes_base is not None and mes_base.cota_fim > 0:
                base = mes_base.cota_fim
            elif serie[presentes[0]].cota_ini > 0:
                base, mes_base = serie[presentes[0]].cota_ini, None
            else:
                continue
            if serie[presentes[-1]].cota_fim <= 0:
                continue
            yield serie, presentes, base, mes_base

    def retorno(self, cnpj: str, competencias: list[str]) -> RetornoJanela | None:
        """Retorno da classe na janela: subclasses ponderadas pelo PL."""
        if not competencias:
            return None
        retornos: list[tuple[float, float]] = []
        pl = 0.0
        cotistas = 0
        observados: set[str] = set()
        for serie, presentes, base, _ in self._subclasses(cnpj, competencias):
            ultimo = serie[presentes[-1]]
            retornos.append(((ultimo.cota_fim / base - 1.0) * 100.0, max(ultimo.pl_fim, 0.0)))
            pl += ultimo.pl_fim
            cotistas += ultimo.cotistas_fim
            observados.update(presentes)

        if not retornos:
            return None
        return RetornoJanela(
            retorno=_media_por_pl(retornos),
            pl=pl,
            cotistas=cotistas,
            subclasses=len(retornos),
            meses=len(observados),
            completa=len(observados) >= len(competencias),
        )

    def consolidar(
        self, competencias: list[str]
    ) -> dict[str, tuple[float, float, int, int, float, int]]:
        """Por CNPJ do universo inteiro, no formato de `performance._consolidar`.

        (retorno, PL final, cotistas finais, pregoes, captacao liquida, variacao
        de cotistas) na janela - para a mediana de referencia e as casas sairem
        do mesmo codigo na janela curta e na de 12 meses. PL final zerado fica de
        fora, como no diario: sem PL nao ha peso. A variacao de cotistas parte do
        fechamento da competencia-base; sem ela, do fim da primeira competencia
        observada, que subconta o primeiro mes de quem nasceu na janela.
        """
        saida: dict[str, tuple[float, float, int, int, float, int]] = {}
        if not competencias:
            return saida
        for cnpj in self._por_cnpj:
            retornos: list[tuple[float, float]] = []
            pl = 0.0
            captacao = 0.0
            cotistas = cotistas_ini = pregoes = 0
            for serie, presentes, base, mes_base in self._subclasses(cnpj, competencias):
                ultimo = serie[presentes[-1]]
                retornos.append(((ultimo.cota_fim / base - 1.0) * 100.0, max(ultimo.pl_fim, 0.0)))
                pl += ultimo.pl_fim
                cotistas += ultimo.cotistas_fim
                cotistas_ini += (mes_base or serie[presentes[0]]).cotistas_fim
                pregoes = max(pregoes, sum(serie[m].pregoes for m in presentes))
                captacao += sum(serie[m].captacao - serie[m].resgates for m in presentes)
            if not retornos or pl <= 0:
                continue
            saida[cnpj] = (
                _media_por_pl(retornos), pl, cotistas, pregoes, captacao,
                cotistas - cotistas_ini,
            )
        return saida


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
    serie: SerieMensal | None = None,
) -> dict:
    """Rentabilidade por fundo e por casa, nas janelas de `JANELAS`.

    `serie` e a serie mensal ja montada nesta execucao (a janela de 12 meses
    da rentabilidade ponderada monta uma); sem ela, e montada aqui.
    """
    vitrines, fora = vitrine_snapshots(snapshots)
    cruzaveis, sem_identidade = identifiable_vitrines(vitrines)
    sem_cnpj = [
        {**item, "reason": (
            "sem CNPJ na vitrine não há como buscar a série de cota no informe "
            f"diário ({item['reason']})"
        )}
        for item in sem_identidade
    ]
    if not cruzaveis:
        return {
            "available": False,
            "reason": (
                "nenhuma vitrine publica CNPJ o bastante para buscar a série de cota"
            ),
            "excluded": fora + sem_cnpj,
        }

    if serie is None:
        serie = SerieMensal.build(
            config, meses=meses, force=force_download, progresso=progresso
        )
    if not serie.meses:
        return {
            "available": False,
            "reason": (
                "nenhuma competência do informe diário disponível - rode com a "
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
        "reason": None if fundos else "nenhum fundo de vitrine com série utilizável",
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
            f"({j['competencias']} {'mês' if j['competencias'] == 1 else 'meses'})"
            for j in resultado["janelas"]
        )
        + "[/]"
    )

    for casa in resultado["casas"]:
        table = Table(
            title=f"{casa['label']} - {casa['com_serie']} de {casa['vitrine']} fundos com série",
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
        "   [dim]Janela mais longa tem menos fundos: quem não existia no começo dela "
        "fica de fora, então o agregado longo é de sobreviventes.[/]"
    )
    for e in resultado.get("excluded", []):
        console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
