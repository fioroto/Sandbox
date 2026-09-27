"""Carteira mensal (CDA) da CVM resumida por classe - o insumo do mapa de familias.

O `cda_fi_AAAAMM.zip` traz a carteira de cada classe em oito blocos por tipo de
ativo, mais o PL e as posicoes sob sigilo temporario. Duas perguntas saem dele:

- **quem investe em quem**: o bloco de cotas de fundos liga o espelho (feeder)
  ao master. O flag "fundo espelho" do extrato marca so uma fracao deles -
  medido em 26/09/2026, 44 dos 201 espelhos entre os gaps da vitrine XP;
- **o que a classe carrega**: a fracao da carteira em cada faixa de ativo, que
  separa, por exemplo, credito de emissor bancario (CDB, LF, DPGE) de credito
  corporativo (debentures, CRI, CRA).

O mapeamento de tipo de aplicacao para faixa foi medido no arquivo real (CDA
de 08/2026, valores distintos de TP_APLIC e TP_ATIVO por bloco), nao suposto:

    BLC_1  Titulos Publicos (LFT -> soberano pos; LTN, NTN-B/C/F/I -> pre e
           inflacao) e Operacoes Compromissadas (-> soberano pos)
    BLC_2  Cotas de Fundos - viram look-through no mapa; FIDC conta como credito
           corporativo, FII e FIP como outros
    BLC_3  diferencial de swap - fora do vetor
    BLC_4  Debentures, Acoes, BDR, certificado de deposito de acoes, recibos e
           bonus de subscricao; opcoes, futuros, termo e emprestimo tomado ficam
           fora do vetor
    BLC_5  Depositos a prazo e titulos de IF (LF, CDB/RDB, DPGE) -> bancario
    BLC_6  agronegocio (CRA, CPR, NCA, CDCA -> corporativo; LCA -> bancario),
           credito privado (CCI, NP, CCB) e debentures -> corporativo
    BLC_7  Investimento no Exterior
    BLC_8  Disponibilidades (-> soberano pos), outras aplicacoes e demais ativos
           nao codificados (-> outros); contas a pagar e a receber fora

Derivativos ficam fora do vetor porque o valor de mercado de futuro e de swap
e o ajuste, perto de zero - nao mede exposicao. Quem separa multimercado de
renda fixa no mapa e a exigencia de mesma categoria ANBIMA.

A carteira de cada competencia e resumida uma vez e guardada em
`data/cache/carteira/`, como a serie mensal do informe: o zip tem ~140 MB de
CSV descomprimido, e o resumo cabe em poucos MB.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market.datasets import dataset_cda
from shelf_monitor.market.fetch import fetch, read_member
from shelf_monitor.market.parse import iter_rows
from shelf_monitor.normalize import normalize_cnpj, normalize_text, parse_number

# As faixas do perfil de carteira, na ordem em que saem nos relatorios.
SOBERANO_POS = "soberano_pos"
SOBERANO_PRE_INFLACAO = "soberano_pre_inflacao"
CREDITO_BANCARIO = "credito_bancario"
CREDITO_CORPORATIVO = "credito_corporativo"
ACOES = "acoes"
EXTERIOR = "exterior"
OUTROS = "outros"
SOB_SIGILO = "sob_sigilo"
COTAS_NAO_RESOLVIDAS = "cotas_nao_resolvidas"

FAIXAS: tuple[str, ...] = (
    SOBERANO_POS, SOBERANO_PRE_INFLACAO, CREDITO_BANCARIO, CREDITO_CORPORATIVO,
    ACOES, EXTERIOR, OUTROS, SOB_SIGILO, COTAS_NAO_RESOLVIDAS,
)
ROTULOS_FAIXA: dict[str, str] = {
    SOBERANO_POS: "soberano pós e caixa",
    SOBERANO_PRE_INFLACAO: "soberano pré e inflação",
    CREDITO_BANCARIO: "crédito bancário",
    CREDITO_CORPORATIVO: "crédito corporativo",
    ACOES: "ações",
    EXTERIOR: "exterior",
    OUTROS: "outros ativos",
    SOB_SIGILO: "sob sigilo",
    COTAS_NAO_RESOLVIDAS: "cotas de fundos sem carteira",
}

# Chaves opacas do vetor de ativos: a posicao existe, mas nao se sabe qual e.
ATIVO_SIGILO = "sigilo"
ATIVO_SEM_IDENTIDADE = "sem_identidade"
ATIVOS_OPACOS = (ATIVO_SIGILO, ATIVO_SEM_IDENTIDADE)

# Os blocos que o resumo le, pela parte do nome do membro no zip.
BLOCOS: tuple[str, ...] = (
    "PL", "BLC_1", "BLC_2", "BLC_3", "BLC_4", "BLC_5", "BLC_6", "BLC_7", "BLC_8",
    "CONFID",
)

# Marcadores internos: resolvidos por coluna auxiliar ou pelo bloco.
_SOBERANO = "_soberano"
_AGRO = "_agro"
_COTAS = "_cotas"
_OUTROS_VM = "_outros_vm"

# TP_APLIC normalizado -> faixa. Medido na CDA de 08/2026.
_POR_APLICACAO: dict[str, str] = {
    "TITULOS PUBLICOS": _SOBERANO,
    "OPERACOES COMPROMISSADAS": SOBERANO_POS,
    "DISPONIBILIDADES": SOBERANO_POS,
    "DEPOSITOS A PRAZO E OUTROS TITULOS DE IF": CREDITO_BANCARIO,
    "DEBENTURES": CREDITO_CORPORATIVO,
    "TITULOS DE CREDITO PRIVADO": CREDITO_CORPORATIVO,
    "TITULOS LIGADOS AO AGRONEGOCIO": _AGRO,
    "ACOES": ACOES,
    "ACOES E OUTROS TVM CEDIDOS EM EMPRESTIMO": ACOES,
    "BRAZILIAN DEPOSITORY RECEIPT BDR": ACOES,
    "CERTIFICADO OU RECIBO DE DEPOSITO DE VALORES MOBILIARIOS": ACOES,
    "INVESTIMENTO NO EXTERIOR": EXTERIOR,
    "OUTRAS APLICACOES": OUTROS,
    "OUTROS VALORES MOBILIARIOS OFERTADOS PRIVADAMENTE": OUTROS,
    "OUTROS VALORES MOBILIARIOS REGISTRADOS NA CVM OBJETO DE OFERTA PUBLICA": _OUTROS_VM,
    "COTAS DE FUNDOS": _COTAS,
}

# Posicoes que nao entram no vetor: derivativos (o valor de mercado e o ajuste)
# e contas de passivo ou de compensacao.
_FORA_DO_VETOR: tuple[str, ...] = (
    "OPCOES", "MERCADO FUTURO", "COMPRAS A TERMO", "VENDAS A TERMO",
    "OBRIGACOES", "VALORES A PAGAR", "VALORES A RECEBER",
    "OUTRAS OPERACOES PASSIVAS", "DIFERENCIAL DE SWAP",
)

# TP_ATIVO das cotas de fundos que nao tem carteira na CDA de FI: o tipo do
# fundo investido ja diz a faixa. Os demais tentam o look-through no mapa.
_COTA_SEM_CARTEIRA: dict[str, str] = {
    "FIDC": CREDITO_CORPORATIVO,
    "FI IMOBILIARIO": OUTROS,
    "FI PARTICIPACOES": OUTROS,
}


@dataclass
class CarteiraClasse:
    """A carteira de uma classe numa competencia, resumida.

    `faixas` guarda as posicoes diretas, em reais; `cotas` guarda o que a classe
    tem em cotas de outras classes (CNPJ investido -> valor), que o mapa
    resolve por look-through. `faixa_das_cotas` diz em que faixa cai a cota
    quando o fundo investido nao tem carteira propria.
    """

    cnpj: str
    competencia: str
    nome: str = ""
    pl: float | None = None
    faixas: dict[str, float] = field(default_factory=dict)
    cotas: dict[str, float] = field(default_factory=dict)
    faixa_das_cotas: dict[str, str] = field(default_factory=dict)
    # Posicoes diretas por identidade do ativo (emissor, codigo ou fundo
    # offshore), em reais. E o que separa duas classes "100% exterior" do mesmo
    # gestor local que investem em fundos offshore diferentes - o vetor por
    # faixa as trata como identicas.
    ativos: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "n": self.nome,
            "pl": self.pl,
            "f": {k: round(v, 2) for k, v in self.faixas.items()},
            "c": {k: round(v, 2) for k, v in self.cotas.items()},
            "t": self.faixa_das_cotas,
            "a": {k: round(v, 2) for k, v in self.ativos.items()},
        }

    @classmethod
    def from_dict(cls, cnpj: str, competencia: str, dados: dict) -> "CarteiraClasse":
        return cls(
            cnpj=cnpj, competencia=competencia, nome=dados.get("n") or "",
            pl=dados.get("pl"), faixas=dict(dados.get("f") or {}),
            cotas=dict(dados.get("c") or {}),
            faixa_das_cotas=dict(dados.get("t") or {}),
            ativos=dict(dados.get("a") or {}),
        )


@dataclass
class ResumoCarteira:
    """Uma competencia da CDA resumida por classe."""

    competencia: str
    classes: dict[str, CarteiraClasse] = field(default_factory=dict)
    # "BLC_x: TP_APLIC" que o mapeamento nao conhece -> linhas. Entram como
    # "outros" e viram aviso, como as colunas novas da base de mercado.
    desconhecidos: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.classes)

    def to_dict(self) -> dict:
        return {
            "competencia": self.competencia,
            "desconhecidos": self.desconhecidos,
            "classes": {c: k.to_dict() for c, k in self.classes.items()},
        }

    @classmethod
    def from_dict(cls, dados: dict) -> "ResumoCarteira":
        competencia = dados["competencia"]
        return cls(
            competencia=competencia,
            classes={
                c: CarteiraClasse.from_dict(c, competencia, k)
                for c, k in (dados.get("classes") or {}).items()
            },
            desconhecidos=dict(dados.get("desconhecidos") or {}),
        )


def _cnpj_da_linha(linha: Mapping) -> str | None:
    return normalize_cnpj(linha.get("CNPJ_FUNDO_CLASSE") or linha.get("CNPJ_FUNDO"))


def _valor(linha: Mapping) -> float:
    valor = parse_number(linha.get("VL_MERC_POS_FINAL"))
    return valor if valor and valor > 0 else 0.0


def faixa_da_posicao(bloco: str, linha: Mapping) -> str | None:
    """A faixa de uma posicao da CDA; None quando ela fica fora do vetor.

    Devolve o marcador interno `_COTAS` para cota de fundo identificada (o
    chamador faz o look-through) e a string vazia para tipo desconhecido.
    """
    if bloco == "BLC_3":
        return None
    if bloco == "BLC_5":
        return CREDITO_BANCARIO
    if bloco == "BLC_7":
        return EXTERIOR
    aplicacao = normalize_text(linha.get("TP_APLIC"))
    if not aplicacao:
        return ""
    if aplicacao.startswith(_FORA_DO_VETOR):
        return None
    if aplicacao.startswith("COTAS DE FUNDOS"):
        aplicacao = "COTAS DE FUNDOS"
    elif aplicacao.startswith("DEBENTURES"):
        # 06/2026 grafava "Debêntures simples" e "Debêntures conversíveis".
        aplicacao = "DEBENTURES"
    faixa = _POR_APLICACAO.get(aplicacao)
    if faixa is None:
        return ""
    sigilo = bloco == "CONFID"
    if faixa == _SOBERANO:
        if sigilo:
            return SOB_SIGILO
        titulo = normalize_text(linha.get("TP_TITPUB"))
        if bloco == "BLC_1" and titulo:
            return SOBERANO_POS if "LETRAS FINANCEIRAS DO TESOURO" in titulo else SOBERANO_PRE_INFLACAO
        return OUTROS
    if faixa == _AGRO:
        if sigilo:
            return SOB_SIGILO
        return CREDITO_BANCARIO if normalize_text(linha.get("TP_ATIVO")) == "LCA" else CREDITO_CORPORATIVO
    if faixa == _COTAS:
        return SOB_SIGILO if sigilo else _COTAS
    if faixa == _OUTROS_VM:
        # No bloco de acoes sao recibos e bonus de subscricao (medido); no
        # BLC_8 misturam CRI com direito de subscricao - fica em outros.
        if sigilo:
            return SOB_SIGILO
        return ACOES if bloco == "BLC_4" else OUTROS
    return faixa


def identidade_do_ativo(bloco: str, linha: Mapping) -> str | None:
    """A chave que diz QUAL ativo e a posicao - None quando a fonte nao diz.

    Emissor quando o titulo e de credito (o que duas carteiras de credito da
    mesma gestora partilham e o risco do emissor, nao o numero da emissao);
    raiz do codigo de negociacao em acoes e debentures listadas (ITUB3 e ITUB4
    sao o mesmo emissor); o fundo offshore investido no exterior. Posicao sob
    sigilo nao tem identidade publica.
    """
    if bloco == "CONFID":
        return None

    def texto(*colunas: str) -> str:
        for coluna in colunas:
            valor = normalize_text(linha.get(coluna))
            if valor:
                return valor
        return ""

    aplicacao = normalize_text(linha.get("TP_APLIC"))
    if bloco == "BLC_1":
        if aplicacao.startswith("OPERACOES COMPROMISSADAS"):
            return "compromissada"
        chave = texto("CD_ISIN") or " ".join(
            filter(None, (texto("TP_TITPUB"), texto("DT_VENC")))
        )
        return f"titulo:{chave}" if chave else None
    if bloco == "BLC_4":
        codigo = texto("CD_ATIVO").replace(" ", "")
        if codigo:
            return f"ativo:{codigo[:4]}"
        isin = texto("CD_ISIN").replace(" ", "")
        return f"ativo:{isin[2:6]}" if len(isin) >= 6 else None
    if bloco == "BLC_5":
        emissor = normalize_cnpj(linha.get("CNPJ_EMISSOR"))
        return f"emissor:{emissor or texto('EMISSOR')}" if emissor or texto("EMISSOR") else None
    if bloco == "BLC_6":
        emissor = normalize_cnpj(linha.get("CPF_CNPJ_EMISSOR"))
        return f"emissor:{emissor or texto('EMISSOR')}" if emissor or texto("EMISSOR") else None
    if bloco == "BLC_7":
        chave = texto("EMISSOR", "DS_ATIVO_EXTERIOR", "CD_ATIVO_BV_MERC")
        return f"exterior:{chave}" if chave else None
    if bloco == "BLC_8":
        if aplicacao == "DISPONIBILIDADES":
            return "caixa"
        chave = texto("CPF_CNPJ_EMISSOR", "EMISSOR", "DS_ATIVO")
        return f"outro:{chave}" if chave else None
    return None


def resumir_competencia(
    competencia: str, membros: Mapping[str, Iterable[Mapping]]
) -> ResumoCarteira:
    """Resume as linhas de uma competencia da CDA por classe.

    `membros` vai do nome do bloco ("PL", "BLC_1".."BLC_8", "CONFID") as
    linhas dele, com o cabecalho original da CVM. Bloco ausente e bloco vazio.
    """
    resumo = ResumoCarteira(competencia=competencia)
    desconhecidos: Counter = Counter()

    def classe(cnpj: str, linha: Mapping) -> CarteiraClasse:
        atual = resumo.classes.get(cnpj)
        if atual is None:
            atual = resumo.classes[cnpj] = CarteiraClasse(
                cnpj=cnpj, competencia=competencia,
                nome=(linha.get("DENOM_SOCIAL") or "").strip(),
            )
        return atual

    for linha in membros.get("PL") or ():
        cnpj = _cnpj_da_linha(linha)
        if cnpj:
            classe(cnpj, linha).pl = parse_number(linha.get("VL_PATRIM_LIQ"))

    for bloco in BLOCOS:
        if bloco == "PL":
            continue
        for linha in membros.get(bloco) or ():
            cnpj = _cnpj_da_linha(linha)
            if not cnpj:
                continue
            faixa = faixa_da_posicao(bloco, linha)
            if faixa is None:
                continue
            valor = _valor(linha)
            if not valor:
                continue
            alvo = classe(cnpj, linha)
            if faixa == _COTAS:
                investido = normalize_cnpj(
                    linha.get("CNPJ_FUNDO_CLASSE_COTA") or linha.get("CNPJ_FUNDO_COTA")
                )
                tipo = normalize_text(linha.get("TP_ATIVO"))
                if not investido or investido == cnpj:
                    alvo.faixas[COTAS_NAO_RESOLVIDAS] = (
                        alvo.faixas.get(COTAS_NAO_RESOLVIDAS, 0.0) + valor
                    )
                    continue
                alvo.cotas[investido] = alvo.cotas.get(investido, 0.0) + valor
                alvo.faixa_das_cotas.setdefault(
                    investido, _COTA_SEM_CARTEIRA.get(tipo, COTAS_NAO_RESOLVIDAS)
                )
                continue
            if faixa == "":
                desconhecidos[f"{bloco}: {linha.get('TP_APLIC') or '(vazio)'}"] += 1
                faixa = OUTROS
            alvo.faixas[faixa] = alvo.faixas.get(faixa, 0.0) + valor
            # Posicao sem identidade publica entra no vetor de ativos com uma
            # chave opaca: pesa no denominador e nunca conta como ativo comum.
            ativo = identidade_do_ativo(bloco, linha) or (
                ATIVO_SIGILO if bloco == "CONFID" else ATIVO_SEM_IDENTIDADE
            )
            alvo.ativos[ativo] = alvo.ativos.get(ativo, 0.0) + valor

    resumo.desconhecidos = dict(sorted(desconhecidos.items()))
    return resumo


# --- disco --------------------------------------------------------------------


# Sobe quando o formato do resumo muda: cache de versao anterior e refeito.
_VERSAO_CACHE = 4


def pasta_cache(config: Config) -> Path:
    return config.cache_dir / "carteira"


def _cache_path(config: Config, competencia: str) -> Path:
    return pasta_cache(config) / f"{competencia}.json.gz"


def _assinatura(zip_path: Path) -> dict:
    estado = zip_path.stat()
    return {"tamanho": estado.st_size, "modificado": int(estado.st_mtime)}


def _linhas_do_membro(zip_path: Path, trecho: str) -> Iterable[dict]:
    """As linhas de um membro, lidas so quando alguem percorre o gerador.

    Preguicoso de proposito: `resumir_competencia` percorre um bloco por vez, e
    ler os dez membros antes (~140 MB de CSV, o maior passa de 150 mil linhas)
    multiplicaria o pico de memoria.
    """
    try:
        conteudo = read_member(zip_path, trecho)
    except SourceUnavailable:
        return
    yield from iter_rows(conteudo)


def ler_zip(zip_path: Path, competencia: str) -> ResumoCarteira:
    """Resume a competencia direto do zip da CVM, um membro por vez."""
    membros = {
        bloco: _linhas_do_membro(zip_path, f"cda_fi_{bloco.lower()}_{competencia}")
        for bloco in BLOCOS
    }
    return resumir_competencia(competencia, membros)


def carregar_competencia(
    config: Config, competencia: str, *, baixar: bool = True
) -> tuple[ResumoCarteira | None, str | None]:
    """O resumo de uma competencia, do cache, do zip em disco ou da CVM.

    Devolve (resumo, aviso). Resumo None quando a competencia nao esta
    publicada ou nao pode ser lida; o aviso diz por que. O cache e refeito
    quando o zip muda (a CVM republica competencias com retificacao).
    """
    dataset = dataset_cda(competencia)
    zip_path = config.market_raw_dir / dataset.filename
    aviso = None
    if baixar:
        try:
            fetch(config, dataset)
        except SourceUnavailable as exc:
            if "HTTP 404" in str(exc):
                return None, None  # ainda nao publicada: nao e falha
            aviso = f"carteira de {competencia[4:]}/{competencia[:4]} não baixada ({exc})"
    if not zip_path.is_file():
        return None, aviso

    cache = _cache_path(config, competencia)
    assinatura = _assinatura(zip_path)
    if cache.is_file():
        try:
            with gzip.open(cache, "rt", encoding="utf-8") as handle:
                dados = json.load(handle)
            if dados.get("origem") == assinatura and dados.get("versao") == _VERSAO_CACHE:
                return ResumoCarteira.from_dict(dados), aviso
        except (OSError, ValueError, KeyError):
            pass  # cache corrompido: refaz do zip

    try:
        resumo = ler_zip(zip_path, competencia)
    except (OSError, ValueError, SourceUnavailable) as exc:
        return None, f"carteira de {competencia[4:]}/{competencia[:4]} ilegível ({exc})"
    cache.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache, "wt", encoding="utf-8") as handle:
        json.dump(
            {**resumo.to_dict(), "origem": assinatura, "versao": _VERSAO_CACHE},
            handle, ensure_ascii=False,
        )
    return resumo, aviso
