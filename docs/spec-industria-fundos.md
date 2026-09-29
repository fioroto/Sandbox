# Monitor da indústria de fundos: a visão de mercado para a distribuição

> Spec aprovada em 28/09/2026 a partir do plano do branch `claude/xp-shelf-gaps-insights-uxlsvq`,
> com medição sobre os dados abertos da CVM. Código em `shelf_monitor/industria/`;
> comando `python -m shelf_monitor industria`.

## Status

| Entrega | Estado |
|---|---|
| 1A. Fundação e início do histórico | implementada em 28/09/2026 |
| 1B. Estrutura e taxonomia | implementada em 28/09/2026 |
| 1C. Bloco e planilha | implementada em 28/09/2026 |
| 1D. Painel interativo | implementada em 28/09/2026 |
| 1E. E-mail e cadência | a fazer |

**Medido pelo monitor em 28/09/2026** (`industria --competencia 2026-08`: cadastro de 09/2026, perfil e informe de 08/2026):

| Degrau | Produtos | Classes | PL |
|---|---|---|---|
| Sem cadastro na CVM | 356 | 351 | R$ 9,5 bi |
| Não operacional | 7.031 | 4.888 | R$ 24,7 bi |
| Estruturados (FIDC, FII, FIP, Fiagro) | 10.564 | 7.619 | R$ 2,25 tri |
| ETF | 229 | 229 | R$ 144,8 bi |
| Previdência | 4.943 | 4.937 | R$ 4,04 tri |
| Exclusivo | 7.872 | 7.844 | R$ 2,59 tri |
| Exclusivo de não residente | 122 | 115 | R$ 38,9 bi |
| Master (veículo de outros fundos) | 4.063 | 3.911 | R$ 2,89 tri |
| Institucional | 242 | 242 | R$ 300,1 bi |
| Restrito | 81 | 76 | R$ 8,1 bi |
| **Universo disponível** | **8.444** | **8.230** | **R$ 4,28 tri (R$ 3,80 tri na camada do investidor)** |

PL do informe diário no fim de 08/2026; estruturados e ETF, que não entregam o informe, com o PL do cadastro. O perfil do próprio mês cobre 99,1% do PL do universo.

O universo saiu com R$ 3,80 tri na camada do investidor, contra R$ 3,95 tri da medição simplificada do plano (seção de contexto abaixo). A diferença de R$ 145 bi vem das regras que a medição simplificada não aplicava:
- o nome "EXCLUSIVO" num fundo sem o flag, como o BB Extramercado Exclusivo FAT, com R$ 56 bi;
- a subclasse exclusiva;
- o público "previdenciário" do extrato;
- a concentração num cotista (restritos, R$ 8 bi).

Os números de verificação desta spec são os do monitor.

**Estrutura e taxonomia medidas pelo monitor em 28/09/2026** (camada do investidor, universo de 08/2026, carteira de 06 a 08/2026):

| Papel | Produtos | PL | Fatia |
|---|---|---|---|
| Feeder | 2.152 | R$ 1,71 tri | 45,0% |
| Direto | 2.506 | R$ 1,00 tri | 26,4% |
| FoF da casa | 833 | R$ 529 bi | 13,9% |
| Híbrido | 1.207 | R$ 384 bi | 10,1% |
| FoF multigestor | 633 | R$ 84 bi | 2,2% |
| Espelho | 784 | R$ 78 bi | 2,1% |
| Feeder de fundo no exterior | 83 | R$ 10 bi | 0,3% |

- **Casas dedicadas:** Banco do Brasil R$ 26,8 bi (68 veículos), Itaú R$ 26,4 bi (224), XP R$ 22,4 bi (89, pela marca "Advisory"), Bradesco R$ 5,3 bi, BTG R$ 4,9 bi.
- **Macroclasses:** pós-fixado e caixa 63,3%, crédito privado 14,3%, multimercado 8,7%, ações 5,7%, internacional 5,1%, juros e inflação 2,7%.
- **Confiança da estratégia:** alta em 71,4% do PL, média em 28,5% e baixa em 0,05%. Os rótulos largos de duração média e livre, que agora dependem da carteira, entram como média.
- **Grupo curado:** cobre 96,0% do PL, com 778 grupos no universo.

**Bloco e planilha medidos pelo monitor em 28/09/2026** (camada do investidor; 12 meses de 01/09/2025 a 31/08/2026, base no fechamento de 29/08/2025):

| Grandeza | Número |
|---|---|
| PL do universo | R$ 3,31 tri → R$ 3,80 tri |
| Captação do investidor em 12 meses | +R$ 49,8 bi (1,5% do PL de abertura) |
| Rendimento em 12 meses | +R$ 435,7 bi |
| Resíduo em 12 meses | +R$ 0,6 bi (a soma dos resíduos mensais é 12,4% da soma das variações) |
| Peer group que mais captou / que mais perdeu | Crédito baixa duração (+R$ 20,9 bi) / DI Grau de investimento (−R$ 24,4 bi) |
| Conta e ordem | 11,9% do PL (+1,9 p.p. em 12 meses) |
| PF varejo | 18,6% do PL (+1,3 p.p.) |
| PJ private | 17,6% do PL (−1,3 p.p.) |
| Gestoras bancárias / independentes | 76,7% (−0,8 p.p.) / 10,8% (−0,5 p.p.) |
| Captação positiva que ficou com os 10 maiores grupos | 59,8% |
| Produtos que entraram / saíram do universo em 12 meses | 704 (R$ 37,6 bi hoje) / 199 (R$ 15,0 bi) |
| Previdência (camada ao lado) | +R$ 48,4 bi em 12 meses (2,5% do PL) |

- **Sazonalidade:** dezembro de 2025 perdeu R$ 61 bi e janeiro de 2026 captou R$ 35 bi.
- **Evento na cota:** retorno mensal acima de 50% (grupamento, desdobramento ou erro de dado) sai do rendimento e do retorno. Sem essa guarda, 293 linhas com saltos de até ×1.000.000 somavam R$ 2,4 tri de "rendimento".
- **Saídas:** `data/reports/industria-AAAA-MM.xlsx`, com 15 abas e o explorador de 8,2 mil produtos, e o bloco publicado em `data/historico/industria/AAAAMM.json`.

**Painel interativo medido em 28/09/2026** (`data/reports/industria_dashboard.html`, competência 08/2026):

| Grandeza | Número |
|---|---|
| Tamanho da página | 1,73 MB, autocontida, com duas tags de script e nenhuma com `src` |
| Explorador | 8.206 produtos, 1,32 MB do JSON |
| Cubo dos filtros | 1.099 células (peer group × público × papel × tipo de gestora), 138 KB |
| Ciclo mensal com cache, painel incluído | 49 s |

- **Filtros.** Público, macroclasse, papel e tipo de gestora recalculam no navegador os destaques, as macroclasses, os peer groups, a captação por janela, o canal, o papel e o explorador. Somado, o cubo devolve os números do bloco (travado em teste). O funil, o dono do dinheiro, o mês a mês, o mapa de calor, as famílias, as gestoras, o retorno e os lançamentos vêm prontos do bloco, e o subtítulo de cada um diz qual filtro vale ali.
- **Conferência.** Chromium headless em 1.280 px e 390 px: nenhum erro de JS, nenhuma rolagem horizontal da página, e os destaques voltam aos do universo quando os filtros são limpos.
- **Nomes.** Cerca de 1,3 mil nomes do cadastro trazem o sufixo jurídico com espaço duplo, abreviado ("RESP LIM"), com erro de digitação ou cortado no limite de 100 caracteres. O painel mostra a forma curta ("FIC FIF", "MM", "CP", "IE"); a planilha mantém o nome da CVM.
- **Neutralidade.** A barra do topo não traz marca de casa. O amarelo marca só o que o leitor selecionou, e verde e vermelho só aparecem ao lado de um sinal.

**Mudança em relação ao plano.** Os rótulos "Grau de investimento" e "Soberano" de duração média e livre (R$ 685 bi) iam para juros e inflação. A carteira da raiz mostrou que são pós na maioria: 77 a 78% em LFT e compromissada no soberano. Por isso ficam em pós-fixado e caixa, e só viram "Juros ativo (pré e inflação)" quando pré e inflação passam de metade da carteira. Juros e inflação ficou com 2,7% do PL, que é o tamanho real do risco de juros fora da previdência.

## Contexto

O repositório já monitora a prateleira de cinco casas, os ETFs e a previdência, mas sempre a partir de uma casa. Falta a visão neutra da indústria inteira para o negócio de distribuição: o que está de fato disponível para PF e PJ, para onde vai o dinheiro, quem ganha, por qual canal e a que preço.

Medido em 28/09/2026 nos dados abertos da CVM:

| Fato | Número |
|---|---|
| Classes FIF em funcionamento | 25.390, com R$ 14,3 tri de PL somado (com dupla contagem) |
| Marcadas como exclusivas | R$ 5,4 tri, dos quais boa parte é previdência (todo fundo de previdência é registrado como exclusivo) |
| Previdência (rótulo ANBIMA "Previdência…") | R$ 3,7 tri |
| PL de não exclusivos e não previdência detido por outros fundos | 43,5% de R$ 7,8 tri (perfil mensal de 08/2026) |
| Dono do dinheiro nesse universo | PJ private 10,9% · PF varejo 10,3% · PF private 6,8% · conta e ordem 6,5% · PJ varejo 5,3% |
| Clientes de plataformas por conta e ordem | 9,3 milhões de posições |
| Captação bruta de 08/2026 em veículos de fundo único (feeders e espelhos) | R$ 486 bi de R$ 1.727 bi (28%). O mesmo dinheiro volta a aparecer no master |

Sem tratar exclusivos, previdência e a estrutura master/feeder/espelho, qualquer total da indústria sai inflado e mistura produtos que o cliente PF/PJ não pode comprar.

**O funil proposto, medido nos mesmos dados** (cadastro de 09/2026, perfil e carteira de 08/2026):

| Degrau | Classes | PL do cadastro |
|---|---|---|
| Não operacionais | 3.082 | R$ 57 bi |
| Fora do escopo da Fase 1 (FIDC, FII, FIP, Fiagro, ETF) | 8.310 | R$ 2,6 tri |
| Previdência | 4.587 | R$ 3,8 tri |
| Exclusivos | 8.090 | R$ 2,8 tri |
| Masters (≥ 90% do PL em mãos de fundos) | 3.893 | R$ 2,9 tri |
| Institucionais, não residentes e restritos | 451 | R$ 373 bi |
| **Universo disponível** | **8.369** | **R$ 4,4 tri (R$ 3,95 tri na camada do investidor)** |

**Conferido com o informe diário de 08/2026:**
- 8.150 classes do universo entregaram o informe, com R$ 3,95 tri na camada do investidor.
- A captação líquida do mês foi de R$ 13,0 bi na camada do investidor, contra R$ 17,1 bi na soma simples das mesmas classes. A diferença de R$ 4,1 bi é dinheiro de FoFs e espelhos contado duas vezes.

**Como o universo se divide** (camada do investidor):
- Por público: Público Geral R$ 2,79 tri, Profissional R$ 649 bi, Qualificado R$ 491 bi.
- Por papel na estrutura: feeders e espelhos R$ 1,84 tri, diretos R$ 1,12 tri, FoFs R$ 622 bi, híbridos R$ 369 bi.
- O cotista "outros" soma R$ 560 bi. São fundos de caixa de empresas e de entes públicos (Itaú Empresa Trust, Caixa Polis). Esse cotista fica num segmento próprio, fora do "dinheiro de PF/PJ".

**Decisões do usuário (28/09/2026)**

| Tema | Decisão |
|---|---|
| Público-alvo | Geral, Qualificado e Profissional na visão padrão, como filtro em tudo |
| Escopo | Fase 1 com fundos líquidos (RF, Multimercado, Ações, Cambial); previdência e ETF como camadas ao lado; FIDC, FII e Fiagro na Fase 3 |
| Formato | Painel interativo já na Fase 1, além de e-mail e planilha |
| Cadência | Ciclo mensal completo (a partir do dia 16) e flash semanal de captação |

## Princípios de desenho

1. **Três níveis de unidade.** Subclasse é o produto comercial, classe é a carteira e estratégia é a raiz da cadeia master (ou a família de `familias.py`).
2. **Toda soma declara a camada.** O padrão é a *camada do investidor*: só o dinheiro de quem não é fundo, sem dupla contagem. As camadas ao lado (previdência, ETF) nunca entram no total do universo.
3. **Existência no tempo, atributos fixos.**
   - A classe conta nos meses em que entregou o informe e até o cancelamento. Os resgates de fundo liquidado ficam na série.
   - Público, exclusividade, previdência, gestora e rótulo vêm do último estado conhecido e valem para a janela toda, porque a CVM não guarda histórico de classe pós-RCVM 175.
   - As regras que dependem do perfil (master, institucional, concentração) são avaliadas uma vez, com o perfil mais recente. Assim a classe não entra e sai do universo a cada mês.
4. **Número com prazo.** Toda janela traz rótulo, datas dd/mm/aaaa e base. Rentabilidade abre com 12 meses.
5. **Neutralidade.** Nenhuma casa em foco, sem chave `foco` no bloco. Cor de casa só aparece onde a casa é o próprio dado (quem cria espelhos). Incluir o Profissional contraria `profile.py:12-13` e `report/custo.py:135-143`, que valem para as vitrines. O módulo declara a própria política de universo e não mexe nesses arquivos.
6. **Fonte com link** em toda superfície, português acentuado, comentários em ASCII.

## 1. Universo disponível: quem sai e por quê

Cada classe e subclasse recebe o primeiro motivo que se aplicar. O funil é uma partição: cada linha tem exatamente um motivo, e a soma dos degraus é a base. A regra de subclasse vale antes da regra de classe, porque 3.392 classes com subclasses têm público e exclusividade em branco no nível da classe.

| # | Motivo | Regra | Dado |
|---|---|---|---|
| 1 | `sem_cadastro` | CNPJ do informe sem linha no cadastro (277 CNPJs e R$ 5,5 bi no painel de 08/2026) | registro + `cad_fi` |
| 2 | `nao_operacional` | Situação diferente de "Em Funcionamento Normal", datada por `Data_Inicio_Situacao` e `registro_fundo.Data_Cancelamento` | classe, subclasse, fundo |
| 3 | `fora_do_escopo` | FIDC, FII, FIP e Fiagro (Fase 3) | `registro_classe.Tipo_Classe` |
| 4 | `etf` | ETF (catálogo de `market/etf.py:258`) → vai para a camada ETF | cadastro |
| 5 | `previdencia` | Rótulo ANBIMA "Previdência…", subclasse `Previdenciario` ou `Exclusivo_Previdencia_Complementar = S`, ou ≥ 95% do PL em EAPC e seguradora → vai para a camada previdência | vem antes de exclusivo, porque todo fundo de previdência é registrado como exclusivo |
| 6 | `exclusivo` | `Exclusivo = S` na subclasse ou na classe, ou nome EXCLUSIV* | registro; regex de `profile.py:32-34` |
| 7 | `nao_residente` | Subclasse `Exclusivo_INR = S` | `registro_subclasse` |
| 8 | `veiculo_de_fundos` | ≥ 90% do PL detido por outros fundos (master) | perfil `PR_PL_COTST_FI_CLUBE` |
| 9 | `institucional` | ≥ 95% do PL em EFPC, RPPS, seguradora, EAPC, banco, PJ financeira, capitalização ou não residente | perfil mensal |
| 10 | `restrito` | Nome RESERVAD*/RESTRIT*/FAMILY OFFICE, ou maior cotista com ≥ 50% do PL e até 10 cotistas | nome; perfil `PR_PATRIM_LIQ_MAIOR_COTST` e `NR_COTST_*` |

O que passa é o **universo disponível**. Público-alvo (Geral, Qualificado, Profissional) e condomínio fechado viram filtros, não exclusões. No mês M, a classe elegível que não entregou o informe aparece como "sem informe no mês", sem sair do universo. Os limiares ficam em `report/shared/limiares.py`, com nome que diz o eixo.

## 2. Estrutura: master, feeder, espelho e FoF

A carteira (CDA, bloco de cotas de fundos) decide a estrutura. O flag `FUNDO_ESPELHO` do extrato só informa e nunca contradiz a carteira. Sem carteira, a classe fica como "sem carteira", marcada "espelho declarado" quando o flag existe. A detecção de espelho e a cadeia até o master já existem em `familias.py` (`_investido_dominante` L561, `master_de` L573) e são estendidas, não duplicadas.

| Papel | Regra | O que muda na análise |
|---|---|---|
| **Master** | ≥ 90% do PL detido por outros fundos | Sai da soma (motivo 8); empresta estratégia, gestora e rentabilidade aos veículos |
| **Feeder** | ≥ 95% do PL em uma classe do mesmo grupo econômico de gestora | Veículo de venda da própria gestora |
| **Espelho** | ≥ 95% em uma classe de outro grupo | Veículo de distribuição de terceiro; revela a casa que espelha |
| **FoF da casa / FoF multigestor** | ≥ 67% em cotas de fundos, sem alvo único; multigestor quando os alvos são de outros grupos | Curadoria vendida como produto; peer group próprio |
| **Feeder exterior** | ≥ 95% em um único veículo coletivo no exterior (CDA bloco 7, `INVEST_COLETIVO`) | Estratégia e gestora do fundo offshore |
| **Híbrido** | 10% a 90% do PL detido por fundos | Conta só a fatia de investidores finais |
| **Direto** | Nenhum dos anteriores | Carteira própria e investidor final |

**Camada do investidor**, por classe e mês. A fatia detida por fundos, s, vem do perfil mensal:

| Grandeza | Fórmula |
|---|---|
| PL do investidor | `pl_inv = PL × (1 − s)` |
| Fluxo implícito dos fundos | `f_fundos = PL(t) × s(t) − PL(t−1) × s(t−1) × (1 + r)` |
| Fluxo do investidor | `f_inv = fluxo declarado − f_fundos` |

Sem perfil em t−1, o fluxo do investidor cai no rateio pela fatia de t.

**Por que o perfil e não a carteira.** Medido em 08/2026, as duas fontes concordam em até 5 p.p. para 84% do PL. No agregado, o perfil dá R$ 5,6 tri detidos por fundos e a carteira dá R$ 5,0 tri. A carteira não vê clubes, posições sob sigilo nem as cotas compradas pelos fundos de previdência, que ficam no arquivo `cda_fie`. Divergência acima de 25 p.p. (9,3% do PL) vira alerta de qualidade.

A estratégia, a gestora da estratégia e a rentabilidade vêm da raiz. A gestora do veículo fica como segunda dimensão.

**Grupo econômico, não raiz do CNPJ.** Medido na carteira de 08/2026 (não exclusivos, fora da previdência, ≥ 95% numa classe):
- 2.391 veículos têm a mesma raiz de gestora do alvo (R$ 1,67 tri).
- 1.421 têm outra raiz (R$ 393 bi).
- Pela raiz do CNPJ, a Caixa DTVM "espelharia" R$ 211 bi, mas a maior parte é feeder do próprio grupo gerido por outra entidade. O mesmo acontece com BB e Santander.

Por isso feeder e espelho se separam pelo grupo de `grupos_gestores.csv`, já na Fase 1.

**Casa dedicada.** Parte dos feeders é da própria gestora, mas feita para um distribuidor: a versão "Advisory" na XP, a "Access" no Inter, a marca própria do BTG (exemplos da `docs/spec-gaps-xp-familias.md`). O veículo ganha a faceta **casa dedicada** por dois sinais:
- a gestora do veículo pertence a um grupo marcado como distribuidor;
- o nome traz uma marca listada em `marcas_de_distribuidor.csv`.

Nos nomes dos veículos de fundo único, as marcas mais frequentes são ITAÚ (606), BRADESCO (274), BB (245), PRIVATE (132), CAIXA (111), UBS (102), ADVISORY (87), SANTANDER (84), ÁGORA e ACCESS (48 cada) e BTG (44). Todas as casas recebem o mesmo tratamento. A matriz casa dedicada × gestora da estratégia é o mapa de parcerias de distribuição do mercado.

## 3. Taxonomia de estratégias para comparação

Três camadas: macroclasse (N1), peer group (N2) e facetas. O N1 segue a forma como as prateleiras de distribuição se organizam. O N2 é o grupo onde a comparação de retorno, risco e taxa é justa.

| N1 | N2 (peer groups) | Origem principal |
|---|---|---|
| Pós-fixado e caixa | RF Simples · DI Soberano · DI Grau de investimento · Grau de investimento, duração média e livre · Soberano, duração média e livre · Indexado DI/Selic | ANBIMA RF Simples, Duração Baixa, Duração Média e Livre (Soberano e GI), Indexados + indicador DI |
| Crédito privado | Crédito baixa duração · Crédito média, alta e livre duração · Crédito incentivado (isento) · Crédito via multimercado | ANBIMA Crédito Livre; nome INCENTIVAD/INFRA/12.431; regra de carteira para MM Livre |
| Juros e inflação | Indexado inflação (IMA-B) · Indexado pré (IRF-M) · Soberano, duração alta · Grau de investimento, duração alta · Juros ativo (pré e inflação) | ANBIMA Indexados + indicador; Duração Alta; Duração Média e Livre com a carteira da raiz em pré e inflação |
| Multimercado | Macro · Trading · Long & Short neutro · Long & Short direcional · Juros e moedas · Livre · Estratégia específica · Capital protegido · Balanceados · Dinâmico · Multigestor (FoF) | ANBIMA Multimercados; papel FoF |
| Ações | Indexado (passivo) · Índice ativo · Valor/crescimento · Dividendos · Small caps · Setoriais · Sustentabilidade · Livre · Específicos (fechados, mono ação, FMP-FGTS) | ANBIMA Ações |
| Internacional | RF global · Multimercado global · Ações global | ANBIMA "Invest. no Exterior", Dívida Externa; papel feeder exterior |
| Cambial | Cambial | ANBIMA Cambial + indicador |

**Cascata de classificação na Fase 1.** Cada classe guarda a origem e a confiança.
1. Override curado (`overrides_estrategia.csv`).
2. Herança da raiz para feeder e espelho.
3. Rótulo ANBIMA mais indicador de desempenho, mapeados em `taxonomia.csv`. O rótulo vem truncado em 40 caracteres, então o casamento é por prefixo normalizado. O indicador refina só metade das classes: cerca de 6 mil dizem "Não se aplica" ou "OUTROS".
4. Uma única regra de carteira: Multimercados Livre vai para "Crédito via multimercado" quando a carteira da raiz tem ≥ 60% em crédito e < 10% em ações (`MapaDeFamilias.perfil_determinado`, `familias.py:658`).
5. Classe CVM mais nome, com confiança baixa.

A cascata estende `mercado_csv.categoria_da_linha` (L32-43), que segue como único ponto de leitura de categoria do `mercado.csv`. As demais reclassificações por carteira (RF Duração Livre GI, Ações Livre) ficam para a Fase 2.

**Cobertura, medida no universo disponível** (camada do investidor, R$ 4,0 tri). O rótulo ANBIMA cobre 97,6% do PL. Os três maiores peer groups concentram 50%: DI Grau de investimento (24%), DI Soberano (14%) e Crédito média/alta/livre (12%). Cerca de 19% está em rótulos largos:

| Rótulo largo | Parcela do PL |
|---|---|
| RF Duração Livre Grau de Investimento | 7,4% |
| Multimercados Livre | 5,9% |
| Ações Livre | 2,4% |
| Sem rótulo ANBIMA | 2,3% |
| Estratégia Específica e Dinâmico | 1,3% |

**Crédito incentivado** ganha peer group próprio porque o isento compete por outro cliente.
- São 1.096 classes não exclusivas com a marca no nome, com R$ 438 bi brutos.
- 942 delas estão sob o rótulo RF Duração Livre Crédito Livre, que não as distingue.
- O campo `Tributacao_Longo_Prazo` vem "N/A" em 1.020 delas, então a marca vem do nome.

**Facetas**
- Fase 1: público-alvo, papel na estrutura, tipo de gestora (bancária, independente, ligada a plataforma, estrangeira, cooperativa, pública), liquidez (`familias.FAIXAS_LIQUIDEZ`) e ESG.
- Fase 2: tributação e aplicação mínima, porque os campos são fracos. `Tributacao_Longo_Prazo` é "N/A" em 9.928 de 13,8 mil classes, e o extrato está defasado.

**Grupo econômico** (`grupos_gestores.csv`: raiz do CNPJ → grupo e tipo). O universo tem 840 raízes de gestora. As 15 maiores cobrem 80% do PL, 51 cobrem 90% e 112 cobrem 95%. A curadoria inicial fica nessas 112, e cada uma das outras 728 raízes vira um grupo próprio, marcado como "não classificado", nunca como independente.

## 4. De quem é o dinheiro: segmentos do perfil mensal

O perfil mensal da CVM, ainda não baixado pelo repositório (`market/datasets.py:150`), traz a fatia do PL de cada classe por tipo de cotista. O arquivo tem cerca de 12,9 MB por mês, coluna `VERSAO` (vale a maior) e CNPJ com máscara. Não traz PL, que vem do informe.

| Segmento | Campos |
|---|---|
| PF varejo | `PF_VAREJO` |
| PF private | `PF_PB` |
| PJ varejo | `PJ_NAO_FINANC_VAREJO` |
| PJ private | `PJ_NAO_FINANC_PB` |
| Conta e ordem (plataformas) | `DISTRIB` |
| Institucional | EFPC, RPPS, SEGUR, EAPC, BANCO, PJ_FINANC, CAPITALIZ, CORRETORA_DISTRIB |
| Não residente | INVNR |
| Fundos (dupla contagem) | `FI_CLUBE` |
| Outros | `OUTRO` |

"Dinheiro de PF/PJ" é a soma das cinco primeiras linhas.

A captação implícita por segmento é `PL(t) × w(t) − PL(t−1) × w(t−1) × (1 + r)`. Ela só é publicada no agregado (mercado, N1, N2), com piso de materialidade. O perfil tem uma casa decimal, e 0,1 p.p. num fundo de R$ 50 bi são R$ 50 mi. Por classe, só aparecem as fatias do PL.

## 5. Indicadores para o negócio de distribuição

Organizados pela pergunta que respondem. Salvo indicação, a camada é a do investidor, o universo é o disponível e as janelas são 1 mês, 3 meses, 12 meses e no ano, sempre com datas.

| # | Pergunta | Indicador | Como se calcula | Fase |
|---|---|---|---|---|
| A1 | O mercado cresce, e por quê? | PL disponível e variação | Soma da camada do investidor no fim da competência | 1 |
| A2 | | Decomposição da variação do PL | ΔPL = captação + rendimento + resíduo; o resíduo (incorporações, eventos, fundos que pararam de informar) é publicado como qualidade | 1 |
| A3 | | Crescimento orgânico | Captação líquida 12m ÷ PL de abertura | 1 |
| A4 | | Contas e ticket médio | Cotistas (posições, não CPFs) e PL ÷ cotistas | 1 |
| B1 | Para onde vai o dinheiro? | Captação líquida por N1 e N2 | R$ e % do PL de abertura | 1 |
| B2 | | Rotação de estratégias | Mapa de calor N2 × mês, captação em % do PL: 12 meses por padrão, 24 com `--meses 25` | 1 |
| B3 | | Estratégias que mais captam e mais perdem | Top 20 famílias consolidadas (master + feeders + espelhos) | 1 |
| B4 | | Captação por público-alvo | Geral × Qualificado × Profissional | 1 |
| B5 | | Giro | Captação bruta, resgates brutos e resgates 12m ÷ PL médio | 2 |
| B6 | | Concentração da captação | % da captação positiva que ficou com as 10 maiores famílias | 2 |
| B7 | | Flash semanal | Captação da semana por N1 contra a média das 12 semanas anteriores (média diária no mês parcial) | 1 |
| B8 | | Para onde os alocadores levam o dinheiro | Fluxo dos FoFs nos fundos investidos, por variação de quantidade de cotas (exige cache da carteira com quantidade) | 3 |
| C1 | Quem ganha? | Participação por grupo gestor | Share de PL e de captação 12m, variação em p.p.; gestora da estratégia (raiz) | 1 |
| C2 | | Bancárias × independentes × plataformas × estrangeiras | Share de PL e captação por tipo de gestora | 1 |
| C3 | | Concentração por peer group | HHI e share das 5 maiores por N2, com tendência | 2 |
| C4 | | Entrantes e saídas | Gestoras com a primeira ou a última classe no universo em 12m | 2 |
| D1 | Por onde o dinheiro chega? | Composição do dono do dinheiro | Fatia de cada segmento, por N1 e tendência | 1 |
| D2 | | Plataformização | % do PL via conta e ordem, por N1 e por grupo gestor; variação 12m | 1 |
| D3 | | Espelhos e veículos dedicados | Quantidade, PL, captação 12m e novos no mês; matriz casa dedicada × gestora da estratégia | 1 |
| D4 | | Captação implícita por segmento | Seção 4, só no agregado | 2 |
| D5 | | Arquitetura aberta × cativa | Extrato `DISTRIB` (rede própria, conveniada, internet), em % do PL, com a data da entrega | 2 |
| D6 | | Remuneração e conflito declarados | Lâmina `REMUN_DISTRIB`, `DISTRIB_GESTOR_UNICO`, `CONFLITO_VENDA`: texto livre, exige classificação | 3 |
| E1 | Quanto custa? | Taxa de administração ponderada | Média ponderada pelo PL, por N2, com tendência. Só taxa em percentual e entrega do extrato com até 24 meses (regra de `report/custo.py`), com a cobertura publicada | 2 |
| E2 | | Custo total | Lâmina `PR_PL_DESPESA`, com a última lâmina de cada classe em 12 meses (o arquivo mensal traz 1.050 classes) | 2 |
| E3 | Quanto rende para a cadeia? | Pool de receita estimado | PL médio × taxa, por N1, N2 e grupo; rotulado como estimativa, sem performance | 2 |
| E4 | | Receita que os fluxos trouxeram | Captação 12m × taxa do fundo | 2 |
| E5 | | Performance | % do PL com taxa de performance, por N2 | 2 |
| F1 | O que se lança e o que se fecha? | Lançamentos e encerramentos | Primeira e última aparição no informe, mais as datas do cadastro, por N2 e gestora | 1 |
| F2 | | Liquidez | PL por faixa de prazo de resgate, com tendência | 2 |
| F3 | | Democratização | PL e número de classes por faixa de aplicação mínima | 2 |
| F4 | | Isentos | PL e captação do peer group Crédito incentivado | 1 |
| F5 | | Fechado para captação (inferido) | Sem captação bruta em 60 pregões, com resgates | 3 |
| G1 | Os fundos entregam? | Rentabilidade 12m e 3m por peer group | Mediana e quartis por raiz; o "% acima do indicador" entra na Fase 2, com o CDI do SGS | 1 |
| G2 | | Fluxo persegue retorno? | Captação 12m em % do PL × quartil de retorno 12m | 2 |
| G3 | | Risco | Volatilidade e maior queda em 12m, por N2 | 3 |
| H1 | Qual o contexto? | Camadas ao lado | Previdência: 4.395 fundos no informe de 08/2026 (R$ 3,6 tri), camada do investidor = fatia de seguradoras e EAPC. ETF: `etf_captacao_mensal`, porque o informe não cobre ETF | 1 |
| H2 | | Macro | Selic/CDI, IPCA e dólar (SGS do BCB: séries 12, 433 e 1) sobre a captação por N1 | 2 |
| H3 | | Conferência com a ANBIMA | Captação e PL por classe contra o boletim (anexo baixado à mão), com a diferença explicada | 2 |
| I1 | O que mudou? | Eventos de cadastro | Diferença entre retratos mensais: troca de gestora, administrador, classificação, taxa, público; incorporação; espelho novo | 2 |
| I2 | | Movimentos atípicos | Captação ou resgate do dia acima de X% do PL e de R$ Y mi | 2 |
| I3 | | Radar regulatório e estrutural | Planilha curada (data, tema, fonte, efeito esperado) | 2 |

## 6. Consciência de mercado

Os indicadores dizem *o que*. Esta camada diz *como ler*.
- **Mapa do mercado.** N1 → N2 por PL disponível, com a cor pelo crescimento orgânico de 12 meses. Uma figura com a indústria inteira.
- **Funil do universo.** De R$ 14,3 tri brutos até o disponível para PF/PJ, degrau a degrau, com o motivo de cada saída.
- **Como a indústria se organiza.** Uma família real (master, feeders, espelhos e FoFs que a compram), com os números do mês.
- **Leitura do mês.** Cinco frases determinísticas, montadas no bloco e iguais em todas as superfícies, cada uma com número, janela e base. Dois moldes de exemplo:
  - "Crédito privado captou R$ X bi em 12 meses (de dd/mm/aaaa a dd/mm/aaaa), Y% do PL de abertura: a maior captação entre as macroclasses."
  - "A fatia do PL disponível que chega por conta e ordem foi de A% para B% em 12 meses."
- **Glossário no ponto de uso.** Captação líquida, camada do investidor, master, feeder, espelho, FoF, subclasse, conta e ordem, público-alvo, come-cotas, taxa global.
- **Qualidade dos dados.**
  - Cobertura em % do PL: perfil do mês, carteira com até 3 meses, N2 por origem, grupo curado.
  - CNPJs sem cadastro.
  - |resíduo| ÷ |ΔPL|.
  - Fluxos estruturais (fluxo maior que o PL).
  - Concordância entre perfil e carteira.
  - Defasagem de cada fonte.

## 7. Superfícies: painel interativo, e-mail, flash e planilha

As saídas ficam em `data/reports/` (versionado), como as do monitor de ETFs.

**Painel da indústria** (`industria_dashboard.html`, a superfície principal). A página é autocontida como o dashboard atual: dados em `<script type="application/json" id="industria-data">`, um único `<script>` com o JS, CSS embutido, tema claro, padrão de `docs/linguagem-visual.md` e cores neutras. A rampa cinza marca quantidade, o amarelo marca só a seleção do usuário, e verde e vermelho só aparecem ao lado de um sinal.

| Seção | Conteúdo |
|---|---|
| Visão executiva | Destaques numéricos (PL disponível, captação 12m em R$ e %, contas, % via plataformas, classes disponíveis), leitura do mês e funil |
| Onde está o dinheiro | Mapa N1 → N2 e dono do dinheiro |
| Para onde vai | Captação por N1/N2, mapa de calor, famílias que mais captam e mais perdem |
| Quem ganha | Share por grupo gestor com variação, bancárias × independentes × plataformas × estrangeiras |
| Por onde chega | Segmentos, plataformização, espelhos e veículos dedicados, FoFs |
| Desempenho | Rentabilidade 12m e 3m por peer group, em quartis |
| O que mudou | Lançamentos e encerramentos |
| Explorador | Tabela filtrável e ordenável por classe e por estratégia, com o quartil de cada fundo no peer group |
| Como ler | Metodologia, glossário, qualidade dos dados e fontes com link |

Os filtros globais são público-alvo, N1, papel na estrutura e tipo de gestora. O cubo compacto (competência × N2 × público × papel × tipo de gestora) permite recalcular os agregados no navegador. O explorador traz as cerca de 8.400 classes do universo. Cru, isso dá 2,57 MB. A compactação leva o total a cerca de 1,7 MB:
- nomes curtos, sem o sufixo jurídico;
- inteiros em milhares de reais e em pontos-base;
- dicionários de grupo e N2;
- fluxos de segmento só em 12 meses;
- classes excluídas fora do painel (elas vão para a planilha).

**E-mail mensal** (`industria-AAAA-MM-DD.md/html`, 640 px, só tabelas, até 100 KB, com o corte de figuras de `digest.py:2527-2552`). Traz a leitura do mês, cinco figuras e o link para o painel.

**Flash semanal** (e-mail `industria-flash-AAAA-MM-DD`). Mostra a captação da semana por N1 na camada do investidor, contra a média de 12 semanas. Usa o retrato do universo do último ciclo mensal e não roda se esse retrato tiver mais de 45 dias.

**Planilha explorador** (`industria_explorador.xlsx`). Abas: Leitura, Funil, Estratégias (N2), Famílias, Classes (com autofiltro e as excluídas com o motivo), Gestoras, Espelhos, Lançamentos e encerramentos, Período de cada aba, Fontes.

## 8. Arquitetura

O monitor segue o molde do bloco de mercado dos ETFs (`market/etf_captacao_mensal.py:572` → `report/etf_captacao.py`): um bloco em dicionário, com `available`/`reason`, janelas, cobertura e leitura, desenhado por renderizadores separados. Não usa o `RunReport` das vitrines.

**Pacote novo `shelf_monitor/industria/`**

| Módulo | Papel | Reaproveita |
|---|---|---|
| `cotistas.py` | Perfil mensal: parser (dedup por `VERSAO`, linhas desalinhadas contadas), cache compacto e segmentos | `market/fetch.fetch`, `parse.iter_rows` |
| `universo.py` | Funil com motivo e política própria de universo | regex de `profile.py:32-34`; `registro_subclasse` já parseado |
| `estrutura.py` | Papel, raiz, grupo, casa dedicada | `MapaDeFamilias` com os acessores públicos novos `carteira()` e `investidores_de()`; `master_de`; `perfil_determinado` |
| `taxonomia.py` | N1, N2, facetas, origem e confiança | `mercado_csv.categoria_da_linha`, `classify.nivel1`, `familias.FAIXAS_LIQUIDEZ` |
| `painel.py` | Painel mensal em memória, no grão (CNPJ, subclasse) × competência, com as fórmulas da seção 2 | `SerieMensal._dados` (`MesSubclasse`), `retail_flow.agrupar_por_classe` |
| `bloco.py` | Bloco, leitura e prazos; estado do mês por `estado_do_mes` | `market/etf_captacao_mensal.py:418-441` |
| `flash.py` | Flash semanal | `read_daily_flows(zip, alvo=universo)` (`retail_flow.py:127`) |
| `cli.py` | `registrar_industria(subparsers, common)`, chamado logo após `main.py:2113` | padrão de `market/etf_bruto/extrator.py:267` |

**Contrato do bloco** (chaves de topo):
- identificação e janelas: `available`, `reason`, `competencia`, `janelas{1m,3m,ano,12m}` (cada uma com rótulo, de, até e base);
- universo e camadas: `camadas`, `funil`, `universo`, `adjacentes{etf,previdencia}`;
- análise: `mercado{historico,decomposicao_12m}`, `estrategias{n1,n2}`, `segmentos`, `estrutura{papeis,espelhos}`, `familias_top`, `gestoras`, `publico`, `lancamentos`;
- controle: `qualidade`, `limiares`, `fontes`, `leitura`, `prazos`;
- privada: `_produtos`.

**Renderizadores** em `shelf_monitor/report/`: `industria_dashboard.py`, `industria_email.py` e `industria_planilha.py`.
- `industria_dashboard.py` usa `assets/industria.js` e um `industria.css` pequeno. Reaproveita de `report/dashboard.py` os itens `_embed_json` (L957), `_FONTES_WEB`, `_LOGO`, `_painel` (L945) e `_STYLE`.
- `industria_email.py` é montado com `email_xp` e `graficos_email`.
- `industria_planilha.py` segue o molde de `etf_report.export_etf_excel:404`.

**JS compartilhado.** Os primitivos de `assets/dashboard.js` passam para `assets/graficos.js`, num commit só dele:
- formatadores, L117-183;
- `quandoVisivel`, L83-103;
- de `figura` a `graficoComTabela`, L195-827;
- `SEQ_STEPS`/`heatColor`, L866-873.

O resto fica onde está. `dashboard.py:76` passa a embutir `graficos + dashboard` no mesmo `<script>`, então as duas páginas seguem com exatamente 2 tags, como exigem `test_shelf_monitor.py:1056`, `:1235` e `test_previdencia_dashboard.py:178`.

**Mudanças no código existente**
- `normalize.py:37-47`: `normalize_cnpj`/`format_cnpj` aceitam CNPJ alfanumérico. Os pontos que usam `isdigit` também são revistos: `distribuivel.py:229`, `familias.py:812`, `report/familias.py:61`, `market/etf_flow.py:242`. Ainda não há CNPJ alfanumérico nos arquivos (medido em 28/09), mas o bloco de lançamentos é onde ele aparece primeiro.
- `report/rentabilidade.py`, três mudanças:
  - `_resumir` (L193-227) deduplica (CNPJ, subclasse, dia) antes de somar captação e resgates. Hoje o arquivo não tem duplicatas, mas já teve 1,03% em 20/09/2026, e o flash lê o mês corrente.
  - `_CABECALHO_CACHE` (L102) muda, para refazer os caches gravados.
  - `build(ate=AAAAMM)` evita rebaixar e reler o mês corrente (L316, L321) no ciclo mensal.
- `market/fees.py:182`: a coluna `TRIB_LPRAZO` não existe no extrato (bug).
- `market/datasets.py`: `dataset_perfil_mensal(competencia)`. Só as 3 competências mais recentes são revalidadas, porque a CVM regenera arquivos antigos (o de 09/2025 tem Last-Modified de 26/09/2026). `CAD_FI_HIST` e `dataset_lamina` entram na Fase 2, e a docstring e o comentário "fora do escopo" (L1-7, L146-152) são atualizados.
- `market/historico.py` (novo), chamado em `importer.run_import` (L131-141) logo depois de `write_database`. Grava o retrato mensal das dimensões a cada importação da base: um mês sem retrato é histórico perdido.
- `main._captacao_mensal_de_etf` (L955-989) vira um ajudante público que devolve dados em vez de imprimir, para a camada ETF.
- `report/shared/fontes.py`: `CVM_PERFIL_MENSAL`, `CVM_CDA`, depois `CVM_LAMINA` e `BCB_SGS`, e o grupo `FONTES_INDUSTRIA`.
- `report/shared/limiares.py` recebe os cortes das seções 1 e 2, cada um testado em `tests/test_shared.py`. `report/shared/numeros.py` recebe os formatadores de R$, % e p.p. hoje espalhados (`etf_competicao.py:654-669`, `retail_flow._reais`).
- `tests/fixtures/cli_surface.json` ganha a entrada `industria`. Os nomes de flag não podem ter dígito (regex de `test_main.py:219`).

**CLI** `python -m shelf_monitor industria`:
- `--ciclo auto|mensal|flash`. O padrão é `auto`: roda o ciclo mensal quando o informe, o perfil e a carteira de M já saíram e M ainda não foi publicado; senão, o flash, se o último tiver 7 dias ou mais; senão, não faz nada e sai com 0.
- `--competencia AAAA-MM` refaz um mês.
- `--meses` define a janela (13 por padrão, 25 é opt-in).
- `--force`, `--offline` e `--json`.
- Cada flag diz o custo no texto de ajuda.

**Dados**

| Caminho | Conteúdo | Git |
|---|---|---|
| `data/historico/cadastro/AAAAMM.csv.gz` | Retrato das classes e subclasses, com os campos do fundo, ordenado e com gzip de `mtime=0` (bytes idênticos a cada nova execução, ~1 MB/mês) | sim: é dado primário, a CVM guarda só 37 classes canceladas |
| `data/historico/industria/AAAAMM.json` | Bloco mensal publicado, como trilha de auditoria | sim |
| `data/industria/` | Curadoria: `taxonomia.csv`, `grupos_gestores.csv`, `marcas_de_distribuidor.csv`, `overrides_estrategia.csv` (depois `radar_regulatorio.csv`) | sim |
| `data/cache/industria/` | Perfis compactos e o retrato do universo para o flash | não |
| `data/reports/` | Painel, e-mails, flash e planilha | sim |

**Orçamento de tempo e memória** (sem pandas: dicionários por tupla e `float()` direto nos próprios caches):

| Execução | Tempo | Pico de memória |
|---|---|---|
| Ciclo mensal com cache | 1,5 a 2 min | 0,6 a 0,8 GB |
| Primeira vez | 8 a 10 min | 170 MB de perfis baixados e cache da série refeito |
| Flash | 20 a 30 s | ~100 MB |

## 9. Fases

**Fase 1 — MVP**, em cinco entregas que já servem sozinhas:
- **1A. Fundação e início do histórico.** É a primeira a entrar, porque cada mês sem retrato do cadastro se perde.
  - `docs/spec-industria-fundos.md`, no formato de `docs/spec-gaps-xp-familias.md`.
  - CNPJ alfanumérico; deduplicação, cabeçalho e `ate=` no `SerieMensal`; correção do `fees.py:182`.
  - `market/historico.py`; perfil mensal com parser e cache.
  - Subcomando `industria` imprimindo funil, camadas e segmentos de M no console e em `--json`.
  - Valor: a primeira resposta sobre quem entra, quem sai, e quanto é dinheiro de PF/PJ e de conta e ordem.
- **1B. Estrutura e taxonomia.** `grupos_gestores.csv` com as 112 raízes, os papéis da seção 2, `taxonomia.csv`, overrides, a regra do MM Livre e a casa dedicada.
- **1C. Bloco e planilha.**
  - Janelas, camada do investidor, segmentos agregados, decomposição do PL, lançamentos e encerramentos.
  - Camadas de previdência e ETF.
  - Leitura do mês e planilha explorador.
  - Aqui se valida o número antes de desenhar.
- **1D. Painel interativo.** Extração do `graficos.js` num commit próprio. Depois, a página com seções, filtros e explorador, e os testes dela.
- **1E. E-mail e cadência.** E-mail mensal, flash semanal, `--ciclo auto`, passo [4/4] no `run_monitor.bat` com código de saída próprio e a seção no README.

**Fase 2 — Aprofundamento**
- Eventos de cadastro pelos retratos, com recuo por `cad_fi_hist` e cancelamentos (`--backfill-cadastro`).
- Taxas, custo total (lâmina) e pool de receita.
- Canal `DISTRIB`, liquidez, aplicação mínima, tributação e as demais regras de carteira.
- Captação por segmento no agregado; giro, concentração e HHI, entrantes; fluxo × retorno.
- SGS do BCB (CDI para o "% acima do indicador" e macro), conferência com a ANBIMA (ampliar `anbima_boletim.TIPOS`, L39), movimentos atípicos, radar regulatório e envio do e-mail.

**Fase 3 — Expansão**
- FIDC, FII e Fiagro pelos informes mensais próprios.
- Fluxo dos alocadores: cache da carteira com quantidade (versão 5) e 13 meses de CDA, como opt-in `--historico-carteira`.
- Fechamento para captação inferido, risco e classificação dos textos da lâmina.

## 10. Verificação

**Testes por costura** (`tests/test_industria_*.py`, sem rede). As fixtures seguem o formato real da CVM (latin-1, `;`), geradas como em `tests/fixtures/make_market_fixtures.py`, e a carteira usa o `Cda` de `tests/carteira_apoio.py`.

| Costura | O que o teste trava |
|---|---|
| Funil | É partição: cada linha tem um motivo, e as somas de quantidade e PL fecham com a base. Classe de previdência registrada como exclusiva cai em `previdencia`. Subclasse vale antes de classe |
| Tempo | Classe liquidada no meio da janela mantém os fluxos anteriores. Nada aparece depois do cancelamento. Lançamento só conta a partir do primeiro mês |
| Camadas | `f_inv + f_fundos = fluxo declarado`; Σ segmentos + resíduo = fluxo declarado; 0 ≤ `pl_inv` ≤ PL; fatias somam 1; N2 → N1 → total fecham em toda janela; camadas ao lado nunca entram no total |
| Estrutura | Duas raízes do mesmo grupo dão feeder, não espelho. O flag do extrato nunca contradiz a carteira. Cadeia espelho → feeder → master. Recuo de competência sob sigilo. Casa dedicada por token |
| Taxonomia | Rótulo truncado em 40 caracteres; herança da raiz; regra do MM Livre; override vence; confiança |
| Chaves | Junção de CNPJ com máscara, sem máscara e alfanumérico. O regex de paridade de `test_shelf_monitor.py:1162` passa a aceitar letras |
| Janelas | 12 meses é a soma dos meses. Rótulo, datas dd/mm/aaaa (de `SerieMensal.calendario`) e base em toda janela |
| Perfil | Cabeçalho real de 08/2026, dedup por `VERSAO`, linhas desalinhadas contadas |
| Determinismo e neutralidade | O JSON do bloco é igual com a entrada embaralhada. O retrato gera os mesmos bytes numa nova execução. Não há chave `foco` nem destaque de casa |
| Painel HTML | Duas `<script>`, nenhuma com `src`, JSON válido. Nome de fundo com `</script>` sai escapado. Todo alvo de `getElementById` existe. Payload ≤ 2,5 MB com 10 mil linhas sintéticas |
| E-mail e flash | Sem svg/style/script/img/div, até 100 KB, mesmos fatos no md e no html. O flash recusa dia incompleto e retrato com mais de 45 dias |
| Planilha e CLI | Ordem das abas, período de cada aba, fontes; `cli_surface.json` |

**Execução real**
1. `python -m unittest discover -s tests -v` inteiro verde antes e depois de cada entrega, incluindo as páginas de fundos e de previdência depois da extração do `graficos.js`.
2. `python -m shelf_monitor industria --competencia 2026-08`, medindo tempo e memória. O resultado deve reproduzir a medição do monitor na seção de status: 8.230 classes no universo e R$ 3,80 tri na camada do investidor. Diferença maior aponta mudança de regra ou de dado, e vai para as notas.
3. Conferir cinco famílias (master com feeders e espelhos) contra a consulta pública da CVM.
4. Abrir o painel no navegador embutido: testar filtros, explorador e largura de celular.
5. Na Fase 2, com o anexo do boletim ANBIMA, conferir captação e PL por classe e publicar a diferença.

## Riscos e limites

- **A captação por segmento é estimada.** O perfil dá a composição do PL, não o fluxo por tipo de cotista. Por isso só sai agregada e rotulada.
- **Defasagem.** O ciclo mensal fecha só quando saem o perfil e a carteira, cerca de 15 dias após o fim do mês. O flash usa o universo do último ciclo.
- **Histórico de cadastro.** Não existe para classes pós-RCVM 175, e o monitor passa a gravá-lo na 1A. No recuo de 24 meses o risco é pequeno.
  - Dos 31.483 CNPJs do informe entre 10/2024 e 09/2026, os 3.886 que já saíram do cadastro somam 1,0% do giro bruto.
  - Quase todos seguem no `registro_fundo` como cancelados.
- **Curadoria.** Grupo econômico e casa dedicada dependem dela. A cobertura aparece no painel, e o que não está mapeado sai como "não classificado".
- **Extrato defasado.** Só 5,0 mil classes entregaram em 2026. Por isso taxa, canal, liquidez e aplicação mínima entram na Fase 2, com a data da entrega à vista.
- **ANBIMA.** Os termos do portal proíbem automação, então a conferência depende do anexo baixado à mão.
- **Agendamento.** O flash semanal só sai se o `run_monitor.bat` rodar ao menos uma vez por semana. Não há tarefa agendada (lacuna G15 de `docs/auditoria-fluxos.md`), e o `pause` do fim bloquearia uma. Isso fica como item à parte.
