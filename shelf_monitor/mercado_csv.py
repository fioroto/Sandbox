"""Leitura de linha do `mercado.csv` - as derivacoes que cinco modulos fazem.

`enrich`, `classify`, `performance`, `custo` e `mandato` abrem o mesmo arquivo
e cada um monta o proprio indice, com shapes legitimamente diferentes (um quer
taxa, outro quer mandato, outro so a categoria). O que NAO pode ser diferente
sao as derivacoes por linha, e elas estavam copiadas:

    categoria = (row.get("classificacao_anbima") or "").strip() or (
        row.get("classificacao_cvm") or ""
    ).strip()

literalmente igual em `classify`, `performance` e `custo`. A ordem dessa
cascata e uma decisao - a ANBIMA de tres niveis vale mais que o balde generico
da CVM - e tres copias significam tres lugares para esquecer de mudar.

Nao se unificou o INDICE porque as cinco formas sao diferentes de verdade, e
forca-las num objeto so trocaria duplicacao por acoplamento. O custo de ler o
arquivo cinco vezes e 1,7 s numa execucao de ~150 s: nao e onde o tempo esta.

Mora fora de `report/` de proposito: `classify` e `enrich` tambem usam, e os
dois sao importados por `report.aggregate`. Um import de `report.*` aqui
dispararia `report/__init__` e fecharia um ciclo.
"""
from __future__ import annotations

# Nomes das colunas, num lugar so: o dia em que a CVM renomear uma delas, o
# grep tem de achar um alvo e nao cinco.
COLUNA_ANBIMA = "classificacao_anbima"
COLUNA_CVM = "classificacao_cvm"


def categoria_da_linha(row: dict) -> str:
    """Categoria oficial da classe, com a cascata ANBIMA -> CVM.

    A classificacao ANBIMA e de tres niveis ("Renda Fixa Duracao Baixa
    Soberano") e a da CVM e o balde generico ("Renda Fixa"), entao a primeira
    vence quando existe. Devolve "" quando nenhuma das duas esta preenchida -
    cabe a quem chama decidir se pula a linha ou registra como sem categoria.
    """
    anbima = (row.get(COLUNA_ANBIMA) or "").strip()
    if anbima:
        return anbima
    return (row.get(COLUNA_CVM) or "").strip()
