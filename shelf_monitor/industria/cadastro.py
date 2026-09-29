"""O cadastro que o monitor da industria usa: um produto por subclasse.

Le o retrato mensal do cadastro (`market/historico.py`) e devolve os produtos
com os campos ja resolvidos na ordem certa - a subclasse antes da classe, a
classe antes do extrato. A ordem importa: 3.392 classes com subclasses tem
publico e exclusividade em branco no nivel da classe (medido em 28/09/2026), e
a regra que so olhasse a classe deixaria todas passarem.

Produto e a unidade comercial: a subclasse, ou a propria classe quando ela nao
tem subclasse, ou o fundo legado (nao adaptado a RCVM 175), que nao tem classe.
A chave (CNPJ da classe, ID da subclasse) e a mesma do informe diario - os IDs
de subclasse do cadastro e do informe batem (330 de 400 classes conferidas em
08/2026 com os mesmos IDs; nas demais, o informe traz um subconjunto).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from shelf_monitor.config import Config
from shelf_monitor.market.historico import competencias_com_retrato, ler_retrato
from shelf_monitor.normalize import normalize_text, parse_number

# Publico-alvo normalizado. A CVM escreve de tres jeitos ("Público Geral",
# "PÚBLICO EM GERAL", "Investidores Qualificados"...).
GERAL, QUALIFICADO, PROFISSIONAL = "geral", "qualificado", "profissional"
ROTULOS_PUBLICO = {
    GERAL: "Público geral",
    QUALIFICADO: "Qualificado",
    PROFISSIONAL: "Profissional",
    "": "Não informado",
}
# Do mais aberto ao mais fechado: a classe com subclasses de varios publicos
# e oferecida ao mais aberto deles.
_ORDEM_PUBLICO = (GERAL, QUALIFICADO, PROFISSIONAL)


def publico_normalizado(texto: str | None) -> str:
    valor = normalize_text(texto)
    if not valor:
        return ""
    if "PROFISSIONA" in valor:
        return PROFISSIONAL
    if "QUALIFICAD" in valor:
        return QUALIFICADO
    if "GERAL" in valor:
        return GERAL
    return ""


def _sim(texto: str | None) -> bool | None:
    valor = (texto or "").strip().upper()[:1]
    return {"S": True, "N": False}.get(valor)


def _data(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def _inteiro(texto: str | None) -> int | None:
    numero = parse_number(texto)
    return None if numero is None else int(round(numero))


@dataclass(slots=True)
class Produto:
    """Uma linha comercial do cadastro, com os campos ja resolvidos."""

    classe: str
    subclasse: str
    nivel: str
    nome: str
    nome_subclasse: str = ""
    fundo_cnpj: str = ""
    gestor: str = ""
    gestor_cnpj: str = ""
    administrador: str = ""
    administrador_cnpj: str = ""
    tipo: str = ""
    fundo_tipo: str = ""
    situacao: str = ""
    inicio_situacao: date | None = None
    cancelamento: date | None = None
    inicio: date | None = None
    classificacao_cvm: str = ""
    classificacao_anbima: str = ""
    indicador: str = ""
    classe_cotas: bool | None = None
    tributacao: str = ""
    esg: bool | None = None
    publico: str = ""
    exclusivo: bool | None = None
    condominio: str = ""
    previdenciario: bool | None = None
    exclusivo_previdencia: bool | None = None
    exclusivo_inr: bool | None = None
    pl_cadastro: float | None = None
    data_pl: date | None = None
    taxa_adm: float | None = None
    taxa_adm_unidade: str = ""
    taxa_performance: float | None = None
    aplicacao_minima: float | None = None
    dias_cotizacao: int | None = None
    dias_pagamento: int | None = None
    extrato_data: date | None = None
    extrato_publico: str = ""
    espelho_declarado: bool | None = None

    @property
    def chave(self) -> tuple[str, str]:
        return (self.classe, self.subclasse)

    @property
    def raiz_gestor(self) -> str:
        return self.gestor_cnpj[:8] if len(self.gestor_cnpj) == 14 else self.gestor_cnpj

    @property
    def dmais(self) -> int | None:
        """D+ total do resgate: cotizacao mais pagamento."""
        if self.dias_cotizacao is None:
            return None
        return self.dias_cotizacao + (self.dias_pagamento or 0)


def _produto_da_linha(linha: dict[str, str]) -> Produto:
    nivel = linha.get("nivel", "")
    subclasse = nivel == "subclasse"
    publico = (
        publico_normalizado(linha.get("subclasse_publico_alvo")) if subclasse else ""
    ) or publico_normalizado(linha.get("classe_publico_alvo")) \
        or publico_normalizado(linha.get("extrato_publico_alvo"))
    exclusivo = (_sim(linha.get("subclasse_exclusivo")) if subclasse else None)
    if exclusivo is None:
        exclusivo = _sim(linha.get("classe_exclusivo"))
    situacao = (linha.get("subclasse_situacao") if subclasse else "") or linha.get("classe_situacao") or linha.get("fundo_situacao") or ""
    inicio_situacao = (
        _data(linha.get("subclasse_inicio_situacao")) if subclasse and linha.get("subclasse_situacao") else None
    ) or _data(linha.get("classe_inicio_situacao")) or _data(linha.get("fundo_inicio_situacao"))
    return Produto(
        classe=linha.get("classe_cnpj", ""),
        subclasse=linha.get("subclasse_id", "") if subclasse else "",
        nivel=nivel,
        nome=linha.get("classe_denominacao", ""),
        nome_subclasse=linha.get("subclasse_denominacao", "") if subclasse else "",
        fundo_cnpj=linha.get("fundo_cnpj", ""),
        gestor=linha.get("gestor", ""),
        gestor_cnpj=linha.get("gestor_cnpj", ""),
        administrador=linha.get("administrador", ""),
        administrador_cnpj=linha.get("administrador_cnpj", ""),
        tipo=linha.get("classe_tipo", ""),
        fundo_tipo=linha.get("fundo_tipo", ""),
        situacao=situacao,
        inicio_situacao=inicio_situacao,
        cancelamento=_data(linha.get("fundo_data_cancelamento")),
        inicio=(
            _data(linha.get("classe_data_inicio"))
            or _data(linha.get("classe_data_registro"))
            or _data(linha.get("fundo_data_registro"))
        ),
        classificacao_cvm=linha.get("classificacao_cvm", ""),
        classificacao_anbima=linha.get("classificacao_anbima", ""),
        indicador=linha.get("indicador_desempenho", ""),
        classe_cotas=_sim(linha.get("classe_cotas")),
        tributacao=linha.get("tributacao_longo_prazo", ""),
        esg=_sim(linha.get("classe_esg")),
        publico=publico,
        exclusivo=exclusivo,
        condominio=(
            (linha.get("subclasse_condominio") if subclasse else "")
            or linha.get("classe_condominio") or ""
        ),
        previdenciario=_sim(linha.get("subclasse_previdenciario")) if subclasse else None,
        exclusivo_previdencia=(
            _sim(linha.get("subclasse_exclusivo_previdencia")) if subclasse else None
        ),
        exclusivo_inr=_sim(linha.get("subclasse_exclusivo_inr")) if subclasse else None,
        pl_cadastro=parse_number(linha.get("classe_patrimonio_liquido")),
        data_pl=_data(linha.get("classe_data_patrimonio_liquido")),
        taxa_adm=parse_number(linha.get("taxa_adm")),
        taxa_adm_unidade=linha.get("taxa_adm_unidade", ""),
        taxa_performance=parse_number(linha.get("taxa_performance")),
        aplicacao_minima=parse_number(linha.get("aplicacao_inicial_minima")),
        dias_cotizacao=_inteiro(linha.get("dias_cotizacao_resgate")),
        dias_pagamento=_inteiro(linha.get("dias_pagamento_resgate")),
        extrato_data=_data(linha.get("extrato_data_base")),
        extrato_publico=linha.get("extrato_publico_alvo", ""),
        espelho_declarado=_sim(linha.get("fundo_espelho_declarado")),
    )


@dataclass
class Cadastro:
    """Os produtos de um retrato, indexados pela chave do informe."""

    competencia: str
    produtos: dict[tuple[str, str], Produto]
    _por_classe: dict[str, list[Produto]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for produto in self.produtos.values():
            self._por_classe.setdefault(produto.classe, []).append(produto)

    def __len__(self) -> int:
        return len(self.produtos)

    @property
    def classes(self) -> dict[str, list[Produto]]:
        return self._por_classe

    def produto(self, cnpj: str, subclasse: str = "") -> Produto | None:
        """O produto de uma linha do informe (CNPJ, ID_SUBCLASSE).

        Sem a subclasse exata, cai para a classe. Classe com subclasses que
        aparece no informe sem ID (mes de transicao) vira a visao da classe:
        publico do produto mais aberto, exclusiva so se todas forem.
        """
        encontrado = self.produtos.get((cnpj, subclasse))
        if encontrado is not None:
            return encontrado
        filhos = self._por_classe.get(cnpj)
        if not filhos:
            return None
        if len(filhos) == 1:
            return filhos[0]
        return visao_da_classe(filhos)


def visao_da_classe(filhos: list[Produto]) -> Produto:
    """A classe vista inteira, a partir das subclasses dela."""
    base = filhos[0]
    publicos = [p.publico for p in filhos if p.publico]
    publico = min(publicos, key=_ORDEM_PUBLICO.index) if publicos else ""
    exclusivos = [p.exclusivo for p in filhos]
    return Produto(
        **{
            **{nome: getattr(base, nome) for nome in Produto.__slots__},
            "subclasse": "",
            "nivel": "classe",
            "nome_subclasse": "",
            "publico": publico,
            "exclusivo": all(exclusivos) if all(e is not None for e in exclusivos) else None,
            "previdenciario": all(p.previdenciario for p in filhos) or None,
            "exclusivo_previdencia": all(p.exclusivo_previdencia for p in filhos) or None,
            "exclusivo_inr": all(p.exclusivo_inr for p in filhos) or None,
        }
    )


def carregar_cadastro(config: Config, competencia: str | None = None) -> Cadastro | None:
    """O cadastro do retrato da competencia (AAAAMM), ou do mais recente."""
    disponiveis = competencias_com_retrato(config)
    if not disponiveis:
        return None
    if competencia is None or competencia not in disponiveis:
        anteriores = [c for c in disponiveis if competencia is None or c <= competencia]
        competencia = anteriores[-1] if anteriores else disponiveis[-1]
    produtos: dict[tuple[str, str], Produto] = {}
    for linha in ler_retrato(config, competencia):
        produto = _produto_da_linha(linha)
        if produto.classe:
            produtos.setdefault(produto.chave, produto)
    return Cadastro(competencia, produtos)
