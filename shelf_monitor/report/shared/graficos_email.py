"""Graficos para e-mail: tabela, celula e cor em linha - nada mais.

O e-mail do digest nao roda script, o Gmail tira `<svg>` e `<style>` e o
Outlook de mesa ignora largura e altura de `div` e o `max-width`. O que
sobrevive aos tres e a tabela: barra e uma celula com largura em % e
`bgcolor`, mapa de calor e celula colorida, grade de pontos e tabela de
celulas. Cada funcao daqui recebe numeros e rotulos e devolve uma string de
HTML feita so de `<table role="presentation">`, `<tr>` e `<td>`, com estilo em
linha - sem `<img>`, `<svg>`, `<script>` nem `<style>`.

Anatomia de toda figura (docs/linguagem-visual.md), no padrao executivo da XP:
fio preto de 3 px no topo, traco amarelo (24 x 3), titulo que afirma a leitura
em peso 800, subtitulo com unidade e prazo, corpo e rodape com a fonte. Figura
sem dado nao sai: os componentes devolvem "" quando nao ha o que desenhar, e
`figura` devolve "" com o corpo vazio - moldura vazia, nunca.

A marca amarela da XP leva sempre o fio preto de 1 px (`contorno`, de
`estilo.contorno_da_casa`): o amarelo fica a 1,6:1 do branco e some sem ele.

Texto nunca usa a cor da serie: o valor fica em tinta e a marca colorida ao
lado carrega a identidade, com o nome da casa escrito na mesma linha.

Larguras em % e nao em px, para a mesma tabela caber nos 600 px do e-mail
aberto no computador e nos ~320 px do telefone sem rolagem lateral. Linhas de
referencia (o zero, a base) sao borda da celula, e nao celula propria: a borda
ocupa a altura inteira da linha, e a linha fica continua mesmo quando um
rotulo quebra em duas.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

from shelf_monitor.report.shared.estilo import (
    BOM,
    COMPONENTE_1,
    CRITICO,
    DESTAQUE_SUAVE,
    ETIQUETA,
    FONTE,
    GRADE,
    SUPERFICIE,
    SUPERFICIE_2,
    TINTA,
    TINTA_2,
    TINTA_FRACA,
    ZERO,
    passo_da_rampa,
)

# Espessura da barra (a linguagem visual pede no maximo 14-16 px).
ALTURA_DA_BARRA = 12

# Colunas das barras deitadas, em % da largura: rotulo | barra | valor. O valor
# tem `nowrap`, entao "+2.397,9 MM" nunca quebra; no telefone o navegador tira
# a diferenca da barra, que e a coluna elastica.
_ROTULO_PCT = 27
_VALOR_PCT = 18

# Largura util do e-mail no telefone (380 px de tela menos as margens). Decide
# se o rotulo de um segmento cabe embaixo dele ou desce para a legenda.
_LARGURA_NO_TELEFONE = 320
# Largura media de um caractere do rotulo de segmento, a 11,5 px.
_PX_POR_CARACTERE = 6.4

# Sem `border`: o padrao do atributo ausente ja e sem borda.
_TABELA = 'role="presentation" cellpadding="0" cellspacing="0"'
_TEXTO = f"font-family:{FONTE};"
# Corpo e entrelinha vao uma vez, na tabela externa de cada componente, e a
# fonte uma vez, na tabela da figura - nunca em cada celula: as celulas herdam
# (documento em modo padrao, o do `<!doctype html>` da casca). O e-mail tem
# orcamento - o Gmail corta a mensagem acima de ~102 KB -, e por celula a pilha
# de fontes sozinha somava 28 KB. Pelo mesmo motivo nenhuma celula repete
# `valign="middle"`, que ja e o padrao.
_TABELA_DE_TEXTO = f'{_TABELA} width="100%" style="font-size:13px;line-height:18px;"'
# A etiqueta leva um &nbsp; (sem ele, cliente antigo recolhe a celula de 28 px
# de largura): sem fonte nem entrelinha, a altura fica a declarada tambem no
# Outlook, que senao impoe a altura da linha de texto. As outras celulas de cor
# sao vazias (ver `_cor`).
_SO_COR = "font-size:0;line-height:0;mso-line-height-rule:exactly;"
# Tinta, corpo e entrelinha herdados da tabela (`_TABELA_DE_TEXTO`); o valor
# que nao quebra usa o atributo `nowrap`, mais curto que o estilo.
_ROTULO = ""
_VALOR = "white-space:nowrap;"
_MIUDO = f"font-size:11.5px;line-height:15px;color:{TINTA_2};"
# O miudo apagado do rodape da figura e dos rotulos do eixo.
_RODAPE = f"font-size:11.5px;line-height:15px;color:{TINTA_FRACA};"


def _e(texto: object) -> str:
    return html.escape(str(texto))


@dataclass(frozen=True)
class Linha:
    """Uma barra: o rotulo escrito, o valor (a escala) e o valor ja formatado."""

    rotulo: str
    valor: float
    texto: str
    cor: str = COMPONENTE_1
    # A casa-base de toda leitura: rotulo e valor em negrito.
    destaque: bool = False
    # Contorno da marca (`estilo.contorno_da_casa`): o amarelo da XP some no
    # branco sem o fio preto de 1 px.
    contorno: str | None = None


@dataclass(frozen=True)
class Segmento:
    """Uma parte de uma soma, na barra segmentada."""

    rotulo: str
    valor: float
    texto: str
    cor: str = COMPONENTE_1


@dataclass(frozen=True)
class Serie:
    """Uma miniatura de colunas: um valor por periodo, na cor da casa."""

    rotulo: str
    valores: tuple[float | None, ...]
    cor: str
    destaque: bool = False
    # Rotulo seletivo sob a miniatura ("set/2026: -38,5 MM/dia").
    nota: str = ""
    contorno: str | None = None


@dataclass(frozen=True)
class Celula:
    """Celula do mapa de calor. `valor` None: so texto, sem cor (uma contagem)."""

    valor: float | None
    texto: str


@dataclass(frozen=True)
class LinhaDeCalor:
    rotulo: str
    celulas: tuple[Celula, ...]
    destaque: bool = False
    # Linha miuda sob o rotulo ("589 fundos"): um dado da linha que nao e
    # parcela, sem gastar uma coluna - no telefone a coluna a mais nao cabe.
    nota: str = ""


# --- pecas -------------------------------------------------------------------


def _cor(
    cor: str, altura: int, largura: str = "", estilo: str = "", colspan: int = 0,
    contorno: str | None = None,
) -> str:
    """Celula de cor com altura exata: o tijolo de toda marca.

    Vazia e so com atributos: sem texto dentro, o Outlook nao impoe a altura da
    linha de texto, e nenhum estilo se repete a toa. O e-mail tem orcamento -
    o Gmail corta a mensagem acima de ~102 KB, e a grade de pontos sozinha tem
    300 celulas.

    Com `contorno`, o fio de 1 px entra dentro da altura declarada: a barra da
    XP tem a mesma espessura das outras.
    """
    atributos = f' width="{largura}"' if largura else ""
    if colspan > 1:
        atributos += f' colspan="{colspan}"'
    if contorno:
        altura = max(1, altura - 2)
        estilo += f"border:1px solid {contorno};"
    estilo_attr = f' style="{estilo}"' if estilo else ""
    return f'<td{atributos} height="{altura}" bgcolor="{cor}"{estilo_attr}></td>'


def _pct(parte: float, todo: float) -> int:
    """Largura inteira em %; valor pequeno mas nao nulo ainda aparece (1%)."""
    if todo <= 0 or parte <= 0:
        return 0
    return max(1, min(100, round(parte / todo * 100)))


def _barra(
    largura: int, cor: str, para_a_esquerda: bool = False, contorno: str | None = None
) -> str:
    """Barra deitada, reta nas duas pontas como no padrao executivo da XP."""
    if largura <= 0:
        return ""
    alinhamento = ' align="right"' if para_a_esquerda else ""
    # Sem `role`: tabela de uma celula vazia, que leitor de tela ja trata como
    # leiaute - e sao dezenas por e-mail.
    return (
        f'<table cellpadding="0" cellspacing="0" width="{largura}%"{alinhamento}><tr>'
        f"{_cor(cor, ALTURA_DA_BARRA, contorno=contorno)}</tr></table>"
    )


def _rotulo(linha: Linha | Serie | LinhaDeCalor) -> str:
    texto = _e(linha.rotulo)
    return f"<strong>{texto}</strong>" if linha.destaque else texto


def _valor(linha: Linha) -> str:
    """O valor na ponta da barra, em tinta; o da casa-base em negrito."""
    texto = _e(linha.texto)
    return f"<strong>{texto}</strong>" if linha.destaque else texto


# O destaque de um numero-chave da XP (a classe `.mark` do deck executivo):
# fundo amarelo, texto preto.
MARCA_XP = f"background:{ETIQUETA};color:{TINTA};padding:0 5px;"


def etiqueta() -> str:
    """O traco amarelo de 24 x 3 px no canto da figura. Cromo, nunca dado."""
    return (
        f'<table {_TABELA}><tr><td width="24" height="3" bgcolor="{ETIQUETA}" '
        f'style="{_SO_COR}">&nbsp;</td></tr></table>'
    )


# Marca de toda figura no HTML - os testes contam figuras por ela.
MARCA_DA_FIGURA = f'<td width="24" height="3" bgcolor="{ETIQUETA}"'


def figura(titulo: str, subtitulo: str, corpo: str, fonte: str = "") -> str:
    """Etiqueta, titulo que afirma, subtitulo com unidade e prazo, corpo, rodape.

    `titulo` e `subtitulo` sao texto (escapados aqui). `corpo` e `fonte` ja
    chegam em HTML: o corpo vem dos componentes deste modulo, e a fonte pode
    trazer links. Corpo vazio devolve "" - a figura nao sai sem dado.
    """
    if not corpo:
        return ""
    # O respiro em volta da figura vai no padding da primeira e da ultima linha
    # (o Outlook ignora margin em tabela), sem uma tabela so para isso.
    rodape = (
        f'<tr><td style="{_RODAPE}padding:8px 0 22px;">{fonte}</td></tr>'
        if fonte else '<tr><td height="22"></td></tr>'
    )
    return (
        f'<table {_TABELA} width="100%" style="{_TEXTO}font-size:13px;line-height:18px;">'
        f'<tr><td style="border-top:3px solid {TINTA};padding:12px 0 8px;">{etiqueta()}</td></tr>'
        f'<tr><td style="font-size:16px;line-height:22px;font-weight:800;'
        f'letter-spacing:-.01em;padding:0 0 2px;">{_e(titulo)}</td></tr>'
        f'<tr><td style="line-height:19px;color:{TINTA_2};'
        f'padding:0 0 12px;">{_e(subtitulo)}</td></tr>'
        f"<tr><td>{corpo}</td></tr>"
        f"{rodape}"
        "</table>"
    )


# --- barras ------------------------------------------------------------------


def barras(linhas: list[Linha], *, rotulo_em_cima: bool = False) -> str:
    """Barras deitadas a partir do zero: rotulo | barra | valor.

    Para valores sem sinal (contagem, tamanho, indice). A linha do zero e a
    borda esquerda da coluna da barra, em tinta. `rotulo_em_cima` poe o rotulo
    numa linha propria, acima da barra - para nome longo (categoria ANBIMA),
    que na coluna de 27% quebraria em tres linhas no telefone; ali a barra
    comeca na margem, e a linha do zero sairia picotada entre os rotulos.
    """
    linhas = [linha for linha in linhas if linha.valor is not None]
    topo = max((linha.valor for linha in linhas), default=0)
    if topo <= 0:
        return ""
    zero = f"border-left:1px solid {TINTA};"
    corpo = []
    for linha in linhas:
        barra = _barra(_pct(linha.valor, topo), linha.cor, contorno=linha.contorno)
        valor = (
            f'<td width="{_VALOR_PCT}%" align="right" '
            f'nowrap style="padding:3px 0 3px 8px;">{_valor(linha)}</td>'
        )
        if rotulo_em_cima:
            corpo.append(
                f'<tr><td colspan="2" style="{_ROTULO}padding:5px 0 1px;">'
                f"{_rotulo(linha)}</td></tr>"
                f'<tr><td style="padding:2px 0;">{barra}</td>'
                f"{valor}</tr>"
            )
        else:
            corpo.append(
                f'<tr><td width="{_ROTULO_PCT}%" '
                f'style="{_ROTULO}padding:3px 8px 3px 0;">{_rotulo(linha)}</td>'
                f'<td style="{zero}padding:3px 0;">{barra}</td>'
                f"{valor}</tr>"
            )
    return f'<table {_TABELA_DE_TEXTO}>{"".join(corpo)}</table>'


def barras_divergentes(linhas: list[Linha]) -> str:
    """Valores com sinal: negativo a esquerda, positivo a direita do zero.

    As duas metades tem a largura dos extremos de cada lado - uma escala so, e
    nenhuma metade vazia quando todos os valores tem o mesmo sinal. O zero e a
    borda esquerda da metade positiva, na cor `ZERO` (ganho e perda).
    """
    linhas = [linha for linha in linhas if linha.valor is not None]
    negativo = max((-linha.valor for linha in linhas if linha.valor < 0), default=0)
    positivo = max((linha.valor for linha in linhas if linha.valor > 0), default=0)
    if not (negativo or positivo):
        return ""
    area = 100 - _ROTULO_PCT - _VALOR_PCT
    lado_negativo = round(area * negativo / (negativo + positivo))
    lado_positivo = area - lado_negativo
    zero = f"border-left:2px solid {ZERO};"
    corpo = []
    for linha in linhas:
        esquerda = (
            _barra(_pct(-linha.valor, negativo), linha.cor, True, linha.contorno)
            if linha.valor < 0 else ""
        )
        direita = (
            _barra(_pct(linha.valor, positivo), linha.cor, contorno=linha.contorno)
            if linha.valor > 0 else ""
        )
        metade_negativa = (
            f'<td width="{lado_negativo}%" align="right" '
            f'style="padding:3px 0;">{esquerda}</td>'
            if lado_negativo else ""
        )
        corpo.append(
            f'<tr><td width="{_ROTULO_PCT}%" '
            f'style="{_ROTULO}padding:3px 8px 3px 0;">{_rotulo(linha)}</td>'
            f"{metade_negativa}"
            f'<td width="{lado_positivo}%" style="{zero}padding:3px 0;">'
            f"{direita}</td>"
            f'<td width="{_VALOR_PCT}%" align="right" '
            f'nowrap style="padding:3px 0 3px 8px;">{_valor(linha)}</td></tr>'
        )
    return f'<table {_TABELA_DE_TEXTO}>{"".join(corpo)}</table>'


@dataclass(frozen=True)
class Oposta:
    """Uma linha com as duas partes: o que saiu (esquerda) e o que entrou (direita)."""

    rotulo: str
    esquerda: float
    direita: float
    texto_esquerda: str
    texto_direita: str
    cor: str = COMPONENTE_1
    destaque: bool = False
    contorno: str | None = None


def barras_opostas(linhas: list[Oposta]) -> str:
    """Parte negativa e parte positiva na mesma linha, a partir do zero.

    Entradas e saidas de uma vitrine, por exemplo: cada casa tem as duas, e o
    saldo nao diz quanto mexeu. O valor de cada lado fica do lado dele, em
    tinta: rotulo | saidas | barra <- zero -> barra | entradas. Um lado todo
    zerado sai inteiro (metade e coluna de valor): uma fileira de "0" contra o
    zero nao diz nada que o titulo nao diga.
    """
    esquerda = max((linha.esquerda for linha in linhas), default=0)
    direita = max((linha.direita for linha in linhas), default=0)
    if not (esquerda or direita):
        return ""
    valor_pct = 10
    area = 100 - _ROTULO_PCT - valor_pct * ((esquerda > 0) + (direita > 0))
    lado_esquerdo = round(area * esquerda / (esquerda + direita))
    lado_direito = area - lado_esquerdo
    zero = f"2px solid {ZERO};"
    corpo = []
    for linha in linhas:
        celulas = [
            f'<td width="{_ROTULO_PCT}%" '
            f'style="{_ROTULO}padding:3px 8px 3px 0;">{_rotulo(linha)}</td>'
        ]
        if esquerda:
            barra = _barra(_pct(linha.esquerda, esquerda), linha.cor, True, linha.contorno)
            # Sem lado direito, o zero e a borda direita desta metade.
            borda = "" if direita else f"border-right:{zero}"
            celulas.append(
                f'<td width="{valor_pct}%" align="right" '
                f'style="{_VALOR}padding:3px 6px 3px 0;">{_e(linha.texto_esquerda)}</td>'
                f'<td width="{lado_esquerdo}%" align="right" '
                f'style="{borda}padding:3px 0;">{barra}</td>'
            )
        if direita:
            barra = _barra(_pct(linha.direita, direita), linha.cor, contorno=linha.contorno)
            celulas.append(
                f'<td width="{lado_direito}%" style="border-left:{zero}'
                f'padding:3px 0;">{barra}</td>'
                f'<td width="{valor_pct}%" '
                f'style="{_VALOR}padding:3px 0 3px 6px;">{_e(linha.texto_direita)}</td>'
            )
        corpo.append(f"<tr>{''.join(celulas)}</tr>")
    return f'<table {_TABELA_DE_TEXTO}>{"".join(corpo)}</table>'


@dataclass(frozen=True)
class Par:
    """Uma linha de barras pareadas: duas medidas do mesmo item (a lider do
    segmento e a casa-base, por exemplo), cada uma com a sua marca."""

    rotulo: str
    a: Linha
    b: Linha


def barras_pareadas(pares: list[Par]) -> str:
    """Duas barras finas por linha, uma sobre a outra, na mesma escala.

    rotulo | barra A sobre barra B | "A texto" sobre "B texto". Cada barra leva
    a cor (e o contorno) da propria marca, e o valor sai com o nome curto de
    quem ele e - a cor nunca e a unica pista. Valor negativo nao desenha
    barra: o sinal escrito diz a direcao.
    """
    topo = max(
        (max(par.a.valor or 0, par.b.valor or 0) for par in pares), default=0
    )
    if topo <= 0:
        return ""
    zero = f"border-left:1px solid {TINTA};"
    corpo = []
    for par in pares:
        barras_ = "".join(
            f'<tr><td style="padding:1px 0;">'
            f'{_barra(_pct(linha.valor, topo), linha.cor, contorno=linha.contorno) or ""}'
            "</td></tr>"
            for linha in (par.a, par.b)
        )
        valores = "<br>".join(
            f"{_e(linha.rotulo)} {_valor(linha)}" for linha in (par.a, par.b)
        )
        corpo.append(
            f'<tr><td width="{_ROTULO_PCT}%" style="padding:4px 8px 4px 0;">{_e(par.rotulo)}</td>'
            f'<td style="{zero}padding:4px 0;"><table {_TABELA} width="100%">{barras_}</table></td>'
            f'<td width="24%" align="right" style="{_MIUDO}padding:4px 0 4px 8px;">{valores}</td></tr>'
        )
    return f'<table {_TABELA_DE_TEXTO}>{"".join(corpo)}</table>'


def barra_segmentada(segmentos: list[Segmento]) -> str:
    """Uma barra inteira dividida nas partes de uma soma, com rotulo embaixo.

    O rotulo (nome e valor) vai embaixo do proprio segmento quando cabe na
    largura que o segmento tem no telefone; quando nao cabe, desce para uma
    legenda, com a marca da cor - em vez de vazar por cima do vizinho.
    """
    segmentos = [s for s in segmentos if s.valor and s.valor > 0]
    total = sum(s.valor for s in segmentos)
    if total <= 0:
        return ""
    larguras = _larguras_que_somam_100([s.valor / total * 100 for s in segmentos])
    faixa, rotulos, legenda = [], [], []
    for i, (segmento, largura) in enumerate(zip(segmentos, larguras)):
        # 2 px de superficie entre segmentos vizinhos, como borda do segmento.
        vao = f"border-left:2px solid {SUPERFICIE};" if i else ""
        faixa.append(_cor(segmento.cor, 16, f"{largura}%", vao))
        if _rotulo_cabe(segmento, largura):
            rotulos.append(
                f'<td width="{largura}%" valign="top" style="{_MIUDO}padding:5px 4px 0 '
                f'{"6px" if i else "0"};overflow:hidden;">{_e(segmento.rotulo)}<br>'
                f'<strong style="color:{TINTA};">{_e(segmento.texto)}</strong></td>'
            )
        else:
            rotulos.append(f'<td width="{largura}%"></td>')
            legenda.append(segmento)
    partes = [
        f'<table {_TABELA} width="100%" style="table-layout:fixed;"><tr>{"".join(faixa)}'
        "</tr></table>",
        f'<table {_TABELA} width="100%" style="table-layout:fixed;"><tr>'
        f'{"".join(rotulos)}'
        "</tr></table>",
    ]
    if legenda:
        itens = "".join(
            f'<span style="display:inline-block;white-space:nowrap;margin:0 14px 0 0;">'
            f'<span style="display:inline-block;width:9px;height:9px;background:{s.cor};'
            f'margin-right:5px;"></span>{_e(s.rotulo)} '
            f'<strong style="color:{TINTA};">{_e(s.texto)}</strong></span>'
            for s in legenda
        )
        partes.append(
            f'<table {_TABELA_DE_TEXTO}><tr><td style="{_MIUDO}padding:8px 0 0;">'
            f"{itens}</td></tr></table>"
        )
    return "".join(partes)


def _rotulo_cabe(segmento: Segmento, largura: int) -> bool:
    """O rotulo cabe embaixo do segmento, no telefone, em ate duas linhas?

    Nenhuma palavra pode passar da largura (senao ela vaza para o vizinho) e o
    nome inteiro tem de caber em duas linhas; o valor vai numa terceira.
    """
    disponivel = largura / 100 * _LARGURA_NO_TELEFONE - 6
    palavras = segmento.rotulo.split() + [segmento.texto]
    maior = max(len(p) for p in palavras) * _PX_POR_CARACTERE
    return maior <= disponivel and len(segmento.rotulo) * _PX_POR_CARACTERE <= 2 * disponivel


def _larguras_que_somam_100(partes: list[float]) -> list[int]:
    """Arredonda as partes para inteiros que somam 100 (maior resto primeiro).

    Parte nao nula fica com ao menos 1%, para nao sumir; o que isso empresta
    sai da maior parte.
    """
    inteiras = [max(1, int(p)) for p in partes]
    faltam = 100 - sum(inteiras)
    if faltam >= 0:
        por_resto = sorted(range(len(partes)), key=lambda i: -(partes[i] - int(partes[i])))
        for i in por_resto[:faltam]:
            inteiras[i] += 1
    else:
        inteiras[inteiras.index(max(inteiras))] += faltam
    return inteiras


# --- mapa de calor -----------------------------------------------------------


def mapa_de_calor(colunas: list[str], linhas: list[LinhaDeCalor]) -> str:
    """Tabela com as celulas pintadas na rampa ardosia, o numero dentro.

    `colunas` sao os cabecalhos das celulas (sem o da coluna de rotulo). A
    escala e uma so, o maior valor da tabela; celula com `valor` None e texto
    puro, sem cor - uma contagem ao lado de parcelas nao entra na rampa.
    """
    valores = [c.valor for linha in linhas for c in linha.celulas if c.valor is not None]
    maximo = max(valores, default=0)
    if not linhas or maximo <= 0:
        return ""
    largura = round((100 - _ROTULO_PCT) / max(1, len(colunas)))
    cabecalho = "".join(
        f'<td width="{largura}%" align="center" valign="bottom" '
        f'style="{_MIUDO}font-size:11px;padding:0 1px 4px;">{_e(c)}</td>'
        for c in colunas
    )
    corpo = []
    for linha in linhas:
        celulas = []
        for celula in linha.celulas:
            passo = passo_da_rampa(celula.valor or 0, maximo) if celula.valor is not None else None
            if passo:
                fundo, tinta = passo
                celulas.append(
                    f'<td align="center" bgcolor="{fundo}" style="{_VALOR}'
                    f'color:{tinta};padding:5px 2px;">{_e(celula.texto)}</td>'
                )
            else:
                cor = SUPERFICIE_2 if celula.valor is not None else SUPERFICIE
                celulas.append(
                    f'<td align="center" bgcolor="{cor}" style="{_VALOR}'
                    f'padding:5px 2px;">{_e(celula.texto)}</td>'
                )
        nota = (
            f'<br><span style="{_MIUDO}color:{TINTA_FRACA};">{_e(linha.nota)}</span>'
            if linha.nota else ""
        )
        fundo_da_base = (
            f'bgcolor="{DESTAQUE_SUAVE}" style="padding:4px 6px;"' if linha.destaque
            else 'style="padding:4px 6px 4px 0;"'
        )
        corpo.append(
            f'<tr><td width="{_ROTULO_PCT}%" {fundo_da_base}>'
            f'{_rotulo(linha)}{nota}</td>{"".join(celulas)}</tr>'
        )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="2" border="0" '
        f'width="100%" style="font-size:13px;line-height:18px;"><tr><td></td>'
        f"{cabecalho}</tr>"
        f'{"".join(corpo)}</table>'
    )


# --- pequenos multiplos ------------------------------------------------------


def pequenos_multiplos(pecas: list[str], por_linha: int = 3) -> str:
    """Poe as miniaturas lado a lado, `por_linha` em cada linha da tabela."""
    pecas = [p for p in pecas if p]
    if not pecas:
        return ""
    largura = round(100 / por_linha)
    linhas = []
    for inicio in range(0, len(pecas), por_linha):
        grupo = pecas[inicio:inicio + por_linha]
        celulas = "".join(
            f'<td width="{largura}%" valign="top" '
            f'style="padding:0 {10 if i < por_linha - 1 else 0}px 14px 0;">{peca}</td>'
            for i, peca in enumerate(grupo)
        )
        # Linha incompleta: celulas vazias seguram a largura das outras.
        celulas += "".join(
            f'<td width="{largura}%"></td>' for _ in range(por_linha - len(grupo))
        )
        linhas.append(f"<tr>{celulas}</tr>")
    return f'<table {_TABELA_DE_TEXTO}>{"".join(linhas)}</table>'


# Rotulo de miniatura mais longo que isto quebra em duas linhas no telefone (um
# terco de ~320 px): as miniaturas de uma fileira reservam as duas linhas para
# todas, senao a de nome longo desce e o zero delas deixa de alinhar.
_ROTULO_CURTO = 13


def _rotulo_de_miniatura(linha: Serie | Linha, duas_linhas: bool, colspan: int = 1) -> str:
    altura = ' height="38" valign="bottom"' if duas_linhas else ""
    span = f' colspan="{colspan}"' if colspan > 1 else ""
    return (
        f'<tr><td{span}{altura} style="{_ROTULO}padding:0 0 6px;">{_rotulo(linha)}</td></tr>'
    )


def colunas_por_casa(
    series: list[Serie], rotulos_x: list[str], *, altura: int = 72, por_linha: int = 3
) -> str:
    """Colunas de ganho e perda, uma miniatura por casa, todas na mesma escala.

    Acima do zero vermelho o que entrou, abaixo o que saiu. A altura total se
    divide entre as duas metades na proporcao dos extremos de todas as casas -
    uma escala so, para a comparacao entre miniaturas ser honesta. A celula
    usa o atributo `height`, que o Outlook respeita.
    """
    valores = [v for s in series for v in s.valores if v is not None]
    positivo = max((v for v in valores if v > 0), default=0)
    negativo = max((-v for v in valores if v < 0), default=0)
    if not series or not (positivo or negativo):
        return ""
    alto = round(altura * positivo / (positivo + negativo))
    baixo = altura - alto
    largura = round(100 / max(1, len(rotulos_x)))

    def coluna(
        valor: float | None, lado: str, cor: str, primeira_fileira: bool,
        contorno: str | None = None,
    ) -> str:
        if lado == "alto":
            h = round(alto * valor / positivo) if valor and valor > 0 else 0
            vertical, espaco = "bottom", alto
        else:
            h = round(baixo * -valor / negativo) if valor and valor < 0 else 0
            vertical, espaco = "top", baixo
        barra = (
            f'<table cellpadding="0" cellspacing="0" width="64%" align="center"><tr>'
            f"{_cor(cor, max(3 if contorno else 1, h), contorno=contorno)}</tr></table>"
            if h else ""
        )
        # A largura da coluna vai so na primeira fileira: as outras herdam.
        largura_attr = f' width="{largura}%"' if primeira_fileira else ""
        return f'<td{largura_attr} height="{espaco}" valign="{vertical}">{barra}</td>'

    rotulo_x = f"{_RODAPE}padding:4px 0 0;"

    duas_linhas = any(len(s.rotulo) > _ROTULO_CURTO for s in series)
    pecas = []
    for serie in series:
        linhas = [_rotulo_de_miniatura(serie, duas_linhas, len(rotulos_x))]
        if alto:
            linhas.append(
                "<tr>" + "".join(
                    coluna(v, "alto", serie.cor, True, serie.contorno) for v in serie.valores
                ) + "</tr>"
            )
        linhas.append(f"<tr>{_cor(ZERO, 2, colspan=len(rotulos_x))}</tr>")
        if baixo:
            linhas.append(
                "<tr>" + "".join(
                    coluna(v, "baixo", serie.cor, not alto, serie.contorno)
                    for v in serie.valores
                ) + "</tr>"
            )
        linhas.append(
            "<tr>" + "".join(
                f'<td align="center" style="{rotulo_x}">{_e(r)}</td>' for r in rotulos_x
            ) + "</tr>"
        )
        if serie.nota:
            linhas.append(
                f'<tr><td colspan="{len(rotulos_x)}" style="{_MIUDO}padding:3px 0 0;">'
                f"{_e(serie.nota)}</td></tr>"
            )
        pecas.append(f'<table {_TABELA} width="100%">{"".join(linhas)}</table>')
    return pequenos_multiplos(pecas, por_linha)


# --- grade de pontos ---------------------------------------------------------


def grade_de_pontos(
    parcela: float | None, cor: str, *, lado: int = 10, tamanho: int = 7,
    contorno: str | None = None,
) -> str:
    """Grade de 100 quadrados (McKinsey): os pintados sao a parcela.

    Enche de baixo para cima, da esquerda para a direita, como um nivel. Vazio
    em cinza de grade, nunca em outra cor - a outra parte nao e uma serie.

    Sao 100 celulas por casa, entao cada byte conta (o Gmail corta o e-mail
    acima de ~102 KB): a cor vai na linha (`<tr bgcolor>`, que as celulas
    herdam) e so a linha parcial pinta celula a celula; a largura vai so na
    primeira linha e a altura so na primeira coluna - as outras celulas herdam
    da coluna e da linha. O vao de 2 px e o `cellspacing`, no branco da tabela.

    Com `contorno` (a XP), cada quadrado pintado leva o fio de 1 px por dentro
    do tamanho: largura e altura declaradas perdem 2 px onde o fio entra, e a
    grade continua regular.
    """
    if parcela is None:
        return ""
    total = lado * lado
    cheios = max(0, min(total, round(parcela * total)))
    linhas = []
    for linha in range(lado):
        primeira = (lado - 1 - linha) * lado  # ordem da primeira celula da linha
        if primeira + lado <= cheios:
            fundo, pintadas = cor, 0
        else:
            fundo, pintadas = GRADE, max(0, cheios - primeira)
        celulas = []
        for coluna in range(lado):
            pintada = primeira + coluna < cheios
            miolo = tamanho - 2 if pintada and contorno else tamanho
            atributos = ""
            if linha == 0:
                atributos += f' width="{miolo}"'
            if coluna == 0:
                atributos += f' height="{miolo}"'
            if coluna < pintadas:
                atributos += f' bgcolor="{cor}"'
            if pintada and contorno:
                atributos += f' style="border:1px solid {contorno}"'
            celulas.append(f"<td{atributos}></td>")
        linhas.append(f'<tr bgcolor="{fundo}">{"".join(celulas)}</tr>')
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="2" border="0" '
        f'bgcolor="{SUPERFICIE}">{"".join(linhas)}</table>'
    )


def grades_por_casa(linhas: list[Linha], por_linha: int = 3) -> str:
    """Uma grade de 100 quadrados por casa, lado a lado, com nome e valor.

    `valor` de cada linha e a parcela (0 a 1); `texto` e o rotulo sob a grade.
    """
    duas_linhas = any(len(linha.rotulo) > _ROTULO_CURTO for linha in linhas)
    pecas = []
    for linha in linhas:
        grade = grade_de_pontos(linha.valor, linha.cor, contorno=linha.contorno)
        if not grade:
            continue
        pecas.append(
            f'<table {_TABELA} width="100%">'
            f"{_rotulo_de_miniatura(linha, duas_linhas)}"
            f"<tr><td>{grade}</td></tr>"
            f'<tr><td style="{_VALOR}padding:4px 0 0;">{_valor(linha)}</td></tr>'
            "</table>"
        )
    return pequenos_multiplos(pecas, por_linha)


def numeros_em_destaque(itens: list[tuple], por_linha: int = 3) -> str:
    """Numeros grandes com rotulo curto embaixo, `por_linha` por fileira.

    O lugar da frase que so enumerava contagens ("84 fundos captam fora da XP,
    mas 32 sao ... Restam 52 fundos, +2.098,4 MM ... 39 de gestora ..."): cada
    contagem vira um numero com o que ele conta. Valor e rotulo sao texto
    (escapados aqui). Um terceiro elemento verdadeiro marca o numero-chave da
    XP com o fundo amarelo (`MARCA_XP`).
    """
    pecas = []
    for item in itens:
        valor, rotulo, marcar = (*item, False)[:3]
        if not valor:
            continue
        numero = f'<span style="{MARCA_XP}">{_e(valor)}</span>' if marcar else _e(valor)
        pecas.append(
            f'<table {_TABELA} width="100%">'
            f'<tr><td style="font-size:18px;line-height:24px;font-weight:800;'
            f'letter-spacing:-.02em;white-space:nowrap;">{numero}</td></tr>'
            f'<tr><td style="{_MIUDO}padding:2px 0 0;">{_e(rotulo)}</td></tr></table>'
        )
    return pequenos_multiplos(pecas, por_linha)


# --- escala divergente -----------------------------------------------------------

# Quanto da cor de status entra em cada passo, do mais claro ao cheio. A escala
# e da raiz da magnitude: fluxo de ETF e concentrado (um mes do BTG vale dez de
# uma gestora media), e na escala linear quase toda celula saia no primeiro
# passo, sem leitura.
_PASSOS_DIVERGENTES = (0.14, 0.28, 0.46, 0.7, 1.0)


def _misturar(cor: str, parte: float) -> str:
    """`parte` da cor sobre o branco: 0 e branco, 1 e a cor."""
    canais = [int(cor[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(255 - (255 - c) * parte):02x}" for c in canais)


def _luminancia(cor: str) -> float:
    def canal(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (canal(int(cor[i:i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def passo_divergente(valor: float | None, maximo: float) -> tuple[str, str] | None:
    """(fundo, tinta) da celula de ganho e perda; None para zero ou vazio.

    Entrada no verde de status, saida no vermelho, ambos tintos do branco e
    mais cheios com a magnitude (em cinco passos, pela raiz de |valor| sobre o
    maximo da tabela). A tinta sai pelo contraste: preta nos passos claros,
    branca nos cheios. O sinal vai escrito na celula - a cor nunca e a unica
    pista (docs/linguagem-visual.md, secao 2).
    """
    if not valor or maximo <= 0:
        return None
    fracao = min(1.0, (abs(valor) / maximo) ** 0.5)
    indice = min(len(_PASSOS_DIVERGENTES) - 1, int(fracao * len(_PASSOS_DIVERGENTES)))
    fundo = _misturar(BOM if valor > 0 else CRITICO, _PASSOS_DIVERGENTES[indice])
    # Contraste de 4,5:1 contra o fundo: a tinta preta passa enquanto o fundo
    # tem luminancia acima de ~0,18.
    tinta = TINTA if _luminancia(fundo) > 0.18 else SUPERFICIE
    return fundo, tinta
