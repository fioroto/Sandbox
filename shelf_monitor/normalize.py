"""Normalizacao de CNPJ, nomes de fundo e classes de ativo entre fontes heterogeneas."""
from __future__ import annotations

import re
import unicodedata

from shelf_monitor.models import AssetClass

# Tokens de forma juridica/estrutura que nao distinguem um fundo de outro.
# Removidos do slug para que "XP MACRO FIC FIM" e "XP MACRO FUNDO DE INVESTIMENTO
# EM COTAS MULTIMERCADO" convirjam para a mesma identidade.
_GENERIC_TOKENS = {
    "FUNDO", "FUNDOS", "INVESTIMENTO", "INVESTIMENTOS", "COTAS", "COTA",
    "FI", "FIC", "FIF", "FIM", "FIA", "FIRF", "FICFI", "FIQ",
    "RF", "MM", "LP", "CP", "IE", "RL",
    "MULTIMERCADO", "REFERENCIADO", "ACOES", "ACAO",
    "CREDITO", "PRIVADO", "RENDA", "FIXA",
    "RESPONSABILIDADE", "LIMITADA", "LTDA",
    "DE", "DA", "DO", "DAS", "DOS", "E", "EM", "A", "O",
    "CLASSE", "SUBCLASSE", "UNICA",
}

_PUNCT_RE = re.compile(r"[^A-Z0-9\s]")
_SPACE_RE = re.compile(r"\s+")
_DIGITS_RE = re.compile(r"\D")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")


def strip_accents(text: str) -> str:
    """Remove acentos preservando o restante dos caracteres."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_cnpj(raw: str | None) -> str | None:
    """Reduz um CNPJ a 14 digitos. Devolve None se nao houver 14 digitos."""
    if not raw:
        return None
    digits = _DIGITS_RE.sub("", str(raw))
    if len(digits) == 14:
        return digits
    # Alguns feeds omitem zeros a esquerda ao serializar como numero.
    if 0 < len(digits) < 14:
        return digits.zfill(14)
    return None


def cnpj_root(cnpj: str | None) -> str | None:
    """Raiz do CNPJ (8 primeiros digitos) - identifica a empresa gestora."""
    normalized = normalize_cnpj(cnpj)
    return normalized[:8] if normalized else None


def format_cnpj(cnpj: str | None) -> str:
    """Formata para exibicao: 00.000.000/0001-00."""
    normalized = normalize_cnpj(cnpj)
    if not normalized:
        return "-"
    return (
        f"{normalized[:2]}.{normalized[2:5]}.{normalized[5:8]}"
        f"/{normalized[8:12]}-{normalized[12:]}"
    )


def normalize_cvm_code(raw: object) -> str | None:
    """Canonicaliza um codigo CVM para servir de chave de indice.

    Aceita qualquer coisa porque as fontes divergem no tipo: o CSV da CVM traz
    texto (as vezes com zeros a esquerda), e os feeds JSON das casas serializam
    como numero - `555001.0` vindo de um float e `555001` do mesmo codigo.

    Ao contrario do CNPJ, o codigo nao tem largura fixa, entao zfill nao serve:
    a canonizacao e por REMOCAO de zeros a esquerda, e so quando o codigo e
    puramente numerico. Codigo alfanumerico fica como esta apos a limpeza.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool e int em Python; aqui nunca e codigo
        return None
    if isinstance(raw, float):
        if raw != raw or raw in (float("inf"), float("-inf")):  # NaN/inf
            return None
        if raw.is_integer():
            raw = int(raw)
    text = _NON_ALNUM_RE.sub("", strip_accents(str(raw)).upper())
    if not text:
        return None
    if text.isdigit():
        text = text.lstrip("0")
    return text or None


def normalize_text(text: str | None) -> str:
    """Upper, sem acentos, sem pontuacao, espacos colapsados."""
    if not text:
        return ""
    cleaned = _PUNCT_RE.sub(" ", strip_accents(text).upper())
    return _SPACE_RE.sub(" ", cleaned).strip()


def name_slug(name: str | None) -> str:
    """Slug estavel de nome de fundo, sem tokens genericos de forma juridica."""
    normalized = normalize_text(name)
    if not normalized:
        return ""
    tokens = [t for t in normalized.split(" ") if t and t not in _GENERIC_TOKENS]
    if not tokens:
        # Nome composto so de tokens genericos: preserva o original normalizado.
        tokens = normalized.split(" ")
    return "-".join(tokens).lower()


# Ordem importa: o primeiro padrao que casar define a classe.
# Previdencia e internacional antes das classes base porque um fundo pode ser
# "previdencia renda fixa" - a intencao competitiva relevante e a previdencia.
# Renda fixa por ultimo porque seus termos sao os mais genericos.
# Os termos sao casados com fronteira de palavra: sem isso "DURACAO" casaria
# com "ACAO" e um fundo de renda fixa viraria fundo de acoes.
_CLASS_TERMS: list[tuple[AssetClass, tuple[str, ...]]] = [
    (AssetClass.PREVIDENCIA, ("PREVIDENCIA", "PREVIDENCIARIO", "PGBL", "VGBL")),
    (
        AssetClass.INTERNACIONAL,
        ("EXTERIOR", "GLOBAL", "INTERNACIONAL", "WORLDWIDE", "OFFSHORE", "BDR",
         "US ", "WORLD"),
    ),
    (AssetClass.CAMBIAL, ("CAMBIAL", "CAMBIO", "DOLAR")),
    (
        AssetClass.ACOES,
        ("ACOES", "ACAO", "EQUITY", "EQUITIES", "STOCK", "SMALL CAP", "SMALL CAPS",
         "DIVIDENDOS", "IBOVESPA", "FIA"),
    ),
    (
        AssetClass.MULTIMERCADO,
        ("MULTIMERCADO", "MULTI MERCADO", "MACRO", "LONG SHORT", "LONG BIASED",
         "BALANCEADO", "HEDGE", "FIM"),
    ),
    (
        AssetClass.RENDA_FIXA,
        ("RENDA FIXA", "REFERENCIADO", "DI", "CDI", "CREDITO PRIVADO", "CURTO PRAZO",
         "LIQUIDEZ", "INFLACAO", "PREFIXADO", "POS FIXADO", "DEBENTURE",
         "DEBENTURES", "FIXED INCOME", "IPCA", "SELIC"),
    ),
]

_CLASS_PATTERNS: list[tuple[AssetClass, re.Pattern]] = [
    (
        asset_class,
        re.compile(
            "|".join(rf"\b{re.escape(term.strip())}\b" for term in terms)
        ),
    )
    for asset_class, terms in _CLASS_TERMS
]


def classify_asset_class(*hints: str | None) -> AssetClass:
    """Deriva a classe interna a partir de categoria ANBIMA, tipo CVM e nome do fundo.

    Recebe varias pistas em ordem de confiabilidade (categoria primeiro, nome por
    ultimo). Cada pista e testada por inteiro antes de passar para a proxima, para
    que a categoria declarada prevaleca sobre palavras soltas no nome do fundo.
    """
    for hint in hints:
        normalized = normalize_text(hint)
        if not normalized:
            continue
        for asset_class, pattern in _CLASS_PATTERNS:
            if pattern.search(normalized):
                return asset_class
    return AssetClass.OUTROS


_THOUSAND_GROUPS_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")


def _clean_numeric(raw: object) -> str | None:
    if raw is None:
        return None
    text = re.sub(r"[^\d,.\-]", "", str(raw).strip())
    if not text or text in {"-", ".", ","}:
        return None
    return text


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def parse_number(raw: object) -> float | None:
    """Converte um numero de fonte BR ou EN para float.

    Onde o formato e ambiguo ("1.500"), assume ponto decimal - a convencao de
    payloads JSON, que e de onde a maior parte dos numeros vem. Para valores em
    reais escritos em texto, use `parse_money`, que resolve a ambiguidade ao
    contrario.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)

    text = _clean_numeric(raw)
    if text is None:
        return None

    if "," in text and "." in text:
        # O separador decimal e o ultimo a aparecer.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        # Nenhum formato tem dois separadores decimais: sao milhares.
        text = text.replace(".", "")

    return _to_float(text)


def parse_money(raw: object) -> float | None:
    """Converte um valor monetario escrito em portugues.

    Aqui "R$ 1.000" e mil reais, nao um real - o inverso da suposicao de
    `parse_number`. A desambiguacao usa a forma dos grupos: pontos que separam
    blocos de exatamente tres digitos sao milhar.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)

    text = _clean_numeric(raw)
    if text is None:
        return None

    negative = text.startswith("-")
    digits = text.lstrip("-")

    if "," in digits:
        # Com virgula presente, ela e o decimal e o ponto e milhar.
        digits = digits.replace(".", "").replace(",", ".")
    elif _THOUSAND_GROUPS_RE.match(digits):
        # "1.000", "25.000", "1.000.000" - grupos de tres, sem decimais.
        digits = digits.replace(".", "")
    elif digits.count(".") > 1:
        digits = digits.replace(".", "")

    value = _to_float(digits)
    if value is None:
        return None
    return -value if negative else value


def parse_int(raw: object) -> int | None:
    value = parse_number(raw)
    return int(value) if value is not None else None
