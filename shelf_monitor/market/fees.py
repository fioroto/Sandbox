"""Taxas e condicoes comerciais, do extrato de informacoes da CVM.

Tres particularidades do arquivo governam este modulo:

1. Ele guarda ~5 anos de entregas, uma linha por classe por data-base. O estado
   atual e a maior DT_COMPTC de cada classe - as anteriores sao historico.
2. TAXA_ADM e polimorfica sem discriminador: para investidor qualificado o
   regulamento admite texto livre, entao a mesma coluna carrega ora percentual
   ao ano, ora um valor fixo em reais. Um ranking de custo que ignore isso
   coloca um fundo de "R$ 30.000 por ano" no topo como se cobrasse 30.000%.
3. **Coluna que nao existe falha em silencio.** `_get` devolve None para nome
   ausente, e `unknown_columns` so registra o caminho inverso (coluna no
   arquivo que o modelo nao conhece). Entao um nome errado aqui nao levanta
   nada: a coluna simplesmente sai vazia para sempre.

   Foi o que aconteceu ate 2026-09-20. O parser procurava `VL_TAXA_ENTRADA`,
   `VL_TAXA_SAIDA`, `QT_DIA_CONVER_COTA` e `VL_SALDO_MIN`; o arquivo traz
   `TAXA_INGRESSO_REAL`/`TAXA_INGRESSO_PR`, `TAXA_SAIDA_REAL`/`TAXA_SAIDA_PR`
   e `QT_DIA_CONVERSAO_COTA`, e **nao tem coluna de saldo minimo**. Medido nas
   38.547 linhas de `taxas.csv`: toda coluna de nome certo saia em 100%
   (`taxa_adm`, `aplicacao_inicial_minima`, `dias_pagamento_resgate`) e toda
   coluna de nome errado saia em 0,0%. Por isso `min_balance` foi removido em
   vez de renomeado - a fonte nao tem o dado - e os nomes conferidos contra o
   cabecalho real, nao contra o que o esquema deveria ter.

Ingresso e saida sao lidos em tres colunas cada (flag S/N + valor em reais +
valor em percentual) porque a fonte ja discrimina a unidade, e porque o flag e
o unico campo preenchido em 100% das linhas: sem ele, "nao cobra" e "nao
informou" ficariam indistinguiveis. Medido: 44 classes (0,1%) cobram ingresso e
1.281 (3,3%) cobram saida.

As colunas de mandato (`ATIVO_CRED_PRIV`, `INVEST_EXTERIOR`, `OPER_DERIV`,
`OPER_VL_SUPERIOR_PL`, `FUNDO_ESPELHO` e os tetos correspondentes) descrevem o
que o regulamento **autoriza**, nunca o que a carteira carrega.
"""
from __future__ import annotations

import re

from pathlib import Path

from shelf_monitor.collectors.cvm import _get
from shelf_monitor.market.models import ClassFees, FeeUnit
from shelf_monitor.market.parse import iter_rows, parse_date
from shelf_monitor.texto import contagem
from shelf_monitor.normalize import (
    normalize_cnpj,
    normalize_text,
    parse_money,
    parse_number,
)

# Acima deste valor, um numero em TAXA_ADM quase certamente e reais e nao
# percentual: taxa de administracao de fundo brasileiro nao passa disso.
# A heuristica e usada em producao por quem consome o mesmo arquivo.
_PERCENT_CEILING = 20.0

_CURRENCY_HINTS = ("R$", "REAIS", "REAL")
_TEXT_HINTS = ("REGULAMENTO", "CONFORME", "NAO HA", "ISENTO", "VARIAVEL", "SEM TAXA")

# Um numero com seus separadores, como aparece no meio de texto livre.
# Precisa aceitar qualquer quantidade de digitos antes do primeiro separador,
# senao "30000" e truncado em "300".
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def resolve_admin_fee(raw: str | None) -> tuple[float | None, FeeUnit, str | None]:
    """Interpreta TAXA_ADM. Devolve (valor, unidade, texto original).

    O texto original e sempre preservado: quando a heuristica erra, o dado
    bruto continua la para conferencia.
    """
    if raw is None:
        return None, FeeUnit.UNKNOWN, None

    text = str(raw).strip()
    if not text:
        return None, FeeUnit.UNKNOWN, None

    upper = normalize_text(text)
    has_currency = "R$" in text or any(hint in upper for hint in _CURRENCY_HINTS)

    # Frase explicativa. O teste vem antes de qualquer parsing porque textos
    # como "NAO HA TAXA ATE 31/12/2026" contem digitos que, extraidos as cegas,
    # viram uma taxa de trinta e um milhoes.
    if any(hint in upper for hint in _TEXT_HINTS) and not has_currency:
        return None, FeeUnit.TEXT, text

    number = _first_number(text)
    if number is None:
        return None, FeeUnit.TEXT, text

    if has_currency:
        return parse_money(number) , FeeUnit.CURRENCY, text

    value = parse_number(number)
    if value is None:
        return None, FeeUnit.TEXT, text

    if value > _PERCENT_CEILING:
        # Sem simbolo, mas grande demais para ser percentual ao ano. Se o texto
        # tem grupos de milhar ("30.000"), o ponto era separador e nao decimal -
        # reinterpretar evita transformar R$ 30.000 em R$ 30.
        currency_value = parse_money(number)
        return (
            currency_value if currency_value is not None else value,
            FeeUnit.CURRENCY,
            text,
        )
    return value, FeeUnit.PERCENT, text


def _first_number(text: str) -> str | None:
    """Extrai o primeiro numero do texto, com seus separadores.

    Necessario porque a coluna aceita texto livre: "0,5% a.a." tem um
    percentual perfeitamente legivel que um parser aplicado ao texto inteiro
    descartaria.
    """
    match = _NUMBER_RE.search(text)
    return match.group(0) if match else None


def parse_fees(source: Path | bytes) -> dict[str, ClassFees]:
    """extrato_fi.csv -> taxas por CNPJ de classe, so a entrega mais recente.

    Le em streaming: o arquivo tem ~100 MB e cinco anos de entregas, mas o
    resultado e uma entrada por classe, entao a memoria fica no tamanho do
    resultado e nao no do arquivo.

    Classe cuja entrega nao tem data em nenhuma linha sai num aviso no console.
    O diagnostico existia (o `undated` abaixo) e nunca chegava a lugar nenhum:
    se a CVM renomear `DT_COMPTC`, TODA classe cai nesse caminho e a base trava
    na entrega mais antiga - exatamente o modo de falha silenciosa que o resto
    do modulo evita.
    """
    latest: dict[str, ClassFees] = {}
    undated: set[str] = set()

    for row in iter_rows(source):
        cnpj = normalize_cnpj(
            _get(row, "CNPJ_FUNDO_CLASSE", "CNPJ_Fundo_Classe", "CNPJ_FUNDO", "CNPJ_Classe")
        )
        if not cnpj:
            continue

        reference = parse_date(_get(row, "DT_COMPTC", "Data_Competencia"))
        existing = latest.get(cnpj)
        if existing is not None:
            if reference is None and existing.reference_date is None:
                # Sem data em nenhum dos dois - provavelmente a CVM renomeou a
                # coluna. O arquivo vem em ordem cronologica, entao a ultima
                # linha e a entrega mais recente; manter a primeira travaria a
                # base na entrega mais antiga, em silencio.
                undated.add(cnpj)
            elif not _is_newer(reference, existing.reference_date):
                continue

        admin_fee, unit, admin_raw = resolve_admin_fee(_get(row, "TAXA_ADM"))

        latest[cnpj] = ClassFees(
            cnpj=cnpj,
            reference_date=reference,
            admin_fee=admin_fee,
            admin_fee_unit=unit,
            admin_fee_raw=admin_raw,
            performance_fee=parse_number(_get(row, "TAXA_PERFM")),
            performance_fee_benchmark=_get(row, "PARAM_TAXA_PERFM"),
            has_performance_fee=_get(row, "EXISTE_TAXA_PERFM"),
            has_entry_fee=_flag(_get(row, "EXISTE_TAXA_INGRESSO")),
            entry_fee_pct=parse_number(_get(row, "TAXA_INGRESSO_PR")),
            entry_fee_amount=parse_number(_get(row, "TAXA_INGRESSO_REAL")),
            has_exit_fee=_flag(_get(row, "EXISTE_TAXA_SAIDA")),
            exit_fee_pct=parse_number(_get(row, "TAXA_SAIDA_PR")),
            exit_fee_amount=parse_number(_get(row, "TAXA_SAIDA_REAL")),
            custody_fee_max=parse_number(_get(row, "TAXA_CUSTODIA_MAX")),
            min_initial=parse_money(_get(row, "APLIC_MIN")),
            redemption_quotation_days=_parse_days(_get(row, "QT_DIA_CONVERSAO_COTA")),
            redemption_payment_days=_parse_days(_get(row, "QT_DIA_PAGTO_RESGATE")),
            target_audience=_get(row, "PUBLICO_ALVO"),
            investment_policy=_get(row, "POLIT_INVEST"),
            # O extrato nao tem coluna de tributacao: `TRIB_LPRAZO` e do
            # `cad_fi` e era lido aqui por engano, entao a coluna saia vazia em
            # 38.668 de 38.668 classes. A tributacao vem do cadastro
            # (`Tributacao_Longo_Prazo`, e `TRIB_LPRAZO` no legado).
            long_term_taxation=None,
            credit_private=_flag(_get(row, "ATIVO_CRED_PRIV")),
            credit_private_max=parse_number(_get(row, "APLIC_MAX_ATIVO_CRED_PRIV")),
            foreign=_flag(_get(row, "INVEST_EXTERIOR")),
            foreign_max=parse_number(_get(row, "APLIC_MAX_ATIVO_EXTERIOR")),
            derivatives=_flag(_get(row, "OPER_DERIV")),
            leverage=_flag(_get(row, "OPER_VL_SUPERIOR_PL")),
            leverage_factor=parse_number(_get(row, "FATOR_OPER_VL_SUPERIOR_PL")),
            mirror_fund=_flag(_get(row, "FUNDO_ESPELHO")),
        )

    if undated:
        print(
            f"  aviso: {contagem(len(undated), 'classe', 'classes')} sem data de "
            "competência no extrato - a coluna DT_COMPTC pode ter sido renomeada; a "
            f"taxa {'dela' if len(undated) == 1 else 'delas'} vem da última linha do "
            "arquivo, não da entrega mais recente"
        )

    return latest


def _is_newer(candidate, current) -> bool:
    """Uma data ausente nunca substitui uma conhecida."""
    if candidate is None:
        return False
    if current is None:
        return True
    return candidate > current


def _parse_days(raw: str | None) -> int | None:
    value = parse_number(raw)
    return int(value) if value is not None else None


def _flag(raw: str | None) -> str | None:
    """Normaliza um campo S/N da CVM. Vazio continua None - nao e "N"."""
    text = (raw or "").strip().upper()
    return text[:1] if text[:1] in ("S", "N") else None
