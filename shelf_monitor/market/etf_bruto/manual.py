"""Planilhas que gente preenche, em `data/etf/manual/`.

O mapeamento tem dado que nenhuma fonte publica entrega pronto: o que e da
casa (classe XP, risco em pontos, suitability, aceita investimento mensal), o
que esta disperso em contrato (formador de mercado, spread garantido) e o que
precisa ser conferido no regulamento (indice de referencia). Para esses, o
extrator mantem uma planilha por assunto com uma linha por ETF do universo:

- colunas de sistema (ticker, nome, gestora, sugestao por triagem do nome) sao
  refeitas a cada execucao;
- colunas humanas (as que a pessoa preenche) nunca sao escritas pelo codigo;
- ETF novo entra no fim; ETF que saiu do universo e tem algo preenchido por
  gente fica, marcado na coluna `no_universo` - o que a pessoa escreveu nunca
  e apagado (a linha sem nada humano sai);
- coluna que a pessoa acrescentar e preservada;
- planilha aberta no Excel (arquivo travado) nao derruba a execucao: fica
  como esta, com aviso.

A sugestao e triagem pelo nome (`segmento_do_etf`), nao dado: por isso vai
numa coluna propria e nunca na coluna que a pessoa confirma.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from shelf_monitor.config import Config
from shelf_monitor.market.etf_bruto import armazem
from shelf_monitor.market.etf_competicao import rotulo_da_gestora, segmento_do_etf
from shelf_monitor.normalize import normalize_text

NO_UNIVERSO = "no_universo"


@dataclass(frozen=True)
class Modelo:
    arquivo: str
    sistema: tuple[str, ...]
    humano: tuple[str, ...]
    chaves: tuple[str, ...] = ("chave",)


XP_CADASTRO = Modelo(
    "xp_cadastro.csv",
    ("chave", "cnpj", "ticker", "nome", "gestora", "tipo_b3", "listado", "segmento_triagem",
     "sugestao_classe_xp", NO_UNIVERSO),
    ("classe_xp", "risco_pontos", "elegivel_suitability", "aceita_investimento_mensal",
     "categoria_regua", "preenchido_por", "preenchido_em", "observacao"),
)
INDICES = Modelo(
    "indices.csv",
    ("chave", "cnpj", "ticker", "nome", "listado", "segmento_triagem", "sugestao_codigo_b3",
     "indice_no_regulamento", NO_UNIVERSO),
    ("indice_referencia", "codigo_b3", "provedor", "codigo_comdinheiro", "com_cambio",
     "conferido_por", "conferido_em", "observacao"),
)
FORMADOR = Modelo(
    "formador.csv",
    ("chave", "cnpj", "ticker", "nome", "gestora", "listado", NO_UNIVERSO),
    ("formador", "contratado", "spread_maximo", "ate_cotas", "fonte", "data_referencia",
     "conferido_por", "conferido_em", "observacao"),
)
MODELOS = (XP_CADASTRO, INDICES, FORMADOR)

# Planilhas so de cabecalho: a pessoa escreve as linhas, o codigo nunca reescreve.
GESTORAS_URLS = ("gestoras_urls.csv", ("ticker", "gestora", "url", "formato", "ativo", "observacao"))
COMDINHEIRO_CONSULTAS = ("comdinheiro_consultas.csv", ("nome", "url_ferramenta", "ativo", "observacao"))
SO_CABECALHO = (GESTORAS_URLS, COMDINHEIRO_CONSULTAS)


def caminho(config: Config, arquivo: str) -> Path:
    return config.etf_manual_dir / arquivo


def ler(config: Config, arquivo: str) -> list[dict]:
    return armazem.ler_csv(caminho(config, arquivo))


def ativo(linha: dict) -> bool:
    """Linha de planilha manual ligada: coluna `ativo` vazia ou afirmativa."""
    return normalize_text(linha.get("ativo")) in ("", "SIM", "S", "1", "TRUE", "X")


@dataclass
class ResultadoPlanilha:
    arquivo: str
    novas: int = 0
    atualizadas: int = 0
    fora_do_universo: int = 0
    travada: bool = False

    def texto(self) -> str:
        if self.travada:
            return f"{self.arquivo}: aberta em outro programa, mantida como estava"
        return (f"{self.arquivo}: {self.novas} novas, {self.atualizadas} atualizadas"
                + (f", {self.fora_do_universo} fora do universo" if self.fora_do_universo else ""))


def atualizar_planilha(
    destino: Path,
    sistema: Sequence[str],
    humano: Sequence[str],
    linhas: list[dict],
    chaves: Sequence[str] = ("chave",),
) -> ResultadoPlanilha:
    """Refaz as colunas de sistema e preserva o resto (ver docstring do modulo)."""
    resultado = ResultadoPlanilha(destino.name)
    existentes = armazem.ler_csv(destino)
    cabecalho = armazem.cabecalho_csv(destino)
    extras = [c for c in cabecalho if c and c not in sistema and c not in humano]

    def chave_de(linha: dict) -> tuple:
        return tuple((linha.get(c) or "").strip() for c in chaves)

    novas_por_chave = {chave_de(linha): linha for linha in linhas}
    saida: list[dict] = []
    vistas: set[tuple] = set()
    for antiga in existentes:
        chave = chave_de(antiga)
        if chave in vistas:
            continue
        vistas.add(chave)
        linha = dict(antiga)
        nova = novas_por_chave.get(chave)
        if nova is not None:
            for coluna in sistema:
                if coluna != NO_UNIVERSO:
                    linha[coluna] = nova.get(coluna, "")
            linha[NO_UNIVERSO] = "sim"
            resultado.atualizadas += 1
        elif any((linha.get(c) or "").strip() for c in list(humano) + extras):
            linha[NO_UNIVERSO] = "não"
            resultado.fora_do_universo += 1
        else:
            # Fora do universo e sem nada que gente escreveu: nao ha o que guardar.
            continue
        saida.append(linha)
    for chave, nova in novas_por_chave.items():
        if chave in vistas:
            continue
        linha = {coluna: nova.get(coluna, "") for coluna in sistema}
        linha.update({coluna: "" for coluna in humano})
        linha[NO_UNIVERSO] = "sim"
        saida.append(linha)
        resultado.novas += 1
    colunas = list(sistema) + [c for c in humano if c not in sistema] + extras
    try:
        armazem.gravar_csv(destino, colunas, saida)
    except PermissionError:
        resultado.travada = True
    return resultado


def garantir_cabecalho(config: Config, arquivo: str, colunas: Sequence[str]) -> bool:
    """Cria a planilha so com o cabecalho quando ela nao existe; nunca reescreve."""
    destino = caminho(config, arquivo)
    if destino.exists():
        return False
    armazem.gravar_csv(destino, colunas, [])
    return True


# --- sugestoes por triagem do nome ----------------------------------------------------

# (grupo, segmento) de `segmento_do_etf` -> classe no vocabulario do mapeamento
# (Inflacao, RV Brasil, RV Internacional, Cambio...). So sugestao.
_CLASSE_POR_SEGMENTO = {
    "Inflação (IMA-B e Tesouro IPCA)": "Inflação",
    "Prefixado (IRF-M e Tesouro pré)": "Prefixado",
    "Tesouro Selic e CDI": "Pós-fixado",
    "Crédito privado (debêntures e LF)": "Crédito privado",
    "Tesouro com duration-alvo": "Renda fixa (duration-alvo)",
    "Renda fixa internacional": "RF Internacional",
    "Dólar": "Câmbio",
    "Ouro": "Commodities",
    "Prata": "Commodities",
    "Agro (futuros)": "Commodities",
}
_CLASSE_POR_GRUPO = {
    "Ações Brasil": "RV Brasil",
    "Internacional": "RV Internacional",
    "Cripto": "Cripto",
    "Imobiliário": "Imobiliário",
}

# Codigo do indice na API da B3, lido do nome. So os indices que a B3 calcula;
# na duvida (IBrX-100 ou MSCI Brasil? Ibovespa com alavancagem?), vazio.
_CODIGOS_B3 = (
    (r"\bS P\b|\bMSCI\b|\bNASDAQ\b|\bFTSE\b|\bCRSP\b|\bSTOXX\b", ""),
    (r"\bALAVANCAD|\bINVERS|\bSHORT\b|\bSMART\b|\bBR\b", ""),
    (r"\bIBRX ?50\b|\bIBXL\b", "IBXL"),
    (r"\bIBRX|\bIBXX\b|\bINDICE BRASIL\b", "IBXX"),
    (r"\bSMLL\b|\bSMALL ?CAPS?\b", "SMLL"),
    (r"\bIDIV\b|\bDIVIDENDOS\b", "IDIV"),
    (r"\bIFIX\b", "IFIX"),
    (r"\bISE\b", "ISEE"),
    (r"\bIGCT\b", "IGCT"),
    (r"\bICO2\b", "ICO2"),
    (r"\bIFNC\b", "IFNC"),
    (r"\bIMAT\b", "IMAT"),
    (r"\bUTIL\b|\bUTILIDADE PUBLICA\b", "UTIL"),
    (r"\bIBOVESPA\b|\bIBOV\b", "IBOV"),
)
_CODIGOS_B3_COMPILADOS = tuple((re.compile(p), codigo) for p, codigo in _CODIGOS_B3)


def sugestao_classe_xp(nome: str | None) -> str:
    grupo, segmento = segmento_do_etf(nome)
    return _CLASSE_POR_SEGMENTO.get(segmento) or _CLASSE_POR_GRUPO.get(grupo, "")


def sugestao_codigo_b3(nome: str | None) -> str:
    texto = normalize_text(nome)
    for regra, codigo in _CODIGOS_B3_COMPILADOS:
        if regra.search(texto):
            return codigo
    return ""


# --- atualizacao ----------------------------------------------------------------------


def _base(linha: dict) -> dict:
    return {
        "chave": linha.get("chave", ""), "cnpj": linha.get("cnpj", ""), "ticker": linha.get("ticker", ""),
        "nome": linha.get("nome", ""), "listado": linha.get("listado", ""),
        "gestora": rotulo_da_gestora(linha.get("gestora"), linha.get("nome")),
        "tipo_b3": linha.get("tipo_b3", ""),
    }


def indice_no_regulamento(config: Config) -> dict[str, str]:
    """chave -> indice de referencia do regulamento: o conferido, ou o candidato."""
    achados: dict[str, str] = {}
    for linha in ler(config, "regulamento.csv"):
        if linha.get("campo") != "indice_referencia":
            continue
        valor = (linha.get("valor_conferido") or "").strip() or (linha.get("candidato") or "").strip()
        if valor:
            achados[linha.get("chave", "")] = valor
    return achados


def atualizar_modelos(config: Config, universo: list[dict]) -> list[ResultadoPlanilha]:
    """Refaz as planilhas de `MODELOS` com o universo atual e cria as de cabecalho."""
    indices_reg = indice_no_regulamento(config)
    base = [_base(linha) for linha in universo]
    for linha in base:
        grupo, segmento = segmento_do_etf(linha["nome"])
        linha["segmento_triagem"] = f"{grupo} - {segmento}"
        linha["sugestao_classe_xp"] = sugestao_classe_xp(linha["nome"])
        linha["sugestao_codigo_b3"] = sugestao_codigo_b3(linha["nome"])
        linha["indice_no_regulamento"] = indices_reg.get(linha["chave"], "")
    base.sort(key=lambda l: (l["ticker"] == "", l["ticker"], l["chave"]))
    resultados = [
        atualizar_planilha(caminho(config, modelo.arquivo), modelo.sistema, modelo.humano, base, modelo.chaves)
        for modelo in MODELOS
    ]
    for arquivo, colunas in SO_CABECALHO:
        garantir_cabecalho(config, arquivo, colunas)
    return resultados


def preenchidos(config: Config, arquivo: str, coluna: str) -> set[str]:
    """Chaves das linhas com `coluna` preenchida - o que a cobertura mede."""
    return {
        (linha.get("chave") or "").strip()
        for linha in ler(config, arquivo)
        if (linha.get(coluna) or "").strip() and (linha.get("chave") or "").strip()
    }


def codigos_b3(config: Config, universo: list[dict]) -> dict[str, str]:
    """chave -> codigo do indice B3: o que a pessoa escreveu em indices.csv, ou a sugestao."""
    humanos = {
        (linha.get("chave") or "").strip(): (linha.get("codigo_b3") or "").strip().upper()
        for linha in ler(config, INDICES.arquivo)
    }
    codigos = {}
    for linha in universo:
        chave = linha.get("chave", "")
        codigo = humanos.get(chave) or sugestao_codigo_b3(linha.get("nome"))
        if codigo:
            codigos[chave] = codigo
    return codigos
