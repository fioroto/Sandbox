# Monitor de Prateleiras de Fundos

Monitora a prateleira de fundos de investimento dos concorrentes da XP a partir de
fontes publicas e cruza com o cadastro da CVM para antecipar movimentos de produto.

Duas perguntas que o monitor responde:

1. **O que cada casa oferta hoje?** Amplitude por classe, gestoras representadas,
   aplicacao minima, taxas, liquidez e exclusividade por CNPJ.
2. **O que cada casa esta prestes a lancar?** Fundos registrados na CVM por gestoras
   ligadas a cada casa, com destaque para os que estao em **fase pre-operacional** -
   registrados e ainda nao lancados.

Casas monitoradas: BTG Pactual, Itau (Ion), Nu, Banco Inter, com a XP como baseline
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
| `data/reports/dashboard.html` | Dashboard comparativo (abre no navegador, sem dependencia externa) |
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
python -m shelf_monitor watchlist-discover "BTG"  # refina a watchlist de gestoras
python -m shelf_monitor run --git-commit          # commita snapshots e relatorios
python -m shelf_monitor run --send-email          # envia o digest por SMTP
python -m shelf_monitor run --no-market           # nao importar a base se faltar
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
`[Monitor de fundos] 29/08 - 2 fundo(s) em operacao, 5 entradas / 1 saidas`.

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
python -m shelf_monitor etf                 # catalogo + PL mensal dos ETFs + fluxo semanal
python -m shelf_monitor etf --export        # idem, gravando planilha .xlsx e CSV
python -m shelf_monitor run --with-etf      # roda junto da coleta normal (opt-in - baixa ~33MB/mes extra)
```

O PL mensal (balancete de FIE) sai em toda execucao do `etf`: sao ~0,1 MB por competencia, ordens de
grandeza abaixo do INF_DIARIO, e e a unica fonte medida que alcanca o ETF em si - nao so os feeders.

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
219 dos 249 ETFs do catalogo (88%), R$ 130,5 bi somados, contra **0 de 249** no INF_DIARIO. Os ETFs
nao tem diretorio proprio nos dados abertos - nao existe `/dados/FIIM/` nem `cda_fiim_*`: eles vivem
sob FIE (Estruturados), em `/FIE/DOC/BALANCETE/DADOS/balancete_fie_{AAAAMM}.zip` (~0,1 MB por
competencia) e tambem no membro `cda_fie_{AAAAMM}.csv` de `/FI/DOC/CDA/DADOS/cda_fi_{AAAAMM}.zip`
(204/249). O balancete e a escolha: cobre mais, pesa 150x menos e traz o PL direto. As contas do
plano COFI foram identificadas por medicao e a identidade `60000002 = 61100004 + 61800005`
(PL = capital de cotas + resultado acumulado) fecha com desvio zero nos 219 - e virou guarda no
codigo: se a CVM renumerar o plano, o registro sai marcado como suspeito em vez de publicar um PL
que e outra coisa.

O balancete **nao** separa captacao de valorizacao, e o modulo diz por que: entre 07 e 08/2026 o
resultado acumulado ficou identico nos 213 ETFs presentes nos dois meses, ou seja a conta de cotas
absorve marcacao a mercado junto com integralizacao. Por isso a coluna dele se chama variacao de PL,
nao captacao. **Quem separa e o informe diario do Fundos.NET** - ver abaixo.

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

O **balancete mensal de FIE** saiu do escopo futuro e esta implementado (ver acima). Seguem fora,
mapeadas: a **carteira mensal** (membro `cda_fie_AAAAMM.csv`, o que cada ETF carrega - o PL ja vem
do balancete, entao isto interessa para composicao, nao para tamanho), o **Boletim de Fundos da
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
| Dashboard (`dashboard.html`) | `report` / `run` | explorar - matriz casa x classe ANBIMA, gaps da vitrine XP, arquitetura aberta, custo/ticket por quartis, exclusivos |
| Deck executivo (`prateleiras-<data>.pptx`) | `report` / `run` | decidir - ate 9 slides para forum/comite (incl. gaps XP), gerados a cada coleta |
| Planilha (`prateleiras-<data>.xlsx`) | `export` | trabalhar - abas Vitrine varejo (tabela-dinamica-ready), Gaps XP, Gaps por categoria, Movimentos e Metodologia |
| Digest (`digest-<data>.md/.html`) | `report` / `run` | acompanhar - o resumo do dia por e-mail, com a secao de gaps XP no topo |

Regras de leitura que valem nas quatro: quartis em vez de media (a media esconde a
forma da prateleira), "gestora da casa" definida pela mesma watchlist do radar CVM,
coleta suspeita nunca vira churn, e todo numero carrega proveniencia (fonte, data,
cobertura) no rodape.

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
listado com motivo (o Nu, que so coleta Open Finance, sai por construcao). Duas
colunas heuristicas - "gestora ja na vitrine XP" (gap de fundo vs. gap de
relacionamento) e "possivel equivalente XP" (feeder proprio com outro CNPJ,
casado por slug de nome) - **informam e nunca removem** um fundo da lista. Sai nas
quatro superficies com os mesmos numeros; a lista completa mora na aba "Gaps XP"
da planilha, com coluna de verificacao em formula contra a aba "Vitrine varejo".

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
