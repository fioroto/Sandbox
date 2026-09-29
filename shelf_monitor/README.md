# Monitor de Prateleiras de Fundos

Monitora a prateleira de fundos de investimento dos concorrentes da XP a partir de
fontes publicas e cruza com o cadastro da CVM para antecipar movimentos de produto.

Duas perguntas que o monitor responde:

1. **O que cada casa oferta hoje?** Amplitude por classe, gestoras representadas,
   aplicacao minima, taxas, liquidez e exclusividade por CNPJ.
2. **O que cada casa esta prestes a lancar?** Fundos registrados na CVM por gestoras
   ligadas a cada casa, com destaque para os que estao em **fase pre-operacional** -
   registrados e ainda nao lancados.

Casas monitoradas: BTG Pactual, Itaú (íon), Nu, Banco Inter, com a XP como baseline
de comparacao.

## Uso

```bash
pip install -r requirements.txt

python -m shelf_monitor doctor       # PRIMEIRO: checa acesso as fontes
python -m shelf_monitor run          # coleta tudo, gera digest e dashboard
```

Na primeira execucao o `run` tambem importa a base de mercado da CVM (~126 MB),
que e de onde vem a gestora oficial e a classificacao comparavel entre casas.
Depois disso o cache condicional resolve com um 304. Para pular, `--no-market` -
mas os relatorios saem sem gestora oficial, e o console avisa.

Saidas de cada execucao:

| Arquivo | O que e |
|---|---|
| `data/reports/prateleiras-<data>.xlsx` | Planilha com todos os fundos (via `export`) |
| `data/reports/dashboard.html` | Dashboard comparativo: visao executiva no topo, cada bloco num painel aberto sob demanda, tema claro (abre no navegador, sem dependencia externa) |
| `data/reports/digest-<data>.md` | Digest de mudancas para leitura no repo |
| `data/reports/digest-<data>.html` | Mesmo digest com CSS inline, pronto para e-mail |
| `data/snapshots/<casa>/<data>.json` | Foto da prateleira (versionada no git) |
| `data/cvm/extract/<data>.json` | Extrato da CVM filtrado pela watchlist (versionado) |

Outros subcomandos:

```bash
python -m shelf_monitor export                    # planilha a partir dos snapshots salvos
python -m shelf_monitor export --collect          # coleta agora e ja exporta
python -m shelf_monitor collect --platform btg --platform inter
python -m shelf_monitor cvm                       # so o radar CVM
python -m shelf_monitor diff                      # recompara os snapshots existentes
python -m shelf_monitor report                    # re-renderiza os relatorios
python -m shelf_monitor insights                  # leitura competitiva das vitrines
python -m shelf_monitor captacao                  # captacao de varejo e grade
python -m shelf_monitor performance               # rentabilidade ponderada por PL e cotistas
python -m shelf_monitor custo                     # taxa contra a categoria de cada fundo
python -m shelf_monitor mandato                   # o que o regulamento autoriza
python -m shelf_monitor familias "SPX GESTAO"     # familias de estrategia de uma gestora
python -m shelf_monitor run --no-carteira         # gaps so por CNPJ, sem o mapa de familias
python -m shelf_monitor watchlist-discover "BTG"  # refina a watchlist de gestoras
python -m shelf_monitor run --git-commit          # commita snapshots e relatorios
python -m shelf_monitor run --send-email          # envia o digest por SMTP
python -m shelf_monitor run --no-market           # nao importar a base se faltar
python -m shelf_monitor run --with-rentabilidade # tambem o retorno por fundo em janelas (opt-in)
python -m shelf_monitor etf-bruto                 # dados brutos de ETF de cada fonte do mapeamento
```

### Planilha

`export` gera um .xlsx com:

| Aba | Conteudo |
|---|---|
| **Resumo** | Contagem por plataforma x classe, exclusivos, gestoras - tudo em formulas sobre o Consolidado |
| **Consolidado** | Todos os fundos de todas as casas, uma linha por fundo, com filtro ativado |
| **Uma por plataforma** | So a prateleira daquela casa |
| **Radar CVM** | Fundos da watchlist, pre-operacionais em destaque no topo |
| **Fontes** | Quanto cada fonte trouxe por casa e quantos fundos so ela enxergou |
| **Coleta** | Status de cada casa - onde conferir se um numero baixo e prateleira enxuta ou fonte parcial |

No Consolidado, cada fundo traz `Fontes` (todas em que apareceu), `Nº de fontes` e uma
coluna Sim/Nao por fonte (`Open Finance`, `Prateleira (site)`, `Area logada`). Da para
filtrar "so no site" ou cruzar plataforma x fonte numa tabela dinamica.

A aba **Fontes** e a que responde se vale manter o scraping de uma casa: se o site nao
traz nada que o Open Finance ja nao tenha, ele so custa tempo de coleta.

### Digest por e-mail

Por padrao o monitor so grava os arquivos - o `digest-<data>.html` tem CSS inline
e nenhum JS, entao pode ser colado num e-mail, enviado por um conector ou aberto
direto. Para envio automatico, configure `SMTP_HOST`, `SMTP_FROM` e `SMTP_TO` no
`.env` e rode com `--send-email`; sem essas variaveis a flag apenas avisa e segue.

O assunto ja carrega a novidade, para ser lido sem abrir:
`[Monitor de fundos] 29/08 - 2 fundos em operação, 5 entradas / 1 saída`.
Entradas, saidas e alteracoes sao da vitrine de varejo - a mesma conta do corpo do
digest, do console, do deck e do dashboard. O dia em que a coleta passa a ver uma
vitrine sai como `nova linha de base: XP Investimentos`, nunca como entradas.

### Fontes com link e texto das saidas

Toda saida diz de onde veio cada numero, com o endereco publico para conferir:

| Saida | Onde ficam as fontes |
|---|---|
| Digest (md, html, e-mail) | secao **Fontes** no fim; link no nome de cada casa, no radar CVM e na abertura de captacao e rentabilidade |
| Dashboard | painel **Fontes** no fim (o rodape e a vitrine levam a ele e o abrem); link na nota do placar da visao executiva e nos subtitulos dos blocos que usam CVM |
| Deck | slide **Fontes**, com hyperlink em cada nome |
| Planilha (fundos e previdencia) | tabela **Fontes** na aba Metodologia; link no bloco de periodo das abas de captacao e rentabilidade |
| Planilha de ETFs | aba **Fontes** |

So entra a fonte que foi usada na execucao (`fontes_do_relatorio`): vitrine de casa
que trouxe fundo de vitrine, Open Finance quando alguma casa veio por ele, e cada
conjunto da CVM quando o bloco que depende dele foi calculado. O registro das fontes
e um so, em `report/shared/fontes.py`; cada entrada tem a pagina do conjunto e,
quando existe, o diretorio dos arquivos que o monitor le.

O texto das saidas segue a norma do portugues do Brasil: acento, crase, concordancia
de numero (`texto.contagem`: "1 alteração", "3 alterações") e separador de milhar
("6.631"). Comentarios e docstrings do codigo seguem em ASCII. Nome de fundo sai
como a fonte publica ("Itau Credito Bancario" fica assim), com uma excecao: texto
decodificado com o charset errado. Em 2026-09-24, 133 dos 466 nomes do Inter estavam
gravados como "DÃ³lar" - o servidor nao declara charset e o `requests` lia UTF-8 como
Latin-1. O `http.get` passou a decodificar certo, e `texto.consertar_mojibake`
repara o que ja estava gravado, na leitura, e compoe os nomes em NFD (4 dos 468 da
Nu), para o conserto nao aparecer como alteracao de nome no diff.

## Pre-requisito de rede

O ambiente onde o monitor roda precisa alcancar os dominios das fontes. Em ambientes
com network policy restritiva, `doctor` mostra exatamente o que esta bloqueado:

```
dados.cvm.gov.br
data.directory.openbankingbrasil.org.br
www.xpi.com.br
investimentos.btgpactual.com
www.itau.com.br
inter.co
nubank.com.br
```

## Fontes e estrategias

**Cada casa e consultada por todas as fontes que ela declara, e a prateleira final e a
uniao.** Nao e a primeira fonte que responde: um fundo que so o site publica ficaria
invisivel, e saber em quais fontes cada fundo aparece e informacao competitiva por si so -
fundo no site e ausente do Open Finance costuma ser oferta nova ainda nao refletida no
dado regulatorio.

Quando duas fontes discordam de um campo, vence a de maior confianca (Open Finance >
area logada > API interna > navegador > HTML), e as demais preenchem so o que faltar.
Uma fonte que falha nao impede as outras; se todas falharem, o snapshot sai `unavailable`
com o motivo de cada uma.

Para uma coleta mais rapida, `--first-source-only` volta ao comportamento de parar na
primeira fonte util - util quando o Playwright, que leva dezenas de segundos por site,
nao compensa.

| Casa | Estrategia | Lista publica | Open Finance |
|---|---|---|---|
| XP | Open Finance (pagina bloqueada por CDN) | `/investimentos/fundos-de-investimento/lista/` -> 403 ate para Chromium | **624** |
| BTG | Open Finance -> **API publica** -> navegador | API `funds/list/v2` da pagina `/produtos`: **693 fundos** na prateleira comercial | **9.950** |
| Itau | Open Finance -> navegador **com janela** | `/investimentos/fundos/rentabilidade`: tabela paginada, **~450 fundos** | **1.751** |
| Inter | scraping (sitemap, **574** fundos) -> Open Finance | paginas estaticas por fundo + `page-data.json` (Gatsby) | via trust store do sistema |
| Nu | Open Finance | app NuInvest encerrado; sem prateleira web deslogada | **478** |

Os numeros sao os medidos pelo `diagnose --browser` na maquina do usuario em
2026-08-31 - nao estimativas.

### O que a evidencia real mostrou, casa a casa

**BTG - o melhor resultado.** O navegador passa (HTTP 200) e a pagina `/produtos` e
alimentada por uma API publica e paginada:
`/services/api/funds-public/public/funds/list/v2?page=N&size=K`. Ela devolve a
**prateleira comercial** (693 fundos na medicao) com taxa (e a unidade dela),
minimo, D+ de cotizacao e liquidacao, gestor, administrador, classificacao ANBIMA,
PL, publico-alvo e ate flag de destaque. E um dado diferente - e complementar -
dos ~9.950 do Open Finance, que listam tudo que a casa administra. O coletor chama
a API direto (`api-json`); se um WAF barrar cliente HTTP simples, o caminho
`playwright` pagina a mesma API por dentro do navegador, com os cookies dele.

**Itau - funciona com o Chrome real.** O Chromium headless leva 403 da Akamai no
proprio documento, mas com `SHELF_BROWSER_HEADED=1` + `SHELF_BROWSER_CHANNEL=chrome`
(o Chrome instalado do usuario, com janela) a lista abre - comprovado na segunda
rodada de evidencia. A tabela e paginada em ~10 fundos por pagina (45 paginas na
medicao); o coletor clica "proxima" ate o fim e mapeia as colunas **pelo cabecalho**
(nome, risco, aplicacao inicial, taxa maxima, resgate), nunca por posicao - as
colunas de retorno ficam de fora por desenho, para rentabilidade nao virar "taxa".
A tabela nao traz CNPJ, entao o merge reconcilia cada fundo com o gemeo do Open
Finance pelo slug exato do nome (FIC/FIRF e afins descartados); o que nao casar
permanece como registro proprio do site. Ha ainda um XHR de ~1,5 MB do gateway
`apicd.cloud.itau.com.br` que deve carregar o dataset inteiro - a captura agora
guarda o corpo dele (era o caso que revelou o bug abaixo) e, se parsear, os campos
entram por cima.

**XP - bloqueio de CDN no proprio documento.** 403 ate para o Chromium, com pagina
de bloqueio propria que indica o e-mail `bloqueio-cdn@xpi.com.br` para pedir
liberacao. Nao ha o que raspar num 403 - e este monitor nao tenta burlar
fingerprinting. As vias: o Open Finance (624 fundos), a liberacao junto a CDN, ou
o modo com janela acima, que tambem vale para a XP.

**Inter - duas correcoes.** (1) A "falha de TLS" do Open Finance nao era do
servidor: o `diagnose` verificou o handshake OK usando o trust store do sistema na
mesma maquina em que o `requests` (que confia so no bundle do certifi) falhava. A
sessao HTTP agora soma os dois stores - verificacao integral mantida, `verify=False`
continua nao existindo aqui. (2) O coletor parava em 400 fundos por um teto
silencioso, com 574 no sitemap; o teto subiu e, se for atingido, vira aviso e
snapshot `partial`. De quebra, cada pagina de fundo tem um `page-data.json`
estatico (o site e Gatsby) que o coletor agora le para completar taxa, minimo e
gestor que o HTML nao entrega.

> **Correcao de rota (2026-08-31).** Duas URLs estavam erradas: a do BTG apontava
> para a raiz institucional (que nao lista nada) em vez de `/produtos`, e a do Itau
> para a area Personnalite, que exige contexto de cliente. O diagnostico na maquina
> do usuario mostrou "SPA vazia" e "403" - parte do que parecia anti-bot era
> endereco errado. A parte que era anti-bot de verdade (XP e Itau) esta documentada
> acima.

A captura pelo navegador le a pagina por tres vias ao mesmo tempo - o trafego JSON
que alimenta a listagem, o estado embutido (`__NEXT_DATA__` e afins) e o DOM
renderizado (linhas de tabela, cartoes repetidos, CNPJ no texto). O DOM entra
sempre, nao so como plano B: um fundo em destaque na tela e ausente do XHR e
justamente o que separa a prateleira comercial da lista bruta da API.

**Open Finance Brasil** e a estrategia preferida onde disponivel: a API de Dados
Abertos de Investimentos (`/open-banking/opendata-investments/v1/funds`) e publica,
sem autenticacao, e entrega CNPJ do fundo, gestor, categoria ANBIMA, taxas, minimo e
prazos de resgate em formato padronizado - sem depender do layout do site. A
completude varia por instituicao, entao o scraping continua como reconciliacao.

> **Paginacao.** O `/funds` e paginado e nem toda instituicao respeita o `page-size`
> pedido - ha quem devolva 10 quando se pede 500, o que multiplica o numero de
> paginas por 50. O coletor registra o `page-size` efetivo e o `totalRecords`, e se
> a trava de seguranca for atingida ele **avisa e marca o snapshot como `partial`**
> em vez de truncar em silencio. Meia prateleira saindo como `ok` seria pior que
> nenhuma: viraria a baseline do dia seguinte e o diff leria a diferenca como churn.

> **Inter: falha de TLS - resolvida sem baixar a guarda.** O `diagnose` provou que
> `api.openbanking.bancointer.com.br` valida pelo trust store do sistema e falha so
> pelo bundle do certifi que o requests usa. A sessao HTTP soma os dois stores
> (mais o `REQUESTS_CA_BUNDLE`, se houver) num unico contexto verificado - hostname
> e cadeia continuam checados, e `verify=False` continua nao existindo aqui.

**CVM**: `registro_fundo_classe.zip` (pos-CVM 175) como fonte primaria, com
`cad_fi.csv` como fallback. O download bruto nao e versionado; so o extrato filtrado
pela watchlist de gestoras.

> Ressalva conhecida: desde out/2024 o `cad_fi.csv` cobre apenas fundos **nao adaptados** a
> RCVM 175, entao usa-lo como fallback (e nao como complemento) deixa esses fundos fora do
> radar quando a fonte primaria funciona. Na pratica o impacto e pequeno - as gestoras da
> watchlist ja migraram - mas a base de mercado (`market`, abaixo) trata os dois arquivos
> como complementares, que e o correto.

## Base geral do mercado (`market`)

Enquanto o radar CVM acima filtra pela watchlist das casas monitoradas, este modulo faz o
oposto: importa o **mercado inteiro** a partir dos dados abertos da CVM, com a hierarquia da
Resolucao CVM 175 preservada.

```bash
python -m shelf_monitor market                 # importa e escreve as tabelas
python -m shelf_monitor market --skip-fees     # pula o extrato (~100 MB) - base sem taxas
python -m shelf_monitor market --force         # rebaixa mesmo sem mudanca na origem
```

Idempotente: sem mudanca na origem, a segunda execucao nao rebaixa nada (requisicao
condicional por ETag/Last-Modified) e reescreve as mesmas tabelas.

### A hierarquia que a RCVM 175 criou

```
fundo        envelope juridico, responsabilidade limitada por classe
  +-- classe        CNPJ proprio, PL, politica, cota  <- o grao central
        +-- subclasse   publico-alvo e condicoes distintas, mesma carteira
```

O grao da base e a **classe**, nao o fundo: e o CNPJ de classe que distribuidores e o informe
diario reportam. Um fundo com duas classes aparece como duas linhas em `mercado.csv`.

Para descer ate o ultimo nivel, use `hierarquia.csv`: uma linha por subclasse, com as colunas
de fundo, classe e subclasse lado a lado (prefixadas `fundo_`, `classe_` e `subclasse_`). E na
subclasse que moram publico-alvo e condicoes comerciais distintas - duas subclasses da mesma
classe partilham carteira e CNPJ e ainda assim sao produtos diferentes na prateleira.

A coluna `nivel` diz o que cada linha e:

| `nivel` | Quando |
|---|---|
| `subclasse` | a classe tem subclasses; uma linha para cada |
| `classe` | a classe nao tem subclasse - a propria classe e a unidade |
| `fundo_legado` | fundo nao adaptado, que nao tem classe nem subclasse |

Assim a tabela cobre o mercado inteiro sem exigir um union de quem consome. As taxas sao as da
classe: subclasses partilham CNPJ e carteira, entao herdam a mesma taxa.

### Tabelas geradas em `data/market/` (gitignored)

| Arquivo | Grao |
|---|---|
| `mercado.csv` | **o consolidado** - uma linha por classe, mais os fundos legados. E o que se abre direto |
| | traz `codigo_cvm` (da classe; do proprio fundo nas linhas legadas) e `codigo_cvm_fundo` (do envelope) |
| `hierarquia.csv` | a visao normalizada completa - uma linha por **subclasse**, com fundo, classe e subclasse lado a lado |
| `fundos.csv` | 1 fundo |
| `classes.csv` | 1 classe |
| `subclasses.csv` | 1 subclasse |
| `taxas.csv` | 1 classe (entrega mais recente do extrato) |
| `_manifest.json` | contagens, fontes, e as colunas novas da CVM ainda nao mapeadas |

### Tres coisas que a base resolve e que quebram pipelines ingenuos

**`cad_fi.csv` nao e mais "o cadastro".** Desde out/2024 ele cobre apenas os fundos que ainda
**nao** migraram para a RCVM 175 - os adaptados sairam dele. Os dois arquivos sao
complementares: a base une os dois e marca a origem em `mercado.csv`.

**A taxa de administracao e polimorfica.** O campo `TAXA_ADM` da CVM carrega ora percentual ao
ano, ora um valor fixo em reais (o regulamento admite texto livre para investidor qualificado),
sem nada que distinga. A base grava a unidade em `taxa_adm_unidade` (`percentual` / `reais` /
`texto`) e preserva o valor original ao lado. Sem isso, um fundo que cobra "R$ 30.000 por ano"
aparece num ranking de custo como se cobrasse 30.000%.

**O CNPJ vem com mascara no extrato e sem no cadastro.** Tudo e normalizado para 14 digitos na
ingestao - e a causa numero um de join vazio nesses dados.

### Classificacao ANBIMA sem pagar por ela

A API de dados da ANBIMA e produto comercial (sandbox devolve dados ficticios; producao exige
contrato). Mas a classificacao de tres niveis - a parte que realmente vale, `Renda Fixa Duracao
Baixa Soberano` em vez do generico `Renda Fixa` da CVM - vem publicada dentro do proprio
cadastro da CVM, na coluna `Classificacao_Anbima` do `registro_classe.csv`. E de la que a base
a le, sem credencial nenhuma.

### Quando a CVM mexer no schema

As colunas sao lidas por nome, nunca por posicao, e o que aparece no arquivo sem estar mapeado
vai para `unknown_columns` no manifesto (e para um aviso no console). A importacao nao quebra -
mas voce fica sabendo que ha dado novo sendo descartado.

### Fora de escopo

Informe diario, composicao de carteira (CDA), lamina e balancete estao mapeados em
`market/datasets.py` mas nao sao baixados: sao series temporais de dezenas de GB, e o uso hoje
pede so cadastro e taxas.

## Monitoramento de ETFs (`etf`)

```bash
python -m shelf_monitor etf                    # catalogo + captacao mes a mes + PL mensal + fluxo semanal
python -m shelf_monitor etf --export           # idem, gravando planilha, relatorios e CSV
python -m shelf_monitor etf --no-fnet-monthly  # sem baixar do Fundos.NET (fechamentos e informe do mes): usa o que esta em disco
python -m shelf_monitor etf --export --anbima "G:\Meu Drive\ETF\Indicadores"  # confere com o boletim da ANBIMA
python -m shelf_monitor run --with-etf         # roda junto da coleta normal (opt-in - baixa ~33MB/mes extra)
```

O PL mensal (balancete de FIE) sai em toda execucao do `etf`: sao ~0,1 MB por competencia, ordens de
grandeza abaixo do INF_DIARIO, e e a unica fonte medida que alcanca o ETF em si - nao so os feeders.

### Captacao de ETF mes a mes (o monitoramento de fluxo)

Toda execucao do `etf` monta a captacao de cada mes, no ano e em 12 meses, nas mesmas visoes da
competicao: gestoras, segmentos, oportunidades e defesa, e cada ETF com o seu segmento. Cada mes
recebe um estado:

| Estado | Quando |
|---|---|
| acelerando | captou mais que no mes anterior, que tambem captou |
| captando | captou |
| virou saida | saiu dinheiro depois de um mes sem saida |
| saida persistente | saiu dinheiro pelo segundo mes seguido, ou mais |
| sem movimento | captacao abaixo de 0,1% do PL do inicio do mes (piso de R$ 100 mil) |
| sem dado | o ETF existia, mas falta o fechamento; antes da estreia o mes fica em branco |

Com `--export`, cinco abas abrem a planilha `etf_captacao_mensal.xlsx` ("Captação - Competição",
"- Segmentos", "- Oport. e defesa", "- por ETF" e "- informe mês a mês"), cada mes na cor do estado; o relatorio sai em
`etf-captacao-<data>.html` e `.md`, e a serie longa por ETF e mes em `etf_captacao_mensal.csv`. A
planilha se chamava `etf_captacao_semanal.xlsx` ate 2026-09-24; o fluxo semanal virou detalhe
dentro dela.

**De onde vem o numero.** Do informe diario de cada ETF no Fundos.NET. A pagina de um informe
(`exibirDocumento`) traz o mes inteiro do fundo - cota, captacao no dia, resgate no dia e PL de
todo dia -, e o `etf` guarda um CSV por mes em `data/etf/fnet_diario/`, uma pagina por ETF por mes
(na primeira vez, os 13 meses da visao: 2.348 paginas em 7 minutos, sem falha, em 2026-09-25). A
captacao liquida do mes e a soma, dia a dia, da captacao menos o resgate declarados, cada dia
conferido contra a diferenca de PL sem a valorizacao - a variacao da quantidade de cotas (PL /
cota), que so muda quando o agente autorizado cria ou resgata cotas. Quando os dois nao fecham
(folga de 5% ou R$ 1 mil), vale a diferenca de PL, e a correcao aparece como "ajuste pela diferenca
de PL". Cada mes sai com captacao, resgates, ajuste, captacao liquida e diferenca de PL (que tem
tambem a valorizacao), no relatorio, nas abas de captacao, na aba "Captação - informe mês a mês"
(cada ETF em cada mes, com PL no inicio e no fim, valorizacao e dias pela diferenca de PL) e no CSV.

Por que conferir cada dia: nos informes de 10 a 18/09/2026, o declarado fechou com a variacao de
cotas em 74 de 84 dias com movimento. O Itau Asset declara zero em parte dos ETFs - o BOVV11 perdeu
600 mil cotas (-R$ 115 mi) em 14/09/2026 com captacao e resgate zerados -, o B-Index LFT (Bradesco)
declarou zero de 10/2025 a 01/2026 com as cotas crescendo (R$ 1,96 bi so no mes de estreia), e os
ETFs de renda com opcoes da Buena Vista declaram resgate com as cotas paradas, o padrao de pagamento
de rendimento. Em 12 meses (set/2025 a ago/2026), 706 de 45.332 dias com informe ficaram com a
diferenca de PL.

Sem a pagina do mes de um ETF (coleta que falhou), vale a conta pelos fechamentos mensais de
`data/etf/fnet_mensal/` - o ultimo informe de cada ETF no mes: a variacao de cotas entre dois
fechamentos, vezes a cota media do mes -, e a liquida desse ETF sai a parte, como "sem o informe do
mes". Os fechamentos continuam sendo baixados (cerca de 220 documentos por mes; na primeira vez, os
14 meses da visao: 2.551 documentos em 3 minutos em 2026-09-24): sao o PL das pontas de cada mes e o
documento de partida da pagina do mes. O capital de cotas do balancete nao serve:
medido em 08/2026, concorda com as cotas no BNP (59 de 63 ETFs), no BTG (48 de 55) e no BB (10 de
10), mas na Vortx em 1 de 13 - no QR Bitcoin o capital subiu R$ 112,7 mi num mes em que as cotas
cairam R$ 3,8 mi -, e no ETF Bradesco IMA-B o capital ficou parado num resgate de R$ 82,7 mi.

Conferido em 2026-09-24: o PL do fechamento e o do balancete em 178 de 179 ETFs (31/07/2026), e a
contagem de ETFs com fechamento bate com a do balancete (219 de 219 em 08/2026, 137 de 137 em
08/2025). Dois consertos vieram dessa conferencia: o leitor do XML passou a aceitar a versao
reapresentada pelo formulario do Fundos.NET, com o CNPJ no cabecalho e o PL num bloco sem CNPJ - sem
isso 18 ETFs do Itau e o da Caixa sumiam do fechamento de 08/2026 -, e o coletor tenta os
documentos de cada fundo do mais recente ao mais antigo ate achar um informe utilizavel. Tres
desdobramentos de cotas em 12 meses (IT NOW S&P500 TRN 1:8 e IT NOW Galaxy Bitcoin 1:3 em 01/2026,
Investo Semicondutores 1:6 em 12/2025) tiveram o fator escolhido pelo retorno dos pares do segmento.

Medido em 2026-09-25 (12 meses de set/2025 a ago/2026, pelo informe diario):

| | |
|---|---|
| Mercado | +R$ 59,8 bi em 12 meses (captacao de R$ 105,0 bi, resgates de R$ 55,8 bi, ajuste de +R$ 10,7 bi pela diferenca de PL; o PL variou +R$ 69,9 bi), +R$ 37,9 bi no ano; captou em todos os 12 meses, acelerando em 6 |
| Ajuste pela diferenca de PL | Itau +R$ 5,7 bi e Bradesco +R$ 4,6 bi (ETFs que declaram zero); nas outras gestoras, no maximo R$ 0,24 bi (BTG) |
| Quem mais captou | BTG +R$ 15,2 bi (25,5%), Itau +R$ 14,4 bi (24,1%), Bradesco +R$ 14,3 bi (23,9%) |
| XP (Trend) | +R$ 3,0 bi (captacao de R$ 6,3 bi, resgates de R$ 3,3 bi, nada ajustado), 5,0% da captacao contra 6,4% do PL - 6a em captacao; virou saida so em 07/2026 |
| Oportunidades | Tesouro Selic e CDI (+R$ 20,8 bi, Bradesco a frente), Inflacao (+R$ 12,3 bi, BTG a frente, XP com 1,7%), Ibovespa (+R$ 6,4 bi) |
| Defesa | China e emergentes (a Investo captou mais que a XP), Prata (XP em saida persistente) |

Pela conta antiga, so com os fechamentos, os 12 meses davam +R$ 59,5 bi: a diferenca e o valor do
dinheiro pela cota do dia em que entrou, em vez da cota media do mes, e os meses com um fechamento
faltando, que o informe do mes resolve.

A tabela do mercado separa, em cada mes, quanto da captacao e de estreia: o monitor conta como
dinheiro que entrou em ETF o que um ETF novo recebe no mes em que estreia (R$ 7,4 bi de 08/2025 a
07/2026).

**Ticker de cada ETF.** O cadastro da CVM nao traz o codigo de negociacao; a lista de fundos
listados da B3 traz. O `etf` refaz `data/etf/tickers.csv` pela API que a propria pagina de ETFs
listados da B3 usa (`fundsListedProxy/Search/GetListFunds` e `GetDetailFund`) quando a ultima
coleta tem mais de 30 dias, e o ticker sai em todas as visoes (por ETF, lancamentos, CSV). Medido
em 2026-09-24: 223 ETFs em quatro tipos ("ETF" 130, "ETF-RF" 65, "ETF-CRIPTO" 26, "ETF-FII" 2),
227 de 252 do catalogo com ticker - faltam 19 pre-operacionais e 6 que a B3 nao lista. O
endpoint que o projeto usava (`fundsProxy/fundsCall/GetListedFundsSIG`) responde com zero
registros para todo tipo, e o COTAHIST nao serve de ponte para renda fixa (nenhum ETF de renda
fixa aparece no arquivo de 08/2026). Uma linha escrita a mao em `tickers.csv` (outra `fonte`)
nunca e sobrescrita pela coleta: e assim que entram os quatro da It Now que a lista da B3 nao
traz - IT NOW NTN-B 2035, 2050 e 2060 (TD3511, TD5011, TD6011) e IT NOW S&P IMA-B (GOAT11),
com `fonte` itnow, pelas paginas de cada ETF em itnow.com.br.

**Conferencia com a ANBIMA.** Com `--anbima` (o anexo .xlsx do boletim de fundos, ou a pasta onde
ele fica; sem a opcao, vale o mais recente de `data/etf/anbima/`), o relatorio ganha a secao
"Conferencia com a ANBIMA" e a planilha a aba "Captação - ANBIMA": mes a mes, a captacao do
monitor, a parte dela que e estreia, a da ANBIMA, a diferenca e a cobertura de PL, com renda fixa e
variavel separadas. O monitor nao baixa nada do ANBIMA Data - os termos do portal proibem acesso
automatizado; le o anexo que a pessoa baixa. Com o boletim de 07/2026, na janela dele (08/2025 a
07/2026):

| | Monitor | ANBIMA |
|---|---|---|
| Captacao em 12 meses | +R$ 58,8 bi (R$ 7,4 bi de estreias) | +R$ 53,3 bi |
| Captacao no ano | +R$ 35,9 bi | +R$ 34,1 bi |
| PL de ETF em 07/2026 | R$ 129,5 bi | R$ 120,4 bi (93%) |
| ETFs em 07/2026 | 213 com PL | 210 classes |

Mes a mes a curva e a mesma - em 10 dos 12 meses a diferenca fica abaixo de R$ 1 bi (medido em
2026-09-25, com a captacao pelo informe diario). A maior e 10/2025: +R$ 2,4 bi, quase toda
estreia - os ETFs da B-Index comecaram com R$ 2,4 bi e a ANBIMA nao contou isso como captacao (os
R$ 3,0 bi da estreia do IT NOW Tesouro Selic, em 12/2025, contou). A diferenca de PL fica em renda
variavel (84% a 87% de cobertura); em renda fixa a ANBIMA tem ate R$ 1,7 bi a mais. Sem a lista de
fundos da base ANBIMA, o fundo a fundo nao fecha. Atencao a janela: o "12 meses" do relatorio vai
ate o ultimo fechamento (08/2026, +R$ 59,8 bi); o boletim de julho, ate 07/2026.

### Competicao em ETFs: gestoras, segmentos e onde a XP compete

Toda execucao do `etf` imprime a competicao; com `--export` (e no `run --with-etf`) ela vai para
a frente da planilha (abas "Competição", "Segmentos", "Oportunidades e defesa", "Lançamentos" e
"ETFs por segmento") e para um relatorio proprio, `etf-competicao-<data>.html` e `.md`, no
formato do digest: leitura, prazos, tabelas e fontes com link. Tudo sai de dados ja em disco -
o PL do balancete de FIE, o cadastro da CVM e os informes do Fundos.NET coletados.

**Segmento, nao categoria.** Dois ETFs competem quando replicam a mesma coisa (BOVA11, BOVV11 e
o Ibovespa da Trend). A CVM deixa vazios `Indicador_Desempenho`, `Classificacao` e
`Classificacao_Anbima` nas 252 classes FIIM do cadastro (medido em 2026-09-24), entao
`market/etf_competicao.py` le o que cada ETF replica do nome, com regras em ordem - opcoes antes
de tudo, cripto antes de Nasdaq, debenture incentivada antes de IPCA, duration-alvo antes de
Treasury, hibrido de infraestrutura e familia IDEX B (NTN-B da XP: "TREND NTNB35" na B3) antes
de credito. 251 dos 252 ETFs caem em um de 30 segmentos
(recontado em 2026-09-24); o que sobra e um "BB ETF 0000"
pre-operacional, sem PL.

**Participacao dentro do segmento e fluxo.** O balancete nao separa captacao de valorizacao, mas
dentro de um segmento a valorizacao e a mesma para todos: a variacao de participacao no segmento
entre a primeira e a ultima competencia e a leitura de fluxo que o balancete permite (menos
exata em segmento que junta indices diferentes, como Inflacao).

Medido em 2026-09-24 (PL de ago/2026, R$ 135,4 bi em 219 ETFs; comparacao com jun/2026):

| | |
|---|---|
| Posicao da XP (Trend) | 6a gestora, R$ 8,7 bi (6,4%); lider Itau Asset (It Now), 27,8% |
| Lidera | Ouro (78%), Nasdaq e tecnologia (48%), China e emergentes (47%), Prata (96%), FII (82%) |
| Sem ETF | Credito privado (R$ 3,3 bi, 3 da XP em lancamento), Dividendos (R$ 2,9 bi), Cesta cripto (R$ 2,9 bi), IBrX (R$ 1,4 bi) |
| Pequena onde o dinheiro esta | Ibovespa 5,1% (5a de 8), Tesouro Selic 0,1% (8a de 8), Inflacao 0,9% (6a de 10), S&P 500 5,0% (4a de 6) |
| Dentro do segmento | Acoes globais +3,7 pp, China e emergentes +2,9 pp, Dolar -2,8 pp |
| Lancamentos | 24 ETFs pre-operacionais, 6 da XP; concorrentes concentrados em credito privado (5 gestoras). Depois do balancete de 31/08, 8 ETFs comecaram a operar (R$ 266 mi no Fundos.NET), 2 da XP |
| Cotistas | 15,6% dos cotistas de ETF contra 6,4% do PL: R$ 30 mil por cotista, contra R$ 72 mil no mercado |

Conferido contra a B3, que publica o mercado de ETF por outra via (custodia em bolsa):

| | Monitor (CVM e Fundos.NET) | B3 |
|---|---|---|
| jun/2026 | R$ 125,6 bi em 204 ETFs (balancete de 30/06) | R$ 126 bi e 204 ETFs listados ([Bora Investir, 30/07/2026](https://borainvestir.b3.com.br/tipos-de-investimentos/renda-variavel/etfs/b3-ultrapassa-marca-de-200-etfs-listados-e-estoque-financeiro-dobra-em-um-ano/)) |
| ago/2026 | 219 ETFs com PL (balancete de 31/08) | 219 produtos locais (ETF Day da B3 Week, 16/09/2026, via [Gazeta Mercantil](https://gazetamercantil.com/etfs-b3-929-mil-investidores-patrimonio-142-bilhoes)) |
| meados de set/2026 | R$ 141,8 bi em 222 ETFs (ultimo informe de cada um, ate 17/09) | R$ 142 bi, apresentados em 16/09 (mesma fonte) |
| BOVA11 | R$ 15,49 bi em 17/09 (Fundos.NET) | R$ 15,51 bi em 23/09 ([pagina do fundo na BlackRock](https://www.blackrock.com/br/products/251816/ishares-ibovespa-fundo-de-ndice-fund)) |

Com o grupo 6, que o monitor lia ate 2026-09-24, ago/2026 dava R$ 130,5 bi. A soma de cotistas
por ETF (1,88 mi nos informes de 10 a 18/09) e o dobro dos 929 mil investidores unicos que a B3
contou em agosto - por isso toda participacao em cotistas sai com o aviso de que quem tem dois ETFs
conta duas vezes.

Quatro regras de leitura que o relatorio escreve junto do numero: cotistas sao somados por ETF
(quem tem dois conta duas vezes) e vem do maior valor da janela de informes, porque o Fundos.NET
traz erro de preenchimento que derruba o numero num dia; estreia e ETF sem PL na primeira
competencia e com PL na ultima - a data de registro nao serve, a migracao para a RCVM 175 a
reescreveu (o iShares Ibovespa, de 2008, aparece registrado em 2025-04-23); ETF que comecou a
operar depois do ultimo balancete (nenhum PL na serie, PL num informe posterior a data-base) entra
nos lancamentos com o PL e a data do ultimo informe, fora das somas de PL, que sao todas da data
do balancete - em 2026-09-24 eram 8, que o radar nao mostrava; e o fluxo publicado soma os informes
depois do primeiro de cada ETF, cuja captacao ja esta no PL do dia-base.

Fora, por falta de fonte: taxa de administracao (o extrato da CVM nao cobre FIIM - nenhuma linha
de ETF em `taxas.csv`) e liquidez em bolsa (o COTAHIST precisa do ticker, que so a coleta da B3
- `etf --collect-sources b3` - liga ao CNPJ).

**ETF e o tipo estrutural FIIM, nao uma palavra no nome.** Na CVM, ETF e Fundo de
Investimento em Indice de Mercado (FIIM) - um tipo proprio de `Tipo_Fundo` (cadastro legado) ou
`Tipo_Classe` (RCVM 175, `"Classes de Cotas de Fundos FIIM"`). Catalogar por palavra-chave no nome
("ETF", "Fundo de Indice") erra nos dois sentidos: perde ETFs de nome abreviado ("BTGP B3
IBOVESPA F DE INDICE") e confunde com *feeders* - FIFs comuns que so replicam um ETF ("BTG
PACTUAL REFERENCE ETF FIA" e um FIF, nao um ETF). Por isso `market/etf.py` decide pelo tipo
primeiro e usa palavra-chave so como sinal secundario (obrigatorio no cadastro legado, que nao
tem `Tipo_Classe`; e o que marca um feeder). Todo registro sai com um `kind` explicito -
`"etf"` ou `"feeder"` - e os dois nunca somam juntos: seria contar o mesmo dinheiro duas vezes.

**Categoria vem do benchmark, nao do nome comercial.** `Indicador_Desempenho` (o que o ETF
replica - IBOVESPA, IMA-B, S&P 500...) e testado antes do nome e da classificacao CVM/ANBIMA -
e o dado mais confiavel para separar Renda Fixa (IMA-B, IRF-M, LFT, CDI...) de Acoes Brasil
(IBOVESPA, SMLL, IDIV...), Internacional (S&P 500, NASDAQ, MSCI...), Cripto e Multimercado.

**Cobertura de fluxo e parcial - e o console diz o quanto.** O INF_DIARIO da CVM (`inf_diario_fi`)
e o informe de FI/FIF; fundos de indice (FIIM) tem arquivos proprios (`cda_fiim` na carteira,
balancete em "Fundos Estruturados"), fora do escopo desta versao. Na pratica isso significa que a
captacao diaria de um ETF de verdade normalmente nao aparece no INF_DIARIO - so a dos *feeders*
aparece, por serem FI/FIF normais. Por isso o comando sempre mostra primeiro o **catalogo completo**
(todo ETF cadastrado, por categoria - a resposta a "o monitor enxerga todos os ETFs do mercado,
incluindo os de Renda Fixa?") e so depois o fluxo semanal, com uma linha de cobertura explicita
("N de M ETFs com fluxo no INF_DIARIO") em vez de deixar a lacuna passar em silencio.

**O PL do ETF em si vem do balancete de FIE** (`market/fie_balancete.py`), medido em 2026-09-19:
219 dos 249 ETFs do catalogo (88%), contra **0 de 249** no INF_DIARIO. Os ETFs
nao tem diretorio proprio nos dados abertos - nao existe `/dados/FIIM/` nem `cda_fiim_*`: eles vivem
sob FIE (Estruturados), em `/FIE/DOC/BALANCETE/DADOS/balancete_fie_{AAAAMM}.zip` (~0,1 MB por
competencia) e tambem no membro `cda_fie_{AAAAMM}.csv` de `/FI/DOC/CDA/DADOS/cda_fi_{AAAAMM}.zip`
(204/249). O balancete e a escolha: cobre mais e pesa 150x menos.

**O PL e Ativo - Passivo, nao o grupo 6.** Ate 2026-09-24 o modulo lia a conta 60000002 (grupo 6)
como PL. Ela e o PL *apurado* - capital de cotas mais resultado acumulado -, sem o resultado do
exercicio em curso, que fica nas receitas (grupo 7) e despesas (grupo 8) ate a apuracao. Contra o
informe diario do Fundos.NET da mesma data (31/08/2026), o grupo 6 acertou 1 de 181 ETFs e somou
3,35% a menos; Ativo (10000007) - Passivo (40000008) acertou os 181, com diferenca abaixo de 0,01%
e R$ 97,63 bi nas duas fontes. O grupo 6 ainda congelava: 79 ETFs tinham o mesmo valor em 07 e
08/2026, porque sem captacao ele nao se mexe. Com a correcao, o PL de ETFs de ago/2026 foi de
R$ 130,5 bi para R$ 135,4 bi. As contas do plano COFI foram identificadas por medicao, e a
identidade `10000007 - 40000008 = 60000002 + 70000009 + 80000006` virou guarda no codigo: fecha em
todos os ETFs de 06, 07 e 08/2026 com desvio relativo abaixo de 1e-5, e se a CVM renumerar o plano
o registro sai marcado como suspeito em vez de publicar um PL que e outra coisa. A abertura do
grupo 6 (`60000002 = 61100004 + 61800005`) nao serve de guarda: em 06/2026 cinco ETFs da Vortx
traziam outras subcontas nele, com o PL certo.

O balancete **nao** entrega captacao separada da valorizacao por ETF. Com o PL certo, a variacao
do capital de cotas parece ser o fluxo - em Tesouro Selic, 08/2026, o ETF que perdeu 23,8% do
capital rendeu 1,08% pela conta, como os que nao tiveram fluxo (1,09% a 1,10%) -, mas nao em todo
administrador: num ETF da S3 Caceis o resultado e incorporado ao capital todo mes, e num ETF de
bitcoin da Vortx a conta da 1,0% no mes em que os outros oito ETFs de bitcoin renderam de 27,7% a
28,9%. Sem uma guarda por ETF, publicar a variacao do capital como captacao daria mercado por
fluxo nesses casos; por isso a coluna se chama variacao de PL. **Quem separa e o informe diario
do Fundos.NET** - ver abaixo. A captacao e o resgate publicados nele batem com a variacao de PL e
cota em 99 de 107 pares de dias com movimento (medido em 2026-09-24).

### Cota, cotistas e a separacao entre captacao e valorizacao (`--flow-source fnet`)

O informe diario de ETF no Fundos.NET traz, por fundo e por dia: `PATRIM_LIQ`, `VL_QUOTA`,
`CAPTC_DIA`, `RESG_DIA` e `NR_COTST`. Medido em 2026-09-19: **853 informes, 223 dos 249 ETFs (90%)**
- contra 0 no INF_DIARIO. Captacao e resgate vem **publicados**, nao derivados, entao a conta fecha:

    variacao_pl = captacao_liquida + valorizacao

A relacao foi verificada dia a dia no informe (`PL(D) = PL(D-1) + captacao(D) - resgates(D) +
valorizacao(D)`), e e dela que vem uma correcao importante no agregador: a captacao do dia usado como
PL inicial **ja esta dentro desse PL**. Somar aquele dia contava o mesmo dinheiro duas vezes - num
ETF de Selic isso dava captacao de R$ 705 MM contra variacao de PL de R$ 405 MM e valorizacao por
diferenca de -R$ 300 MM (queda de 19% num fundo de Selic, que nao acontece). Com a janela do fluxo
alinhada a do PL, a valorizacao fica R$ 1,9 MM contra R$ 1,87 MM que a cota implica.

Duas guardas nascem da mesma logica, porque a identidade fecha por construcao e por isso nao prova
nada sozinha:

| Guarda | O que pega | Como |
|---|---|---|
| `decomposicao_suspeita` | semana com dia faltando na coleta | a cota da uma leitura da valorizacao independente do fluxo; divergencia acima de 2% do PL inicial marca o registro |
| `cotistas_suspeito` | erro de preenchimento na origem | queda de mais de metade dos cotistas com o PL praticamente parado. Caso real: em 2026-09-15 varios fundos de uma mesma casa declararam ~850 cotistas contra ~50 mil do dia anterior, com o PL intacto - somados, faziam o saldo semanal dar -245 mil cotistas. Registros marcados ficam fora do ranking e do saldo |

O `etf` imprime, alem da captacao: **Ranking de Valorizacao** (retorno da cota lado a lado com a
valorizacao em reais, captacao liquida e variacao de PL) e **Evolucao de Cotistas** (inicio, fim,
variacao e saldo da semana). As colunas `Variacao PL`, `Valorizacao`, `Decomposicao Suspeita` e
`Cotistas Suspeito` vao para o CSV e para a aba "Detalhamento Completo".

**Ressalva que sai junto do numero:** o retorno e da cota patrimonial, ex-provento. O informe diario
nao publica provento pago, entao um ETF que distribui aparece abaixo do retorno total do investidor -
reincorporar exigiria uma segunda fonte de proventos, que nao esta implementada.

O acesso ao Fundos.NET tambem estava barrado por uma exigencia que o servidor nao faz: o codigo
procurava um `csrf_token` numa atribuicao literal de script. O token real vive numa
`<meta name="_csrf">` que a pagina **publica nao emite** - o proprio JS do site le com fallback vazio
-, e os XHR respondem HTTP 200 sem o header. Medido em 2026-09-19.

**Rentabilidade implausivel e descartada, nao publicada.** O informe mensal e "atualizado
diariamente com retificacoes" (a propria CVM documenta isso), entao o mesmo CNPJ pode aparecer
duas vezes na mesma data dentro do arquivo do mes - sem tratar isso, duas versoes do mesmo dia
eram lidas como dois pregoes e a cota comparava valores incompativeis. `etf_flow.py` agora
deduplica por (CNPJ, data) e, mesmo assim, descarta (marca `data_suspeita`) qualquer retorno
semanal acima de um teto plausivel por categoria (heuristica, nao fato de mercado) em vez de
publicar um numero que nao e real.

A planilha (`etf --export`) traz o catalogo completo numa aba propria ("Catalogo FIIM", com
Renda Fixa incluida) e a coluna "Tipo" (ETF/Feeder) nas demais abas de fluxo.

**Ticker de negociacao.** O cadastro da CVM nao traz o codigo de negociacao em bolsa (BOVA11,
IVVB11...), e a maioria dos ETFs tambem nao o escreve no proprio nome cadastral - so `extract_ticker`
consegue algo como "IT NOW IVVB11 FI EM COTAS DE ETF". `data/etf/tickers.csv` (`cnpj;ticker;...;fonte`)
e o join CNPJ -> ticker: a coleta da B3 (abaixo) o preenche com `fonte=b3`, e linhas escritas a mao
(qualquer outra `fonte`) nunca sao sobrescritas por uma coleta. Ele vem vazio de proposito - uma
lista de CNPJ/ticker escrita de memoria, sem fonte para validar, contaminaria tudo rio abaixo.

### As fontes que cobrem ETF de verdade (`etf --probe-sources`)

O INF_DIARIO nao e o fim da linha. Tres fontes publicas, sem autenticacao, cobrem o que ele nao
cobre - cada uma um pedaco:

| Fonte | O que da | Frequencia | Modulo |
|---|---|---|---|
| **Fundos.NET** (`fnet.bmfbovespa.com.br`) | o **informe diario do ETF**: PL, cota, cotas emitidas, cotistas - e o canal pelo qual fundos de indice entregam documentos a CVM, com tipos de fundo `ETF` (3) e `ETF RF` (4) | diaria | `market/fundosnet.py` |
| **B3 fundos listados** (`sistemaswebb3-listados.b3.com.br`) | ticker, CNPJ, classificacao, cotas aprovadas de cada ETF listado - e o que preenche `tickers.csv` | cadastro | `market/b3_funds.py` |
| **COTAHIST** (`bvmf.bmfbovespa.com.br/InstDados/SerHist/`) | preco, volume financeiro, negocios e quantidade por pregao - liquidez, nao patrimonio | diaria | `market/cotahist.py` |

O **balancete mensal de FIE** saiu do escopo futuro e esta implementado (ver acima). Seguem fora do
`etf`, mapeadas: a **carteira mensal** (membro `cda_fie_AAAAMM.csv`, o que cada ETF carrega - o PL ja
vem do balancete, entao isto interessa para composicao, nao para tamanho; o `etf-bruto` a captura),
o **Boletim de Fundos da
ANBIMA** (captacao liquida de ETF por tipo, mensal, planilha) e a API de ETFs da ANBIMA Developers
(comercial). Sites de terceiros (StatusInvest
e afins) ficam de fora: nao sao fonte oficial e o uso automatizado depende dos termos deles.

**Evidencia, nao suposicao.** Os endpoints, parametros e formatos de resposta da B3 e do Fundos.NET
vem do codigo aberto da biblioteca [`mercados`](https://github.com/PythonicCafe/mercados) (Pythonic
Cafe), que usa exatamente esses caminhos para FII, FIP, FI-Infra e FI-Agro; o layout do COTAHIST
tambem. Duas coisas **nao** tem evidencia e a sondagem existe para fecha-las: o `typeFund` dos ETFs na
B3 (descoberto varrendo candidatos e reconhecendo o tipo pelo CNPJ contra o catalogo FIIM) e o
esquema do XML do informe diario de ETF (o parser e tolerante: reconhece os nomes do padrao XML de
informe diario da CVM - `VL_PATRIM_LIQ`, `VL_QUOTA`, `CAPTC_DIA`, `RESG_DIA`, `NR_COTST` - e
variantes, e devolve o resto em `unknown_tags`, como o importador de mercado faz com colunas novas).
Nada disso pode ser exercitado contra a fonte real no ambiente em que foi escrito (rede bloqueada).

```bash
python -m shelf_monitor etf --probe-sources                 # 1. sonda as tres fontes, grava a evidencia
python -m shelf_monitor etf --collect-sources b3,fnet,cotahist   # 2. foto do dia (tickers, informes, cotacoes)
python -m shelf_monitor etf --flow-source fnet --export     # 3. fluxo semanal a partir dos informes
```

A sondagem grava tudo em `data/cache/debug/etf-sources/` (`diagnostico-etf.json` consolida) e
responde, linha a linha: o INF_DIARIO em cache tem alguma linha de FIIM (fase 0 do diagnostico); o
Fundos.NET lista `ETF`/`ETF RF` e qual e o id do "Informe Diario" para eles; quantos informes sairam
nos ultimos dias e, dos XML baixados, **quais campos o parser reconheceu e quais tags ficaram sem
mapa**; quais `typeFund` da B3 devolvem ETF; e quantas cotas de fundo o COTAHIST do ultimo pregao
traz. Se a linha `fundosnet/xml` vier com `unknown_tags` e sem `pl`/`cota`, o mapa de tags em
`market/fundosnet.py` (`_TAG_MAP`) e o unico lugar a mexer - com o XML salvo ao lado.

A coleta grava fotos versionaveis e pequenas: `data/etf/b3/<data>.json`, `data/etf/fnet/<data>.csv`
(um informe por ETF por data-base; o XML bruto fica no cache) e `data/etf/cotahist/<data>.csv` (so
os tickers conhecidos). Com `--flow-source fnet`, o fluxo semanal passa a vir desses informes - com
captacao e resgate publicados quando o informe os traz, ou derivados de `(cotas hoje - cotas ontem)
x cota` quando so ha cotas emitidas (a coluna `flow_source` do registro diz qual foi a via) - e o
resto do pipeline (semanas, categorias, planilha, guarda de sanidade) e o mesmo.

Sem `--flow-source`, o padrao e `auto`: Fundos.NET quando ha ao menos 10 datas de informe em
disco (duas semanas de pregao), senao INF_DIARIO - com um aviso em vermelho de que o fluxo e dos
feeders, nao do ETF, e o titulo da planilha dizendo o mesmo. Em 23/09 a unica coleta em disco
trazia 7 datas (10 a 18/09); `etf --collect-sources fnet` a cada pregao fecha a serie.

Desde 2026-09-25 o fluxo semanal sai tambem do informe do mes - a pagina de cada ETF, a mesma da
captacao mes a mes. `fundosnet.read_fnet_csvs` le tres origens, uma linha por (CNPJ, data), da que
menos vale para a que mais vale: os XML de `data/etf/fnet/`, a pagina do mes em aberto
(`data/etf/fnet_diario/<mes>-parcial.csv`, refeita uma vez por dia pelo `etf`, com o documento de
partida vindo da busca dos ultimos 10 dias) e a do mes fechado. A janela semanal e a dos ultimos 3
meses, a mesma do INF_DIARIO; com 13 meses de informe em disco, o `auto` escolhe o Fundos.NET e o
aviso de feeder deixa de aparecer. Cada dia confere contra a variacao de cotas pela mesma regra da
captacao mes a mes (`fundosnet.ajustes_do_informe`): o que a diferenca de PL corrige vai para a
coluna "Ajuste pela diferenca de PL" (CSV, aba "Detalhamento Completo" e tabela por categoria), e a
captacao liquida da semana e captacao - resgates + ajuste. A competicao le so as 7 datas mais
recentes (`fundosnet.informes_recentes`): cotistas e captacao liquida da janela, tambem conferida.

Ate 2026-09-24 a coleta do COTAHIST gravava zero linha de ETF: o filtro aceitava so o codigo BDI
02 (lote padrao de acao), e ETF negocia com BDI 14 (FII com 12). O filtro aceita os tres desde entao.

### Dados brutos de ETF, fonte a fonte (`etf-bruto`)

O extrator do mapeamento "Indicadores de ETF e a melhor origem de cada um" (set/2026): captura, sem
calcular nada, o que cada fonte publica sobre os ETFs, guarda como veio e mede, indicador a
indicador, quantos ETFs listados ja tem o insumo em disco. O derivado (mediana de 63 pregoes, premio
ou desconto, tracking difference) e a etapa seguinte. Roda a mao - nao entra no `run_monitor.bat`.

```bash
python -m shelf_monitor etf-bruto                                          # todas as fontes padrao
python -m shelf_monitor etf-bruto --fontes b3-arquivos --desde 2025-01-02  # historico mais longo da B3
python -m shelf_monitor etf-bruto --fontes cotahist --desde 2020-01-01     # COTAHIST anual (opcional)
python -m shelf_monitor etf-bruto --cobertura                              # so a cobertura, sem rede
python -m shelf_monitor etf-bruto --listar-fontes                          # fontes, captura e o que ha em disco
```

| `--fontes` | Fonte do mapeamento | O que fica em `data/etf/bruto/` |
|---|---|---|
| `b3-fundos` | B3, fundos listados (extra) | `b3_fundos/<data>.json`: lista e detalhe de cada ETF como a API devolve; atualiza `tickers.csv` |
| `cvm-cadastro` | CVM, cadastro (extra) | `cvm_cadastro/<data>/registro_{classe,fundo}_etf.csv`: linhas FIIM com todas as colunas (custodiante, auditor, constituicao) |
| `b3-arquivos` | B3, portal de arquivos | `b3_arquivos/instrumentos/<data>.csv` e `b3_arquivos/negocios/<data>.csv`: linhas de ETF com as colunas originais e `x_tipo_linha`, `x_ticker_base`, `x_criterio` |
| `fnet-informe` | FNET, informe diario | `fnet_informe/xml/<data>.zip` (o XML de cada documento) e `fnet_informe/<data>.csv` (cada tag vira coluna, com o nome original) |
| `fnet-mensal` | FNET, informe diario | o fechamento de cada mes em `data/etf/fnet_mensal/` (o mesmo coletor do `etf`) |
| `fnet-documentos` | FNET, regulamento e demonstracoes | `fnet_documentos/indice.csv` (todos os documentos, com link) e `fnet_documentos/regulamento/<cnpj>/<doc_id>.pdf` |
| `regulamento` | Regulamento | o texto de cada PDF (`<doc_id>.txt`) e a planilha de conferencia `data/etf/manual/regulamento.csv` |
| `cvm-cda` | CVM, carteira mensal (extra) | `cvm_cda/cda_fie_etf_<AAAAMM>.csv` (e o `_confid_` quando houver) |
| `indices-b3` | Provedor do indice | `indices_b3/serie/<indice>/<ano>.json` e `indices_b3/carteira/<indice>/<data>.json` |
| `gestoras` | Gestora, arquivo de composicao | `gestoras/<ticker>/<data>.<ext>` (URLs de `manual/gestoras_urls.csv`) e `gestoras/_entrada/` |
| `comdinheiro` | Comdinheiro | `comdinheiro/api/<data>/<consulta>.json` e `comdinheiro/entrada/` |
| `cotahist` (so quando pedida) | B3, COTAHIST | `cotahist/<ano>.csv`: os 26 campos do layout, como texto |

Cada execucao grava um manifesto em `bruto/_execucoes/` (o que foi pedido, o estado de cada fonte e
cada arquivo com sha256, linhas e URL de origem - nunca token nem senha), refaz
`data/etf/universo.csv` (um ETF por linha: CNPJ, ticker, ISIN, categoria B3, liquidacao, tipo) e
mede a cobertura em `data/etf/cobertura_bruto.csv`. Uma fonte que falha nao derruba as outras.
`bruto/` e `entrada/` sao gitignored (reproduziveis na origem; o dado da Comdinheiro e licenciado);
`universo.csv`, `cobertura_bruto.csv` e `manual/` sao versionados. Captura e idempotente: o que ja
esta em disco nao e baixado de novo (`--forcar` refaz). A primeira execucao completa (medida em
2026-09-25) baixou ~1,5 GB da B3 em 6 minutos - 266 pregoes de negocios de 25/08/2025 a 24/09/2026,
dos quais ficaram 6,8 MB de linhas de ETF - e 555 regulamentos (3 por fundo, ~300 MB).

**O que foi medido nas fontes (2026-09-24) e mudou o desenho:**

- ETF de renda fixa **nao esta no COTAHIST** (0 de 65) nem nas categorias "ETF" do cadastro de
  instrumentos: e `FIXED INCOME TRADABLE INSTRUMENT T1`, segmento FORWARD, liquidacao **D+1** (o
  mapeamento diz D+2, que vale para ETF de acoes e de indice estrangeiro). Os negocios consolidados
  trazem os 68 - por isso eles sao a serie de preco do extrator, e o COTAHIST fica opcional.
- **Lote de bloco e um ticker a parte** (ACWI11M, BOVA11Q; segmento "Equity Block Trading Lot"). A
  linha fica, marcada `bloco`, para a mediana "sem lotes de bloco" poder exclui-la.
- O portal de arquivos responde **400** para dia sem pregao e para dia futuro. Cinco dias uteis
  seguidos com 400 sao o comeco do historico, nao feriado. Os 12 feriados do periodo (20/11,
  Natal, Ano-Novo, Carnaval, Sexta-feira Santa, Tiradentes, 1o de maio, Corpus Christi, 7 de
  setembro) sairam assim, sem calendario escrito a mao.
- Em **6 dos 266 pregoes** o consolidado que a B3 publica veio **sem o mercado a vista** - so
  derivativos (12/12/2025, 23/12/2025, 11/02/2026, 09/04/2026, 14/04/2026 e 14/05/2026). Gravar
  esses dias seria dizer que nenhum ETF negociou: ficam em `b3_arquivos/_incompletos.json`, fora do
  disco, e so `--forcar` os pede de novo. A cobertura tolera ate 3% de pregoes faltando numa janela,
  desde que as pontas existam, e lista as datas.
- A API de fundos listados da B3 erra o codigo de negociacao em alguns casos ("T10R0" para o
  T10R11): o universo liga o cadastro de instrumentos ao CNPJ pelo acronimo (`Asst`), nao pelo ticker
  da API; depois pelo nome igual ao da CVM e, so entre ETFs que negociam e sem disputa, pelo nome
  abreviado da B3 contido no da CVM (AGGX11, USDB11). A coluna `ligacao` diz qual regra ligou; um ETF
  que nenhuma liga (BNDX11, cujo nome na B3 diz "FII") se resolve com uma linha manual em
  `tickers.csv`.
- **ETF cancelado segue no cadastro da B3**, com inicio de negociacao 9999-12-31: 20 dos 23 que so
  existiam no cadastro de instrumentos estavam cancelados na CVM entre 2023 e 2025 (XBIO11, USAG11,
  INDA11, NOGV11...), sem nenhum negocio. Ficam no universo com `listado=nao` - o universo de
  25/09/2026 tem 231 ETFs listados.
- A B3 reinicia o "inicio de negociacao" (o BOVA11, de 2008, aparece com 16/07/2026): a idade de um
  ETF na cobertura vem da primeira foto do cadastro de instrumentos em que ele aparece.
- No Fundos.NET o CNPJ vem nulo na busca, mas o arquivo baixado se chama `<CNPJ>-...`: e dali que o
  regulamento e ligado ao ETF. Filtrar a busca por CNPJ estoura o tempo, e a primeira pagina de uma
  busca leva ate 60 s - a sessao do extrator tem timeout minimo de 180 s, pausa entre requisicoes e
  nova tentativa em 429/5xx.
- Os sites de gestora testados (XP Asset, BlackRock) respondem 403 a cliente HTTP: a carteira diaria
  entra pela pasta `data/etf/entrada/gestoras/` (arquivo baixado no navegador, com o ticker no comeco
  do nome) ou por URL em `manual/gestoras_urls.csv`. A carteira mensal padronizada vem da CVM
  (`cda_fie`, ~205 ETFs em 08/2026).
- O endpoint de eventos do portal de fundos listados responde 404 para ETF: proventos e DY ficam com
  a Comdinheiro, como o mapeamento recomenda.

**Planilhas que gente preenche (`data/etf/manual/`).** `xp_cadastro.csv` (classe XP, risco,
suitability, aceita investimento mensal, categoria da regua), `indices.csv` (indice de referencia,
codigo B3, provedor, com ou sem cambio), `formador.csv` (formador, spread garantido ate N cotas),
`regulamento.csv` (conferencia do regulamento), `gestoras_urls.csv` e `comdinheiro_consultas.csv`.
Colunas de sistema (ticker, nome, sugestao por triagem do nome) sao refeitas a cada execucao; as
humanas nunca sao escritas pelo codigo; ETF que sai do universo com algo preenchido fica, marcado
`no_universo=nao`. Planilha aberta no Excel nao derruba a execucao, e CSV salvo pelo Excel (cp1252)
e lido.

**Regulamento, com sugestao e trecho.** Os tres regulamentos mais recentes de cada ETF viram texto
(`pypdf`, opcional) e regras de expressao regular sugerem taxa de administracao, taxa maxima, taxa do
fundo investido, custos fora da taxa, indice de referencia, publico-alvo, PMRC, politica de
distribuicao e taxa de performance - com a pagina e o trecho, uma linha por ETF e campo. Cada campo
vem do regulamento mais novo que tem candidato para ele: o de 05/2026 do GPUS11 era so a parte
geral, sem a taxa, que estava no de 05/2025. As regras aceitam o espaco que o `pypdf` poe no meio da
palavra ("T AXA GLOBAL", no do LTBX11), e o indice definido entre parenteses (`NASDAQ 100
("Indice")`) vem antes de qualquer mencao solta. So conta como dado quando alguem preenche
`valor_conferido` e `conferido_por`; um regulamento novo depois da conferencia marca
`regulamento_mudou=sim`.

**Comdinheiro.** As consultas ficam em `manual/comdinheiro_consultas.csv` (nome, URL da ferramenta
copiada da tela - `{dia}` e `{desde}` viram ddmmaaaa -, ativo). Com `COMDINHEIRO_USUARIO` e
`COMDINHEIRO_SENHA` no `.env`, cada consulta vai por POST ao endpoint da API e a resposta e gravada
como veio; `COMDINHEIRO_API_URL` troca o endpoint padrao, que nao pode ser conferido sem assinatura.
Sem credencial, so a pasta `data/etf/entrada/comdinheiro/` e lida (exportacao manual). A senha so
vai no corpo do POST.

**Planilha consolidada: uma linha por ETF.** Toda captura termina com
`data/reports/etf-consolidado-<data>.xlsx` (e o CSV `data/reports/etf_consolidado.csv`, com as mesmas
colunas); `etf-bruto --consolidar` refaz so a planilha a partir do disco, sem rede. A aba "ETFs" tem
um ETF por linha - os que negociam primeiro, depois listados sem negociacao, pre-operacionais (so na
CVM) e cancelados (so no cadastro da B3) - e 77 colunas agrupadas nas 8 secoes do mapeamento:

- o que a fonte publica vai como veio (ISIN, CNPJ, categoria B3, PL, cotistas, liquidacao...);
- o derivavel e calculado sobre o que esta em disco, com a janela no cabecalho da coluna: mediana de
  volume e de negocios em 63 pregoes (dia sem negocio como zero, sem lote de bloco), presenca,
  premio ou desconto no mesmo dia, rentabilidade de preco em 12 meses e no ano (fechamento contra
  fechamento, sem proventos), rentabilidade desde a estreia dos ETFs novos, rentabilidade da cota em
  12 meses (fechamentos mensais do FNET), retorno do indice B3 e diferenca para ele, volatilidade e
  queda maxima de 12 meses, beta contra o Ibovespa (ETF de acoes Brasil), numero de ativos,
  concentracao nas 10 maiores, maiores posicoes, paises e o ETF de maior sobreposicao (carteira da
  CVM), captacao liquida de 30 dias e idade;
- **o que so existe em PDF sai como "pendente leitura pdf"**: taxa de administracao e maxima, taxa do
  fundo investido, custos fora da taxa, taxa por faixa de PL, custo total, taxa de performance, receita
  de aluguel, indice de referencia, publico-alvo, politica de distribuicao e, nos ETFs de renda fixa,
  PMRC, duration e a aliquota do IR. Quando uma pessoa confere o valor em `manual/regulamento.csv`
  (`valor_conferido` e `conferido_por`), a planilha passa a mostrar o valor;
- o que e da casa (classe XP, risco, aceita investimento mensal, formador) sai de `data/etf/manual/`
  ou como "pendente cadastro"; o que so a Comdinheiro tem (DY, VaR), como "sem Comdinheiro".

As abas "Dicionário" (de onde vem e como foi calculada cada coluna) e "Fontes" (links) vao junto. Em
25/09/2026: 273 ETFs, 227 negociando; rentabilidade de 12 meses para 143 (o mapeamento dizia 143 de
235) e 3.207 celulas pendentes de leitura de PDF.

**Desdobramento e grupamento.** Rentabilidade, volatilidade, queda maxima e beta usam a serie de
fechamentos ajustada: um salto de um pregao para o seguinte perto de 1:k ou k:1 e evento de cota, e o
preco anterior vai para a base de hoje (a coluna "Desdobramento ou grupamento na série" diz qual). Na
serie de 25/08/2025 a 24/09/2026 foram tres - CHIP11 1:6 (26/12/2025), BITI11 1:3 e SPXI11 1:8
(22/01/2026); sem o ajuste, o SPXI11 aparecia com -86% em 12 meses de preco (e +13,9% com ele, de
24/09/2025 a 24/09/2026).

**Pregao sem arquivo completo.** Em 6 pregoes a B3 publicou o consolidado de negocios sem o mercado a
vista (ver acima; reconferido em 25/09/2026, o de 14/05/2026 segue igual). A volatilidade e o beta so
usam retorno entre pregoes consecutivos do calendario completo da B3 - com esses 6 dias -, entao os
12 retornos em volta deles ficam de fora; antes, o retorno de dois dias contava como diario e a
volatilidade de 12 meses saia ~1% acima.

**Comparacao com a Comdinheiro (`--comparar-comdinheiro`).**

```bash
python -m shelf_monitor etf-bruto --comparar-comdinheiro "G:/Meu Drive/ETF/Indicadores/COMDINHEIRO_XP_Fundos855217296.xlsx" "G:/Meu Drive/ETF/Indicadores/COMDINHEIRO_XP_Fundos647288622.xlsx"
```

Le as exportacoes da Comdinheiro (o formato cadastral e o de mercado sao reconhecidos pelo cabecalho),
refaz os numeros da captura na data da analise dela, com a janela de dias uteis de cada linha, e grava
`data/reports/etf-comparacao-comdinheiro-<data>.xlsx` ("Resumo" por campo, "Detalhe" por ETF e campo,
"Cobertura"). As exportacoes vao para `data/etf/entrada/comdinheiro/`, de onde a proxima captura as
registra. Medido em 25/09/2026, com a analise da Comdinheiro de 23/09/2026 (223 ETFs nos dois lados):

- **o que vem da mesma fonte bate**: fechamento, maxima, minima, preco medio, volume e retorno do dia
  (218 de 218), CNPJ, prestadores e constituicao (todos), Ibovespa em todas as janelas (220 de 220), CDI
  de 12 meses igual ao acumulado da serie 12 do Banco Central;
- **retornos batem fora dos que distribuem**: em todas as janelas de 1 a 12 meses, toda divergencia e de
  um dos 18 ETFs com DY maior que zero - a Comdinheiro reinveste o provento, a captura nao; com o
  COTAHIST de 2021 a 2025 (`--fontes cotahist --desde 2021-01-01`), 24, 36, 48 e 60 meses tambem batem
  em todos os que nao distribuem (75, 66, 59 e 39 ETFs);
- **a janela e a da ANBIMA**: os "250 du" da Comdinheiro contam 24/12 e 31/12 como dia util; contados
  so os pregoes, o retorno de 12 meses ficava 0,22 pp de diferenca media;
- **volatilidade da Comdinheiro sai ~1,2% abaixo** (mediana nos que nao distribuem; 80% entre 2,1%
  abaixo e 0,5% acima) - metodo, nao dado; beta igual ou proximo em 132 de 138 (48 de 48 nos ETFs de
  acoes Brasil); o VaR e formula sobre a volatilidade (1,65 x vol de 12 meses x raiz de 100/252);
- **o que a comparacao achou na Comdinheiro**: 10 dos 12 tickers que so ela tem sao o ticker antigo de
  ETF renomeado, em linha separada e sem preco (5GTK11 e hoje CHIP11, SAET11 e BOVS11, BTCN11 e BITH11...);
  FIXX11 com +435% em 12 meses e 402% de volatilidade, contra -0,8% e 11% na serie da B3; ISIN antigo do
  AGGX11; "qualificado" em NASD11, BOVX11, QBTC11 e GOVE11 com "investidores em geral" no mesmo registro
  (e no regulamento); "Inicio das atividades" e a data de adaptacao a RCVM 175;
- **o que depende do regulamento diverge**: taxa de administracao em 61 de 158, contra a sugestao tirada
  do PDF - que pega muitas vezes so uma parcela (0,04% nos ETFs It Now, 0,08% nos do BB). Numa amostra
  de 11 divergencias lidas no regulamento, a Comdinheiro acertou o total em 5 e errou 5: valor de uma
  versao anterior do regulamento em DEFI11 (entregue em 17/01/2022), ESGB11 (24/06/2025) e POSB11
  (19/02/2026) e valor que nao esta em nenhuma versao baixada em EBIT11 e BOVS11. Por isso a
  consolidada marca esses campos como pendentes de leitura.

**Cobertura.** Para cada um dos 54 indicadores do mapeamento, quantos ETFs listados tem o insumo da
origem recomendada e da melhor alternativa, com a janela em datas ("63 pregoes, de 29/06/2026 a
24/09/2026"). Nao e o indicador calculado: e "o dado bruto para calcula-lo esta em disco". Regra
(tributacao, liquidacao) sai com o texto da regra; o que nao e coletado (feed intradiario, book de
ofertas, site do fundo) sai com o motivo. Medida em 25/09/2026, sobre 231 ETFs listados: ticker,
ISIN e categoria em 230; preco de fechamento em 226; cota, PL e cotistas do FNET em 206; liquidez de
63 pregoes em 202; 12 meses de preco em 133 (o mapeamento dizia 143 de 235); carteira mensal da CVM
em 203; e zero no que depende de gente (classe XP, taxa conferida, formador) e da Comdinheiro sem
credencial.

## Monitor de previdencia (`shelf_monitor.previdencia`)

Modulo independente: as vitrines de PGBL/VGBL de cada casa, com pastas proprias
em `data/previdencia/` e o mesmo pipeline do monitor de fundos - snapshot, diff,
enriquecimento, captacao, rentabilidade, mandato, custo, e as quatro
superficies (digest, dashboard, deck e planilha).

```
python -m shelf_monitor.previdencia run         # coleta, diff e relatorios
python -m shelf_monitor.previdencia collect     # so a coleta
python -m shelf_monitor.previdencia report      # re-renderiza dos snapshots
python -m shelf_monitor.previdencia insights    # leitura competitiva, sem coletar
```

O `run_monitor.bat` roda a previdencia depois de fundos, com o codigo de saida
de cada um reportado em separado. Nao ha radar CVM no modulo: ele acompanha
registros de fundos das gestoras da watchlist e continua no `run` de fundos.

Filtrar o monitor de fundos nao servia: em 2026-09-20 nenhuma vitrine de fundos
trazia fundo de previdencia com `on_listing=True`. As casas vendem previdencia
em paginas proprias.

| Casa | Vitrine | Via | Fundos (2026-09-22) | CNPJ |
|---|---|---|---|---|
| XP (baseline) | `lista-de-fundos/#/` | API da pagina, interceptada no navegador | 223 | 223 |
| BTG | `previdencia-privada/produtos` | API publica `pension-management/funds/public` | 226 | 226 |
| Itau Private | catalogo de rentabilidade do Private Bank | HTML, so o bloco de previdencia | 41 | 0 |
| Nu, Inter | - | sem vitrine publica: UNAVAILABLE com motivo | - | - |

A XP desenha 50 cards, mas a API que a pagina chama devolve os 223; ela exige a
chave do Azure APIM, entao o coletor le a resposta que a propria pagina recebe.
O Itau e o Private Bank, nao a prateleira íon - sai rotulado "Itaú Private".

### O que o pipeline de fundos erraria aqui

Cinco adaptacoes, cada uma para um erro medido antes de ser corrigido. Todas
vivem no pacote; o codigo compartilhado so ganhou ganchos cujo padrao e o
comportamento de antes (verificado em 22/09: as superficies de fundos saiam
identicas byte a byte; o gancho de supressao so acrescenta `suprimidos: {}` ao
payload do dashboard de fundos).

- **Eixo grosso** (`eixo.py`). Todo rotulo ANBIMA de previdencia tem nivel 1
  "Previdencia": as 4.609 classes do cadastro caiam num balde so. Tirado o
  prefixo, sai a familia do ativo - Multimercados, Renda Fixa, Acoes,
  Balanceados. O rotulo completo continua em `categoria_oficial`, porque o
  custo busca a mediana por ele.
- **Particao varejo/restrito** (`eixo.py`). Todo FIE e "exclusivo" e de publico
  "profissional" no registro: o cotista e a seguradora. So com o carimbo da CVM,
  a regra de varejo tirava 226 de 226 fundos do BTG e 219 de 223 da XP. Essas
  marcas descrevem o veiculo e saem da regra; a restricao do PLANO (publico
  profissional declarado pela casa) continua valendo.
- **Resolvedor por nome** (`eixo.IndicePrevidencia`). O de fundos escolhia classe
  de varejo para fundo de previdencia. Contra o gabarito do BTG (226 fundos com
  CNPJ, resolvidos pelo nome como se nao tivessem): categoria exata 75% -> 88%,
  familia 89% -> 94%, familia de varejo errada 36 -> 1.
- **Referencia do custo** (`custo.py`). Pela mesma exclusividade, so 21 das
  2.086 classes de previdencia com taxa comparavel serviam de referencia, e
  nenhuma categoria tinha mediana. Com a regra de previdencia, 11 categorias
  passam a ter (Multimercado Livre 0,80%, 1.073 classes).
- **Blocos suprimidos** (`ConfigPrevidencia.blocos_suprimidos`). Tres cortes nao
  medem nada aqui, e as superficies trocam cada um pelo motivo:
  *cotistas* - o cotista do FIE e a seguradora (438 dos 439 fundos de vitrine
  com informe tinham 1 cotista em 23/09), entao saem a ponderada por cotistas, o
  "PL menos cotistas", os "fundos de muita gente", o "+N cotistas" e a aba "Onde
  esta a gente", e as listas de captacao ranqueiam so por dinheiro (exigindo
  cotista novo, "maiores captadores" tinha 1 fundo e "maiores perdas", nenhum);
  *CNPJ* - cada seguradora tem o proprio veiculo (XP e BTG dividiam 1 CNPJ),
  entao saem os gaps, o "so nesta vitrine", a oportunidade de grade e as
  decisoes de consenso ("inclusao operacional") e de exclusivos; e *produto
  proprio* - o gestor registrado do fundo vendido e a seguradora ou a asset dela
  (88 dos 118 "proprios" da XP tinham gestor XP Vida e Previdencia, com Kinea, AZ
  Quest e Kapitalo por tras), entao a captacao sai sem o corte proprio x terceiros.

### Dashboard (`dashboard.py`)

O molde do dashboard de fundos - tema claro, visao executiva estatica no topo e
cada bloco num painel recolhido, desenhado pelo mesmo JS -, com o que a
previdencia mede. Renderizada pelo dashboard de fundos, a pagina saia com duas
manchetes e tres paineis de "nao se aplica"; a propria troca:

- manchetes: fundos na vitrine (com a seguradora dos planos), captacao,
  rentabilidade em 12 meses e parcela do PL em fundo abaixo da mediana da
  categoria; o custo entra sozinho quando a cobertura passar do piso;
- placar: a seguradora sob o numero de fundos, e Nu e Inter listados como sem
  vitrine publica (no `report` eles nao tem snapshot util e sumiam da tabela);
- paineis: gaps, exclusivos e radar saem; a seguradora dos planos entra no
  lugar da arquitetura aberta; o de gestoras leva a ressalva da gestora
  registrada; e "O que este monitor nao compara" junta os motivos.

`render_outputs(renderizar_dashboard=)` e o gancho: a CLI da previdencia passa
`render_dashboard_previdencia`, e o padrao continua o dashboard de fundos
(verificado em 25/09: HTML de fundos identico byte a byte fora do JS embutido,
e o DOM que o JS desenha, identico).

### Limites conhecidos

- **Sem comparacao de oferta entre seguradoras.** Com gaps e exclusivos por CNPJ
  suprimidos, o bloco de gestoras e o que compara de verdade (37 gestoras em
  comum entre XP e BTG); casar veiculos equivalentes entre seguradoras e
  trabalho a fazer.
- **Custo fica abaixo do piso de cobertura.** A referencia agora existe, mas a
  taxa do proprio fundo no extrato da CVM falta, e zero ou esta velha em boa
  parte da vitrine: comparavel em 90 de 226 no BTG (39,8%) e 78 de 218 na XP,
  contra o piso de 40%. As duas casas publicam a taxa na vitrine; usa-la como
  reserva mudaria a metodologia do indicador e e decisao pendente.
- **Itau sem CNPJ.** Fica fora de captacao, custo, mandato e gaps, com o motivo
  no relatorio. A categoria vem do resolvedor por nome.
- **Arquitetura aberta ainda sai no deck e no `insights`.** O corte produto
  proprio x terceiros foi suprimido na captacao e no dashboard (que mostra a
  seguradora dos planos no lugar), mas o slide do deck e a coluna "Casa propria"
  do `insights` ainda o publicam pelo gestor registrado.

## Monitor da industria de fundos (`industria`)

A visao de mercado para a distribuicao, sem casa em foco: o que um cliente PF
ou PJ pode comprar, para onde vai o dinheiro, quem ganha e por qual canal. A
spec, com as medicoes, esta em `docs/spec-industria-fundos.md`.

```
python -m shelf_monitor industria                         # --ciclo auto: o que estiver na hora
python -m shelf_monitor industria --ciclo mensal          # universo, painel, planilha e e-mail
python -m shelf_monitor industria --ciclo flash           # captacao da semana por macroclasse
python -m shelf_monitor industria --competencia 2026-08   # refaz um mes
```

`--ciclo auto` e o que o `run_monitor.bat` chama, no passo [4/4], com codigo
de saida proprio. Ele roda o mensal quando o perfil mensal e a carteira da
competencia ja sairam na CVM (por volta do dia 16) e ela ainda nao foi
publicada; senao o flash, se o ultimo tem 7 dias ou mais; senao nada.

| Saida | Ciclo | O que e |
|---|---|---|
| `data/reports/industria_dashboard.html` | mensal | painel interativo: filtros por publico, macroclasse, papel e tipo de gestora, e o explorador dos ~8,2 mil produtos |
| `data/reports/industria-AAAA-MM.xlsx` | mensal | planilha explorador, 15 abas |
| `data/reports/industria-AAAA-MM-DD.md/.html` | mensal | e-mail do mes (640 px, ate 100 KB) |
| `data/reports/industria-flash-AAAA-MM-DD.md/.html` | flash | captacao da semana por macroclasse contra as 12 semanas anteriores |
| `data/historico/industria/AAAAMM.json` | mensal | o bloco publicado, trilha de auditoria (versionado) |
| `data/historico/cadastro/AAAAMM.csv.gz` | toda importacao da base | retrato mensal do cadastro (versionado) |
| `data/cache/industria/` | mensal | perfis compactos e o retrato do universo que o flash usa |

Tres regras mudam o numero, e toda superficie diz qual vale:

- **Universo disponivel.** Saem previdencia, exclusivos, masters,
  institucionais, nao residentes e restritos; o funil mostra cada degrau.
  Publico geral, qualificado e profissional ficam, como filtro.
- **Camada do investidor.** PL e captacao sem o dinheiro que um fundo aplica
  em outro, pelo perfil mensal da CVM: o FoF e o espelho nao contam duas
  vezes. Em 08/2026, R$ 3,80 tri contra R$ 4,28 tri brutos do mesmo universo.
- **Estrutura.** Master, feeder, espelho e FoF pela carteira (CDA); a
  estrategia, a gestora e o retorno vem da raiz da cadeia.

O flash usa o universo do ultimo ciclo mensal e recusa um retrato com mais de
45 dias. Dia do informe com menos de 90% dos produtos de um dia tipico fica de
fora e e dito no e-mail: em 28/09/2026, a sexta 25/09 tinha 2% dos produtos.

## Area logada

A v1 usa apenas fontes publicas. A arquitetura preve coletores autenticados sem
guardar credenciais: `AuthenticatedBrowserCollector` (em `collectors/base.py`) espera
um `storage_state` do Playwright que voce exporta apos fazer login manualmente, e o
reusa ate expirar. Para implementar um, sobrescreva `_collect_authenticated`,
registre a classe em `collectors/__init__.py` e aponte
`SHELF_AUTH_STATE_<PLATAFORMA>` para o arquivo de sessao.

O Nu e o caso que mais se beneficia disso: sem app web deslogado, e a unica via para
ver sua prateleira.

## Quando uma prateleira nao traz nada

Os coletores de prateleira foram escritos a partir de documentacao publica, nao de
observacao dos sites. A mecanica e testada (parsing, uniao, diff), mas os seletores e URLs
podem nao corresponder ao que cada site publica hoje - e sites de banco mudam com frequencia.

Quando uma casa vem vazia, `diagnose` responde por que:

```bash
python -m shelf_monitor diagnose --platform inter
python -m shelf_monitor diagnose --browser          # abre as listas num Chromium real
```

Ele sonda cada fonte daquela casa e **grava a resposta bruta** em `data/cache/debug/`:

| Sinal na sondagem | O que significa |
|---|---|
| `bloqueado pela network policy` | firewall ou proxy corporativo barrando o dominio |
| pagina responde 200 mas sem CNPJ, sem `<tr>` e sem bloco JSON | casca de SPA: os dados chegam por JavaScript, so o Playwright alcanca |
| diretorio Open Finance: `0 casaram` | a instituicao usa outra razao social; ajuste `open_finance_brands` em `config.py` |
| `/funds` responde mas `0 fundos extraidos` | o envelope tem formato diferente do previsto - o JSON salvo mostra qual |
| sitemap sem o prefixo de fundos | o site reorganizou as URLs; o sitemap salvo mostra a estrutura atual |

Os arquivos em `data/cache/debug/` sao a evidencia que permite corrigir o coletor com o dado
na mao, em vez de por tentativa. `diagnostico.json` consolida tudo num arquivo so.

> **Bug que a segunda rodada de evidencia revelou.** O capturador lia o corpo das
> respostas dentro do handler de evento; para uma resposta grande ainda chegando,
> o `body()` falha - foi assim que o XHR de 1,5 MB do Itau se perdeu enquanto os
> JSONs pequenos sobreviviam. Os corpos agora sao lidos depois que a pagina
> assenta, e `requisicoes.json` registra por resposta se o corpo foi lido e se o
> JSON parseou; corpos rotulados como JSON que nao parseiam ficam em
> `payloads-invalidos/` (e como se reconhece BOM, JSONP ou payload cifrado).

### `--browser`: o que o navegador ve

O modo padrao usa `requests`, entao mede o que ja se sabe que falha nas tres listas
que exigem JavaScript. Com `--browser`, cada lista e aberta num Chromium real e o
diagnostico grava, em `data/cache/debug/<casa>/`:

| Arquivo | Para que serve |
|---|---|
| `pagina.html` | o HTML **depois** do JavaScript - e nele que se ve se a lista existe |
| `requisicoes.json` | toda requisicao da pagina (URL, status, tamanho, content-type) - identifica o XHR da listagem |
| `payloads/NNN.json` | o corpo de **toda** resposta JSON, nao so a que o parser reconheceu |
| `tela.png` | screenshot, para o caso de a pagina mostrar bloqueio ou consentimento |

A sondagem tambem informa quantos fundos sairiam por cada via ("12 pelo JSON, 40 pelo
DOM"), que e o que diz onde mexer. E lenta - cerca de um minuto por site.

Para gravar essa mesma evidencia durante uma coleta normal, `SHELF_BROWSER_DEBUG=1`.

## Comparacao profissional: vitrine, normalizacao e as quatro superficies

A comparacao competitiva se apoia em dois conceitos e sai por quatro superficies.

**Vitrine vs. catalogo.** Vitrine e o que o cliente ve no canal da propria casa
(site, API do portal, area logada) - e a resposta a "o que esta disponivel para o
varejo". Catalogo e o que o Open Finance publica: tudo que a casa administra,
incluindo o que nunca aparece para o cliente. Todo relatorio separa os dois; um
filtro por minimo sobre o catalogo nao aproxima a vitrine (no BTG marcaria 5.415
fundos "varejo" quando a vitrine tem 693; na XP deixaria 9).

A vitrine e a unidade de **toda** comparacao entre casas, e o catalogo so aparece
rotulado como tal: tamanho por casa, composicao por classe, gestoras distintas,
faixa de minimo e "diferenciais de prateleira" saem da vitrine; o catalogo vira
coluna propria ("Catalogo") e a contagem bruta da coleta fica na saude da coleta.
Ate 20/09/2026 os tres numeros estavam colapsados num "total" no dashboard e no
digest, e o efeito era grande: a tabela de sumario abria com BTG 10.073 contra XP
781 (vitrine: 634 contra 664), o BTG aparecia com 884 gestoras contra 213 da XP
(vitrine: 167 contra 169) e com 5.767 fundos exclusivos contra 284 (vitrine: 284
contra 337). Nenhum desses numeros descrevia prateleira - descreviam os 9.413
fundos que so o Open Finance do BTG publica.

Casa sem vitrine alcancavel (hoje o Nu, que nao tem listagem deslogada) sai dos
blocos comparativos com o motivo escrito, em vez de entrar neles medida pelo
catalogo. No digest, mudanca que aconteceu so no catalogo tambem nao entra em
"entraram na vitrine": ela vira uma frase com a contagem, para nao afogar a
mudanca real das outras casas num dia de churn do catalogo do BTG.

**Normalizacao ANBIMA.** Cada casa descreve categoria do seu jeito ("Credito Pos"
no BTG, "baixo risco" no Itau). Com a base de mercado importada (o `run` a importa
sozinho quando ela falta; `python -m shelf_monitor market` faz o mesmo a mao),
todo fundo identificavel e carimbado com a classificacao ANBIMA e CVM oficial,
gestor, situacao e PL do registro - e a comparacao por classe passa a significar a
mesma coisa nas cinco casas. A classificacao publicada pela casa, quando diverge,
e preservada (`classificacao_anbima_casa`): a divergencia em si e informacao. A
cobertura do join sai no rodape de cada superficie.

**Como um fundo da prateleira encontra seu registro.** Tres chaves, tentadas nesta
ordem: o **CNPJ da classe** (o que o informe e a distribuicao usam), o **CNPJ do
fundo** (o envelope, que e o que o Open Finance publica - num fundo multi-classes
ele nao e o CNPJ de nenhuma classe) e o **codigo CVM**, para quem nao publica CNPJ
nenhum. O codigo cobre os tres niveis da RCVM 175 e, em colisao, vence o mais
especifico: subclasse antes de classe, classe antes de fundo. Como a subclasse
partilha CNPJ e carteira com a classe, o codigo dela resolve para o registro da
classe-mae. Cada fundo sai dizendo por qual chave casou, em `match_cvm`.

**A gestora vem da CVM.** Quando o fundo casa com a base, `gestor` e `cnpj_gestor`
do registro prevalecem sobre o que a casa publicou. Isso existe porque a mesma
gestora aparecia com tres identidades: nome comercial numa casa, razao social
noutra, e ate o gestor pessoa fisica numa terceira ("MARCO AURELIO VIRZI" onde o
registro diz "BTG PACTUAL GESTAO E CONSULTORIA") - contar "gestoras distintas"
somava a mesma empresa varias vezes. O nome da casa nao se perde: vai para
`gestor_casa` quando diverge de fato (grafia diferente da mesma razao social nao
conta). Sem base importada nada disso roda, e o console avisa - antes ele nao
avisava, e prateleira inteira saia sem gestora em silencio.

| Superficie | Comando | Para quem |
|---|---|---|
| Dashboard (`dashboard.html`) | `report` / `run` | ler e explorar - visao executiva no topo (manchetes com prazo, leituras e placar casa a casa) e, sob demanda, gaps da vitrine XP, captacao, rentabilidade e custo, matriz casa x classe ANBIMA, arquitetura aberta, ticket, exclusivos e radar CVM |
| Deck executivo (`prateleiras-<data>.pptx`) | `report` / `run` | decidir - ate 9 slides para forum/comite (incl. gaps XP), gerados a cada coleta |
| Planilha (`prateleiras-<data>.xlsx`) | `export` | trabalhar - abas Vitrine varejo (tabela-dinamica-ready), Gaps XP, Gaps cobertos, Gaps por categoria, Familias, Movimentos e Metodologia |
| Digest (`digest-<data>.md/.html`) | `report` / `run` | acompanhar - o resumo do dia por e-mail, com a secao de gaps XP no topo |

O dashboard abre na visao executiva: as manchetes da XP (o que falta na vitrine,
quanto capta, quanto rende, quanto custa, o que vem por ai no radar CVM), cada
uma com o prazo e a melhor concorrente ao lado; as leituras da coleta, com as
mesmas frases do digest; e o placar casa a casa. Essa camada e HTML estatico,
montado em Python - aparece mesmo quando o visualizador bloqueia script. Cada
bloco analitico fica num painel recolhido, com uma linha de resumo; manchete,
topico e link interno abrem o painel de destino, e **Abrir todos** expande a
pagina inteira (para imprimir o relatorio completo). Tema claro sempre: o painel
seguia o tema do sistema e abria escuro em maquina configurada assim.

Regras de leitura que valem nas quatro: quartis em vez de media (a media esconde a
forma da prateleira), "gestora da casa" definida pela mesma watchlist do radar CVM,
coleta suspeita nunca vira churn, e todo numero carrega proveniencia (fonte, data,
cobertura) no rodape.

**Movimento e linha de base.** Movimento e entrada, saida ou alteracao na vitrine de
varejo (`report/shared/movimentos.py`), a mesma definicao em todas as superficies -
medido no BTG de 13 para 19/09, o diff inteiro tinha 236 movimentos e a vitrine 174,
e cada superficie mostrava um dos dois. Duas guardas protegem a comparacao:

- casa que declara fonte de vitrine e so trouxe o catalogo e gravada `partial`
  (`assess_quality`), e por isso nao vira baseline confiavel - em 13/09 XP, BTG e Itau
  foram gravados `ok` so com o Open Finance;
- vitrine que aparece onde nao havia, ou que dobra com ao menos 50 fundos a mais
  (`SUSPECT_GROWTH_RATIO`, `SUSPECT_GROWTH_MIN_FUNDS`), vira **nova linha de base**:
  sem entradas, com o motivo. Em 19/09 o conserto do navegador levou tres vitrines
  de 0 a centenas de fundos e o digest publicou "828 fundos entraram". O maior
  crescimento real medido entre duas coletas com vitrine foi +17% (Inter, 399 -> 466).

**Ticket de entrada.** As faixas de aplicacao minima e o veredito do ticket leem o
minimo que a propria vitrine mostra (`FundOffer.minimo_vitrine`), guardado antes de o
Open Finance vencer o campo no merge - em 511 fundos da XP o catalogo traz outro minimo,
o institucional (ex.: R$ 50 mil contra R$ 500 na tela). Sem ao menos 30 fundos com esse minimo
(`MIN_AMOSTRA_TICKET`), a leitura diz "sem base para comparar", nunca "a casa nao tem
fundo barato": foi assim que o digest de 21/09 publicou que a XP nao tinha nenhum fundo
abaixo de R$ 5 mil - a coleta de 23/09 achou 501 deles na lista dela.

**Perfil de varejo.** Fundos exclusivos/restritos - exclusivo no registro CVM,
publico-alvo profissional, marcados pela propria casa (flags da API do BTG) ou
denunciados pelo nome (EXCLUSIVO/RESERVADO/RESTRITO/FAMILY OFFICE) - ficam **fora de
toda analise principal**: sao veiculos de um investidor, nao produtos a venda, e
inflam qualquer comparacao. Nao somem: moram na aba "Restritos (auxiliar)" da
planilha e no card auxiliar do dashboard, cada um com o motivo. Investidor
*qualificado* nao e motivo de corte - plataforma de varejo distribui fundo
qualificado a alta renda normalmente. Com o corte, "exclusivo" passou a significar
uma coisa so; o conceito competitivo virou "so nesta casa"/"diferencial".

**Gaps de prateleira da XP.** A leitura XP-centrica da coleta: gap e um fundo de
varejo presente na vitrine de ao menos um concorrente e ausente da vitrine da XP,
cruzado **por CNPJ**. A base e sempre a vitrine, dos dois lados - o catalogo Open
Finance nao entra em nenhum ponto da conta: um fundo que a XP so tem via Open
Finance conta como gap, porque o cliente nao o ve. Duas dimensoes: a lista nominal
(ranqueada por numero de casas que vitrinam e PL, quando a base de mercado esta
importada) e a cobertura por categoria ANBIMA (vitrine XP vs. melhor concorrente,
com quantos gaps caem em cada categoria). Supressoes, na mesma linha do resto do
monitor: XP indisponivel ou **sem vitrine coletada** suprime o bloco inteiro com o
motivo (sem a vitrine da XP, todo fundo concorrente pareceria gap - hoje e o caso
quando a coleta da XP roda so via Open Finance, sem o Playwright); XP parcial gera
aviso de superestimacao; concorrente indisponivel ou sem vitrine sai do universo,
listado com motivo (o Nu, que so coleta Open Finance, sai por construcao). Sai nas
quatro superficies com os mesmos numeros; a lista completa mora na aba "Gaps XP"
da planilha, com coluna de verificacao em formula contra a aba "Vitrine varejo".

### Gap aparente e gap real: familias de estrategia

O CNPJ responde "este fundo nao esta na vitrine", nao "o cliente da XP nao tem
acesso a esta estrategia". As gestoras vendem a mesma estrategia com um CNPJ por
distribuidor - o master e um espelho em cada casa ("Advisory" na XP, "Access" no
BTG e no Inter) - e em versoes com liquidez diferente. Medido em 2026-09-26
(vitrines de 25/09, carteira da CVM de 08/2026), dos 621 gaps por CNPJ:

| | Gaps |
|---|---:|
| Gaps aparentes (CNPJ fora da vitrine XP) | 621 |
| Espelho do mesmo master de um fundo da vitrine XP | 59 |
| Classe paralela equivalente da mesma gestora | 1 |
| A XP tem a estrategia so em outra faixa de liquidez | 14 |
| **Gaps reais** | **547** |
| ... de estrategia (gestora ja distribuida) | 179 |
| ... de gestora (gestora nova) | 148 |
| ... sem carteira que permita decidir | 220 |

O mapa de familias (`familias.py`) sai da carteira mensal da CVM (CDA,
`market/carteira.py`), do cadastro e do extrato:

- **espelho**: classe com 95% ou mais do PL em cotas de uma unica classe; a cadeia
  e seguida ate o master (ate 4 saltos, protegida contra ciclo). Quando a cota do
  master esta sob sigilo na competencia mais recente, a ligacao recua para as
  anteriores (8 gaps a mais em 2026-09-26). O flag "fundo espelho" do extrato marca
  so 44 de 201 espelhos entre os gaps - aparece como informacao, nao decide;
- **perfil de carteira**: fracao da carteira por faixa (soberano pos e caixa,
  soberano pre e inflacao, credito bancario - CDB, LF, DPGE, LCA -, credito
  corporativo - debentures, CRI, CRA, notas -, acoes, exterior, outros, sob
  sigilo), com look-through das cotas. O mapeamento de TP_APLIC para faixa foi
  medido no arquivo de 08/2026; derivativos ficam fora (o valor de mercado de
  futuro e swap e o ajuste);
- **classe paralela**: master da mesma gestora (raiz do CNPJ do gestor), mesma
  categoria ANBIMA, indicador de desempenho compativel, no maximo 15% da carteira
  em faixas diferentes (`LIMIAR_DISTANCIA_CARTEIRA`) e ao menos 60% da carteira nos
  mesmos ativos - emissor, acao ou fundo offshore (`LIMIAR_SOBREPOSICAO`). A
  sobreposicao existe porque o vetor por faixa nao separa feeders offshore do
  mesmo gestor local: Schroder GAIA, MAN AHL e Pearl Diver, todos "100% exterior",
  saiam na mesma familia (28 equivalentes; com a sobreposicao, 1). Calibrada sobre
  os 225 pares gap x vitrine XP da mesma gestora e categoria: com 0,5 entravam
  fundos irmaos de credito com metade da carteira em comum (ARX Vinson x ARX
  Everest, 0,51);
- **liquidez**: D+ de cotizacao mais pagamento do resgate, em faixas (ate D+1,
  D+2 a D+5, D+6 a D+30, D+31 a D+90, D+91 a D+180, acima de D+180). Espelho na
  mesma faixa cobre; em faixa diferente, o gap vira "coberto em outra liquidez"
  (Sparta Max: concorrente D+61, XP D+121). Liquidez nao informada nao rebaixa.

Cada gap aparente recebe um veredito: mesma classe (outro CNPJ da mesma classe),
coberto por espelho (confianca alta), coberto por equivalente (media), coberto em
outra liquidez, gap de estrategia, gap de gestora, ou sem dado de carteira. So
evidencia estrutural ou de carteira reclassifica; o "possivel equivalente" (mesma
gestora e categoria, sem carteira que decida, ou nome parecido) **informa e nunca
remove** um fundo da lista. Os cobertos saem da contagem e vao para a aba "Gaps
cobertos" da planilha, com o fundo XP que cobre, o master, o D+ dos dois lados, a
distancia e a sobreposicao.

A gestora que decide o esforco e a **da estrategia** (a do master), nao a do
veiculo: o BTGP Thunderbolt, feeder de marca propria do BTG, investe no SPX Raptor
- saia "produto proprio do concorrente", e a XP exibe o SPX Raptor 180 Advisory. A
escada de esforco, do mais barato ao mais caro: expor na vitrine, ativar
distribuicao (as duas so com a lista distribuivel), inclusao operacional, versao
de liquidez (custa o mesmo que a operacional, mas a estrategia ja esta na
prateleira), abrir gestora, gestora nao identificada, produto proprio do
concorrente. A decisao de consenso (`insights`) e as oportunidades de grade
(`captacao`) usam o mesmo veredito: fundo coberto nao vira lacuna em nenhum bloco.

`python -m shelf_monitor familias "NOME"` lista todas as familias de uma gestora
(ou a de um fundo, pelo CNPJ): versoes, papel de cada classe, D+, perfil de
carteira, PL e em que vitrine cada classe aparece; o console resume os espelhos
que nenhuma vitrine mostra (a SPX Falcon tem 46) e o `--json` traz todos. A aba
"Familias" da planilha traz as familias das gestoras dos gaps que tem alguma
classe numa vitrine ou na lista.

Custo, medido em 2026-09-26: cada competencia da CDA (~22 MB) e resumida uma vez
em `data/cache/carteira/` (~13 s, ~2,5 MB); com o cache, o mapa monta em ~10 s.
`--no-carteira` em `run`, `report` e `export` pula o mapa, e o bloco volta a
comparar CNPJ contra CNPJ, com o aviso. O mesmo zip serve a carteira dos ETFs do
`etf-bruto`.

### Lista distribuivel

A vitrine publica nao e tudo que a XP distribui. CSVs soltos em
`data/distribuivel/entrada/` (fora do git: dado comercial interno; modelo em
`data/distribuivel/modelo.csv`) viram uma segunda base de cobertura:

- separador `;` ou `,`, UTF-8 com ou sem BOM, ou Latin-1; colunas por nome:
  `cnpj` (ou `codigo_cvm`), `situacao` (`distribui` ou `pode_distribuir`; vazio
  vale `distribui`), e opcionais `nome`, `gestora`, `data_referencia`, `observacao`;
- CNPJ da classe, CNPJ do fundo (vale para todas as classes dele, com aviso) ou
  codigo CVM, resolvidos contra o cadastro; arquivos unidos, repetido fica com
  `distribui`;
- linha que nao se resolve (CNPJ invalido, situacao desconhecida, fundo fora do
  cadastro) e listada com arquivo, linha e motivo no fim da aba "Gaps XP".

As duas lentes sao independentes. Com a vitrine, a lista muda o esforco: gap da
vitrine que a lista cobre vira "expor na vitrine" (ou "ativar distribuicao"). Sem
a vitrine da XP (dia de bloqueio de CDN), a lista vira a base e o aviso diz que a
vitrine faltou. Sem CSV na pasta, a lente sai suprimida com o motivo.

**Gestora quando a fonte nao publica.** A tabela do Itau nao traz CNPJ nem gestora.
Quatro vias, na ordem de confianca: (0) o join com o registro da CVM, pelo CNPJ ou
pelo codigo CVM que a fonte publicar - e a via que resolve o caso geral, e a unica
que devolve dado oficial; (1) o join por CNPJ quando a reconciliacao por nome
encontra o gemeo no Open Finance - exata pelo slug, ou aproximada com razao
>= 0,95, candidato unico por margem e bloqueio de tokens que distinguem
master/feeder/espelho (a proveniencia fica em `reconciliacao`); (2) inferencia pela
marca no prefixo do nome ("Bahia AM Muta..." -> Bahia AM), contra um indice de
marcas construido das gestoras da base CVM - so marcas inequivocas entram ("ITAU"
fica de fora porque aponta para Itau Asset, Itau DTVM e Intrag), e o dado sai
marcado `gestor_inferido`, nunca como oficial; (3) o XHR de ~1,5 MB do gateway do
Itau, capturado pelo `diagnose --browser`, que deve trazer o dataset completo -
parser pendente de evidencia parseavel. O campo `cvm_code` da oferta ja existe e o
extrator generico de JSON o captura, entao quando esse parser chegar o caminho ate
o registro ja esta pronto.

## Qualidade da prateleira: rentabilidade, custo e mandato

As quatro leituras acima respondem *o que cada casa oferta*. Estas respondem *se o
que ela oferta e bom, quanto custa e que mandato tem* - a metade que decide uma
inclusao de grade.

```bash
python -m shelf_monitor insights      # posicionamento, gestoras, momentum e decisoes
python -m shelf_monitor captacao      # captacao de varejo e oportunidades de grade
python -m shelf_monitor performance   # rentabilidade ponderada por PL e por cotistas
python -m shelf_monitor rentabilidade # retorno por fundo em 1m, 3m, YTD, 12m e 24m
python -m shelf_monitor custo         # taxa de cada fundo contra a categoria dele
python -m shelf_monitor mandato       # o que o regulamento de cada classe autoriza
```

Todos aceitam `--json <caminho>` e `--platform`. `custo` e `mandato` entram
sozinhos na planilha, no dashboard e no digest; `rentabilidade` so entra com
`--with-rentabilidade` em `run`, `report` ou `export`, porque a primeira
montagem le ate 24 competencias do informe (~2,5 min e ~260 MB).

### Captacao dos fundos de vitrine (`captacao`)

O informe diario da CVM publica fluxo por **fundo**, nunca por distribuidor: o numero
de cada casa e o fluxo, no mercado inteiro, dos fundos que ela exibe. Sozinho, esse
numero nao dizia de onde vinha a diferenca entre as casas. O bloco abre com quatro
frases, escritas uma vez (`retail_flow.leitura_da_captacao`) e iguais no digest, no
dashboard, no deck, na planilha e no console. Medido em 24/09, de 07 a 09/2026:

- saldo em reais e em % do PL de inicio: XP -R$ 1,3 bi (-0,40%), BTG +R$ 3,0 bi (+0,77%);
- compartilhado x so nesta vitrine: nos fundos que divide com outra casa a XP capta
  +R$ 1,5 bi; nos que so ela exibe perde R$ 2,8 bi, contra +R$ 2,3 bi do BTG;
- por familia de ativo, de onde vem a diferenca: dos R$ 4,3 bi entre XP e BTG,
  R$ 2,9 bi estao em Renda Fixa e R$ 1,7 bi em Multimercados;
- produto da gestora da propria casa x terceiros: o saldo do BTG vem do produto
  proprio (+R$ 3,3 bi); os fundos de terceiros da vitrine dele perdem R$ 333 mi.

Abaixo das frases: a tabela de composicao, a tabela por familia (com o universo das
vitrines, cada fundo uma vez), a media diaria por competencia e a tendencia dos fundos
**por casa** - a contagem global so descrevia o mercado (66% dos fundos da vitrine da
XP perdiam dinheiro nos dois periodos, 61% no BTG, 77% no Inter). As listas trazem o
rotulo da casa, o ticket por cotista novo e o que a vitrine da XP esta perdendo. O
ticket existe porque o fluxo e do fundo inteiro: os maiores captadores traziam de
R$ 88 mil a R$ 433 mil por cotista novo, e so o numero mostra que nao e varejo.

A familia e o nivel 1 da categoria oficial. A categoria fina foi medida e descartada
para este corte: 36 categorias ANBIMA, cada uma dominada por um ou dois fundos.

### Rentabilidade ponderada (`performance`)

Tres numeros, e a diferenca entre eles e o achado:

| Leitura | Formula | Responde |
|---|---|---|
| Mediana da vitrine | `mediana(retorno)` | quanto rende o fundo *mediano* da prateleira |
| Ponderada por PL | `Sigma(retorno x PL) / Sigma PL` | quanto rendeu o *real medio* |
| Ponderada por cotistas | `Sigma(retorno x cotistas) / Sigma cotistas` | quanto rendeu o *cotista medio* |

O gap entre as duas ponderadas diz onde esta o desalinhamento: positivo, o dinheiro
grande esta nos fundos bons e o varejo nos piores; negativo, o inverso.

Medido em 2026-09-20, janela 2026-07-01 a 2026-09-18 (56 pregoes):

| Casa | Fundos | Mediana | Por PL | Por cotista | Gap | Abaixo da categoria |
|---|---:|---:|---:|---:|---:|---|
| XP | 567 | 2,93% | 2,96% | **3,61%** | -0,65pp | 34% do PL, 34% dos cotistas |
| BTG | 601 | 2,91% | 3,12% | **3,62%** | -0,50pp | 37% do PL, 32% dos cotistas |
| Inter | 363 | 2,92% | 3,54% | **3,93%** | -0,39pp | 34% do PL, 33% dos cotistas |

Nas tres o cotista medio rendeu mais que o real medio: o varejo se pulveriza em
categorias que subiram no periodo (Multimercados Livre, Acoes Livre) enquanto o PL
grande fica em renda fixa de duracao baixa, onde a dispersao e minima.

**Duas janelas, cada uma com o prazo escrito.** Tres meses dominados pelo CDI
comprimem a dispersao - quase tudo fica a centesimos da mediana da categoria -,
entao o bloco sai em 12 meses tambem, e 12 meses vem primeiro em toda superficie:

| Janela | De onde vem | Base da conta | Em 2026-09-24 |
|---|---|---|---|
| 12 meses | serie mensal (`data/cache/serie`) | cota do fechamento da competencia anterior a janela (convencao de lamina) | de 30/09/2025 a 23/09/2026, 246 pregoes |
| 3 meses | informe diario das 3 ultimas competencias | cota do primeiro pregao do periodo | de 01/07/2026 a 23/09/2026, 60 pregoes |

A mediana de referencia e a da mesma categoria no universo oficial, **no mesmo
prazo**. Na janela de 12 meses o sinal do gap vira, nas tres casas:

| Casa | Mediana | Por PL | Por cotista | Gap | Abaixo da categoria |
|---|---:|---:|---:|---:|---|
| XP | 12,97% | 13,27% | 12,46% | +0,80pp | 260 de 551 fundos, 22% do PL - indice 0,47 |
| BTG | 12,54% | 13,78% | 12,03% | +1,75pp | 275 de 578, 19% do PL - indice 0,39 |
| Inter | 12,01% | 12,89% | 10,46% | +2,43pp | 197 de 361, 30% do PL - indice 0,55 |

Em 3 meses o cotista medio rende mais que o real medio; em 12, o inverso - o que
confirma a ressalva de que o gap descreve que categorias performaram no periodo, nao
a base de clientes. A lista "fundos de muita gente rendendo abaixo da categoria" usa
os 12 meses, onde a distancia deixa de ser de centesimos.

Cada saida escreve o prazo junto do numero, em data brasileira: o digest abre a secao
com "Prazos: 12 meses, de 30/09/2025 a 23/09/2026 (246 pregoes, base na cota do
fechamento de set/2025) e 3 meses, de ...", o dashboard tem um seletor 12 meses | 3
meses com o prazo na nota, e a planilha tem uma aba por janela ("Rentabilidade 12
meses", "Rentabilidade 3 meses") com os campos Janela, Periodo, Pregoes e
Competencias. "Pregoes" e a contagem mais comum entre os fundos que informaram ate o
ultimo dia - 246 e 60 batem com os dias uteis dos dois periodos; a uniao das datas
contava fundo que publica cota em dia sem pregao. A captacao ganhou o mesmo: "Prazo: 3
meses, de 01/07/2026 a 23/09/2026 (competencias jul/2026, ago/2026, set/2026)".

A janela de 12 meses custa ~2,5 s por execucao (medido em 2026-09-24): so a competencia
corrente e resumida de novo, as fechadas vem do cache, e so e montada quando alguma
vitrine publica CNPJ.

**Tres coisas que este numero nao e.** Nao e o retorno dos clientes da casa - a CVM
publica cota, PL e cotistas por FUNDO, somando todos os distribuidores, entao o peso
e o do mercado, nao o da base dela. Nao e retorno total: a cota patrimonial e
ex-provento. E "adequado" so significa algo contra uma referencia, que aqui e a
mediana da mesma categoria no **universo oficial inteiro**, nao na propria vitrine.

**Uma linha do informe e uma SUBCLASSE, nao uma classe.** Uma classe com subclasses
emite uma linha por subclasse por dia, cada uma com cota, PL e cotistas proprios -
273 dos 25.307 CNPJs em 09/2026, somando R$ 183 bi. Ordenar as linhas do CNPJ e pegar
a primeira e a ultima compara pontas de subclasses diferentes: o Kinea RF Absoluto
saia com 107,70% de retorno e 220 "pregoes" numa janela de 57. `agrupar_por_classe`
(em `report/retail_flow.py`) soma PL, cotistas e fluxo entre subclasses, pondera o
retorno pelo PL de cada uma e deduplica a retificacao do informe.

### Rentabilidade por fundo, em janelas (`rentabilidade`)

Enquanto `performance` responde pela vitrine inteira numa janela so, este responde
**fundo a fundo em cinco janelas** - 1 mes, 3 meses, YTD, 12 e 24 meses - que e a
pergunta que se faz olhando uma linha da planilha.

O informe sai num zip por competencia, e ler 24 deles a cada execucao custa minutos.
Por isso o modulo guarda um **resumo mensal por subclasse** em
`data/cache/serie/<AAAAMM>.csv` (primeira e ultima cota do mes, PL e cotistas do
ultimo dia, fluxo somado, pregoes distintos). Competencia ja resumida nao e relida;
a corrente e sempre refeita, porque muda todo dia. A primeira montagem das 24
competencias leva ~2,5 minutos; as seguintes, segundos.

**A base de cada janela e a cota do fim da competencia ANTERIOR**, nao a do primeiro
dia dela - e a convencao de lamina: "12 meses ate setembro" compara 30/09 com 30/09
do ano anterior. Quando o fundo nao existia la, a base passa a ser a primeira cota
observada dentro da janela, e o campo `meses` registra sobre quantas competencias o
numero foi calculado.

> Isso faz o mesmo fundo aparecer com dois valores na mesma pasta: `Captacao por
> fundo` e `Rentabilidade ponderada` leem so as competencias da janela e usam o
> primeiro pregao como base; `Rentabilidade por fundo` usa o fechamento anterior.
> Medido em 2026-09-20, o Mapfre Confianza sai com 2,99% numa e 3,05% na outra nos
> mesmos tres meses. A aba Metodologia traz essa linha por escrito.

O fundo precisa ter sido observado em **80% das competencias da janela**: sem isso,
um fundo que so aparece nos dois ultimos meses entraria com retorno de duas
competencias ao lado de outro com doze. E a janela longa tem **vies de
sobrevivencia** - 570 fundos da XP em 3 meses contra 507 em 24 -, entao o agregado
de 24 meses e de sobreviventes e esta para cima. O campo `fundos` de cada janela
existe para que isso seja visivel em vez de suposto.

Medido em 2026-09-20 (retorno do periodo, nao anualizado):

| Casa | 1 mes | 3 meses | YTD | 12 meses | 24 meses |
|---|---:|---:|---:|---:|---:|
| XP (por cotista) | 0,64% | 3,72% | 8,13% | **11,89%** | **30,18%** |
| BTG (por cotista) | 0,71% | 3,74% | 7,83% | 11,32% | 29,22% |
| Inter (por cotista) | 0,93% | 4,09% | 6,51% | 9,64% | 26,45% |

O sinal do gap entre a ponderada por PL e a por cotistas **nao e estavel entre
janelas**: negativo em 3 meses nas tres casas, positivo em YTD e 12 meses, e negativo
de novo em 24 na XP. Ele descreve que categorias performaram em cada janela, nao uma
caracteristica da base de clientes - ler o de 3 meses como estrutural foi o erro que
a serie longa corrigiu.

### Custo relativo a categoria (`custo`)

A metrica de custo anterior comparava a mediana da taxa da vitrine inteira entre
casas. Ela mede **composicao, nao preco**: uma casa com mais renda fixa (~0,2% ao ano)
aparece mais barata que uma com mais acoes (~2%) sem que nenhum produto custe
diferente. No lugar, cada fundo e comparado com a mediana da **propria categoria** e o
que se agrega e o indice.

Tres guardas que o dado exige, cada uma com o efeito medido:

- **Taxa polimorfica.** `TAXA_ADM` carrega percentual ao ano e valor fixo em reais no
  mesmo campo. Entram so as classes em percentual; sem isso um fundo de "R$ 30.000 por
  ano" lidera o ranking como se cobrasse 30.000%.
- **Idade da entrega.** O extrato vai de 2015-10-02 a 2026-08-28. Acima de 24 meses a
  classe sai. ~40% do mercado com taxa nao reentrega o extrato - por isso o piso de
  cobertura e 0,4 e a cobertura sai impressa ao lado de todo indice.
- **A referencia e o universo de VAREJO.** Das 13.093 classes com taxa percentual e
  entrega recente, 47,2% sao exclusivas, 21,1% cobram zero (master ou feeder que cobra
  na outra ponta) e 1.368 sao de publico profissional. Com tudo dentro, a mediana de
  "Multimercados Livre" cai para 0,080% e a primeira execucao devolveu indice mediano
  de **6,79x** com 82% da vitrine do BTG marcada como cara. Aplicando ao universo a
  mesma regra de varejo que `profile.restriction_reason` ja aplica a vitrine, as
  medianas passam a 0,800% em Multimercados Livre, 1,300% em Acoes Livre e 1,500% em
  Multimercados Macro. **Investidor qualificado continua dentro** - a decisao e de
  `profile.py:12-13` e nao se desfaz aqui por conveniencia.

Medido em 2026-09-20: XP 1,65x, BTG 1,50x, Inter 1,57x. Nenhuma delas cobra 60% acima
do mercado - a vitrine exibe a classe de varejo, que carrega a camada de distribuicao,
enquanto o universo inclui classes da mesma estrategia sem essa camada. O que decide e
a **diferenca entre as casas** e o fundo individual muito acima da propria categoria,
nao a distancia de 1,00x.

Um resultado negativo que vale registrar: a coluna "com porta menor" (existe classe
mais barata do mesmo fundo?) sai em zero. Das 22.027 estruturas com aplicacao minima,
apenas 13 tem duas ou mais classes com minimo e **5** tem minimos diferentes. Onde as
condicoes comerciais variam de fato e a subclasse, e `subclasses.csv` nao traz minimo
nem taxa: o extrato e entregue por CNPJ de classe.

### Mandato declarado (`mandato`)

Das 117 colunas do extrato, o parser consumia 17. O bloco maior das que sobravam
descreve **o que cada classe pode carregar**: credito privado, ativo no exterior,
derivativos, exposicao acima do patrimonio e fundo espelho.

A palavra que governa tudo e *autorizado*. Um fundo pode ter autorizacao para 100% de
credito privado e carregar 10%; medir exposicao realizada exigiria a carteira mensal
(CDA), que nao esta no escopo. Nenhum rotulo diz "carrega" onde o dado diz "pode
carregar".

Share da vitrine que autoriza cada coisa, em 2026-09-20:

| Casa | Credito privado | Exterior | Derivativos | Alavancagem | Espelho |
|---|---:|---:|---:|---:|---:|
| XP | 69% | 63% | 61% | 35% | 10% |
| BTG | 61% | 65% | 70% | 43% | 11% |
| Inter | 63% | 67% | 64% | 40% | 10% |

Como no posicionamento por categoria, o que se publica e o **indice contra as demais
casas**, nao o share bruto: com a propria no denominador, todo indice e puxado para 1.

### O que o extrato deixou de perder

`market/fees.py` procurava `VL_TAXA_ENTRADA`, `VL_TAXA_SAIDA`, `QT_DIA_CONVER_COTA` e
`VL_SALDO_MIN`. Nenhum desses nomes existe nas 117 colunas do arquivo, que traz
`TAXA_INGRESSO_REAL`/`TAXA_INGRESSO_PR`, `TAXA_SAIDA_REAL`/`TAXA_SAIDA_PR` e
`QT_DIA_CONVERSAO_COTA` - e nao tem coluna de saldo minimo. Como `_get` devolve `None`
em silencio para nome ausente e `unknown_columns` so registra o caminho inverso, a
falha nao levantava nada: medido em `taxas.csv`, toda coluna de nome certo saia em
100% e toda coluna de nome errado em 0,0%, nas 38.547 linhas.

Corrigido, `dias_cotizacao_resgate` passou de 0% a 100%, e ingresso e saida aparecem
nas 44 (0,1%) e 1.281 (3,3%) classes que de fato cobram. `saldo_minimo` foi removido em
vez de renomeado - a fonte nao tem o dado. A fixture do teste foi regerada a partir do
cabecalho real: ela casava com o parser, nao com o arquivo, e por isso o teste passava.

## Como o monitor evita falso alarme

Um monitor de prateleira erra de dois jeitos: reportando mudanca que nao houve, ou
perdendo mudanca que houve. As defesas contra o primeiro caso:

- **Identidade por CNPJ** quando disponivel; sem CNPJ, um slug que descarta tokens de
  forma juridica (FIC, FIF, LP, CP...), para que uma reestruturacao societaria nao
  vire "fundo novo".
- **Reconciliacao de renomeacao**: entradas e saidas muito parecidas (sem CNPJ) sao
  tratadas como renomeacao, nao como troca.
- **Regra anti-churn**: se a coleta atual for parcial ou trouxer menos da metade dos
  fundos da anterior, o diff e marcado como suspeito e as saidas nao sao reportadas -
  fonte bloqueada nao vira "prateleira encolheu".
- **Campo ausente nao e mudanca**: se a fonte parou de publicar a taxa, isso nao vira
  "taxa alterada".

## Refinando a watchlist de gestoras

O radar CVM depende de reconhecer as gestoras de cada casa no campo `Gestor`. A
watchlist em `config.py` comeca com padroes de nome; para tornar o match preciso,
levante os CNPJs reais e adicione as raizes:

```bash
python -m shelf_monitor watchlist-discover "BTG PACTUAL"
python -m shelf_monitor watchlist-discover "KINEA"
```

Depois adicione as raizes de CNPJ em `WATCHLIST[<casa>].cnpj_roots`. A partir dai o
match por CNPJ e a via principal e o regex fica como rede de seguranca para gestoras
novas da casa.

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes rodam contra fixtures (`tests/fixtures/`) que reproduzem o formato real das
fontes, entao passam mesmo sem acesso de rede. Para regerar as fixtures:
`python tests/fixtures/make_fixtures.py` (prateleiras), `python tests/fixtures/make_market_fixtures.py`
(base de mercado) e `python tests/fixtures/etf/make_etf_fixtures.py` (catalogo de ETF - fixture
propria, separada da de mercado para nao acoplar aos asserts de contagem de `test_market.py`).
Os testes do `etf-bruto` (`test_etf_bruto*.py`) montam os arquivos da B3 em memoria, com os
cabecalhos reais medidos em 2026-09-24 (`tests/etf_bruto_apoio.py`), e usam um informe diario
publico do Fundos.NET e um regulamento sintetico em `tests/fixtures/etf_bruto/`.
