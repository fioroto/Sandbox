# Linguagem visual do dashboard e do digest

Base visual (desde 27/09/2026): o padrão executivo da XP, do material "Quando o dinheiro cai? — versão executiva" — fundo branco, preto `#0a0a0a` e amarelo `#f9c900`, fonte Inter com peso 800 nos títulos, barra preta no topo com a marca, retranca em caixa alta com um traço amarelo de 24 × 3 px antes, bloco marcado por linha preta de 3 px no topo (sem cartão, sem sombra, sem canto arredondado), tabela com cabeçalho em caixa alta sobre linha preta de 2 px, aviso com borda amarela à esquerda sobre `#f6f6f4`, botão de alternância com borda preta que, ligado, fica preto com texto amarelo. Tema claro, sempre.

Sobre essa base, a anatomia de figura da The Economist e da McKinsey: título que afirma a conclusão, subtítulo com unidade e prazo, rótulo direto no lugar de legenda, fonte no rodapé; dumbbell, colunas de ganho e perda, mapa de calor, matriz de bolhas, grade de pontos, barras empilhadas com parte positiva e negativa.

Tokens: `shelf_monitor/report/shared/estilo.py` (Python) e as variáveis `--casa-*`, `--tag`, `--zero`, `--contorno-xp` e `--destaque-suave` em `shelf_monitor/report/assets/dashboard.css` (um teste garante que as duas cópias batem).

## 1. Anatomia de toda figura

De cima para baixo, sempre nesta ordem:

1. **Traço**: amarelo `#f9c900`, 24 × 3 px, no canto esquerdo (o mesmo da retranca). É cromo, nunca dado.
2. **Título que afirma**: uma frase curta com a leitura, não o nome do gráfico. "XP capta menos que BTG e Itaú em 3 meses", e não "Captação por casa". Inter 800, 16–18 px, entreletra negativa.
3. **Subtítulo**: o que está medido, a unidade e o prazo com datas no formato dd/mm/aaaa, em uma linha.
4. **Legenda** (só com duas ou mais séries e quando o rótulo direto não basta).
5. **Corpo**: o gráfico.
6. **Rodapé**: fonte com link e notas, 11,5 px, tinta fraca.

## 2. Cor

| Casa | Cor | Observação |
|---|---|---|
| XP | `#f9c900` | amarelo da marca; a casa-base de toda leitura. Toda marca amarela leva contorno preto `#0a0a0a` |
| BTG | `#1d4e9e` | azul |
| Itaú | `#c24400` | laranja escuro |
| Nu | `#b065e8` | roxo |
| Inter | `#ff7a00` | laranja da marca; abaixo de 3:1 no branco, então sempre com nome escrito ao lado |

- A cor segue a casa, nunca a posição no ranking. Casa fora do registro: cinza `#9a9993`.
- Validação (`validate_palette.js`, superfície branca): croma e separação sob daltonismo passam em todos os pares. O amarelo é exceção declarada à faixa de luminosidade e fica a 1,6:1 do branco, por ser o da marca; por isso o contorno preto (uma regra CSS para toda marca `var(--casa-xp)`, e `border` nas células do e-mail) e o nome da casa escrito ao lado. Com o laranja mais claro de antes, amarelo e Inter ficavam a 14,6 de distância (piso: 15); o Inter foi para o laranja de marca e o Itaú um passo mais escuro.
- Texto nunca usa a cor da série. O valor fica em tinta; a marca colorida ao lado carrega a identidade. O amarelo aparece em texto só como marca-texto (fundo amarelo, texto preto).
- Quantidade (mapa de calor, faixas ordenadas): cinza neutro, `#efefef` → `#232323`. Sem matiz de propósito: todo matiz já é de alguma casa.
- Componentes de uma mesma soma (compartilhado × só nesta vitrine, próprio × terceiros): grafite `#232427` e cinza `#bcbcbc`, porque ali a identidade é o componente e não a casa.
- Linha da casa-base em tabela: fundo `#fff6cc` com borda amarela de 4 px à esquerda.
- Verde `#2e7d4f` e vermelho `#c6423c` de status só com rótulo e sinal ("+R$ 1,2 bi", "−0,4 pp"), nunca como a única pista.

## 3. Marcas e eixos

- Barra de no máximo 14–16 px de espessura, ponta arredondada de 3 px, base reta no zero; 2 px de superfície entre barras vizinhas e entre segmentos.
- Ponto de ≥ 8 px com anel de 2 px da cor da superfície.
- Grade só horizontal (ou só vertical nos gráficos deitados), linha fina sólida `#e5e5e3`. Nunca tracejada.
- Linha do zero: preta `#0a0a0a`, também em ganho e perda.
- Eixo de valor com números redondos, em cima (deitado) ou à direita (em pé), algarismos tabulares.
- Rótulo seletivo: o valor na ponta da barra, o extremo, a casa-base. Nunca um número em cada ponto.
- Barra sempre começa no zero. Onde a diferença entre casas é pequena perto do nível (retorno de 12 meses entre 11% e 13%), a forma é **ponto** (dumbbell), que pode cortar o eixo sem mentir.

## 4. Forma por pergunta

| Bloco | Pergunta | Forma |
|---|---|---|
| Manchetes | onde a XP está entre as casas | número + faixa com um ponto por casa (XP maior e rotulada) |
| Casa a casa | quem lidera cada coluna | tabela com barra fina na célula, na cor da casa |
| Funil de gaps | de aparente a real | barra única segmentada com rótulos (SoftBank, Economist) |
| Categorias rasas | onde a XP é fina | matriz de bolhas categoria × casa (McKinsey) |
| Captação por casa | quem capta e quem perde | barras divergentes a partir do zero |
| De onde vem o saldo | quais componentes somam o líquido | barras empilhadas com parte positiva e negativa e marca do líquido (McKinsey) |
| Tendência mensal | ritmo por competência | colunas de ganho e perda, uma miniatura por casa, mesma escala (Economist) |
| Ritmo dos fundos | que parcela acelera ou virou saída | barras 100% empilhadas por casa |
| Rentabilidade | mediana × PL × cotista | dumbbell: um ponto por leitura, uma linha por casa (Economist) |
| Onde está o dinheiro | quanto do PL está abaixo da mediana | grade de 100 pontos por casa (McKinsey) |
| Custo | índice contra a categoria | ponto por casa com referência em 1,00x |
| Mandato | acima ou abaixo das concorrentes | mapa de calor divergente |
| Classes de ativo | onde cada prateleira é forte | pontos por casa em cada classe |
| Aplicação mínima | varejo ou alta renda | barras 100% empilhadas por faixa, rampa ordenada |
| Radar CVM | registrados e pré-operacionais | barras empilhadas por casa |

## 5. Texto: menos frase, mais número

Pedido de 27/09/2026: "textos mais simplificados ou em tabelas, menos texto, mais informação".

- **Uma frase por figura, e é o título.** Ele afirma a leitura; o resto da figura é número, rótulo e eixo.
- **Introdução de bloco: uma linha.** O que o bloco mede, em até ~20 palavras. Metodologia, ressalva e definição vão para a nota do rodapé (letra pequena) ou para um "Como ler" recolhido, nunca para o parágrafo de abertura.
- **Lista vira tabela.** Fundo com número (captação, cotistas, retorno, esforço) é linha de tabela: `Fundo | número | casas`, não "Nome (CNPJ) - categoria - casas - +R$ 12 MM - esforço".
- **Frase que repete o gráfico sai.** Se o gráfico e a tabela já mostram, o parágrafo que narra os mesmos números é cortado.
- **Prazo no cabeçalho, não em cada frase.** O prazo (rótulo e datas dd/mm/aaaa) aparece uma vez, no subtítulo ou no cabeçalho da coluna, e vale para o bloco inteiro.
- **Rótulo curto.** "Captação 3 meses", não "Captação líquida dos fundos da vitrine no período". Separador " · " no lugar de conjunções.
- **Não se corta:** fonte com link, prazo com datas, e as ressalvas que evitam leitura errada (autorização ≠ exposição; fluxo por fundo, não por distribuidor). Elas encolhem para uma nota curta.

## 6. E-mail do digest

O e-mail não roda script e não carrega SVG no Gmail. Os gráficos são **tabelas HTML** com estilo em linha: barra é uma célula com largura em % e `bgcolor`; mapa de calor é célula colorida; grade de pontos é tabela de células. Nada de `<img>`, `<svg>`, `<script>` nem `<style>` em bloco. Layout em tabela de 640 px (Outlook ignora `max-width` em `div`), `color-scheme` claro declarado e texto de pré-visualização escondido. O markdown continua sendo a parte em texto do e-mail, sem gráfico. No padrão XP, o e-mail abre com uma faixa preta (sem logo: imagem e SVG não passam), usa só a pilha de fontes (o Gmail descarta a Inter do Google Fonts) e toda célula amarela leva `border` preto de 1 px.
