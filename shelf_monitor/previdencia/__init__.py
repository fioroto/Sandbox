"""Monitor de previdencia - modulo independente do monitor de fundos.

Uso:
    python -m shelf_monitor.previdencia run        # coleta, diff e relatorios
    python -m shelf_monitor.previdencia collect    # so a coleta
    python -m shelf_monitor.previdencia report     # re-renderiza dos snapshots

Por que um modulo separado, e nao um filtro sobre o monitor de fundos
--------------------------------------------------------------------

Medido em 2026-09-20: nenhuma vitrine de fundos traz fundo de previdencia com
`on_listing=True` - zero nas cinco casas. As casas vendem PGBL/VGBL em paginas
proprias, que os coletores de fundos nao visitam. A previdencia tem vitrine
propria, pasta propria (`data/previdencia/`) e coletores proprios; reusa o
resto do pipeline - FundOffer, ShelfSnapshot, enrich, diff, captacao, custo,
digest, dashboard, deck e planilha.

Quatro coisas nao podiam ser reusadas como estao, e cada uma foi medida antes
de ser trocada (os numeros estao nos modulos que as tratam):

- **Vitrine.** `coletores/` - BTG por API publica propria, XP pela API que a
  pagina chama (interceptada no navegador), Itau pelo bloco de previdencia do
  catalogo do Private Bank.
- **Eixo de categoria.** `eixo.py` - todo rotulo ANBIMA de previdencia tem o
  mesmo nivel 1, e o eixo grosso colapsava num balde so.
- **Particao varejo/restrito.** `eixo.py` - todo FIE e "exclusivo" no registro
  da CVM, porque o cotista e a seguradora. A regra de varejo do modulo de
  fundos jogava a vitrine inteira fora da analise.
- **Referencia do custo.** `custo.py` - pelo mesmo motivo, nenhuma categoria de
  previdencia tinha mediana de mercado, e o custo saia indisponivel.

O dashboard tem pagina propria, no molde do de fundos (`dashboard.py`): a
visao executiva e os paineis de fundos sobre a previdencia davam duas manchetes
e tres paineis de "nao se aplica".

O modulo de fundos nao muda de comportamento: os ganchos que este modulo usa
(`build_report(classificador=, pos_enriquecimento=, indice_custo=)`, a chave
de eixo grosso, os rotulos sobrepostos e a identidade das superficies no
`Config`) tem como padrao exatamente o que o codigo fazia antes - verificado
renderizando as superficies de fundos antes e depois: identicas byte a byte.

Limites conhecidos (numeros em `shelf_monitor/README.md`):

- exclusividade e gaps por CNPJ sao estruturais aqui - cada seguradora tem o
  proprio veiculo para a mesma estrategia (XP e BTG: 1 CNPJ em comum, 37
  gestoras em comum). O bloco de gestoras e o que compara de verdade;
- o custo tem referencia, mas a taxa do proprio fundo no extrato da CVM falta
  ou esta velha em boa parte da vitrine, e a cobertura fica abaixo do piso;
- o Itau Private nao publica CNPJ: ele vem da cota do dia no informe diario
  e, no que ela nao identifica, do nome (`previdencia/cota.py`). Desde
  28/09/2026 a pagina do catalogo esta fora do ar no servidor do Itau, e a
  coleta sai UNAVAILABLE com esse motivo.
"""
