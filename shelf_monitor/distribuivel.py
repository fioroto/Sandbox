"""Lista distribuivel: os fundos que a XP distribui ou tem capacidade de distribuir.

A vitrine capturada do site nao e tudo que a casa oferta: um fundo pode estar
fora da vitrine publica e ja disponivel para distribuicao (assessoria, private,
contrato assinado com a gestora). Com a lista em maos, um gap da vitrine que a
lista cobre deixa de ser "incluir" e vira "expor na vitrine" - o degrau mais
barato da escada de esforco.

A pessoa solta um ou mais CSVs em `data/distribuivel/entrada/` (fora do git:
dado comercial interno). O modelo com o cabecalho mora em
`data/distribuivel/modelo.csv`. Regras de leitura:

- separador `;` (a convencao das planilhas manuais do repositorio) ou `,`;
  UTF-8 com ou sem BOM, ou Latin-1;
- colunas por nome, sem diferenciar maiuscula nem acento: `cnpj` (ou
  `codigo_cvm`) e obrigatorio; `situacao` e `distribui` ou `pode_distribuir`
  (vazio vale `distribui`); `servico` e o servico que a XP presta ao fundo,
  `Distribuição` ou `FaaS` (Fund as a Service) - o digest o escreve ao lado de
  todo fundo da casa; `nome`, `gestora`, `data_referencia` e `observacao` sao
  opcionais;
- o fundo e resolvido pela mesma regra do enriquecimento: CNPJ da classe,
  CNPJ do fundo (que vale para todas as classes dele) ou codigo CVM;
- arquivos sao unidos; fundo repetido fica com `distribui`;
- linha que nao se resolve e listada com arquivo, linha e motivo - nunca
  descartada em silencio.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from shelf_monitor.market.parse import parse_date
from shelf_monitor.normalize import normalize_cnpj, normalize_cvm_code, normalize_text

DISTRIBUI = "distribui"
PODE_DISTRIBUIR = "pode_distribuir"
ROTULOS_SITUACAO = {DISTRIBUI: "distribui", PODE_DISTRIBUIR: "pode distribuir"}

_SITUACOES = {
    "": DISTRIBUI,
    "DISTRIBUI": DISTRIBUI,
    "DISTRIBUIMOS": DISTRIBUI,
    "PODE DISTRIBUIR": PODE_DISTRIBUIR,
    "CAPACIDADE DE DISTRIBUIR": PODE_DISTRIBUIR,
}

# O servico que a XP presta ao fundo da lista (pedido em 14/10/2026): o digest
# marca cada fundo da casa com ele.
SERVICO_DISTRIBUICAO = "distribuicao"
SERVICO_FAAS = "faas"
ROTULOS_SERVICO = {SERVICO_DISTRIBUICAO: "Distribuição", SERVICO_FAAS: "FaaS"}
# Grafias do FaaS, comparadas por trecho do texto normalizado. O plural veio
# na planilha de 16/10/2026: "FUND AS A SERVICE" nao e trecho de "FUNDS AS A
# SERVICE", e o fundo caia como servico desconhecido.
_TERMOS_FAAS = ("FAAS", "FUND AS A SERVICE", "FUNDS AS A SERVICE")

CABECALHO_MODELO = (
    "cnpj", "nome", "situacao", "servico", "gestora", "data_referencia", "observacao",
)


def _servico(texto: str | None) -> tuple[str | None, bool]:
    """(servico, reconhecido). Vazio e reconhecido, sem servico."""
    normalizado = normalize_text(texto)
    if not normalizado:
        return None, True
    if any(termo in normalizado for termo in _TERMOS_FAAS):
        return SERVICO_FAAS, True
    if normalizado.startswith("DISTRIBU"):
        return SERVICO_DISTRIBUICAO, True
    return None, False

# Resolve (cnpj, codigo_cvm) -> CNPJs de classe. None quando nao ha base para
# validar (sem mercado.csv): o CNPJ entra como veio, com aviso.
Resolvedor = Callable[[str | None, str | None], list[str] | None]


@dataclass(frozen=True)
class LinhaInvalida:
    arquivo: str
    linha: int
    motivo: str

    def to_dict(self) -> dict:
        return {"arquivo": self.arquivo, "linha": self.linha, "motivo": self.motivo}


@dataclass
class ArquivoLido:
    nome: str
    linhas: int = 0
    validas: int = 0
    invalidas: int = 0
    data_referencia: date | None = None

    def to_dict(self) -> dict:
        return {
            "nome": self.nome, "linhas": self.linhas, "validas": self.validas,
            "invalidas": self.invalidas,
            "data_referencia": (
                self.data_referencia.isoformat() if self.data_referencia else None
            ),
        }


@dataclass
class BaseDistribuivel:
    """A uniao dos CSVs da pasta, resolvida para CNPJ de classe."""

    pasta: Path
    situacao: dict[str, str] = field(default_factory=dict)
    # CNPJ de classe -> servico (SERVICO_DISTRIBUICAO ou SERVICO_FAAS), quando
    # a lista traz a coluna.
    servico: dict[str, str] = field(default_factory=dict)
    # CNPJ de classe -> nome da gestora: o do cadastro da CVM (`carregar`) ou,
    # sem ele, o da coluna `gestora` do CSV. A gestora com fundo na lista conta
    # como gestora que a XP distribui (pedido de 19/10/2026).
    gestora: dict[str, str] = field(default_factory=dict)
    arquivos: list[ArquivoLido] = field(default_factory=list)
    invalidas: list[LinhaInvalida] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def vazia(self) -> bool:
        return not self.situacao

    @property
    def data_referencia(self) -> date | None:
        datas = [a.data_referencia for a in self.arquivos if a.data_referencia]
        return max(datas) if datas else None

    def cnpjs(self) -> list[str]:
        return sorted(self.situacao)

    def situacao_de(self, cnpj: str | None) -> str | None:
        return self.situacao.get(cnpj) if cnpj else None

    def servico_de(self, cnpj: str | None) -> str | None:
        return self.servico.get(cnpj) if cnpj else None

    def rotulo_do_servico(self, cnpj: str | None) -> str | None:
        """"Distribuição" ou "FaaS"; None fora da lista ou sem a coluna."""
        return ROTULOS_SERVICO.get(self.servico_de(cnpj) or "")

    def gestoras(self) -> set[str]:
        """As gestoras com fundo na lista, normalizadas como as da vitrine."""
        return {normalize_text(g) for g in self.gestora.values() if g}

    def motivo_da_supressao(self) -> str | None:
        if not self.arquivos:
            # O caminho a partir de data/, que e o que a pessoa procura.
            pasta = f"{self.pasta.parent.name}/{self.pasta.name}"
            # A planilha colada no modelo nao e lida (16/10/2026, na maquina da
            # empresa: a lista inteira estava no modelo e a lente saia
            # suprimida) - o motivo aponta para onde mover.
            dados = _linhas_do_modelo(self.pasta.parent / "modelo.csv")
            if dados:
                linhas = "linha de dados" if dados == 1 else "linhas de dados"
                return (
                    f"nenhum CSV na pasta {pasta}: o modelo.csv tem {dados} {linhas}, "
                    f"mas só {pasta} é lida - mova o CSV para essa pasta"
                )
            return f"nenhum CSV na pasta {pasta} (modelo em {self.pasta.parent.name}/modelo.csv)"
        if self.vazia:
            return "a lista distribuível não trouxe nenhum fundo válido"
        return None

    def to_dict(self) -> dict:
        return {
            "pasta": str(self.pasta),
            "total": len(self.situacao),
            "distribui": sum(1 for s in self.situacao.values() if s == DISTRIBUI),
            "pode_distribuir": sum(
                1 for s in self.situacao.values() if s == PODE_DISTRIBUIR
            ),
            "servicos": {
                chave: sum(1 for s in self.servico.values() if s == chave)
                for chave in ROTULOS_SERVICO
            },
            "data_referencia": (
                self.data_referencia.isoformat() if self.data_referencia else None
            ),
            "arquivos": [a.to_dict() for a in self.arquivos],
            "invalidas": [i.to_dict() for i in self.invalidas],
            "avisos": list(self.avisos),
        }

    @classmethod
    def ler(cls, pasta: Path, resolver: Resolvedor | None = None) -> "BaseDistribuivel":
        base = cls(pasta=pasta)
        if not pasta.is_dir():
            return base
        sem_validacao = False
        for caminho in sorted(pasta.glob("*.csv"), key=lambda p: p.name.lower()):
            lido = ArquivoLido(nome=caminho.name)
            base.arquivos.append(lido)
            try:
                linhas = _ler_csv(caminho)
            except (OSError, UnicodeDecodeError, csv.Error) as exc:
                base.invalidas.append(LinhaInvalida(caminho.name, 0, f"arquivo ilegível ({exc})"))
                continue
            datas: list[date] = []
            for numero, linha in linhas:
                lido.linhas += 1
                motivo, classes, situacao = _resolver_linha(linha, resolver)
                if classes is None and motivo is None:
                    sem_validacao = True
                    classes = [normalize_cnpj(linha.get("cnpj"))]
                if motivo:
                    lido.invalidas += 1
                    base.invalidas.append(LinhaInvalida(caminho.name, numero, motivo))
                    continue
                lido.validas += 1
                if len(classes) > 1:
                    base.avisos.append(
                        f"{caminho.name}, linha {numero}: CNPJ de fundo com "
                        f"{len(classes)} classes - vale para todas"
                    )
                servico, reconhecido = _servico(linha.get("servico"))
                if not reconhecido:
                    base.avisos.append(
                        f"{caminho.name}, linha {numero}: serviço desconhecido "
                        f"'{linha.get('servico')}' (use Distribuição ou FaaS) - "
                        "fundo mantido, sem serviço"
                    )
                gestora = (linha.get("gestora") or "").strip()
                for cnpj in classes:
                    atual = base.situacao.get(cnpj)
                    if atual != DISTRIBUI:
                        base.situacao[cnpj] = situacao
                    if servico:
                        anterior = base.servico.setdefault(cnpj, servico)
                        if anterior != servico:
                            base.avisos.append(
                                f"{caminho.name}, linha {numero}: o fundo já veio "
                                f"como {ROTULOS_SERVICO[anterior]} - vale o primeiro"
                            )
                    if gestora:
                        base.gestora.setdefault(cnpj, gestora)
                referencia = parse_date(linha.get("data_referencia"))
                if referencia:
                    datas.append(referencia)
            lido.data_referencia = max(datas) if datas else _data_do_arquivo(caminho)
        if sem_validacao:
            base.avisos.append(
                "base de mercado ausente: os CNPJs da lista não foram conferidos "
                "contra o cadastro da CVM"
            )
        return base


def _data_do_arquivo(caminho: Path) -> date | None:
    try:
        return datetime.fromtimestamp(caminho.stat().st_mtime).date()
    except OSError:
        return None


def _linhas_do_modelo(caminho: Path) -> int:
    """Linhas de dados do modelo, sem o cabecalho; 0 se ausente ou ilegivel."""
    try:
        return len(_ler_csv(caminho))
    except (OSError, UnicodeDecodeError, csv.Error):
        return 0


def _chave_de_coluna(nome: str | None) -> str:
    return normalize_text(nome).lower().replace(" ", "_")


def _ler_csv(caminho: Path) -> list[tuple[int, dict]]:
    """Linhas do CSV com o numero da linha no arquivo (cabecalho = 1)."""
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    primeira = texto.split("\n", 1)[0]
    separador = ";" if primeira.count(";") >= primeira.count(",") else ","
    leitor = csv.reader(io.StringIO(texto), delimiter=separador)
    try:
        cabecalho = [_chave_de_coluna(c) for c in next(leitor)]
    except StopIteration:
        return []
    linhas = []
    for numero, valores in enumerate(leitor, start=2):
        if not any(v.strip() for v in valores):
            continue
        linhas.append((numero, {
            coluna: (valores[i].strip() if i < len(valores) else "")
            for i, coluna in enumerate(cabecalho)
        }))
    return linhas


def _resolver_linha(
    linha: dict, resolver: Resolvedor | None
) -> tuple[str | None, list[str] | None, str]:
    """(motivo da recusa, classes, situacao). Classes None = sem base para validar."""
    situacao = _SITUACOES.get(normalize_text(linha.get("situacao")))
    if situacao is None:
        return (
            f"situação desconhecida '{linha.get('situacao')}' "
            "(use distribui ou pode_distribuir)",
            [], DISTRIBUI,
        )
    bruto_cnpj = (linha.get("cnpj") or "").strip()
    # Letras contam: o CNPJ alfanumerico (desde 07/2026) tem 14 caracteres e
    # poucos digitos, e seria recusado como lixo se so os digitos contassem.
    caracteres = "".join(c for c in bruto_cnpj if c.isalnum())
    cnpj = normalize_cnpj(bruto_cnpj) if bruto_cnpj else None
    # Menos de 8 caracteres nao e CNPJ sem zeros a esquerda (o Excel os come), e
    # lixo; mais de 14 tambem.
    if bruto_cnpj and (not cnpj or not 8 <= len(caracteres) <= 14):
        return f"CNPJ inválido '{bruto_cnpj}'", [], situacao
    codigo = normalize_cvm_code(linha.get("codigo_cvm"))
    if not cnpj and not codigo:
        return "sem CNPJ nem código CVM", [], situacao
    if resolver is None:
        if not cnpj:
            return "código CVM sem base de mercado para resolver", [], situacao
        return None, None, situacao
    classes = resolver(cnpj, codigo)
    if classes is None:
        if not cnpj:
            return "código CVM sem base de mercado para resolver", [], situacao
        return None, None, situacao
    if not classes:
        alvo = f"CNPJ {bruto_cnpj}" if cnpj else f"código CVM {linha.get('codigo_cvm')}"
        return f"{alvo} não encontrado no cadastro da CVM", [], situacao
    return None, classes, situacao


def escrever_modelo(caminho: Path) -> None:
    """O CSV so com o cabecalho, para a pessoa saber o formato sem ler codigo."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle, delimiter=";").writerow(CABECALHO_MODELO)


def _resolvedor_do_cadastro(lookup) -> Resolvedor | None:
    """Sem o mapa de familias (carteira ausente), valida pelo cadastro da CVM."""
    if lookup is None:
        return None

    def resolver(cnpj: str | None, codigo: str | None) -> list[str]:
        registro = lookup.get(cnpj) if cnpj else None
        if registro is None and codigo:
            achado = lookup.get_by_cvm_code(codigo)
            registro = achado[0] if achado else None
        return [registro.cnpj] if registro else []

    return resolver


def carregar(config, familias=None) -> BaseDistribuivel:
    """A lista da pasta de entrada, resolvida pelo mapa ou, sem ele, pelo cadastro.

    A gestora de cada fundo vem da mesma fonte e prevalece sobre a coluna
    `gestora` do CSV, como no enriquecimento: e o nome que a vitrine tambem
    traz, e o que o bloco de gaps compara (pedido de 19/10/2026).
    """
    if familias is not None:
        base = BaseDistribuivel.ler(config.distribuivel_dir, familias.resolver)
        registros = {cnpj: familias.classe(cnpj) for cnpj in base.situacao}
    else:
        from shelf_monitor.enrich import MarketLookup

        lookup = MarketLookup.load(config)
        base = BaseDistribuivel.ler(config.distribuivel_dir, _resolvedor_do_cadastro(lookup))
        registros = (
            {cnpj: lookup.get(cnpj) for cnpj in base.situacao} if lookup is not None else {}
        )
    for cnpj, registro in registros.items():
        if registro is not None and registro.gestor:
            base.gestora[cnpj] = registro.gestor
    return base
