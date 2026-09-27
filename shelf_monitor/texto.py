"""Texto das saidas: reparo de charset e concordancia de numero.

Quando o servidor nao declara charset, o `requests` le text/* como ISO-8859-1,
e o UTF-8 da pagina vira mojibake: medido em 2026-09-24, 132 dos 466 nomes da
vitrine do Inter estavam gravados como "DÃ³lar", "ClÃ¡ssico", "AmÃ©rica". O
`http.get` passou a decodificar certo; este modulo repara o que ja foi gravado,
na leitura, para o relatorio nao publicar o nome quebrado nem contar o conserto
como mudanca de nome na comparacao com a coleta anterior.

O mesmo defeito tem uma segunda forma: o nome vem decomposto (NFD, letra +
acento combinante U+0301) e o acento vira "Ì\\x81" - 1 dos 466 nomes do Inter
em 2026-09-24 ("SulameÌ\\x81rica"), alem dos 132 acima. E 4 dos 468 nomes da
Nu chegam em NFD corretos, que so aparecem certos quando o leitor compoe. Por
isso o reparo termina em NFC.

Sem dependencias do resto do pacote: `models` e `http` importam daqui.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

# Um byte inicial de sequencia UTF-8 (0xC2-0xF4) lido como Latin-1 seguido de
# um byte de continuacao (0x80-0xBF): "ó" = C3 B3 -> "Ã³"; U+0301 = CC 81 ->
# "Ì\x81". Portugues correto nao tem essa sequencia: "INFLAÇÃO" e "CÂMBIO" tem
# Ã/Â seguidos de letra. A volta so e aceita se o texto inteiro for UTF-8
# valido, o que protege o texto que ja estava certo.
_MOJIBAKE = re.compile("[\u00c2-\u00f4][\u0080-\u00bf]")
_COMBINANTE = re.compile("[\u0300-\u036f]")


def consertar_mojibake(texto: str | None) -> str | None:
    """"DÃ³lar" -> "Dólar"; acento decomposto -> composto. Texto certo volta intacto."""
    if not texto:
        return texto
    if _MOJIBAKE.search(texto):
        try:
            texto = texto.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    if _COMBINANTE.search(texto):
        texto = unicodedata.normalize("NFC", texto)
    return texto


def milhar(n: int) -> str:
    """"6.631" - separador de milhar brasileiro, sem casas decimais."""
    return f"{n:,}".replace(",", ".")


def contagem(n: int, singular: str, plural: str) -> str:
    """"1 alteração", "3 alterações", "6.631 fundos" - concorda e separa o milhar.

    O assunto do e-mail de 2026-09-24 saiu "1 alterações", e o digest do dia
    trazia "1 entradas" e "1 saídas" nas linhas por casa.
    """
    return f"{milhar(n)} {singular if n == 1 else plural}"


_MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def data_br(valor: str | date | None) -> str:
    """"2026-07-01" (ou um date) -> "01/07/2026". Vazio vira "-".

    Os blocos guardam a data em ISO, que e o que o JSON do dashboard e os testes
    comparam; a conversao acontece so na hora de escrever para gente. Em
    2026-09-24 o digest dizia "2026-07-01 a 2026-09-23" sem dizer que eram tres
    meses.
    """
    if not valor:
        return "-"
    if isinstance(valor, str):
        try:
            valor = date.fromisoformat(valor[:10])
        except ValueError:
            return valor
    return valor.strftime("%d/%m/%Y")


def competencia_br(yyyymm: str | None) -> str:
    """"202607" -> "jul/2026". O que nao for AAAAMM volta como veio."""
    if not yyyymm or len(yyyymm) != 6 or not yyyymm.isdigit() or not 1 <= int(yyyymm[4:]) <= 12:
        return yyyymm or "-"
    return f"{_MESES[int(yyyymm[4:]) - 1]}/{yyyymm[:4]}"


def periodo_br(de: str | date | None, ate: str | date | None) -> str:
    """"de 01/07/2026 a 23/09/2026"."""
    return f"de {data_br(de)} a {data_br(ate)}"


def meses_br(n: int) -> str:
    """"1 mês", "12 meses" - o rotulo de uma janela."""
    return contagem(n, "mês", "meses")
