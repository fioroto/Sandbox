"""Coletor da vitrine de previdencia do Itau - o catalogo do Private Bank.

Por decisao do usuario (2026-09-22), a vitrine do Itau neste modulo e a tabela
de rentabilidade do Private Bank, nao a prateleira de varejo (Ion) - e sai
rotulada "Itau Private" por isso. HTML servido pronto: HTTP simples responde, sem
navegador.

Estrutura medida em 2026-09-22
------------------------------

Uma tabela principal com 437 fundos numerados (1)..(437), em secoes marcadas por
`td.tabLinhaTitulo`. A sequencia de secoes aparece DUAS vezes:

- bloco 1, fundos 1-392: o catalogo geral por classe de ativo (Juros
  Pos-Fixados, Prefixados, Inflacao, Multimercados, Acoes, Cambial);
- bloco 2, fundos 393-437: a sequencia recomeca - e a secao de previdencia,
  com os nomes comerciais dos planos ("Private Prev RF", "Flexprev Private DI").
  Uma das secoes dele se chama literalmente "PREVIDENCIA - Renda Fixa".

A vitrine de previdencia e o bloco 2, identificado pela ESTRUTURA (a primeira
secao cujo titulo repete um anterior, ou que diz "previdencia"), nao pelo nome
do fundo. Medido: um filtro por nome perderia 6 dos 45 planos do bloco 2
("Platinum III", "Private Kinea Multimercado"...) e traria 22 veiculos do bloco
1 que sao o mesmo fundo com o nome de registro.

Tres detalhes do HTML que o parser trata:

- **Duplicata dentro do bloco 2.** Quatro pares tem a mesma cota e os mesmos
  seis retornos: o mesmo veiculo sob dois nomes ("Itau Private Prev Kinea Rf" e
  "Itau Personnalite VGBL Kinea RF"). Deduplicado por essa chave - 45 linhas, 41
  veiculos.
- **Nome de registro.** 15 planos do bloco 2 tem um gemeo no bloco 1 com a
  mesma cota e os mesmos retornos, e o gemeo traz o nome de registro ("Platinum III" <-> "Itau
  Flexprev Platinum III Renda Fixa"), mais proximo da denominacao da CVM. Vai
  para `nome_registro`, que o resolvedor por nome tenta primeiro.
- **Codificacao.** A pagina nao declara charset no HTML; o cabecalho HTTP diz
  iso-8859-1. Deixar o BeautifulSoup adivinhar dava iso-8859-2 - "Inflacao" saia
  com a-breve no lugar do a-til. O texto e decodificado antes do parse.

Fora da tabela principal ha 8 tabelas soltas com a mesma numeracao (438 em
diante): sao indicadores - IGPM, Poupanca, CDI -, nao fundos. Nao entram.

Nao ha CNPJ na pagina: o Itau fica fora dos blocos que dependem de identidade
(`competitive._MIN_CNPJ_COVERAGE`), como ja fica no modulo de fundos.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable, get
from shelf_monitor.models import AssetClass, CollectorStrategy, FundOffer
from shelf_monitor.normalize import classify_asset_class, normalize_text, parse_number
from shelf_monitor.previdencia.eixo import KEY_NOME_REGISTRO

_NUMERO_RE = re.compile(r"\((\d+)\)\s*$")
_MARCA_PREVIDENCIA = re.compile(r"\b(PREV\w*|FLEXPREV|PGBL|VGBL|FIE|FIFE)\b")

# Abaixo desta fracao de nomes com marca de previdencia, o "bloco 2" provavelmente
# nao e mais o de previdencia - a pagina mudou de estrutura. Medido: 39 de 45.
_MIN_MARCA_NO_BLOCO = 0.5

_RETORNOS = ("mes", "mes_anterior", "ano", "12m", "24m", "36m")

# Codecs que o cabecalho HTTP declara e que o navegador le como windows-1252.
_LATIN1 = {"", "iso-8859-1", "latin-1", "latin1", "us-ascii", "ascii"}


def decodificar(conteudo: bytes, declarado: str | None) -> str:
    """Bytes da pagina como texto, pelo cabecalho HTTP - nunca por adivinhacao.

    iso-8859-1 declarado vira cp1252, como os navegadores fazem: e um
    superconjunto dele para os caracteres imprimiveis.
    """
    codec = (declarado or "").strip().lower()
    return conteudo.decode("cp1252" if codec in _LATIN1 else codec, errors="replace")


@dataclass
class _Linha:
    numero: int
    nome: str
    secao: str
    bloco_previdencia: bool
    data_cota: str | None
    cota: float | None
    retornos: dict[str, float | None]
    pl_mm: float | None
    taxa: float | None
    status: str | None
    nomes_alternativos: list[str] = field(default_factory=list)


def _texto(elemento) -> str:
    return " ".join(elemento.get_text(" ", strip=True).split())


def _linhas_diretas(tabela) -> list:
    """As <tr> da propria tabela - as aninhadas (retornos) ficam de fora."""
    return [tr for tr in tabela.find_all("tr") if tr.find_parent("table") is tabela]


def _eh_linha_de_fundo(tr) -> bool:
    celulas = tr.find_all("td", recursive=False)
    return bool(celulas) and _NUMERO_RE.search(_texto(celulas[0])) is not None


def _eh_titulo_de_secao(tr) -> bool:
    celulas = tr.find_all("td", recursive=False)
    return len(celulas) == 1 and (
        "tabLinhaTitulo" in (celulas[0].get("class") or [])
        or celulas[0].get("colspan") is not None
    )


def _tabela_principal(soup):
    """A tabela com mais linhas de fundo proprias - as soltas tem uma so."""
    melhor, maior = None, 0
    for tabela in soup.find_all("table"):
        quantas = sum(1 for tr in _linhas_diretas(tabela) if _eh_linha_de_fundo(tr))
        if quantas > maior:
            melhor, maior = tabela, quantas
    return melhor


def _ler_linha(tr, secao: str, bloco_previdencia: bool) -> _Linha | None:
    celulas = tr.find_all("td", recursive=False)
    if len(celulas) < 8:
        return None
    primeira = _texto(celulas[0])
    achado = _NUMERO_RE.search(primeira)
    if not achado:
        return None
    nome = primeira[: achado.start()].strip()
    if not nome:
        return None
    valores = _texto(celulas[4]).split()
    retornos = {
        chave: parse_number(valor) if valor != "-" else None
        for chave, valor in zip(_RETORNOS, valores)
    }
    data = _texto(celulas[1])
    status = _texto(celulas[7]).upper() or None
    return _Linha(
        numero=int(achado.group(1)),
        nome=nome,
        secao=secao,
        bloco_previdencia=bloco_previdencia,
        data_cota=data if data and data != "-" else None,
        cota=parse_number(_texto(celulas[2])),
        retornos=retornos,
        pl_mm=parse_number(_texto(celulas[5])),
        taxa=parse_number(_texto(celulas[6])),
        status=status,
    )


def _ler_tabela(tabela) -> tuple[list[_Linha], bool]:
    """Linhas de fundo em ordem, e se o bloco de previdencia foi identificado."""
    linhas: list[_Linha] = []
    vistas: set[str] = set()
    secao = ""
    em_previdencia = False
    for tr in _linhas_diretas(tabela):
        if _eh_titulo_de_secao(tr):
            secao = _texto(tr)
            chave = normalize_text(secao)
            # O bloco de previdencia comeca onde a sequencia de secoes recomeca,
            # ou numa secao que se declara de previdencia - o que vier primeiro.
            if chave in vistas or "PREVIDENCIA" in chave:
                em_previdencia = True
            vistas.add(chave)
            continue
        if _eh_linha_de_fundo(tr):
            linha = _ler_linha(tr, secao, em_previdencia)
            if linha is not None:
                linhas.append(linha)
    return linhas, any(linha.bloco_previdencia for linha in linhas)


def _tem_marca(nome: str) -> bool:
    return _MARCA_PREVIDENCIA.search(normalize_text(nome)) is not None


def _veiculo(linha: _Linha) -> tuple | None:
    """Chave do veiculo: a cota e os seis retornos, juntos.

    Cota sozinha basta nos casos medidos, mas um erro de pagina que repetisse a
    cota de um fundo em outro fundiria dois produtos. Cota E seis janelas de
    retorno identicas nao acontecem por coincidencia. Sem cota nao ha como
    provar identidade: None, e a linha fica sozinha.
    """
    if linha.cota is None:
        return None
    return (linha.cota, *(linha.retornos.get(chave) for chave in _RETORNOS))


def _deduplicar(linhas: list[_Linha]) -> list[_Linha]:
    """Mesmo veiculo: a primeira linha fica, as outras viram nome alternativo."""
    por_veiculo: OrderedDict[object, _Linha] = OrderedDict()
    for linha in linhas:
        chave = _veiculo(linha) or ("sem-cota", linha.numero)
        if chave in por_veiculo:
            por_veiculo[chave].nomes_alternativos.append(linha.nome)
        else:
            por_veiculo[chave] = linha
    return list(por_veiculo.values())


def _classe_da_secao(secao: str, nome: str) -> AssetClass:
    texto = normalize_text(secao)
    if "MULTIMERCADO" in texto:
        return AssetClass.MULTIMERCADO
    if "ACOES" in texto:
        return AssetClass.ACOES
    if "CAMBIAL" in texto:
        return AssetClass.CAMBIAL
    if "JUROS" in texto or "INFLACAO" in texto or "RENDA FIXA" in texto:
        return AssetClass.RENDA_FIXA
    return classify_asset_class(nome)


def _oferta(linha: _Linha, nome_registro: str | None) -> FundOffer:
    fechado = {"A": False, "F": True}.get(linha.status or "")
    extra = {
        chave: valor
        for chave, valor in (
            ("secao", linha.secao or None),
            ("numero_catalogo", linha.numero),
            ("cota", linha.cota),
            ("data_cota", linha.data_cota),
            ("rentabilidade", {k: v for k, v in linha.retornos.items() if v is not None} or None),
            # A pagina publica em R$ milhoes; aqui em reais, como o `pl` das
            # outras casas.
            ("pl_medio_12m", linha.pl_mm * 1_000_000 if linha.pl_mm is not None else None),
            ("nomes_alternativos", linha.nomes_alternativos or None),
            (KEY_NOME_REGISTRO, nome_registro),
        )
        if valor is not None
    }
    return FundOffer(
        platform="itau",
        name=linha.nome,
        asset_class=_classe_da_secao(linha.secao, linha.nome),
        raw_category=linha.secao or None,
        admin_fee_pct=linha.taxa,
        closed_for_investment=fechado,
        extra=extra,
    )


def parse_catalogo(html: str) -> tuple[list[FundOffer], list[str]]:
    """(fundos da vitrine de previdencia, avisos) a partir do HTML ja decodificado."""
    soup = BeautifulSoup(html, "lxml")
    tabela = _tabela_principal(soup)
    if tabela is None:
        raise SourceUnavailable("tabela de rentabilidade não encontrada na página")

    linhas, achou_bloco = _ler_tabela(tabela)
    avisos: list[str] = []
    if achou_bloco:
        vitrine = [linha for linha in linhas if linha.bloco_previdencia]
        com_marca = sum(1 for linha in vitrine if _tem_marca(linha.nome))
        if vitrine and com_marca / len(vitrine) < _MIN_MARCA_NO_BLOCO:
            avisos.append(
                f"bloco de previdência com só {com_marca} de {len(vitrine)} nomes "
                "com marca de previdência - a estrutura da página pode ter mudado"
            )
    else:
        vitrine = [linha for linha in linhas if _tem_marca(linha.nome)]
        avisos.append(
            "bloco de previdência não identificado pela estrutura; vitrine filtrada "
            f"pelo nome ({len(vitrine)} de {len(linhas)} linhas)"
        )

    # Nome de registro: o gemeo no catalogo geral, reconhecido pelo mesmo veiculo.
    registro_por_veiculo: dict[tuple, str] = {}
    for linha in linhas:
        if not linha.bloco_previdencia and linha.cota is not None:
            registro_por_veiculo.setdefault(_veiculo(linha), linha.nome)

    ofertas = [
        _oferta(linha, registro_por_veiculo.get(_veiculo(linha)) if _veiculo(linha) else None)
        for linha in _deduplicar(vitrine)
    ]
    return ofertas, avisos


class ItauPrivateCollector(PlatformCollector):
    """HTTP simples na tabela; o navegador so se o HTTP for barrado."""

    def _collect_impl(self) -> list[FundOffer]:
        """Uma fonte so, e sem `merge_offers`.

        O merge une fundos pela `identity`, que sem CNPJ e o slug do nome - e o
        slug descarta RF e MM como tokens genericos. Medido em 2026-09-22:
        "Private Prev Rf Vision Plus" e "Private Prev Vision Plus MM" viram o
        mesmo `private-prev-vision-plus`, e o merge fundia dois fundos
        diferentes (a vitrine saia com 40 veiculos em vez de 41). Com uma fonte
        so nao ha o que unir, e o parser ja deduplica pelo veiculo (a cota).

        O diff aguenta a identidade repetida: indexa a segunda como `#2`, na
        ordem do catalogo, que e estavel entre coletas.
        """
        fonte = CollectorStrategy.HTML_SCRAPE
        ofertas = self._collect_web()
        for oferta in ofertas:
            oferta.sources = [fonte.value]
        self.strategy_used = fonte
        self.strategies_used = [fonte.value]
        self.source_counts = {fonte.value: len(ofertas)}
        return ofertas

    def _collect_web(self) -> list[FundOffer]:
        url = self.platform.shelf_url
        if not url:
            raise SourceUnavailable("shelf_url não configurada para o Itaú Private")
        try:
            resposta = get(self.session, url, self.config, accept="text/html")
            html = decodificar(resposta.content, resposta.encoding)
        except SourceUnavailable as erro:
            html = self._pelo_navegador(url, erro)
        self.source_urls.append(url)

        ofertas, avisos = parse_catalogo(html)
        self.warnings.extend(avisos)
        if not ofertas:
            raise SourceUnavailable(f"{url}: nenhum fundo de previdência na tabela")
        return ofertas

    def _pelo_navegador(self, url: str, erro_http: SourceUnavailable) -> str:
        from shelf_monitor.collectors.browser import render_page

        captura = render_page(
            url,
            user_agent=self.config.user_agent,
            executable_path=self.config.chromium_path,
            headed=self.config.browser_headed,
            channel=self.config.browser_channel,
        )
        # O navegador decodifica pelo cabecalho, como `decodificar` faz.
        self.partial_reason = f"tabela lida pelo navegador ({erro_http})"
        return captura.html
