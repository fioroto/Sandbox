"""O nome do fundo para ler: o mesmo padrao em todas as superficies.

As fontes escrevem o mesmo fundo de jeitos diferentes. A XP e o Inter usam caixa
de titulo, com as siglas ("AZ Quest Termo FIF RF LP"); o BTG, o Itau e o Nu,
caixa alta; e o cadastro da CVM ainda traz a forma juridica por extenso ("JGP
SELECT FUNDO DE INVESTIMENTO FINANCEIRO DA CLASSE DE INVESTIMENTO EM COTAS ...
RESPONSABILIDADE LIMITADA"). Na exibicao todos saem no padrao da XP (pedido de
19/10/2026): a forma juridica em sigla e, no nome em caixa alta, a caixa de
titulo - siglas em maiuscula, conectores em minuscula ("Trend Cash Renda Fixa
...").

So a exibicao muda: o nome guardado, que os cruzamentos por nome usam, e o
CNPJ continuam os da fonte.
"""
from __future__ import annotations

import re

# A forma juridica por extenso, como o cadastro da CVM e o Open Finance a
# escrevem, e a sigla dela. O nome de registro do Itau ocupava sete linhas na
# tabela do e-mail.
_FORMA_JURIDICA = tuple(
    (re.compile(rf"\b{expressao}\b", re.IGNORECASE), sigla)
    for expressao, sigla in (
        (r"FUNDO DE INVESTIMENTO EM COTAS DE FUNDOS DE INVESTIMENTO", "FIC FI"),
        (r"FUNDO INCENTIVADO DE INVESTIMENTO(?: FINANCEIRO)? EM INFRAESTRUTURA", "FI INFRA"),
        (r"FUNDO DE INVESTIMENTO FINANCEIRO", "FIF"),
        (r"FUNDO DE INVESTIMENTO", "FI"),
        (r"(?:DA )?CLASSE DE INVESTIMENTO EM COTAS(?: DE FUNDOS DE INVESTIMENTO)?", "CIC"),
        (r"(?:DA )?CLASSE DE INVESTIMENTO", ""),
        (r"RESPONSABILIDADE LIMITADA", "RL"),
        (r"RESP\.? LIMITADA", "RL"),
    )
)

# As siglas que ficam em maiuscula na caixa de titulo: as da forma juridica e
# do mercado, e as marcas que a XP e o Inter escrevem em sigla - levantadas nos
# nomes em caixa de titulo das vitrines de 14/10/2026.
SIGLAS = frozenset("""
    FI FIC FIF FIM FIA FIRF FIDC FII FIP FIQ FIE FIMM FIFM FICP FIAGRO FICFI
    FICFIA FICFIM FICFIRF FICFIDC CIC CI CIA CIM CIRF RL MM RF RV CP LP IE DI
    REF INFRA ESG BDR ETF IS LS LB
    CDI IPCA IMA IMAB IRFM IDKA IFIX IBX IBOV SMLL CRI CRA LCI LCA CDB LF NTN
    NTNB USD BRL EUR EUA US USA UK FX FGTS MSCI CVM ANBIMA DTVM PGBL VGBL
    XP XPA BTG BTGP SPX AZ BB AM JGP ARX BNP MAG MS WHG RPS ASA HIX AF BBM BOCOM
    AHL GTI ACE WF GT HG GBV PIMCO JP JPM IPG NW AXA BW TB GLG AQR RMB MZK CTM
    VEX STK VRB EXA BV UBS MAPFRE
""".split())
_ROMANOS = frozenset("II III IV VI VII VIII IX XI XII XIII XIV XV XVI XVII XVIII XIX XX".split())
# Em minuscula, salvo na abertura do nome. "A" e "O" ficam de fora: no nome de
# fundo sao, quase sempre, a letra da classe ("Classe A").
_CONECTORES = frozenset("DE DA DO DAS DOS E EM NO NA NOS NAS PARA COM POR AO AOS À ÀS OU".split())
_LETRAS = re.compile(r"[^\W\d_]+")


def nome_de_fundo(nome: str | None) -> str:
    """O nome para ler: a forma juridica em sigla e, em caixa alta, caixa de titulo."""
    texto = nome or ""
    for padrao, sigla in _FORMA_JURIDICA:
        texto = padrao.sub(sigla, texto)
    # O traco que separava o sufixo por extenso fica solto: "V8 Cash FIF – RL".
    texto = re.sub(r"\s*[-–]\s*(?=RL\b)", " ", texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip(" -")
    if _em_caixa_alta(texto):
        texto = _caixa_de_titulo(texto)
    return texto or (nome or "")


def _em_caixa_alta(texto: str) -> bool:
    """Caixa alta = 80% ou mais das letras em maiuscula. O nome em caixa de
    titulo da XP, mesmo cheio de siglas, fica abaixo ("SPX Nimitz FIC FIM": 67%)."""
    letras = [c for c in texto if c.isalpha()]
    return bool(letras) and sum(c.isupper() for c in letras) >= 0.8 * len(letras)


def _caixa_de_titulo(texto: str) -> str:
    palavras = texto.split(" ")
    return " ".join(
        # Palavra com digito fica como veio: "D30", "B5", "V8", "D+2".
        palavra if any(c.isdigit() for c in palavra)
        else _LETRAS.sub(lambda m, i=i: _caixa_da_palavra(m.group(), abertura=i == 0), palavra)
        for i, palavra in enumerate(palavras)
    )


def _caixa_da_palavra(palavra: str, abertura: bool) -> str:
    maiuscula = palavra.upper()
    if maiuscula in SIGLAS or maiuscula in _ROMANOS:
        return maiuscula
    if maiuscula in _CONECTORES and not abertura:
        return palavra.lower()
    if len(palavra) == 1:
        return maiuscula
    return maiuscula[0] + palavra[1:].lower()
