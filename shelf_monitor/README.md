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
no BTG, "baixo risco" no Itau). Com a base de mercado importada
(`python -m shelf_monitor market`), todo fundo com CNPJ e carimbado com a
classificacao ANBIMA e CVM oficial, gestor, situacao e PL do registro - e a
comparacao por classe passa a significar a mesma coisa nas cinco casas. A
classificacao publicada pela casa, quando diverge, e preservada
(`classificacao_anbima_casa`): a divergencia em si e informacao. A cobertura do
join sai no rodape de cada superficie.

| Superficie | Comando | Para quem |
|---|---|---|
| Dashboard (`dashboard.html`) | `report` / `run` | explorar - matriz casa x classe ANBIMA, arquitetura aberta, custo/ticket por quartis, exclusivos |
| Deck executivo (`prateleiras-<data>.pptx`) | `report` / `run` | decidir - 8 slides para forum/comite, gerados a cada coleta |
| Planilha (`prateleiras-<data>.xlsx`) | `export` | trabalhar - abas Vitrine varejo (tabela-dinamica-ready), Movimentos e Metodologia |
| Digest (`digest-<data>.md/.html`) | `report` / `run` | acompanhar - o resumo do dia por e-mail |

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

**Gestora quando a fonte nao publica.** A tabela do Itau nao traz CNPJ nem gestora.
Tres vias, na ordem de confianca: (1) o join por CNPJ quando a reconciliacao por
nome encontra o gemeo no Open Finance - exata pelo slug, ou aproximada com razao
>= 0,95, candidato unico por margem e bloqueio de tokens que distinguem
master/feeder/espelho (a proveniencia fica em `reconciliacao`); (2) inferencia pela
marca no prefixo do nome ("Bahia AM Muta..." -> Bahia AM), contra um indice de
marcas construido das gestoras da base CVM - so marcas inequivocas entram ("ITAU"
fica de fora porque aponta para Itau Asset, Itau DTVM e Intrag), e o dado sai
marcado `gestor_inferido`, nunca como oficial; (3) o XHR de ~1,5 MB do gateway do
Itau, capturado pelo `diagnose --browser`, que deve trazer o dataset completo -
parser pendente de evidencia parseavel.

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
`python tests/fixtures/make_fixtures.py`.
