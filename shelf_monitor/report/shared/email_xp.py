"""A pagina de e-mail no padrao executivo da XP: casca, retranca, tabela, chamada.

Nasceu dentro do digest de prateleiras (`report/digest.py`) e saiu para ca
quando os relatorios de ETF passaram a usar a mesma cara: importar nomes
privados do digest ja era como os modulos de ETF se vestiam, e cada ajuste
de estilo no digest mudava os outros sem aviso. Os graficos ficam em
`graficos_email.py`; aqui fica o que envolve eles - a casca de 640 px, a faixa
preta do topo, o cabecalho com retranca, os titulos de secao, a tabela de
dados, a chamada, a nota e as pecas de texto.

Tudo e HTML de e-mail: tabela, estilo em linha, sem `<style>`, `<script>`,
`<svg>` nem `<img>` (ver docs/linguagem-visual.md, secao 6). Os tokens vem de
`estilo.py`.

O e-mail tem orcamento - o Gmail corta a mensagem acima de ~102 KB e esconde o
resto atras de um link -, entao o estilo mora na tabela e na linha, nunca na
celula: a fonte vai na casca e em cada <table>; paragrafo, lista e celula
herdam dela. O preco: o Outlook de mesa, que nao herda estilo para dentro de
tabela aninhada, cai na fonte padrao dele nas tabelas, e nao desenha a borda
de linha (as tabelas saem sem os fios ali).
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable

from shelf_monitor.report.shared.estilo import (
    BOM_TEXTO,
    CRITICO_TEXTO,
    DESTAQUE_SUAVE,
    EIXO,
    ETIQUETA,
    FONTE,
    GRADE,
    LINHA_FINA,
    PAGINA,
    SUPERFICIE,
    SUPERFICIE_2,
    TINTA,
    TINTA_2,
    TINTA_FRACA,
)
from shelf_monitor.texto import milhar

LARGURA_EMAIL = 640
FONTE_CSS = f"font-family:{FONTE};"

# --- estilos -------------------------------------------------------------------

EMAIL_BODY = f"margin:0;padding:24px 12px;background:{PAGINA};{FONTE_CSS}color:{TINTA};"
EMAIL_CARD = (
    f"max-width:{LARGURA_EMAIL}px;margin:0 auto;background:{SUPERFICIE};"
    f"border-top:3px solid {TINTA};padding:24px;"
)
# Titulo de secao antigo (caixa alta sob o fio de tinta), para quem ainda monta
# o proprio cartao em <div>.
H2 = (
    "font-size:12.5px;letter-spacing:0.07em;text-transform:uppercase;"
    f"margin:34px 0 14px;padding:8px 0 0;border-top:2px solid {TINTA};"
)
# Titulo de secao e a retranca: caixa alta miuda e espacada, em cinza, depois
# do traco amarelo de 24 x 3 (um <span> em bloco - o Outlook de mesa nao
# desenha o traco e fica com o texto). Entre secoes, o fio fino; o fio preto
# de 3 px e de cada figura.
TRACO = (
    f'<span style="display:inline-block;width:24px;height:3px;background:{ETIQUETA};'
    'vertical-align:middle;margin:0 10px 3px 0;"></span>'
)
RETRANCA = (
    f"font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:{TINTA_2};"
    f"margin:36px 0 18px;padding-top:28px;border-top:1px solid {GRADE};"
)
H3 = (
    f"font-size:15px;line-height:21px;font-weight:800;letter-spacing:-0.01em;"
    f"color:{TINTA};margin:22px 0 8px;"
)
# Chamada (coleta suspeita, bloco que nao se aplica, aviso de base): o fio
# amarelo de 4 px a esquerda, sobre o cinza claro.
CHAMADA = (
    f"border-left:4px solid {ETIQUETA};background:{SUPERFICIE_2};padding:10px 12px;"
    f"margin:0 0 14px;font-size:13px;line-height:1.5;color:{TINTA};"
)
P = f"font-size:14px;line-height:1.55;margin:0 0 12px;color:{TINTA_2};"
LI = f"font-size:13.5px;line-height:1.5;margin-bottom:5px;color:{TINTA};"
NOTA = f"font-size:12px;line-height:1.5;margin:0 0 12px;color:{TINTA_FRACA};"
TABLE = f"{FONTE_CSS}border-collapse:collapse;width:100%;font-size:13px;margin-bottom:14px;"
# Celula com padding e fio proprios, para quem monta a tabela a mao.
TH = (
    f"text-align:left;padding:6px 8px;border-bottom:1px solid {TINTA};"
    f"font-size:11.5px;color:{TINTA_2};vertical-align:bottom;"
)
TD = f"padding:6px 8px;border-bottom:1px solid {LINHA_FINA};"
# A tabela de `tabela`: cabecalho em caixa alta miuda e espacada sobre o fio
# preto de 2 px; linha com o fio fino; a linha da casa-base no amarelo bem
# claro com o traco amarelo de 4 px a esquerda; o grupo em caixa alta, peso
# 800, sobre o fio preto; o total sob o fio preto.
LINHA_DE_DADOS = f'style="border-bottom:1px solid {GRADE};"'
LINHA_DA_BASE = f'bgcolor="{DESTAQUE_SUAVE}" style="border-bottom:1px solid {GRADE};"'
CELULA_DA_BASE = f"border-left:4px solid {ETIQUETA};font-weight:700;"
LINHA_DE_CABECALHO = (
    'valign="bottom" style="font-size:10px;letter-spacing:.12em;'
    f'text-transform:uppercase;color:{TINTA_2};border-bottom:2px solid {TINTA};"'
)
GRUPO = (
    "font-size:11px;font-weight:800;letter-spacing:0.14em;text-transform:uppercase;"
    f"border-bottom:2px solid {TINTA};padding-top:18px;"
)
TOTAL = f'style="border-top:2px solid {TINTA};font-weight:700;"'
# Lista: corpo, entrelinha e tinta no <ul>, que os itens herdam.
UL = f"margin:0 0 14px;padding-left:18px;font-size:13.5px;line-height:1.6;color:{TINTA};"
LINK = f"color:{TINTA};"
CINZA = f"color:{TINTA_FRACA};"
# Texto miudo e apagado sob outro (CNPJ sob o nome): `<small>` ja reduz o corpo.
# Sem `nowrap`: toda coluna de fundo com CNPJ tem largura para ele inteiro (a
# tabela larga tem 520 px no minimo), e o estilo repetido em ~75 CNPJs pesava.
MIUDO_CINZA = f"color:{TINTA_FRACA};"
# Legenda de tabela: o nome da lista em negrito e o recorte em uma linha.
LEGENDA = f"font-size:13px;line-height:1.45;margin:16px 0 4px;color:{TINTA_2};"


# --- texto -----------------------------------------------------------------------


def esc(value: object) -> str:
    return html.escape(str(value))


def negrito_html(texto: str) -> str:
    partes = re.split(r"\*\*(.+?)\*\*", texto)
    return "".join(
        f"<strong>{esc(parte)}</strong>" if i % 2 else esc(parte)
        for i, parte in enumerate(partes)
    )


LINK_MD = r"\[([^\]]+)\]\((https?://[^)\s]+)\)"


def md_para_html(texto: str) -> str:
    """Converte o `**negrito**` e os `[links](url)` de uma frase, escapando o resto.

    Existe porque as frases de veredito sao escritas uma vez, em markdown, e
    saem nos dois formatos: duplicar o texto para ter uma versao com <strong>
    seria a mesma divergencia que o resto desta revisao veio apagar. Os links
    sao as fontes citadas no texto (ver report/shared/fontes.py).
    """
    saida = []
    pos = 0
    for m in re.finditer(LINK_MD, texto):
        saida.append(negrito_html(texto[pos:m.start()]))
        saida.append(
            f'<a href="{esc(m.group(2))}" style="{LINK}">{esc(m.group(1))}</a>'
        )
        pos = m.end()
    saida.append(negrito_html(texto[pos:]))
    return "".join(saida)


def pontuado(*partes: str) -> str:
    """Partes curtas numa linha, separadas pelo ponto medio (U+00B7) no lugar de
    conjuncao - secao 5 da linguagem visual: rotulo curto, prazo uma vez."""
    return " · ".join(parte for parte in partes if parte)


def cor_do_valor(texto: str) -> str:
    """Verde para saldo positivo, vermelho para negativo - so em celula de valor.

    A cor nunca vem sozinha: a celula ja traz o sinal ("+2.397,9 MM",
    "-0,90pp"). Um par so de verde e vermelho em todo o e-mail, o de texto dos
    tokens - antes eram dois, um por secao.
    """
    if texto.startswith("+"):
        return f"color:{BOM_TEXTO};"
    if texto.startswith("-") and len(texto) > 1:
        return f"color:{CRITICO_TEXTO};"
    return ""


def fundo_html(nome: str, cnpj: str | None = None) -> str:
    """Nome do fundo com o CNPJ embaixo, miudo e apagado: o CNPJ nao ganha coluna."""
    if not cnpj:
        return esc(nome)
    return f'{esc(nome)}<br><small style="{MIUDO_CINZA}">{esc(cnpj)}</small>'


def miudo(texto_html: str) -> str:
    """Uma segunda linha miuda e apagada dentro de uma celula."""
    return f'<br><small style="{MIUDO_CINZA}">{texto_html}</small>'


# --- blocos ----------------------------------------------------------------------


def rolavel(tabela: str) -> str:
    """Tabela larga rola dentro da propria caixa no telefone, e nao a pagina."""
    return f'<div style="overflow-x:auto;">{tabela}</div>'


def legenda(texto_md: str) -> str:
    """O titulo curto de uma tabela: o nome da lista em negrito e o recorte."""
    return f'<p style="{LEGENDA}">{md_para_html(texto_md)}</p>'


def mais(quantos: int, onde: str) -> str:
    return f'<p style="{NOTA}">Mais {milhar(quantos)} {onde}.</p>' if quantos > 0 else ""


def secao(titulo_html: str) -> str:
    """O titulo de secao: a retranca com o traco amarelo."""
    return f'<h2 style="{RETRANCA}">{TRACO}{titulo_html}</h2>'


def chamada(texto_html: str) -> str:
    """O aviso que evita a leitura errada: fio amarelo sobre o cinza claro."""
    return f'<p style="{CHAMADA}">{texto_html}</p>'


def nota(texto_html: str) -> str:
    """Nota miuda (metodologia, ressalva curta, "Como ler")."""
    return f'<p style="{NOTA}">{texto_html}</p>'


def tabela(
    cabecalho: list[str],
    linhas: list[list[str]],
    *,
    direita=(),
    sinal=(),
    base: list[bool] | None = None,
    total: bool = False,
    compacta: bool = False,
    larguras: dict[int, int] | None = None,
    larga: bool = False,
    pintar: Callable[[int, int], tuple[str, str] | None] | None = None,
    centro=(),
) -> str:
    """Tabela de dados do e-mail. As celulas chegam em HTML (escapadas por quem
    monta); `direita` sao as colunas de numero (a direita; valor curto nao
    quebra) e as de `sinal` ganham a cor do sinal, sempre ao lado dele.
    `base` marca a linha da casa-base (fundo amarelo claro e traco amarelo);
    `total` poe a ultima linha sob o fio preto. `larguras` fixa a coluna em % - as
    listas de fundos de uma secao saem alinhadas entre si. Corpo alinhado no
    topo (no <tbody>, uma vez): com o CNPJ embaixo do nome, o numero fica na
    altura do nome.

    Uma linha que chega como texto (e nao lista) e cabecalho de grupo: ocupa a
    largura toda, em caixa alta ("BTG Pactual" antes das mudancas dela).

    Tabela de cinco colunas ou mais (ou `larga`) tem largura minima de 520 px:
    no telefone ela rola dentro da propria caixa, em vez de espremer a coluna
    de texto ate o nome do fundo quebrar em cinco linhas.

    O alinhamento da maioria das colunas vai na linha (`<tr align>`) e so a
    excecao o repete na celula: com um `align="right"` por numero, a tabela de
    captacao pagava ~6 KB so disso no orcamento do e-mail.

    `pintar(linha, coluna)` devolve (fundo, tinta) de uma celula de mapa de
    calor, ou None; `centro` sao as colunas centradas (as do mapa)."""
    larguras = larguras or {}
    direita = set(direita)
    centro = set(centro)
    maioria = "right" if len(direita) * 2 > len(cabecalho) else "left"

    def alinhamento(i: int) -> str:
        if i in centro:
            return ' align="center"'
        lado = "right" if i in direita else "left"
        return "" if lado == maioria else f' align="{lado}"'

    def cabeca(i: int, titulo: str) -> str:
        atributos = alinhamento(i)
        if i in larguras:
            atributos += f' width="{larguras[i]}%"'
        if len(titulo) <= 10:
            atributos += " nowrap"
        return f"<th{atributos}>{esc(titulo)}</th>"

    cab = "".join(cabeca(i, t) for i, t in enumerate(cabecalho))
    corpo = []
    for n, linha in enumerate(linhas):
        if isinstance(linha, str):
            corpo.append(
                f'<tr><td colspan="{len(cabecalho)}" style="{GRUPO}">{linha}</td></tr>'
            )
            continue
        e_base = bool(base and base[n])
        celulas = []
        for i, celula in enumerate(linha):
            texto = re.sub(r"<[^>]+>", "", celula)
            atributos = alinhamento(i)
            if i in direita or i in centro:
                # "+2.397,9 MM" em duas linhas se le como dois valores; texto
                # com mais de um numero ("606 de 636 (95%)") pode quebrar. Cada
                # linha da celula (o ticket vai sob os cotistas) conta sozinha.
                partes = re.split(r"<br\s*/?>", celula)
                if max(len(re.sub(r"<[^>]+>", "", parte)) for parte in partes) <= 16:
                    atributos += " nowrap"
            estilo = cor_do_valor(texto) if i in sinal else ""
            cor = pintar(n, i) if pintar else None
            if cor:
                atributos += f' bgcolor="{cor[0]}"'
                estilo += f"color:{cor[1]};"
            if i == 0 and e_base:
                estilo += CELULA_DA_BASE
            if estilo:
                atributos += f' style="{estilo}"'
            celulas.append(f"<td{atributos}>{celula}</td>")
        if total and n == len(linhas) - 1:
            atributos_da_linha = TOTAL
        else:
            atributos_da_linha = LINHA_DA_BASE if e_base else LINHA_DE_DADOS
        corpo.append(f'<tr {atributos_da_linha}>{"".join(celulas)}</tr>')
    largo = larga or len(cabecalho) >= 5
    alinhar = ' align="right"' if maioria == "right" else ""
    tabela = (
        f'<table cellpadding="{4 if compacta else 6}" cellspacing="0" '
        f'style="{TABLE}{"min-width:520px;" if largo else ""}'
        f'{"font-size:12px;" if compacta else ""}">'
        f'<thead><tr {LINHA_DE_CABECALHO}{alinhar}>{cab}</tr></thead>'
        f'<tbody valign="top"{alinhar}>{"".join(corpo)}</tbody></table>'
    )
    # Toda tabela na caixa que rola: mesmo a de quatro colunas passa dos ~320 px
    # do telefone quando os numeros nao quebram (a tabela mensal, os cobertos).
    return rolavel(tabela)


# --- a pagina --------------------------------------------------------------------


def faixa(rotulo: str, data: str) -> str:
    """A faixa preta do topo, como a barra de navegacao do deck: o rotulo do
    monitor em caixa alta a esquerda, a data da execucao a direita."""
    return (
        f'<tr><td bgcolor="{TINTA}" style="padding:0 20px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td height="44" style="{FONTE_CSS}font-size:11px;font-weight:700;'
        f'letter-spacing:0.18em;text-transform:uppercase;color:{SUPERFICIE};">'
        f"{esc(rotulo)}</td>"
        f'<td align="right" nowrap style="{FONTE_CSS}font-size:11px;color:{EIXO};">'
        f"{esc(data)}</td></tr></table></td></tr>"
    )


def cabecalho(retranca: str, titulo: str, linha: str = "") -> str:
    """Retranca com o traco, titulo em peso 800 e a linha de data (texto)."""
    return (
        f'<p style="{RETRANCA}font-weight:700;margin:0 0 14px;padding:0;border:0;">'
        f"{TRACO}{esc(retranca)}</p>"
        f'<h1 style="font-size:28px;line-height:32px;font-weight:800;letter-spacing:-0.02em;'
        f'color:{TINTA};margin:0 0 8px;">{esc(titulo)}</h1>'
        + (f'<p style="font-size:14px;line-height:20px;color:{TINTA_2};margin:0;">{esc(linha)}</p>'
           if linha else "")
    )


def casca(
    titulo: str, previa: str, corpo: str, faixa: str = "", largura: int = LARGURA_EMAIL
) -> str:
    """A casca do e-mail: tabela de 640 px, centrada, com a previa escondida.

    Tabela e nao `div`, porque o Outlook de mesa ignora `max-width` em `div`;
    o bloco condicional `[if mso]` fixa os 640 px so para ele, e os outros
    clientes encolhem a tabela ate a largura do telefone. `color-scheme` claro
    declarado: o modo escuro automatico inverteria a rampa dos graficos. A
    previa (o texto que a caixa de entrada mostra ao lado do assunto) e a
    frase de abertura, escondida no corpo. `titulo` e `previa` chegam escapados.

    `faixa` e a linha de topo, na largura toda do cartao (a faixa preta do
    padrao executivo da XP, de `faixa()`). `largura` alarga a pagina para o
    relatorio que nao e e-mail (a grade de 12 meses da captacao de ETF).
    """
    preenchimento = "&#847;&zwnj;&nbsp;" * 16
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{titulo}</title></head>
<body style="margin:0;padding:0;background:{PAGINA};" bgcolor="{PAGINA}">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{PAGINA};opacity:0;">{previa}{preenchimento}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGINA}" style="background:{PAGINA};">
<tr><td align="center" style="padding:24px 8px;">
<!--[if mso]><table role="presentation" width="{largura}" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{SUPERFICIE}" style="max-width:{largura}px;width:100%;table-layout:fixed;background:{SUPERFICIE};">
{faixa}
<tr><td style="padding:26px 20px 30px;{FONTE_CSS}color:{TINTA};text-align:left;">
{corpo}
</td></tr></table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
</body></html>
"""


def fontes_html(fontes) -> str:
    """As fontes como tabela: nome com link | o que fornece (e os arquivos lidos).

    Recebe `Fonte`s (report/shared/fontes.py); vazio devolve "".
    """
    linhas = []
    for fonte in fontes:
        uso = esc(fonte.uso[:1].upper() + fonte.uso[1:])
        if fonte.url_dados:
            uso += (
                f' · <a href="{esc(fonte.url_dados)}" style="{LINK}">arquivos lidos</a>'
            )
        linhas.append([md_para_html(f"[{fonte.nome}]({fonte.url})"), uso])
    if not linhas:
        return ""
    return secao("Fontes") + tabela(["Fonte", "O que fornece"], linhas)
