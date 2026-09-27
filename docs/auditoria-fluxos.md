# Auditoria dos fluxos do monitor

Auditoria de 2026-09-23 do sistema inteiro: como os fluxos se encadeiam, o que
funciona, o que falha, e o plano de correcao. Complementa `docs/analise-fluxo.md`
(a analise anterior) - os achados dela foram reconferidos um a um contra o codigo
de hoje (secao 4).

Nada foi afirmado so pela leitura do codigo quando dava para medir:

- coleta real das cinco casas de fundos e do radar CVM, numa pasta temporaria,
  sem tocar em `data/`;
- `doctor`, `insights` de fundos e duas execucoes do modulo de previdencia (22 e
  23/09 - o primeiro diff real dele);
- leitura das superficies publicadas em `data/reports/` e conferencia contra as
  paginas das casas, no navegador;
- os achados de `analise-fluxo.md` reconferidos contra o codigo atual.

Severidade:

- **Alta** - o relatorio afirma algo falso, ou um objetivo do produto nao opera.
- **Media** - o sinal enfraquece, ou a operacao depende de alguem lembrar.
- **Baixa** - higiene, custo de manutencao, cosmetico.

## 1. O sistema

### Objetivos

1. **O que cada casa oferta hoje** - a vitrine de fundos de varejo de XP
   (baseline), BTG, Itau, Nu e Inter, comparada entre as casas.
2. **O que cada casa esta para lancar** - radar de fundos pre-operacionais na CVM,
   das gestoras ligadas a cada casa.
3. **A qualidade do que esta na vitrine** - captacao, rentabilidade, custo e
   mandato dos fundos de cada vitrine, contra o mercado.
4. **Mercado de ETFs** - captacao semanal dos ETFs listados.
5. **Previdencia** - as vitrines de PGBL/VGBL, no mesmo modelo, em modulo proprio.

### Fluxos

```
run_monitor.bat  (manual - nao ha agendamento; termina em `pause`)
  |
  +-- doctor ................................ acesso as fontes (so as de fundos)
  |
  +-- python -m shelf_monitor run
  |     |
  |     +-- COLETA DE PRATELEIRAS  collect_platforms (main.py:165), em serie
  |     |     sites (Playwright / API / HTML) + Open Finance, uniao por casa
  |     |     merge_offers -> assess_quality -> save_shelf_snapshot
  |     |     => data/snapshots/<casa>/<data>.json
  |     |
  |     +-- RADAR CVM  collect_cvm: cadastro CVM x watchlist de gestoras
  |     |     => data/cvm/extract/<data>.json
  |     |
  |     +-- ensure_market_base: importa a BASE DE MERCADO so se ausente
  |     |     (o `market` completo e manual) => data/market/*.csv
  |     |
  |     +-- build_report (main.py:259)
  |     |     enrich       join por CNPJ + resolvedor por nome, contra a base
  |     |     diff_shelf   vitrine de hoje x coleta confiavel anterior
  |     |     diff_cvm     transicoes de situacao (lancamentos)
  |     |     captacao     informe diario da CVM, fundos da vitrine
  |     |     rentabilidade ponderada, custo, mandato (informe e extrato)
  |     |
  |     +-- render_outputs: digest md/html, dashboard, deck, planilha
  |     |     => data/reports/
  |     +-- --send-email / --git-commit ....... opcionais, nunca usados
  |
  +-- python -m shelf_monitor.previdencia run
        mesmo pipeline, vitrines de previdencia => data/previdencia/

Fora do .bat, sob demanda:
  market              base geral da CVM (cadastro, extrato de taxas, cad_fi)
  etf                 catalogo, fluxo semanal e PL dos ETFs => etf_*.xlsx/.csv
  insights, captacao, performance, rentabilidade, custo, mandato   leituras avulsas
  diff, report, export    recomparar, re-renderizar, planilha avulsa
  diagnose, watchlist-discover    evidencia bruta das fontes; gestoras da watchlist
```

### Funcionalidades

| Fluxo | Comando | Entra | Sai | Cadencia hoje |
|---|---|---|---|---|
| Coleta de prateleiras | `run`, `collect` | sites + Open Finance | snapshot por casa e dia | manual: 5 coletas em 3,5 semanas |
| Radar CVM | `run`, `cvm` | cadastro da CVM | extrato por dia | junto do `run` |
| Base de mercado | `market` | cadastro, extrato, cad_fi | `data/market/*.csv` | manual; `run` so importa se faltar |
| Enriquecimento e diff | dentro de `build_report` | snapshots + base | em memoria | a cada `run`/`report` |
| Blocos analiticos | `run`; avulsos `captacao`, `performance`, `rentabilidade`, `custo`, `mandato`, `insights` | informe diario, extrato | anexos do relatorio | a cada `run` |
| Superficies | `run`, `report`, `export` | relatorio | digest, dashboard, deck, planilha | a cada `run` |
| Distribuicao | `--send-email`, `--git-commit` | relatorio | e-mail, commit | nunca usada |
| ETF | `etf` | INF_DIARIO ou Fundos.NET, B3, COTAHIST | xlsx + csv | manual, fora do `.bat` |
| Previdencia | `python -m shelf_monitor.previdencia` | vitrines PGBL/VGBL | `data/previdencia/` | no `.bat`, apos fundos |
| Diagnostico | `doctor`, `diagnose`, `watchlist-discover` | fontes | console, `data/cache/debug` | sob demanda |

## 2. O que funciona bem

- **As fontes estao de pe.** `doctor` em 23/09: CVM, Open Finance e as cinco
  prateleiras alcancaveis - XP e Itau so pelo navegador, como documentado.
- **A coleta funciona ao vivo.** Em 23/09, numa pasta temporaria, as cinco casas
  responderam: vitrine de 666 fundos na XP, 662 no BTG, 460 no Itau e 466 no Inter
  (o Nu nao tem vitrine publica). CNPJ em 93% da vitrine da XP, 100% no BTG e 99% no
  Inter; o Itau segue com 6%.
- **As defesas da analise anterior continuam valendo**: uniao de fontes com
  precedencia, guarda de escrita do snapshot e do extrato CVM, anti-churn por
  encolhimento e por fonte perdida, `run` com codigo de saida, renderers isolados
  (um que quebra nao derruba os outros).
- **O diff do dia seguinte e limpo.** Fundos, 19 -> 20/09: zero movimento nas cinco
  casas. Previdencia, 22 -> 23/09: zero entrada ou saida falsa, e um sinal real
  ("JGP Acoes 100 Prev XP Seg FIC FIA" fechou para captacao).
- **A classificacao fechou a lacuna do Itau** (C8 da analise anterior): com o
  resolvedor por nome, as vitrines de fundos ficam entre 94% (Inter) e 100% (Itau)
  classificadas, pelo docstring de `classify.py`.
- **Captacao e rentabilidade tem cobertura real e declarada**: BTG 95% e XP 86% da
  vitrine com informe diario, a cobertura impressa ao lado do numero; a referencia
  de rentabilidade usa o universo inteiro da categoria.
- **O radar CVM tem historico**: 4 extratos em disco. O "primeira execucao eterna"
  da analise anterior acabou.
- **540 testes verdes**; a saida de fundos foi verificada byte a byte na entrega
  da previdencia.

## 3. Gaps por fluxo

### 3.1 Blocos analiticos de fundos

**G1 [Alta] O veredito de ticket da XP e falso - e as duas partes do sistema se
contradizem.**

- Digest, dashboard e deck (`aggregate.leitura_do_ticket`, aggregate.py:246):
  "A vitrine da XP nao tem nenhum fundo abaixo de R$ 5.000. Nao e diferenca de
  composicao: e a ausencia de uma porta de entrada de varejo." A docstring da
  funcao registra isso como medido.
- `insights` (modulo competitivo): "A vitrine da XP nao exibe aplicacao minima; as
  concorrentes exibem", e recomenda a XP "avaliar exibir o ticket na listagem",
  com confianca alta.
- A pagina da XP, conferida no navegador em 23/09: **exibe** o minimo, e 39 dos 50
  primeiros fundos tem minimo de ate R$ 1.000.

Causa, em tres elos: (1) o coletor da XP nao captura o minimo - os 154 fundos que
so a vitrine trouxe estao sem ele; (2) o merge poe o Open Finance em primeiro na
precedencia (merge.py:24), entao nos 514 fundos que tambem estao no catalogo vale o
minimo institucional da classe, que `custo.py` ja documenta como "nao o que o site
cobra"; (3) as faixas de ticket usam esse valor mesclado (aggregate.py:768), e nao
`vitrine_only_values` (competitive.py:379), a regra que o modulo competitivo usa
para o mesmo tema.

**G2 [Media] Mesmo indicador, duas regras.** G1 e sintoma: "ticket" tem uma regra
em `aggregate` e outra em `competitive`. A analise anterior ja apontava que
`aggregate` centraliza so metade das superficies (D9).

### 3.2 Coleta e qualidade do snapshot

**G3 [Alta] Snapshot "ok" sem vitrine nenhuma.** Em 13/09, XP, BTG e Itau foram
gravados `ok` com vitrine = 0: so o Open Finance respondeu. `assess_quality`
(diff.py:74) so rebaixa quando a prateleira encolhe ou uma fonte da baseline some -
se a baseline tambem nao tinha vitrine, nada dispara. Para um monitor de vitrine,
coleta sem vitrine nao pode ser "ok". Continua acontecendo: na coleta de 23/09 o Nu
saiu `ok` com vitrine zero (por desenho ele nao tem vitrine publica, mas o status
nao diz isso). Residual do C1.

**G4 [Alta] Ganho de cobertura do coletor vira "fundos que entraram".** Em 19/09,
com o conserto do navegador (commit a2dbed4), as vitrines apareceram: o diff contou
+154 na XP, +174 no BTG e +432 no Itau, nenhum marcado como suspeito, e o digest de
19/09 - versionado - abre com "828 fundos entraram". O anti-churn e assimetrico:
guarda contra encolher (`SUSPECT_SHRINK_RATIO`, config.py:160), nao contra crescer.
O modulo competitivo ja trata "vitrine que acabou de nascer" como nao-movimento; o
diff e as superficies, nao.

**G5 [Media] A XP perde 7 fundos da vitrine no merge.** Em 20/09 o navegador trouxe
675 e so 668 ficaram com essa fonte depois do merge. Pode ser duplicata legitima ou a
colisao medida na previdencia - o `name_slug` descarta RF e MM, e dois fundos sem
CNPJ viram um. O snapshot nao permite distinguir. Ha tarefa aberta.

**G6 [Media] Tudo em serie, sem nova tentativa.** Nenhum paralelismo no pacote (C5)
e nenhum retry com espera em `http.get` (http.py:125; C7): um erro passageiro
derruba a fonte. Os dois aconteceram na coleta de 23/09. Tempos: XP 38 s, BTG
2 min 24 s, Itau 1 min 8 s, Nu instantaneo - e o Inter 13 min 24 s, com 574 paginas
em serie, 3,2 vezes as outras quatro casas somadas. E o Open Finance do Inter
respondeu HTTP 500 uma vez: sem nova tentativa, a fonte ficou fora da coleta do
dia, que saiu `ok` com a falha so como observacao.

### 3.3 Diff e movimentos

**G7 [Media] As superficies discordam sobre o que e "entrada".** O corpo do digest
conta so a vitrine de varejo (digest.py:91). O assunto do e-mail (mailer.py:34), o
resumo do console (main.py:556), o slide de movimentos do deck (deck.py:458) e o
`diff_summary` do dashboard (aggregate.py:1093) contam o diff inteiro, com o
catalogo do Open Finance. Medido 13 -> 19/09 no BTG: 236 no diff inteiro, 174 na
vitrine. O deck ainda cita como exemplo de entrada fundos que so estao no catalogo.
Residual do D1 e do D6.

**G8 [Media] A janela de comparacao varia sem aviso.** Coletas em 29/08, 31/08,
13/09, 19/09 e 20/09: intervalos de 2, 13, 6 e 1 dia. "Entradas desde a ultima
coleta" muda de significado a cada relatorio, e nenhuma superficie diz qual foi a
janela. Consequencia de G15.

**G9 [Baixa]** `list.remove` sobre dataclass com igualdade estrutural
(diff.py:197-198; D12), e CNPJ apagado na aba Movimentos para identidade com sufixo
`#N` (spreadsheet.py:859; D13).

### 3.4 Radar CVM

**G10 [Alta] O lancamento so chega ao digest.** Zero mencoes a lancamento no
dashboard, no deck e na planilha (D4). O objetivo 2 - "o que a casa esta para
lancar" - aparece em uma das quatro superficies.

**G11 [Media] Radar sem estado e sem filtro.** Nao se sabe ha quanto tempo um fundo
esta pre-operacional (D3), e FIP, veiculo de previdencia fechada e fundo
institucional nao sao filtrados (D5).

**G12 [Media] Watchlist so por regex.** Nenhum `cnpj_roots` preenchido
(config.py:126; C10): a via precisa, por raiz de CNPJ da gestora, esta desligada.

Coleta ao vivo em 23/09: 7 s, 11.851 fundos na watchlist, 381 pre-operacionais.
Deles, 104 (27%) sao veiculos de previdencia (PREV ou FLEXPREV no nome) e 49 (13%)
sao FIDC, FIP, FII ou FIIM - perto de 40% do radar de "lancamentos" nao e
lancamento de varejo, o que G11 quantifica.

### 3.5 Enriquecimento e base de mercado

**G13 [Media] Enriquecimento nao persistido** (O5). O snapshot e gravado antes do
enriquecimento (main.py:190): um `report` semanas depois, com outra base, da outro
numero, e nada registra qual base gerou qual relatorio.

**G14 [Baixa] Dois downloaders da CVM** (C6): `collectors/cvm.py` baixa com o
proprio `get`, sem o ETag e a gravacao atomica de `market/fetch.py`. E `market`
importa funcoes privadas de `collectors/cvm.py` (market/fees.py:42,
market/parse.py:19; C12).

### 3.6 Operacao e distribuicao

**G15 [Alta] O monitor nao roda sozinho.** Nenhuma tarefa no Agendador do Windows,
e o `run_monitor.bat` termina em `pause` (linha 101), que travaria uma execucao
agendada. Resultado: 5 coletas em 3,5 semanas, com buracos de ate 13 dias - e G8.

**G16 [Media] A distribuicao esta desligada.** Nao ha `.env`, entao nao ha SMTP, e o
`.bat` nao passa `--send-email`: o digest nunca e enviado. O `--git-commit` nunca foi
usado - nenhum commit "data: snapshot" no historico; os dados sao commitados a mao.

**G17 [Media] `git_commit` pega o indice do usuario** (main.py:581; O7). O `git add`
tem pathspec, o `git commit -m` nao: commita tudo que estiver staged, inclusive
trabalho em andamento. Na previdencia ainda inclui `data/cvm/extract`, que e de
fundos.

**G18 [Baixa]** `dashboard.html` sem data: cada execucao apaga a anterior (O10). O
`export` duplica a orquestracao do `build_report` (O6) - hoje produz o mesmo, mas
todo gancho novo precisa ser lembrado nos dois lugares.

### 3.7 ETF

**G19 [Alta] O caminho padrao mede feeders, nao ETFs.** `etf --flow-source` tem
padrao `inf_diario` (main.py:1570), e o proprio help diz "so cobre feeders"; medido
em `fie_balancete.py`: 0 de 249 ETFs no INF_DIARIO. O comando se anuncia como
"captacao semanal de todos os ETFs".

**G20 [Media] O caminho certo nao tem serie.** A via `fnet` depende dos informes
diarios coletados do Fundos.NET: ha 1 coleta em disco (18/09), e a coleta e manual e
fora do `.bat`. Fluxo semanal pede pelo menos duas semanas de pregao. (Corrigido na
onda 1: aquela coleta traz 853 informes de 7 datas, de 10 a 18/09 - uma semana
inteira e uma pela metade -, e nao "1 dia".)

### 3.8 Previdencia

Detalhe e numeros na auditoria anterior (conversa de 2026-09-23) e em
`docs/plano-previdencia.md`.

- **G21 [Alta] Metricas por cotista sem sentido.** 216 de 217 fundos da XP e 224 de
  224 do BTG tem 1 cotista, a seguradora: rentabilidade "por cotista", "PL menos
  cotistas", "fundos de muita gente" e "+N cotistas" nao medem nada.
- **G22 [Alta] Gaps e exclusivos por CNPJ sao estruturais, e sao a manchete.** Cada
  seguradora tem o proprio veiculo; o "inclusao operacional" recomendado e
  impossivel. Ha tarefa aberta.
- **G23 [Media] A guarda de truncamento da XP nao dispara.** API com 150 dos 223 sai
  `ok`, com 73 saidas falsas: o total da pagina nao existe no layout sem janela.
- **G24 [Media] Sem diagnostico.** O `doctor` nao sonda as vitrines de previdencia,
  nao ha `diagnose`, e `SHELF_BROWSER_DEBUG` nao tem efeito nos coletores novos.
- **G25 [Media]** Custo abaixo do piso (39,8% no BTG, 36% na XP) - decisao pendente.
- **G26 [Media]** Itau Private sem gestora em 36 de 41 fundos.
- **G27 [Baixa]** Radar CVM vazio e rodape citando Open Finance nas superficies da
  previdencia; motivo de Nu e Inter com data fixa e ponto duplo; seguradora "KIPREV".

### 3.9 Testes

**G28 [Media]** Os caminhos de rede nao tem teste: na previdencia, a interceptacao
da XP, o HTTP do BTG, os fallbacks por navegador e os comandos
`run`/`report`/`insights`/`collect`. As dependencias opcionais seguem sem
`skipUnless`: sem `openpyxl` ou `python-pptx` a suite da ERROR, nao SKIP (D11).

## 4. Os achados da analise anterior, hoje

| Achado | Hoje | Evidencia |
|---|---|---|
| O1 `run` sempre saia 0 | Corrigido | `run_outcome` |
| O2 renderers sem protecao | Corrigido | `render_outputs` isola cada um |
| O3 extrato CVM sem guarda | Corrigido | P0 |
| O4 base ausente em silencio | Corrigido | `warn_market_missing`, `doctor` |
| O5 enriquecimento nao persistido | Aberto | G13 |
| O6 `export` duplica `build_report` | Aberto | G18 |
| O7 `git_commit` sem pathspec | Aberto | G17 |
| O8 `run` nao consulta o `doctor` | Aberto, aceitavel | o `.bat` roda o `doctor` antes |
| O10 `dashboard.html` sem data | Aberto | G18 |
| O11 zero testes de orquestracao | Parcial | `test_main` com 13 testes |
| C1 `ok` sem vitrine | Parcial | G3 |
| C2, C3, C4 truncamento e contadores | Corrigidos | P0 |
| C5 sem paralelismo | Aberto | G6 |
| C6 dois downloaders da CVM | Aberto | G14 |
| C7 sem retry | Aberto | G6 |
| C8 join so por CNPJ | Corrigido | resolvedor por nome, Itau 100% |
| C10 `cnpj_roots` vazios | Aberto | G12 |
| C11 duplicacoes | Parcial | `report/shared` unificou limiares e rotulos |
| C12 `market` importa privados | Aberto | G14 |
| D1 diff mistura vitrine e catalogo | Parcial | digest corrigido; assunto, console, deck e dashboard nao (G7) |
| D2 anti-churn cego as fontes | Corrigido | P0 |
| D3 radar sem estado | Aberto | G11 |
| D4 lancamento so no digest | Aberto | G10 |
| D5 sem filtro no pre-operacional | Aberto | G11 |
| D6 restritos nos movimentos | Parcial | digest corrigido, deck nao (G7) |
| D7 sem historico | Parcial | o momentum do `insights` usa as datas |
| D8/D9 superficies divergentes | Parcial | teste impede md x html; G1/G2 mostram regra duplicada |
| D10 `browser.py` sem teste | Parcial | 132 testes em `test_listings` |
| D11 deps opcionais sem `skipUnless` | Aberto | G28 |
| D12 `list.remove` | Aberto | G9 |
| D13 CNPJ com `#N` | Aberto | G9 |
| `requirements.txt` com `azure-devops` | Aberto | linha 1 |
| binarios em `data/reports/` + `prateleiras20260829.xlsx` orfao | Aberto | 11 arquivos versionados |
| `playwright` comentado | Corrigido | agora dependencia declarada |

O9 (recomputacao) e C9 (fuzzy sem bloqueio) nao foram reconferidos.

## 5. Plano de correcao

Esforco: **P** ate meio dia, **M** ate dois dias, **G** mais que isso.

### Onda 1 - parar de afirmar o que e falso (feita em 2026-09-23/24, ver 5.1)

| # | Gap | Correcao | Onde | Como verificar | Esforco |
|---|---|---|---|---|---|
| 1 | G1, G2 | Uma regra para ticket: faixas e veredito sobre `vitrine_only_values`, com amostra minima; sem amostra, "a casa nao publica" - nunca "nao tem". Corrigir a docstring de `leitura_do_ticket`. | `aggregate.py:246`, `:768` | teste: vitrine sem minimo proprio -> "sem base", nao "nenhum fundo" | P |
| 2 | G1 | Capturar o minimo na vitrine da XP ("Aplicacao min." esta na lista) e guarda-lo a parte do valor do catalogo. | coletor da XP (`browser.py`) | coleta real: minimo em >= 30 fundos, maioria ate R$ 1.000 | M |
| 3 | G3 | Coleta sem nenhum fundo de vitrine sai PARTIAL em casa que declara fonte de vitrine. | `diff.assess_quality` | teste; reprocessar 13/09 | P |
| 4 | G4 | Vitrine anterior vazia, ou crescimento acima de um limiar, marca o diff como "nova linha de base", sem entradas. | `diff.py` | teste 0 -> 668; digest de 19/09 re-renderizado sem "828 entraram" | P |
| 5 | G7 | Uma definicao de movimento - a vitrine de varejo - para assunto, console, deck e dashboard. | `mailer.py:34`, `main.py:556`, `deck.py:458`, `aggregate.py:1093` | teste que compara os cinco numeros | P |
| 6 | G21, G22 | Previdencia: tirar ou reescrever as metricas por cotista e os gaps/exclusivos por CNPJ nas superficies do modulo. | `shelf_monitor/previdencia` | re-render de 23/09 sem essas secoes | M |
| 7 | G19 | `etf` avisa em destaque que `inf_diario` mede feeders, e passa a `fnet` como padrao quando houver serie. | `main.py:1570` | `etf` padrao nao publica fluxo de feeder como de ETF | P |

### 5.1 Onda 1 - o que foi feito e como foi verificado

Suite: 580 testes, verde no `pytest` e no `unittest discover`. As verificacoes ao
vivo rodaram em memoria ou em pasta temporaria; nenhum snapshot gravado foi
reescrito.

| # | O que mudou | Verificacao |
|---|---|---|
| 1 | `leitura_do_ticket` exige 30 fundos com o minimo da propria vitrine (`MIN_AMOSTRA_TICKET`); sem eles diz "sem base para comparar", e as faixas, o menor minimo e o posicionamento leem `FundOffer.minimo_vitrine`. | `tests/test_ticket.py` (9). Com o snapshot da XP de 20/09, gravado antes do conserto, o dashboard diz "Sem base para comparar o ticket da XP ... 0 fundos" no lugar de "nao tem nenhum fundo abaixo de R$ 5 mil". |
| 2 | O extrator le `minimalInitialInvestment` e `administrationRate` (o XHR da lista da XP), e o merge guarda o minimo da vitrine (`minimo_vitrine`) antes de o Open Finance vencer o campo. | Coleta real da XP em 23/09, em pasta temporaria: 620 dos 666 fundos da vitrine com o minimo dela, 488 ate R$ 1.000, 501 abaixo de R$ 5.000; em 511 o Open Finance trazia outro minimo (ex.: R$ 50 mil contra R$ 500). O veredito virou "XP: 501 fundos abaixo de R$ 5.000, contra 340 da Itau (Ion)". |
| 3 | `assess_quality(..., espera_vitrine=PlatformConfig.declara_vitrine)`: casa que declara vitrine e so trouxe catalogo sai PARTIAL. | 13/09 reprocessado em memoria: XP (627 fundos), BTG (9.837) e Itau (1.743) viram `partial`; Nu (so Open Finance) e Inter (vitrine de 466) seguem `ok`. 5 testes em `test_movimentos.py`. |
| 4 | `diff_shelf` marca `nova_linha_de_base` quando a vitrine de varejo aparece ou dobra com +50 fundos (`SUSPECT_GROWTH_RATIO`, `SUSPECT_GROWTH_MIN_FUNDS`); `is_first_run` passa a cobrir o caso, e digest, assunto, console, deck e planilha dizem "nova linha de base" com o motivo. | 19/09 contra 13/09 re-renderizado em memoria: XP 0 -> 668, BTG 0 -> 649, Itau 0 -> 458 como linha de base; o assunto vira "nova linha de base: XP Investimentos, BTG Pactual, Itau (Ion)" e "828 fundos entraram" some. O maior crescimento real medido (Inter, 399 -> 466) segue movimento. 5 testes. |
| 5 | `report/shared/movimentos.py`: a vitrine de varejo e o movimento de assunto, console, deck, payload do dashboard e digest; o deck tira os exemplos de entrada so da vitrine. `na_vitrine` mora em `profile.py`, para o diff contar a mesma vitrine que o texto publica. | Teste que compara os numeros das cinco superficies num diff com catalogo, restrito e vitrine misturados (`TestUmaDefinicaoDeMovimento`, 5 testes). |
| 6 | `RunReport.suprimidos`, preenchido de `Config.blocos_suprimidos` (vazio em fundos): a previdencia declara `cotistas` e `cnpj`, e digest, dashboard, deck, planilha e `insights` trocam cada bloco pelo motivo. | Re-render dos snapshots de 23/09 (arquivos de 24/09): nenhum dos termos no digest md/html; no dashboard aberto no navegador, so o motivo; planilha sem abas de gaps nem "Onde esta a gente", sem colunas de cotistas e de exclusivo; deck sem slide de gaps. `tests/test_supressao.py` (8) trava os dois lados: some na previdencia, fica em fundos. |
| 7 | `etf --flow-source` passa a `auto`: Fundos.NET com 10 datas de informe em disco, senao INF_DIARIO com aviso em vermelho; `run --with-etf` usa a mesma escolha, e a planilha diz a fonte no titulo ("FEEDERS de ETF ... - INF_DIARIO"). | Com as 7 datas de hoje, `auto` fica no INF_DIARIO e diz por que. 7 testes em `test_etf.py`. |

Achados durante a onda:

- A razao de crescimento sozinha lia vitrine de brinquedo (1 -> 2) como mudanca de
  escala; dai o piso de 50 fundos.
- O diff e o digest contavam vitrines diferentes (com e sem restritos), e a mesma
  frase dizia "649 fundos na vitrine" e "passou de 0 para 660"; o diff passou a
  contar a vitrine de varejo.
- Os relatorios de fundos em `data/reports/` (20/09) e os de previdencia de 23/09
  continuam com a leitura antiga ate a proxima execucao; os de previdencia de 24/09
  ja saem corrigidos.

### Onda 2 - operacao que nao depende de lembrar

| # | Gap | Correcao | Onde | Esforco |
|---|---|---|---|---|
| 8 | G15 | Agendar no Windows (diario, apos o fechamento) e pular o `pause` fora do modo interativo; saida em arquivo de log. | `run_monitor.bat` | P |
| 9 | G16 | Decidir a distribuicao: SMTP no `.env` e `--send-email` no `.bat`, ou tirar a promessa do README. | `.env`, `.bat` | P |
| 10 | G17 | `git commit -- <paths>` com pathspec e `cwd` na raiz; previdencia sem `cvm_extract_dir`. | `main.py:581` | P |
| 11 | G23 | Guarda de truncamento que funcione sem o total da pagina: comparar com a coleta anterior da mesma fonte, com limiar mais apertado que 50%. | coletor da XP previdencia, `diff.py` | P |
| 12 | G24 | `doctor` e `diagnose` cobrindo a previdencia; debug de navegador nos coletores novos. | `diagnose.py`, `previdencia/` | M |
| 13 | G6 | Retry com espera para 429/503 e erro de rede em `http.get`. | `http.py:125` | P |

### Onda 3 - cumprir as promessas do produto

| # | Gap | Correcao | Esforco |
|---|---|---|---|
| 14 | G10 | Lancamento nas quatro superficies - o payload ja e calculado em `aggregate`. | M |
| 15 | G11 | Estado "pre-operacional desde" por CNPJ e filtro de relevancia no radar. | M |
| 16 | G12 | Preencher `cnpj_roots` com `watchlist-discover`. | P |
| 17 | G20 | Coletar o Fundos.NET diariamente no `.bat`, para a serie de ETF existir. | P |
| 18 | G13 | Persistir o enriquecimento no snapshot, com a versao da base usada. | M |
| 19 | G8 | Imprimir a janela da comparacao nas superficies ("desde 13/09, 6 dias"). | P |
| 20 | G25, G26 | Custo da previdencia (decisao pendente: taxa da casa como reserva) e gestora do Itau Private. | M |

### Onda 4 - velocidade e higiene

| # | Gap | Correcao | Esforco |
|---|---|---|---|
| 21 | G6 | Paralelismo por casa e nas paginas do Inter. | M |
| 22 | G5 | Medir e corrigir a perda no merge (tarefa aberta). | M |
| 23 | G28 | Testes dos caminhos de rede com rede simulada; `skipUnless` nas deps opcionais. | M |
| 24 | G9, G14, G18 | `list.remove`, CNPJ com `#N`, downloader unico da CVM, camada do `market`, `dashboard` datado, `export` via `build_report`. | M |
| 25 | higiene | `requirements.txt` por projeto; parar de versionar binarios de `data/reports/`; remover o `.xlsx` orfao. | P |

## 6. O que nao mudar

As decisoes que a analise anterior listou continuam certas: uniao de todas as
fontes, identidade por CNPJ com slug sem forma juridica, anti-churn persistido no
snapshot, guarda de escrita, TLS somando os dois trust stores, colunas lidas por
nome, taxa polimorfica.

Uma ressalva que esta auditoria acrescenta: a **precedencia do Open Finance no
merge** continua certa para cadastro e identidade, mas nao para campo que e da
vitrine. Aplicacao minima e o caso medido (G1) - o valor que o cliente ve tem de
ser guardado a parte do valor institucional do catalogo.
