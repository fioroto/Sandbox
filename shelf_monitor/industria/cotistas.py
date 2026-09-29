"""De quem e o dinheiro de cada classe: o perfil mensal da CVM.

O perfil mensal traz, por classe, a fatia do PL e o numero de cotistas de cada
tipo de investidor: pessoa fisica de varejo e de private, empresa de varejo e
de private, clientes de distribuidor por conta e ordem (as plataformas), outros
fundos, previdencia, institucionais, nao residentes. E a unica fonte publica
que separa o dinheiro de PF/PJ do dinheiro que um fundo aplica em outro.

Medido em 28/09/2026 no arquivo de 08/2026: 25.223 linhas, todas com o mesmo
numero de colunas; percentuais com uma casa decimal que somam 100 +- 0,4 por
classe; 5 CNPJs repetidos em versoes diferentes (vale a maior `VERSAO`). Nao ha
coluna de PL - o PL vem do informe diario.

Os tipos da CVM viram nove segmentos. Os cinco primeiros formam o "dinheiro de
PF/PJ"; "fundos" e a fatia que, somada, contaria o mesmo dinheiro duas vezes;
"outros" (R$ 560 bi no universo de 08/2026) e sobretudo caixa de empresas e de
entes publicos, e fica de fora do PF/PJ.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market.datasets import dataset_perfil_mensal
from shelf_monitor.market.fetch import CacheReason, fetch
from shelf_monitor.normalize import normalize_cnpj
from shelf_monitor.texto import competencia_br


@dataclass(frozen=True)
class Segmento:
    """Um tipo de dono do dinheiro, e as colunas da CVM que o compoem."""

    chave: str
    rotulo: str
    colunas: tuple[str, ...]
    grupo: str  # "pf_pj" | "institucional" | "nao_residente" | "fundos" | "outros"


SEGMENTOS: tuple[Segmento, ...] = (
    Segmento("pf_varejo", "PF varejo", ("PF_VAREJO",), "pf_pj"),
    Segmento("pf_private", "PF private", ("PF_PB",), "pf_pj"),
    Segmento("pj_varejo", "PJ varejo", ("PJ_NAO_FINANC_VAREJO",), "pf_pj"),
    Segmento("pj_private", "PJ private", ("PJ_NAO_FINANC_PB",), "pf_pj"),
    Segmento("conta_e_ordem", "Conta e ordem (plataformas)", ("DISTRIB",), "pf_pj"),
    Segmento(
        "institucional", "Institucional",
        ("EFPC", "RPPS", "SEGUR", "EAPC", "BANCO", "PJ_FINANC", "CAPITALIZ",
         "CORRETORA_DISTRIB"),
        "institucional",
    ),
    Segmento("nao_residente", "Não residente", ("INVNR",), "nao_residente"),
    Segmento("fundos", "Fundos e clubes", ("FI_CLUBE",), "fundos"),
    Segmento("outros", "Outros", ("OUTRO",), "outros"),
)
CHAVES: tuple[str, ...] = tuple(s.chave for s in SEGMENTOS)
PF_PJ: tuple[str, ...] = tuple(s.chave for s in SEGMENTOS if s.grupo == "pf_pj")
_INDICE = {s.chave: i for i, s in enumerate(SEGMENTOS)}
# Previdencia e seguradora contam juntos na regra de previdencia do universo,
# mas moram dentro do segmento institucional.
_COLUNAS_PREVIDENCIA = ("EAPC", "SEGUR")


def rotulo_do_segmento(chave: str) -> str:
    return SEGMENTOS[_INDICE[chave]].rotulo


@dataclass(slots=True)
class PerfilClasse:
    """A composicao do dono do dinheiro de uma classe numa competencia.

    `fatias` estao na ordem de SEGMENTOS, em fracao, normalizadas para somar 1
    (a CVM arredonda cada tipo a uma casa decimal). `informado` e falso quando a
    classe entregou o perfil com tudo zerado - ai as fatias sao zero e ninguem
    deve usa-las como composicao.
    """

    cnpj: str
    versao: int
    fatias: tuple[float, ...]
    cotistas: tuple[int, ...]
    previdencia: float
    maior_cotista: float | None
    soma_informada: float

    @property
    def informado(self) -> bool:
        return self.soma_informada > 0

    def fatia(self, chave: str) -> float:
        return self.fatias[_INDICE[chave]]

    @property
    def fundos(self) -> float:
        """Fracao do PL em maos de outros fundos e clubes."""
        return self.fatia("fundos")

    @property
    def pf_pj(self) -> float:
        return sum(self.fatias[_INDICE[c]] for c in PF_PJ)

    @property
    def total_cotistas(self) -> int:
        return sum(self.cotistas)


def _numero(texto: str) -> float:
    texto = (texto or "").strip()
    if not texto:
        return 0.0
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return 0.0


@dataclass
class Diagnostico:
    """O que a leitura encontrou de estranho, para o bloco de qualidade."""

    linhas: int = 0
    desalinhadas: int = 0
    versoes_descartadas: int = 0
    sem_composicao: int = 0

    def to_dict(self) -> dict:
        return {
            "linhas": self.linhas,
            "desalinhadas": self.desalinhadas,
            "versoes_descartadas": self.versoes_descartadas,
            "sem_composicao": self.sem_composicao,
        }


def ler_perfil(bruto: bytes) -> tuple[dict[str, PerfilClasse], Diagnostico]:
    """perfil_mensal_fi_AAAAMM.csv -> PerfilClasse por CNPJ de classe.

    Colunas lidas por nome. Linha com numero de colunas diferente do cabecalho
    e contada e descartada - ler por posicao uma linha desalinhada poria a
    fatia de um tipo na coluna de outro.
    """
    texto = bruto.decode("latin-1")
    leitor = csv.reader(io.StringIO(texto), delimiter=";")
    diagnostico = Diagnostico()
    try:
        cabecalho = [c.strip().upper() for c in next(leitor)]
    except StopIteration:
        return {}, diagnostico
    indice = {c: i for i, c in enumerate(cabecalho)}
    col_cnpj = indice.get("CNPJ_FUNDO_CLASSE", indice.get("CNPJ_FUNDO"))
    if col_cnpj is None:
        raise SourceUnavailable("perfil mensal sem a coluna CNPJ_FUNDO_CLASSE")
    col_versao = indice.get("VERSAO")
    col_maior = indice.get("PR_PATRIM_LIQ_MAIOR_COTST")

    def colunas(prefixo: str, segmento: Segmento) -> list[int]:
        return [indice[f"{prefixo}{c}"] for c in segmento.colunas if f"{prefixo}{c}" in indice]

    pl_cols = [colunas("PR_PL_COTST_", s) for s in SEGMENTOS]
    nr_cols = [colunas("NR_COTST_", s) for s in SEGMENTOS]
    prev_cols = [indice[f"PR_PL_COTST_{c}"] for c in _COLUNAS_PREVIDENCIA if f"PR_PL_COTST_{c}" in indice]

    saida: dict[str, PerfilClasse] = {}
    for linha in leitor:
        if not linha:
            continue
        diagnostico.linhas += 1
        if len(linha) != len(cabecalho):
            diagnostico.desalinhadas += 1
            continue
        cnpj = normalize_cnpj(linha[col_cnpj])
        if not cnpj:
            continue
        versao = int(_numero(linha[col_versao])) if col_versao is not None else 0
        existente = saida.get(cnpj)
        if existente is not None:
            diagnostico.versoes_descartadas += 1
            if versao < existente.versao:
                continue
        brutas = [sum(_numero(linha[i]) for i in cols) for cols in pl_cols]
        soma = sum(brutas)
        if soma > 0:
            fatias = tuple(v / soma for v in brutas)
        else:
            fatias = tuple(0.0 for _ in brutas)
            diagnostico.sem_composicao += 1
        maior = _numero(linha[col_maior]) / 100 if col_maior is not None else 0.0
        saida[cnpj] = PerfilClasse(
            cnpj=cnpj,
            versao=versao,
            fatias=fatias,
            cotistas=tuple(int(sum(_numero(linha[i]) for i in cols)) for cols in nr_cols),
            previdencia=(sum(_numero(linha[i]) for i in prev_cols) / soma) if soma > 0 else 0.0,
            # Zero e "nao informado", nao "ninguem tem nada": toda classe tem
            # um maior cotista.
            maior_cotista=maior if maior > 0 else None,
            soma_informada=soma / 100,
        )
    return saida, diagnostico


# --- cache compacto -----------------------------------------------------------
# O CSV da CVM tem 107 colunas e ~13 MB por mes; o monitor usa 21 numeros por
# classe. O compacto e escrito aqui, com ponto decimal, e lido com float() -
# a mesma escolha de `SerieMensal._ler_cache`.

_CABECALHO_COMPACTO = ";".join(
    ["cnpj", "versao", "previdencia", "maior_cotista", "soma_informada"]
    + [f"f_{c}" for c in CHAVES] + [f"n_{c}" for c in CHAVES]
)


def _caminho_compacto(config: Config, competencia: str) -> Path:
    return config.industria_cache_dir / "perfil" / f"{competencia}.csv"


def _gravar_compacto(caminho: Path, perfis: dict[str, PerfilClasse]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".part")
    with temporario.open("w", encoding="utf-8", newline="") as handle:
        handle.write(_CABECALHO_COMPACTO + "\n")
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        for cnpj in sorted(perfis):
            p = perfis[cnpj]
            writer.writerow(
                [cnpj, p.versao, f"{p.previdencia:.6f}",
                 "" if p.maior_cotista is None else f"{p.maior_cotista:.4f}",
                 f"{p.soma_informada:.4f}"]
                + [f"{v:.6f}" for v in p.fatias] + list(p.cotistas)
            )
    temporario.replace(caminho)


def _ler_compacto(caminho: Path) -> dict[str, PerfilClasse] | None:
    if not caminho.is_file():
        return None
    n = len(CHAVES)
    saida: dict[str, PerfilClasse] = {}
    with caminho.open(encoding="utf-8", newline="") as handle:
        if handle.readline().strip() != _CABECALHO_COMPACTO:
            return None  # versao anterior do compacto: refazer e mais barato
        for linha in csv.reader(handle, delimiter=";"):
            if len(linha) != 5 + 2 * n:
                continue
            try:
                saida[linha[0]] = PerfilClasse(
                    cnpj=linha[0],
                    versao=int(linha[1]),
                    previdencia=float(linha[2]),
                    maior_cotista=float(linha[3]) if linha[3] else None,
                    soma_informada=float(linha[4]),
                    fatias=tuple(float(v) for v in linha[5:5 + n]),
                    cotistas=tuple(int(v) for v in linha[5 + n:]),
                )
            except ValueError:
                continue
    return saida


def carregar_perfil(
    config: Config,
    competencia: str,
    *,
    revalidar: bool = False,
    baixar: bool = True,
    force: bool = False,
) -> tuple[dict[str, PerfilClasse] | None, str | None]:
    """(perfis por CNPJ, aviso) de uma competencia AAAAMM.

    Sem `revalidar`, o compacto em disco basta e nada vai para a rede. Com ele,
    a CVM e consultada por requisicao condicional (304 quando nada mudou) - e o
    que o ciclo mensal faz com as competencias recentes. `baixar=False` e o
    modo offline: so o que ja esta em disco.
    """
    dataset = dataset_perfil_mensal(competencia)
    compacto = _caminho_compacto(config, competencia)
    bruto = config.market_raw_dir / dataset.filename
    rotulo = competencia_br(competencia)

    if not (revalidar or force):
        lido = _ler_compacto(compacto)
        if lido is not None:
            return lido, None
        if bruto.is_file():
            return _parsear_e_guardar(bruto, compacto), None
    if not baixar:
        lido = _ler_compacto(compacto)
        if lido is not None:
            return lido, None
        if bruto.is_file():
            return _parsear_e_guardar(bruto, compacto), None
        return None, f"perfil mensal de {rotulo} não está em disco (modo offline)"

    try:
        resultado = fetch(config, dataset, force=force)
    except SourceUnavailable as exc:
        lido = _ler_compacto(compacto)
        if lido is not None:
            return lido, f"perfil mensal de {rotulo}: origem indisponível, usado o de disco"
        return None, f"perfil mensal de {rotulo} indisponível ({exc})"

    if resultado.reason is CacheReason.NOT_MODIFIED:
        lido = _ler_compacto(compacto)
        if lido is not None:
            return lido, None
    aviso = None
    if resultado.reason is CacheReason.SOURCE_DOWN:
        aviso = f"perfil mensal de {rotulo}: origem indisponível, usado o de disco"
    return _parsear_e_guardar(resultado.path, compacto), aviso


def _parsear_e_guardar(bruto: Path, compacto: Path) -> dict[str, PerfilClasse]:
    perfis, _ = ler_perfil(bruto.read_bytes())
    _gravar_compacto(compacto, perfis)
    return perfis


def diagnostico_do_bruto(config: Config, competencia: str) -> Diagnostico | None:
    """O diagnostico de leitura do arquivo bruto, se ele estiver em disco."""
    bruto = config.market_raw_dir / dataset_perfil_mensal(competencia).filename
    if not bruto.is_file():
        return None
    return ler_perfil(bruto.read_bytes())[1]
