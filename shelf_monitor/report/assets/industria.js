/* Painel da industria de fundos: filtros, destaques e blocos.

   Vem depois de `graficos.js`, na mesma tag de script, e usa os primitivos de
   la (figura, barrasH, barrasDivergentes, dumbbell, formatos, rampa). Tudo
   aqui mora dentro de uma funcao: nome local nunca colide com os globais de
   `graficos.js` - um `const fluxo` no topo redeclararia a funcao `fluxo` de
   la e derrubaria o script inteiro, navegacao inclusive.

   Os filtros (publico, macroclasse, papel, tipo de gestora) recalculam o que
   sai do cubo - destaques, macroclasses, peer groups, captacao, canal, papel,
   tipo de gestora - e o explorador. O que vem pronto do bloco (funil, dono do
   dinheiro, mes a mes, mapa de calor, familias, gestoras, retorno,
   lancamentos e a dinamica da Fase 2: giro, macro, concentracao, entrantes,
   captacao por tipo de cotista e fluxo x retorno) diz no subtitulo qual
   filtro vale ali, quando vale algum.

   Dinheiro do cubo e do explorador chega em milhares de reais (o Python
   compacta) e volta a reais na leitura, antes de qualquer conta. */
(function painelDaIndustria() {
  const noDados = document.getElementById("industria-data");
  if (!noDados) return;
  const DADOS = JSON.parse(noDados.textContent);
  const META = DADOS.meta;
  const DIC = DADOS.dic || {};
  const ROT = DADOS.rotulos;
  const JANELAS = ["1m", "3m", "ano", "12m"];
  const FIM = dataBR(META.informe && META.informe.ate);
  const BASE_12M = dataBR(META.janelas["12m"].data_base);
  const FONTE = "Fonte: CVM (informe diário, perfil mensal, cadastro e carteira); estratégia, papel e " +
    "grupo gestor pela classificação do monitor.";
  const SEM_FILTRO = " · sem filtro: o universo inteiro";

  /* --- formatos (os de report/shared/numeros.py) ------------------------------ */
  // "R$ 3,95 tri", "R$ 8,6 bi", "R$ 115 mi" - sem sinal.
  function dinheiro(v) {
    if (!ok(v)) return "-";
    const a = Math.abs(v);
    if (a >= 1e12) return `R$ ${numBR(v / 1e12, 2)} tri`;
    if (a >= 1e9) return `R$ ${numBR(v / 1e9, 1)} bi`;
    return `R$ ${numBR(v / 1e6, 0)} mi`;
  }
  // "+R$ 1,2 bi" - fluxo, que tem direcao.
  const dinheiroSinal = v => (ok(v) ? (v < 0 ? "-" : "+") + dinheiro(Math.abs(v)) : "-");
  // No explorador o fundo pequeno nao vira "R$ 0 mi".
  function dinheiroFino(v) {
    if (!ok(v)) return "-";
    const a = Math.abs(v);
    if (a >= 1e9) return dinheiro(v);
    if (a >= 1e6) return `R$ ${numBR(v / 1e6, 1)} mi`;
    return `R$ ${numBR(v / 1e3, 0)} mil`;
  }
  const dinheiroFinoSinal = v => (ok(v) ? (v < 0 ? "-" : "+") + dinheiroFino(Math.abs(v)) : "-");
  const fracao = (v, casas = 1) => (ok(v) ? numBR(v * 100, casas) + "%" : "-");
  // Zero arredondado sai sem sinal: "-0,0%" leria como perda.
  function fracaoSinal(v, casas = 1) {
    if (!ok(v)) return "-";
    if (Math.abs(v * 100) < 0.5 * Math.pow(10, -casas)) return fracao(0, casas);
    return (v > 0 ? "+" : "") + fracao(v, casas);
  }
  // "42.592.315/0001-15" - o bloco guarda a raiz crua em algumas listas.
  const cnpjBR = s => (/^\d{14}$/.test(s || "")
    ? `${s.slice(0, 2)}.${s.slice(2, 5)}.${s.slice(5, 8)}/${s.slice(8, 12)}-${s.slice(12)}` : s || "");
  function pontos(v) {
    if (!ok(v)) return "-";
    if (Math.abs(v) < 0.05) return "0,0 pp";
    return (v > 0 ? "+" : "-") + numBR(Math.abs(v), 1) + " pp";
  }
  function contas(n) {
    if (!n) return "-";
    if (n >= 1e6) return numBR(n / 1e6, 1) + " mi";
    if (n >= 1e4) return numBR(n / 1e3, 0) + " mil";
    return inteiro(n);
  }
  function ticket(v) {
    if (!ok(v)) return "-";
    if (v >= 1e6) return `R$ ${numBR(v / 1e6, 1)} mi`;
    return `R$ ${numBR(v / 1e3, 0)} mil`;
  }
  const mesCurto = c => (/^\d{6}$/.test(c || "") ? `${MESES_BR[Number(c.slice(4)) - 1]}/${c.slice(2, 4)}` : c || "-");
  const classeSinal = v => (ok(v) && v > 0 ? "pos" : ok(v) && v < 0 ? "neg" : "");
  // Valor em reais nunca quebra no meio ("-" numa linha, "R$ 1,3 bi" na outra).
  const REAIS_RE = /[+-]?R\$ -?\d[\d.,]* (?:tri|bi|mi|mil)\b/g;
  const texto = s => escapeHtml(s).replace(REAIS_RE, m => `<span class="nowrap">${m}</span>`);
  const semAcento = s => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  /* --- rotulos e ordem das dimensoes ------------------------------------------ */
  const ROTULO = {
    publico: v => (v in ROT.publico ? ROT.publico[v] : v),
    n1: v => v || "Não classificado",
    papel: v => ROT.papel[v] || v,
    tipo_gestora: v => ROT.tipo_gestora[v] || v,
  };
  const ORDEM = {
    publico: Object.keys(ROT.publico),
    n1: ROT.n1_ordem,
    papel: Object.keys(ROT.papel),
    tipo_gestora: Object.keys(ROT.tipo_gestora),
  };
  function ordenar(campo, valores) {
    const ordem = ORDEM[campo];
    const pos = v => { const i = ordem.indexOf(v); return i < 0 ? ordem.length : i; };
    return valores.slice().sort((a, b) => pos(a) - pos(b) || String(a).localeCompare(String(b), "pt-BR"));
  }
  const ORIGEM = {
    override: "curadoria", anbima: "rótulo ANBIMA", raiz: "herdada da raiz", carteira: "regra de carteira",
    nome: "nome do fundo", cvm: "classe CVM e nome",
  };

  /* --- o cubo e o explorador, de volta a objetos ------------------------------ */
  const CUBO = (() => {
    const c = DADOS.cubo;
    const mil = new Set(c.em_mil);
    const n = c.dimensoes.length;
    return c.linhas.map(l => {
      const o = {};
      c.dimensoes.forEach((d, i) => { o[d] = (DIC[d] || [])[l[i]]; });
      c.medidas.forEach((m, i) => { const v = l[n + i] || 0; o[m] = mil.has(m) ? v * 1e3 : v; });
      return o;
    });
  })();
  const MEDIDAS = DADOS.cubo.medidas;
  const PRODUTOS = (() => {
    const x = DADOS.explorador;
    const dic = new Set(x.dicionario), mil = new Set(x.em_mil);
    return x.linhas.map(l => {
      const o = {};
      x.colunas.forEach((c, i) => {
        const v = l[i];
        o[c] = dic.has(c) ? (DIC[c] || [])[v] : mil.has(c) && ok(v) ? v * 1e3 : v;
      });
      o.busca = semAcento([o.nome, o.raiz_nome, o.gestora, o.n2, o.n1, o.casa_dedicada, o.cnpj,
        String(o.cnpj || "").replace(/\D/g, "")].join(" "));
      return o;
    });
  })();
  // Peer group com o mesmo nome em duas macroclasses ("Livre" em Ações e em
  // Multimercado) leva a macroclasse junto quando o grafico mistura as duas.
  const N2_AMBIGUO = (() => {
    const n1s = new Map();
    CUBO.forEach(c => { if (!n1s.has(c.n2)) n1s.set(c.n2, new Set()); n1s.get(c.n2).add(c.n1); });
    return new Set([...n1s].filter(([, s]) => s.size > 1).map(([n2]) => n2));
  })();
  const nomeN2 = (n1, n2) => (N2_AMBIGUO.has(n2) ? `${n2} (${n1})` : n2);

  /* --- filtros ---------------------------------------------------------------- */
  const TODOS = "*";
  const FILTRO = { publico: TODOS, n1: TODOS, papel: TODOS, tipo_gestora: TODOS };
  const CAMPOS = Object.keys(FILTRO);
  // `ignorar`: o eixo do proprio grafico. O grafico por macroclasse mostra
  // todas elas e marca a escolhida, em vez de virar uma barra so.
  const passa = (item, ignorar) => CAMPOS.every(k => k === ignorar || FILTRO[k] === TODOS || item[k] === FILTRO[k]);
  const filtrado = ignorar => CAMPOS.some(k => k !== ignorar && FILTRO[k] !== TODOS);
  function recorte(ignorar) {
    return CAMPOS.filter(k => k !== ignorar && FILTRO[k] !== TODOS).map(k => ROTULO[k](FILTRO[k])).join(" · ");
  }
  // O subtitulo diz o recorte - ou que o grafico e do universo inteiro.
  const comRecorte = (s, ignorar) => (filtrado(ignorar) ? `${s} · recorte: ${recorte(ignorar)}` : s);
  const corDaSelecao = (campo, valor) => (FILTRO[campo] === valor ? "var(--tag)" : "var(--comp-1)");

  function vazio() { return Object.fromEntries(MEDIDAS.map(m => [m, 0])); }
  function somar(celulas, chave) {
    const grupos = new Map();
    celulas.forEach(c => {
      const g = chave(c);
      let a = grupos.get(g);
      if (!a) { a = vazio(); grupos.set(g, a); }
      MEDIDAS.forEach(m => { a[m] += c[m]; });
    });
    return grupos;
  }
  function total(celulas) {
    const a = vazio();
    celulas.forEach(c => MEDIDAS.forEach(m => { a[m] += c[m]; }));
    return a;
  }
  const sobre = (a, j) => (a[`pl_abertura_${j}`] > 0 ? a[`fluxo_${j}`] / a[`pl_abertura_${j}`] : null);
  const fatiaCeo = a => (a.pl_perfil > 0 ? a.pl_conta_e_ordem / a.pl_perfil : null);
  const fatiaPfPj = a => (a.pl_perfil > 0 ? a.pl_pf_pj / a.pl_perfil : null);
  function variacaoCeo(a) {
    const agora = fatiaCeo(a);
    const antes = a.pl_perfil_12m_antes > 0 ? a.pl_conta_e_ordem_12m_antes / a.pl_perfil_12m_antes : null;
    return ok(agora) && ok(antes) ? (agora - antes) * 100 : null;
  }

  const SELECT = {};
  function montarFiltros() {
    document.querySelectorAll("#filtros select[data-campo]").forEach(sel => {
      const campo = sel.dataset.campo;
      SELECT[campo] = sel;
      const valores = ordenar(campo, [...new Set(CUBO.map(c => c[campo]))]);
      sel.innerHTML = `<option value="${TODOS}">Todos</option>` + valores
        .map(v => `<option value="${escapeHtml(v)}">${escapeHtml(ROTULO[campo](v))}</option>`).join("");
      sel.addEventListener("change", () => definir(campo, sel.value));
    });
    document.getElementById("filtro-limpar").addEventListener("click", () => {
      CAMPOS.forEach(k => { FILTRO[k] = TODOS; });
      atualizar();
    });
  }
  function definir(campo, valor) {
    FILTRO[campo] = valor;
    atualizar();
  }
  const alternar = (campo, valor) => definir(campo, FILTRO[campo] === valor ? TODOS : valor);
  function sincronizarFiltros() {
    CAMPOS.forEach(k => {
      const sel = SELECT[k];
      if (!sel) return;
      sel.value = FILTRO[k];
      sel.classList.toggle("ativo", FILTRO[k] !== TODOS);
    });
    document.getElementById("filtro-limpar").disabled = !filtrado();
    const nota = document.getElementById("filtros-nota");
    if (!filtrado()) {
      nota.textContent = "Sem filtro: o universo disponível inteiro.";
      return;
    }
    const t = total(CUBO.filter(c => passa(c)));
    const universo = total(CUBO);
    nota.innerHTML = `Recorte: <b>${escapeHtml(recorte())}</b> · ${texto(dinheiro(t.pl))} de PL do investidor, ` +
      `${fracao(universo.pl ? t.pl / universo.pl : null)} do universo disponível em ${escapeHtml(FIM)}.`;
  }

  /* --- tabela ------------------------------------------------------------------
     `colunas`: [{rotulo, num}]. Linha: {celulas, classe} ou {grupo} (a linha de
     macroclasse). Celula: HTML ja escapado, ou {html, classe}. A primeira
     celula e o cabecalho da linha. */
  const sinal = (html, v) => ({ html, classe: classeSinal(v) });
  function tabela(colunas, linhas, classe = "") {
    const cabeca = colunas.map(c => `<th scope="col"${c.num ? ' class="num"' : ""}>${escapeHtml(c.rotulo)}</th>`).join("");
    const corpo = linhas.map(l => {
      if (l.grupo !== undefined) {
        return `<tr class="grupo-n1"><th colspan="${colunas.length}" scope="colgroup">${escapeHtml(l.grupo)}</th></tr>`;
      }
      const celulas = l.celulas.map((c, i) => {
        const html = c !== null && typeof c === "object" ? c.html : c;
        if (i === 0) return `<th scope="row">${html}</th>`;
        const classes = [colunas[i] && colunas[i].num ? "num" : "", c && c.classe ? c.classe : ""].filter(Boolean);
        return `<td${classes.length ? ` class="${classes.join(" ")}"` : ""}>${html}</td>`;
      }).join("");
      return `<tr${l.classe ? ` class="${l.classe}"` : ""}>${celulas}</tr>`;
    }).join("");
    return `<div class="table-scroll"><table${classe ? ` class="${classe}"` : ""}>` +
      `<thead><tr>${cabeca}</tr></thead><tbody>${corpo}</tbody></table></div>`;
  }
  const botaoFiltro = (campo, valor, rotulo) =>
    `<button type="button" class="linha-filtro" data-campo="${campo}" data-valor="${escapeHtml(valor)}" ` +
    `aria-pressed="${FILTRO[campo] === valor}" title="Filtrar a página por ${escapeHtml(rotulo)}">${escapeHtml(rotulo)}</button>`;
  function ligarBotoes(mount) {
    mount.querySelectorAll("button.linha-filtro").forEach(b =>
      b.addEventListener("click", () => alternar(b.dataset.campo, b.dataset.valor)));
  }

  /* --- colunas por mes (fluxo com sinal) --------------------------------------
     Uma coluna por competencia sobre o zero; negativa em vermelho, que vem
     com o sinal no eixo e no valor escrito. */
  function colunasMensais(mount, pontosDoMes, o = {}) {
    quandoVisivel(mount, largura => {
      if (!pontosDoMes.length) { mount.innerHTML = VAZIO; return; }
      const W = Math.max(largura, 280), H = o.altura || 230;
      const padT = 22, padB = 26, padE = 46, padD = 6;
      const valores = pontosDoMes.map(p => p.valor);
      const esc = escala(Math.min(0, ...valores), Math.max(0, ...valores), 4);
      const y = v => padT + (1 - (v - esc.lo) / ((esc.hi - esc.lo) || 1)) * (H - padT - padB);
      const svg = svgRaiz(W, H, o.aria);
      esc.ticks.forEach(t => {
        svg.appendChild(el("line", { class: "gridline", x1: padE, x2: W - padD, y1: y(t), y2: y(t) }));
        svg.appendChild(el("text", { class: "tick", x: padE - 6, y: y(t) + 4, "text-anchor": "end" }, o.fmtEixo(t)));
      });
      const passo = (W - padE - padD) / pontosDoMes.length;
      const w = Math.max(4, Math.min(44, passo * 0.64));
      const comValor = passo >= 46;
      pontosDoMes.forEach((p, i) => {
        const x = padE + i * passo + (passo - w) / 2;
        const barraMes = coluna(x, y(0), w, y(p.valor), p.valor >= 0 ? "var(--comp-1)" : "var(--critical)",
          `${p.rotulo}: ${o.fmt(p.valor)}`);
        if (barraMes) svg.appendChild(barraMes);
        // Sem espaco, um rotulo sim e um nao, contando do ultimo mes para tras.
        if (passo >= 34 || (pontosDoMes.length - 1 - i) % 2 === 0) {
          svg.appendChild(el("text", { class: "tick", x: x + w / 2, y: H - 8, "text-anchor": "middle" }, p.curto));
        }
        if (comValor || i === pontosDoMes.length - 1) {
          const acima = p.valor >= 0;
          svg.appendChild(el("text", {
            class: "val", x: x + w / 2, y: acima ? y(p.valor) - 5 : y(p.valor) + 13, "text-anchor": "middle",
            "font-size": 11,
          }, o.fmtCurto(p.valor)));
        }
      });
      svg.appendChild(el("line", { class: "zeroline", x1: padE, x2: W - padD, y1: y(0), y2: y(0) }));
      mount.replaceChildren(svg);
    });
  }

  // Rampa divergente do mapa de calor: verde para captacao, vermelho para
  // resgate, a intensidade pelo tamanho; o sinal vai escrito na celula.
  const VERDES = ["#e8f3ec", "#c6e2ce", "#95c8a4", "#5ea878", "#2e7d4f"];
  const VERMELHOS = ["#faeae9", "#f2c9c6", "#e59c97", "#d56d66", "#c6423c"];
  function corDivergente(v, max) {
    if (!ok(v) || !max || v === 0) return null;
    const passos = v > 0 ? VERDES : VERMELHOS;
    const i = Math.min(passos.length - 1, Math.floor((Math.abs(v) / max) * passos.length));
    return { fill: passos[i], ink: i >= 3 ? "#ffffff" : "#121212" };
  }

  /* --- destaques ------------------------------------------------------------------ */
  const KPIS = document.getElementById("kpis");
  const KPIS_DO_UNIVERSO = KPIS ? KPIS.innerHTML : "";
  function kpi(alvo, rotulo, valor, nota, comparacao, prazo) {
    return `<a class="kpi" href="#${alvo}"><span class="kpi-label">${texto(rotulo)}</span>` +
      `<span class="kpi-value">${texto(valor)}</span><span class="kpi-note">${texto(nota)}</span>` +
      (comparacao ? `<span class="kpi-comp">${texto(comparacao)}</span>` : "") +
      (prazo ? `<span class="kpi-prazo">${texto(prazo)}</span>` : "") + "</a>";
  }
  function desenharKpis() {
    if (!KPIS) return;
    KPIS.classList.toggle("recorte", filtrado());
    // Sem filtro valem os destaques que vieram escritos na pagina.
    if (!filtrado()) { KPIS.innerHTML = KPIS_DO_UNIVERSO; return; }
    const t = total(CUBO.filter(c => passa(c)));
    const classes = new Set(PRODUTOS.filter(p => passa(p)).map(p => p.cnpj)).size;
    const variacao = variacaoCeo(t);
    KPIS.innerHTML = [
      kpi("onde-section", "PL disponível para PF e PJ", dinheiro(t.pl),
        "na camada do investidor, sem o dinheiro que um fundo aplica em outro", "",
        `fechamento de ${FIM} · ${inteiro(classes)} ${plural(classes, "classe", "classes")} com informe`),
      kpi("para-onde-section", "Captação do investidor em 12 meses", dinheiroSinal(t.fluxo_12m),
        `${fracaoSinal(sobre(t, "12m"))} do PL de abertura`, "", META.janelas["12m"].texto),
      kpi("para-onde-section", `Captação do investidor em ${META.rotulo}`, dinheiroSinal(t.fluxo_1m),
        `${fracaoSinal(sobre(t, "1m"))} do PL de abertura`, "", META.janelas["1m"].texto),
      kpi("chega-section", "Via conta e ordem", fracao(fatiaCeo(t)),
        "do PL, aplicado por plataformas em nome dos clientes",
        ok(variacao) ? `${pontos(variacao)} em 12 meses` : "", `${FIM} contra ${BASE_12M}`),
      kpi("onde-section", "Contas", contas(t.cotistas),
        `posições de cotistas, não CPFs · ${ticket(t.cotistas ? t.pl / t.cotistas : null)} por conta`, "",
        `fechamento de ${FIM}`),
    ].join("\n");
  }

  function desenharResumoN1() {
    const mount = document.getElementById("tabela-n1-resumo");
    const celulas = CUBO.filter(c => passa(c, "n1"));
    const grupos = somar(celulas, c => c.n1);
    const t = total(celulas);
    const linhas = ordenar("n1", [...grupos.keys()]).map(n1 => {
      const a = grupos.get(n1);
      return {
        classe: FILTRO.n1 === n1 ? "selecionada" : "",
        celulas: [
          botaoFiltro("n1", n1, n1), texto(dinheiro(a.pl)), fracao(t.pl ? a.pl / t.pl : null),
          sinal(texto(dinheiroSinal(a.fluxo_12m)), a.fluxo_12m), sinal(fracaoSinal(sobre(a, "12m")), a.fluxo_12m),
          sinal(texto(dinheiroSinal(a.fluxo_1m)), a.fluxo_1m),
        ],
      };
    });
    linhas.push({
      classe: "total",
      celulas: ["Total", texto(dinheiro(t.pl)), t.pl ? "100%" : "-",
        sinal(texto(dinheiroSinal(t.fluxo_12m)), t.fluxo_12m), sinal(fracaoSinal(sobre(t, "12m")), t.fluxo_12m),
        sinal(texto(dinheiroSinal(t.fluxo_1m)), t.fluxo_1m)],
    });
    mount.innerHTML = tabela([
      { rotulo: "Macroclasse" }, { rotulo: "PL", num: true }, { rotulo: "Fatia", num: true },
      { rotulo: "Captação 12m", num: true }, { rotulo: "% do PL", num: true }, { rotulo: "No mês", num: true },
    ], linhas);
    ligarBotoes(mount);
    const j12 = META.janelas["12m"], j1 = META.janelas["1m"];
    document.getElementById("resumo-n1-nota").textContent = comRecorte(
      `Camada do investidor. PL no fechamento de ${FIM}; captação em 12 meses ` +
      `(${periodoBR(j12.de, j12.ate)}) e no mês (${periodoBR(j1.de, j1.ate)}), em % do PL de abertura. ` +
      "Clique numa macroclasse para filtrar a página.", "n1");
  }

  /* --- onde esta o dinheiro -------------------------------------------------------- */
  function desenharOnde() {
    const celulas = CUBO.filter(c => passa(c, "n1"));
    const grupos = somar(celulas, c => c.n1);
    const t = total(celulas);
    const linhas = ordenar("n1", [...grupos.keys()]).filter(n1 => grupos.get(n1).pl > 0).map(n1 => {
      const a = grupos.get(n1);
      return {
        label: n1, value: a.pl, display: dinheiro(a.pl), extra: fracao(t.pl ? a.pl / t.pl : null),
        base: FILTRO.n1 === n1, color: corDaSelecao("n1", n1),
      };
    });
    const maior = linhas.slice().sort((a, b) => b.value - a.value)[0];
    const mount = document.getElementById("chart-n1-pl");
    figura(mount, {
      titulo: maior ? `${maior.label} concentra ${fracao(t.pl ? maior.value / t.pl : null)} do PL disponível`
        : "PL disponível por macroclasse",
      sub: comRecorte(`PL na camada do investidor no fechamento de ${FIM}, por macroclasse, e a fatia de cada uma`, "n1"),
      fonte: FONTE,
    });
    barrasH(mount, linhas, { aria: "PL disponível por macroclasse" });
    desenharTabelaN2();
  }

  function desenharTabelaN2() {
    const mount = document.getElementById("tabela-n2-pl");
    const celulas = CUBO.filter(c => passa(c));
    const t = total(celulas);
    const porN1 = new Map();
    somar(celulas, c => c.n1 + "\u0001" + c.n2).forEach((a, chave) => {
      const [n1, n2] = chave.split("\u0001");
      if (!porN1.has(n1)) porN1.set(n1, []);
      porN1.get(n1).push({ n2, a });
    });
    const linhas = [];
    ordenar("n1", [...porN1.keys()]).forEach(n1 => {
      linhas.push({ grupo: n1 });
      porN1.get(n1).sort((x, y) => y.a.pl - x.a.pl).forEach(({ n2, a }) => {
        linhas.push({
          celulas: [escapeHtml(n2), texto(dinheiro(a.pl)), fracao(t.pl ? a.pl / t.pl : null), fracao(fatiaPfPj(a), 0),
            fracao(fatiaCeo(a), 0), contas(a.cotistas), texto(ticket(a.cotistas ? a.pl / a.cotistas : null)),
            sinal(texto(dinheiroSinal(a.fluxo_12m)), a.fluxo_12m), sinal(fracaoSinal(sobre(a, "12m")), a.fluxo_12m)],
        });
      });
    });
    linhas.push({
      classe: "total",
      celulas: ["Total", texto(dinheiro(t.pl)), t.pl ? "100%" : "-", fracao(fatiaPfPj(t), 0), fracao(fatiaCeo(t), 0),
        contas(t.cotistas), texto(ticket(t.cotistas ? t.pl / t.cotistas : null)),
        sinal(texto(dinheiroSinal(t.fluxo_12m)), t.fluxo_12m), sinal(fracaoSinal(sobre(t, "12m")), t.fluxo_12m)],
    });
    mount.innerHTML = tabela([
      { rotulo: "Peer group" }, { rotulo: "PL", num: true }, { rotulo: "Fatia", num: true },
      { rotulo: "PF e PJ", num: true }, { rotulo: "Conta e ordem", num: true }, { rotulo: "Contas", num: true },
      { rotulo: "PL por conta", num: true }, { rotulo: "Captação 12m", num: true }, { rotulo: "% do PL", num: true },
    ], linhas);
    figura(mount, {
      titulo: "Cada peer group: tamanho, dono do dinheiro e captação",
      sub: comRecorte(`PL do investidor e contas no fechamento de ${FIM}; PF e PJ e conta e ordem em fração do ` +
        `PL bruto com perfil mensal; captação ${META.janelas["12m"].texto}`),
      fonte: FONTE,
    });
  }

  function desenharSegmentos() {
    const segs = (DADOS.segmentos || []).filter(s => ok(s.fatia));
    const mount = document.getElementById("chart-segmentos");
    if (!segs.length) { mount.innerHTML = VAZIO; return; }
    const pfpj = segs.filter(s => s.grupo === "pf_pj").reduce((a, s) => a + s.fatia, 0);
    const ceo = segs.find(s => s.chave === "conta_e_ordem");
    const { grafico, tabela: mountTabela } = graficoComTabela(mount);
    figura(mount, {
      titulo: `PF e PJ têm ${fracao(pfpj)} do PL disponível` +
        (ceo ? `, contando os ${fracao(ceo.fatia)} que chegam por conta e ordem` : ""),
      sub: `Fatia do PL bruto do universo por tipo de cotista no perfil mensal de ${META.rotulo}, e a variação ` +
        `contra 12 meses antes (${BASE_12M})` + SEM_FILTRO,
      fonte: "Fonte: CVM, perfil mensal (composição do PL por tipo de cotista); PL do informe diário.",
    });
    barrasH(grafico, segs.map(s => ({
      label: s.rotulo, value: s.fatia, display: fracao(s.fatia),
      extra: ok(s.variacao_pp) ? pontos(s.variacao_pp) : "", color: s.grupo === "pf_pj" ? "var(--comp-1)" : "var(--comp-2)",
    })), { aria: "Dono do dinheiro por tipo de cotista" });
    mountTabela.innerHTML = tabela([{ rotulo: "Cotista" }, { rotulo: "PL", num: true }, { rotulo: "Fatia", num: true },
      { rotulo: "12 meses antes", num: true }, { rotulo: "Variação", num: true }],
    segs.map(s => ({ celulas: [escapeHtml(s.rotulo), texto(dinheiro(s.pl)), fracao(s.fatia), fracao(s.fatia_12m_antes),
      sinal(pontos(s.variacao_pp), s.variacao_pp)] })));
  }

  function desenharFunil() {
    const degraus = DADOS.funil || [];
    const mount = document.getElementById("chart-funil");
    if (!degraus.length) { mount.innerHTML = VAZIO; return; }
    const soma = degraus.reduce((a, d) => a + d.pl, 0);
    const universo = degraus.find(d => d.motivo === "universo");
    const { grafico, tabela: mountTabela } = graficoComTabela(mount);
    figura(mount, {
      titulo: `Do cadastro de ${dinheiro(soma)}, ${dinheiro(universo ? universo.pl : null)} estão no universo disponível`,
      sub: `PL de cada degrau do funil no fechamento de ${FIM}, com dupla contagem (o master e os fundos que o ` +
        "compram somam os dois); ETF e estruturados com o PL do cadastro" + SEM_FILTRO,
      fonte: "Fonte: CVM, cadastro de fundos e informe diário; motivos na ordem em que o monitor os aplica.",
    });
    barrasH(grafico, degraus.map(d => ({
      label: d.rotulo, value: d.pl, display: dinheiro(d.pl),
      extra: `${inteiro(d.classes)} ${plural(d.classes, "classe", "classes")}`,
      color: d.motivo === "universo" ? "var(--text-primary)" : "var(--comp-2)", base: d.motivo === "universo",
    })), { aria: "Funil do universo disponível" });
    mountTabela.innerHTML = tabela([{ rotulo: "Degrau" }, { rotulo: "Produtos", num: true },
      { rotulo: "Classes", num: true }, { rotulo: "PL", num: true }, { rotulo: "Fatia", num: true }],
    degraus.map(d => ({
      classe: d.motivo === "universo" ? "total" : "",
      celulas: [escapeHtml(d.rotulo), inteiro(d.produtos), inteiro(d.classes), texto(dinheiro(d.pl)), fracao(soma ? d.pl / soma : null)],
    })));
  }

  /* --- para onde vai o dinheiro ------------------------------------------------ */
  let JANELA = "12m";
  function montarSeletorJanela() {
    const mount = document.getElementById("seletor-janela");
    const disponiveis = JANELAS.filter(j => (META.janelas[j].competencias || []).length);
    mount.innerHTML = disponiveis.map(j =>
      `<button type="button" data-janela="${j}" aria-pressed="${j === JANELA}">${escapeHtml(META.janelas[j].rotulo)}</button>`).join("");
    mount.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      JANELA = b.dataset.janela;
      mount.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      seguro("captação por estratégia", desenharFluxos);
    }));
  }

  function desenharFluxos() {
    const jan = META.janelas[JANELA];
    const chave = `fluxo_${JANELA}`;
    const rotuloDoFluxo = a => `${dinheiroSinal(a[chave])} (${fracaoSinal(sobre(a, JANELA))})`;

    const celulas = CUBO.filter(c => passa(c, "n1"));
    const porN1 = somar(celulas, c => c.n1);
    const linhasN1 = ordenar("n1", [...porN1.keys()]).map(n1 => ({
      label: n1, value: porN1.get(n1)[chave], display: rotuloDoFluxo(porN1.get(n1)),
      base: FILTRO.n1 === n1, color: corDaSelecao("n1", n1),
    }));
    const maior = linhasN1.slice().sort((a, b) => b.value - a.value)[0];
    const menor = linhasN1.slice().sort((a, b) => a.value - b.value)[0];
    const mountN1 = document.getElementById("chart-n1-fluxo");
    figura(mountN1, {
      titulo: maior && maior.value > 0
        ? `${maior.label} captou mais em ${jan.rotulo}` + (menor && menor.value < 0 ? `; ${menor.label} perdeu mais` : "")
        : `Captação por macroclasse em ${jan.rotulo}`,
      sub: comRecorte(`Captação líquida do investidor e % do PL de abertura, ${jan.texto}`, "n1"),
      fonte: FONTE,
    });
    barrasDivergentes(mountN1, linhasN1, { aria: `Captação por macroclasse em ${jan.rotulo}`, maxRotulo: 44 });

    const porN2 = somar(CUBO.filter(c => passa(c)), c => c.n1 + "\u0001" + c.n2);
    let itens = [...porN2].map(([k, a]) => { const [n1, n2] = k.split("\u0001"); return { n1, n2, a }; })
      .filter(i => i.a[chave] !== 0 || i.a.pl > 0)
      .sort((x, y) => y.a[chave] - x.a[chave]);
    const todos = FILTRO.n1 === TODOS;
    if (todos && itens.length > 20) itens = [...itens.slice(0, 10), ...itens.slice(-10)];
    const mountN2 = document.getElementById("chart-n2-fluxo");
    const primeiro = itens[0];
    figura(mountN2, {
      titulo: primeiro && primeiro.a[chave] > 0
        ? `${nomeN2(primeiro.n1, primeiro.n2)} foi o peer group que mais captou em ${jan.rotulo}`
        : `Captação por peer group em ${jan.rotulo}`,
      sub: comRecorte(`Captação líquida do investidor e % do PL de abertura, ${jan.texto}; ` +
        (todos ? "os 10 maiores e os 10 menores saldos" : "todos os peer groups da macroclasse")),
      fonte: FONTE,
    });
    barrasDivergentes(mountN2, itens.map(i => ({
      label: nomeN2(i.n1, i.n2), value: i.a[chave], display: rotuloDoFluxo(i.a),
    })), { aria: `Captação por peer group em ${jan.rotulo}`, maxRotulo: 44 });
  }

  function desenharHistorico() {
    const j12 = META.janelas["12m"];
    const meses = new Set(j12.competencias || []);
    const pontosDoMes = (DADOS.historico || []).filter(h => meses.has(h.competencia)).map(h => ({
      rotulo: competenciaBR(h.competencia), curto: mesCurto(h.competencia), valor: h.fluxo_investidor,
    }));
    const positivos = pontosDoMes.filter(p => p.valor > 0).length;
    const mount = document.getElementById("chart-historico");
    const { grafico, tabela: mountTabela } = graficoComTabela(mount);
    figura(mount, {
      titulo: `A captação do investidor foi positiva em ${positivos} de ${pontosDoMes.length} meses`,
      sub: `Captação líquida do investidor por competência, em R$ bilhões, ${periodoBR(j12.de, j12.ate)}` + SEM_FILTRO,
      fonte: FONTE,
    });
    colunasMensais(grafico, pontosDoMes, {
      aria: "Captação do investidor mês a mês", fmt: dinheiroSinal,
      fmtEixo: v => numBR(v / 1e9, 0), fmtCurto: v => (v > 0 ? "+" : "") + numBR(v / 1e9, 1),
    });
    mountTabela.innerHTML = tabela([{ rotulo: "Competência" }, { rotulo: "Captação do investidor", num: true },
      { rotulo: "Rendimento", num: true }, { rotulo: "Resíduo", num: true }, { rotulo: "PL do investidor", num: true }],
    (DADOS.historico || []).filter(h => meses.has(h.competencia)).map(h => ({
      celulas: [competenciaBR(h.competencia), sinal(texto(dinheiroSinal(h.fluxo_investidor)), h.fluxo_investidor),
        texto(dinheiroSinal(h.rendimento_investidor)), texto(dinheiroSinal(h.residuo)), texto(dinheiro(h.pl_investidor))],
    })));
  }

  function desenharCalor() {
    const calor = DADOS.calor;
    const mount = document.getElementById("tabela-calor");
    const meses = (calor && calor.meses) || [];
    const linhas = ((calor && calor.linhas) || []).filter(l => FILTRO.n1 === TODOS || l.n1 === FILTRO.n1);
    // Referencia da rampa: o percentil 90 do tamanho, para um peer group
    // minusculo com +300% num mes nao apagar todos os outros.
    const tamanhos = linhas.flatMap(l => l.valores.filter(ok).map(Math.abs)).sort((a, b) => a - b);
    const max = tamanhos.length ? tamanhos[Math.min(tamanhos.length - 1, Math.floor(tamanhos.length * 0.9))] : 0;
    let n1Atual = null;
    let corpo = "";
    linhas.forEach(l => {
      if (l.n1 !== n1Atual) {
        n1Atual = l.n1;
        corpo += `<tr class="grupo-n1"><th colspan="${meses.length + 1}" scope="colgroup">${escapeHtml(l.n1)}</th></tr>`;
      }
      corpo += `<tr><th scope="row">${escapeHtml(l.n2)}</th>` + l.valores.map((v, i) => {
        if (!ok(v)) return '<td class="cel vazia">-</td>';
        const cor = corDivergente(v, max);
        const estilo = cor ? ` style="background:${cor.fill};color:${cor.ink}"` : "";
        const dica = `${l.n2}, ${competenciaBR(meses[i])}: ${fracaoSinal(v)} do PL do mês anterior`;
        return `<td class="cel"${estilo} title="${escapeHtml(dica)}">${fracaoSinal(v)}</td>`;
      }).join("") + "</tr>";
    });
    mount.innerHTML = linhas.length
      ? '<div class="table-scroll"><table class="calor"><thead><tr><th scope="col">Peer group</th>' +
        meses.map(m => `<th scope="col" class="num">${mesCurto(m)}</th>`).join("") +
        `</tr></thead><tbody>${corpo}</tbody></table></div>`
      : VAZIO;
    const fig = figura(mount, {
      titulo: "Rotação: a captação de cada peer group, mês a mês",
      sub: `Captação líquida do investidor em % do PL do mês anterior, ${periodoBR(META.janelas["12m"].de, META.janelas["12m"].ate)}` +
        (FILTRO.n1 !== TODOS ? ` · macroclasse: ${FILTRO.n1}` : "") + " · só o filtro de macroclasse vale aqui",
      fonte: FONTE,
    });
    // Doze meses nao cabem na largura de figura: esta usa a pagina inteira.
    if (fig) fig.classList.add("fig-larga");
    // Tela estreita: a tabela abre no mes mais recente, com o peer group preso
    // a esquerda - quem abre quer ver o ultimo mes, nao o de um ano atras.
    const rolo = mount.querySelector(".table-scroll");
    if (rolo) quandoVisivel(rolo, () => { rolo.scrollLeft = rolo.scrollWidth; });
  }

  function desenharFamilias() {
    const f = DADOS.familias || {};
    const daMacro = i => FILTRO.n1 === TODOS || i.n1 === FILTRO.n1;
    const captam = (f.que_mais_captam || []).filter(daMacro);
    const perdem = (f.que_mais_perdem || []).filter(daMacro);
    const mount = document.getElementById("tabela-familias");
    const linha = i => ({
      celulas: [`${escapeHtml(i.nome)}<small>${escapeHtml(i.gestora || "")}</small>`, escapeHtml(nomeN2(i.n1, i.n2)),
        inteiro(i.veiculos), texto(dinheiro(i.pl_investidor_fim)),
        sinal(texto(dinheiroSinal(i.fluxo_investidor)), i.fluxo_investidor), sinal(fracaoSinal(i.sobre_pl), i.fluxo_investidor)],
    });
    const linhas = [];
    if (captam.length) { linhas.push({ grupo: "Mais captaram" }); captam.forEach(i => linhas.push(linha(i))); }
    if (perdem.length) { linhas.push({ grupo: "Mais perderam" }); perdem.forEach(i => linhas.push(linha(i))); }
    mount.innerHTML = linhas.length ? tabela([{ rotulo: "Estratégia e gestora" }, { rotulo: "Peer group" },
      { rotulo: "Veículos", num: true }, { rotulo: "PL", num: true }, { rotulo: "Captação 12m", num: true },
      { rotulo: "% do PL", num: true }], linhas) : VAZIO;
    figura(mount, {
      titulo: captam.length ? `${captam[0].nome} foi a estratégia que mais captou em 12 meses` : "Estratégias que mais captam e mais perdem",
      sub: `Família: o master com os feeders, espelhos e FoFs que o compram. Captação líquida do investidor, ` +
        `${META.janelas["12m"].texto}; PL do investidor em ${FIM}; ${inteiro(f.familias_no_universo || 0)} famílias no universo` +
        (FILTRO.n1 !== TODOS ? ` · macroclasse: ${FILTRO.n1}` : "") + " · só o filtro de macroclasse vale aqui",
      fonte: FONTE,
    });
  }

  // Bilhoes com sinal e uma casa, para tabela com a unidade no cabecalho.
  // Zero arredondado sai sem sinal: "-0,0" leria como perda.
  const bi = v => (!ok(v) ? "-" : Math.abs(v) < 5e7 ? "0,0" : (v > 0 ? "+" : "") + numBR(v / 1e9, 1));
  // "3,1×" - giro e entrada, em vezes o PL medio.
  const vezes = v => (ok(v) ? numBR(v, 1) + "×" : "-");

  function desenharGiro() {
    const g = (DADOS.dinamica || {}).giro || {};
    const mount = document.getElementById("tabela-giro");
    const total = g.total || {};
    // Macroclasse com menos de 0,1% do PL medio sai: dez fundos minusculos
    // giram qualquer coisa e nao dizem nada do mercado.
    const itens = (g.n1 || []).filter(i => i.pl_medio > 0.001 * (total.pl_medio || 0));
    if (!itens.length) { mount.innerHTML = VAZIO; return; }
    const linha = (rotulo, i, classe) => ({
      classe,
      celulas: [rotulo, texto(dinheiro(i.captacao)), texto(dinheiro(i.resgates)),
        sinal(texto(dinheiroSinal(i.captacao_liquida)), i.captacao_liquida), texto(dinheiro(i.pl_medio)),
        vezes(i.entrada), `<b>${vezes(i.giro)}</b>`],
    });
    const linhas = itens.map(i => linha(escapeHtml(i.n1), i, FILTRO.n1 === i.n1 ? "selecionada" : ""));
    if (g.total) linhas.push(linha("Universo", g.total, "total"));
    mount.innerHTML = tabela([{ rotulo: "Macroclasse" }, { rotulo: "Captação bruta", num: true },
      { rotulo: "Resgates", num: true }, { rotulo: "Líquido", num: true }, { rotulo: "PL médio", num: true },
      { rotulo: "Entrada", num: true }, { rotulo: "Giro", num: true }], linhas);
    const maior = itens.slice().sort((a, b) => (b.giro || 0) - (a.giro || 0))[0];
    figura(mount, {
      titulo: maior ? `${maior.n1} gira ${numBR(maior.giro, 1)} vezes o PL médio em 12 meses` : "Giro por macroclasse",
      sub: `Captação bruta e resgates declarados, com o dinheiro que um fundo aplica em outro, ${META.janelas["12m"].texto}; ` +
        "PL médio do fim de cada mês; entrada = captação bruta ÷ PL médio, giro = resgates ÷ PL médio" + SEM_FILTRO,
      fonte: FONTE,
    });
  }

  function desenharMacro() {
    const m = DADOS.macro || {};
    const mount = document.getElementById("tabela-macro");
    if (!m.available) {
      mount.innerHTML = `<p class="empty">${escapeHtml(m.reason || "Sem o SGS do Banco Central.")}</p>`;
      return;
    }
    const meses = m.meses || [];
    // So as macroclasses que moveram ao menos R$ 100 mi num mes.
    const n1s = ordenar("n1", [...new Set(meses.flatMap(x => Object.keys(x.por_n1 || {})))])
      .filter(n1 => meses.some(x => Math.abs((x.por_n1 || {})[n1] || 0) >= 1e8));
    const taxa = v => (ok(v) ? numBR(v, 2) + "%" : "-");
    const linhas = meses.map(x => ({
      celulas: [competenciaBR(x.competencia), sinal(`<b>${bi(x.fluxo_investidor)}</b>`, x.fluxo_investidor),
        ...n1s.map(n1 => { const v = (x.por_n1 || {})[n1]; return sinal(bi(v), v); }),
        fracao(x.cdi, 2), fracaoSinal(x.ipca, 2), taxa(x.selic), ok(x.dolar) ? numBR(x.dolar, 2) : "-"],
    }));
    const j12 = (m.janelas || {})["12m"] || {};
    const soma = f => meses.reduce((a, x) => a + (f(x) || 0), 0);
    const total = soma(x => x.fluxo_investidor);
    linhas.push({
      classe: "total",
      celulas: ["12 meses", sinal(`<b>${bi(total)}</b>`, total),
        ...n1s.map(n1 => { const v = soma(x => (x.por_n1 || {})[n1]); return sinal(bi(v), v); }),
        fracao(j12.cdi, 1), fracaoSinal(j12.ipca, 1), taxa(j12.selic_fim), ok(j12.dolar_fim) ? numBR(j12.dolar_fim, 2) : "-"],
    });
    mount.innerHTML = tabela([{ rotulo: "Competência" }, { rotulo: "Captação (R$ bi)", num: true },
      ...n1s.map(n1 => ({ rotulo: n1, num: true })), { rotulo: "CDI", num: true }, { rotulo: "IPCA", num: true },
      { rotulo: "Selic (fim)", num: true }, { rotulo: "Dólar (fim)", num: true }], linhas);
    const real = ok(j12.cdi) && ok(j12.ipca) ? (1 + j12.cdi) / (1 + j12.ipca) - 1 : null;
    const fig = figura(mount, {
      titulo: ok(real) ? `CDI de ${fracao(j12.cdi)} contra IPCA de ${fracao(j12.ipca)} em 12 meses: juro real de ${fracao(real)}`
        : "O macro ao lado da captação",
      sub: `Captação líquida do investidor por macroclasse, em R$ bilhões, por competência, ` +
        `${periodoBR(META.janelas["12m"].de, META.janelas["12m"].ate)}; CDI e IPCA do mês; Selic (meta, % ao ano) e ` +
        "dólar (PTAX venda, R$) no último dia útil; na linha de 12 meses, o CDI e o IPCA acumulados" + SEM_FILTRO,
      fonte: "Fonte: CVM (informe diário e perfil mensal) para a captação; Banco Central, SGS (séries 12, 432, 433 e 1) para o macro.",
    });
    if (fig) fig.classList.add("fig-larga");
  }

  function desenharParaOnde() {
    seguro("captação por estratégia", desenharFluxos);
    seguro("rotação", desenharCalor);
    seguro("famílias", desenharFamilias);
    seguro("giro", desenharGiro);
  }

  /* --- quem ganha -------------------------------------------------------------- */
  function desenharQuemGanha() {
    const celulas = CUBO.filter(c => passa(c, "tipo_gestora"));
    const grupos = somar(celulas, c => c.tipo_gestora);
    const t = total(celulas);
    const linhas = [...grupos].filter(([, a]) => a.pl > 0).map(([tipo, a]) => {
      const agora = t.pl ? a.pl / t.pl : null;
      const antes = t.pl_abertura_12m ? a.pl_abertura_12m / t.pl_abertura_12m : null;
      const variacao = ok(agora) && ok(antes) ? (agora - antes) * 100 : null;
      return {
        label: ROTULO.tipo_gestora(tipo), value: agora || 0, display: fracao(agora),
        extra: ok(variacao) ? pontos(variacao) : "",
        base: FILTRO.tipo_gestora === tipo, color: corDaSelecao("tipo_gestora", tipo),
      };
    }).sort((a, b) => b.value - a.value);
    const mount = document.getElementById("chart-tipos");
    figura(mount, {
      titulo: linhas.length ? `Gestoras do tipo ${linhas[0].label.toLowerCase()} têm ${linhas[0].display} do PL disponível`
        : "Fatia do PL por tipo de gestora",
      sub: comRecorte(`Fatia do PL do investidor por tipo de gestora da estratégia (a da raiz) no fechamento de ${FIM}, ` +
        `e a variação em pontos percentuais contra ${BASE_12M}`, "tipo_gestora"),
      fonte: FONTE + " Tipo de gestora pela curadoria de grupos econômicos.",
    });
    barrasH(mount, linhas, { aria: "Fatia do PL por tipo de gestora" });
    seguro("gestoras", desenharGestoras);
    seguro("concentração", desenharConcentracao);
  }

  function desenharConcentracao() {
    const c = (DADOS.dinamica || {}).concentracao || {};
    const mount = document.getElementById("tabela-concentracao");
    const universo = c.universo || {};
    const itens = (c.n2 || []).filter(i => FILTRO.n1 === TODOS || i.n1 === FILTRO.n1);
    if (!itens.length) { mount.innerHTML = VAZIO; return; }
    const linhas = [];
    let n1Atual = null;
    const posN1 = v => { const i = ORDEM.n1.indexOf(v); return i < 0 ? ORDEM.n1.length : i; };
    itens.slice().sort((a, b) => posN1(a.n1) - posN1(b.n1) || b.pl_investidor - a.pl_investidor)
      .forEach(i => {
        if (i.n1 !== n1Atual) { n1Atual = i.n1; linhas.push({ grupo: i.n1 }); }
        linhas.push({
          celulas: [escapeHtml(i.n2), texto(dinheiro(i.pl_investidor)), inteiro(i.gestoras),
            `<b>${ok(i.hhi) ? inteiro(Math.round(i.hhi)) : "-"}</b>`,
            ok(i.variacao_hhi) ? (i.variacao_hhi > 0 ? "+" : "") + inteiro(Math.round(i.variacao_hhi)) : "-",
            fracao(i.top5, 0), `${escapeHtml(i.lider || "-")}<small>${fracao(i.fatia_do_lider)}</small>`],
        });
      });
    if (universo.hhi) {
      linhas.push({ classe: "total", celulas: ["Universo", texto(dinheiro(universo.pl_investidor)), inteiro(universo.gestoras),
        `<b>${inteiro(Math.round(universo.hhi))}</b>`,
        ok(universo.variacao_hhi) ? (universo.variacao_hhi > 0 ? "+" : "") + inteiro(Math.round(universo.variacao_hhi)) : "-",
        fracao(universo.top5, 0), `${escapeHtml(universo.lider || "-")}<small>${fracao(universo.fatia_do_lider)}</small>`] });
    }
    mount.innerHTML = tabela([{ rotulo: "Peer group" }, { rotulo: "PL", num: true }, { rotulo: "Gestoras", num: true },
      { rotulo: "HHI", num: true }, { rotulo: "Em 12 meses", num: true }, { rotulo: "5 maiores", num: true },
      { rotulo: "Líder" }], linhas);
    // O titulo escolhe entre os peer groups com ao menos 1% do PL: com duas
    // gestoras, um HHI de 5.000 nao diz nada do mercado.
    const grandes = itens.filter(i => ok(i.hhi) && i.pl_investidor >= 0.01 * (universo.pl_investidor || 0));
    const mais = grandes.slice().sort((a, b) => b.hhi - a.hhi)[0];
    figura(mount, {
      titulo: mais ? `${nomeN2(mais.n1, mais.n2)} é o peer group grande mais concentrado: ${mais.lider} tem ${fracao(mais.fatia_do_lider, 0)}`
        : "Concentração por peer group",
      sub: `HHI (0 a 10.000) e fatia das 5 maiores, pelo PL do investidor de cada grupo gestor da estratégia em ${FIM}; ` +
        `variação do HHI contra ${BASE_12M} (negativa = mercado mais disputado)` +
        (FILTRO.n1 !== TODOS ? ` · macroclasse: ${FILTRO.n1}` : "") + " · só o filtro de macroclasse vale aqui",
      fonte: FONTE,
    });
  }

  function desenharEntrantes() {
    const g = (DADOS.dinamica || {}).gestoras || {};
    const entraram = g.entraram || {}, sairam = g.sairam || {};
    const mount = document.getElementById("tabela-entrantes");
    const resumo = tabela([{ rotulo: "" }, { rotulo: "Grupos", num: true }, { rotulo: "PL do investidor", num: true },
      { rotulo: "Captação 12m", num: true }], [
      { celulas: [`No universo em ${escapeHtml(BASE_12M)}`, inteiro(g.grupos_na_base || 0), "", ""] },
      { celulas: ["Entraram", inteiro(entraram.grupos || 0), texto(dinheiro(entraram.pl_investidor)),
        sinal(texto(dinheiroSinal(entraram.fluxo_investidor)), entraram.fluxo_investidor)] },
      { celulas: ["Saíram", inteiro(sairam.grupos || 0), texto(dinheiro(sairam.pl_investidor)), ""] },
      { classe: "total", celulas: [`No universo em ${escapeHtml(FIM)}`, inteiro(g.grupos_no_fim || 0), "", ""] },
    ]);
    const lista = [];
    if ((entraram.lista || []).length) {
      lista.push({ grupo: "Maiores entradas" });
      entraram.lista.forEach(i => lista.push({ celulas: [`${escapeHtml(i.grupo)}<small>${escapeHtml(ROTULO.tipo_gestora(i.tipo))}</small>`,
        `desde ${competenciaBR(i.primeira)}`, escapeHtml(i.n2 || "-"), texto(dinheiroFino(i.pl_investidor)),
        sinal(texto(dinheiroFinoSinal(i.fluxo_investidor)), i.fluxo_investidor)] }));
    }
    if ((sairam.lista || []).length) {
      lista.push({ grupo: "Saídas" });
      sairam.lista.forEach(i => lista.push({ celulas: [`${escapeHtml(i.grupo)}<small>${escapeHtml(ROTULO.tipo_gestora(i.tipo))}</small>`,
        `até ${competenciaBR(i.ultima)}`, "", i.pl_investidor > 0 ? texto(dinheiroFino(i.pl_investidor)) : "-", ""] }));
    }
    mount.innerHTML = resumo + (lista.length ? tabela([{ rotulo: "Grupo gestor" }, { rotulo: "No universo" },
      { rotulo: "Peer group principal" }, { rotulo: "PL do investidor", num: true }, { rotulo: "Captação 12m", num: true }], lista) : "");
    figura(mount, {
      titulo: `${inteiro(entraram.grupos || 0)} grupos gestores entraram no universo em 12 meses e ${inteiro(sairam.grupos || 0)} saíram`,
      sub: `Entrou: sem produto no universo em ${BASE_12M} e com produto dentro da janela (${META.janelas["12m"].texto}). ` +
        `Saiu: tinha produto na base ou na janela e não tem em ${FIM}. PL do investidor no fim (entradas) ou no último mês ` +
        "presente (saídas); os 10 maiores de cada lado" + SEM_FILTRO,
      fonte: FONTE,
    });
  }

  function desenharGestoras() {
    const g = DADOS.gestoras || {};
    const grupos = (g.grupos || []).filter(i => FILTRO.tipo_gestora === TODOS || i.tipo === FILTRO.tipo_gestora);
    const mount = document.getElementById("tabela-gestoras");
    mount.innerHTML = grupos.length ? tabela([{ rotulo: "Grupo gestor" }, { rotulo: "Tipo" }, { rotulo: "PL", num: true },
      { rotulo: "Fatia", num: true }, { rotulo: "Em 12 meses", num: true }, { rotulo: "Captação 12m", num: true },
      { rotulo: "% do PL", num: true }],
    grupos.map(i => ({
      celulas: [escapeHtml(i.grupo), escapeHtml(ROTULO.tipo_gestora(i.tipo)), texto(dinheiro(i.pl_investidor)),
        fracao(i.share), sinal(pontos(i.variacao_pp), i.variacao_pp),
        sinal(texto(dinheiroSinal(i.fluxo_investidor_12m)), i.fluxo_investidor_12m),
        sinal(fracaoSinal(i.sobre_pl_12m), i.fluxo_investidor_12m)],
    }))) : VAZIO;
    const soma = grupos.slice(0, 10).reduce((a, i) => a + (i.share || 0), 0);
    figura(mount, {
      titulo: grupos.length ? `Os ${Math.min(10, grupos.length)} maiores grupos gestores têm ${fracao(soma)} do PL disponível`
        : "Grupos gestores",
      sub: `Grupo econômico da gestora da estratégia. Fatia do PL do investidor do universo inteiro em ${FIM} e ` +
        `variação contra ${BASE_12M}; captação ${META.janelas["12m"].texto}. ${inteiro(g.grupos_no_universo || 0)} ` +
        `grupos no universo; a curadoria cobre ${fracao(g.cobertura_curada)} do PL; os 10 grupos que mais captaram ` +
        `ficaram com ${fracao(g.concentracao_da_captacao)} da captação positiva` +
        (FILTRO.tipo_gestora !== TODOS ? ` · tipo: ${ROTULO.tipo_gestora(FILTRO.tipo_gestora)}` : "") +
        " · só o filtro de tipo de gestora vale aqui",
      fonte: FONTE,
    });
  }

  /* --- por onde chega -------------------------------------------------------- */
  function desenharCanal() {
    const celulas = CUBO.filter(c => passa(c, "n1"));
    const grupos = somar(celulas, c => c.n1);
    const t = total(celulas);
    const linha = (rotulo, a, extra) => {
      const v = variacaoCeo(a);
      return { label: rotulo, value: fatiaCeo(a) || 0, display: fracao(fatiaCeo(a)), extra: ok(v) ? pontos(v) : "", ...extra };
    };
    // Macroclasse com menos de 0,1% do PL sai do grafico: R$ 60 mi com 100%
    // por conta e ordem viraria a maior barra e nao diz nada do canal.
    const relevante = n1 => grupos.get(n1).pl_perfil >= 0.001 * t.pl_perfil;
    const linhas = [linha(filtrado("n1") ? "Recorte inteiro" : "Universo inteiro", t, { base: true, color: "var(--text-primary)" }),
      ...ordenar("n1", [...grupos.keys()]).filter(n1 => grupos.get(n1).pl_perfil > 0 && (relevante(n1) || FILTRO.n1 === n1))
        .map(n1 => linha(n1, grupos.get(n1), { base: FILTRO.n1 === n1, color: corDaSelecao("n1", n1) }))];
    const mount = document.getElementById("chart-plataforma");
    figura(mount, {
      titulo: `Conta e ordem responde por ${fracao(fatiaCeo(t))} do PL disponível`,
      sub: comRecorte(`Fatia do PL bruto com perfil mensal aplicada por distribuidores em nome dos clientes, no ` +
        `fechamento de ${FIM}, e a variação em pontos percentuais contra ${BASE_12M}; macroclasses com menos de ` +
        "0,1% do PL ficam de fora", "n1"),
      fonte: "Fonte: CVM, perfil mensal (cotistas por conta e ordem) e informe diário.",
    });
    barrasH(mount, linhas, { aria: "Fatia do PL via conta e ordem por macroclasse" });
    seguro("papel na estrutura", desenharPapeis);
    seguro("casas dedicadas", desenharCasas);
  }

  function desenharPapeis() {
    const celulas = CUBO.filter(c => passa(c, "papel"));
    const grupos = somar(celulas, c => c.papel);
    const t = total(celulas);
    const papeis = ordenar("papel", [...grupos.keys()]);
    const mount = document.getElementById("tabela-papeis");
    const linhas = papeis.map(p => {
      const a = grupos.get(p);
      return {
        classe: FILTRO.papel === p ? "selecionada" : "",
        celulas: [botaoFiltro("papel", p, ROTULO.papel(p)), texto(dinheiro(a.pl)), fracao(t.pl ? a.pl / t.pl : null),
          inteiro(a.produtos), sinal(texto(dinheiroSinal(a.fluxo_12m)), a.fluxo_12m),
          sinal(fracaoSinal(sobre(a, "12m")), a.fluxo_12m), fracao(fatiaCeo(a), 0)],
      };
    });
    linhas.push({ classe: "total", celulas: ["Total", texto(dinheiro(t.pl)), t.pl ? "100%" : "-", inteiro(t.produtos),
      sinal(texto(dinheiroSinal(t.fluxo_12m)), t.fluxo_12m), sinal(fracaoSinal(sobre(t, "12m")), t.fluxo_12m),
      fracao(fatiaCeo(t), 0)] });
    mount.innerHTML = tabela([{ rotulo: "Papel" }, { rotulo: "PL", num: true }, { rotulo: "Fatia", num: true },
      { rotulo: "Produtos", num: true }, { rotulo: "Captação 12m", num: true }, { rotulo: "% do PL", num: true },
      { rotulo: "Conta e ordem", num: true }], linhas);
    ligarBotoes(mount);
    const veiculos = ["feeder", "espelho", "feeder_exterior"].reduce((a, p) => a + (grupos.has(p) ? grupos.get(p).pl : 0), 0);
    figura(mount, {
      titulo: `Feeders e espelhos somam ${fracao(t.pl ? veiculos / t.pl : null)} do PL disponível`,
      sub: comRecorte(`Papel da classe na estrutura master/feeder/espelho, pela carteira; PL do investidor em ${FIM} ` +
        `e captação ${META.janelas["12m"].texto}. Clique num papel para filtrar a página.`, "papel"),
      fonte: "Fonte: CVM, carteira (CDA) e perfil mensal; regras de papel do monitor.",
    });
  }

  function desenharCasas() {
    const e = DADOS.estrutura || {};
    const casas = (e.casas_dedicadas || []).filter(c => c.casa).slice(0, 15);
    const mount = document.getElementById("chart-casas");
    figura(mount, {
      titulo: casas.length ? `${casas[0].casa} lidera os veículos dedicados, com ${dinheiro(casas[0].pl_investidor)}`
        : "Casas dedicadas",
      sub: `PL do investidor em veículos feitos para cada distribuidor (espelho de plataforma ou banco, ou marca ` +
        `do distribuidor no nome) no fechamento de ${FIM}, e captação ${META.janelas["12m"].texto}` + SEM_FILTRO,
      fonte: "Fonte: CVM, carteira (CDA) e cadastro; marcas e grupos pela curadoria do monitor.",
    });
    barrasH(mount, casas.map(c => ({
      label: c.casa, value: c.pl_investidor, display: dinheiro(c.pl_investidor),
      extra: ok(c.fluxo_investidor_12m) ? `${dinheiroSinal(c.fluxo_investidor_12m)} em 12m` : "",
    })), { aria: "PL em veículos dedicados por casa" });

    const parcerias = (e.parcerias || []).slice(0, 40);
    const novos = DADOS.novos_dedicados || [];
    const mountP = document.getElementById("tabela-parcerias");
    mountP.innerHTML = (parcerias.length ? tabela([{ rotulo: "Casa dedicada" }, { rotulo: "Gestora da estratégia" },
      { rotulo: "Produtos", num: true }, { rotulo: "PL do investidor", num: true }],
    parcerias.map(p => ({ celulas: [escapeHtml(p.casa), escapeHtml(p.gestora), inteiro(p.produtos),
      texto(dinheiro(p.pl_investidor))] }))) : VAZIO) +
      (novos.length ? "<h4 class=\"fig-title\" style=\"margin-top:22px\">Veículos dedicados novos no mês</h4>" +
        tabela([{ rotulo: "Veículo" }, { rotulo: "Papel" }, { rotulo: "Casa" }, { rotulo: "Gestora da estratégia" },
          { rotulo: "Peer group" }],
        novos.map(n => ({ celulas: [`${escapeHtml(n.nome)}<small>${escapeHtml(cnpjBR(n.cnpj))}</small>`,
          escapeHtml(ROTULO.papel(n.papel)), escapeHtml(n.casa || "-"), escapeHtml(n.gestora_da_estrategia || ""),
          escapeHtml(n.n2 || "-")] }))) : "");
    figura(mountP, {
      titulo: "Parcerias de distribuição: quem monta veículo sobre a estratégia de quem",
      sub: `Casa dedicada × grupo gestor da estratégia, os 40 maiores pares por PL do investidor em ${FIM}` + SEM_FILTRO,
      fonte: "Fonte: CVM, carteira (CDA) e cadastro; curadoria do monitor.",
    });
  }

  function desenharSegmentosFluxo() {
    const s = (DADOS.dinamica || {}).captacao_por_segmento || {};
    const t = s.total;
    const mount = document.getElementById("chart-segmentos-fluxo");
    if (!t || !(t.segmentos || []).length) { mount.innerHTML = VAZIO; return; }
    const { grafico, tabela: mountTabela } = graficoComTabela(mount);
    const rotulo = x => x.rotulo + (x.no_ruido ? " (dentro do ruído)" : "");
    const linhas = [...t.segmentos.map(x => ({
      label: rotulo(x), value: x.captacao, display: dinheiroSinal(x.captacao),
      color: x.no_ruido ? "var(--comp-2)" : "var(--comp-1)",
    })), { label: "Resíduo (não explicado)", value: t.residuo, display: dinheiroSinal(t.residuo), color: "var(--comp-2)" }];
    const medidos = t.segmentos.filter(x => !x.no_ruido);
    const entrou = medidos.slice().sort((a, b) => b.captacao - a.captacao)[0];
    const saiu = medidos.slice().sort((a, b) => a.captacao - b.captacao)[0];
    figura(mount, {
      titulo: entrou && saiu && entrou.captacao > 0 && saiu.captacao < 0
        ? `${entrou.rotulo} trouxe ${dinheiroSinal(entrou.captacao)} em 12 meses; ${saiu.rotulo} tirou ${dinheiro(Math.abs(saiu.captacao))}`
        : "Captação do investidor por tipo de cotista",
      sub: `Captação implícita de cada tipo de cotista em ${META.janelas["12m"].texto}: a variação da fatia dele no perfil ` +
        `mensal, descontado o rendimento. Somada ao resíduo, dá a captação do investidor (${dinheiroSinal(t.fluxo_investidor)}); ` +
        `os fundos que compram fundos, fora dela, somam ${dinheiroSinal(t.fundos)}. Ruído do arredondamento do perfil: ` +
        `${dinheiro(t.ruido)}` + SEM_FILTRO,
      fonte: "Fonte: CVM, perfil mensal (composição do PL por tipo de cotista) e informe diário; estimativa do monitor.",
    });
    barrasDivergentes(grafico, linhas, { aria: "Captação do investidor por tipo de cotista em 12 meses", maxRotulo: 44 });
    const n1s = (s.n1 || []).filter(i => Math.abs(i.fluxo_investidor) >= 1e8 || i.segmentos.some(x => Math.abs(x.captacao) >= 1e8));
    const chaves = t.segmentos.map(x => x.chave);
    const nomes = Object.fromEntries(t.segmentos.map(x => [x.chave, x.rotulo]));
    const celula = (lista, chave) => {
      const x = (lista || []).find(y => y.chave === chave);
      if (!x) return "-";
      return x.no_ruido ? '<span class="fraco">≈0</span>' : sinal(bi(x.captacao), x.captacao);
    };
    const linhaDe = (rotuloLinha, i, classe) => ({
      classe, celulas: [rotuloLinha, ...chaves.map(c => celula(i.segmentos, c)), sinal(bi(i.residuo), i.residuo),
        sinal(`<b>${bi(i.fluxo_investidor)}</b>`, i.fluxo_investidor)],
    });
    mountTabela.innerHTML = tabela([{ rotulo: "Macroclasse (R$ bi)" }, ...chaves.map(c => ({ rotulo: nomes[c], num: true })),
      { rotulo: "Resíduo", num: true }, { rotulo: "Captação do investidor", num: true }],
    [...n1s.map(i => linhaDe(escapeHtml(i.n1), i, "")), linhaDe("Universo", t, "total")]);
  }

  /* --- desempenho ------------------------------------------------------------ */
  const CDI = META.cdi || {};
  // "105% do CDI" - o retorno sobre o CDI da mesma janela.
  const doCdi = (v, cdi) => (ok(v) && ok(cdi) && cdi > 0 ? `${numBR((v / cdi) * 100, 0)}% do CDI` : "");

  function desenharDesempenho() {
    const itens = (DADOS.rentabilidade || []).filter(i => (FILTRO.n1 === TODOS || i.n1 === FILTRO.n1) && ok(i.mediana_12m));
    const porN1 = new Map();
    itens.forEach(i => { if (!porN1.has(i.n1)) porN1.set(i.n1, []); porN1.get(i.n1).push(i); });
    const n1s = ordenar("n1", [...porN1.keys()]);
    const j12 = META.janelas["12m"], j3 = META.janelas["3m"];
    const sub = `Retorno da cota em ${j12.texto}; uma vez por estratégia-raiz, cota ex-provento, ` +
      "sem impostos; peer group com amostra pequena só na tabela" +
      (ok(CDI["12m"]) ? `; a linha vertical é o CDI da mesma janela (${fracao(CDI["12m"])})` : "") +
      (FILTRO.n1 !== TODOS ? ` · macroclasse: ${FILTRO.n1}` : "") + " · só o filtro de macroclasse vale aqui";

    // Uma faixa por macroclasse, cada uma no proprio eixo: acoes vao de -30% a
    // +40% e esmagariam a renda fixa, que fica entre 10% e 13%. Amostra pequena
    // fica so na tabela: tres fundos de "sem rotulo" esticavam o eixo do
    // multimercado ate -60%.
    const mount = document.getElementById("chart-rentabilidade");
    const comMediana = itens.filter(i => !i.amostra_pequena && ok(i.acima_do_cdi_12m));
    const batem = comMediana.filter(i => i.mediana_12m > CDI["12m"]).length;
    figura(mount, {
      titulo: ok(CDI["12m"]) && comMediana.length
        ? `${batem} de ${comMediana.length} peer groups tiveram a mediana acima do CDI de ${fracao(CDI["12m"])} em 12 meses`
        : "Retorno de 12 meses por peer group: mediana e quartis",
      sub, fonte: "Fonte: CVM, informe diário (cota); CDI do Banco Central (SGS, série 12); peer group pela classificação do monitor.",
    });
    const noGrafico = n1s.filter(n1 => porN1.get(n1).some(i => !i.amostra_pequena));
    mount.innerHTML = noGrafico.length ? "" : VAZIO;
    noGrafico.forEach(n1 => {
      const faixa = document.createElement("div");
      faixa.className = "faceta";
      faixa.innerHTML = `<p class="faceta-titulo">${escapeHtml(n1)}</p><div class="chart-scroll"></div>`;
      mount.appendChild(faixa);
      const lista = porN1.get(n1).filter(i => !i.amostra_pequena).sort((a, b) => b.mediana_12m - a.mediana_12m);
      // Faixa estreita (a renda fixa, entre 12% e 15%) ganha uma casa decimal
      // no eixo; sem ela os passos de 0,5 p.p. saiam "13%, 13%, 14%".
      const valores = lista.flatMap(i => [i.q1_12m, i.mediana_12m, i.q3_12m]).filter(ok);
      const casas = valores.length && (Math.max(...valores) - Math.min(...valores)) * 100 < 6 ? 1 : 0;
      dumbbell(faixa.querySelector(".chart-scroll"), lista.map(i => ({
        label: i.n2, valores: { q1: i.q1_12m, mediana: i.mediana_12m, q3: i.q3_12m },
      })), [
        { chave: "q1", rotulo: "Quartil inferior", forma: "traco", cor: "var(--comp-2)" },
        { chave: "mediana", rotulo: "Mediana", cor: "var(--comp-1)" },
        { chave: "q3", rotulo: "Quartil superior", forma: "traco", cor: "var(--comp-2)" },
      ], {
        fmt: v => fracao(v), fmtEixo: v => numBR(v * 100, casas) + "%", nota: l => fracao(l.valores.mediana),
        aria: `Retorno de 12 meses dos peer groups de ${n1}`,
        ...(ok(CDI["12m"]) ? { ref: CDI["12m"], refRotulo: "CDI em 12 meses", fmtRef: v => fracao(v) } : {}),
      });
    });

    const linhas = [];
    n1s.forEach(n1 => {
      linhas.push({ grupo: n1 });
      porN1.get(n1).slice().sort((a, b) => b.mediana_12m - a.mediana_12m).forEach(i => linhas.push({
        celulas: [escapeHtml(i.n2) + (i.amostra_pequena ? " *" : ""), inteiro(i.estrategias), fracao(i.q1_12m),
          `<b>${fracao(i.mediana_12m)}</b>`, fracao(i.q3_12m),
          ok(i.sobre_cdi_12m) ? `${numBR(i.sobre_cdi_12m * 100, 0)}%` : "-", fracao(i.acima_do_cdi_12m, 0),
          fracao(i.mediana_3m), ok(i.sobre_cdi_3m) ? `${numBR(i.sobre_cdi_3m * 100, 0)}%` : "-"],
      }));
    });
    const mountT = document.getElementById("tabela-rentabilidade");
    mountT.innerHTML = linhas.length ? tabela([{ rotulo: "Peer group" }, { rotulo: "Estratégias", num: true },
      { rotulo: "Quartil inferior", num: true }, { rotulo: "Mediana 12m", num: true },
      { rotulo: "Quartil superior", num: true }, { rotulo: "Mediana em % do CDI", num: true },
      { rotulo: "Estratégias acima do CDI", num: true }, { rotulo: "Mediana 3m", num: true },
      { rotulo: "3m em % do CDI", num: true }], linhas) : VAZIO;
    figura(mountT, {
      titulo: "Os números do gráfico, contra o CDI e com a mediana de 3 meses",
      sub: `12 meses: ${j12.texto}, CDI de ${fracao(CDI["12m"])}. 3 meses: ${j3.texto}, CDI de ${fracao(CDI["3m"])}. ` +
        "Crédito incentivado é isento de IR: contra o CDI bruto, sai abaixo por construção. " +
        "* amostra pequena: poucas estratégias no peer group",
      fonte: "Fonte: CVM, informe diário (cota); Banco Central, SGS (CDI, série 12).",
    });
  }

  function desenharFluxoRetorno() {
    const q = (DADOS.dinamica || {}).fluxo_x_retorno || [];
    const mount = document.getElementById("chart-fluxo-retorno");
    const quartis = q.filter(x => x.quartil && ok(x.sobre_pl));
    if (!quartis.length) { mount.innerHTML = VAZIO; return; }
    const { grafico, tabela: mountTabela } = graficoComTabela(mount);
    const melhor = quartis.find(x => x.quartil === 1), pior = quartis.find(x => x.quartil === 4);
    const sem = q.find(x => !x.quartil);
    figura(mount, {
      titulo: melhor && pior
        ? `Quem rendeu mais no peer group captou ${fracaoSinal(melhor.sobre_pl)} do PL em 12 meses; quem rendeu menos, ${fracaoSinal(pior.sobre_pl)}`
        : "Captação por quartil de retorno",
      sub: `Captação do investidor em ${META.janelas["12m"].texto}, em % do PL de abertura, por quartil do retorno de 12 meses ` +
        "da estratégia-raiz no próprio peer group. Mesma janela nos dois eixos: mostra associação, não causa" +
        (sem ? `. Sem retorno de 12 meses (lançados há pouco ou sem cota suficiente): ${inteiro(sem.classes_fim)} classes, ` +
          `${dinheiroSinal(sem.fluxo_investidor)}, só na tabela` : "") + SEM_FILTRO,
      fonte: FONTE,
    });
    barrasDivergentes(grafico, quartis.map(x => ({
      label: x.rotulo, value: x.sobre_pl, display: `${fracaoSinal(x.sobre_pl)} (${dinheiroSinal(x.fluxo_investidor)})`,
    })), { aria: "Captação por quartil de retorno em 12 meses", maxRotulo: 34 });
    mountTabela.innerHTML = tabela([{ rotulo: "Quartil de retorno" }, { rotulo: "Classes", num: true },
      { rotulo: "PL de abertura", num: true }, { rotulo: "Captação 12m", num: true }, { rotulo: "% do PL", num: true }],
    q.map(x => ({ celulas: [escapeHtml(x.rotulo), inteiro(x.classes_fim), texto(dinheiro(x.pl_investidor_abertura)),
      sinal(texto(dinheiroSinal(x.fluxo_investidor)), x.fluxo_investidor), sinal(fracaoSinal(x.sobre_pl), x.sobre_pl)] })));
  }

  /* --- o que mudou ----------------------------------------------------------- */
  function desenharMudou() {
    const l = DADOS.lancamentos || {};
    const mount = document.getElementById("tabela-lancamentos");
    const resumo = ["1m", "12m"].filter(j => l[j]).map(j => ({
      celulas: [escapeHtml(META.janelas[j].rotulo), inteiro(l[j].entrou.produtos), texto(dinheiro(l[j].entrou.pl_investidor)),
        inteiro(l[j].saiu.produtos), texto(dinheiro(l[j].saiu.pl_investidor))],
    }));
    const daMacro = i => FILTRO.n1 === TODOS || i.n1 === FILTRO.n1;
    const lista = (titulo, itens) => (itens.length ? [{ grupo: titulo }, ...itens.map(i => ({
      celulas: [`${escapeHtml(i.nome)}<small>${escapeHtml(cnpjBR(i.cnpj))}</small>`, escapeHtml(nomeN2(i.n1, i.n2)),
        escapeHtml(i.gestora || ""), escapeHtml(ROTULO.papel(i.papel)), competenciaBR(i.competencia),
        texto(dinheiroFino(i.pl_investidor))],
    }))] : []);
    const doze = l["12m"] || { entrou: { maiores: [] }, saiu: { maiores: [] } };
    const linhas = [...lista("Maiores entradas em 12 meses", doze.entrou.maiores.filter(daMacro)),
      ...lista("Maiores saídas em 12 meses", doze.saiu.maiores.filter(daMacro))];
    mount.innerHTML = tabela([{ rotulo: "Janela" }, { rotulo: "Entraram", num: true }, { rotulo: "PL ao entrar", num: true },
      { rotulo: "Saíram", num: true }, { rotulo: "PL ao sair", num: true }], resumo) +
      (linhas.length ? tabela([{ rotulo: "Produto" }, { rotulo: "Peer group" }, { rotulo: "Gestora" }, { rotulo: "Papel" },
        { rotulo: "Competência" }, { rotulo: "PL do investidor", num: true }], linhas) : "");
    figura(mount, {
      titulo: l["12m"] ? `${inteiro(l["12m"].entrou.produtos)} produtos entraram e ${inteiro(l["12m"].saiu.produtos)} saíram do universo em 12 meses`
        : "Entradas e saídas do universo",
      sub: `Entrou: primeira competência no universo dentro da janela, com cadastro de até 45 dias antes. Saiu: ` +
        `estava no universo e não está em ${META.rotulo}. 12 meses: ${periodoBR(META.janelas["12m"].de, META.janelas["12m"].ate)}; ` +
        `1 mês: ${periodoBR(META.janelas["1m"].de, META.janelas["1m"].ate)}. PL no mês da entrada ou no último mês presente` +
        (FILTRO.n1 !== TODOS ? ` · listas da macroclasse ${FILTRO.n1}` : "") + " · só o filtro de macroclasse vale nas listas",
      fonte: "Fonte: CVM, informe diário e cadastro de fundos.",
    });
  }

  /* --- explorador ------------------------------------------------------------ */
  const PASSO = 100;
  const EXP = { busca: "", campo: "pl_investidor", desc: true, limite: PASSO };
  const COLUNAS_EXP = [
    { campo: "nome", rotulo: "Fundo", num: false },
    { campo: "n2", rotulo: "Estratégia", num: false },
    { campo: "papel", rotulo: "Papel e público", num: false },
    { campo: "gestora", rotulo: "Gestora", num: false },
    { campo: "pl_investidor", rotulo: "PL do investidor", num: true },
    { campo: "fluxo_12m", rotulo: "Captação 12m", num: true },
    { campo: "sobre_pl_12m", rotulo: "% do PL", num: true },
    { campo: "retorno_12m", rotulo: "Retorno 12m", num: true },
    { campo: "conta_e_ordem", rotulo: "Conta e ordem", num: true },
    { campo: "taxa_adm", rotulo: "Taxa adm.", num: true },
  ];
  function celulaDoExplorador(p, campo) {
    switch (campo) {
      case "nome":
        return `<td class="nome"><b>${escapeHtml(p.nome || "-")}</b><small>${escapeHtml(p.cnpj || "")}` +
          `${p.subclasse ? " · subclasse " + escapeHtml(p.subclasse) : ""}` +
          `${p.raiz_nome ? " · via " + escapeHtml(p.raiz_nome) : ""}</small></td>`;
      case "n2":
        return `<td>${escapeHtml(p.n2 || "-")}<small>${escapeHtml(p.n1 || "")}</small></td>`;
      case "papel":
        // Papel, publico, resgate e casa dedicada numa coluna: onze colunas
        // nao cabiam em 1.280 px e a taxa ficava fora da tela.
        return `<td>${escapeHtml(ROTULO.papel(p.papel))}<small>${escapeHtml(ROTULO.publico(p.publico))}` +
          `${p.faixa_liquidez ? ` · resgate ${escapeHtml(p.faixa_liquidez)}` : ""}` +
          `${p.casa_dedicada ? ` · casa: ${escapeHtml(p.casa_dedicada)}` : ""}</small></td>`;
      case "gestora":
        return `<td>${escapeHtml(p.gestora || "-")}<small>${escapeHtml(ROTULO.tipo_gestora(p.tipo_gestora))}</small></td>`;
      case "pl_investidor":
        return `<td class="num">${texto(dinheiroFino(p.pl_investidor))}` +
          `${p.cotistas ? `<small>${inteiro(p.cotistas)} ${plural(p.cotistas, "conta", "contas")}</small>` : ""}</td>`;
      case "fluxo_12m":
        return `<td class="num ${classeSinal(p.fluxo_12m)}">${texto(dinheiroFinoSinal(p.fluxo_12m))}</td>`;
      case "sobre_pl_12m":
        return `<td class="num ${classeSinal(p.sobre_pl_12m)}">${fracaoSinal(p.sobre_pl_12m)}</td>`;
      case "retorno_12m": {
        const nota = [doCdi(p.retorno_12m, CDI["12m"]), ok(p.quartil_12m) ? `${p.quartil_12m}º quartil` : ""].filter(Boolean);
        return `<td class="num">${fracao(p.retorno_12m)}${nota.length ? `<small>${nota.join(" · ")}</small>` : ""}</td>`;
      }
      case "conta_e_ordem":
        return `<td class="num">${fracao(p.conta_e_ordem, 0)}</td>`;
      case "taxa_adm":
        return `<td class="num">${ok(p.taxa_adm) ? numBR(p.taxa_adm, 2) + "%" : "-"}</td>`;
      default:
        return `<td>${escapeHtml(p[campo] ?? "-")}</td>`;
    }
  }
  function valorDeOrdem(p, campo) {
    if (campo === "papel") return ROTULO.papel(p.papel);
    if (campo === "publico") return ROTULO.publico(p.publico);
    return p[campo];
  }
  function desenharExplorador(reiniciar) {
    if (reiniciar) EXP.limite = PASSO;
    const termos = semAcento(EXP.busca).split(/\s+/).filter(Boolean);
    const linhas = PRODUTOS.filter(p => passa(p) && termos.every(t => p.busca.includes(t)));
    const coluna = COLUNAS_EXP.find(c => c.campo === EXP.campo) || COLUNAS_EXP[5];
    const direcao = EXP.desc ? -1 : 1;
    linhas.sort((a, b) => {
      const x = valorDeOrdem(a, coluna.campo), y = valorDeOrdem(b, coluna.campo);
      const semX = !ok(x) || x === "", semY = !ok(y) || y === "";
      if (semX || semY) return semX === semY ? 0 : semX ? 1 : -1;  // sem valor sempre no fim
      return coluna.num ? direcao * (x - y) : direcao * String(x).localeCompare(String(y), "pt-BR");
    });
    const visiveis = linhas.slice(0, EXP.limite);
    const cabeca = COLUNAS_EXP.map(c => {
      const ordem = c.campo === coluna.campo ? ` aria-sort="${EXP.desc ? "descending" : "ascending"}"` : "";
      return `<th scope="col"${c.num ? ' class="num"' : ""}${ordem}><button type="button" data-campo="${c.campo}">` +
        `${escapeHtml(c.rotulo)}</button></th>`;
    }).join("");
    const corpo = visiveis.map(p => `<tr>${COLUNAS_EXP.map(c => celulaDoExplorador(p, c.campo)).join("")}</tr>`).join("");
    const mount = document.getElementById("tabela-explorador");
    mount.innerHTML = linhas.length
      ? `<div class="table-scroll"><table class="explorador"><thead><tr>${cabeca}</tr></thead><tbody>${corpo}</tbody></table></div>`
      : '<p class="empty">Nenhum produto com esses filtros e essa busca.</p>';
    mount.querySelectorAll("th button[data-campo]").forEach(b => b.addEventListener("click", () => {
      const campo = b.dataset.campo;
      const col = COLUNAS_EXP.find(c => c.campo === campo);
      if (EXP.campo === campo) EXP.desc = !EXP.desc;
      else { EXP.campo = campo; EXP.desc = col.num; }
      desenharExplorador(true);
    }));
    const pl = linhas.reduce((a, p) => a + (p.pl_investidor || 0), 0);
    document.getElementById("explorador-resumo").innerHTML =
      `${inteiro(linhas.length)} ${plural(linhas.length, "produto", "produtos")}, ${texto(dinheiro(pl))} de PL do investidor ` +
      `em ${escapeHtml(FIM)}` + (linhas.length > visiveis.length ? ` · mostrando ${inteiro(visiveis.length)}` : "") +
      (filtrado() ? ` · recorte: ${escapeHtml(recorte())}` : "");
    const mais = document.getElementById("explorador-mais");
    const faltam = linhas.length - visiveis.length;
    mais.hidden = faltam <= 0;
    mais.textContent = `Mostrar mais ${inteiro(Math.min(faltam, PASSO))}`;
  }
  function montarExplorador() {
    let espera = null;
    const busca = document.getElementById("busca");
    busca.addEventListener("input", () => {
      clearTimeout(espera);
      espera = setTimeout(() => { EXP.busca = busca.value; seguro("explorador", () => desenharExplorador(true)); }, 150);
    });
    document.getElementById("explorador-mais").addEventListener("click", () => {
      EXP.limite += PASSO;
      seguro("explorador", () => desenharExplorador(false));
    });
    document.getElementById("explorador-nota").textContent =
      `Um produto (classe ou subclasse) por linha, com os filtros do topo. PL do investidor em ${FIM}; ` +
      `captação e % do PL ${META.janelas["12m"].texto}; retorno da estratégia-raiz em 12 meses, em % do CDI ` +
      "e o quartil dela no peer group (1º = melhor); conta e ordem em fração do PL; taxa de administração do cadastro. " +
      "Clique no cabeçalho para ordenar. " + FONTE;
  }

  /* --- contexto, qualidade e fontes ------------------------------------------ */
  function desenharContexto() {
    const a = DADOS.adjacentes || {};
    const universo = DADOS.universo || {};
    const fluxos = janelas => JANELAS.map(j => {
      const v = janelas && janelas[j] ? janelas[j].fluxo_investidor : null;
      return sinal(texto(dinheiroSinal(v)), v);
    });
    const linhas = [{
      celulas: ["<b>Universo disponível</b>", texto(dinheiro(DADOS.camadas && DADOS.camadas.pl_investidor)), ...fluxos(universo),
        "informe diário e perfil mensal da CVM"],
    }];
    const avisos = [];
    [["previdencia", "Previdência"], ["etf", "ETF"]].forEach(([chave, rotulo]) => {
      const camada = a[chave] || {};
      if (camada.available === false) {
        linhas.push({ celulas: [escapeHtml(rotulo), texto(dinheiro(camada.pl)), "-", "-", "-", "-", escapeHtml(camada.reason || "indisponível")] });
        return;
      }
      linhas.push({
        celulas: [escapeHtml(rotulo), texto(dinheiro(ok(camada.pl_investidor) ? camada.pl_investidor : camada.pl)),
          ...fluxos(camada.janelas), escapeHtml(camada.fonte || "")],
      });
      if (chave === "etf" && camada.janelas) {
        JANELAS.forEach(j => {
          const x = camada.janelas[j];
          if (x && x.meses_cobertos < x.meses) avisos.push(`ETF, ${META.janelas[j].rotulo}: ${x.meses_cobertos} de ${x.meses} meses com captação em disco.`);
        });
      }
    });
    const mount = document.getElementById("tabela-adjacentes");
    mount.innerHTML = tabela([{ rotulo: "Camada" }, { rotulo: "PL", num: true },
      ...JANELAS.map(j => ({ rotulo: META.janelas[j].rotulo, num: true })), { rotulo: "Fonte" }], linhas) +
      (avisos.length ? `<p class="nota">${avisos.map(escapeHtml).join(" ")}</p>` : "");
    figura(mount, {
      titulo: "Previdência e ETF ficam ao lado: nunca entram no total do universo",
      sub: `PL no fechamento de ${FIM}; captação do investidor por janela: ${META.prazos.join(" · ")}`,
      fonte: "Fonte: CVM, informe diário e perfil mensal; ETF pelo monitor de ETFs (Fundos.NET).",
    });

    const q = DADOS.qualidade || {};
    const e = DADOS.estrutura || {};
    const cl = DADOS.classificacao || {};
    const origem = Object.entries(cl.origem || {}).sort((x, y) => (y[1] || 0) - (x[1] || 0))
      .map(([k, v]) => `${ORIGEM[k] || k} ${fracao(v)}`).join(", ");
    const itens = [
      ["PL do universo com perfil mensal do próprio mês", fracao(q.cobertura_perfil)],
      ["PL com o perfil do mês anterior (recuo)", dinheiro(q.pl_com_perfil_anterior)],
      ["Produtos no informe sem cadastro", q.sem_cadastro ? `${inteiro(q.sem_cadastro.produtos)}, com ${dinheiro(q.sem_cadastro.pl)}` : "-"],
      ["Fluxos estruturais (captação maior que o PL anterior)", q.fluxos_estruturais
        ? `${inteiro(q.fluxos_estruturais.produtos)} produtos, ${dinheiroSinal(q.fluxos_estruturais.captacao_liquida)}` : "-"],
      ["Eventos na cota (retorno fora do plausível, fora da mediana)", inteiro(q.eventos_na_cota || 0)],
      ["Resíduo da decomposição sobre a variação do PL, 12 meses", fracao(q.residuo_sobre_variacao)],
      ["Retrato do cadastro usado", competenciaBR(q.retrato_do_cadastro)],
      ["Competências com perfil mensal", (q.meses_com_perfil || []).map(mesCurto).join(", ") || "-"],
      ["Competências da carteira (CDA)", (e.competencias_da_carteira || []).map(mesCurto).join(", ") || "sem carteira"],
      ["PL sem carteira", dinheiro(e.pl_sem_carteira)],
      ["PL com perfil e carteira divergentes em mais de 25 p.p.", dinheiro(e.pl_com_divergencia)],
      ["Origem da estratégia, em fração do PL", origem || "-"],
      ["PL em grupos gestores curados", fracao((DADOS.gestoras || {}).cobertura_curada)],
      ["CDI em 12 meses (SGS do Banco Central)", ok(CDI["12m"]) ? fracao(CDI["12m"], 2) : ((DADOS.macro || {}).reason || "sem o SGS")],
    ];
    const seg = ((DADOS.dinamica || {}).captacao_por_segmento || {}).total;
    if (seg) {
      itens.push(["Captação por tipo de cotista: resíduo e ruído do perfil, 12 meses",
        `resíduo ${dinheiroSinal(seg.residuo)}; ruído ${dinheiro(seg.ruido)} (sai como número acima de ${dinheiro(seg.piso)})`]);
    }
    const mountQ = document.getElementById("tabela-qualidade");
    mountQ.innerHTML = tabela([{ rotulo: "Indicador" }, { rotulo: "Valor" }],
      itens.map(([k, v]) => ({ celulas: [escapeHtml(k), texto(v)] })));
    figura(mountQ, {
      titulo: "Quanto do número é medido e quanto é estimado",
      sub: `Competência ${META.rotulo}; informe ${periodoBR(META.informe.de, META.informe.ate)}`,
      fonte: FONTE,
    });
    const listaAvisos = document.getElementById("lista-avisos");
    notas(listaAvisos, DADOS.avisos || []);
  }

  function desenharFontes() {
    const mount = document.getElementById("lista-fontes");
    mount.innerHTML = '<ul class="fontes">' + (DADOS.fontes || []).map(f =>
      `<li><a href="${escapeHtml(f.url)}" target="_blank" rel="noopener">${escapeHtml(f.nome)}</a>` +
      `<span class="uso">${escapeHtml(f.uso)}</span></li>`).join("") + "</ul>";
  }

  /* --- ciclo ----------------------------------------------------------------- */
  // Um bloco que quebra nao leva junto os outros nem os filtros.
  function seguro(nome, desenhar) {
    try {
      desenhar();
    } catch (erro) {
      console.error(`painel da indústria, ${nome}:`, erro);
    }
  }
  function atualizar() {
    seguro("filtros", sincronizarFiltros);
    seguro("destaques", desenharKpis);
    seguro("macroclasses", desenharResumoN1);
    seguro("onde está o dinheiro", desenharOnde);
    seguro("para onde vai", desenharParaOnde);
    seguro("quem ganha", desenharQuemGanha);
    seguro("por onde chega", desenharCanal);
    seguro("desempenho", desenharDesempenho);
    seguro("o que mudou", desenharMudou);
    seguro("explorador", () => desenharExplorador(true));
  }

  seguro("filtros", montarFiltros);
  seguro("janelas", montarSeletorJanela);
  seguro("explorador", montarExplorador);
  // Blocos que nao mudam com filtro: desenhados uma vez.
  seguro("dono do dinheiro", desenharSegmentos);
  seguro("funil", desenharFunil);
  seguro("mês a mês", desenharHistorico);
  seguro("macro", desenharMacro);
  seguro("entrantes", desenharEntrantes);
  seguro("quem trouxe o dinheiro", desenharSegmentosFluxo);
  seguro("fluxo e retorno", desenharFluxoRetorno);
  seguro("contexto", desenharContexto);
  seguro("fontes", desenharFontes);
  atualizar();
})();
