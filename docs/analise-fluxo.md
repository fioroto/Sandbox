# Analise do fluxo do monitor de prateleiras

> Reconferida em 2026-09-23: o estado atual de cada achado abaixo, os gaps novos e
> o plano de correcao estao em `docs/auditoria-fluxos.md`.

Diagnostico do fluxo ponta a ponta do `shelf_monitor`, feito contra o codigo e os
snapshots reais em `data/`, com as melhorias priorizadas pelo que cada uma faz
pelos dois objetivos do projeto:

1. **O que cada casa oferta hoje** - comparacao competitiva de prateleira.
2. **O que cada casa esta prestes a lancar** - radar CVM de fundos pre-operacionais.

Cada achado traz a evidencia (`arquivo:linha`) que o sustenta.

## Sumario executivo

O fluxo funciona ponta a ponta e as defesas centrais estao bem construidas - a uniao
multi-fonte, a identidade por CNPJ, o anti-churn por tamanho e a guarda de escrita do
snapshot sao decisoes acertadas e nao devem ser mexidas. Os problemas estao em outro
lugar: **o que o fluxo faz com o sinal depois de coleta-lo**.

Tres riscos estruturais, em ordem de impacto sobre os objetivos:

**1. O sinal competitivo esta enterrado.** O diff compara o catalogo regulatorio
inteiro, nao a vitrine: as "132 saidas do BTG" que a execucao de hoje reporta sao churn
dos ~9.8 mil registros do Open Finance, nao movimento de prateleira. Some-se a isso um
status `ok` que nao distingue "coletei tudo" de "coletei metade das fontes" - os
snapshots de 2026-08-31 de XP, BTG e Itau estao gravados como `ok` **sem nenhuma fonte
de vitrine**, so com o catalogo.

**2. A promessa de antecipar lancamentos nunca operou.** Ha um unico extrato CVM em
disco, entao `cvm_diff.is_first_run` e sempre verdadeiro e a secao de lancamentos do
digest nunca abriu. O radar tambem nao tem estado (ninguem sabe ha quanto tempo um fundo
esta pre-operacional), o lancamento nao chega a tres das quatro superficies, e os 370
pre-operacionais listados sao dominados por FIP, FLEXPREV e veiculos institucionais -
ruido que treina o leitor a ignorar a secao.

**3. A operacao e fragil e silenciosa.** O `run` saia 0 com as cinco casas bloqueadas;
uma excecao no dashboard descartava o e-mail e o commit de uma coleta de dezenas de
minutos; o extrato CVM da tarde sobrescrevia o da manha sem guarda; e a base de mercado
ausente degradava toda a analise sem dizer uma palavra.

O terceiro grupo foi corrigido nesta entrega (P0, abaixo). Os dois primeiros estao
mapeados como P1 e P2.

## Mapa do fluxo atual

```
python -m shelf_monitor run
  |
  +-- collect_platforms (main.py:76)          laco SEQUENCIAL: xp -> btg -> itau -> nu -> inter
  |     +-- PlatformCollector (platform.py:39)  laco SEQUENCIAL de estrategias por casa
  |     |     open-finance | api-json | playwright | html-scrape
  |     +-- merge_offers (merge.py:55)          uniao, precedencia por confianca
  |     +-- assess_quality (diff.py:45)         rebaixa antes de salvar
  |     +-- save_shelf_snapshot (storage.py:37) recusa sobrescrever coleta melhor
  |
  +-- collect_cvm (main.py:114)               radar da watchlist
  |
  +-- build_report (main.py:129)
  |     +-- enrich_snapshots (enrich.py:294)   join por CNPJ com data/market/mercado.csv
  |     +-- diff_shelf (diff.py:160)           2 snapshots, anti-churn
  |     +-- diff_cvm (diff.py:221)             transicoes de situacao
  |
  +-- render_outputs (main.py:156)            digest md/html, dashboard, deck
  +-- git_commit / send_email (opcionais)

Fora do run: `export` (planilha) e `market` (base geral do mercado).
```

## Achados por camada

### Orquestracao

| # | Achado | Evidencia |
|---|---|---|
| O1 | `run` sempre saia 0 - cinco casas indisponiveis davam job verde no cron | `main.py:245-257`, sem `sys.exit` |
| O2 | Digest e dashboard sem protecao: excecao descartava e-mail e commit | `main.py:159-160` vs. `main.py:162-169` (so o deck tinha `try`) |
| O3 | `save_cvm_snapshot` sobrescrevia sem guarda de qualidade | `storage.py:106-108` vs. `storage.py:37-53` |
| O4 | Base `market` ausente degradava tudo em silencio | `main.py:138-140` sem `else`; muda ate a particao varejo/restrito (`profile.py:43-49`) |
| O5 | Enriquecimento nao e persistido nem datado - `report` semanas depois pode dar outro numero | snapshot salvo em `main.py:92`, enriquecido em `main.py:138` |
| O6 | `export` reimplementa `build_report` (sem `diff_cvm`) - duas fontes de verdade | `main.py:346-356` duplica `main.py:138-146` |
| O7 | `git_commit` sem `cwd=` e sem pathspec: pega o indice pre-existente do usuario | `main.py:215-239`; ha `.xlsx` de 4,2 MB versionado |
| O8 | `doctor` e descrito como gate mas o `run` nao consulta nada dele | `README.md:22` vs. `main.py:245-257` |
| O9 | Recomputacao: baseline lida 2x/casa, `build_dashboard_data` 2x, `split_by_profile` ~5x/snapshot | `main.py:88` e `:143`; `dashboard.py:741` e `deck.py:48`; `aggregate.py:179,185,290,308,355` |
| O10 | `dashboard.html` sem data: cada execucao apaga a anterior | `dashboard.py:867` |
| O11 | Zero testes de orquestracao - `main.py` nao era importado por nenhum teste | - |

### Coleta e consolidacao

| # | Achado | Evidencia |
|---|---|---|
| C1 | Status `ok` escondia vitrine nao coletada: fonte inteira caida virava so observacao | `base.py:68` (so `warnings` rebaixa), `platform.py:77-79` |
| C2 | Truncamento silencioso do Open Finance: `total_pages` reatribuido por pagina | `open_finance.py:187`; a flag `truncated` nunca disparava |
| C3 | Teto de 400 paginas do BTG cortava sem avisar | `btg.py:141` |
| C4 | Inter descartava paginas com falha sem contador | `inter.py:103-109`, `:132-135` |
| C5 | **Zero paralelismo** em todo o pacote; Inter faz ate 1.148 GETs em serie | `main.py:80`, `platform.py:39`, `inter.py:50-53` + `:117` |
| C6 | Dois downloaders da CVM de qualidade desigual - o mesmo zip baixado 2x | `collectors/cvm.py:121-130` (sem ETag) vs. `market/fetch.py:108-208` (com tudo) |
| C7 | Sem retries; 429 e 503 tratados como 404 | `http.py:149-150` |
| C8 | Join do enrich so por CNPJ - os ~450 fundos do Itau ficam de fora | `enrich.py:311-313`, apesar do fuzzy pronto em `merge.py:93-115` |
| C9 | Fuzzy do merge e O(n×m) com `difflib`, sem bloqueio: ~790 mil comparacoes no caso Itau | `merge.py:96-103` |
| C10 | Watchlist com `cnpj_roots` vazios: a via precisa esta desligada, so o regex de nome roda | `config.py:126-151` vs. `cvm.py:73-78` |
| C11 | Duplicacoes: `_as_text` x3, paginacao x2, dedup-por-slug x2, fuzzy com 3 limiares, `_CNPJ_RE` x3 | `open_finance.py:61`, `btg.py:42`, `browser.py:87`, etc. |
| C12 | `market` importa privados de `collectors/cvm.py` - quebra de camada | `market/parse.py:19`, `market/fees.py:18` |

### Diff, relatorios e testes

| # | Achado | Evidencia |
|---|---|---|
| D1 | **O diff mistura vitrine e catalogo** - o sinal competitivo fica sob 9,8 mil registros | `diff.py:180-184` nao filtra por `on_listing` (`models.py:105-113`) |
| D2 | Anti-churn cego a composicao de fontes: so olhava `len(funds)` | `diff.py:54-64`, `:203-214`; `strategies`/`source_counts` nunca lidos |
| D3 | **Radar CVM sem estado**: ninguem sabe ha quanto tempo um fundo esta pre-operacional | `registered_at`/`situation_started_at` so ordenam (`diff.py:252,262`) |
| D4 | Lancamento nao chega a 3 das 4 superficies; o payload e serializado e ignorado | `spreadsheet.py:332`; `aggregate.py:429-435`, `:458-468` nunca lidos pelo JS |
| D5 | Sem filtro de relevancia no pre-operacional: FIP/FLEXPREV/institucional dominam | `restriction_reason` (`profile.py:37-61`) nunca toca `CvmFund` |
| D6 | Restritos entram nos movimentos do digest e do deck (a planilha filtra) | `digest.py:132-166`, `deck.py:331-337` vs. `spreadsheet.py:641-644` |
| D7 | Zero historico: o diff ve dois snapshots; `shelf_snapshot_dates` e codigo morto | `storage.py:99-100` sem chamadores |
| D8 | Divergencia numerica entre superficies sob o mesmo rotulo | "Gestoras" (`spreadsheet.py:289` sobre tudo x `aggregate.py:315-320` sobre varejo); "Classe" (heuristica x ANBIMA oficial) |
| D9 | `aggregate` centraliza so metade: digest e planilha recalculam tudo | 6 implementacoes de added/removed/changed; 4 definicoes de "so nesta casa" |
| D10 | `browser.py` (979 linhas) com 2 funcoes testadas; `diagnose.py` com 1 | `test_listings.py:16-18` |
| D11 | Deps opcionais sem `skipUnless`: sem `openpyxl`/`pptx` a suite da ERROR, nao SKIP | `test_shelf_monitor.py:727-729` |
| D12 | `_reconcile_renames` usa `list.remove` sobre dataclass com `__eq__` estrutural | `diff.py:149-150` |
| D13 | CNPJ zerado na aba Movimentos para identidades desambiguadas com sufixo `#N` | `spreadsheet.py:664` (`isdigit()` falha) |

### Higiene do repositorio

- `requirements.txt:1` traz `azure-devops`, dependencia do `sprint_metrics` (projeto sem
  nenhuma relacao com o monitor), instalada por quem so quer rodar o `shelf_monitor`;
  `python-pptx` esta como dependencia dura embora tratado como opcional
  (`main.py:163-167`), e `playwright` - necessario para tres das cinco casas - esta
  comentado.
- `data/reports/` esta versionado, contra o que o README declara (`README.md:34-35`):
  ha `.xlsx` de 4,2 MB, `.pptx` e `dashboard.html` no historico, mais um
  `prateleiras20260829.xlsx` orfao, fora do padrao de nome de `spreadsheet.py:795`.

## Roadmap priorizado

### P0 - Confiar no sinal (implementado nesta entrega)

Correcoes de baixo risco que impedem o monitor de mentir em silencio. Todas com teste
offline novo.

| # | Correcao | Arquivos |
|---|---|---|
| 1 | Anti-churn sensivel a composicao de fontes: fonte que sumiu da uniao rebaixa o snapshot e marca o diff como suspeito | `diff.py` |
| 2 | Guarda de qualidade no `save_cvm_snapshot`, espelhando a das prateleiras | `storage.py`, `main.py` |
| 3 | `run` com resumo agregado e codigo de saida | `main.py` |
| 4 | Digest e dashboard protegidos: um renderer nao derruba os outros nem o commit | `main.py` |
| 5 | Aviso de base de mercado ausente ou velha, no `run`, no `export` e no `doctor` | `main.py` |
| 6 | Truncamento silencioso do Open Finance corrigido; teto do BTG avisa | `open_finance.py`, `btg.py` |
| 7 | Contadores de falha no Inter, com limiar para nao rebaixar por ruido | `inter.py`, `platform.py` |

Detalhe do item 1, que e o de maior efeito: o BTG saiu de 9.950 para 9.818 fundos entre
as duas coletas em disco porque o caminho do navegador caiu. A razao e 0,99 - passa longe
do limiar de encolhimento de 50% - e ainda assim a **vitrine comercial inteira** sumiu da
coleta. A regra nova compara as fontes que alimentavam a baseline com as que responderam
agora e, quando uma fonte material desaparece **e** a prateleira encolhe, rebaixa para
PARTIAL nomeando a fonte. Snapshots anteriores a essa coluna voltam do disco sem ela e a
regra fica inerte, sem acusar perda que nao se pode verificar.

### P1 - Cumprir a promessa do radar

O grupo que faz o produto entregar o que o README anuncia. Nenhum item aqui e cosmetico.

1. **Separar vitrine de catalogo no diff** (D1). Rodar o diff sobre `on_listing` alem do
   conjunto completo, e reportar os dois: "3 entradas na vitrine do BTG" e o sinal
   competitivo; "132 entradas no catalogo" e contexto regulatorio. Hoje so o segundo
   existe, e ele afoga o primeiro.
2. **Dar estado ao radar CVM** (D3). Persistir, por CNPJ, desde quando o fundo esta
   pre-operacional. Isso habilita as tres perguntas que o radar promete e nao responde:
   quem esta esperando ha mais tempo, qual o tempo tipico registro->operacao naquela casa,
   e o que mudou de estado desde a ultima leitura.
3. **Levar o lancamento as quatro superficies** (D4). O payload ja e calculado e
   descartado em `aggregate.py`; falta consumi-lo no dashboard, no deck e na planilha.
4. **Filtrar relevancia no pre-operacional** (D5). Aplicar `restriction_reason` a
   `CvmFund`, do mesmo modo que ja se aplica a `FundOffer`: FIP, veiculo de previdencia
   fechada e fundo institucional saem da lista principal e vao para um bloco auxiliar.
5. **Tirar os restritos dos movimentos do digest e do deck** (D6), alinhando com a
   planilha e com o que o README afirma.
6. **Cruzar CVM x prateleira**: dizer "este pre-operacional ja apareceu na vitrine do
   BTG" e exatamente o momento em que o radar vira acao comercial.

### P2 - Operacao e velocidade

1. **Paralelismo** (C5). Um `ThreadPoolExecutor` por casa e outro para as paginas do
   Inter resolve o grosso: hoje sao ~6 minutos so de renderizacao em serie, mais ate
   1.148 GETs sequenciais no Inter. Cada coletor ja e independente - nao ha estado
   compartilhado a proteger.
2. **Unificar o download da CVM** (C6): fazer `collectors/cvm.py` usar `market/fetch.py`,
   que ja tem ETag, gravacao atomica e validacao de conteudo.
3. **Retries com backoff** distinguindo 429/503 de 403 (C7); e reuso da sessao HTTP
   entre casas.
4. **Persistir o enriquecimento no snapshot** (O5), com a versao da base usada, para o
   relatorio de um dia ser reprodutivel meses depois.
5. **Historico e tendencia** (D7): usar `shelf_snapshot_dates`, datar o `dashboard.html`
   e responder "a prateleira do Itau cresceu ou encolheu no trimestre".
6. **Integrar `market` ao `run`** ou, no minimo, manter o aviso do P0 como gate explicito.
7. **Memoizar** `build_dashboard_data` e `split_by_profile` (O9).

### P3 - Higiene

1. `aggregate` como fonte unica das quatro superficies (D9), eliminando as divergencias
   numericas do D8.
2. Separar `requirements.txt` por projeto; tornar `python-pptx` opcional de verdade e
   descomentar `playwright` (ou documentar o extra).
3. Parar de versionar binarios em `data/reports/`; remover o `.xlsx` orfao.
4. Correcoes pontuais: D12 (`list.remove`), D13 (CNPJ com sufixo `#N`), O7 (`git_commit`
   com `cwd=` e pathspec), D11 (`skipUnless` nas deps opcionais).
5. Consolidar duplicacoes (C11), levar os parsers de dominio para `normalize.py` e
   resolver a quebra de camada do C12.
6. Completar o que esta pendente: parser do XHR do Itau, coletor autenticado do Nu,
   `cnpj_roots` da watchlist (C10).

## O que nao mudar

Estas decisoes estao certas e resolvem problemas reais - vale registrar para que uma
refatoracao futura nao as desfaca por engano:

- **Uniao de todas as fontes** em vez de parar na primeira (`platform.py:39-80`), com a
  coluna por fonte no relatorio. Saber em quais fontes cada fundo aparece e informacao
  competitiva por si so.
- **Precedencia por confianca** no merge (`merge.py:24-30`), com a fonte melhor vencendo
  e as demais preenchendo so o que falta.
- **Identidade por CNPJ, com slug que descarta forma juridica** (`models.py:116-122`):
  e o que impede uma reestruturacao societaria de virar "fundo novo".
- **Anti-churn por tamanho e rebaixamento persistido no snapshot** (`diff.py:45-64`):
  a marcacao precisa viver no snapshot, nao so no diff, exatamente como o docstring diz.
- **Guarda de escrita do snapshot** (`storage.py:37-53`) - agora tambem no extrato CVM.
- **TLS somando os dois trust stores** sem nunca desligar verificacao (`http.py:59-92`).
- **Colunas lidas por nome, com `unknown_columns` no manifesto** (`market/`): e o que faz
  a importacao sobreviver a uma mudanca de schema da CVM sem descartar dado em silencio.
- **Taxa de administracao polimorfica** com a unidade preservada: sem isso, "R$ 30.000
  por ano" entra num ranking de custo como 30.000%.
