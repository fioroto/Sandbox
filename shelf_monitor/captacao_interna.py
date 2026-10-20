"""Captacao interna: quanto cada fundo captou na XP, pelas bases da casa.

Pedido de 20/10/2026: ao lado das maiores captacoes e resgates do mercado - o
informe diario da CVM, que e por fundo e nunca por distribuidor -, quanto o
fundo captou na XP. A captacao vem de duas bases internas, em pastas fora do
git (dado comercial):

- `data/captacao_xp/diaria/`: uma linha por fundo e dia;
- `data/captacao_xp/mensal/`: uma linha por fundo e mes.

Os modelos com o cabecalho moram em `data/captacao_xp/modelo_diaria.csv` e
`modelo_mensal.csv`. Regras de leitura:

- CSV (`;` ou `,`, UTF-8 com ou sem BOM, ou Latin-1) ou Excel `.xlsx` (a
  primeira aba com cabecalho; o cabecalho e a primeira linha, das 20 primeiras,
  com a coluna de CNPJ ou de codigo CVM). `.xls` e `.xlsb` sao recusados com o
  motivo. Vale qualquer pasta dentro de `data/captacao_xp`: a que tem "diaria"
  ou "mensal" no nome, com ou sem acento, decide a base; fora delas, decide o
  cabecalho (`_tipo_do_arquivo`, 21/10/2026);
- colunas por nome, sem diferenciar maiuscula, acento nem pontuacao: `cnpj`
  (ou `cod_cnpj`, ou `codigo_cvm`) e obrigatorio, como texto ou como numero; a
  data (`data`, `dia`, `data_movimento`) na diaria e o mes (`mes`,
  `competencia`, `mes_ano`, ou uma data do mes) na mensal - sem coluna com
  esses nomes, a primeira cujos valores sao todos datas (ou meses), com aviso;
  o valor e `captacao_liquida`, ou `captacao` sozinha (a coluna das bases da
  XP, 20/10/2026), ou `captacao_bruta` (ou `aplicacao`) menos `resgate`;
- valores em reais, com sinal: negativo e resgate liquido, inclusive no formato
  contabil ("(1.234,56)" ou "1.234,56-"). A unidade escrita no cabecalho vale:
  "(R$ mil)" multiplica por mil, "(R$ MM)" ou "(R$ milhões)" por um milhao - o
  numero sai na mesma ordem de grandeza do da CVM;
- a data como data, texto (16/10/2026), numero aaaammdd (20261016) ou o numero
  serial do Excel (46311, a celula sem formato de data); o mes como 202607,
  07/2026, jul/2026 ou uma data do mes;
- o fundo e resolvido como na lista distribuivel: CNPJ da classe, CNPJ do
  fundo ou codigo CVM. CNPJ de fundo com mais de uma classe fica como veio, com
  aviso: a captacao nao se divide entre as classes;
- no mesmo arquivo, linhas do mesmo fundo e dia (ou mes) somam - a base pode
  trazer uma linha por canal ou por subclasse. O mesmo dia (ou mes) em dois
  arquivos vale o do ultimo pela ordem do nome, com aviso: reexportar um
  periodo nao conta a captacao duas vezes;
- linha que nao se resolve e listada com arquivo, linha e motivo - nunca
  descartada em silencio.

Um dia que a base traz e um dia medido: o fundo que nao aparece nele captou
zero na XP naquele dia. O mesmo vale para o mes da base mensal.
"""
from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from shelf_monitor.distribuivel import (
    ArquivoLido,
    LinhaInvalida,
    Resolvedor,
    _chave_de_coluna,
    _resolvedor_do_cadastro,
)
from shelf_monitor.market.parse import parse_date
from shelf_monitor.normalize import (
    normalize_cnpj,
    normalize_cvm_code,
    parse_money,
    strip_accents,
)
from shelf_monitor.texto import competencia_br, contagem, data_br, milhar

DIARIA = "diaria"
MENSAL = "mensal"

CABECALHO_DIARIA = ("data", "cnpj", "nome", "captacao_bruta", "resgate", "captacao_liquida")
CABECALHO_MENSAL = ("mes", "cnpj", "nome", "captacao_bruta", "resgate", "captacao_liquida")

# Os nomes de coluna aceitos, ja como `_chave_de_coluna` os deixa e sem a
# unidade (ver `_sem_unidade`).
_CNPJ = (
    "cnpj", "cod_cnpj", "codigo_cnpj", "cnpj_fundo", "cnpj_classe", "cnpj_do_fundo",
    "cnpj_da_classe",
)
_CODIGO = ("codigo_cvm", "cod_cvm")
_DIA = (
    "data", "dia", "dt", "data_movimento", "data_mov", "dt_movimento", "dt_mov",
    "data_referencia", "data_ref", "dt_referencia", "date",
)
_MES = (
    "mes", "competencia", "mes_ano", "ano_mes", "mes_referencia", "mes_ref", "periodo",
    "data", "data_referencia", "data_ref", "dt_referencia", "dt",
)
_LIQUIDA = (
    "captacao_liquida", "capt_liquida", "captacao_liq", "capt_liq", "liquida",
    "liquido", "valor_liquido", "fluxo_liquido", "net",
)
# "captacao" sozinha e a liquida: e a coluna das bases da XP (coluna S, ao lado
# do cod_cnpj na coluna N - informado em 20/10/2026).
_CAPTACAO = ("captacao", "capt")
_BRUTA = (
    "captacao_bruta", "capt_bruta", "aplicacao", "aplicacoes", "aplicacao_bruta",
    "entradas",
)
_RESGATE = ("resgate", "resgates", "resgate_bruto", "resgates_brutos", "saidas")
# Coluna unica de valor, sem resgate ao lado: e a liquida.
_UNICA = ("valor", "fluxo")
# Quantas linhas a deteccao da coluna de data pelo conteudo olha.
_AMOSTRA = 50
# A unidade no fim do nome da coluna, da mais longa para a mais curta.
_UNIDADES = (
    ("_r_milhoes", 1e6), ("_milhoes", 1e6), ("_r_mm", 1e6), ("_mm", 1e6),
    ("_r_mil", 1e3), ("_mil", 1e3), ("_reais", 1.0), ("_brl", 1.0), ("_rs", 1.0),
    ("_r", 1.0),
)
_LINHAS_ATE_O_CABECALHO = 20
_MESES = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


def _sem_unidade(chave: str) -> tuple[str, float]:
    """("captacao_liquida", 1000.0) para "captacao_liquida_r_mil"."""
    for sufixo, escala in _UNIDADES:
        if chave.endswith(sufixo) and len(chave) > len(sufixo):
            return chave[: -len(sufixo)].removesuffix("_em"), escala
    return chave, 1.0


@dataclass(frozen=True)
class _Colunas:
    """As colunas de um arquivo: (nome no arquivo, escala) para os valores."""

    cnpj: str | None
    codigo: str | None
    quando: str
    liquida: tuple[str, float] | None = None
    bruta: tuple[str, float] | None = None
    resgate: tuple[str, float] | None = None
    # A data (ou o mes) saiu do conteudo, nao do nome da coluna.
    quando_pelo_conteudo: bool = False


def _pelo_conteudo(linhas: list[tuple[int, dict]], cabecalho, fora: set[str], ler) -> str | None:
    """A primeira coluna, fora as ja usadas, cujos valores da amostra `ler` le todos."""
    amostra = [linha for _, linha in linhas[:_AMOSTRA]]
    for chave in cabecalho:
        if not chave or chave in fora:
            continue
        valores = [linha.get(chave) for linha in amostra if linha.get(chave) not in (None, "")]
        if valores and all(ler(v) is not None for v in valores):
            return chave
    return None


# Quantas linhas a escolha da coluna de CNPJ confere contra o cadastro.
_AMOSTRA_DO_CNPJ = 200


def _coluna_de_cnpj(
    candidatas: list[tuple[str, float]], linhas: list[tuple[int, dict]],
    resolver: Resolvedor | None,
) -> tuple[str, float] | None:
    """Entre as colunas com nome de CNPJ, a cujos valores mais estao no cadastro
    (21/10/2026): a planilha pode trazer o CNPJ do cliente ou da gestora ao lado
    do `cod_cnpj` do fundo. Sem cadastro para conferir, a primeira pelo nome."""
    if len(candidatas) < 2 or resolver is None:
        return candidatas[0] if candidatas else None

    def no_cadastro(coluna: str) -> int:
        achados = 0
        for _, linha in linhas[:_AMOSTRA_DO_CNPJ]:
            cnpj = normalize_cnpj(_texto_de_cnpj(linha.get(coluna)))
            if cnpj and resolver(cnpj, None):
                achados += 1
        return achados

    pontos = [no_cadastro(c[0]) for c in candidatas]
    return candidatas[max(range(len(candidatas)), key=lambda i: (pontos[i], -i))]


def _colunas(
    cabecalho: list[str], tipo: str, linhas: list[tuple[int, dict]] = (),
    resolver: Resolvedor | None = None,
) -> _Colunas | str:
    """As colunas do arquivo, ou o motivo de nao dar para le-lo."""
    por_nome: dict[str, tuple[str, float]] = {}
    for chave in cabecalho:
        if chave:
            nome, escala = _sem_unidade(chave)
            por_nome.setdefault(nome, (chave, escala))

    def achar(nomes: tuple[str, ...]) -> tuple[str, float] | None:
        return next((por_nome[n] for n in nomes if n in por_nome), None)

    cnpj = _coluna_de_cnpj([por_nome[n] for n in _CNPJ if n in por_nome], linhas, resolver)
    codigo = achar(_CODIGO)
    if cnpj is None and codigo is None:
        return "sem coluna de CNPJ nem de código CVM"
    valores: dict = {}
    if liquida := achar(_LIQUIDA) or achar(_CAPTACAO):
        valores["liquida"] = liquida
    elif (bruta := achar(_BRUTA)) and (resgate := achar(_RESGATE)):
        valores.update(bruta=bruta, resgate=resgate)
    elif (unica := achar(_UNICA)) and not achar(_RESGATE):
        valores["liquida"] = unica
    else:
        return "sem coluna de captação líquida (nem de captação bruta e resgate)"
    quando = achar(_DIA if tipo == DIARIA else _MES)
    pelo_conteudo = quando is None
    if pelo_conteudo:
        usadas = {c[0] for c in (cnpj, codigo, *valores.values()) if c}
        achada = _pelo_conteudo(linhas, cabecalho, usadas, _dia if tipo == DIARIA else _mes)
        if achada is None:
            return "sem coluna de data" if tipo == DIARIA else "sem coluna de mês"
        quando = (achada, 1.0)
    return _Colunas(
        cnpj=cnpj[0] if cnpj else None, codigo=codigo[0] if codigo else None,
        quando=quando[0], quando_pelo_conteudo=pelo_conteudo, **valores,
    )


def _numero(bruto: object, coluna: tuple[str, float] | None) -> float | None:
    if coluna is None or bruto in (None, ""):
        return None
    negativo = False
    if isinstance(bruto, str):
        # O formato contabil: "(1.234,56)" ou "1.234,56-" e negativo.
        texto = bruto.strip()
        negativo = (texto.startswith("(") and texto.endswith(")")) or texto.endswith("-")
        bruto = texto.strip("()").rstrip("-").strip()
    valor = parse_money(bruto)
    if valor is None:
        return None
    return (-abs(valor) if negativo else valor) * coluna[1]


def _liquida(linha: dict, colunas: _Colunas) -> float | None:
    """A captacao liquida da linha, em reais; None sem valor."""
    if colunas.liquida:
        return _numero(linha.get(colunas.liquida[0]), colunas.liquida)
    bruta = _numero(linha.get(colunas.bruta[0]), colunas.bruta)
    resgate = _numero(linha.get(colunas.resgate[0]), colunas.resgate)
    if bruta is None and resgate is None:
        return None
    # O resgate vem positivo ou negativo, conforme a base: sai sempre.
    return (bruta or 0.0) - abs(resgate or 0.0)


# O numero serial do Excel: dias desde 30/12/1899. A faixa vai de 1954 a 2119 -
# fora dela o numero nao e data.
_EPOCA_DO_EXCEL = date(1899, 12, 30)
_SERIAIS = range(20_000, 80_000)


def _aaaammdd(texto: str) -> date | None:
    if not re.fullmatch(r"(19|20)\d{6}", texto):
        return None
    try:
        return date(int(texto[:4]), int(texto[4:6]), int(texto[6:]))
    except ValueError:
        return None


def _dia(bruto: object, serial: bool = False) -> date | None:
    """A data da celula. `serial` aceita o numero serial do Excel - so na coluna
    com nome de data: pelo conteudo, um codigo de assessor passaria por data."""
    if isinstance(bruto, datetime):
        return bruto.date()
    if isinstance(bruto, date):
        return bruto
    if isinstance(bruto, float) and bruto.is_integer():
        bruto = int(bruto)
    if isinstance(bruto, int) and not isinstance(bruto, bool):
        if serial and bruto in _SERIAIS:
            return _EPOCA_DO_EXCEL + timedelta(days=bruto)
        return _aaaammdd(str(bruto))
    texto = str(bruto or "").strip()
    achado = parse_date(texto) or _aaaammdd(texto)
    if achado is None and texto:
        for formato in ("%d/%m/%y", "%d.%m.%Y"):
            try:
                return datetime.strptime(texto.split(" ")[0], formato).date()
            except ValueError:
                continue
    return achado


def _mes(bruto: object) -> str | None:
    """"202608" para 2026-08, 08/2026, ago/2026, ago-26 ou uma data do mes."""
    dia = _dia(bruto)
    if dia is not None:
        return f"{dia.year:04d}{dia.month:02d}"
    texto = strip_accents(str(bruto or "")).strip().lower()
    ano = mes = None
    if achado := re.fullmatch(r"(\d{4})[-/]?(\d{2})", texto):
        ano, mes = int(achado[1]), int(achado[2])
    elif achado := re.fullmatch(r"(\d{1,2})[-/](\d{4})", texto):
        ano, mes = int(achado[2]), int(achado[1])
    elif achado := re.fullmatch(r"([a-z]{3})[a-z]*[-/ .]*(?:de\s+)?(\d{2}|\d{4})", texto):
        mes = _MESES.get(achado[1])
        ano = int(achado[2])
        ano = ano + 2000 if ano < 100 else ano
    if ano is None or mes is None or not 1 <= mes <= 12:
        return None
    return f"{ano:04d}{mes:02d}"


def _texto_de_cnpj(bruto: object) -> str:
    """O CNPJ como texto: o Excel o guarda como numero, as vezes com ".0"."""
    if isinstance(bruto, float) and bruto.is_integer():
        bruto = int(bruto)
    texto = str(bruto if bruto is not None else "").strip()
    # O mesmo numero salvo em CSV: "11000000000101,0".
    return re.sub(r"^(\d+)[.,]0+$", r"\1", texto)


def _classe(
    linha: dict, colunas: _Colunas, resolver: Resolvedor | None
) -> tuple[str | None, str | None, int]:
    """(CNPJ de classe, motivo da recusa, classes do CNPJ quando sao varias)."""
    bruto = _texto_de_cnpj(linha.get(colunas.cnpj)) if colunas.cnpj else ""
    if re.fullmatch(r"[\d.,]+[eE][+-]?\d+", bruto):
        # "1,1E+13": o CSV salvo pelo Excel com o CNPJ em numero perdeu digitos.
        return None, (
            f"CNPJ em notação científica '{bruto}' - os dígitos se perderam; use o "
            ".xlsx ou a coluna como texto"
        ), 0
    caracteres = "".join(c for c in bruto if c.isalnum())
    cnpj = normalize_cnpj(bruto) if bruto else None
    # Mesma regra da lista distribuivel: menos de 8 caracteres e lixo, nao CNPJ
    # sem os zeros a esquerda.
    if bruto and (not cnpj or not 8 <= len(caracteres) <= 14):
        return None, f"CNPJ inválido '{bruto}'", 0
    codigo = normalize_cvm_code(linha.get(colunas.codigo)) if colunas.codigo else None
    if not cnpj and not codigo:
        return None, "sem CNPJ nem código CVM", 0
    classes = resolver(cnpj, codigo) if resolver is not None else None
    if classes is None:
        if not cnpj:
            return None, "código CVM sem base de mercado para resolver", 0
        return cnpj, None, 0
    if not classes:
        alvo = f"CNPJ {bruto}" if cnpj else f"código CVM {linha.get(colunas.codigo)}"
        return None, f"{alvo} não encontrado no cadastro da CVM", 0
    if len(classes) == 1:
        return classes[0], None, 0
    if not cnpj:
        return None, f"código CVM de fundo com {len(classes)} classes", len(classes)
    return cnpj, None, len(classes)


def _eh_cabecalho(chaves: list[str]) -> bool:
    return any(_sem_unidade(c)[0] in _CNPJ + _CODIGO for c in chaves if c)


def _linhas_da_tabela(fileiras) -> tuple[list[str], list[tuple[int, dict]]]:
    """(cabecalho, linhas) de fileiras cruas: o cabecalho e a primeira das 20
    primeiras com a coluna de CNPJ - a exportacao do BI abre com titulo."""
    cabecalho: list[str] = []
    linhas = []
    for numero, valores in enumerate(fileiras, start=1):
        if not cabecalho:
            chaves = [_chave_de_coluna(str(v)) if v is not None else "" for v in valores]
            if _eh_cabecalho(chaves):
                cabecalho = chaves
            elif numero >= _LINHAS_ATE_O_CABECALHO:
                break
            continue
        if not any(v not in (None, "") and str(v).strip() for v in valores):
            continue
        linhas.append((numero, {
            coluna: (valores[i] if i < len(valores) else None)
            for i, coluna in enumerate(cabecalho) if coluna
        }))
    return [c for c in cabecalho if c], linhas


def _ler_xlsx(caminho: Path) -> tuple[list[str], list[tuple[int, dict]]]:
    """A primeira aba com cabecalho, com o numero da linha na planilha."""
    from openpyxl import load_workbook

    pasta = load_workbook(caminho, read_only=True, data_only=True)
    try:
        for aba in pasta.worksheets:
            # A exportacao do BI grava a dimensao da aba como "A1", e no modo de
            # leitura o openpyxl so le essa celula: a dimensao sai da propria aba.
            aba.reset_dimensions()
            cabecalho, linhas = _linhas_da_tabela(aba.iter_rows(values_only=True))
            if cabecalho:
                return cabecalho, linhas
        return [], []
    finally:
        pasta.close()


def _ler_csv_da_base(caminho: Path) -> tuple[list[str], list[tuple[int, dict]]]:
    """O CSV, com o cabecalho onde estiver (ver `_linhas_da_tabela`)."""
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    amostra = texto.split("\n", _LINHAS_ATE_O_CABECALHO)
    separador = ";" if sum(l.count(";") for l in amostra) >= sum(l.count(",") for l in amostra) else ","
    fileiras = [[v.strip() for v in valores] for valores in csv.reader(io.StringIO(texto), delimiter=separador)]
    return _linhas_da_tabela(fileiras)


# Formatos de planilha que o leitor nao abre: o motivo diz o que fazer.
_SEM_LEITOR = {".xls", ".xlsb", ".ods"}


def _ler_arquivo(caminho: Path) -> tuple[list[str], list[tuple[int, dict]]]:
    """(cabecalho, linhas) de um CSV ou de uma planilha."""
    if caminho.suffix.lower() == ".xlsx":
        return _ler_xlsx(caminho)
    return _ler_csv_da_base(caminho)


# O mes pelo nome da coluna, sem ambiguidade com a data da base diaria.
_SO_MES = ("mes", "competencia", "mes_ano", "ano_mes", "mes_referencia", "mes_ref", "periodo")


_FORA_DO_CADASTRO = "CNPJ não encontrado no cadastro da CVM"


def _tipo_da_recusa(motivo: str) -> str:
    """O motivo sem o valor da linha: "CNPJ 1790... nao encontrado no cadastro da
    CVM" e "data ilegivel '32/10/2026'" viram um motivo so cada."""
    sem_valor = re.sub(r"'[^']*'", "", motivo)
    sem_valor = re.sub(r"(CNPJ|código CVM) \S+ não encontrado", r"\1 não encontrado", sem_valor)
    return re.sub(r"\s+", " ", sem_valor).strip()


def _tipo_pela_pasta(relativo: Path) -> str | None:
    """"diaria" ou "mensal" pelo nome de uma das pastas, com ou sem acento."""
    for parte in relativo.parts[:-1]:
        nome = _chave_de_coluna(parte)
        if nome.startswith("diari"):
            return DIARIA
        if nome.startswith("mensa"):
            return MENSAL
    return None


def _tipo_pelo_cabecalho(cabecalho: list[str], linhas: list[tuple[int, dict]]) -> str | None:
    """Fora das pastas: o mes pelo nome decide; a coluna de data cujos valores
    sao todos meses (202607) tambem; a data, pelo nome ou pelo conteudo, e diaria."""
    nomes = {_sem_unidade(c)[0]: c for c in cabecalho}
    if set(nomes) & set(_SO_MES):
        return MENSAL
    data = next((nomes[n] for n in _DIA if n in nomes), None)
    if data is not None:
        amostra = [l.get(data) for _, l in linhas[:_AMOSTRA] if l.get(data) not in (None, "")]
        if amostra and all(_dia(v) is None and _mes(v) for v in amostra):
            return MENSAL
        return DIARIA
    if _pelo_conteudo(linhas, cabecalho, set(), _dia):
        return DIARIA
    return None


@dataclass
class ArquivoDaBase(ArquivoLido):
    """Um arquivo lido: a base, as colunas usadas e o periodo que ele traz - o que
    o diagnostico (`python -m shelf_monitor captacao-xp`) mostra."""

    tipo: str | None = None
    colunas: dict[str, str] = field(default_factory=dict)
    de: str | None = None
    ate: str | None = None

    def to_dict(self) -> dict:
        return {
            **super().to_dict(), "tipo": self.tipo, "colunas": dict(self.colunas),
            "de": self.de, "ate": self.ate,
        }


@dataclass
class BaseCaptacaoInterna:
    """As bases diaria e mensal da pasta, resolvidas para CNPJ de classe."""

    pasta: Path
    # dia -> CNPJ de classe -> captacao liquida na XP (R$)
    dias: dict[date, dict[str, float]] = field(default_factory=dict)
    # competencia (AAAAMM) -> CNPJ de classe -> captacao liquida na XP (R$)
    meses: dict[str, dict[str, float]] = field(default_factory=dict)
    arquivos: list[ArquivoDaBase] = field(default_factory=list)
    invalidas: list[LinhaInvalida] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    # CNPJ -> [(dia, captacao)], montado na primeira soma: o cruzamento soma a
    # janela de cada fundo de vitrine, e varrer a base inteira a cada um custava
    # dias x fundos por fundo.
    _por_fundo: dict[str, list[tuple[date, float]]] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def vazia(self) -> bool:
        return not self.dias and not self.meses

    @property
    def primeiro_dia(self) -> date | None:
        return min(self.dias) if self.dias else None

    @property
    def ultimo_dia(self) -> date | None:
        return max(self.dias) if self.dias else None

    def cnpjs(self) -> set[str]:
        """Os fundos com alguma linha nas bases."""
        return {
            cnpj for periodo in (*self.dias.values(), *self.meses.values()) for cnpj in periodo
        }

    def tem_mes(self, mes: str) -> bool:
        return mes in self.meses

    def faltam(self, dias: Iterable[date]) -> list[date]:
        """Os dias que a base diaria nao traz."""
        return [d for d in dias if d not in self.dias]

    def nos_dias(self, cnpjs: Iterable[str], de: date, ate: date) -> float:
        """A captacao dos fundos entre os dois dias, inclusive."""
        if self._por_fundo is None:
            self._por_fundo = {}
            for dia, fundos in self.dias.items():
                for cnpj, valor in fundos.items():
                    self._por_fundo.setdefault(cnpj, []).append((dia, valor))
        return sum(
            valor
            for cnpj in set(cnpjs)
            for dia, valor in self._por_fundo.get(cnpj, ()) if de <= dia <= ate
        )

    def no_mes(self, cnpjs: Iterable[str], mes: str) -> float:
        fundos = self.meses.get(mes) or {}
        return sum(fundos.get(cnpj, 0.0) for cnpj in set(cnpjs))

    def motivo_da_supressao(self) -> str | None:
        if not self.arquivos:
            pasta = f"{self.pasta.parent.name}/{self.pasta.name}"
            return (
                f"nenhum arquivo em {pasta}/{DIARIA} nem em {pasta}/{MENSAL} "
                f"(modelos em {pasta})"
            )
        if self.vazia:
            return "as bases de captação da XP não trouxeram nenhuma linha válida"
        return None

    def descricao(self) -> str:
        """"diária de 01/08/2026 a 09/10/2026; mensal de ago/2026 e set/2026"."""
        partes = []
        if self.dias:
            partes.append(f"diária de {data_br(self.primeiro_dia)} a {data_br(self.ultimo_dia)}")
        if self.meses:
            meses = sorted(self.meses)
            partes.append(
                f"mensal de {competencia_br(meses[0])}" if len(meses) == 1
                else f"mensal de {competencia_br(meses[0])} a {competencia_br(meses[-1])}"
            )
        return "; ".join(partes)

    def to_dict(self) -> dict:
        return {
            "pasta": str(self.pasta),
            "dias": len(self.dias),
            "primeiro_dia": self.primeiro_dia.isoformat() if self.primeiro_dia else None,
            "ultimo_dia": self.ultimo_dia.isoformat() if self.ultimo_dia else None,
            "meses": sorted(self.meses),
            "fundos": len(self.cnpjs()),
            "arquivos": [a.to_dict() for a in self.arquivos],
            "invalidas": [i.to_dict() for i in self.invalidas],
            "avisos": list(self.avisos),
        }

    @classmethod
    def ler(cls, pasta: Path, resolver: Resolvedor | None = None) -> "BaseCaptacaoInterna":
        """Todos os arquivos dentro da pasta, em qualquer subpasta, pela ordem do
        caminho - os modelos, so com o cabecalho, ficam de fora."""
        base = cls(pasta=pasta)
        if not pasta.is_dir():
            return base
        caminhos = sorted(
            (
                c for c in pasta.rglob("*")
                if c.is_file() and not c.name.startswith("~$")
                and c.suffix.lower() in {".csv", ".xlsx", *_SEM_LEITOR}
                and not (c.parent == pasta and c.name.lower().startswith("modelo_"))
            ),
            key=lambda c: str(c.relative_to(pasta)).lower(),
        )
        for caminho in caminhos:
            base._ler_um(caminho, resolver)
        if resolver is None and base.arquivos:
            base.avisos.append(
                "base de mercado ausente: os CNPJs das bases da XP não foram conferidos "
                "contra o cadastro da CVM"
            )
        return base

    def _ler_um(self, caminho: Path, resolver: Resolvedor | None) -> None:
        relativo = caminho.relative_to(self.pasta)
        nome = relativo.as_posix()
        lido = ArquivoDaBase(nome=nome)
        self.arquivos.append(lido)
        if caminho.suffix.lower() in _SEM_LEITOR:
            self.invalidas.append(LinhaInvalida(
                nome, 0, f"formato {caminho.suffix.lower()} não é lido - salve como .xlsx ou .csv"
            ))
            return
        try:
            cabecalho, linhas = _ler_arquivo(caminho)
        except Exception as exc:  # noqa: BLE001 - arquivo de fora: ilegivel nao derruba a leitura
            self.invalidas.append(LinhaInvalida(nome, 0, f"arquivo ilegível ({exc})"))
            return
        if not cabecalho:
            self.invalidas.append(LinhaInvalida(
                nome, 0,
                f"sem cabeçalho com coluna de CNPJ ({', '.join(_CNPJ[:3])} ou "
                f"{_CODIGO[0]}) nas {_LINHAS_ATE_O_CABECALHO} primeiras linhas",
            ))
            return
        if not linhas:
            self.avisos.append(f"{nome}: sem linhas de dados")
            return
        tipo = _tipo_pela_pasta(relativo) or _tipo_pelo_cabecalho(cabecalho, linhas)
        lido.tipo = tipo
        if tipo is None:
            self.invalidas.append(LinhaInvalida(
                nome, 1, f"sem coluna de data nem de mês - ponha o arquivo em {DIARIA}/ "
                f"ou {MENSAL}/",
            ))
            return
        destino = self.dias if tipo == DIARIA else self.meses
        colunas = _colunas(cabecalho, tipo, linhas, resolver)
        if isinstance(colunas, str):
            self.invalidas.append(LinhaInvalida(nome, 1, colunas))
            return
        lido.colunas = {
            "cnpj": colunas.cnpj or colunas.codigo or "",
            "quando": colunas.quando,
            "captacao": (
                colunas.liquida[0] if colunas.liquida
                else f"{colunas.bruta[0]} - {colunas.resgate[0]}"
            ),
        }
        if colunas.quando_pelo_conteudo:
            self.avisos.append(
                f"{nome}: sem coluna com nome de {'data' if tipo == DIARIA else 'mês'} - "
                f"usada a coluna '{colunas.quando}', a primeira com "
                f"{'datas' if tipo == DIARIA else 'meses'}"
            )
        do_arquivo: dict = {}
        for numero, linha in linhas:
            lido.linhas += 1
            bruto = linha.get(colunas.quando)
            quando = (
                _dia(bruto, serial=not colunas.quando_pelo_conteudo) if tipo == DIARIA
                else _mes(bruto)
            )
            if quando is None:
                motivo = (
                    f"{'data' if tipo == DIARIA else 'mês'} ilegível "
                    f"'{linha.get(colunas.quando)}'"
                )
            else:
                cnpj, motivo, classes = _classe(linha, colunas, resolver)
                valor = _liquida(linha, colunas) if motivo is None else None
                if motivo is None and valor is None:
                    motivo = "sem valor de captação"
            if motivo:
                lido.invalidas += 1
                self.invalidas.append(LinhaInvalida(nome, numero, motivo))
                continue
            lido.validas += 1
            if classes > 1:
                self.avisos.append(
                    f"{nome}, linha {numero}: CNPJ de fundo com {classes} classes - "
                    "fica como veio, a captação não se divide entre elas"
                )
            fundos = do_arquivo.setdefault(quando, {})
            fundos[cnpj] = fundos.get(cnpj, 0.0) + valor
        repetidos = [quando for quando in do_arquivo if quando in destino]
        if repetidos:
            periodos = contagem(len(repetidos), "dia", "dias") if tipo == DIARIA else (
                contagem(len(repetidos), "mês", "meses")
            )
            self.avisos.append(
                f"{nome} repete {periodos} de arquivo anterior - valem os dele"
            )
        if lido.invalidas and not lido.validas:
            motivo, _, _ = self.recusas_por_motivo(nome)[0]
            if motivo == _FORA_DO_CADASTRO:
                self.avisos.append(
                    f"{nome}: nenhum valor da coluna '{colunas.cnpj}' está no cadastro da "
                    "CVM - ela não parece trazer o CNPJ do fundo. Use o arquivo exportado, "
                    "com o cabeçalho dele (cod_cnpj, Captação...): dado colado no modelo, "
                    "com as colunas em outra ordem, dá nisso"
                )
        destino.update(do_arquivo)
        self._por_fundo = None
        if do_arquivo:
            primeiro, ultimo = min(do_arquivo), max(do_arquivo)
            if tipo == DIARIA:
                lido.data_referencia = ultimo
                lido.de, lido.ate = data_br(primeiro), data_br(ultimo)
            else:
                lido.de, lido.ate = competencia_br(primeiro), competencia_br(ultimo)

    def recusas_por_motivo(self, arquivo: str) -> list[tuple[str, int, LinhaInvalida]]:
        """(motivo, quantas, a primeira) das recusas do arquivo, da mais comum para
        a menos - o motivo sem o valor da linha ("CNPJ ... nao encontrado")."""
        grupos: dict[str, list[LinhaInvalida]] = {}
        for recusa in self.invalidas:
            if recusa.arquivo == arquivo:
                grupos.setdefault(_tipo_da_recusa(recusa.motivo), []).append(recusa)
        return sorted(
            ((motivo, len(lista), lista[0]) for motivo, lista in grupos.items()),
            key=lambda grupo: -grupo[1],
        )

    def diagnostico(self) -> list[str]:
        """Como cada arquivo foi lido e o que as bases trazem - o que o comando
        `captacao-xp` escreve."""
        pasta = f"{self.pasta.parent.name}/{self.pasta.name}"
        if not self.arquivos:
            return [f"Nenhum arquivo em {pasta}."]
        nomes = {DIARIA: "base diária", MENSAL: "base mensal", None: "base não identificada"}
        rotulos = {"cnpj": "CNPJ", "quando": "data ou mês", "captacao": "captação"}
        linhas = [f"Bases de captação da XP em {pasta}:"]
        for arquivo in self.arquivos:
            linhas.append(
                f"- {arquivo.nome}: {nomes[arquivo.tipo]}, "
                f"{contagem(arquivo.linhas, 'linha', 'linhas')}, "
                f"{contagem(arquivo.validas, 'válida', 'válidas')}, "
                f"{contagem(arquivo.invalidas, 'recusada', 'recusadas')}"
            )
            if arquivo.colunas:
                linhas.append("  colunas: " + ", ".join(
                    f"{rotulos[chave]} '{valor}'" for chave, valor in arquivo.colunas.items()
                ))
            if arquivo.de:
                linhas.append(f"  período: {arquivo.de} a {arquivo.ate}")
            for motivo, quantas, primeira in self.recusas_por_motivo(arquivo.nome)[:3]:
                onde = f"linha {primeira.linha}: " if primeira.linha else ""
                linhas.append(
                    f"  {contagem(quantas, 'recusada', 'recusadas')} - {motivo} "
                    f"(a primeira, {onde}{primeira.motivo})"
                )
        if self.dias:
            uteis = [
                self.primeiro_dia + timedelta(days=n)
                for n in range((self.ultimo_dia - self.primeiro_dia).days + 1)
            ]
            faltam = [d for d in uteis if d.weekday() < 5 and d not in self.dias]
            linhas.append(
                f"Base diária: {contagem(len(self.dias), 'dia', 'dias')}, de "
                f"{data_br(self.primeiro_dia)} a {data_br(self.ultimo_dia)}"
                + (
                    f"; sem {contagem(len(faltam), 'dia útil', 'dias úteis')} no meio "
                    f"(feriado também aparece aqui): "
                    + ", ".join(data_br(d) for d in faltam[:8])
                    + ("..." if len(faltam) > 8 else "")
                    if faltam else ""
                )
            )
        if self.meses:
            linhas.append("Base mensal: " + ", ".join(competencia_br(m) for m in sorted(self.meses)))
        linhas.append(f"Fundos com captação: {milhar(len(self.cnpjs()))}")
        linhas += [f"Aviso: {aviso}" for aviso in self.avisos]
        return linhas


def escrever_modelos(pasta: Path) -> None:
    """Os CSVs so com o cabecalho, para a pessoa saber o formato sem ler codigo."""
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, cabecalho in (
        (f"modelo_{DIARIA}.csv", CABECALHO_DIARIA), (f"modelo_{MENSAL}.csv", CABECALHO_MENSAL),
    ):
        with (pasta / nome).open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle, delimiter=";").writerow(cabecalho)


def carregar(config, familias=None) -> BaseCaptacaoInterna:
    """As bases da pasta, resolvidas pelo mapa de familias ou, sem ele, pelo cadastro."""
    if familias is not None:
        return BaseCaptacaoInterna.ler(config.captacao_xp_dir, familias.resolver)
    from shelf_monitor.enrich import MarketLookup

    lookup = MarketLookup.load(config)
    return BaseCaptacaoInterna.ler(config.captacao_xp_dir, _resolvedor_do_cadastro(lookup))
