# Monitor de Prateleiras de Fundos

Monitora a prateleira de fundos de investimento dos concorrentes da XP a partir de
fontes publicas e cruza com o cadastro da CVM para antecipar movimentos de produto.

Duas perguntas que o monitor responde:

1. **O que cada casa oferta hoje?** Amplitude por classe, gestoras representadas,
   aplicacao minima, taxas, liquidez e exclusividade por CNPJ.
2. **O que cada casa esta prestes a lancar?** Fundos registrados na CVM por gestoras
   ligadas a cada casa, com destaque para os que estao em **fase pre-operacional** -
   registrados e ainda nao lancados.

Casas monitoradas: BTG Pactual, Itaú (a prateleira de varejo íon), Nu, Banco Inter,
com a XP como baseline de comparacao. Em toda superficie o nome e so "Itaú" desde
15/10/2026 - nos fundos e na previdencia.

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
| `data/reports/digest-<data>.md` | Digest semanal (oportunidade de grade, ritmo, radar e vitrines) para leitura no repo |
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

**Captacao no mercado** (19/10/2026). As colunas "no mercado" (captacao, cotistas e
PL) sao do fundo inteiro: aplicacoes menos resgates de todos os cotistas, por todos
os distribuidores, somadas do informe diario da CVM - nao a captacao que a casa fez
com o fundo, porque a CVM nao publica fluxo por distribuidor. A definicao fica na aba
Metodologia e no comentario do cabecalho "Captacao no mercado" das abas Gaps XP e
Onde esta a gente (`spreadsheet.DEFINICAO_CAPTACAO_NO_MERCADO`).

### Digest por e-mail

Por padrao o monitor so grava os arquivos - o `digest-<data>.html` tem CSS inline
e nenhum JS, entao pode ser colado num e-mail, enviado por um conector ou aberto
direto. Para envio automatico, configure `SMTP_HOST`, `SMTP_FROM` e `SMTP_TO` no
`.env` e rode com `--send-email`; sem essas variaveis a flag apenas avisa e segue.

**O digest e semanal e curto (v2, 14/10/2026).** So XP, BTG e Itau
(`Config.casas_do_digest`; dashboard, planilha e deck seguem com todas as casas), e
nesta ordem:

| Secao | O que traz |
|---|---|
| Oportunidade de grade e gaps | a mensagem principal: os gaps reais (o que a XP nao tem de forma nenhuma - fora da vitrine e da lista da casa, sem espelho, equivalente nem correlato da mesma gestora, nem so em outra faixa de liquidez) que captam varejo em 3 meses, aceleram na semana e captam mais de R$ 10 MM nela; numeros em destaque, o resumo por concorrente e os 10 primeiros de cada um pela captacao em 3 meses, com o produto proprio do concorrente marcado; e a legenda do ritmo, que abre no clique, logo depois dos numeros |
| Ritmo de captacao | o mapa de ritmo por casa e as maiores captacoes e resgates, com a captacao da semana e o ritmo dela e, ao lado, a captacao do fundo na XP pelas bases internas (coluna "Na XP"); abaixo delas, a captacao de todos os fundos das vitrines por categoria ANBIMA - a visao de mercado -, na semana e em 3 meses, com o ritmo de cada uma |
| Vitrines: entradas e saidas | so o que entrou e o que saiu de cada vitrine |
| Rentabilidade e custo | so na edicao mensal: o primeiro digest de cada mes |

A **semana** e a do ultimo dia completo do informe diario (ao menos 3 dias; senao a
anterior), contra a media por dia util das 12 semanas anteriores - a regra mora em
`report/shared/semanas.py`, a mesma do flash da industria. O dia que ainda esta
chegando sai de todas as somas, e a janela de 3 meses termina no mesmo dia. O ritmo
da semana (acelerando, desacelerando, virou saida...) usa a regra e a faixa de 25% do
ritmo mensal. Todo fundo que a XP tem sai com o servico da lista da casa
(`XP · Distribuição` ou `XP · FaaS`).

Em 15/10/2026, a pedido da area, o digest perdeu mais tres pecas: a frase de
abertura ("67 fundos captam nas vitrines..."), a frase do funil ("504 gaps reais nas
vitrines...") e a evolucao semanal da captacao dos gaps, com as miniaturas por casa.
Ele abre direto na secao; a unica frase antes dela e o alerta de coleta que falhou
inteira, e a previa do e-mail e a linha do cabecalho (casas e semana) - o assunto ja
traz a oportunidade. O ritmo de cada fundo segue na tabela ("Ritmo na semana").

Em 17/10/2026 saiu o radar CVM - o que as gestoras dos grupos BTG e Itau
registraram -, e o assunto deixou de contar registro na CVM. O radar segue no
dashboard, na planilha e no deck, sem fundo cancelado (14/10/2026: so os ativos
contam e a passagem para cancelado nao vira movimento; o extrato gravado segue
inteiro). No mesmo dia entrou a legenda do ritmo, logo depois dos numeros em
destaque, num bloco que abre no clique (no texto puro, a vista): uma tabela com
acelerando, virou entrada, estavel, desacelerando, virou saida, saida persistente e
sem serie, com a faixa de 25% da regra e as duas janelas - a semana contra as 12
anteriores nas tabelas de gaps e por categoria e nas listas, o ultimo mes contra os
anteriores no mapa por casa.

**Por categoria** (17 e 19/10/2026). Na secao Ritmo de captacao, abaixo das maiores
captacoes e dos maiores resgates, a visao de mercado das vitrines: todos os fundos de
XP, BTG e Itau, cada um uma vez, e nao so os gaps, com a captacao de cada fundo no
mercado inteiro (o informe da CVM e por fundo, nao por distribuidor), somados pela
categoria ANBIMA - a mesma da coluna Categoria das listas: captacao na semana e em 3
meses e o ritmo da soma de cada categoria (`report/por_categoria.py`). Entram os
fundos com informe, PL de ao menos R$ 1 mi no inicio e sem movimento estrutural; o
caixa e a previdencia ja sairam da captacao. O e-mail mostra a categoria que mexe ao
menos R$ 50 mi em 3 meses, para entrar ou para sair, e conta as outras numa linha.
A tabela nasceu no dia 17 somando so os gaps (primeiro pela classe, depois pela
categoria), na secao de oportunidade; no dia 19 virou a visao de mercado, na secao
de captacao. No relatorio de 14/10/2026: 1.113 fundos em 30 categorias, 19 na
tabela; entrou dinheiro em Renda Fixa Duracao Alta Credito Livre (+R$ 1,8 bi em 3
meses) e saiu de Renda Fixa Duracao Livre Credito Livre (-R$ 2,8 bi), Multimercados
Macro (-R$ 1,7 bi) e Acoes Livre (-R$ 1,3 bi).

As notas de metodologia de cada secao - o criterio do gap real e da lista, onde
esta o resto na planilha, os fundos caixa, as janelas de rentabilidade e de
captacao - ficam num bloco "Como ler" que abre no clique
(`email_xp.como_ler`, um `<details>`; 17/10/2026): a secao termina nos numeros.
Abre no navegador, no Apple Mail e no Thunderbird; o Gmail e o Outlook nao tem o
recurso e mostram o bloco aberto, como antes. Casa fora da analise e vitrine de
reserva sao aviso e ficam a mostra. O texto puro do e-mail (o `.md`) segue com
tudo a vista.

**Rentabilidade de referencia** (16/10/2026). Toda lista de gaps e de captacoes - no
digest, no painel e na planilha - traz a rentabilidade do fundo em 12 meses (convencao
de lamina: base no fechamento da competencia anterior a janela) e, em pp, a distancia
para a mediana da categoria no universo da CVM. O fundo sem 12 meses de cota mostra 3
meses (base no primeiro pregao), com o prazo escrito na celula; sem serie de cota, "-".
A janela de cada prazo vai na nota da secao. O numero sai dos blocos de rentabilidade
que toda execucao ja calcula (`performance.rentabilidade_por_cnpj`). Na planilha, as
colunas entram em Gaps XP (12 e 3 meses, com as medianas), Captacao por fundo e
Oportunidades de grade (12 meses e mediana), e a nota de cada aba da a janela de cada
coluna (`performance.prazo_das_colunas_de_rentabilidade`). Retorno negativo e captacao
liquida sobre o PL negativa aparecem com o sinal - o formato de taxa os trocava por "-".

**Taxa global nos gaps** (20/10/2026). As tabelas de gaps por concorrente do digest e a
aba Gaps XP ("Taxa global na vitrine (%)") trazem a taxa global de cada fundo -
administracao, gestao e distribuicao, em % ao ano - como o site da casa a mostra, ao
lado da rentabilidade; com o fundo em duas casas, a menor. Vem da vitrine e nao do Open
Finance: o merge da precedencia ao catalogo, cujo `maxAdminFee` vem em fracao (0,02
para 2%) e, em parte dos fundos, so com a taxa de administracao (o Itau Small Cap
Valuation com 0,0005 no Open Finance e 2,0% no site). Como o minimo da vitrine, o merge
guarda a taxa do site antes da precedencia (`FundOffer.taxa_da_vitrine`); snapshot
gravado antes disso a le da divergencia registrada. Medido nas vitrines salvas (XP e
BTG de 28/09, Itau de 14/10/2026, CNPJ contra CNPJ): 354 dos 391 gaps do BTG e os 311
do Itau com taxa global, medianas de 0,90% e 1,70%. A coluna "Menor taxa adm (%)" e o
campo mesclado: a da vitrine e, sem ela, a do Open Finance em % ("Taxa em pontos
percentuais").

**A lista de gaps do digest** (16/10/2026). Entra o gap real que capta em 3 meses e
acelera na semana: ritmo "acelerando" (captacao por dia mais de 25% acima da media das
12 semanas anteriores, que ja era positiva). Estavel, desacelerando, virou saida e,
desde 19/10/2026, virou entrada ficam fora; e, desde a mesma data, o gap tem de
captar mais de R$ 10 MM liquidos na semana (`CAPTACAO_MINIMA_NA_SEMANA`) - o fundo
pequeno que acelera nao muda a grade. Os numeros de cima e as contagens do resumo por
concorrente ("Na lista", "Para trazer") contam a mesma lista. A ordem e a captacao em 3
meses, 10 por concorrente, sem repetir fundo. O produto proprio do concorrente entra, marcado
"produto proprio" na coluna Esforco, e fica fora dos numeros de cima e do assunto, que
contam so o que da para trazer (`report/oportunidade.py`). Ate ali a ordem era esforco e
depois captacao, 5 por casa e sem o produto proprio - e no digest de 14/10/2026, 3 dos
5 primeiros do BTG ja nao aceleravam, um deles tendo virado saida. Medido no mesmo
relatorio: no BTG 25 gaps aceleram na semana (14 para trazer, 11 produto proprio), no
Itau 24 (8 e 16). Sem semana completa no informe, a lista nao filtra pelo ritmo e a nota
diz isso. A planilha segue com a lista inteira, na ordem de esforco.

**A captacao do resumo por concorrente** (pedido de 20/10/2026). As colunas Semana e 3
meses somam todos os gaps reais da vitrine da casa - na lista ou nao, para trazer ou
produto proprio, captando ou resgatando -, com o recorte da tabela por categoria:
informe, PL de ao menos R$ 1 mi no inicio e sem movimento estrutural. A captacao e a do
fundo no mercado inteiro, e a nota do digest diz isso. Ate ali somavam so o que dava
para trazer da lista, e repetiam casa a casa os numeros de cima.

**O Itau em todas as analises** (pedido de 14/10/2026). A oportunidade sai por
concorrente: o resumo de cada um (gaps reais, quantos aceleram, quantos da para
trazer e a captacao deles na semana e em 3 meses) e os 10 primeiros de cada um - numa
lista unica o BTG, de vitrine maior, ocupava o topo inteiro. E a coleta
que falha nao tira a casa das analises: para as casas de `Config.vitrine_de_reserva`
(hoje, o Itau), um dia sem vitrine utilizavel - coleta indisponivel, so o catalogo
Open Finance, menos de 30 fundos ou CNPJ em menos de metade da vitrine - entra com a
ultima vitrine utilizavel de ate 30 dias (`shelf_monitor/reserva.py`), e toda
superficie diz de que dia ela e. O arquivo gravado e o diff continuam os da coleta do
dia (o digest nao inventa entrada nem saida), e a linha final da execucao conta a
casa como nao coletada. Vitrine guardada sem CNPJ ganha uma identificacao na hora do
relatorio quando o modulo sabe fazer isso (`identificar_vitrine`): o Itau Private,
com a pagina fora do ar desde 28/09/2026, volta as analises de previdencia pela
vitrine de 25/09, com o CNPJ pela cota do dia (37 dos 41 planos), marcada com a data.

O assunto ja carrega a novidade, para ser lido sem abrir:
`[Monitor de fundos] 14/10 - 46 fundos para a grade (+220,0 MM na semana), 2 entradas / 1 saída, 1 fundo em operação`.
Entradas e saidas sao da vitrine de varejo das casas do digest - a mesma conta do
corpo do digest. O dia em que a coleta passa a ver uma vitrine sai como
`nova linha de base: XP Investimentos`, nunca como entradas.

### Fontes com link e texto das saidas

Toda saida diz de onde veio cada numero, com o endereco publico para conferir:

| Saida | Onde ficam as fontes |
|---|---|
| Digest (md, html, e-mail) | secao **Fontes** no fim; link no nome de cada casa, no radar CVM e na abertura de captacao e rentabilidade |
| Dashboard | painel **Fontes** no fim (o rodape e a vitrine levam a ele e o abrem); link na nota do placar da visao executiva e nos subtitulos dos blocos que usam CVM |
| Deck | slide **Fontes**, com hyperlink em cada nome |
| Planilha (fundos e previdencia) | tabela **Fontes** na aba Metodologia; link no bloco de periodo das abas de captacao e rentabilidade |

So entra a fonte que foi usada na execucao (`fontes_do_relatorio`): vitrine de casa
que trouxe fundo de vitrine, Open Finance quando alguma casa veio por ele, e cada
conjunto da CVM quando o bloco que depende dele foi calculado. O registro das fontes
e um so, em `report/shared/fontes.py`; cada entrada tem a pagina do conjunto e,
quando existe, o diretorio dos arquivos que o monitor le.

O texto das saidas segue a norma do portugues do Brasil: acento, crase, concordancia
de numero (`texto.contagem`: "1 alteração", "3 alterações") e separador de milhar
("6.631"). Comentarios e docstrings do codigo seguem em ASCII.

**Nome de fundo no padrao das vitrines** (19/10/2026, pedido do usuario). A XP e o
Inter escrevem o nome em caixa de titulo, com as siglas ("AZ Quest Termo FIF RF
LP"); o BTG, o Itau, o Nu e o cadastro da CVM, em caixa alta, e o cadastro ainda
com a forma juridica por extenso. Na exibicao - digest, painel, planilha e deck -
todos saem no padrao da XP (`report/shared/nomes.py`): a forma juridica em sigla
(FIF, FIC FI, CIC, RL) e, no nome em caixa alta, caixa de titulo com as siglas em
maiuscula e os conectores em minuscula ("TREND CASH RENDA FIXA SIMPLES FIF RL" vira
"Trend Cash Renda Fixa Simples FIF RL"). As siglas sao as da forma juridica e do
mercado e as marcas que a XP e o Inter escrevem em sigla (SPX, JGP, AZ, BTG...). So
a exibicao muda: o nome guardado, que os cruzamentos por nome usam, e o CNPJ seguem
os da fonte, e o acento que a fonte nao publica nao e reposto ("Itau Credito
Bancario" fica assim). A outra excecao e o texto
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
A tabela nao traz CNPJ, e o XHR de ~1,6 MB do gateway `apicd.cloud.itau.com.br`
(`charon`) que carrega o dataset vem cifrado - base64, nao JSON (debug de
2026-10-14). O CNPJ vem pelo nome (`shelf_monitor/cnpj_por_nome.py`): nome
normalizado contra o catalogo Open Finance da mesma coleta e o cadastro da CVM,
com empate sem casar - 403 dos 477 fundos (84,5%) em 2026-10-14, contra 27 de 459
(6%) pelo slug do merge. A coleta de 2026-09-28 nao achou fundo nenhum porque a
tabela nao carregou; desde 2026-10-14 o coletor espera a primeira linha, recarrega
a pagina uma vez e confere as linhas lidas contra o "exibindo 10 de 477
resultados" da pagina.

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

## Monitor de ETFs (repositorio a parte)

Desde 07/10/2026 o monitor de ETFs - captacao mes a mes e competicao (`etf`),
dados brutos de cada fonte (`etf-bruto`) e cadastro com leitura do regulamento
(`etf-cadastro`) - mora no repositorio irmao `etf-monitor`, com os mesmos
comandos: `python -m etf_monitor etf`, `etf-bruto` e `etf-cadastro`. O monitor da
industria segue mostrando a camada de ETF ao lado do universo, lendo a captacao
mensal que o etf-monitor grava: aponte `SHELF_ETF_CAPTACAO_CSV` para o
`data/reports/etf_captacao_mensal.csv` dele.

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
O Itau e o Private Bank, nao a prateleira íon. Saia rotulado "Itaú Private" ate
15/10/2026; desde entao o nome e so "Itaú", como no monitor de fundos, e o canal
fica nas notas da casa e no link da fonte.

**Itau Private: CNPJ pela cota, e a pagina fora do ar.** A pagina nao publica
CNPJ, mas publica a cota de cada plano com a data; a mesma cota, no mesmo dia,
numa classe previdenciaria do informe diario e o veiculo
(`previdencia/cota.py`). O que a cota nao identifica tenta o nome, restrito a
classes previdenciarias e com as exclusivas incluidas - todo FIE e exclusivo no
cadastro. Medido na vitrine de 25/09/2026: 37 dos 41 planos pela cota, sem erro;
pelo nome sozinho seriam 17, e com os nomes alternativos da pagina 18, um deles
errado (o "IBIUNA PREVIDÊNCIA" generico no lugar do veiculo do Itau). Desde
28/09/2026 a pagina responde "Pagina indisponivel no momento" (erro no servidor
do Itau, conferido no navegador em 14/10): a coleta sai UNAVAILABLE com esse
motivo, e com `SHELF_BROWSER_DEBUG=1` grava a resposta em
`data/cache/debug/itau-previdencia/`. A Tabela de Previdencia do itau.com.br
(íon, Uniclass, Personnalité) tambem estava vazia em 14/10.

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
- **Itau com CNPJ pelo nome, nao todo.** Desde 2026-10-14 o CNPJ vem do nome
  normalizado (403 dos 477 fundos, 84,5%), o que poe a vitrine acima do piso de
  50% dos gaps. Os 74 restantes: 39 empates - 37 deles o par FIC/master de
  mesmo nome, que a regra deixa sem casar - e 35 sem candidato. Escolher a
  classe de cotas no par FIC/master levaria a 440 de 477 (92,2%), e e decisao
  pendente.
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
| `data/reports/industria-AAAA-MM.xlsx` | mensal | planilha explorador, 17 abas (com Dinamica e Macro) |
| `data/reports/industria-AAAA-MM-DD.md/.html` | mensal | e-mail do mes (640 px, ate 100 KB) |
| `data/reports/industria-flash-AAAA-MM-DD.md/.html` | flash | captacao da semana por macroclasse contra as 12 semanas anteriores |
| `data/historico/industria/AAAAMM.json` | mensal | o bloco publicado, trilha de auditoria (versionado) |
| `data/historico/cadastro/AAAAMM.csv.gz` | toda importacao da base | retrato mensal do cadastro (versionado) |
| `data/cache/industria/` | mensal | perfis compactos e o retrato do universo que o flash usa |
| `data/cache/sgs/` | mensal | CDI, Selic, IPCA e dolar do SGS do Banco Central; competencia fechada nao volta a rede |

Tres regras mudam o numero, e toda superficie diz qual vale:

- **Universo disponivel.** Saem previdencia, exclusivos, masters,
  institucionais, nao residentes e restritos; o funil mostra cada degrau.
  Publico geral, qualificado e profissional ficam, como filtro.
- **Camada do investidor.** PL e captacao sem o dinheiro que um fundo aplica
  em outro, pelo perfil mensal da CVM: o FoF e o espelho nao contam duas
  vezes. Em 08/2026, R$ 3,80 tri contra R$ 4,28 tri brutos do mesmo universo.
- **Estrutura.** Master, feeder, espelho e FoF pela carteira (CDA); a
  estrategia, a gestora e o retorno vem da raiz da cadeia.

Da Fase 2 (entregas 2A e 2B, em 10/10/2026), toda na janela de 12 meses:

- **Retorno contra o CDI.** O CDI acumulado de cada janela vem do SGS do
  Banco Central, na mesma base do retorno (fechamento da competencia anterior,
  exclusive, ao ultimo pregao, inclusive). Cada peer group ganha a mediana em %
  do CDI e a fracao das estrategias acima dele; o explorador, o % do CDI de
  cada produto. Sem o SGS (sem rede e sem cache), sai sem o % do CDI e diz.
- **Macro.** CDI e IPCA do mes, Selic e dolar no fechamento, ao lado da
  captacao por macroclasse, mes a mes.
- **Dinamica da captacao.** Giro (resgates sobre o PL medio, no fluxo
  declarado), concentracao por peer group (HHI e fatia das 5 maiores gestoras),
  grupos gestores que entraram e sairam do universo, captacao por quartil de
  retorno no peer group e a captacao implicita por tipo de cotista. Esta ultima
  sai com o ruido do arredondamento do perfil ao lado: abaixo de duas vezes
  ele, "dentro do ruido".

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
| Digest (`digest-<data>.md/.html`) | `report` / `run` | acompanhar - o resumo semanal por e-mail, so XP, BTG e Itau, com a oportunidade de grade e os gaps reais no topo (rentabilidade e custo na edicao mensal) |

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

**Taxa em pontos percentuais** (20/10/2026). `admin_fee_pct` guarda a taxa em % ao ano
(2,0 para 2%), como a vitrine e o extrato da CVM. O `maxAdminFee` do Open Finance vem
em fracao na XP, no BTG e no Itau (0,02 para 2%) e em percentual na Nu; misturadas, as
escalas faziam a taxa da maioria dos fundos sair 100 vezes menor na "Taxa adm (%)" da
planilha, na mediana de taxa por casa do painel e na menor taxa dos gaps. O coletor do
Open Finance decide a escala por catalogo, pela mediana das taxas positivas (ate 0,1 e
fracao, e cada taxa ate 0,1 vira percentual - `shelf_monitor/taxas.py`), sem aviso de
coleta. No merge, a taxa que a vitrine publica vence a do catalogo, que fica na
divergencia: o `maxAdminFee` e, em parte dos fundos, so a de administracao (Itau Small
Cap Valuation: 0,05% no catalogo, 2,0% no site). A do site so vale ate 20% ao ano, o
teto do extrato: o site do BTG publicava 100,0 no Brasil Plural Debentures Incentivadas.
O snapshot de prateleira passou a versao 2; o da versao 1 e corrigido na leitura, e o
diff contra ele nao acusa mudanca de taxa (BTG de 25/09 para 28/09/2026: as mesmas 21
mudancas reais, agora em %). Medianas das vitrines salvas: XP 1,00%, BTG 0,91%, Itau
1,50% - antes 0,01 a 0,02. O fundo cuja taxa so a vitrine trazia, sem o Open Finance
divergir, e indistinguivel no snapshot antigo: ate 0,1, num catalogo em fracao, e
convertido junto.

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
coberto por espelho (confianca alta), coberto por equivalente (media), coberto por
correlato (media), coberto em outra liquidez, gap de estrategia, gap de gestora, ou
sem dado de carteira. Os cobertos saem da contagem e vao para a aba "Gaps cobertos"
da planilha, com o fundo XP que cobre, o master, o D+ dos dois lados, a distancia e
a sobreposicao.

**Gap real e so o que nao esta coberto** (19/10/2026, pedido do usuario: a aba de
gaps e so do gap efetivo). Alem do fundo da XP (mesma classe, espelho, equivalente
ou correlato), vao para os cobertos o gap que a lista da casa cobre (a XP ja
distribui ou pode distribuir) e o que a XP tem so em outra faixa de liquidez
(`aggregate._cobertura`). A aba "Gaps cobertos" abre com a coluna "Coberto por"
(fundo da XP, lista da casa ou outra faixa de liquidez) e fecha com o esforco de
quem ainda pede acao (expor na vitrine, ativar distribuicao, versao de liquidez); a
aba "Gaps XP", o painel, o deck, o digest, a grade e o consenso so contam o gap
real. No funil a lista da casa e um passo proprio, entre o correlato e a outra
liquidez, e o detalhe do real (de estrategia, de gestora, sem dado) soma o real.

**Correlato** (15/10/2026, pedido do usuario: gap real e so o que a XP nao tem de
forma nenhuma). E outra versao da mesma estrategia da mesma gestora (raiz do CNPJ
do gestor) que a carteira nao ligou: o nome da estrategia e o mesmo depois de tirar
forma juridica, classe de ativo, a marca da gestora e as marcas de versao - o
feeder de cada distribuidor ("Advisory", "Seleção", "Selection", "Access", "IU"), o
publico ("Institucional", "Alocadores", "Dist."), o papel ("Feeder", "Master"), a
faixa ("Plus", "Premium", "NTNB35"), numero, letra de classe e o clone romano ("II")
(`familias.palavras_da_estrategia`). Quando o nome dos dois lados e so marca ("TB
SELEÇÃO" e "TB ADVISORY"), decide a mesma categoria ANBIMA. Medido no digest de
14/10/2026: 51 dos 504 gaps reais eram correlatos - o Kapitalo Tarkus FIC do BTG
contra o Tarkus Advisory da XP, o RPS Long Bias Selection D30 contra o Advisory D30,
o HIX Capital FIC contra o Institucional -, e a grade caiu de 67 para 54 fundos.
Desde 19/10/2026 (pedido do usuario) tambem e correlato o nome de um contido no do
outro, com a mesma categoria ANBIMA: a XP tem o Itau Artax Endurance, entao o Itau
Artax nao e gap - os masters sao outros e a carteira nao os liga, mas {ARTAX} esta
em {ARTAX, ENDURANCE} e os dois sao Multimercados Livre. A parte comum precisa de
uma palavra propria da familia - usada por ate 3 donos de estrategia (a gestora do
master) no mercado: ARTAX 2, SINFONIA 2, JANEIRO 3; AM 9, ESG 16, HEDGE 100, INFRA
227. Sem a trava, o "Verde AM Patrimonio" virava versao do "Verde AM X60" e o "JGP
ESG", do "Regia Equities ESG" (17 dos 19 casos de 14/10/2026); com ela, sobram o
Itau Sinfonia e o Itau Janeiro Onze. O de mesmo nome vem antes; em outra categoria
(o Artax Long Bias, se fosse de acoes) continua gap. E a
unica heuristica de nome que reclassifica; o "possivel equivalente" (mesma gestora e
categoria, sem carteira que decida e com OUTRO nome de estrategia - o MAN AHL Target
Risk contra o Gama Pearl Diver, ambos da Gama) **informa e nunca remove** um fundo
da lista.

A gestora que decide o esforco e a **da estrategia** (a do master), nao a do
veiculo: o BTGP Thunderbolt, feeder de marca propria do BTG, investe no SPX Raptor
- saia "produto proprio do concorrente", e a XP exibe o SPX Raptor 180 Advisory.
Desde 19/10/2026 (pedido do usuario) a regra vale para o monitor inteiro: o fundo
espelho (master pela carteira) fica com a gestora do master
(`familias.anotar_gestora_do_master`, lida primeiro por `aggregate._offer_manager`)
na captacao, na grade, no consenso, no produto proprio, na gestora da casa, nas
vitrines do digest e nas abas da planilha; a coluna "Gestora (veículo)" e a aba
Consolidado guardam a do registro. Medido em 14/10/2026: 188 dos 1.334 fundos da
captacao estavam com a gestora do veiculo - 147 "Seleção" do Itau, 19 Access do
BTG -, e o BTGP Access SPX Nimitz saia como produto proprio do BTG. A
escada de esforco, do mais barato ao mais caro: expor na vitrine, ativar
distribuicao (as duas so com a lista distribuivel), inclusao operacional, versao
de liquidez (custa o mesmo que a operacional, mas a estrategia ja esta na
prateleira), abrir gestora, gestora nao identificada, produto proprio do
concorrente. Desde 19/10/2026 expor, ativar e a versao de liquidez so marcam os
cobertos: o gap real comeca na inclusao operacional. A decisao de consenso
(`insights`) e as oportunidades de grade (`captacao`) usam o mesmo veredito: fundo
coberto nao vira lacuna em nenhum bloco.

`python -m shelf_monitor familias "NOME"` lista todas as familias de uma gestora
(ou a de um fundo, pelo CNPJ): versoes, papel de cada classe, D+, perfil de
carteira, PL e em que vitrine cada classe aparece; o console resume os espelhos
que nenhuma vitrine mostra (a SPX Falcon tem 46) e o `--json` traz todos. A aba
"Familias" da planilha traz as familias das gestoras dos gaps que tem alguma
classe numa vitrine ou na lista.

Custo, medido em 2026-09-26: cada competencia da CDA (~22 MB) e resumida uma vez
em `data/cache/carteira/` (~13 s, ~2,5 MB); com o cache, o mapa monta em ~10 s.
`--no-carteira` em `run`, `report` e `export` pula o mapa, e o bloco volta a
comparar CNPJ contra CNPJ, com o aviso.

### Lista distribuivel

A vitrine publica nao e tudo que a XP distribui. CSVs soltos em
`data/distribuivel/entrada/` (fora do git: dado comercial interno; modelo em
`data/distribuivel/modelo.csv`) viram uma segunda base de cobertura:

- separador `;` ou `,`, UTF-8 com ou sem BOM, ou Latin-1; colunas por nome:
  `cnpj` (ou `codigo_cvm`), `situacao` (`distribui` ou `pode_distribuir`; vazio
  vale `distribui`), `servico` (`Distribuição` ou `FaaS` - o digest escreve ao lado
  de todo fundo da casa; valor desconhecido mantem o fundo, sem servico, com aviso),
  e opcionais `nome`, `gestora`, `data_referencia`, `observacao`. A lista minima e
  `cnpj;nome;servico`;
- CNPJ da classe, CNPJ do fundo (vale para todas as classes dele, com aviso) ou
  codigo CVM, resolvidos contra o cadastro; arquivos unidos, repetido fica com
  `distribui`;
- linha que nao se resolve (CNPJ invalido, situacao desconhecida, fundo fora do
  cadastro) e listada com arquivo, linha e motivo no fim da aba "Gaps XP".

As duas lentes sao independentes. Com a vitrine, gap da vitrine que a lista cobre
nao e gap: vai para os cobertos (motivo "lista da casa"), com o esforco "expor na
vitrine" (ou "ativar distribuicao"). Sem a vitrine da XP (dia de bloqueio de CDN),
a lista vira a base e o aviso diz que a vitrine faltou. Sem CSV na pasta, a lente
sai suprimida com o motivo.

**A gestora da lista e gestora da XP** (19/10/2026, pedido do usuario). A gestora
com algum fundo na lista conta como gestora que a XP distribui, como a da vitrine:
decide a coluna "Gestora na base XP", o gap de estrategia contra o de gestora, o
esforco (inclusao operacional contra abrir gestora), os numeros do digest, a
divisao da grade e o consenso - uma regra so, `aggregate.gestoras_da_casa`. A
gestora do fundo da lista vem do cadastro da CVM (a coluna `gestora` do CSV so vale
para fundo que o cadastro nao traz) e de qualquer vitrine que o exiba: o Tork
Advisory 30 nao esta na base de mercado de 07/10/2026, mas o BTG o publica com a
gestora Tork Capital - e com ele a XP tem a Tork. O nome se compara sem a forma
juridica (`enrich.assinatura_da_gestora`: "TORK CAPITAL GESTÃO DE RECURSOS LTDA."
e "Tork Capital Gestao de Recursos Ltda" dao "TORK CAPITAL"); com o mapa de
familias, a gestora conhecida so pelo nome entra pela raiz do CNPJ
(`MapaDeFamilias.raizes_das_gestoras`). A marca no inicio do nome nao decide:
medida nos feeders da XP registrados em gestora da casa, ligava o "Fundo 24 Horas"
ao FGC e o "First Trust" a First Who Capital. Ate ali so a vitrine contava, e a
gestora que a XP distribui fora da vitrine saia como "abrir gestora".

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
marcado `gestor_inferido`, nunca como oficial. O XHR do gateway do Itau nao e via:
vem cifrado. Desde 2026-10-14 o coletor do Itau resolve o CNPJ pelo nome
(`shelf_monitor/cnpj_por_nome.py`) antes do merge, e o fundo resolvido entra pela
via (0) como qualquer outro.

### Captacao na XP

Pedido de 20/10/2026: ao lado das maiores captacoes e resgates do digest e de todo
fundo da aba "Captacao por fundo" da planilha, quanto o fundo captou **na XP**. O
informe da CVM e por fundo, nunca por distribuidor; a captacao na XP vem de duas
bases internas, soltas em pastas fora do git (dado comercial; modelos em
`data/captacao_xp/modelo_diaria.csv` e `modelo_mensal.csv`):

- `data/captacao_xp/diaria/`: uma linha por fundo e dia; `data/captacao_xp/mensal/`:
  uma linha por fundo e mes;
- CSV (`;` ou `,`) ou Excel `.xlsx` (primeira aba; o cabecalho pode vir depois de
  linhas de titulo); colunas por nome, em qualquer posicao: `cnpj` ou `cod_cnpj`
  (ou `codigo_cvm`), como texto ou numero; a data (`data`, `dia`, `data_movimento`)
  ou o mes (`mes`, `competencia`, `ago/2026`, `2026-08`, uma data do mes) - sem
  nome conhecido, a primeira coluna so com datas, com aviso dizendo qual; e
  `captacao_liquida` ou `captacao` sozinha - ou `captacao_bruta`/`aplicacao` e
  `resgate`. As bases da XP trazem `cod_cnpj` em numero na coluna N e `captação` na
  S (20/10/2026). CNPJ em notacao cientifica ("1,3E+13", o CSV salvo pelo Excel)
  perdeu digitos e e recusado com esse motivo. Valores em reais; "(R$ mil)" ou
  "(R$ MM)" no cabecalho multiplicam;
- linhas do mesmo fundo e dia somam no arquivo; o mesmo dia em dois arquivos vale
  o do ultimo pela ordem do nome (reexportar nao conta duas vezes); linha ruim sai
  listada com o motivo (`captacao_interna.py`).

Os dois numeros comparam a mesma coisa (`report/captacao_na_xp.py`):

- **mesma janela**: a semana e os 3 meses do bloco de captacao, com os mesmos dias.
  O mes fechado sai da base mensal; o mes corrente e a semana, da diaria, ate o
  ultimo dia completo do informe. A janela que as bases nao cobrem inteira fica sem
  numero, com o motivo na nota ("a base diaria da XP vai ate 07/10/2026") - nunca a
  soma parcial ao lado dos 3 meses da CVM;
- **mesmo fundo**: so o fundo que a XP distribui (vitrine ou "distribui" na lista da
  casa; "pode distribuir" nao conta). Sem ele, os fundos que a XP distribui com o
  mesmo master pela carteira da CVM, somados ("pelo espelho", "pelo master", "por 2
  fundos do mesmo master"). Classe paralela e correlato ficam de fora: outra
  carteira, outro fluxo. O fundo que a XP nao distribui fica sem numero, e nao com
  zero; o distribuido sem linha num dia (ou mes) que a base traz captou zero.

Onde o mapa acaba: a data da base interna pode seguir o pedido do cliente, e a do
informe da CVM, a cotizacao - em fundo de cotizacao longa, a captacao da semana na XP
e a da CVM podem cair em semanas diferentes. Sem arquivo nas pastas, o digest e a
planilha saem como antes, sem a coluna.

### Fundos caixa/simples fora das analises

Fundo caixa e fundo simples sao produto padrao: toda casa tem o seu, e nenhum vira
decisao de grade. Eles enchiam o topo da captacao - 3 das 5 maiores captacoes do
digest de 14/10/2026 - e somavam gaps que ninguem vai trazer. Desde 16/10/2026 saem
da captacao, dos gaps (e com eles da grade), da rentabilidade ponderada e do custo
(`shelf_monitor/caixa.py`). A regra, na versao "nucleo":

- so renda fixa;
- rotulo ANBIMA de caixa (Renda Fixa Simples, Duracao Baixa Soberano ou Duracao
  Baixa Grau de Investimento) **ou** a marca no nome: Simples, Selic, Referenciado
  DI (Ref DI, RF DI, DI CP). So o rotulo deixava caixa passar: o BTG Tesouro Selic
  esta como "Duracao Livre Grau de Investimento" no cadastro, e so o nome o denuncia;
- CDB e credito bancario fora desses rotulos ficam: o BTG CDB Plus (Duracao Livre
  Grau de Investimento) continua na analise.

Cada bloco conta o que tirou (`caixa_fora`), e o digest diz isso em uma linha na
captacao, nos gaps e na rentabilidade e custo da edicao mensal. Nos gaps a conta e
sobre os gaps aparentes (CNPJ contra CNPJ), antes do veredito de cobertura. Medido
no relatorio de 14/10/2026: 93 dos 1.497 CNPJs das vitrines cruzaveis (XP 53, BTG
41, Itau 9, Inter 24); 81 dos 1.334 fundos com informe, com R$ 7,9 bi dos R$ 23,1 bi
de captacao positiva; 39 gaps aparentes, dos quais 21 eram gap real (14 dos 453 do
digest, contra BTG e Itau); a grade foi de 54 para 53 fundos.

Continuam com todos os fundos: o tamanho das vitrines, as entradas e saidas, o radar
CVM, os blocos descritivos (competitivo, mandato, rentabilidade por fundo) e a
mediana de cada categoria na rentabilidade e no custo, que segue sendo a do mercado.

### Fundos de previdencia fora das analises

A previdencia tem o monitor proprio (`shelf_monitor.previdencia`). Desde
17/10/2026, o fundo de previdencia que aparece numa vitrine de fundos sai da
captacao, dos gaps (e com eles da grade e da tabela por categoria), da
rentabilidade e do custo do monitor de fundos, como o caixa
(`shelf_monitor/previdencia_fora.py`). A regra: o rotulo ANBIMA comeca por
"Previdencia", ou o nome traz a marca - Prev, Previdencia, FIE, VGBL, PGBL. Cada
bloco conta o que tirou (`previdencia_fora`), e o digest diz isso nas notas.
Medido nas vitrines de 14/10/2026: 5 fundos, todos pelo rotulo (3 na XP e 3 no
BTG, um nas duas - debentures incentivadas que o cadastro classifica como
previdencia), com +R$ 30 mi em 3 meses; 2 deles eram gap. O monitor de
previdencia, que analisa justamente esses fundos e passa pelos mesmos blocos,
desliga a regra (`ConfigPrevidencia.tira_previdencia`).

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

**O ritmo da XP pela prateleira aberta** (20/10/2026). No mapa de ritmo por casa
(acelerando, virou entrada, estavel, desacelerando, virou saida, saida persistente), a
linha da XP conta a vitrine do site, como a das demais casas: o que qualquer cliente ve
e compra, com o mesmo recorte (informe, PL de ao menos R$ 1 mi no inicio, sem
movimento estrutural). Vale no digest, no painel e na aba Captacao por casa da
planilha, que leem o mesmo quadro. De 18 a 20/10/2026 a linha saia da lista
distribuivel ("XP Investimentos (lista)"), que soma fundos fora da vitrine - assessoria,
private, FaaS - e por isso nao comparava a mesma coisa que as linhas do BTG e do Itau.
A lista segue onde ela responde outra pergunta: cobertura dos gaps, gestora da casa,
servico ao lado do fundo, custo da XP e o cruzamento da captacao na XP.

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

**Por classe de fundo e a XP pela base interna** (16/10/2026). Cada casa sai tambem por
classe - o nivel 1 da ANBIMA (renda fixa, multimercados, acoes, cambial...), o eixo
grosso do monitor (`competitive.classe_grossa`). A conta e a mesma, mediana dos
indices, dentro de cada classe; com menos de 5 fundos comparaveis na casa
(`MIN_COMPARAVEIS_CLASSE_CUSTO`) a classe fica sem indice, com a contagem. Medido em
15/10/2026: renda fixa, multimercados e acoes tem de 41 a 136 comparaveis por casa;
cambial, de 1 a 4. O estruturado sem categoria ANBIMA (FIDC, FII, FIP...) sai no balde
"Estruturado" tambem pelo tipo do cadastro, nao so pelo nome. O custo da **XP sai da
base interna** - a lista distribuivel, os fundos de situacao "distribui", menos as
classes exclusivas (veiculo de um cotista) -, e o das demais casas, da vitrine do site.
O fundo caixa/simples sai da lista como sai das vitrines (`caixa.sem_caixa`, depois da
troca): a oferta da lista leva a categoria do cadastro, que e o que a regra le, e a
nota da XP diz quantos sairam.
Publico profissional fica, como fica nas vitrines (2 na XP, 8 no BTG, 4 no Itau). Sem
lista em `data/distribuivel/entrada/`, a XP volta a vitrine e a nota do bloco diz por
que. Superficies: digest mensal (tabela casa x classe), painel (no bloco de custo),
planilha (aba "Custo por classe" e a coluna "Base da prateleira" em "Custo e acesso")
e deck (segunda tabela no slide de custo). O indice da casa e o da classe vao com duas
casas: com tres, 1,625 saia 1,62x no digest e 1,63x no painel.

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
`python tests/fixtures/make_fixtures.py` (prateleiras) e `python tests/fixtures/make_market_fixtures.py`
(base de mercado). Os testes de ETF foram com o monitor de ETFs para o repositorio etf-monitor.
