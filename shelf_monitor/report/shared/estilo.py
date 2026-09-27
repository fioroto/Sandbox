"""Tokens visuais dos relatorios - uma paleta, nao tres.

Os hexes viviam copiados no CSS do dashboard, nas constantes do e-mail do
digest e no deck. Aqui fica a fonte da verdade para o que e Python (digest,
e-mail do ETF); o CSS do dashboard repete os mesmos valores nas variaveis
`--casa-*` e um teste garante que as duas copias nao divergem.

Linguagem visual (ver docs/linguagem-visual.md): o padrao executivo da XP -
fundo branco, preto e amarelo, Inter em peso 800 nos titulos, retranca em
caixa alta com o traco amarelo - com a anatomia de figura da The Economist e
da McKinsey (titulo que afirma, subtitulo com unidade e prazo, fonte no rodape).

Cor por casa, com a cor de marca de cada uma; a XP com o amarelo dela. Os
passos foram validados (dataviz, validate_palette.js, superficie branca, modo
claro): croma e separacao sob daltonismo passam em TODOS os pares, nao so nos
vizinhos. O amarelo e o par mais arriscado: ao lado dele o Inter fica no
laranja de marca (#ff7a00) e o Itau um passo mais escuro - com o laranja mais
claro de antes, amarelo e Inter ficavam a 14,6 de distancia, abaixo do piso
de 15. O amarelo fica fora da faixa de luminosidade e a 1,6:1 do branco, por
ser o amarelo da marca: toda marca da XP leva contorno preto (CONTORNO_XP) e
o nome da casa escrito ao lado, nunca so a cor.
"""
from __future__ import annotations

# Cor de cada casa, pelo id da plataforma. A cor segue a casa, nunca a posicao
# no ranking nem a ordem desta execucao.
CORES_DAS_CASAS: dict[str, str] = {
    "xp": "#f9c900",     # amarelo XP - a casa-base de toda leitura
    "btg": "#1d4e9e",    # azul BTG
    "itau": "#c24400",   # laranja escuro Itau
    "nu": "#b065e8",     # roxo Nu, um passo mais claro para separar do azul
    "inter": "#ff7a00",  # laranja Inter
}
# Casa fora do registro (um id novo, uma casa so do radar): cinza neutro, e
# nunca um matiz gerado - um sexto matiz nao passaria no validador.
COR_OUTRA_CASA = "#9a9993"
# O contorno de toda marca amarela: e o que a faz aparecer no branco.
CONTORNO_XP = "#0a0a0a"

# Fundo tintado da linha da casa-base (placar, tabelas): o amarelo bem claro.
DESTAQUE_SUAVE = "#fff6cc"

# Tinta e cromo - os do padrao executivo da XP.
PAGINA = "#ffffff"
SUPERFICIE = "#ffffff"
SUPERFICIE_2 = "#f6f6f4"
TINTA = "#0a0a0a"
TINTA_2 = "#55575c"
TINTA_FRACA = "#737373"
GRADE = "#e5e5e3"
LINHA_FINA = "#ececea"
EIXO = "#c9c9c6"
# O traco da retranca e da figura (amarelo, 24 x 3) e a linha do zero nos
# graficos de ganho e perda (preta). E cromo, nao dado: nunca pinta uma serie.
ETIQUETA = "#f9c900"
ZERO = "#0a0a0a"

# Status: fixos, sempre com rotulo e sinal ao lado (nunca so a cor).
BOM = "#2e7d4f"
BOM_TEXTO = "#2e7d4f"
ALERTA = "#c58a00"
CRITICO = "#c6423c"
CRITICO_TEXTO = "#c6423c"

# Rampa sequencial para mapa de calor e quantidade ordenada: cinza neutro, do
# claro ao grafite. Sem matiz de proposito - todo matiz ja e de alguma casa.
RAMPA: tuple[str, ...] = (
    "#efefef", "#d7d7d7", "#bcbcbc", "#9c9c9c", "#777777", "#4d4d4d", "#232323",
)
# A partir deste passo a tinta da celula vira branca.
RAMPA_TINTA_CLARA_DESDE = 4

# Componentes de uma mesma soma (compartilhado x so nesta vitrine, proprio x
# terceiros): grafite e cinza, porque a identidade ali e o componente, nao a
# casa.
COMPONENTE_1 = "#232427"
COMPONENTE_2 = "#bcbcbc"

FONTE = "Inter, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif"


def cor_da_casa(platform_id: str) -> str:
    """Cor de marca da casa; cinza neutro para casa fora do registro."""
    return CORES_DAS_CASAS.get(platform_id, COR_OUTRA_CASA)


def contorno_da_casa(platform_id: str) -> str | None:
    """Contorno da marca da casa: so a XP tem (o amarelo some no branco sem ele)."""
    return CONTORNO_XP if platform_id == "xp" else None


def passo_da_rampa(valor: float, maximo: float) -> tuple[str, str] | None:
    """(fundo, tinta) da celula de mapa de calor; None para zero ou vazio."""
    if not valor or maximo <= 0:
        return None
    passos = len(RAMPA)
    indice = min(passos - 1, int(valor / maximo * passos))
    tinta = "#ffffff" if indice >= RAMPA_TINTA_CLARA_DESDE else TINTA
    return RAMPA[indice], tinta
