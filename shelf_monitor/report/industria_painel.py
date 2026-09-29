"""O painel interativo da industria: `data/reports/industria_dashboard.html`.

Pagina unica e autocontida, como o dashboard das vitrines: os dados vao num
`<script type="application/json" id="industria-data">` e o codigo
(`graficos.js` + `industria.js`) numa unica tag de script - exatamente duas
tags, nenhuma com `src`. Tema claro e o padrao executivo de
`docs/linguagem-visual.md`, sem a marca de nenhuma casa na barra: o monitor e
neutro, e o amarelo marca so o que o leitor selecionou.

**Filtros no navegador.** Publico, macroclasse, papel na estrutura e tipo de
gestora recalculam os destaques, as estrategias, o canal e o explorador a
partir de um cubo compacto (peer group x publico x papel x tipo de gestora,
com PL, captacao e abertura por janela, contas e as fatias de conta e ordem e
de PF/PJ), montado aqui com as mesmas funcoes do bloco. O que nao sai do cubo
(funil, dono do dinheiro, mapa de calor, gestoras, familias) diz no subtitulo
qual filtro vale ali.

**Tamanho.** O explorador leva as ~8,4 mil linhas do universo em colunas, com
dicionario para os textos que se repetem e o nome encurtado ("FIC FIM" em vez
de "FUNDO DE INVESTIMENTO EM COTAS..."). Dinheiro em milhares de reais,
fracoes com 4 casas.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.industria.cadastro import ROTULOS_PUBLICO
from shelf_monitor.industria.cotistas import PF_PJ
from shelf_monitor.industria.curadoria import TIPOS_DE_GESTORA
from shelf_monitor.industria.estrutura import PAPEIS
from shelf_monitor.industria.fluxos import fluxos_por, segmentos_do_mes
from shelf_monitor.industria.janelas import ORDEM
from shelf_monitor.industria.taxonomia import N1_ORDEM
from shelf_monitor.report.dashboard import (
    _FONTES_WEB,
    _GRAFICOS,
    _STYLE,
    _e,
    _embed_json,
    _painel,
)
from shelf_monitor.report.shared.fontes import fontes_da_industria
from shelf_monitor.report.shared.numeros import pct, pp, reais, reais_com_sinal
from shelf_monitor.texto import contagem, data_br, periodo_br

_ASSETS = Path(__file__).parent / "assets"
_ESTILO_INDUSTRIA = (_ASSETS / "industria.css").read_text(encoding="utf-8")
_SCRIPT_INDUSTRIA = (_ASSETS / "industria.js").read_text(encoding="utf-8")

TITULO = "Monitor da indústria de fundos"
ARQUIVO = "industria_dashboard.html"

# Trechos longos e repetidos dos nomes da CVM, trocados pela forma curta que o
# mercado usa. A ordem importa: o mais longo primeiro.
_ENCURTAR = (
    (r"FUNDO DE INVESTIMENTO EM COTAS DE FUNDOS DE INVESTIMENTO", "FIC FI"),
    (r"FUNDO DE INVESTIMENTO FINANCEIRO EM COTAS DE FUNDOS DE INVESTIMENTO", "FIC FIF"),
    (r"FUNDO DE INVESTIMENTO EM COTAS DE FIF", "FIC FIF"),
    (r"FUNDO DE INVESTIMENTO FINANCEIRO", "FIF"),
    (r"FUNDO DE INVESTIMENTO EM COTAS", "FIC"),
    (r"FUNDO DE INVESTIMENTO", "FI"),
    (r"CLASSE DE INVESTIMENTO EM COTAS", "CIC"),
    (r"CLASSE DE INVESTIMENTO", "CI"),
    (r"RESPONSABILIDADE LIMITADA", ""),
    (r"RESP\.? ?LIMITADA", ""),
    (r"RESP\.? ?LTDA", ""),
    (r"CR[EÉ]DITO PRIVADO", "CP"),
    (r"CR[EÉ]D\.? PRIV\.?", "CP"),
    (r"RENDA FIXA", "RF"),
    (r"MULTIMERCADO", "MM"),
    (r"LONGO PRAZO", "LP"),
    (r"REFERENCIADO", "REF"),
    (r"INVESTIMENTO NO EXTERIOR", "IE"),
    (r"INFRAESTRUTURA", "INFRA"),
)
_ENCURTAR_RE = [(re.compile(rf"\b{p}\b", re.I), s) for p, s in _ENCURTAR]
# O sufixo juridico, em ~1,3 mil nomes do cadastro de 09/2026 que as trocas
# acima nao pegam: abreviado ("RESP LIM"), com erro de digitacao
# ("RESPONSANILIDADE LIMITADA"), colado na palavra anterior
# ("ACOESRESPONSABILIDADE LIMITADA") ou cortado pelo limite de 100
# caracteres da CVM ("RESPONSABILI", "RESPONSABILIDADE LI", "RL"). A palavra
# sozinha so sai no fim e so comecando por RESPONSAB: "INVESTIMENTO
# RESPONSAVEL" fica.
_SUFIXO_JURIDICO = (
    re.compile(r"\s*[-–]?\s*R?RES?P\w*\.?\s+(?:I?LIM\w*|LTDA\b)\.?", re.I),
    re.compile(r"\s*[-–]?\s*\b(?:RESPONSAB\w*|RESP\.?)(?:\s+L\w{0,6})?\.?\s*$", re.I),
    re.compile(r"\s*[-–]?\s*\bRL\b\.?(?=\s*[-–]|\s*$)", re.I),
)


def nome_curto(nome: str | None) -> str:
    """"XP MACRO FUNDO DE INVESTIMENTO EM COTAS ... RESPONSABILIDADE LIMITADA" -> "XP MACRO FIC FI"."""
    # O cadastro traz espaco duplo ("RESPONSABILIDADE  LIMITADA").
    texto = re.sub(r"\s+", " ", nome or "").strip()
    for padrao, troca in _ENCURTAR_RE:
        texto = padrao.sub(troca, texto)
    for padrao in _SUFIXO_JURIDICO:
        texto = padrao.sub("", texto)
    # O sufixo que sumiu deixa hifen ou ponto solto ("XP MACRO FIC FI -").
    texto = re.sub(r"\s*[-–.]\s*(?=[-–]|$)", "", texto)
    return re.sub(r"\s{2,}", " ", texto).strip(" -–")


def _mil(valor: float | None) -> int | None:
    return None if valor is None else round(valor / 1e3)


def _f(valor: float | None, casas: int = 4) -> float | None:
    return None if valor is None else round(valor, casas)


class _Dicionario:
    """Texto repetido vira indice; o JS le `dic[campo][indice]`."""

    def __init__(self) -> None:
        self.valores: dict[str, list[str]] = {}
        self._indices: dict[str, dict[str, int]] = {}

    def __call__(self, campo: str, valor: str | None) -> int:
        valor = valor or ""
        indices = self._indices.setdefault(campo, {})
        if valor not in indices:
            indices[valor] = len(self.valores.setdefault(campo, []))
            self.valores[campo].append(valor)
        return indices[valor]


# --- o cubo dos filtros ------------------------------------------------------------

DIMENSOES_DO_CUBO = ("n1", "n2", "publico", "papel", "tipo_gestora")
MEDIDAS_DO_CUBO = (
    "pl",
    *(f"pl_abertura_{j}" for j in ORDEM),
    *(f"fluxo_{j}" for j in ORDEM),
    "cotistas", "produtos",
    "pl_perfil", "pl_conta_e_ordem", "pl_pf_pj",
    "pl_perfil_12m_antes", "pl_conta_e_ordem_12m_antes",
)
_CONTAGENS = {"cotistas", "produtos"}


def _cubo(bloco: dict, dic: _Dicionario) -> dict:
    """Uma celula por combinacao de peer group, publico, papel e tipo de gestora.

    `pl` e o PL do investidor no fim da competencia; `pl_abertura_<janela>`, o
    da base de cada janela; `fluxo_<janela>`, o fluxo do investidor. As fatias
    de canal saem do perfil mensal: `pl_perfil` e o PL bruto das classes com
    perfil no mes, e as fatias sao `pl_conta_e_ordem / pl_perfil` - a mesma
    conta de `bloco["plataformizacao"]`.
    """
    base, jan = bloco["_base"], bloco["_janelas"]

    def chave(linha, d):
        return (d.n1, d.n2, d.publico, d.papel, d.tipo_gestora)

    por_janela = {j: fluxos_por(base, jan[j], chave) for j in ORDEM}
    agora = segmentos_do_mes(base, base.competencia, chave)
    antes = segmentos_do_mes(base, jan["12m"].base, chave)
    grupos: set = set(agora) | set(antes)
    for fluxos in por_janela.values():
        grupos |= set(fluxos)

    linhas = []
    for grupo in sorted(grupos):
        um = por_janela["1m"].get(grupo)
        seg, seg_antes = agora.get(grupo, {}), antes.get(grupo, {})
        medidas = {
            "pl": um.pl_investidor_fim if um else 0.0,
            **{f"pl_abertura_{j}": por_janela[j][grupo].pl_investidor_abertura
               if grupo in por_janela[j] else 0.0 for j in ORDEM},
            **{f"fluxo_{j}": por_janela[j][grupo].fluxo_investidor
               if grupo in por_janela[j] else 0.0 for j in ORDEM},
            "cotistas": um.cotistas_fim if um else 0,
            "produtos": um.produtos_fim if um else 0,
            "pl_perfil": sum(seg.values()),
            "pl_conta_e_ordem": seg.get("conta_e_ordem", 0.0),
            "pl_pf_pj": sum(seg.get(k, 0.0) for k in PF_PJ),
            "pl_perfil_12m_antes": sum(seg_antes.values()),
            "pl_conta_e_ordem_12m_antes": seg_antes.get("conta_e_ordem", 0.0),
        }
        linhas.append([
            *(dic(campo, valor) for campo, valor in zip(DIMENSOES_DO_CUBO, grupo)),
            *(medidas[m] if m in _CONTAGENS else _mil(medidas[m]) for m in MEDIDAS_DO_CUBO),
        ])
    return {
        "dimensoes": list(DIMENSOES_DO_CUBO),
        "medidas": list(MEDIDAS_DO_CUBO),
        "em_mil": [m for m in MEDIDAS_DO_CUBO if m not in _CONTAGENS],
        "linhas": linhas,
    }


# --- o explorador -------------------------------------------------------------------

# So o que a pagina mostra, filtra ou busca; a linha inteira (fluxo por
# janela, retorno de 3 meses, fatia de fundos, PF/PJ) esta na planilha.
_CAMPOS_EXPLORADOR = (
    "cnpj", "subclasse", "nome", "n1", "n2", "papel", "raiz_nome", "gestora", "tipo_gestora",
    "casa_dedicada", "publico", "faixa_liquidez", "taxa_adm", "pl_investidor", "cotistas",
    "fluxo_12m", "sobre_pl_12m", "retorno_12m", "quartil_12m", "conta_e_ordem",
)
_TEXTO_REPETIDO = ("n1", "n2", "papel", "gestora", "tipo_gestora", "casa_dedicada", "publico",
                   "faixa_liquidez")
_EM_MIL = ("pl_investidor", "fluxo_12m")


def _nome_do_produto(linha: dict) -> str:
    """O nome da subclasse, quando o produto e uma - e o que o cliente compra."""
    if linha.get("subclasse") and linha.get("nome_subclasse"):
        return nome_curto(linha["nome_subclasse"])
    return nome_curto(linha.get("nome"))


def _explorador(bloco: dict, dic: _Dicionario) -> dict:
    linhas = []
    for linha in bloco.get("_explorador") or []:
        valores = []
        for campo in _CAMPOS_EXPLORADOR:
            valor = linha.get(campo)
            if campo == "nome":
                valores.append(_nome_do_produto(linha))
            elif campo == "raiz_nome":
                valores.append(nome_curto(valor))
            elif campo in _TEXTO_REPETIDO:
                valores.append(dic(campo, valor))
            elif campo in _EM_MIL:
                valores.append(_mil(valor))
            elif isinstance(valor, float):
                valores.append(_f(valor))
            else:
                valores.append(valor)
        linhas.append(valores)
    return {
        "colunas": list(_CAMPOS_EXPLORADOR),
        "dicionario": list(_TEXTO_REPETIDO),
        "em_mil": list(_EM_MIL),
        "linhas": linhas,
    }


def _com_nome_curto(itens: list[dict]) -> list[dict]:
    return [{**i, "nome": nome_curto(i.get("nome"))} for i in itens]


def _prazo_da_janela(janela: dict) -> str:
    """"12 meses, de 01/09/2025 a 31/08/2026; base no fechamento de 29/08/2025"."""
    if not janela.get("de"):
        return ""
    texto = f"{janela['rotulo']}, {periodo_br(janela['de'], janela['ate'])}"
    if janela.get("data_base"):
        texto += f"; base no fechamento de {data_br(janela['data_base'])}"
    return texto


def _rentabilidade(item: dict) -> dict:
    doze = (item.get("rentabilidade") or {}).get("12m") or {}
    tres = (item.get("rentabilidade") or {}).get("3m") or {}
    return {
        "n1": item["n1"], "n2": item["n2"],
        "estrategias": doze.get("estrategias"),
        "amostra_pequena": doze.get("amostra_pequena"),
        "q1_12m": doze.get("q1"), "mediana_12m": doze.get("mediana"), "q3_12m": doze.get("q3"),
        "mediana_3m": tres.get("mediana"),
    }


def dados_do_painel(bloco: dict, gerado: datetime | None = None) -> dict:
    """O JSON que a pagina embute: o bloco sem as chaves privadas, o cubo e o
    explorador compactos, e os rotulos que o JS nao deve inventar."""
    gerado = gerado or datetime.now()
    dic = _Dicionario()
    cubo = _cubo(bloco, dic)
    explorador = _explorador(bloco, dic)
    estrategias = bloco["estrategias"]
    familias = bloco["familias"]
    estrutura = bloco["estrutura"]
    return {
        "meta": {
            "titulo": TITULO,
            "competencia": bloco["competencia"],
            "rotulo": bloco["rotulo_competencia"],
            "gerado_em": gerado.strftime("%d/%m/%Y %H:%M"),
            "informe": bloco["informe"],
            "janelas": {
                j: {
                    **{k: bloco["janelas"][j][k]
                       for k in ("rotulo", "de", "ate", "data_base", "competencias", "faltando")},
                    "texto": _prazo_da_janela(bloco["janelas"][j]),
                }
                for j in ORDEM
            },
            "prazos": bloco["prazos"],
        },
        "rotulos": {
            "publico": ROTULOS_PUBLICO,
            "papel": PAPEIS,
            "tipo_gestora": TIPOS_DE_GESTORA,
            "n1_ordem": list(N1_ORDEM),
        },
        "funil": bloco["funil"],
        "camadas": bloco["camadas"],
        "segmentos": bloco["segmentos"],
        "historico": [
            {k: h[k] for k in ("competencia", "pl_investidor", "fluxo_investidor", "rendimento_investidor", "residuo")}
            for h in bloco["mercado"]["historico"]
        ],
        "universo": bloco["mercado"]["janelas"],
        "calor": estrategias.get("mapa_de_calor"),
        "classificacao": {"origem": estrategias.get("origem"), "confianca": estrategias.get("confianca")},
        "rentabilidade": [_rentabilidade(i) for i in estrategias["n2"]],
        "gestoras": {
            k: bloco["gestoras"].get(k)
            for k in ("grupos", "cobertura_curada", "grupos_no_universo", "concentracao_da_captacao")
        },
        "estrutura": {
            k: estrutura.get(k)
            for k in ("casas_dedicadas", "parcerias", "competencias_da_carteira", "pl_sem_carteira",
                      "pl_com_divergencia", "com_carteira")
        },
        "novos_dedicados": _com_nome_curto(estrutura.get("veiculos_dedicados_novos_no_mes") or []),
        "familias": {
            "janela": familias["janela"],
            "familias_no_universo": familias["familias_no_universo"],
            "que_mais_captam": _com_nome_curto(familias["que_mais_captam"]),
            "que_mais_perdem": _com_nome_curto(familias["que_mais_perdem"]),
        },
        "lancamentos": {
            j: {lado: {**v[lado], "maiores": _com_nome_curto(v[lado]["maiores"])} for lado in ("entrou", "saiu")}
            for j, v in bloco["lancamentos"].items()
        },
        "adjacentes": bloco["adjacentes"],
        "qualidade": bloco["qualidade"],
        "avisos": bloco.get("avisos") or [],
        "fontes": [
            {"nome": f.nome, "url": f.url, "uso": f.uso}
            for f in fontes_da_industria(com_carteira=bool(estrutura.get("com_carteira", True)))
        ],
        "dic": dic.valores,
        "cubo": cubo,
        "explorador": explorador,
    }


# --- o que a pagina ja traz escrito (e o que fica sem script) --------------------

_REAIS = re.compile(r"[+-]?R\$ -?\d[\d.,]* (?:tri|bi|mi|mil)\b")
_NEGRITO = re.compile(r"\*\*(.+?)\*\*")


def _texto(texto: object) -> str:
    """Escapado, `**negrito**` como <strong> e valor em reais inquebravel."""
    saida = _NEGRITO.sub(r"<strong>\1</strong>", _e(texto))
    return _REAIS.sub(lambda m: f'<span class="nowrap">{m.group(0)}</span>', saida)


def _com_sinal(fracao: float | None) -> str:
    """"+1,2%" - fluxo em fracao do PL, que tem direcao. Zero arredondado sai sem sinal."""
    if fracao is None:
        return "-"
    if abs(fracao) < 0.0005:
        return "0,0%"
    return ("+" if fracao > 0 else "") + pct(fracao)


def contas(n: int | None) -> str:
    """"23,5 mi", "345 mil", "8.700" - quantidade de contas no tamanho da manchete."""
    if not n:
        return "-"
    if n >= 1e6:
        return f"{n / 1e6:.1f} mi".replace(".", ",")
    if n >= 1e4:
        return f"{n / 1e3:.0f} mil"
    return f"{n:,}".replace(",", ".")


def ticket(valor: float | None) -> str:
    """"R$ 170 mil", "R$ 2,3 mi" - PL por conta."""
    if valor is None:
        return "-"
    if valor >= 1e6:
        return f"R$ {valor / 1e6:.1f} mi".replace(".", ",")
    return f"R$ {valor / 1e3:,.0f} mil".replace(",", ".")


def _kpi(alvo: str, rotulo: str, valor: str, nota: str, comparacao: str = "", prazo: str = "") -> str:
    """O tile de manchete, no mesmo HTML que o JS escreve quando um filtro muda."""
    extra = f'<span class="kpi-comp">{_texto(comparacao)}</span>' if comparacao else ""
    extra += f'<span class="kpi-prazo">{_texto(prazo)}</span>' if prazo else ""
    return (
        f'<a class="kpi" href="#{alvo}"><span class="kpi-label">{_texto(rotulo)}</span>'
        f'<span class="kpi-value">{_texto(valor)}</span>'
        f'<span class="kpi-note">{_texto(nota)}</span>{extra}</a>'
    )


def _manchetes(bloco: dict, dados: dict) -> str:
    """Os cinco destaques do universo inteiro; o JS reescreve com os filtros."""
    camadas = bloco["camadas"]
    universo = bloco["mercado"]["janelas"]
    plataforma = (bloco.get("plataformizacao") or {}).get("total") or {}
    janelas = dados["meta"]["janelas"]
    fim = data_br(bloco["informe"]["ate"])
    cotistas = camadas.get("cotistas") or 0
    variacao = plataforma.get("variacao_pp")
    return "\n".join((
        _kpi("onde-section", "PL disponível para PF e PJ", reais(camadas["pl_investidor"]),
             "na camada do investidor, sem o dinheiro que um fundo aplica em outro",
             prazo=f"fechamento de {fim} · {contagem(camadas['classes'], 'classe', 'classes')} com informe"),
        _kpi("para-onde-section", "Captação do investidor em 12 meses",
             reais_com_sinal(universo["12m"]["fluxo_investidor"]),
             f"{_com_sinal(universo['12m']['sobre_pl'])} do PL de abertura", prazo=janelas["12m"]["texto"]),
        _kpi("para-onde-section", f"Captação do investidor em {bloco['rotulo_competencia']}",
             reais_com_sinal(universo["1m"]["fluxo_investidor"]),
             f"{_com_sinal(universo['1m']['sobre_pl'])} do PL de abertura", prazo=janelas["1m"]["texto"]),
        _kpi("chega-section", "Via conta e ordem", pct(plataforma.get("conta_e_ordem")),
             "do PL, aplicado por plataformas em nome dos clientes",
             f"{pp(variacao)} em 12 meses" if variacao is not None else "",
             f"{fim} contra {data_br(janelas['12m']['data_base'])}"),
        _kpi("onde-section", "Contas", contas(cotistas),
             f"posições de cotistas, não CPFs · "
             f"{ticket(camadas['pl_investidor'] / cotistas if cotistas else None)} por conta",
             prazo=f"fechamento de {fim}"),
    ))


# Topico da leitura -> (painel que detalha, rotulo, prazo). "canal" e a
# comparacao do fim da competencia com 12 meses antes.
_LEITURA = {
    "tamanho": ("onde-section", "Tamanho", None),
    "captacao": ("para-onde-section", "Captação", "12m"),
    "estrategias": ("para-onde-section", "Estratégias", "12m"),
    "canal": ("chega-section", "Canal", "canal"),
    "gestoras": ("quem-ganha-section", "Gestoras", "canal"),
}


def _leituras(bloco: dict, dados: dict) -> str:
    janelas = dados["meta"]["janelas"]
    fim = data_br(bloco["informe"]["ate"])
    topicos = bloco.get("leitura_topicos") or []
    linhas = []
    for i, frase in enumerate(bloco.get("leitura") or []):
        alvo, rotulo, janela = _LEITURA.get(topicos[i] if i < len(topicos) else "", ("", "Leitura", None))
        if janela == "canal":
            prazo = f"{fim} contra {data_br(janelas['12m']['data_base'])}"
        elif janela:
            prazo = janelas[janela]["texto"]
        else:
            prazo = f"fechamento de {fim}"
        cabeca = (f'<a class="topico" href="#{alvo}">{_e(rotulo)}</a>' if alvo
                  else f'<span class="topico">{_e(rotulo)}</span>')
        linhas.append(
            f'<tr><th scope="row">{cabeca}<small class="prazo">{_texto(prazo)}</small></th>'
            f"<td>{_texto(frase)}</td></tr>"
        )
    return "\n".join(linhas)


_FILTROS = """
  <section class="filtros" id="filtros" aria-label="Filtros">
    <label>Público <select id="filtro-publico" data-campo="publico"></select></label>
    <label>Macroclasse <select id="filtro-n1" data-campo="n1"></select></label>
    <label>Papel na estrutura <select id="filtro-papel" data-campo="papel"></select></label>
    <label>Tipo de gestora <select id="filtro-tipo" data-campo="tipo_gestora"></select></label>
    <button type="button" id="filtro-limpar" disabled>Limpar filtros</button>
    <p class="filtros-nota" id="filtros-nota" aria-live="polite">Sem filtro: o universo disponível inteiro.</p>
  </section>"""

_COMO_LER = """<ul>
      <li><b>Universo disponível</b>: fundos líquidos (renda fixa, multimercado, ações, cambial) que um cliente PF ou PJ pode comprar. Saem previdência, exclusivos, masters, institucionais, não residentes e restritos; o funil mostra cada degrau com o PL que saiu.</li>
      <li><b>Camada do investidor</b>: o PL e a captação sem o dinheiro que um fundo aplica em outro, pela fatia de cotistas-fundo do perfil mensal da CVM. É o que evita contar duas vezes o FoF e o espelho.</li>
      <li><b>Master, feeder, espelho</b>: o master guarda a carteira e é detido por outros fundos; o feeder é o veículo da própria gestora; o espelho é o veículo que outra casa monta sobre a estratégia. Estratégia, gestora e retorno vêm da raiz (o master).</li>
      <li><b>Casa dedicada</b>: veículo feito para um distribuidor (espelho de uma plataforma ou banco, ou marca do distribuidor no nome).</li>
      <li><b>Conta e ordem</b>: clientes de distribuidores que aplicam em nome deles (plataformas). Private e varejo seguem a classificação do administrador na CVM.</li>
      <li><b>Captação</b> é por fundo, nunca por distribuidor: a CVM não publica quem vendeu. Toda janela termina na competência de referência e tem base no fechamento da competência anterior.</li>
      <li><b>Retorno</b>: mediana e quartis por estratégia-raiz, na cota ex-provento; não é retorno total nem líquido de impostos.</li>
      <li><b>Contas</b> são posições de cotistas: um CPF com três fundos conta três vezes.</li>
    </ul>"""


def _paineis() -> str:
    return "\n\n  ".join((
        '<p class="grupo">O mercado</p>',
        _painel("onde-section", "Onde está o dinheiro", "PL por macroclasse e peer group, e de quem é o dinheiro", """
    <div class="chart-scroll" id="chart-n1-pl"></div>
    <h3>Peer groups</h3>
    <div class="matrix-scroll" id="tabela-n2-pl"></div>
    <h3>De quem é o dinheiro</h3>
    <div class="chart-scroll" id="chart-segmentos"></div>
    <h3>Do cadastro ao universo disponível</h3>
    <div class="chart-scroll" id="chart-funil"></div>"""),
        _painel("para-onde-section", "Para onde vai o dinheiro",
                "captação do investidor por estratégia, mês a mês e por família", """
    <div class="janelas" id="seletor-janela" role="group" aria-label="Janela da captação"></div>
    <div class="chart-scroll" id="chart-n1-fluxo"></div>
    <div class="chart-scroll" id="chart-n2-fluxo"></div>
    <h3>Mês a mês</h3>
    <div class="chart-scroll" id="chart-historico"></div>
    <h3>Rotação de estratégias</h3>
    <div class="matrix-scroll" id="tabela-calor"></div>
    <h3>Estratégias que mais captam e mais perdem</h3>
    <div class="matrix-scroll" id="tabela-familias"></div>"""),
        '<p class="grupo">Competição e canal</p>',
        _painel("quem-ganha-section", "Quem ganha", "fatia do PL por tipo de gestora e por grupo gestor", """
    <div class="chart-scroll" id="chart-tipos"></div>
    <h3>Grupos gestores</h3>
    <div class="matrix-scroll" id="tabela-gestoras"></div>"""),
        _painel("chega-section", "Por onde chega", "conta e ordem, papel na estrutura e casas dedicadas", """
    <div class="chart-scroll" id="chart-plataforma"></div>
    <h3>Papel na estrutura</h3>
    <div class="matrix-scroll" id="tabela-papeis"></div>
    <h3>Casas dedicadas</h3>
    <div class="chart-scroll" id="chart-casas"></div>
    <div class="matrix-scroll" id="tabela-parcerias"></div>"""),
        _painel("desempenho-section", "Os fundos entregam?", "retorno por peer group, uma vez por estratégia-raiz", """
    <div class="chart-scroll" id="chart-rentabilidade"></div>
    <div class="matrix-scroll" id="tabela-rentabilidade"></div>"""),
        _painel("mudou-section", "O que mudou", "o que entrou e o que saiu do universo", """
    <div class="matrix-scroll" id="tabela-lancamentos"></div>"""),
        '<p class="grupo">Explorar e conferir</p>',
        _painel("explorador-section", "Explorador", "um produto do universo por linha, com busca e ordenação", """
    <p class="card-sub" id="explorador-nota"></p>
    <div class="explorador-busca">
      <input type="search" id="busca" placeholder="Buscar fundo, gestora, estratégia ou CNPJ" aria-label="Buscar no explorador">
      <span class="explorador-resumo" id="explorador-resumo" aria-live="polite"></span>
    </div>
    <div class="matrix-scroll" id="tabela-explorador"></div>
    <button type="button" class="mais" id="explorador-mais" hidden>Mostrar mais</button>"""),
        _painel("contexto-section", "Contexto e qualidade", "camadas ao lado, qualidade dos dados e metodologia", f"""
    <h3>Camadas ao lado</h3>
    <div class="matrix-scroll" id="tabela-adjacentes"></div>
    <h3>Qualidade dos dados</h3>
    <div class="matrix-scroll" id="tabela-qualidade"></div>
    <div class="coverage" id="lista-avisos"></div>
    <details class="como-ler" open><summary>Como ler</summary>{_COMO_LER}</details>"""),
        _painel("fontes-section", "Fontes", "de onde vem cada número, com link", """
    <div id="lista-fontes"></div>"""),
    ))


def _documento(bloco: dict, dados: dict) -> str:
    meta = dados["meta"]
    prazos = " · ".join(meta["prazos"])
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{html.escape(TITULO)}</title>
{_FONTES_WEB}
<style>{_STYLE}
{_ESTILO_INDUSTRIA}</style>
</head>
<body>
<div class="topbar"><div class="wrap">
  <span class="topbar-t">{html.escape(TITULO)} &middot; <b>{html.escape(meta['rotulo'])}</b></span>
</div></div>
<div class="wrap">
  <header class="page">
    <div class="kicker">Visão de mercado para a distribuição · sem casa em foco</div>
    <h1>A indústria de fundos em {html.escape(meta['rotulo'])}</h1>
    <p class="sub">O que está disponível para pessoas e empresas, para onde vai o dinheiro, quem ganha e por qual canal. Dados abertos da CVM (cadastro, informe diário, perfil mensal e carteira) &middot; gerado em {html.escape(meta['gerado_em'])}</p>
    <p class="sub prazos">{html.escape(prazos)}</p>
  </header>
{_FILTROS}
  <main>
  <section class="exec" id="visao-executiva" aria-label="Visão executiva">
    <div class="kpis" id="kpis">
{_manchetes(bloco, dados)}
    </div>
    <div class="exec-grid">
      <section class="exec-card">
        <h2>Leitura do mês</h2>
        <table class="leituras"><tbody>
{_leituras(bloco, dados)}
        </tbody></table>
      </section>
      <section class="exec-card">
        <h2>Macroclasses</h2>
        <p class="exec-nota" id="resumo-n1-nota"></p>
        <div class="matrix-scroll" id="tabela-n1-resumo"></div>
      </section>
    </div>
  </section>

  <div class="detalhes-head">
    <div>
      <h2>Detalhes sob demanda</h2>
      <p>Cada bloco abre com um clique. Os filtros do topo valem para os destaques, as macroclasses, as estratégias, o canal e o explorador.</p>
    </div>
    <button type="button" class="abrir-tudo" id="abrir-tudo" hidden>Abrir todos</button>
  </div>

  {_paineis()}
  </main>

  <footer>
    Gerado em {html.escape(meta['gerado_em'])}. Cada fonte, com o link para consulta, está na seção
    <a href="#fontes-section">Fontes</a>.
  </footer>
</div>

<script type="application/json" id="industria-data">{_embed_json(dados)}</script>
<script>{_GRAFICOS}
{_SCRIPT_INDUSTRIA}</script>
</body>
</html>
"""


def render_painel_industria(config: Config, bloco: dict, gerado: datetime | None = None) -> Path:
    """Grava o painel da competencia e devolve o caminho."""
    dados = dados_do_painel(bloco, gerado)
    destino = config.reports_dir / ARQUIVO
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(_documento(bloco, dados), encoding="utf-8")
    return destino
