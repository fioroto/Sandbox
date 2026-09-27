# Modulo de previdencia

Registro da implementacao do monitor de previdencia (`shelf_monitor.previdencia`):
o que foi construido, cada decisao com a medicao que a sustenta, e o que ficou
em aberto. Substitui o plano que estava neste arquivo - varias premissas dele
foram desmentidas na implementacao, e as correcoes estao marcadas.

Medicoes feitas em 2026-09-22 contra `data/` e contra as vitrines ao vivo.

## Premissa

Filtrar o monitor de fundos nao resolve. Em 2026-09-20, fundos de previdencia
com `on_listing=True`: zero nas cinco casas. As casas vendem PGBL/VGBL em
paginas proprias, que os coletores de fundos nao visitam.

Decisao do usuario: monitoramento separado, com vitrine propria, como modulo
independente.

## O que foi construido

```
shelf_monitor/previdencia/
    __init__.py, __main__.py   python -m shelf_monitor.previdencia {run,collect,report,insights}
    config.py                  vitrines, rotulos proprios, ConfigPrevidencia (data/previdencia/)
    eixo.py                    eixo grosso, particao varejo/restrito, IndicePrevidencia
    custo.py                   IndiceCustoPrevidencia (referencia do custo)
    cli.py                     orquestracao - as funcoes do main de fundos, com os ganchos
    dashboard.py               dashboard no molde do de fundos, com a visao executiva da previdencia
    coletores/                 btg.py, xp.py, itau.py, sem_vitrine.py
tests/test_previdencia.py      50 testes
```

O modulo reusa o pipeline inteiro: `collect_platforms`, `build_report`,
`render_outputs`, storage, diff, enrich, captacao, rentabilidade, mandato,
custo e as quatro superficies. `run_monitor.bat` roda a previdencia depois de
fundos, com o codigo de saida de cada um em separado.

### Mudancas no codigo compartilhado

Todas com o comportamento de antes como padrao:

| onde | o que |
|---|---|
| `Config` | identidade das superficies como atributo de classe (titulo, capa, prefixo de arquivo, assunto, commit) |
| digest, dashboard, deck, planilha, mailer, `git_commit` | leem essa identidade em vez do literal |
| `build_report` | `classificador=`, `pos_enriquecimento=`, `indice_custo=` |
| `enrich_snapshots`, `build_custo`, `attach_custo_e_mandato` | repassam os ganchos |
| `collect_platforms` | `construtor=` do coletor; rotulo por `rotulo_da_casa` |
| `competitive.coarse_category` | le `KEY_CATEGORIA_GROSSA` quando existe |
| `report/shared/plataformas.py` | `rotulos_sobrepostos` (contexto que restaura ao sair) |
| `aggregate.py` | rotulos inline passam por `_platform_label` |
| `render_outputs` | `renderizar_dashboard=` (padrao: o dashboard de fundos) |
| `report/dashboard.py` | manchetes, leituras e paineis viram uma funcao cada; `_documento` monta a pagina; `_placar` ganha `sub_vitrine`, `cabeca_vitrine` e `casas_sem_vitrine` |
| `assets/dashboard.js` | guarda para painel ausente; arquitetura aberta e "fundos em 2+ vitrines" respeitam a supressao |
| `shared/fontes.py` | cadastro da CVM sem "radar de registros" quando a execucao nao o coletou |

**Regressao verificada:** digest (md e html), dashboard, e o conteudo de deck e
planilha de fundos, renderizados com carimbo fixo antes e depois de todas as
mudancas - identicos byte a byte. Suite: 489 testes antes, 539 depois, todos OK.

## As vitrines

| Casa | Vitrine | Via | Fundos | CNPJ | Join com a base |
|---|---|---|---|---|---|
| XP (baseline) | `institucional.xpi.com.br/previdencia-privada/lista-de-fundos/#/` | API da pagina, interceptada no navegador | 223 | 223 | 219 |
| BTG | `investimentos.btgpactual.com/previdencia-privada/produtos` | `GET /services/api/pension-management/funds/public` | 226 | 226 | 226 |
| Itau Private | tabela de rentabilidade do Private Bank | HTML por HTTP simples | 41 | 0 | - |
| Nu, Inter | - | sem vitrine publica | - | - | - |

### XP - corrigido: 223 fundos, com CNPJ

O plano dizia "50 de 223, sem CNPJ". Errado: 50 e o que a pagina desenha. A
pagina chama `api.xpi.com.br/corprev-apim-portal-noauth/v1/investment-fund`,
que devolve os 223 com CNPJ. A API responde 401 sem a chave do Azure APIM, e so
o app da pagina a tem - o coletor le a resposta que a pagina recebe, pelo
navegador. Nenhuma chave no codigo.

O DOM nao e estavel: na mesma data, o navegador sem janela recebeu um layout
(`.funds-table-row`, nome no `title`) e o com janela outro (`.name-column`). O
fallback, quando a API nao e interceptada, le so os NOMES dos dois layouts e
marca o snapshot PARTIAL - taxa e minimo de um DOM que muda arriscariam
atribuicao cruzada.

Campos: taxa e taxa maxima, minimo, prazos, seguradora (`plans[].insurer`),
publico do plano (`targetAudience` - 5 profissionais saem da analise),
`brands[].fundingBlocked` (36 fechados para aporte).

### BTG

Array plano, sem paginacao. Taxa em `detail`, so `PERCENTAGE` vira taxa (165 de
226; MIXED 57, FINANCIAL 4). Seguradora em `pension.partner` (KIPREV 165, ICATU
61 - gravado como a API escreve). Planos em `pension.susepCodes[].planType`: PG
e VG em todos.

### Itau Private - corrigido: 437 fundos no catalogo, 41 na vitrine

O plano contava 445 fundos: 8 eram indicadores (IGPM, Poupanca, CDI) em tabelas
soltas com a mesma numeracao. A tabela tem 437.

A vitrine de previdencia e identificada pela ESTRUTURA: a sequencia de secoes
aparece duas vezes, e a segunda passada (fundos 393-437) e a de previdencia. Um
filtro por nome perderia 6 dos 45 planos dela ("Platinum III", "Private Kinea
Multimercado") e traria 22 veiculos do catalogo geral que sao o mesmo fundo com
o nome de registro.

- Dedup pelo veiculo (cota e seis retornos): 45 linhas, 41 veiculos.
- Nome de registro do gemeo no catalogo geral vai para `nome_registro` (14
  veiculos), que o resolvedor tenta primeiro.
- A pagina nao declara charset no HTML; o BeautifulSoup adivinhava iso-8859-2.
  Decodificado pelo cabecalho HTTP.
- **Bug achado ao vivo:** o `merge_offers` compartilhado une fundos sem CNPJ
  pelo slug, e o slug descarta RF e MM - "Private Prev Rf Vision Plus" e
  "Private Prev Vision Plus MM" viravam o mesmo fundo (40 em vez de 41). O
  coletor do Itau, de fonte unica, nao passa mais pelo merge.

## As quatro adaptacoes

### 1. Eixo grosso

As 4.609 classes de previdencia do cadastro tem um unico nivel 1. Tirado o
prefixo: MULTIMERCADO 2.676, RF 1.412, ACOES 306, BALANCEADOS 215. O rotulo
completo NAO e reescrito - o custo busca a mediana por ele; o eixo grosso vem
de uma chave propria. Ao vivo, a captacao quebra por Multimercados e Renda Fixa,
e o posicionamento mostra o Itau Private com Renda Fixa 1,95x.

### 2. Particao varejo/restrito

So com o carimbo da CVM, a regra de varejo tirava 226 de 226 fundos do BTG e
219 de 223 da XP - todo FIE e "exclusivo" (cotista unico, a seguradora). As
marcas de veiculo (CVM e casa) saem da regra, preservadas em chaves proprias.
445 fundos ajustados ao vivo, exatamente BTG 226 + XP 219.

### 3. Resolvedor por nome

O de fundos colocava 20% dos nomes da XP e 18% dos do Itau em categoria de
varejo. `IndicePrevidencia` so aceita classe previdenciaria. Contra o gabarito
do BTG (nome resolvido como se nao houvesse CNPJ):

| resolvedor | categoria exata | familia | familia de varejo errada |
|---|---|---|---|
| de fundos | 75% | 89% | 36 fundos |
| de previdencia | 88% | 94% | 1 fundo |

### 4. Referencia do custo

Das 2.086 classes de previdencia com taxa comparavel, so 21 serviam de
referencia (1.608 fora por exclusivas, 333 por publico profissional); nenhuma
categoria tinha mediana. `IndiceCustoPrevidencia` ignora as marcas de veiculo
so nas classes de previdencia: 11 categorias passam a ter mediana
(Multimercado Livre 0,80% com 1.073 classes; RF Duracao Livre Credito 0,57%;
Acoes Ativo 1,50%), e as medianas de fundos ficam identicas.

## O que foi medido e rejeitado

**Preencher CNPJ pelo nome.** Contra o gabarito de 64 fundos do BTG: precisao
de 60% entre os atribuidos. O erro cai numa classe irma da mesma familia - que
acerta a categoria (86% dos erros) e erra a identidade. Nao se preenche CNPJ
por nome, como o `enrich` ja decidia por principio.

**Levar a flag `Previdenciario` da CVM ao `mercado.csv`.** Preenchida em 47 de
9.970 subclasses (43 classes). O criterio que funciona e o rotulo ANBIMA.

**Baixar limiares.** Nenhum precisou: `MIN_AMOSTRA_CATEGORIA` e do universo de
mercado, e as tres vitrines passam em `MIN_VITRINE`.

## Em aberto - decisoes do usuario

1. **Exclusividade e gaps por CNPJ sao estruturais na previdencia.** XP e BTG
   tem 1 CNPJ em comum (em 218 e 226), mas 37 gestoras em comum, e 49 fundos da
   XP estao em pares (gestora, categoria) que o BTG tambem oferece. Cada
   seguradora tem o proprio veiculo para a mesma estrategia. O "so ela tem:
   100%" e a decisao "diferencial exclusivo" do relatorio NAO sao leitura
   competitiva. O bloco de gestoras compara de verdade. Casar veiculos
   equivalentes entre seguradoras (pela gestora e estrategia, ou pelo master via
   carteira) e trabalho novo.
2. **Custo abaixo do piso.** A taxa do proprio fundo no extrato da CVM falta, e
   zero ou esta velha: comparavel em 90 de 226 no BTG (39,8%) e 78 de 218 na XP
   (36%), piso de 40%. As duas casas publicam a taxa; usa-la como reserva
   mudaria a metodologia do indicador.
3. **Lista da XP provisoria**, ate o usuario passar o que a XP de fato tem.
4. **Itau Private** por decisao do usuario: sem CNPJ, fora de captacao, custo,
   mandato e gaps.

## Execucao ao vivo (2026-09-22)

```
XP Investimentos   223 fundos via playwright   ok
BTG Pactual        226 fundos via api-json     ok
Itau Private        41 fundos via html-scrape  ok
Nu, Banco Inter    unavailable - sem vitrine publica de previdencia
```

Disponiveis: captacao (BTG, XP), rentabilidade, mandato, gaps, leitura
competitiva. Indisponivel com motivo: custo (item 2 acima). Saidas em
`data/previdencia/reports/`.
