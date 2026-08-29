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


def parse_number(raw: object) -> float | None:
    """Converte valores monetarios/percentuais de fontes BR ou EN para float.

    Aceita "1.234,56" (BR), "1234.56" (EN), "R$ 1.000,00" e numeros nativos.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip()
    if not text:
        return None

    text = re.sub(r"[^\d,.\-]", "", text)
    if not text or text in {"-", ".", ","}:
        return None

    if "," in text and "." in text:
        # O separador decimal e o ultimo a aparecer.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def parse_int(raw: object) -> int | None:
    value = parse_number(raw)
    return int(value) if value is not None else None
