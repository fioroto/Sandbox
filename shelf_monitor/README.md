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

| Casa | Estrategia | Fonte |
|---|---|---|
| XP | Open Finance -> Playwright | lista de fundos (SPA) |
| BTG | Open Finance -> Playwright | catalogo de fundos (SPA) |
| Itau | Open Finance -> scraping | catalogo Personnalite |
| Inter | scraping -> Open Finance | paginas estaticas por fundo, via sitemap.xml |
| Nu | Open Finance | app NuInvest encerrado; sem prateleira web deslogada |

**Open Finance Brasil** e a estrategia preferida onde disponivel: a API de Dados
Abertos de Investimentos (`/open-banking/opendata-investments/v1/funds`) e publica,
sem autenticacao, e entrega CNPJ do fundo, gestor, categoria ANBIMA, taxas, minimo e
prazos de resgate em formato padronizado - sem depender do layout do site. A
completude varia por instituicao, entao o scraping continua como reconciliacao.

**CVM**: `registro_fundo_classe.zip` (pos-CVM 175) como fonte primaria, com
`cad_fi.csv` como fallback. O download bruto nao e versionado; so o extrato filtrado
pela watchlist de gestoras.

## Area logada

A v1 usa apenas fontes publicas. A arquitetura preve coletores autenticados sem
guardar credenciais: `AuthenticatedBrowserCollector` (em `collectors/base.py`) espera
um `storage_state` do Playwright que voce exporta apos fazer login manualmente, e o
reusa ate expirar. Para implementar um, sobrescreva `_collect_authenticated`,
registre a classe em `collectors/__init__.py` e aponte
`SHELF_AUTH_STATE_<PLATAFORMA>` para o arquivo de sessao.

O Nu e o caso que mais se beneficia disso: sem app web deslogado, e a unica via para
ver sua prateleira.

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
