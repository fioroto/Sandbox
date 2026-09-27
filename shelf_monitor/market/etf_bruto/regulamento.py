"""Texto do regulamento e sugestoes com trecho, para uma pessoa conferir.

O mapeamento deixa no regulamento o que nao tem base central: taxa de
administracao e maxima, taxa do fundo investido, custos fora da taxa, indice
de referencia, publico-alvo, PMRC, politica de distribuicao e taxa de
performance. Aqui nada vira dado sozinho: o PDF baixado do FNET vira texto
(`pypdf`), regras de expressao regular acham candidatos, e cada candidato vai
para `data/etf/manual/regulamento.csv` com a pagina e o trecho em volta, uma
linha por ETF e campo. So a pessoa preenche `valor_conferido` e
`conferido_por`; o codigo nunca escreve essas colunas, e marca
`regulamento_mudou=sim` quando sai um regulamento novo depois da conferencia.

Roda sem rede: trabalha sobre os PDFs que o `fnet-documentos` ja gravou. O
texto de cada PDF fica ao lado dele (`<doc_id>.txt`, paginas separadas por
\\f), entao a extracao roda uma vez e as regras podem ser refeitas a vontade.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shelf_monitor.config import Config
from shelf_monitor.market.etf_bruto import armazem, manual
from shelf_monitor.market.etf_bruto.contexto import INDISPONIVEL, OK, PARCIAL, Contexto, ResumoFonte
from shelf_monitor.market.etf_bruto.fnet import ler_indice, pasta_regulamentos
from shelf_monitor.texto import contagem

FONTE = "regulamento"
ARQUIVO = "regulamento.csv"

CAMPOS = {
    "taxa_administracao": "Taxa de administração",
    "taxa_maxima": "Taxa máxima",
    "taxa_fundo_investido": "Taxa do fundo investido",
    "custos_extras": "Custos fora da taxa",
    "indice_referencia": "Índice de referência",
    "publico_alvo": "Público-alvo",
    "pmrc": "PMRC",
    "politica_distribuicao": "Política de distribuição",
    "taxa_performance": "Taxa de performance",
}

SISTEMA = (
    "chave", "cnpj", "ticker", "nome", "campo", "rotulo", "candidato", "outros_candidatos", "pagina",
    "trecho", "regra", "doc_id", "data_entrega_regulamento", "link", "status", "regulamento_mudou",
    manual.NO_UNIVERSO,
)
HUMANO = ("valor_conferido", "conferido_por", "conferido_em", "doc_id_conferido", "observacao")

# Menos que isso numa pagina inteira de regulamento e PDF digitalizado.
_TEXTO_MINIMO = 200
_CONTEXTO = 120


class PypdfAusente(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidato:
    campo: str
    valor: str
    pagina: int
    trecho: str
    regra: str


# --- texto ------------------------------------------------------------------------


def texto_do_pdf(caminho: Path) -> list[str]:
    """Texto de cada pagina. pypdf e opcional: sem ele, PypdfAusente."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PypdfAusente("pypdf não instalado (pip install pypdf); os PDFs seguem sendo baixados") from exc
    leitor = PdfReader(str(caminho))
    paginas = []
    for pagina in leitor.pages:
        try:
            paginas.append(pagina.extract_text() or "")
        except Exception:  # noqa: BLE001 - uma pagina ilegivel nao perde o resto do documento
            paginas.append("")
    return paginas


def texto_em_cache(pdf: Path) -> list[str]:
    txt = pdf.with_suffix(".txt")
    if txt.exists():
        return txt.read_text(encoding="utf-8").split("\f")
    paginas = texto_do_pdf(pdf)
    armazem.gravar_atomico(txt, "\f".join(paginas).encode("utf-8"))
    return paginas


def limpar(texto: str) -> str:
    """Religa palavra quebrada no fim da linha e colapsa espacos."""
    texto = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", texto or "")
    return re.sub(r"\s+", " ", texto).strip()


# --- regras -----------------------------------------------------------------------

# O pypdf quebra palavra com espaco no meio: no regulamento do LTBX11 (medido
# em 2026-09-24) saiu "T AXA GLOBAL Valor da Taxa: 0,14%", "Coti stas",
# "E STRATEGIA". As palavras-ancora das regras aceitam um espaco entre letras.
_VARIANTES = {"a": "[aáâã]", "c": "[cç]", "e": "[eéê]", "i": "[ií]", "o": "[oóôõ]", "u": "[uú]"}


def _solto(palavra: str) -> str:
    return r"\s?".join(_VARIANTES.get(letra, re.escape(letra)) for letra in palavra)


_TAXA = _solto("taxa")
_ADM = rf"(?:{_solto('global')}|(?:de\s+)?{_solto('administracao')})"
_MAXIMA = _solto("maxima")
_PCT = r"(\d{1,2}(?:\s?[.,]\s?\d{1,4})?)\s?%"


def _pct(valor: str) -> str:
    return re.sub(r"\s", "", valor).replace(".", ",") + "%"


def _grupo_pct(m: re.Match, texto: str) -> str | None:
    return _pct(m.group(1))


def _primeiro_pct(m: re.Match, texto: str) -> str | None:
    achado = re.search(_PCT, m.group(0))
    return _pct(achado.group(1)) if achado else None


def _consultoria(m: re.Match, texto: str) -> str | None:
    achado = re.search(_PCT, m.group(0))
    return "consultoria" + (f" {_pct(achado.group(1))}" if achado else "")


def _minimo_mensal(m: re.Match, texto: str) -> str | None:
    return f"mínimo mensal {m.group(1).strip()}"


def _fixo(valor: str) -> Callable[[re.Match, str], str]:
    return lambda m, texto: valor


_CONECTIVOS = re.compile(r"^(?:\s*(?:da\s+classe|do\s+fundo|de\s+refer[êe]ncia|(?:[ée]|ser[áa]|o|a)(?=\s)|:|-|–|—)\s*)+", re.I)


def _indice(m: re.Match, texto: str) -> str | None:
    depois = texto[m.end(): m.end() + 200]
    frase = re.split(r"[.;]\s|\n", depois, maxsplit=1)[0]
    frase = _CONECTIVOS.sub("", frase).strip(" \"'“”().;:")
    return frase[:120] if len(frase) >= 3 else None


def _indice_objetivo(m: re.Match, texto: str) -> str | None:
    return m.group(1).strip(" \"'“”()")[:120] or None


_INTRODUZ_NOME = re.compile(r"\b(?:[Oo]|[Aa]|[Dd]o|[Dd]a|[Pp]elo|[Pp]ela|[ÍíIi]ndice)\s+(?=[A-Z0-9“\"])")
_ARTIGO_E_INDICE = re.compile(r"^(?:(?:[OoAa]|[Oo]s|[Aa]s)\s+)?(?:[ÍíIi]ndice\s+)?")


def _indice_secao(m: re.Match, texto: str) -> str | None:
    """"3.1. INDICE DE REFERENCIA O indice Bovespa - Ibovespa, registrado..." -> "Bovespa - Ibovespa".

    Os regulamentos da Trend e da BlackRock (medido em 2026-09-24) abrem a secao
    com o nome do indice, ate o primeiro parentese, virgula ou ponto.
    """
    nome = _ARTIGO_E_INDICE.sub("", m.group(1).strip()).strip(" -–\"'“”")
    return nome[:120] if len(nome) >= 3 else None


def _indice_definido(m: re.Match, texto: str) -> str | None:
    """O nome que o regulamento define como "Indice": `LBMA Gold Price ("Indice")`.

    Do comeco da frase ate o parentese, cortado na ultima palavra que introduz
    o nome ("refletir o LBMA...", "do indice NASDAQ 100").
    """
    frase = re.split(r"[.;:]\s", texto[max(0, m.start() - 160): m.start()])[-1]
    cortes = list(_INTRODUZ_NOME.finditer(frase))
    if cortes:
        frase = frase[cortes[-1].end():]
    frase = frase.strip(" ,\"'“”(")
    return frase[-120:] if len(frase) >= 3 else None


def _publico(m: re.Match, texto: str) -> str | None:
    trecho = m.group(0).lower()
    if "profission" in trecho:
        return "profissionais"
    if "qualificad" in trecho:
        return "qualificados"
    if "em geral" in trecho:
        return "em geral"
    return "ver trecho"


def _pmrc(m: re.Match, texto: str) -> str | None:
    depois = texto[m.start(): m.end() + 200]
    achado = re.search(r"(\d{1,5}(?:[.,]\d+)?)\s*(dias|anos|meses)", depois, re.I)
    return f"{achado.group(1)} {achado.group(2).lower()}" if achado else "ver trecho"


def _distribui(m: re.Match, texto: str) -> str | None:
    """"distribuicao de rendimentos" negada logo antes ("nao havendo distribuicao
    de rendimentos") e o contrario: fica para a regra de reinvestimento."""
    antes = texto[max(0, m.start() - 40): m.start()].lower()
    if re.search(r"n[ãa]o\s+(?:havendo|haver[áa]|h[áa]|ser[áa]|ocorrer[áa])\b", antes):
        return None
    return "distribui"


def _performance(m: re.Match, texto: str) -> str | None:
    antes = texto[max(0, m.start() - 80): m.start()].lower()
    trecho = m.group(0).lower()
    if re.search(r"n[ãa]o\s+(?:haver[áa]|h[áa]|ser[áa]|ser[ãa]o|cobra|incidir[áa])|isent|sem\s+taxa", antes + " " + trecho[:160]):
        return "não cobra"
    achado = re.search(_PCT, trecho)
    return _pct(achado.group(1)) if achado else "ver trecho"


_I = re.I | re.S
# (campo, nome da regra, prioridade, padrao, valor). Dentro de um campo, sai
# primeiro o candidato da regra de menor prioridade e, empatado, o que aparece
# antes no documento.
REGRAS: tuple[tuple[str, str, int, re.Pattern, Callable[[re.Match, str], str | None]], ...] = (
    ("taxa_administracao", "taxa de administração ... %", 0,
     re.compile(rf"\b{_TAXA}\s+{_ADM}\b(?!\s+(?:{_MAXIMA}|{_solto('global')}))[^%]{{0,300}}?{_PCT}", _I), _grupo_pct),
    ("taxa_maxima", "taxa (de administração) máxima ... %", 0,
     re.compile(rf"\b{_TAXA}\s+(?:{_ADM}\s+)?{_MAXIMA}\b[^%]{{0,300}}?{_PCT}", _I), _grupo_pct),
    ("taxa_fundo_investido", "fundos/classes investidos ... %", 0,
     re.compile(rf"\b(?:fundos?|classes?|ETFs?)\s+(?:de\s+[íi]ndice\s+)?{_solto('investid')}[oa]s?\b[^%]{{0,300}}?{_PCT}",
                _I), _grupo_pct),
    ("custos_extras", "consultoria", 0,
     re.compile(rf"\b{_solto('consultor')}(?:ia)?\b[^.]{{0,200}}", _I), _consultoria),
    ("custos_extras", "mínimo mensal R$", 0,
     re.compile(r"\b(?:valor|remunera[çc][ãa]o|taxa)\s+m[íi]nim[oa]\s+mensal\b[^.]{0,200}?(R\$\s*[\d.,]+(?:\s*mil)?)", _I),
     _minimo_mensal),
    ("custos_extras", "banco liquidante", 0,
     re.compile(r"\bbanco\s+liquidante\b[^.]{0,200}", _I), _fixo("banco liquidante")),
    # A secao "INDICE DE REFERENCIA" (titulo em maiusculas, com acento, sem
    # re.I) e o indice definido entre parenteses - `Ibovespa ("Indice")` - batem mais que
    # qualquer mencao solta a "indice de referencia" (em 2026-09-24 ela aparecia
    # em clausula de derivativo).
    ("indice_referencia", "seção ÍNDICE DE REFERÊNCIA", 0,
     re.compile(r"\b[ÍI]\s?N\s?D\s?I\s?C\s?E\s+D\s?E\s+R\s?E\s?F\s?E\s?R\s?[ÊE]\s?N\s?C\s?I\s?A\b\s*[:\-–]?\s*([^(,.;]{3,160})"),
     _indice_secao),
    ("indice_referencia", "nome definido como (\"Índice\")", 1,
     re.compile(r"\(\s*[“\"]\s*" + _solto("indice") + r"\s*[”\"]\s*\)", _I), _indice_definido),
    ("indice_referencia", "objetivo ... refletir o índice", 2,
     re.compile(r"\bobjetivo\b[^.]{0,200}?\b(?:refletir|acompanhar|replicar)\b[^.]{0,120}?\b[íi]ndice\s+([^.;,]{3,100})", _I),
     _indice_objetivo),
    ("indice_referencia", "índice de referência é ...", 3,
     re.compile(rf"\b{_solto('indice')}\s+de\s+{_solto('referencia')}\b"
                r"(?=\s*(?:da\s+classe|do\s+fundo)?\s*(?:(?:[ée]|ser[áa])\s|:|-\s|–))",
                _I), _indice),
    ("publico_alvo", "público-alvo", 0,
     re.compile(rf"\b{_solto('publico')}[\s-]+{_solto('alvo')}\b[^.]{{0,250}}", _I), _publico),
    ("pmrc", "PMRC / prazo médio", 0,
     re.compile(r"\bPMRC\b|\bprazo\s+m[ée]dio\s+(?:ponderado\s+)?(?:de\s+)?(?:repactua[çc][ãa]o|vencimento|da\s+carteira)\b", _I),
     _pmrc),
    ("politica_distribuicao", "distribui rendimentos", 0,
     re.compile(r"\bdistribui[çc][ãa]o\s+(?:de\s+|dos\s+)?(?:rendimentos|dividendos|proventos)\b"
                r"|\b(?:rendimentos|dividendos|proventos)\b[^.]{0,120}?\bser[ãa]o\s+distribu[íi]dos\b"
                r"|\bdistribuir[áa]\b[^.]{0,60}?\b(?:rendimentos|dividendos|proventos)\b", _I),
     _distribui),
    ("politica_distribuicao", "reinveste", 0,
     re.compile(r"\breinvestid[oa]s?\b|\breinvestimento\b|\bincorporad[oa]s?\s+ao\s+patrim[ôo]nio\b"
                r"|\bn[ãa]o\s+(?:haver[áa]|ser[áa]|ser[ãa]o)\s+distribu", _I),
     _fixo("reinveste")),
    ("taxa_performance", "taxa de performance", 0,
     re.compile(rf"\b{_TAXA}\s+de\s+{_solto('performance')}\b[^.]{{0,250}}", _I), _performance),
)


def candidatos(paginas: list[str]) -> dict[str, list[Candidato]]:
    """Candidatos por campo: pela prioridade da regra, depois pela ordem no documento."""
    achados: dict[str, list[tuple[int, int, int, Candidato]]] = {campo: [] for campo in CAMPOS}
    for numero, pagina in enumerate(paginas, start=1):
        texto = limpar(pagina)
        for campo, nome, prioridade, regra, valor_de in REGRAS:
            for m in regra.finditer(texto):
                valor = valor_de(m, texto)
                if not valor:
                    continue
                trecho = texto[max(0, m.start() - _CONTEXTO): m.end() + _CONTEXTO].strip()
                achados[campo].append((prioridade, numero, m.start(), Candidato(campo, valor, numero, trecho, nome)))
    return {campo: [c for *_, c in sorted(lista, key=lambda item: item[:3])] for campo, lista in achados.items()}


# --- planilha de conferencia ---------------------------------------------------------


def regulamentos_por_cnpj(config: Config) -> dict[str, list[tuple[Path, dict]]]:
    """CNPJ -> [(PDF, linha do indice)], do mais recente ao mais antigo."""
    indice = {linha.get("doc_id"): linha for linha in ler_indice(config)}
    pasta = pasta_regulamentos(config)
    achados: dict[str, list[tuple[Path, dict]]] = {}
    if not pasta.exists():
        return achados
    for pdf in pasta.glob("*/*.pdf"):
        if pdf.parent.name.isdigit():
            achados.setdefault(pdf.parent.name, []).append((pdf, {**indice.get(pdf.stem, {}), "doc_id": pdf.stem}))
    for lista in achados.values():
        lista.sort(key=lambda item: (item[1].get("data_entrega", ""), int(item[0].stem) if item[0].stem.isdigit() else 0),
                   reverse=True)
    return achados


def _conferidos(config: Config) -> dict[tuple[str, str], dict]:
    return {
        ((linha.get("chave") or "").strip(), (linha.get("campo") or "").strip()): linha
        for linha in manual.ler(config, ARQUIVO)
    }


def _candidatos_do_etf(ctx: Contexto, documentos: list[tuple[Path, dict]]) -> tuple[str, list[tuple[dict, dict]]]:
    """(status, [(documento, candidatos por campo)]) dos regulamentos do ETF, do mais novo ao mais antigo."""
    lidos: list[tuple[dict, dict]] = []
    status = "sem regulamento" if not documentos else "sem texto (PDF digitalizado?)"
    for pdf, doc in documentos:
        try:
            paginas = texto_em_cache(pdf)
        except PypdfAusente:
            raise
        except Exception as exc:  # noqa: BLE001 - PDF corrompido nao para os outros
            ctx.manifesto.ocorrencia(FONTE, pdf.name, f"PDF ilegível ({type(exc).__name__})")
            continue
        if sum(len(p) for p in paginas) < _TEXTO_MINIMO:
            continue
        status = "texto"
        lidos.append((doc, candidatos(paginas)))
    return status, lidos


def extrair(ctx: Contexto) -> ResumoFonte:
    """Texto dos regulamentos em disco e a planilha de conferencia refeita.

    Com mais de um regulamento do ETF em disco, cada campo vem do mais novo que
    tem candidato para ele: em 2026-09-24 o regulamento de 05/2026 do GPUS11 era
    so a parte geral (10 paginas, sem taxa), e a taxa estava no de 05/2025.
    """
    config = ctx.config
    regulamentos = regulamentos_por_cnpj(config)
    conferidos = _conferidos(config)
    linhas: list[dict] = []
    com_texto = sem_texto = com_candidato = 0
    for etf in ctx.universo:
        cnpj = etf.get("cnpj") or ""
        base = {"chave": etf.get("chave", ""), "cnpj": cnpj, "ticker": etf.get("ticker", ""), "nome": etf.get("nome", "")}
        documentos = regulamentos.get(cnpj, [])
        try:
            status, lidos = _candidatos_do_etf(ctx, documentos)
        except PypdfAusente as exc:
            return ResumoFonte(INDISPONIVEL, str(exc))
        com_texto += status == "texto"
        sem_texto += status.startswith("sem texto")
        mais_novo = documentos[0][1] if documentos else {}
        tem_candidato = False
        for campo, rotulo in CAMPOS.items():
            doc, lista = mais_novo, []
            for doc_lido, por_campo in lidos:
                if por_campo.get(campo):
                    doc, lista = doc_lido, por_campo[campo]
                    break
            primeiro = lista[0] if lista else None
            outros = []
            for candidato in lista[1:]:
                if candidato.valor != (primeiro.valor if primeiro else None) and candidato.valor not in outros:
                    outros.append(candidato.valor)
            conferido = conferidos.get((base["chave"], campo), {})
            doc_conferido = (conferido.get("doc_id_conferido") or "").strip()
            linha_status = ("com candidato" if primeiro else "sem candidato") if status == "texto" else status
            tem_candidato = tem_candidato or primeiro is not None
            linhas.append({
                **base, "campo": campo, "rotulo": rotulo,
                "candidato": primeiro.valor if primeiro else "",
                "outros_candidatos": " | ".join(outros[:3]),
                "pagina": str(primeiro.pagina) if primeiro else "",
                "trecho": primeiro.trecho if primeiro else "",
                "regra": primeiro.regra if primeiro else "",
                "doc_id": doc.get("doc_id", ""),
                "data_entrega_regulamento": doc.get("data_entrega", ""),
                "link": doc.get("link", ""),
                "status": linha_status,
                # Saiu regulamento depois do que a pessoa conferiu: vale olhar de novo.
                "regulamento_mudou": "sim" if doc_conferido and mais_novo.get("doc_id")
                and doc_conferido != mais_novo.get("doc_id") else "",
            })
        com_candidato += tem_candidato

    resultado = manual.atualizar_planilha(manual.caminho(config, ARQUIVO), SISTEMA, HUMANO, linhas,
                                          chaves=("chave", "campo"))
    detalhe = (f"{contagem(sum(len(d) for d in regulamentos.values()), 'regulamento em disco', 'regulamentos em disco')} "
               f"de {contagem(len(regulamentos), 'CNPJ', 'CNPJs')}; texto de {com_texto} ETFs, "
               f"{contagem(com_candidato, 'ETF com candidato', 'ETFs com candidato')}")
    if sem_texto:
        detalhe += f", {sem_texto} sem texto"
    detalhe += f"; {resultado.texto()}"
    if resultado.travada:
        return ResumoFonte(PARCIAL, detalhe)
    return ResumoFonte(OK, detalhe, arquivos=1)
