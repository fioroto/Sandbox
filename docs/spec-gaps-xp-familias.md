# Gaps da vitrine XP por estratégia: famílias, espelhos, liquidez e a lista distribuível

> Triagem: `ready-for-agent`. Spec gerada em 26/09/2026 a partir do código do branch
> `claude/xp-shelf-gaps-insights-uxlsvq` e de medição sobre os dados em disco.

## Status: implementada em 26/09/2026

Medido com o `report` de 26/09/2026 (vitrines de 25/09, carteira da CVM de 08/2026, com
07 e 06/2026 como recuo): **621 gaps aparentes → 59 cobertos por espelho, 1 por
equivalente, 14 em outra liquidez → 547 gaps reais** (179 de estratégia, 148 de gestora,
220 sem carteira que permita decidir). O total aparente é 621, e não os 636 da medição
abaixo, porque o `report` roda com o enriquecimento, que tira os restritos da vitrine.

O que a implementação mudou em relação ao texto abaixo, e por quê:

- **Equivalência exige também ativos em comum** (`LIMIAR_SOBREPOSICAO` = 0,6, além da
  distância por faixa ≤ 0,15). Só com o vetor por faixa, 28 gaps viravam "equivalente",
  e boa parte eram feeders offshore diferentes do mesmo gestor local, todos "100%
  exterior" (Schroder GAIA, MAN AHL, Pearl Diver). Com a sobreposição de emissores,
  ações e fundos offshore, fica 1. A calibração está no README.
- **Carteira sob sigilo torna o perfil indeterminado.** A CVM publica o tipo da posição
  sigilosa, mas não o ativo. Uma carteira quase toda sob sigilo tinha perfil por faixa e
  nenhum ativo para comparar, e o veredito virava "gap de estratégia" quando o certo é
  "sem dado".
- **Ligação espelho → master recua de competência** quando a cota do master está sob
  sigilo na mais recente (8 espelhos a mais).
- **Escada de esforço:** "versão de liquidez" vem depois de "inclusão operacional", não
  antes. O custo é o mesmo (a gestora já é distribuída), mas a estratégia já está na
  prateleira; na ordem da spec, o digest abria com dez fundos que não eram gap real.
- **A lista distribuível entra na proveniência do bloco** (notas de rodapé nas quatro
  superfícies, com arquivos e data), não no registro de fontes, porque ali toda fonte
  tem um link público.
- A **consulta por gestora** no console mostra o master e as classes que aparecem em
  alguma vitrine ou na lista, e resume os demais espelhos numa linha com as faixas de
  liquidez (a SPX Falcon tem 46); o `--json` traz todos.

## Problema

Hoje o monitor chama de gap todo fundo de varejo que aparece na vitrine de um concorrente e
não aparece na vitrine da XP, cruzado pelo CNPJ da classe. A conta é exata para "este CNPJ
não está na vitrine", mas não responde à pergunta que o comitê faz: **o cliente da XP tem
acesso a esta estratégia?**

As gestoras vendem a mesma estratégia com CNPJs diferentes em cada distribuidor: um master e
um espelho (feeder) por casa — as versões "Advisory" na XP, "Access" no Inter, a marca própria
no BTG — e versões paralelas da mesma carteira com liquidez diferente (um crédito de emissores
bancários em D+5 e outro em D+30, por exemplo). Cada um desses CNPJs vira um gap, embora a XP
já distribua a estratégia. O efeito:

- a lista infla com falsos gaps, e o plano de ação manda "incluir" o que já está na prateleira
  (Giant Zarathustra FIC, Absolute Atenas P, SPX Seahawk, Occam Equity Hedge: todos espelhos do
  mesmo master que a versão Advisory da vitrine XP);
- o esforço sai errado: o BTGP Thunderbolt, feeder de marca própria do BTG, é rotulado "produto
  próprio do concorrente", quando o master é da SPX e a XP já exibe o SPX Raptor 180 Advisory;
- a diferença real de liquidez some: a XP tem o Sparta Max em D+121 e o concorrente oferece a
  mesma estratégia em D+61. Isso é lacuna — mas de outra natureza, e com outra ação;
- a vitrine capturada no site não é tudo o que a XP distribui ou pode distribuir. Um fundo fora
  da vitrine pode já estar disponível para distribuição, e aí a ação é expor, não incluir.

**Medido em 26/09/2026**, com as vitrines coletadas em 25/09/2026 e a carteira mensal da CVM
(CDA) de 08/2026:

| Leitura | Gaps |
|---|---:|
| Gaps por CNPJ (vitrine concorrente fora da vitrine XP) | 636 |
| … com carteira publicada na CDA de 08/2026 | 515 |
| … que são espelho (≥ 95% do PL em cotas de um único fundo) | 201 |
| … espelho do mesmo master que um fundo da vitrine XP | **62** |
| … desses 62, com D+ de resgate diferente da versão XP | 21 |
| … desses 21, em faixa de liquidez diferente | 14 |
| Restantes com fundo XP da mesma gestora e mesma categoria ANBIMA | 135 |
| … com o mesmo D+ | 68 |
| Marcados como "fundo espelho" no extrato da CVM | 44 |

O flag declarado de fundo espelho marca 44 dos 201 espelhos: o dado declarado não basta, é
preciso ler a carteira. Os 135 candidatos da mesma gestora e categoria só viram "equivalente"
com a carteira confirmando.

## Solução

O gap deixa de ser "CNPJ ausente" e passa a ser "estratégia ausente". Três peças:

1. **Mapa de famílias.** Para cada gestora, o monitor monta as famílias de estratégia a partir
   do cadastro e do extrato da CVM e da carteira mensal (CDA): o master, os espelhos que
   investem nele (em qualquer nível), as classes paralelas com carteira equivalente, o perfil
   de carteira da família (quanto em soberano, crédito bancário, crédito corporativo, ações,
   exterior…) e a liquidez de cada versão. Consultável por gestora ("todas as famílias da
   Kinea") e por CNPJ ("a que família pertence este fundo, e a XP a tem?").
2. **Duas bases de cobertura.** A análise de gap roda contra (a) a vitrine da XP capturada do
   site, como hoje, e (b) a lista de fundos que a XP distribui ou tem capacidade de distribuir,
   lida de uma pasta de CSVs. As duas lentes saem lado a lado, cada uma com a própria supressão.
3. **Veredito de cobertura por gap.** Cada gap aparente recebe um veredito — coberto por
   espelho, coberto por equivalente, coberto em outra liquidez, gap de estratégia, gap de
   gestora, sem dado de carteira — com a evidência (qual fundo da base cobre, por qual ligação,
   com que confiança) e um esforço que passa a incluir "expor na vitrine" e "ativar
   distribuição". A lista vira um funil — gaps aparentes → cobertos → gaps reais —, igual nas
   quatro superfícies. Nada some: o coberto sai da contagem de gaps reais e continua listado com
   a prova.

## Histórias de usuário

1. Como analista de produtos da XP, quero que um fundo concorrente que é espelho do mesmo master
   de um fundo da vitrine XP não conte como gap, para que a lista mostre só estratégias que o
   cliente da XP de fato não acessa.
2. Como analista, quero ver, para cada gap coberto, qual fundo da XP o cobre e por qual ligação
   (mesmo master, carteira equivalente), para conferir a decisão do monitor.
3. Como analista, quero que espelho e equivalência sejam decididos pela carteira publicada na
   CVM, e não só pelo flag declarado de fundo espelho, porque o flag marca menos de um quarto
   dos espelhos.
4. Como analista, quero que a ligação espelho → master siga vários níveis (FIC de FIC), para que
   um feeder de feeder chegue ao mesmo master.
5. Como analista, quero que classes da mesma gestora com carteira equivalente e sem ligação
   estrutural (classes paralelas) sejam reconhecidas como a mesma estratégia, para que uma versão
   paralela não apareça como gap.
6. Como analista, quero que a equivalência por carteira exija a mesma gestora, a mesma categoria
   ANBIMA e um perfil de carteira próximo, para não juntar estratégias que só compartilham o
   rótulo.
7. Como analista, quero ver a liquidez de cada versão da estratégia (D+ de cotização mais D+ de
   pagamento do resgate), para saber se a versão que a XP tem serve ao mesmo cliente.
8. Como analista, quero que uma estratégia coberta só em outra faixa de liquidez apareça como
   "coberto em outra liquidez", separada dos cobertos e dos gaps reais, para decidir se vale
   buscar a outra versão.
9. Como analista, quero que o perfil de carteira separe crédito de emissor bancário (CDB, LF,
   DPGE) de crédito corporativo (debêntures, CRI, CRA), para que um "crédito bancário D+5" não
   seja tomado por equivalente de um crédito corporativo D+30.
10. Como analista, dada uma gestora, quero listar todas as famílias de estratégia dela — master,
    espelhos, versões de liquidez, perfil de carteira, PL e em que vitrines cada membro
    aparece —, para preparar uma conversa com a gestora.
11. Como analista, quero consultar a família de um CNPJ específico, para responder na hora "a XP
    já tem isso?".
12. Como analista, quero que a consulta por gestora aceite nome ou CNPJ e resolva pela gestora
    oficial da CVM, para não depender de como cada casa escreve o nome.
13. Como analista, quero que a gestora seja identificada pela raiz do CNPJ do gestor, para que
    filiais da mesma empresa não virem gestoras diferentes.
14. Como analista, quero soltar numa pasta um ou mais CSVs com os fundos que a XP distribui ou
    pode distribuir, para que a análise considere a oferta real e não só a vitrine pública.
15. Como analista, quero indicar em cada linha se a XP distribui ou tem capacidade de distribuir
    o fundo, para separar "expor" de "ativar".
16. Como analista, quero que a lista aceite CNPJ da classe, CNPJ do fundo ou código CVM, com ou
    sem máscara, porque a planilha interna não segue um padrão.
17. Como analista, quero que linhas inválidas (CNPJ inválido, situação desconhecida, fundo que
    não existe na base) sejam listadas com arquivo, linha e motivo, e nunca descartadas em
    silêncio.
18. Como analista, quero que os arquivos da pasta sejam unidos, e que um fundo repetido com
    situações diferentes fique com "distribui", para poder manter uma planilha por área.
19. Como analista, quero que o relatório diga quais arquivos foram lidos, a data de referência
    de cada um e quantos fundos cada um trouxe, para saber se a lista está velha.
20. Como analista, quero um modelo de CSV só com o cabeçalho, para saber o formato sem ler código.
21. Como analista, quero ver lado a lado o veredito contra a vitrine e contra a lista
    distribuível, para distinguir "falta na vitrine" de "falta na distribuição".
22. Como analista, quero que um gap da vitrine coberto pela lista distribuível receba a ação
    "expor na vitrine" (ou "ativar distribuição", quando só há capacidade), o degrau mais
    barato da escada de esforço.
23. Como analista, quero que, sem lista na pasta, a lente distribuível seja suprimida com o
    motivo e a lente de vitrine rode normalmente.
24. Como analista, quero que, sem vitrine da XP coletada, a lente distribuível rode mesmo assim,
    para não perder a análise num dia de bloqueio de CDN.
25. Como membro do comitê de produtos, quero um funil — gaps aparentes, cobertos por espelho, por
    equivalente, em outra liquidez, gaps reais —, para entender o tamanho real da lacuna.
26. Como membro do comitê, quero que o funil e a lista sejam os mesmos no dashboard, na
    planilha, no digest e no deck.
27. Como membro do comitê, quero que os gaps reais continuem ordenados por esforço e depois por
    captação, para começar pelo que dá para fazer.
28. Como analista, quero que o esforço de um gap use a gestora do master da família, e não a do
    veículo, para que um feeder de marca própria do concorrente sobre estratégia de terceiro não
    saia como "produto próprio do concorrente".
29. Como analista, quero que só um fundo cujo master é gerido pela própria casa concorrente saia
    como não transferível.
30. Como analista, quero que a heurística por nome continue aparecendo como "possível
    equivalente" de baixa confiança, e que nunca, sozinha, tire um fundo dos gaps reais.
31. Como analista, quero que cada veredito traga a confiança (alta para espelho, média para
    carteira equivalente, baixa para dado declarado ou nome), e que só alta e média
    reclassifiquem um gap.
32. Como analista, quero que um gap sem carteira publicada (fundo novo, FIDC, carteira sob
    sigilo, CNPJ que não casa com a base) apareça como "sem dado de carteira", com o que o dado
    declarado sugere, e continue contado como gap real.
33. Como analista, quero ver a competência da carteira usada ("carteira de 08/2026") junto de
    todo número que depende dela, para saber a idade da evidência.
34. Como analista, quero que a carteira da CVM apareça na lista de fontes com o link público do
    conjunto, como as demais fontes.
35. Como analista, quero que a decisão de consenso da leitura competitiva e as oportunidades de
    grade da captação usem o mesmo veredito, para que nenhum bloco chame de lacuna o que o bloco
    de gaps diz estar coberto.
36. Como analista, quero que, sem carteira em disco e sem acesso à CVM, o bloco saia como hoje
    (CNPJ contra CNPJ), com o aviso de que espelho e equivalência não foram verificados.
37. Como analista, quero na planilha uma aba com os gaps cobertos e a evidência, e uma aba com as
    famílias das gestoras que aparecem nos gaps, para trabalhar com filtro e tabela dinâmica.
38. Como mantenedor, quero que a carteira seja resumida uma vez por competência, em cache, para
    que o `run` não releia centenas de MB a cada execução.
39. Como mantenedor, quero que os limiares (espelho, distância de carteira, faixas de liquidez,
    fração máxima sob sigilo) sejam constantes nomeadas, com a medição que as sustenta, para que
    mudá-los seja decisão explícita.
40. Como mantenedor, quero que as colunas da CDA sejam lidas por nome, e que coluna ausente ou
    valor de tipo de aplicação desconhecido vire aviso com contagem, como na base de mercado.
41. Como mantenedor, quero que a subida espelho → master seja protegida contra ciclo e contra
    profundidade excessiva.
42. Como mantenedor, quero um subcomando de consulta de famílias e uma flag para pular a
    carteira, com a superfície do CLI travada no teste que já existe.
43. Como mantenedor, quero que a ordem de famílias, membros e gaps seja determinística, com
    desempate explícito, para que um refactor não mude o relatório sem mudar o dado.
44. Como agente que implementa, quero fixtures da CDA montadas em memória com os cabeçalhos
    reais, para testar sem rede.
45. Como leitor de qualquer superfície, quero todo texto novo em português do Brasil, com acento,
    concordância ("1 gap coberto", "3 gaps cobertos") e separador de milhar.
46. Como analista de previdência, quero que o bloco de gaps continue suprimido por construção no
    módulo de previdência, sem tentar montar famílias de veículos de seguradora.

## Decisões de implementação

### Vocabulário

| Termo | Significado |
|---|---|
| **Família de estratégia** | Conjunto de classes que entregam a mesma exposição: um master, os espelhos que chegam a ele e as classes paralelas equivalentes. |
| **Master** | Classe onde a carteira mora de fato; o topo da cadeia de espelhos. Uma classe que não é espelho de ninguém é master de si mesma. |
| **Espelho** | Classe com ao menos 95% do PL em cotas de uma única outra classe. Ligação estrutural, lida da carteira. |
| **Classe paralela** | Master de mesma gestora, mesma categoria ANBIMA e carteira próxima, sem ligação estrutural. |
| **Gestora da estratégia** | A gestora do master da família — não a do veículo que o concorrente exibe. |
| **Perfil de carteira** | Fração do PL por faixa de ativo, com look-through dos espelhos e FICs. |
| **Liquidez (D+)** | Dias de cotização mais dias de pagamento do resgate, do extrato da CVM. |
| **Faixa de liquidez** | até D+1; D+2 a D+5; D+6 a D+30; D+31 a D+90; D+91 a D+180; acima de D+180. |
| **Base de cobertura** | O conjunto de classes contra o qual se mede gap: a vitrine XP ou a lista distribuível. |
| **Gap aparente** | O gap de hoje: CNPJ de varejo na vitrine de um concorrente e fora da base. |
| **Gap real** | Gap aparente sem cobertura de espelho, equivalente ou outra liquidez. |
| **Veredito de cobertura** | A classificação de um gap aparente contra uma base, com evidência e confiança. |

### Módulo novo: Mapa de famílias

Módulo profundo, só com dado da CVM, sem saber de vitrine.

- **Entrada**: as classes da base de mercado (CNPJ da classe, CNPJ do fundo, código CVM,
  gestor e CNPJ do gestor, classificação ANBIMA, indicador de desempenho, público-alvo, PL,
  dias de cotização e de pagamento do resgate, flag declarado de espelho) e as linhas da carteira
  mensal (CDA) de uma ou mais competências: os blocos por tipo de aplicação, o PL por classe e as
  posições sob sigilo.
- **Interface pequena**: montar o mapa a partir dessas duas entradas; consultar a família de um
  CNPJ; listar as famílias de uma gestora; e dar o veredito de cobertura de um CNPJ contra um
  conjunto de CNPJs (a base). Família, membro e veredito são valores simples que as superfícies
  serializam.
- **O que fica escondido**: look-through, cálculo do perfil, limiares, proteção contra ciclo,
  escolha da competência, tratamento de sigilo.

**Espelho e master.** Uma classe com ao menos `LIMIAR_ESPELHO` (95%) do PL em cotas de uma única
classe é espelho dela. O master é o fim da cadeia, seguida até `PROFUNDIDADE_MAXIMA` (4) níveis,
parando em ciclo. A família estrutural é o master mais todos que chegam a ele. O flag declarado
do extrato aparece ao lado como informação e não decide nada (44 de 201).

**Perfil de carteira.** Fração do PL por faixa, com look-through: a exposição de uma classe que
detém cotas é a média, ponderada pela posição, da exposição das classes investidas, recursiva até
a mesma profundidade. Faixas:

| Faixa | De onde vem na CDA |
|---|---|
| Soberano pós e compromissada | títulos públicos indexados à Selic e operações compromissadas |
| Soberano pré e inflação | demais títulos públicos |
| Crédito bancário | depósitos a prazo e títulos de instituição financeira (CDB, RDB, LF, LFSN, DPGE, LCI, LCA) |
| Crédito corporativo | debêntures, títulos do agronegócio e de crédito privado (CRI, CRA, notas comerciais, CPR), cotas de FIDC |
| Ações Brasil | ações, BDR, opções de ações |
| Exterior | investimento no exterior |
| Outros | demais ativos não codificáveis |
| Sob sigilo | posições sob sigilo temporário |
| Cotas não resolvidas | cotas de classe sem carteira na CDA |

Derivativos e swaps ficam fora do vetor: o valor de mercado de futuro e de swap é o ajuste, perto
de zero, e não mede exposição. Quem separa multimercado de renda fixa é a exigência de mesma
categoria ANBIMA e o mandato declarado. O mapeamento dos valores de tipo de aplicação para as
faixas é **medido no arquivo real** (valores distintos por bloco numa competência) e travado em
teste; valor desconhecido vai para "Outros" com aviso e contagem.

**Classe paralela.** Dois masters são equivalentes quando têm a mesma gestora da estratégia (raiz
do CNPJ do gestor), a mesma classificação ANBIMA completa, o mesmo indicador de desempenho quando
os dois declaram, e distância de carteira até `LIMIAR_DISTANCIA_CARTEIRA`. A distância é a metade
da soma das diferenças absolutas entre as faixas — a fração do PL que teria de mudar de lugar (0 é
carteira idêntica, 1 é carteira disjunta). Perfil com mais de `LIMIAR_SIGILO` em "sob sigilo" mais
"cotas não resolvidas" é indeterminado e não entra em comparação. O limiar de distância começa
conservador (0,15) e é calibrado sobre os 135 candidatos medidos (mesma gestora e categoria), com
uma amostra conferida à mão; a calibração vai para o README com data.

**Liquidez.** Cada membro carrega o seu D+. Versões na mesma faixa se cobrem; em faixa diferente,
o veredito é "coberto em outra liquidez". Liquidez não informada num dos lados não rebaixa o
veredito, mas aparece na evidência.

**Carteira.** Usa a última competência publicada da CDA; para uma classe ausente dela, recua até
`COMPETENCIAS_CARTEIRA` (3) e registra por classe a competência usada. O zip mensal (~22 MB) é
baixado com requisição condicional junto da base de mercado, e o download é o mesmo que o
monitoramento de ETF já faz para o membro de estruturados — uma definição de conjunto, um
download, os dois consumidores. O que o mapa precisa (PL, fração por faixa, cotas detidas por
CNPJ, fração sob sigilo) é resumido uma vez por competência em cache, como a série mensal do
informe; competência já resumida não é relida.

**Gestora.** Identificada pela raiz (8 dígitos) do CNPJ do gestor oficial da CVM, a mesma regra
de raiz da watchlist. Grupo econômico com raízes diferentes não é consolidado.

### Duas bases de cobertura

**Vitrine XP.** A vitrine de varejo da XP, com as regras de hoje: XP indisponível ou sem vitrine
suprime a lente com motivo; XP parcial gera aviso de superestimação; concorrente sem vitrine sai
do universo com motivo.

**Lista distribuível.** Uma pasta de entrada, fora do versionamento (dado comercial interno), com
um ou mais CSVs, e um modelo só com o cabeçalho versionado ao lado das outras planilhas manuais.

- Separador `;` com BOM, a convenção das planilhas manuais do repositório; `,` também aceito.
- Colunas lidas por nome, sem diferenciar maiúscula nem acento: `cnpj` (obrigatório, ou
  `codigo_cvm` no lugar), `situacao` (`distribui` ou `pode_distribuir`; vazio vale `distribui`),
  e opcionais `nome`, `gestora`, `data_referencia`, `observacao`.
- Resolução pela mesma regra de três chaves do enriquecimento: CNPJ da classe, depois CNPJ do
  fundo, depois código CVM. CNPJ de fundo com várias classes vale para todas, com aviso.
- Arquivos unidos; fundo repetido fica com `distribui`.
- Linha inválida é listada com arquivo, linha e motivo — nunca descartada em silêncio.
- O bloco registra arquivos lidos, linhas válidas e inválidas por arquivo, e a data de
  referência (a maior `data_referencia`, ou a data de modificação do arquivo quando a coluna
  falta), escrita como "lista distribuível de dd/mm/aaaa".
- Pasta vazia ou ausente suprime só esta lente, com o motivo.

As lentes são independentes: sem vitrine XP, a lente distribuível roda; sem lista, a de vitrine
roda.

### Veredito de cobertura

Para cada gap aparente (varejo na vitrine de ao menos um concorrente, CNPJ fora da base), por
lente:

| Veredito | Quando | Confiança | Gap real? |
|---|---|---|---|
| Coberto por espelho | a base tem uma classe da mesma família estrutural (inclusive o próprio master), na mesma faixa de liquidez | alta | não |
| Coberto por equivalente | a base tem uma classe paralela, na mesma faixa | média | não |
| Coberto em outra liquidez | espelho ou equivalente presente só em outra faixa | alta ou média | não — conta à parte, como gap de liquidez |
| Gap de estratégia | nada acima, e a gestora da estratégia está na base | — | sim |
| Gap de gestora | nada acima, e a gestora da estratégia não está na base | — | sim |
| Sem dado de carteira | a carteira não permite decidir; o dado declarado pode sugerir "possível equivalente" | baixa | sim |

Precedência: espelho, equivalente, outra liquidez. Com mais de um fundo da base cobrindo, vence o
da mesma faixa, depois o de D+ mais próximo, depois o de maior PL, depois o CNPJ.

A evidência de cada veredito traz: o fundo da base que cobre (nome e CNPJ), o master (nome e
CNPJ), a ligação, a distância de carteira quando houver, o D+ dos dois lados, a competência da
carteira, a confiança e, à parte, o "possível equivalente" de baixa confiança (dado declarado ou
nome). A regra da casa continua valendo para a baixa confiança: **informa e nunca remove**. Só
evidência estrutural (espelho) ou de carteira (paralela) reclassifica.

### Escada de esforço

Do mais barato ao mais caro, aplicada aos gaps reais e aos de liquidez:

1. **Expor na vitrine** — a lente de vitrine diz gap, a distribuível diz coberto com `distribui`.
2. **Ativar distribuição** — idem, com `pode_distribuir`.
3. **Versão de liquidez** — coberto em outra liquidez: buscar a versão na faixa do concorrente.
4. **Inclusão operacional** — a gestora da estratégia já está na base.
5. **Abrir gestora**.
6. **Gestora não identificada**.
7. **Produto próprio do concorrente** — só quando a gestora do **master** pertence à casa
   concorrente que exibe o fundo (watchlist).

Dentro de cada degrau vale a ordem de hoje: captação, depois número de casas, depois PL. O
esforço passa a usar a gestora da estratégia em todo lugar.

### Contrato do bloco de gaps

O bloco de gaps de hoje é estendido, não substituído, e recebe o mapa de famílias e a lista
distribuível além das vitrines.

- Por lente (vitrine, distribuível): disponível ou motivo da supressão, tamanho da base e o
  funil — aparentes, cobertos por espelho, cobertos por equivalente, em outra liquidez, sem dado
  de carteira, reais.
- Por gap: os campos de hoje, mais o veredito e a evidência de cada lente, o esforço da escada,
  a gestora da estratégia, a família (identificador e nome do master) e o D+.
- Proveniência: competência da carteira, quantos gaps aparentes tinham carteira, arquivos da
  lista distribuível.
- O total que as superfícies usam como manchete passa a ser o de **gaps reais**, com o funil ao
  lado; o total aparente vira campo próprio, com nome que diz o que é. A cobertura por categoria
  conta gaps reais e ganha a coluna de cobertos.
- A lista de gaps continua uma só, ordenada pela escada, igual nas quatro superfícies.

### Outros blocos que dizem "a XP não tem"

A decisão de consenso da leitura competitiva e as oportunidades de grade da captação hoje fazem a
própria conta de CNPJ fora da vitrine XP. Passam a consumir o mesmo veredito: fundo coberto não
aparece como oportunidade nem como consenso. É a mesma lição do commit que unificou as três listas
de gaps — duas contas de "o que falta" divergem.

### Consulta por gestora e superfícies

- **CLI**: subcomando novo de consulta de famílias, que recebe uma gestora (nome ou CNPJ) ou o
  CNPJ de um fundo e imprime as famílias com master, membros, papel de cada um, perfil de
  carteira resumido ("82% crédito bancário, 15% soberano pós"), categoria, D+ de cada versão, PL
  e em que vitrines cada membro aparece, com a cobertura XP (vitrine e distribuível). Aceita
  `--json`, como os demais. Flag para pular a carteira em `run`, `report` e `export`.
- **Planilha**: aba "Gaps cobertos" (gap, veredito, evidência) e aba "Famílias", restrita às
  gestoras que aparecem nos gaps aparentes.
- **Dashboard**: a linha de cada gap mostra família, veredito e evidência; o funil abre o
  painel de gaps. Sem painel novo.
- **Digest e deck**: o funil na seção e no slide de gaps.
- **Fontes**: a carteira (CDA) entra no registro de fontes com o link público do conjunto da CVM,
  só quando usada; a lista distribuível aparece como fonte interna, sem link, com a data.
- **Prazos**: toda superfície escreve a competência da carteira e a data da lista junto dos
  números que dependem delas.

### Integração no `run`

- `run`, `report` e `export` montam o mapa quando a base de mercado e a carteira estão
  disponíveis. Sem carteira, o bloco sai como hoje, com o aviso de que espelho e equivalência não
  foram verificados — nunca em silêncio.
- A superfície do CLI travada em teste é atualizada com o subcomando e a flag.
- Previdência: continua suprimida pelo bloco de cruzamento por CNPJ, sem montar famílias.
- O custo (download, primeiro resumo, execução com cache) é medido e escrito no README.
- A seção "Gaps de prateleira da XP" do README é reescrita: gap aparente e real, espelho,
  equivalente, liquidez, as duas bases e os limiares com a medição.

## Decisões de teste

**O que é um bom teste aqui.** Exercita comportamento externo pelas duas costuras combinadas e
afirma sobre vereditos, contagens do funil, evidência, esforço e mensagens de supressão e aviso.
Não afirma sobre o vetor de perfil, a distância numérica ou funções privadas — só sobre a decisão
que elas produzem. Fixtures reproduzem o formato real das fontes e não usam rede.

**Costura 1 — Mapa de famílias**, montado com linhas da base de mercado e da CDA em memória, com
os cabeçalhos reais dos blocos medidos nos arquivos de 06/2026. Casos:

- espelho simples; FIC de FIC chegando ao mesmo master; ciclo; profundidade acima do limite;
- classe com 94% num fundo não é espelho; com 95%, é;
- famílias de uma gestora listadas em ordem determinística; gestora por nome e por CNPJ;
  filiais com a mesma raiz juntas;
- classes paralelas: mesma gestora, categoria e carteira próxima → equivalentes; carteira
  distante → não; categoria diferente → não; gestoras diferentes com carteira idêntica → não;
- crédito bancário contra crédito corporativo com a mesma categoria → não equivalentes;
- carteira com sigilo acima do limite → perfil indeterminado, sem equivalência; a ligação de
  espelho visível no bloco de cotas continua valendo;
- classe ausente da última competência usa a anterior e registra qual;
- valor de tipo de aplicação desconhecido → "Outros" e aviso com contagem;
- veredito de cobertura contra uma base: espelho, equivalente, outra liquidez (faixas
  diferentes), mesma faixa com D+ diferente, liquidez não informada.

**Costura 2 — bloco de gaps**, com snapshots de fixture (XP e concorrentes), o mapa da costura 1 e
a lista distribuível numa pasta temporária criada pela configuração temporária dos testes. Casos:

- o funil fecha: aparentes = cobertos + outra liquidez + reais;
- gap espelho do master que a XP exibe → coberto, com a evidência apontando o fundo XP;
- feeder de marca própria do concorrente sobre master de terceiro → esforço pela gestora do
  master (o caso BTGP Thunderbolt → SPX Raptor);
- master gerido pela casa que exibe → produto próprio do concorrente;
- nome parecido sem carteira → "possível equivalente" e continua gap real;
- lista distribuível: união de arquivos, `;` e `,`, BOM, CNPJ com máscara, código CVM, CNPJ de
  fundo com várias classes, duplicado com situações diferentes, linha inválida reportada com
  arquivo e linha;
- gap da vitrine coberto na lista → "expor na vitrine"; com `pode_distribuir` → "ativar
  distribuição";
- pasta vazia → lente distribuível suprimida com motivo, vitrine normal; XP sem vitrine com lista
  presente → só a lente distribuível;
- sem carteira → bloco como hoje, com aviso;
- ordem determinística com empates explícitos.

Além das costuras: o teste que trava números iguais nas quatro superfícies passa a cobrir o
funil; o teste que impede o digest em markdown e em HTML de divergir cobre a seção nova; o teste
da escada de esforço e da ordem ganha os degraus novos; o teste da superfície do CLI ganha o
subcomando e a flag.

**Prior art.** Os testes do bloco de gaps na suíte de vitrines (snapshots de fixture, casos de
supressão); os testes da lista única de gaps (esforço e ordem); os testes do `etf-bruto`, que
montam arquivos da CVM e da B3 em memória com cabeçalhos reais; os testes da base de mercado e as
fixtures dela; os testes de mandato (flags do extrato); as fixtures comuns (configuração
temporária e faixas de CNPJ por casa); a trava da superfície do CLI.

## Fora de escopo

- **Equivalência entre gestoras diferentes** (dois créditos high grade de casas distintas como
  substitutos). A família é da gestora; substituição entre gestoras é outra pergunta.
- **Previdência** (FIE de seguradora), **ETF** e **fundos estruturados** (FII, FIDC, FIP): a CDA
  de FI não traz a carteira deles, e o FIDC entra só como ativo de crédito corporativo na carteira
  de quem o detém.
- **Deriva da carteira no tempo**: o mapa usa uma competência por classe, não uma série.
- **Leitura de regulamento e lâmina** para descrever estratégia.
- **Consolidação de grupo econômico** de gestoras com raízes de CNPJ diferentes.
- **Área logada da XP** como terceira base de cobertura.
- **Execução do plano de ação** (contato com gestora, CRM, aprovação de grade): o monitor
  recomenda, não executa.
- **Investigar por que 102 gaps não casam com a base de mercado pelo CNPJ** (ver notas): ficam
  "sem dado de carteira" até a causa ser tratada em separado.

## Notas

- **Medição.** Vitrines de 25/09/2026 (XP 622 fundos de vitrine, BTG 651, Inter 463, Itaú 27, Nu
  sem vitrine) e CDA de 08/2026, a competência mais recente em disco junto da base de mercado. O
  script da medição foi descartável; os números devem ser refeitos na implementação e escritos no
  README com data, como o resto do monitor faz.
- **102 gaps sem casamento com a base.** 102 dos 636 gaps (97 do Inter, 4 do Itaú, 1 do BTG) têm
  CNPJ que não aparece na base de mercado nem como classe nem como fundo. Dos 121 gaps sem
  carteira, 101 são desses, 17 são classes de FIDC e 3 são FIF ausentes da competência.
- **Onde a cobertura por espelho aparece.** A maior parte dos 62 cobertos é a linha "Advisory" da
  XP: a XP exibe o seu feeder, o concorrente exibe o dele, os dois sobre o mesmo master.
- **Exemplos de liquidez para os testes e o README.** Sparta Max: concorrente D+61, XP D+121
  (outra faixa). Bahia AM: concorrente D+41, XP Advisory D+20 (outra faixa). Inter Access Arbor
  D+10 contra Arbor II D+22 (mesma faixa, coberto). BTGP Thunderbolt D+361 contra SPX Raptor 180
  Advisory D+360 (mesma faixa, coberto, e deixa de ser "produto próprio do concorrente").
- **Limiares a calibrar antes de fechar**: `LIMIAR_DISTANCIA_CARTEIRA` (começa em 0,15) e
  `LIMIAR_SIGILO`. `LIMIAR_ESPELHO` (95%) e as faixas de liquidez já foram usados na medição acima.
- **Decisão que merece ADR**: gap passa a ser "estratégia ausente", e só evidência estrutural ou
  de carteira reclassifica — a heurística continua informando sem remover. O repositório não tem
  ADRs ainda; vale registrar esta como a primeira.
