"""Apoio dos testes do mapa de familias: CDA e cadastro montados em memoria.

Os cabecalhos sao os reais, medidos nos arquivos da CVM de 06/2026
(`cda_fi_202606.zip`): um teste que monta a linha com o nome de coluna errado
passa contra o parser e falha contra o arquivo - foi o que aconteceu com o
extrato em `market/fees.py`. Toda linha sai com TODAS as colunas do bloco,
vazias quando o caso nao precisa delas.
"""
from __future__ import annotations

import io
import zipfile
from collections import defaultdict
from pathlib import Path

from shelf_monitor.market.carteira import ResumoCarteira, resumir_competencia

_COMUM = (
    "TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;DENOM_SOCIAL;DT_COMPTC;TP_APLIC;TP_ATIVO;"
    "EMISSOR_LIGADO;TP_NEGOC;QT_VENDA_NEGOC;VL_VENDA_NEGOC;QT_AQUIS_NEGOC;"
    "VL_AQUIS_NEGOC;QT_POS_FINAL;VL_MERC_POS_FINAL;VL_CUSTO_POS_FINAL;DT_CONFID_APLIC"
)
CABECALHOS: dict[str, str] = {
    "PL": "TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;DENOM_SOCIAL;DT_COMPTC;VL_PATRIM_LIQ",
    "BLC_1": _COMUM + ";TP_TITPUB;CD_ISIN;CD_SELIC;DT_EMISSAO;DT_VENC",
    "BLC_2": _COMUM + ";CNPJ_FUNDO_CLASSE_COTA;ID_SUBCLASSE;NM_FUNDO_CLASSE_SUBCLASSE_COTA",
    "BLC_3": _COMUM + ";CD_SWAP;DS_SWAP",
    "BLC_4": _COMUM + ";CD_ATIVO;DS_ATIVO;CD_ISIN;DT_INI_VIGENCIA;DT_FIM_VIGENCIA",
    "BLC_5": _COMUM + (
        ";CNPJ_EMISSOR;EMISSOR;DT_VENC;TITULO_POSFX;CD_INDEXADOR_POSFX;"
        "DS_INDEXADOR_POSFX;PR_INDEXADOR_POSFX;PR_CUPOM_POSFX;PR_TAXA_PREFX;"
        "RISCO_EMISSOR;AG_RISCO;DT_RISCO;GRAU_RISCO"
    ),
    "BLC_6": _COMUM + (
        ";PF_PJ_EMISSOR;CPF_CNPJ_EMISSOR;EMISSOR;DT_VENC;TITULO_POSFX;"
        "CD_INDEXADOR_POSFX;DS_INDEXADOR_POSFX;PR_INDEXADOR_POSFX;PR_CUPOM_POSFX;"
        "PR_TAXA_PREFX;TITULO_CETIP;TITULO_GARANTIA"
    ),
    "BLC_7": _COMUM + (
        ";INVEST_COLETIVO;INVEST_COLETIVO_GESTOR;EMISSOR;DT_VENC;CD_PAIS;PAIS;"
        "CD_BV_MERC;BV_MERC;CD_ATIVO_BV_MERC;RISCO_EMISSOR;AG_RISCO;DT_RISCO;"
        "GRAU_RISCO;DS_ATIVO_EXTERIOR;QT_ATIVO_EXTERIOR"
    ),
    "BLC_8": _COMUM + ";DS_ATIVO;PF_PJ_EMISSOR;CPF_CNPJ_EMISSOR;EMISSOR",
    "CONFID": (
        "TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;DENOM_SOCIAL;DT_COMPTC;TP_APLIC;"
        "VL_VENDA_NEGOC;VL_AQUIS_NEGOC;VL_MERC_POS_FINAL;VL_CUSTO_POS_FINAL;"
        "DT_CONFID_APLIC"
    ),
}


def mascara(cnpj: str) -> str:
    """A CDA publica o CNPJ com mascara, como o arquivo real."""
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


class Cda:
    """Uma competencia da carteira, posicao a posicao."""

    def __init__(self, competencia: str = "202608") -> None:
        self.competencia = competencia
        self.membros: dict[str, list[dict]] = defaultdict(list)

    def _linha(self, bloco: str, cnpj: str, **valores: object) -> None:
        linha = {coluna: "" for coluna in CABECALHOS[bloco].split(";")}
        linha.update({
            "TP_FUNDO_CLASSE": "CLASSES - FIF",
            "CNPJ_FUNDO_CLASSE": mascara(cnpj),
            "DENOM_SOCIAL": f"CLASSE {cnpj}",
            "DT_COMPTC": f"{self.competencia[:4]}-{self.competencia[4:]}-31",
        })
        linha.update({k: str(v) for k, v in valores.items()})
        self.membros[bloco].append(linha)

    def pl(self, cnpj: str, valor: float, nome: str | None = None) -> "Cda":
        self._linha("PL", cnpj, VL_PATRIM_LIQ=valor, DENOM_SOCIAL=nome or f"CLASSE {cnpj}")
        return self

    def cota(self, cnpj: str, investido: str, valor: float,
             tipo: str = "Fundo de Investimento e de Cotas") -> "Cda":
        self._linha("BLC_2", cnpj, TP_APLIC="Cotas de Fundos", TP_ATIVO=tipo,
                    VL_MERC_POS_FINAL=valor, CNPJ_FUNDO_CLASSE_COTA=mascara(investido))
        return self

    def titulo(self, cnpj: str, valor: float,
               titulo: str = "LETRAS FINANCEIRAS DO TESOURO", venc: str = "2029-03-01") -> "Cda":
        self._linha("BLC_1", cnpj, TP_APLIC="Títulos Públicos", TP_ATIVO="Título público federal",
                    VL_MERC_POS_FINAL=valor, TP_TITPUB=titulo, DT_VENC=venc)
        return self

    def cdb(self, cnpj: str, valor: float, emissor: str) -> "Cda":
        self._linha("BLC_5", cnpj, TP_APLIC="Depósitos a prazo e outros títulos de IF",
                    TP_ATIVO="Letra Financeira", VL_MERC_POS_FINAL=valor,
                    CNPJ_EMISSOR=mascara(emissor), EMISSOR=f"BANCO {emissor}")
        return self

    def debenture(self, cnpj: str, valor: float, codigo: str) -> "Cda":
        self._linha("BLC_4", cnpj, TP_APLIC="Debêntures", TP_ATIVO="Debênture simples",
                    VL_MERC_POS_FINAL=valor, CD_ATIVO=codigo, DS_ATIVO="DEB")
        return self

    def acao(self, cnpj: str, valor: float, codigo: str) -> "Cda":
        self._linha("BLC_4", cnpj, TP_APLIC="Ações", TP_ATIVO="Ação ordinária",
                    VL_MERC_POS_FINAL=valor, CD_ATIVO=codigo)
        return self

    def exterior(self, cnpj: str, valor: float, fundo_offshore: str) -> "Cda":
        self._linha("BLC_7", cnpj, TP_APLIC="Investimento no Exterior",
                    TP_ATIVO="Fundos Offshore", VL_MERC_POS_FINAL=valor,
                    EMISSOR=fundo_offshore)
        return self

    def futuro(self, cnpj: str, valor: float) -> "Cda":
        self._linha("BLC_8", cnpj, TP_APLIC="Mercado Futuro - Posições compradas",
                    VL_MERC_POS_FINAL=valor, DS_ATIVO="DI1F27")
        return self

    def sigilo(self, cnpj: str, valor: float, aplicacao: str = "Ações") -> "Cda":
        self._linha("CONFID", cnpj, TP_APLIC=aplicacao, VL_MERC_POS_FINAL=valor)
        return self

    def estranho(self, cnpj: str, valor: float) -> "Cda":
        self._linha("BLC_8", cnpj, TP_APLIC="Tipo que a CVM ainda não publicava",
                    VL_MERC_POS_FINAL=valor, DS_ATIVO="X")
        return self

    def resumo(self) -> ResumoCarteira:
        return resumir_competencia(self.competencia, dict(self.membros))

    def zip(self, destino: Path) -> Path:
        """O zip como a CVM publica: um CSV latin-1 por bloco."""
        caminho = destino / f"cda_fi_{self.competencia}.zip"
        with zipfile.ZipFile(caminho, "w") as arquivo:
            for bloco, cabecalho in CABECALHOS.items():
                buffer = io.StringIO()
                buffer.write(cabecalho + "\n")
                colunas = cabecalho.split(";")
                for linha in self.membros.get(bloco, []):
                    buffer.write(";".join(linha.get(c, "") for c in colunas) + "\n")
                arquivo.writestr(
                    f"cda_fi_{bloco}_{self.competencia}.csv",
                    buffer.getvalue().encode("latin-1"),
                )
        return caminho


def classe(
    cnpj: str,
    nome: str,
    gestor: str,
    cnpj_gestor: str,
    *,
    anbima: str = "Multimercados Livre",
    indicador: str = "DI de um dia",
    dias: tuple[int, int] | None = (30, 1),
    pl: float = 100_000_000.0,
    cnpj_fundo: str | None = None,
    codigo: str | None = None,
    espelho: str = "N",
    situacao: str = "Em Funcionamento Normal",
) -> dict:
    """Uma linha do mercado.csv, com os nomes de coluna do arquivo."""
    return {
        "origem": "rcvm175",
        "cnpj": cnpj,
        "denominacao": nome,
        "codigo_cvm": codigo or "",
        "situacao": situacao,
        "classificacao_anbima": anbima,
        "indicador_desempenho": indicador,
        "patrimonio_liquido": str(pl),
        "gestor": gestor,
        "cnpj_gestor": cnpj_gestor,
        "cnpj_fundo": cnpj_fundo or cnpj,
        "dias_cotizacao_resgate": "" if dias is None else str(dias[0]),
        "dias_pagamento_resgate": "" if dias is None else str(dias[1]),
        "fundo_espelho": espelho,
    }
