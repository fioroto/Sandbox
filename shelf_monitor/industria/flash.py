"""O flash semanal: a captacao da semana por macroclasse, na camada do investidor.

Entre dois ciclos mensais o informe diario da CVM ja diz para onde o dinheiro
esta indo. O flash le a captacao diaria dos produtos do universo disponivel e
compara a semana com a media das 12 semanas anteriores - por dia util, para a
semana com feriado nao parecer fraca.

**O universo e o do ultimo ciclo mensal.** O ciclo grava o retrato do
universo (`data/cache/industria/universo.json`): cada produto com a
macroclasse, o peer group, a gestora e a fatia do PL detida por outros fundos
no perfil do mes. O flash nao refaz funil nem estrutura: usa o retrato e tira
a fatia dos fundos de cada fluxo (o mesmo rateio que `painel` usa quando falta
o perfil do mes anterior). Retrato com mais de 45 dias - o ciclo mensal nao
rodou - e o flash recusa, em vez de medir um universo velho.

**Dia incompleto fica de fora.** O informe do mes corrente e atualizado todo
dia, e os ultimos dias chegam com parte dos fundos: em 28/09/2026 a sexta
25/09 tinha 2% dos produtos. Dia com menos de 90% dos produtos de um dia
tipico sai, e o flash diz qual e com quanto. A semana do flash e a do ultimo
dia completo, mesmo parcial, desde que tenha ao menos 3 dias - a comparacao
e por dia util; com menos, vale a semana anterior. Esperar a sexta chegar
atrasava o flash de segunda-feira em dez dias.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.industria.agregados import dimensoes
from shelf_monitor.industria.base import competencia_anterior
from shelf_monitor.industria.universo import UNIVERSO
from shelf_monitor.report.shared.numeros import mediana, reais_com_sinal
from shelf_monitor.texto import competencia_br, data_br, periodo_br

ARQUIVO_DO_RETRATO = "universo.json"
IDADE_MAXIMA_DO_RETRATO_DIAS = 45
SEMANAS_DE_COMPARACAO = 12
# Dia com menos que isto dos produtos de um dia tipico ainda esta chegando.
MIN_COBERTURA_DO_DIA = 0.90
# Semana em curso com menos dias completos que isto cede a vez a anterior.
MIN_DIAS_NA_SEMANA = 3
MAIORES = 5
INTERVALO_DO_FLASH_DIAS = 7

_ARQUIVO_DO_FLASH = re.compile(r"^industria-flash-(\d{4})-(\d{2})-(\d{2})\.md$")

Leitor = Callable[[str], list]


# --- o retrato do universo, gravado pelo ciclo mensal ------------------------------


def caminho_do_retrato(config: Config) -> Path:
    return config.industria_cache_dir / ARQUIVO_DO_RETRATO


def gravar_retrato_do_universo(config: Config, bloco: dict, hoje: date | None = None) -> Path:
    """Os produtos do universo da competencia do bloco, para o flash."""
    base = bloco["_base"]
    produtos = []
    for linha in bloco["_linhas"]:
        if linha.motivo != UNIVERSO:
            continue
        dim = dimensoes(base, linha)
        produto = base.cadastro.produto(linha.classe, linha.subclasse)
        nome = ""
        if produto:
            nome = produto.nome_subclasse if linha.subclasse and produto.nome_subclasse else produto.nome
        produtos.append([
            linha.classe, linha.subclasse, dim.n1, dim.n2, round(linha.fatia_fundos, 4), nome, dim.grupo,
        ])
    produtos.sort()
    destino = caminho_do_retrato(config)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({
        "competencia": bloco["competencia"],
        "data_base": bloco["informe"]["ate"],
        "gravado_em": (hoje or date.today()).isoformat(),
        "colunas": ["cnpj", "subclasse", "n1", "n2", "fatia_fundos", "nome", "gestora"],
        "produtos": produtos,
    }, ensure_ascii=False), encoding="utf-8")
    return destino


def ler_retrato_do_universo(config: Config) -> dict | None:
    caminho = caminho_do_retrato(config)
    if not caminho.is_file():
        return None
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not dados.get("produtos") or not dados.get("data_base"):
        return None
    return dados


def ultimo_flash(config: Config) -> date | None:
    """A data do flash mais recente em data/reports, pelo nome do arquivo."""
    datas = []
    for caminho in config.reports_dir.glob("industria-flash-*.md"):
        achado = _ARQUIVO_DO_FLASH.match(caminho.name)
        if achado:
            try:
                datas.append(date(*(int(g) for g in achado.groups())))
            except ValueError:
                continue
    return max(datas) if datas else None


# --- a captacao diaria -------------------------------------------------------------


def _ler_mes(config: Config, mes: str, alvo: set[str], *, baixar: bool, corrente: bool) -> list:
    """Registros do informe diario da competencia, so dos CNPJs do universo.

    O mes corrente e baixado de novo (muda todo dia); os fechados vem do disco,
    onde os outros monitores ja os deixaram, e so sao baixados se faltarem.
    """
    from shelf_monitor.market.informe_diario import fetch_inf_diario_month
    from shelf_monitor.report.retail_flow import read_daily_flows

    local = config.market_raw_dir / f"inf_diario_fi_{mes}.zip"
    if baixar:
        caminho = fetch_inf_diario_month(config, mes, force=corrente)
    else:
        caminho = local if local.is_file() else None
    return read_daily_flows(caminho, alvo) if caminho else []


def _indisponivel(motivo: str, **extra) -> dict:
    return {"available": False, "reason": motivo, **extra}


def _segunda(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


def montar_flash(
    config: Config,
    *,
    hoje: date | None = None,
    baixar: bool = True,
    leitor: Leitor | None = None,
) -> dict:
    """O bloco do flash: a semana, a comparacao e os maiores saldos, ou o motivo."""
    hoje = hoje or date.today()
    retrato = ler_retrato_do_universo(config)
    if retrato is None:
        return _indisponivel(
            "sem o retrato do universo: rode o ciclo mensal uma vez "
            "(`python -m shelf_monitor industria --ciclo mensal`)"
        )
    data_base = date.fromisoformat(retrato["data_base"][:10])
    idade = (hoje - data_base).days
    sobre_o_retrato = {
        "competencia": retrato["competencia"],
        "data_base": data_base.isoformat(),
        "idade_dias": idade,
    }
    if idade > IDADE_MAXIMA_DO_RETRATO_DIAS:
        return _indisponivel(
            f"o universo é o do ciclo de {competencia_br(retrato['competencia'])}, com informe até "
            f"{data_br(data_base)}: {idade} dias, acima dos {IDADE_MAXIMA_DO_RETRATO_DIAS} que o flash "
            "aceita. Rode o ciclo mensal da competência seguinte",
            retrato=sobre_o_retrato,
        )

    colunas = retrato.get("colunas") or ["cnpj", "subclasse", "n1", "n2", "fatia_fundos", "nome", "gestora"]
    produtos = {
        (item["cnpj"], item["subclasse"]): item
        for item in (dict(zip(colunas, linha)) for linha in retrato["produtos"])
    }
    alvo = {cnpj for cnpj, _ in produtos}
    mes_de_hoje = f"{hoje.year:04d}{hoje.month:02d}"
    if leitor is None:
        def leitor(mes: str) -> list:
            return _ler_mes(config, mes, alvo, baixar=baixar, corrente=mes == mes_de_hoje)

    # dia -> {(cnpj, subclasse): captacao liquida declarada}. A mesma linha
    # repetida no arquivo (1% das linhas em 20/09/2026) vale a ultima.
    diario: dict[date, dict[tuple[str, str], float]] = {}
    lidos: set[str] = set()

    def carregar(mes: str) -> None:
        if mes in lidos:
            return
        lidos.add(mes)
        for registro in leitor(mes):
            chave = (registro.cnpj, registro.subclasse)
            if chave in produtos:
                diario.setdefault(registro.dia, {})[chave] = registro.captacao - registro.resgates

    carregar(mes_de_hoje)
    carregar(competencia_anterior(mes_de_hoje))
    dias = sorted(d for d in diario if d <= hoje)
    if not dias:
        return _indisponivel("sem informe diário da CVM do mês corrente nem do anterior", retrato=sobre_o_retrato)

    # Dia completo: ao menos 90% dos produtos de um dia tipico (a mediana dos
    # dias lidos, sem os cinco ultimos, que sao os que ainda estao chegando).
    contagem = {d: len(diario[d]) for d in dias}
    tipico = mediana([contagem[d] for d in (dias[:-5] or dias)]) or 0
    incompletos = []
    while dias and contagem[dias[-1]] < MIN_COBERTURA_DO_DIA * tipico:
        dia = dias.pop()
        incompletos.append({"dia": dia.isoformat(), "cobertura": round(contagem[dia] / tipico, 3)})
    incompletos.reverse()
    if not dias:
        return _indisponivel("nenhum dia completo no informe diário recente", retrato=sobre_o_retrato)
    ultimo = dias[-1]

    # A semana do flash e a do ultimo dia completo, se ja tem 3 dias completos;
    # senao, a anterior.
    segunda = _segunda(ultimo)
    if sum(1 for d in dias if segunda <= d <= ultimo) < MIN_DIAS_NA_SEMANA:
        segunda -= timedelta(days=7)
    sexta = segunda + timedelta(days=4)
    inicio = segunda - timedelta(weeks=SEMANAS_DE_COMPARACAO)
    mes = f"{inicio.year:04d}{inicio.month:02d}"
    while mes < mes_de_hoje:
        carregar(mes)
        ano, numero = int(mes[:4]), int(mes[4:])
        mes = f"{ano + 1:04d}01" if numero == 12 else f"{ano:04d}{numero + 1:02d}"

    dias_da_semana = sorted(d for d in diario if segunda <= d <= sexta and d <= ultimo)
    dias_de_comparacao = sorted(d for d in diario if inicio <= d < segunda)
    if not dias_da_semana:
        return _indisponivel(
            f"nenhum dia completo na semana {periodo_br(segunda, sexta)}", retrato=sobre_o_retrato,
        )

    def investidor(chave: tuple[str, str], bruto: float) -> float:
        return bruto * (1.0 - float(produtos[chave].get("fatia_fundos") or 0.0))

    def somar(dias_: list[date]) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
        por_n1: dict[str, float] = {}
        por_produto: dict[tuple[str, str], float] = {}
        for dia in dias_:
            for chave, bruto in diario[dia].items():
                valor = investidor(chave, bruto)
                n1 = produtos[chave]["n1"]
                por_n1[n1] = por_n1.get(n1, 0.0) + valor
                por_produto[chave] = por_produto.get(chave, 0.0) + valor
        return por_n1, por_produto

    semana_n1, semana_produto = somar(dias_da_semana)
    comparacao_n1, _ = somar(dias_de_comparacao)
    n_semana, n_comparacao = len(dias_da_semana), len(dias_de_comparacao)

    def linha(n1: str | None, semana: float, comparacao: float) -> dict:
        por_dia = semana / n_semana
        por_dia_antes = comparacao / n_comparacao if n_comparacao else None
        return {
            "n1": n1,
            "semana": round(semana, 2),
            "por_dia": round(por_dia, 2),
            "por_dia_12_semanas": None if por_dia_antes is None else round(por_dia_antes, 2),
            "diferenca_por_dia": None if por_dia_antes is None else round(por_dia - por_dia_antes, 2),
        }

    total = linha(None, sum(semana_n1.values()), sum(comparacao_n1.values()))
    por_n1 = sorted(
        (linha(n1, semana_n1.get(n1, 0.0), comparacao_n1.get(n1, 0.0))
         for n1 in set(semana_n1) | set(comparacao_n1)),
        key=lambda item: -item["semana"],
    )

    def produto(chave: tuple[str, str]) -> dict:
        item = produtos[chave]
        return {
            "cnpj": chave[0], "subclasse": chave[1], "nome": item.get("nome") or "",
            "n1": item["n1"], "n2": item.get("n2") or "", "gestora": item.get("gestora") or "",
            "fluxo": round(semana_produto[chave], 2),
        }

    ordenados = sorted(semana_produto, key=lambda c: -semana_produto[c])
    entradas = [produto(c) for c in ordenados[:MAIORES] if semana_produto[c] > 0]
    saidas = [produto(c) for c in reversed(ordenados[-MAIORES:]) if semana_produto[c] < 0]
    com_informe = {chave for dia in dias_da_semana for chave in diario[dia]}

    flash = {
        "available": True,
        "semana": {"de": dias_da_semana[0].isoformat(), "ate": dias_da_semana[-1].isoformat(),
                   "segunda": segunda.isoformat(), "sexta": sexta.isoformat(), "dias": n_semana},
        "comparacao": {
            "de": dias_de_comparacao[0].isoformat() if dias_de_comparacao else None,
            "ate": dias_de_comparacao[-1].isoformat() if dias_de_comparacao else None,
            "semanas": SEMANAS_DE_COMPARACAO, "dias": n_comparacao,
        },
        "total": total,
        "por_n1": por_n1,
        "maiores_entradas": entradas,
        "maiores_saidas": saidas,
        "ultimo_dia_completo": ultimo.isoformat(),
        "dias_incompletos": incompletos,
        "retrato": sobre_o_retrato,
        "cobertura": {"produtos_no_retrato": len(produtos), "produtos_com_informe_na_semana": len(com_informe)},
    }
    flash["prazos"] = prazos_do_flash(flash)
    flash["leitura"] = leitura_do_flash(flash)
    return flash


def prazos_do_flash(flash: dict) -> list[str]:
    semana, comparacao, retrato = flash["semana"], flash["comparacao"], flash["retrato"]
    linhas = [f"semana: {periodo_br(semana['de'], semana['ate'])} ({semana['dias']} dias úteis com informe)"]
    if comparacao["de"]:
        linhas.append(
            f"comparação: {comparacao['semanas']} semanas anteriores, "
            f"{periodo_br(comparacao['de'], comparacao['ate'])} ({comparacao['dias']} dias úteis)"
        )
    linhas.append(
        f"universo e fatia de fundos: ciclo mensal de {competencia_br(retrato['competencia'])} "
        f"(informe até {data_br(retrato['data_base'])})"
    )
    return linhas


def leitura_do_flash(flash: dict) -> list[str]:
    total, semana = flash["total"], flash["semana"]
    frases = [
        f"A captação líquida do investidor nos fundos do universo disponível foi de "
        f"**{reais_com_sinal(total['semana'])}** na semana {periodo_br(semana['de'], semana['ate'])}: "
        f"{reais_com_sinal(total['por_dia'])} por dia útil"
        + (f", contra {reais_com_sinal(total['por_dia_12_semanas'])} por dia nas 12 semanas anteriores."
           if total["por_dia_12_semanas"] is not None else ".")
    ]
    por_n1 = flash["por_n1"]
    if por_n1 and por_n1[0]["semana"] > 0 and por_n1[-1]["semana"] < 0:
        frases.append(
            f"**{por_n1[0]['n1']}** foi a macroclasse que mais captou ({reais_com_sinal(por_n1[0]['semana'])}); "
            f"**{por_n1[-1]['n1']}**, a que mais perdeu ({reais_com_sinal(por_n1[-1]['semana'])})."
        )
    acima = [i for i in por_n1 if i["diferenca_por_dia"] is not None and i["diferenca_por_dia"] > 0]
    if acima and len(acima) < len(por_n1):
        frases.append(
            f"{len(acima)} de {len(por_n1)} macroclasses captaram por dia acima da média das 12 semanas."
        )
    return frases
