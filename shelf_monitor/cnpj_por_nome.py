"""CNPJ pelo nome: identifica os fundos de uma vitrine que nao publica CNPJ.

A tabela de rentabilidade do Itau traz nome, risco, aplicacao, taxa e resgate,
e nenhum identificador. Medido em 2026-09-25: so 27 dos 459 fundos da vitrine
(6%) ganhavam CNPJ, e todos pelo slug exato no merge com o Open Finance. Sem
CNPJ a vitrine fica fora dos gaps e da captacao, que cruzam as casas por CNPJ
(`report/competitive.py` exige 50% da vitrine identificada).

O XHR da listagem nao resolve: o gateway `charon` do Itau entrega o dataset
cifrado (1,6 MB de base64, `json_ok=False` no debug de 2026-10-14), e o bundle
do app nao tem campo de CNPJ. O modal "mais info" mostra taxas e o link da
lamina, com um codigo interno do Itau - tambem sem CNPJ.

A regra e conservadora - nome normalizado, e empate nao casa:

1. O nome vira um conjunto de palavras canonicas: sem acento e pontuacao,
   abreviacoes numa forma so ("Renda Fixa" e "RF", "Crédito Privado" e "CP",
   "Investimento no Exterior" e "IE"), sem a forma juridica (FIF, FIC, CIC,
   "classe de investimento em cotas", "responsabilidade limitada") e sem
   conectivos. A ordem das palavras nao conta: "Absolute Hiker Renda Fixa Infra
   Seleção" e o "ABSOLUTE HIKER ... SELEÇÃO ... INFRA" do cadastro.
2. Tres niveis, do mais estrito ao mais solto, parando no primeiro que tiver
   candidato: o nome inteiro; sem as palavras que o nome comercial costuma
   omitir (classe de ativo, IE, IS, FIDC); e, por fim, tambem sem a marca da
   casa ("Blackrock ... Seleção" na vitrine e "ITAÚ SELEÇÃO BLACKROCK ..." no
   cadastro).
3. Candidatos: o catalogo Open Finance da propria casa, na mesma coleta, e o
   cadastro da CVM (classes ativas e nao exclusivas). Fundo cancelado, em
   liquidacao ou exclusivo nao e candidato, venha de onde vier - o Open Finance
   do Itau listava 360 fundos ja cancelados na CVM em 2026-09-28.
4. Um candidato casa; dois ou mais sao empate e nao casam. O catalogo da casa
   decide antes do cadastro: se so um fundo dele tem o nome, e esse que ela
   oferece. Com uma excecao: o catalogo apontando a classe master quando o
   cadastro tem a classe de cotas de mesmo nome e empate. Medido: "Itaú Artax
   Renda Fixa LP" cobra 0,90% na vitrine, a taxa do FIC; o catalogo so lista o
   master, de taxa 0%.
5. Classe de ativo que contradiz (renda fixa na vitrine, acoes no cadastro)
   tira o candidato da disputa.
6. Dois fundos da vitrine no mesmo CNPJ e conflito: nenhum dos dois fica com
   ele.

Medido em 2026-10-14: CNPJ em 403 dos 477 fundos da vitrine do Itau (84,5%).
O par FIC/master de mesmo nome e quase todo empate que sobra (37 dos 39). Ele
continua empate de proposito: escolher a classe de cotas (que levaria a 440,
92,2%) e uma regra de dominio que ainda nao foi aprovada.

Todo casamento grava a origem em `extra[KEY_CNPJ_POR_NOME]`; todo fundo que
ficou sem CNPJ grava o motivo em `extra[KEY_CNPJ_PENDENTE]`.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import normalize_cnpj, normalize_text
from shelf_monitor.texto import contagem

# Chaves gravadas em FundOffer.extra.
KEY_CNPJ_POR_NOME = "cnpj_por_nome"
KEY_CNPJ_PENDENTE = "cnpj_pendente"

FONTE_CATALOGO = "catálogo Open Finance"
FONTE_CADASTRO = "cadastro CVM"

MOTIVO_EMPATE = "empate"
MOTIVO_SEM_CANDIDATO = "sem candidato"
MOTIVO_CONFLITO = "conflito"

_SITUACOES_ATIVAS = frozenset({"EM FUNCIONAMENTO NORMAL", "FASE PRE OPERACIONAL"})

# "IMA-B5" e "IMA B 5" sao o mesmo indice; "S&P500" e "S&P 500" tambem.
_LETRA_NUMERO = re.compile(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])")
# A subclasse partilha o CNPJ da classe: "Subclasse II" sai do nome antes de
# comparar. So o identificador logo apos a palavra (romano, letra ou numero).
_SUBCLASSE = re.compile(r"\bSUBCLASSE(?:\s+(?:[IVX]+|[A-Z]|\d+)\b)?")


def _expressao(texto: str) -> re.Pattern:
    return re.compile(r"(?<![A-Z0-9])" + texto + r"(?![A-Z0-9])")


# Expressoes de mais de uma palavra, na forma canonica. Ordem importa: "L S"
# (de "L&S") antes de "S P" (de "S&P").
_EXPRESSOES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (_expressao(origem), destino)
    for origem, destino in (
        ("INVESTIMENTO NO EXTERIOR", "IE"),
        ("INVEST NO EXTERIOR", "IE"),
        ("INV NO EXTERIOR", "IE"),
        ("INV EXTERIOR", "IE"),
        ("INVESTIMENTO SUSTENTAVEL", "IS"),
        ("INVEST SUSTENTAVEL", "IS"),
        ("DIREITOS CREDITORIOS", "FIDC"),
        ("RENDA FIXA", "RF"),
        ("CREDITO PRIVADO", "CP"),
        ("CRED PRIVADO", "CP"),
        ("CREDITO PRIV", "CP"),
        ("CRED PRIV", "CP"),
        ("LONGO PRAZO", "LP"),
        ("MULTI MERCADO", "MM"),
        ("LONG AND SHORT", "LONG SHORT"),
        ("L S", "LONG SHORT"),
        ("S P", "SP"),
        ("J P MORGAN", "JPMORGAN"),
        ("JP MORGAN", "JPMORGAN"),
    )
)

# Grafias da mesma palavra -> forma canonica.
_CANONICAS = {
    "MULTIMERCADO": "MM", "MULTIMERCADOS": "MM", "MULTIMERC": "MM",
    "MULTMERCADO": "MM", "MULTI": "MM", "MULT": "MM", "FIM": "MM",
    "ACAO": "ACOES", "FIA": "ACOES", "CIA": "ACOES",
    "FIRF": "RF", "CPRIV": "CP",
    "INFRAESTRUTURA": "INFRA", "INFRAEST": "INFRA", "INFR": "INFRA",
    "DEBENTURE": "DEBENTURES", "DEB": "DEBENTURES", "DEBS": "DEBENTURES",
    "REF": "REFERENCIADO",
    "PREVIDENCIA": "PREV", "PREVIDENCIARIO": "PREV", "PREVIDENCIARIA": "PREV",
    "BIASED": "BIAS",
    # "Itaú Flexprev PVT Juro Real Target 2026 II" e o "Private" da vitrine.
    "PVT": "PRIVATE",
}

# "Incentivado" e a marca do fundo de infraestrutura da Lei 12.431: o cadastro
# escreve "FUNDO INCENTIVADO DE INVESTIMENTO EM INFRAESTRUTURA" onde a vitrine
# diz "Infra" ou "Debêntures Incentivadas". Vira a palavra INFRA - que nao e
# omissivel: sem ela, "SPX Seahawk Seleção Infra Renda Fixa" casava com o SPX
# Seahawk de credito, que nao e de infraestrutura (medido em 2026-10-14).
_INCENTIVADO = frozenset(
    {"INCENTIVADO", "INCENTIVADA", "INCENTIVADOS", "INCENTIVADAS", "INCENT", "INC"}
)
# No fundo de infraestrutura, a designacao legal ("FIF INCENTIVADO EM
# DEBÊNTURES DE INFRA", "FC DE FII EM DEB DE INFRA") repete o que INFRA ja diz:
# "Absolute Hiker Renda Fixa Infra Seleção" e, no cadastro, "... SELEÇÃO FIF
# INCENTIVADO EM DEBÊNTURES DE INFRA". Com INFRA presente, estas somem.
_REDUNDANTES_COM_INFRA = frozenset({"DEBENTURES", "FII"})

# Forma juridica, marca de cotas e conectivos: nao distinguem um fundo de outro.
# "A" e "O" ficam de fora de proposito - no nome de fundo sao letra de classe
# ("ABSOLUTE HIDRA CDI A", "SPARTA MAX A 60"), nao artigo.
_FORMA_JURIDICA = frozenset({
    "FUNDO", "FUNDOS", "FDO", "INVESTIMENTO", "INVESTIMENTOS", "INVEST", "INV",
    "FINANCEIRO", "FINAN", "FIN", "FIF", "FIIF", "FI", "FIC", "FICFI", "FICFIM",
    "FICFIA", "FIQ", "CIC", "CI", "FC", "COTAS", "COTA", "QUOTAS", "QUOTA",
    "CLASSE", "CLASSES", "UNICA", "RESPONSABILIDADE", "RRESPONSABILIDADE",
    "LIMITADA", "LTDA", "RL", "RESP", "LIM", "LIMIT",
    "DE", "DA", "DO", "DAS", "DOS", "E", "EM", "NO", "NA", "AO",
})

# Palavras que o nome comercial costuma omitir: classe de ativo e rotulos
# regulatorios. Somem a partir do nivel 1.
_OMITIDAS_NO_NOME_COMERCIAL = frozenset(
    {"RF", "MM", "ACOES", "CP", "LP", "REFERENCIADO", "IE", "IS", "FIDC"}
)
# Classe de ativo que, presente dos dois lados e diferente, tira o candidato.
_CLASSES_PRIMARIAS = frozenset({"RF", "MM", "ACOES", "FIDC"})

# Palavras que marcam o veiculo de previdencia (o mesmo criterio de
# `classify.ClasseOficial.previdenciaria`, mais a marca Flexprev do Itau).
# Num universo so de previdencia elas nao distinguem ninguem e somem a partir
# do nivel 1: "Private Prev RF" na vitrine, "ITAÚ FLEXPREV EXCELLENCE ... RF"
# no cadastro.
_PALAVRAS_DE_PREVIDENCIA = frozenset({"PREV", "FIE", "FIFE", "PGBL", "VGBL", "PGBLVGBL"})
_MARCAS_DE_PREVIDENCIA = _PALAVRAS_DE_PREVIDENCIA | {"FLEXPREV"}

# A classe de cotas se anuncia no nome; o master, nao.
_MARCA_DE_COTAS = re.compile(
    r"\b(FIC|FICFI|FICFIM|FICFIA|FIQ|CIC|FC|COTAS|QUOTAS)\b"
)

_NIVEIS = (0, 1, 2)
_ROTULO_DO_NIVEL = {
    0: "nome normalizado",
    1: "nome normalizado, sem classe de ativo",
    2: "nome normalizado, sem classe de ativo nem marca da casa",
}
# Sem a marca da casa, uma palavra so e generica demais para identificar.
_MINIMO_DE_PALAVRAS_SEM_MARCA = 2


def palavras_do_nome(nome: str | None) -> frozenset[str]:
    """As palavras que identificam o fundo, na forma canonica."""
    texto = _LETRA_NUMERO.sub(" ", normalize_text(nome))
    texto = _SUBCLASSE.sub(" ", texto)
    for padrao, destino in _EXPRESSOES:
        texto = padrao.sub(destino, texto)

    palavras: set[str] = set()
    incentivado = False
    for palavra in texto.split():
        palavra = _CANONICAS.get(palavra, palavra)
        if palavra in _INCENTIVADO:
            incentivado = True
        elif palavra not in _FORMA_JURIDICA:
            palavras.add(palavra)
    if incentivado:
        palavras.add("INFRA")
    if "INFRA" in palavras:
        palavras -= _REDUNDANTES_COM_INFRA
    return frozenset(palavras)


def nucleo(
    palavras: frozenset[str],
    nivel: int,
    marcas: frozenset[str],
    omitidas: frozenset[str] = _OMITIDAS_NO_NOME_COMERCIAL,
) -> frozenset[str]:
    """As palavras comparadas em cada nivel (ver o docstring do modulo)."""
    if nivel >= 1:
        palavras = palavras - omitidas
    if nivel >= 2:
        palavras = palavras - marcas
    return palavras


def eh_previdenciaria(nome: str | None, classificacao_anbima: str | None = None) -> bool:
    """Classe de previdencia: rotulo ANBIMA "Previdência ..." ou marca no nome."""
    if normalize_text(classificacao_anbima).startswith("PREVIDENCIA"):
        return True
    return bool(palavras_do_nome(nome) & _MARCAS_DE_PREVIDENCIA)


def tem_marca_de_cotas(nome: str | None) -> bool:
    """O nome de registro diz que a classe investe em cotas (FIC, CIC...)."""
    return bool(_MARCA_DE_COTAS.search(normalize_text(nome)))


def _compativeis(vitrine: frozenset[str], candidato: frozenset[str]) -> bool:
    """Falso quando as duas pontas declaram classes de ativo sem nada em comum."""
    a = vitrine & _CLASSES_PRIMARIAS
    b = candidato & _CLASSES_PRIMARIAS
    return not (a and b and not (a & b))


@dataclass(frozen=True)
class _Situacao:
    ativo: bool
    exclusivo: bool


@dataclass(frozen=True)
class _Classe:
    """Uma classe ativa do cadastro, nos campos que decidem se ela concorre."""

    cnpj: str
    nome: str
    exclusivo: bool
    classificacao_anbima: str


@dataclass
class _Indice:
    """Nucleo do nome -> CNPJs, por nivel, e o nome de cada CNPJ."""

    marcas: frozenset[str]
    omitidas: frozenset[str] = _OMITIDAS_NO_NOME_COMERCIAL
    chaves: dict[int, dict[frozenset[str], set[str]]] = field(
        default_factory=lambda: {nivel: defaultdict(set) for nivel in _NIVEIS}
    )
    nomes: dict[str, str] = field(default_factory=dict)
    palavras: dict[str, frozenset[str]] = field(default_factory=dict)

    def adicionar(self, cnpj: str, nome: str) -> None:
        if cnpj in self.nomes:
            return
        palavras = palavras_do_nome(nome)
        self.nomes[cnpj] = nome
        self.palavras[cnpj] = palavras
        for nivel in _NIVEIS:
            chave = nucleo(palavras, nivel, self.marcas, self.omitidas)
            if chave:
                self.chaves[nivel][chave].add(cnpj)

    def candidatos(
        self, chave: frozenset[str], nivel: int, vitrine: frozenset[str]
    ) -> set[str]:
        return {
            cnpj
            for cnpj in self.chaves[nivel].get(chave, ())
            if _compativeis(vitrine, self.palavras[cnpj])
        }


def _ler_cadastro(market_dir: Path) -> tuple[dict[str, _Situacao], list[_Classe]] | None:
    """Situacao por CNPJ (classe e envelope) e as classes ativas do cadastro.

    None quando a base de mercado nao foi importada. O `fundos.csv` entra so
    para a situacao do envelope: o fundo legado cancelado antes da adaptacao a
    RCVM 175 nao aparece no `mercado.csv`, e e justamente o que o Open Finance
    do Itau ainda lista. Quem decide se a exclusiva concorre e o resolvedor.
    """
    mercado = market_dir / "mercado.csv"
    if not mercado.is_file():
        return None

    situacao: dict[str, _Situacao] = {}
    envelopes: dict[str, list[_Situacao]] = defaultdict(list)
    classes: list[_Classe] = []
    with mercado.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            cnpj = normalize_cnpj(row.get("cnpj"))
            if not cnpj or cnpj in situacao:
                continue
            estado = _Situacao(
                ativo=normalize_text(row.get("situacao")) in _SITUACOES_ATIVAS,
                exclusivo=(row.get("exclusivo") or "").strip().upper() == "S",
            )
            situacao[cnpj] = estado
            envelope = normalize_cnpj(row.get("cnpj_fundo"))
            if envelope:
                envelopes[envelope].append(estado)
            if estado.ativo:
                classes.append(_Classe(
                    cnpj=cnpj,
                    nome=row.get("denominacao") or "",
                    exclusivo=estado.exclusivo,
                    classificacao_anbima=row.get("classificacao_anbima") or "",
                ))

    # O envelope de um fundo multiclasse: ativo se alguma classe esta ativa;
    # exclusivo so se todas sao (como em enrich._fund_level_record).
    for envelope, estados in envelopes.items():
        situacao.setdefault(
            envelope,
            _Situacao(
                ativo=any(e.ativo for e in estados),
                exclusivo=all(e.exclusivo for e in estados),
            ),
        )

    fundos = market_dir / "fundos.csv"
    if fundos.is_file():
        with fundos.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=";"):
                cnpj = normalize_cnpj(row.get("cnpj_fundo"))
                if cnpj and cnpj not in situacao:
                    situacao[cnpj] = _Situacao(
                        ativo=normalize_text(row.get("situacao")) in _SITUACOES_ATIVAS,
                        exclusivo=False,
                    )
    return situacao, classes


@dataclass
class ResumoPorNome:
    """O que a resolucao fez - vira observacao do snapshot e linha do console."""

    examinados: int = 0
    pelo_catalogo: int = 0
    pelo_cadastro: int = 0
    empates: list[tuple[str, list[str]]] = field(default_factory=list)
    sem_candidato: list[str] = field(default_factory=list)
    conflitos: list[tuple[str, str]] = field(default_factory=list)
    # A base de mercado nao estava em disco: so o catalogo serviu.
    sem_cadastro: bool = False

    @property
    def resolvidos(self) -> int:
        return self.pelo_catalogo + self.pelo_cadastro

    @property
    def pendentes(self) -> int:
        return len(self.empates) + len(self.sem_candidato) + len(self.conflitos)

    def nota(self) -> str:
        """Uma linha para a observacao do snapshot."""
        texto = (
            f"CNPJ pelo nome em {self.resolvidos} de "
            f"{contagem(self.examinados, 'fundo', 'fundos')} da vitrine "
            f"({self.pelo_catalogo} pelo catálogo Open Finance, "
            f"{self.pelo_cadastro} pelo cadastro da CVM)"
        )
        if self.pendentes:
            partes = [
                contagem(len(self.empates), "empate", "empates"),
                f"{len(self.sem_candidato)} sem candidato",
                contagem(len(self.conflitos), "conflito", "conflitos"),
            ]
            texto += f"; sem CNPJ: {', '.join(partes)}"
        if self.sem_cadastro:
            texto += (
                "; base de mercado ausente, só o catálogo serviu "
                "(rode `python -m shelf_monitor market`)"
            )
        return texto


class ResolvedorPorNome:
    """Casa nome de vitrine com o catalogo da casa e o cadastro da CVM.

    O padrao e a vitrine de fundos. Dois parametros servem a de previdencia
    (medidos na do Itau Private, 41 fundos em 2026-09-25):

    - `incluir_exclusivos`: todo FIE e exclusivo no cadastro, porque o cotista
      e a seguradora; sem isto 3 dos 41 casavam pelo nome, com isto 17;
    - `so_previdencia`: so classe previdenciaria concorre (`eh_previdenciaria`),
      e as palavras de previdencia (Prev, FIE, PGBL...) viram omissiveis, porque
      nesse universo nao distinguem ninguem. Nao mudou a conta de 25/09; tira
      da disputa a classe de varejo de mesmo nome.
    """

    def __init__(
        self,
        catalogo: list[FundOffer],
        cadastro: tuple[dict[str, _Situacao], list[_Classe]] | None,
        marcas: frozenset[str] = frozenset(),
        *,
        incluir_exclusivos: bool = False,
        so_previdencia: bool = False,
    ):
        self.marcas = frozenset(normalize_text(m) for m in marcas)
        self.sem_cadastro = cadastro is None
        situacao, classes = cadastro if cadastro is not None else ({}, [])
        omitidas = _OMITIDAS_NO_NOME_COMERCIAL | (
            _PALAVRAS_DE_PREVIDENCIA if so_previdencia else frozenset()
        )

        self._catalogo = _Indice(self.marcas, omitidas)
        for oferta in catalogo:
            estado = situacao.get(oferta.cnpj) if oferta.cnpj else None
            if not oferta.cnpj or (
                estado and (not estado.ativo or (estado.exclusivo and not incluir_exclusivos))
            ):
                continue
            self._catalogo.adicionar(oferta.cnpj, oferta.name)

        self._cadastro = _Indice(self.marcas, omitidas)
        for classe in classes:
            if classe.exclusivo and not incluir_exclusivos:
                continue
            if so_previdencia and not eh_previdenciaria(
                classe.nome, classe.classificacao_anbima
            ):
                continue
            self._cadastro.adicionar(classe.cnpj, classe.nome)

    @classmethod
    def carregar(
        cls,
        market_dir: Path,
        catalogo: list[FundOffer],
        marcas: tuple[str, ...] = (),
        *,
        incluir_exclusivos: bool = False,
        so_previdencia: bool = False,
    ) -> "ResolvedorPorNome":
        return cls(
            catalogo, _ler_cadastro(market_dir), frozenset(marcas),
            incluir_exclusivos=incluir_exclusivos, so_previdencia=so_previdencia,
        )

    def nome_no_cadastro(self, cnpj: str | None) -> str | None:
        """O nome da classe, se ela concorre neste universo; senao None.

        Quem identifica por outra via (a cota do dia, na previdencia) usa isto
        para ficar no mesmo universo de candidatos que o nome.
        """
        return self._cadastro.nomes.get(cnpj) if cnpj else None

    def resolver(
        self,
        ofertas: list[FundOffer],
        nomes: Callable[[FundOffer], list[str]] | None = None,
    ) -> ResumoPorNome:
        """Preenche o CNPJ das ofertas sem CNPJ, in-place, e diz o que fez.

        `nomes(oferta)` da os nomes a tentar, em ordem; o padrao e so o da
        vitrine. O primeiro que casa decide, e o primeiro empate tambem: um
        nome mais especifico que empata nao e desempatado por um mais solto.
        """
        resumo = ResumoPorNome(sem_cadastro=self.sem_cadastro)
        casados: dict[str, list[FundOffer]] = defaultdict(list)
        for oferta in ofertas:
            if oferta.cnpj:
                casados[oferta.cnpj].append(oferta)
                continue
            resumo.examinados += 1
            cnpj, fonte, detalhe = self._pelos_nomes(
                oferta, nomes(oferta) if nomes else [oferta.name]
            )
            if cnpj is None:
                oferta.extra[KEY_CNPJ_PENDENTE] = detalhe
                if fonte == MOTIVO_EMPATE:
                    resumo.empates.append((oferta.name, detalhe["candidatos"]))
                else:
                    resumo.sem_candidato.append(oferta.name)
                continue
            oferta.cnpj = cnpj
            oferta.extra[KEY_CNPJ_POR_NOME] = detalhe
            casados[cnpj].append(oferta)

        # Dois fundos da vitrine no mesmo CNPJ: um dos casamentos esta errado e
        # nao ha como saber qual - nenhum fica com o CNPJ.
        for cnpj, grupo in casados.items():
            if len(grupo) < 2:
                continue
            for oferta in grupo:
                if KEY_CNPJ_POR_NOME not in oferta.extra:
                    continue  # o CNPJ veio da fonte, nao do nome
                outros = [o.name for o in grupo if o is not oferta]
                del oferta.extra[KEY_CNPJ_POR_NOME]
                oferta.cnpj = None
                oferta.extra[KEY_CNPJ_PENDENTE] = {
                    "motivo": MOTIVO_CONFLITO,
                    "cnpj": cnpj,
                    "outros": outros,
                }
                resumo.conflitos.append((oferta.name, cnpj))

        for oferta in ofertas:
            origem = oferta.extra.get(KEY_CNPJ_POR_NOME)
            if origem and oferta.cnpj:
                if origem["fonte"] == FONTE_CATALOGO:
                    resumo.pelo_catalogo += 1
                else:
                    resumo.pelo_cadastro += 1
        return resumo

    def _pelos_nomes(self, oferta: FundOffer, nomes: list[str]) -> tuple[str | None, str, dict]:
        """O primeiro nome que casa ou empata decide; sem nenhum, sem candidato."""
        for nome in dict.fromkeys(n for n in nomes if n):
            cnpj, fonte, detalhe = self._um(nome)
            if fonte == MOTIVO_SEM_CANDIDATO:
                continue
            if nome != oferta.name:
                detalhe["nome_tentado"] = nome
            return cnpj, fonte, detalhe
        return None, MOTIVO_SEM_CANDIDATO, {"motivo": MOTIVO_SEM_CANDIDATO}

    def _um(self, nome: str) -> tuple[str | None, str, dict]:
        """(cnpj, fonte ou motivo, detalhe para o extra)."""
        palavras = palavras_do_nome(nome)
        for nivel in _NIVEIS:
            chave = nucleo(palavras, nivel, self.marcas, self._cadastro.omitidas)
            if not chave:
                continue
            if nivel == 2 and len(chave) < _MINIMO_DE_PALAVRAS_SEM_MARCA:
                continue
            do_catalogo = self._catalogo.candidatos(chave, nivel, palavras)
            do_cadastro = self._cadastro.candidatos(chave, nivel, palavras)
            if do_catalogo:
                if len(do_catalogo) > 1:
                    return None, MOTIVO_EMPATE, self._empate(do_catalogo, nivel)
                cnpj = next(iter(do_catalogo))
                cotas_fora = sorted(
                    c for c in do_cadastro - do_catalogo
                    if tem_marca_de_cotas(self._cadastro.nomes[c])
                )
                if cotas_fora and not tem_marca_de_cotas(self._catalogo.nomes[cnpj]):
                    return None, MOTIVO_EMPATE, self._empate({cnpj, *cotas_fora}, nivel)
                return cnpj, FONTE_CATALOGO, self._casamento(
                    FONTE_CATALOGO, nivel, self._catalogo.nomes[cnpj]
                )
            if do_cadastro:
                if len(do_cadastro) > 1:
                    return None, MOTIVO_EMPATE, self._empate(do_cadastro, nivel)
                cnpj = next(iter(do_cadastro))
                return cnpj, FONTE_CADASTRO, self._casamento(
                    FONTE_CADASTRO, nivel, self._cadastro.nomes[cnpj]
                )
        return None, MOTIVO_SEM_CANDIDATO, {"motivo": MOTIVO_SEM_CANDIDATO}

    @staticmethod
    def _casamento(fonte: str, nivel: int, nome_oficial: str) -> dict:
        return {"fonte": fonte, "regra": _ROTULO_DO_NIVEL[nivel], "nome": nome_oficial}

    def _empate(self, cnpjs: set[str], nivel: int) -> dict:
        return {
            "motivo": MOTIVO_EMPATE,
            "regra": _ROTULO_DO_NIVEL[nivel],
            "candidatos": sorted(cnpjs),
            "nomes": [
                self._catalogo.nomes.get(c) or self._cadastro.nomes.get(c, "")
                for c in sorted(cnpjs)
            ],
        }
