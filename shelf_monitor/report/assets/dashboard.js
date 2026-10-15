const DATA = JSON.parse(document.getElementById("dashboard-data").textContent);
// Blocos que o modulo declarou sem sentido (chave -> motivo); vazio em fundos.
// "cotistas": nada que conte cotistas; "cnpj": nada que cruze vitrines por CNPJ.
const SUPRIMIDOS = DATA.suprimidos || {};

/* Cor por casa, com a cor de marca de cada uma (docs/linguagem-visual.md). A
   cor segue a casa pelo id - nunca a posicao no ranking nem a ordem desta
   execucao -, entao a XP e amarela em todo grafico e em toda coleta (com o
   contorno preto do CSS). Casa fora do registro fica cinza: um sexto matiz
   nao passaria no validador. */
const CASAS_COM_COR = ["xp", "btg", "itau", "nu", "inter"];
function corDaCasa(id) {
  return CASAS_COM_COR.includes(id) ? `var(--casa-${id})` : "var(--casa-outra)";
}
const platformColor = {};
(DATA.house_order || []).concat(DATA.platforms.map(p => p.platform_id)).forEach(id => {
  platformColor[id] = corDaCasa(id);
});
// Numero escrito DENTRO de uma marca da cor da casa: tinta ou branco, pelo
// contraste medido de cada cor (o laranja do Inter e o verde da XP pedem tinta).
const TINTA_SOBRE = { xp: "#0a0a0a", btg: "#ffffff", itau: "#ffffff", nu: "#0a0a0a", inter: "#0a0a0a" };
const BASE_ID = (DATA.gaps && DATA.gaps.baseline_id) || (DATA.flow && DATA.flow.baseline_id) || "xp";

function rotuloDaBase() {
  const p = DATA.platforms.find(x => x.platform_id === BASE_ID);
  return (p && p.label) || (DATA.gaps && DATA.gaps.baseline_label) || "XP";
}

/* A base comparativa e a vitrine, sempre. Casa sem vitrine alcancavel nao entra
   nos graficos medida pelo catalogo Open Finance - sai com motivo, em
   `foraDaComparacao`, que cada bloco imprime no rodape. */
const usable = DATA.platforms.filter(p => p.status !== "unavailable" && p.listing_funds > 0);
const foraDaComparacao = DATA.platforms.filter(
  p => p.status !== "unavailable" && !p.listing_funds && p.catalog_funds > 0);

/* Rodape de proveniencia: quem ficou fora do bloco, e por que. Sobe ate o corpo
   do painel porque o alvo costuma estar dentro de um <details> fechado, e nota
   de metodologia que so aparece se o leitor expandir a tabela nao e nota. O
   corpo, e nao a section: ela e a moldura do painel, e a nota pendurada nela
   ficaria fora do recolhimento, solta abaixo do titulo. */
function notaFora(alvo) {
  const mount = alvo && alvo.closest
    ? (alvo.closest(".panel-body") || alvo.closest("section")) : null;
  if (!mount || !foraDaComparacao.length) return;
  /* Travessao em vez de parenteses: o rotulo da casa ja traz os seus
     ("Nu (Nubank)"), e o motivo tambem ("(estrategia: open-finance)"). */
  const nomes = foraDaComparacao
    .map(p => `${p.label} — ${p.listing_reason || "sem vitrine"}, ` +
      `${p.catalog_funds.toLocaleString("pt-BR")} no catálogo`).join("; ");
  const nota = document.createElement("p");
  nota.className = "card-sub";
  nota.style.marginTop = "10px";
  nota.textContent = `Fora da comparação: ${nomes}.`;
  mount.appendChild(nota);
}

// A melhor de uma lista pelo campo, e a posicao da base nela (1 = lidera).
function lider(itens, valor, maior = true) {
  const validos = itens.filter(i => ok(valor(i)));
  const ordem = validos.slice().sort((a, b) => (maior ? valor(b) - valor(a) : valor(a) - valor(b)));
  const posicaoBase = ordem.findIndex(i => (i.platform_id || i.id) === BASE_ID) + 1;
  return { melhor: ordem[0], ordem, posicaoBase, total: ordem.length };
}

/* --- Vitrine de varejo ---------------------------------------------------- */

function renderVitrine(listing) {
  const section = document.getElementById("vitrine-section");
  const withListing = (listing && listing.platforms || []).filter(p => p.total > 0);
  if (!withListing.length) {
    // So dentro do corpo: o <details> do proprio painel tambem casaria com
    // "details", e remove-lo levaria o painel inteiro junto.
    const corpo = section.querySelector(".panel-body") || section;
    corpo.querySelectorAll(".tiles, .matrix-scroll, .subgrid, details").forEach(n => n.remove());
    document.getElementById("vitrine-coverage").textContent =
      "Nenhuma vitrine coletada nesta execução - rode a coleta com navegador " +
      "(SHELF_BROWSER_HEADED=1 para o Itaú) para materializar as listagens.";
    return;
  }
  const base = rotuloDaBase();

  // Tamanho da vitrine por casa, o catalogo como texto fraco ao lado.
  const tiles = document.getElementById("vitrine-tiles");
  tiles.className = "chart-scroll";
  const porTamanho = withListing.slice().sort((a, b) => b.total - a.total);
  const maior = porTamanho[0];
  const daBase = withListing.find(p => p.platform_id === BASE_ID);
  figura(tiles, {
    titulo: maior.platform_id === BASE_ID
      ? `${maior.label} tem a maior vitrine: ${inteiro(maior.total)} fundos`
      : `${maior.label} tem a maior vitrine, com ${inteiro(maior.total)} fundos` +
        (daBase ? `; ${daBase.label}, ${inteiro(daBase.total)}` : ""),
    sub: "Fundos na vitrine de cada casa; ao lado, o catálogo Open Finance.",
    fonte: "Fonte: vitrines públicas das casas e catálogo Open Finance, na coleta desta execução.",
  });
  barrasH(tiles, porTamanho.map(p => {
    const catalog = p.catalog_total || 0;
    return {
      label: p.label, value: p.total, color: corDaCasa(p.platform_id), base: p.platform_id === BASE_ID,
      extra: catalog > p.total ? `de ${inteiro(catalog)} no catálogo` : "",
    };
  }), { aria: "Fundos na vitrine por casa" });

  // Matriz casa x classe ANBIMA (mapa de calor sequencial).
  let matrix = listing.matrix || [];
  const mount = document.getElementById("matrix-vitrine");
  // Matriz so de "sem classificacao" nao e matriz - e a ausencia da base.
  if (matrix.length === 1 && matrix[0].category === "Sem classificação") {
    matrix = [];
  }
  if (matrix.length) {
    const max = Math.max(1, ...matrix.flatMap(r => Object.values(r.counts)));
    figura(mount, {
      titulo: manchetePorLinhas(matrix, withListing, (r, p) => r.counts[p.platform_id] || 0,
        "classe ANBIMA", "classes ANBIMA") || "Fundos da vitrine por classe ANBIMA",
      sub: "Fundos da vitrine por classe ANBIMA; mais escuro, mais fundos.",
      fonte: "Fonte: vitrines das casas, cruzadas por CNPJ com o registro de fundos da CVM e a classificação ANBIMA.",
    });
    const table = document.createElement("table");
    table.className = "matrix";
    const head = table.createTHead().insertRow();
    const corner = document.createElement("th");
    corner.className = "rowh"; corner.textContent = "Classe ANBIMA";
    head.appendChild(corner);
    withListing.forEach(p => {
      const th = document.createElement("th");
      th.textContent = p.label;
      if (p.platform_id === BASE_ID) th.style.boxShadow = "inset 0 -3px 0 var(--casa-xp)";
      head.appendChild(th);
    });
    const body = table.createTBody();
    matrix.forEach(rowData => {
      const row = body.insertRow();
      const rowLabel = row.insertCell();
      rowLabel.className = "rowlabel";
      rowLabel.textContent = rowData.category;
      rowLabel.title = rowData.category;
      withListing.forEach(p => {
        const cell = row.insertCell();
        const value = rowData.counts[p.platform_id] || 0;
        const span = document.createElement("span");
        span.className = "cell";
        if (value) {
          const heat = heatColor(value, max);
          span.style.background = heat.fill;
          span.style.color = heat.ink;
          span.textContent = value.toLocaleString("pt-BR");
          span.title = `${p.label} - ${rowData.category}: ${value}`;
        } else {
          span.className = "cell zero";
          span.textContent = "–";
        }
        cell.appendChild(span);
      });
    });
    mount.innerHTML = "";
    mount.appendChild(table);

    table2(document.getElementById("table-vitrine"),
      ["Classe ANBIMA", ...withListing.map(p => p.label)],
      matrix.map(r => [r.category, ...withListing.map(p => String(r.counts[p.platform_id] || 0))]));
  } else {
    mount.innerHTML = '<p class="empty">Sem classificação ANBIMA disponível - importe a base ' +
      'de mercado (python -m shelf_monitor market) para normalizar a vitrine.</p>';
  }

  // Arquitetura aberta: propria vs terceiros, em parcela da vitrine.
  // A previdencia suprime o corte (o gestor registrado e a seguradora, nao o
  // dono da estrategia) e a pagina dela nao traz o grafico; se a pagina de
  // fundos receber um relatorio com o corte suprimido, sai o motivo - o
  // grafico publicaria justamente o numero que o modulo declarou sem sentido.
  const archMount = document.getElementById("chart-arch");
  const archLegend = document.getElementById("legend-arch");
  if (archMount && archLegend && SUPRIMIDOS.produto_proprio) {
    archMount.innerHTML = '<div class="note-box">Não se aplica a este monitor: ' +
      `${escapeHtml(SUPRIMIDOS.produto_proprio)}.</div>`;
  } else if (archMount && archLegend) {
    const partes = [
      { chave: "propria", rotulo: "Gestora da casa", cor: "var(--comp-1)" },
      { chave: "terceiros", rotulo: "Terceiros", cor: "var(--comp-2)", tinta: "#121212" },
      { chave: "sem", rotulo: "Sem gestor identificado", cor: "var(--grid)", tinta: "#121212" },
    ];
    legenda(archLegend, partes);
    const linhas = withListing.map(p => ({
      label: p.label, base: p.platform_id === BASE_ID,
      valores: {
        propria: p.own_product || 0, terceiros: p.third_party || 0,
        sem: Math.max(0, p.total - (p.own_product || 0) - (p.third_party || 0)),
      },
    }));
    const aberta = linhas.slice().sort((a, b) =>
      b.valores.terceiros / (b.valores.propria + b.valores.terceiros + b.valores.sem || 1) -
      a.valores.terceiros / (a.valores.propria + a.valores.terceiros + a.valores.sem || 1))[0];
    const shareTerceiros = l => Math.round(100 * l.valores.terceiros /
      (l.valores.propria + l.valores.terceiros + l.valores.sem || 1));
    figura(archMount, {
      titulo: `${aberta.label} é a vitrine mais aberta: ${shareTerceiros(aberta)}% de terceiros`,
      sub: "% dos fundos da vitrine por gestora: da casa, de terceiros ou sem gestor identificado.",
      legenda: archLegend,
      fonte: "Fonte: vitrines das casas; gestora pelo registro da CVM.",
    });
    cemPorCento(archMount, linhas, partes, { aria: "Arquitetura aberta por casa" });
  }

  // Ticket de entrada: tabela compacta - poucos numeros precisos pedem tabela.
  // A taxa de administracao NAO entra aqui. O quartil de taxa bruta entre casas
  // descreve o mix da prateleira (renda fixa ~0,2% a.a., acoes ~2%), nao o
  // preco do produto - e o bloco "Custo relativo a categoria", cinco secoes
  // abaixo, dizia isso por escrito enquanto esta tabela seguia publicando a
  // leitura que ele desautorizava.
  table2(document.getElementById("table-costs"),
    ["Casa", "Mínimo q1", "mediana", "q3", "Cobertura CNPJ"],
    withListing.map(p => {
      const min = p.min_quartiles;
      const money = v => v === undefined || v === null ? "–"
        : "R$ " + v.toLocaleString("pt-BR");
      return [
        p.label,
        min ? money(min.q1) : "–", min ? money(min.median) : "–",
        min ? money(min.q3) : "–",
        Math.round((p.cnpj_coverage || 0) * 100) + "%",
      ];
    }));

  // Cobertura e casas sem vitrine - o rodape que um comite pergunta primeiro.
  const parts = [];
  const missing = (listing.platforms || []).filter(p => !p.total);
  if (missing.length) {
    parts.push("Sem vitrine alcançável: " + missing.map(p => p.label).join(", "));
  }
  const identifiable = DATA.enrichment
    ? Math.max(DATA.enrichment.with_cnpj || 0, DATA.enrichment.with_cvm_code || 0)
    : 0;
  if (identifiable) {
    const e = DATA.enrichment;
    parts.push(`Classe ANBIMA: ${inteiro(e.matched)} de ${inteiro(identifiable)} fundos ` +
      `casados com o registro oficial (${Math.round(100 * e.matched / identifiable)}%)`);
    if (e.managers_from_cvm) {
      parts.push(`Gestora pelo registro da CVM: ${inteiro(e.managers_from_cvm)} fundos`);
    }
    if (e.matched_by_cvm_code) {
      parts.push(`Casados pelo código CVM, sem CNPJ na fonte: ${inteiro(e.matched_by_cvm_code)}`);
    }
    if (e.managers_inferred) {
      parts.push(`Gestoras inferidas pelo prefixo do nome (nunca como oficiais): ${inteiro(e.managers_inferred)}`);
    }
  } else {
    parts.push("Sem classe ANBIMA: base de mercado não importada (python -m shelf_monitor market)");
  }
  // Cruza vitrines por CNPJ: some com a supressao, como gaps e exclusivos.
  if (listing.shared_all && !SUPRIMIDOS.cnpj) {
    parts.push(`Em 2+ vitrines (por CNPJ): ${inteiro(listing.shared_all)} fundos`);
  }
  notas(document.getElementById("vitrine-coverage"), parts);
}

renderVitrine(DATA.listing);

/* --- Gaps de prateleira da XP ---------------------------------------------
   Leitura XP-centrica dos mesmos dados da vitrine: o que os concorrentes
   vitrinam e a vitrine da XP nao tem. Sem baseline utilizavel o bloco vira
   uma frase de supressao - nunca um numero falso. */
function renderGaps(gaps) {
  const section = document.getElementById("gaps-section");
  // A pagina da previdencia nao tem este painel (gap por CNPJ mede a
  // seguradora, nao a oferta). Sem a guarda, o null derrubava todo bloco
  // desenhado depois deste.
  if (!section) return;
  if (!gaps || !gaps.available) {
    // O titulo mora no <summary> do painel; troca-se so o corpo.
    (section.querySelector(".panel-body") || section).innerHTML =
      `<p class="card-sub">Fundos de varejo nas vitrines concorrentes e fora da vitrine
       da XP (por CNPJ).</p>
       <div class="note-box">${gaps && gaps.nao_se_aplica
         ? "Não se aplica a este monitor"
         : "Análise de gaps suprimida nesta execução"}: ` +
      `${escapeHtml((gaps && gaps.reason) || "dados insuficientes")}.</div>`;
    return;
  }

  const money = v => v === undefined || v === null ? "–"
    : "R$ " + v.toLocaleString("pt-BR");
  const pct = v => v === undefined || v === null ? "–"
    : v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "%";
  const base = gaps.baseline_label || rotuloDaBase();

  const tiles = document.getElementById("gaps-tiles");
  tiles.innerHTML = "";
  // Com a carteira da CVM o bloco mede estrategia, nao so CNPJ: o funil vira
  // um fluxo de aparente a real (McKinsey). Sem ela, os tiles de sempre.
  const comCarteira = gaps.carteira && gaps.carteira.available;
  const funil = gaps.funil || {};
  if (comCarteira && funil.aparentes) {
    tiles.className = "chart-scroll";
    // Nos reais no amarelo da XP (com o contorno do CSS); as faixas que levam a
    // eles no amarelo mais aberto, sem contorno - so o no e marca da casa.
    const cinza = "var(--axis)", real = "var(--casa-xp)", faixaReal = "var(--xp-link)";
    const nos = [
      { id: "aparentes", coluna: 0, valor: funil.aparentes, rotulo: "Gaps aparentes", cor: "var(--text-secondary)" },
      { id: "reais", coluna: 1, valor: funil.reais || 0, rotulo: "Gaps reais", cor: real, forte: true },
      { id: "mesma", coluna: 1, valor: funil.mesma_classe || 0, rotulo: "Mesma classe, outro CNPJ", cor: cinza },
      { id: "espelho", coluna: 1, valor: funil.espelho || 0, rotulo: "Espelho de fundo da XP", cor: cinza },
      { id: "equivalente", coluna: 1, valor: funil.equivalente || 0, rotulo: "Equivalente na XP", cor: cinza },
      { id: "correlato", coluna: 1, valor: funil.correlato || 0, rotulo: "Outra versão na XP", cor: cinza },
      { id: "liquidez", coluna: 1, valor: funil.outra_liquidez || 0, rotulo: "Só em outra liquidez", cor: cinza },
      { id: "estrategia", coluna: 2, valor: funil.estrategia || 0, rotulo: "De estratégia", cor: real },
      { id: "gestora", coluna: 2, valor: funil.gestora || 0, rotulo: "De gestora", cor: real },
      { id: "sem_dado", coluna: 2, valor: funil.sem_dado || 0, rotulo: "Sem carteira para decidir", cor: "var(--comp-2)" },
    ];
    const ligacoes = ["reais", "mesma", "espelho", "equivalente", "correlato", "liquidez"].map(id => ({
      de: "aparentes", para: id, valor: nos.find(n => n.id === id).valor,
      cor: id === "reais" ? faixaReal : cinza, opacidade: id === "reais" ? 0.5 : 0.32,
      rotulo: nos.find(n => n.id === id).rotulo,
    })).concat(["estrategia", "gestora", "sem_dado"].map(id => ({
      de: "reais", para: id, valor: nos.find(n => n.id === id).valor,
      cor: id === "sem_dado" ? "var(--comp-2)" : faixaReal, opacidade: id === "sem_dado" ? 0.32 : 0.5,
      rotulo: nos.find(n => n.id === id).rotulo,
    })));
    const comp = (gaps.carteira.competencias || [])[0];
    figura(tiles, {
      titulo: `${inteiro(funil.reais)} fundos faltam de fato na vitrine da ${base}; ` +
        `${inteiro(funil.aparentes)} pareciam faltar`,
      sub: "Fundos, do gap aparente (CNPJ fora da XP) ao real" +
        (comp ? `; carteira mensal da CVM de ${competenciaBR(comp)}.` : "."),
      fonte: "Fonte: vitrines das casas; carteira mensal (CDA) e cadastro de fundos da CVM.",
    });
    fluxo(tiles, nos, ligacoes, { altura: 280, aria: "Funil dos gaps: de aparentes a reais" });
  } else {
    [
      ["Fundos de gap", gaps.total_gaps, "em vitrine concorrente, fora da vitrine XP"],
      ["Em 2+ vitrines", gaps.multi_house_gaps, "gap confirmado por mais de uma casa"],
      ["Vitrine da XP", gaps.baseline_listing_total, "a base de comparação"],
      ["Concorrentes no universo", gaps.competitors.length,
       gaps.competitors.map(c => c.label).join(", ") || "nenhum"],
    ].forEach(([label, value, note]) => {
      const tile = document.createElement("div");
      tile.className = "tile";
      const l = document.createElement("div"); l.className = "label"; l.textContent = label;
      const v = document.createElement("div"); v.className = "value";
      v.textContent = Number(value).toLocaleString("pt-BR");
      const n = document.createElement("div"); n.className = "note"; n.textContent = note;
      tile.append(l, v, n);
      tiles.appendChild(tile);
    });
  }
  // A frase do funil so quando nao ha o fluxo desenhado: com ele, ela repete
  // em cinco oracoes o que o grafico mostra.
  const funilEl = document.getElementById("gaps-funil");
  if (funilEl) funilEl.textContent = comCarteira && funil.aparentes ? "" : (gaps.frase_funil || "");

  // Categorias em deficit: matriz de bolhas (McKinsey) - a vitrine de cada
  // casa na categoria, area proporcional ao numero -, e os gaps reais da XP
  // na categoria como barra a direita.
  const mount = document.getElementById("matrix-gaps");
  const categories = gaps.categories || [];
  if (categories.length) {
    const houses = [{ platform_id: gaps.baseline_id, label: gaps.baseline_label }]
      .concat(gaps.competitors);
    const valor = (c, h) => h.platform_id === gaps.baseline_id
      ? (c.xp || 0) : (c.by_platform || {})[h.platform_id] || 0;
    const max = Math.max(1, ...categories.flatMap(c => houses.map(h => valor(c, h))));
    const maxGap = Math.max(1, ...categories.map(c => c.gap_count || 0));
    // "Outras" e o resto somado, nao uma categoria: fica fora da manchete.
    const topo = categories.filter(c => !/^outr[ao]s$/i.test(c.category))
      .sort((a, b) => (b.gap_count || 0) - (a.gap_count || 0))
      .slice(0, 3).map(c => c.category);
    figura(mount, {
      titulo: `A vitrine da ${base} é mais rasa em ${juntar(topo)}`,
      sub: "Fundos na vitrine, por categoria (área do círculo); à direita, gaps reais e cobertos da XP.",
      fonte: "Fonte: vitrines das casas; categoria ANBIMA pelo registro da CVM.",
    });
    const D = 34;
    const bolha = (v, h) => {
      if (!v) return '<span class="zero" style="color:var(--muted)">–</span>';
      const d = Math.max(6, Math.round(D * Math.sqrt(v / max)));
      const dentro = d >= 24;
      const tinta = TINTA_SOBRE[h.platform_id] || "#ffffff";
      return `<span title="${escapeHtml(h.label)}: ${v}" style="display:inline-flex;align-items:center;gap:5px">` +
        `<i style="width:${d}px;height:${d}px;background:${corDaCasa(h.platform_id)};color:${tinta}">` +
        `${dentro ? v : ""}</i>${dentro ? "" : `<small style="font-size:11.5px">${v}</small>`}</span>`;
    };
    mount.innerHTML =
      '<table class="matrix bolhas"><thead><tr><th class="rowh">Categoria</th>' +
      houses.map(h => `<th class="col"${h.platform_id === gaps.baseline_id
        ? ' style="box-shadow:inset 0 -3px 0 var(--casa-xp)"' : ""}>${escapeHtml(h.label)}</th>`).join("") +
      '<th class="num">Gaps reais</th><th class="num">Cobertos</th></tr></thead><tbody>' +
      categories.map(c => `<tr><th class="rowh" title="${escapeHtml(c.category)}">${escapeHtml(cortar(c.category, 42))}</th>` +
        houses.map(h => `<td class="bolha">${bolha(valor(c, h), h)}</td>`).join("") +
        `<td class="num"><span style="display:inline-flex;align-items:center;gap:6px;justify-content:flex-end">` +
        `<i style="display:inline-block;height:10px;border-radius:0 2px 2px 0;background:var(--casa-xp);width:${Math.round(56 * (c.gap_count || 0) / maxGap)}px"></i>` +
        `<strong>${inteiro(c.gap_count)}</strong></span></td>` +
        `<td class="num">${inteiro(c.cobertos)}</td></tr>`).join("") +
      "</tbody></table>";
  } else {
    mount.innerHTML =
      '<p class="empty">Nenhum gap por categoria nesta execução.</p>';
  }

  // A lista nominal - o insumo de trabalho. Ordenada por captacao e nao por
  // PL: o gap que vale primeiro e o que esta levando dinheiro de varejo agora.
  // "Esforco" e o que separa uma inclusao operacional de uma que exige abrir
  // gestora - era a unica coisa que a lista de "oportunidades de grade" tinha
  // e esta nao, e por isso as duas conviviam.
  const mm = v => (v === null || v === undefined) ? "–"
    : (v > 0 ? "+" : "") + (v / 1e6).toLocaleString("pt-BR",
        { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " MM";
  const dmais = v => v === null || v === undefined ? "–" : "D+" + v;
  table(
    document.getElementById("table-gaps"),
    [{ key: "name", label: "Fundo" }, { key: "cnpj_display", label: "CNPJ" },
     { key: "category", label: "Categoria" },
     { key: "manager", label: "Gestora da estratégia" },
     { key: "houses_label", label: "Casas" },
     { key: "capt", label: "Captação no mercado", num: true },
     { key: "cot", label: "Cotistas ganhos", num: true },
     { key: "esforco", label: "Esforço" },
     { key: "veredito", label: "Veredito" },
     { key: "dmais", label: "Resgate", num: true },
     { key: "min", label: "Menor mínimo", num: true },
     { key: "fee", label: "Menor taxa", num: true },
     { key: "pl", label: "PL CVM", num: true },
     { key: "equiv", label: "Possível equivalente XP" }],
    (gaps.funds || []).map(f => ({
      name: f.name, cnpj_display: f.cnpj_display, category: f.category,
      manager: f.gestora_estrategia || f.manager || "–", houses_label: f.houses_label,
      capt: mm(f.captacao_liquida),
      cot: f.variacao_cotistas === null || f.variacao_cotistas === undefined
        ? "–" : f.variacao_cotistas.toLocaleString("pt-BR"),
      esforco: f.esforco || "–",
      veredito: (f.veredito && f.veredito.rotulo) || "–",
      dmais: dmais(f.dmais),
      min: money(f.min_initial), fee: pct(f.admin_fee_pct), pl: money(f.pl_cvm),
      equiv: (f.veredito && f.veredito.nome_possivel_equivalente)
        || f.possible_xp_equivalent || "–",
    }))
  );

  // Os gaps aparentes que a XP ja cobre - fora da conta, nunca fora da vista.
  const cobertosMount = document.getElementById("table-gaps-cobertos");
  if (cobertosMount) {
    const cobertos = gaps.cobertos || [];
    if (cobertos.length) {
      const frac = v => v === null || v === undefined ? "–"
        : Math.round(v * 100).toLocaleString("pt-BR") + "%";
      table(
        cobertosMount,
        [{ key: "name", label: "Fundo concorrente" }, { key: "cnpj_display", label: "CNPJ" },
         { key: "houses_label", label: "Casas" },
         { key: "veredito", label: "Veredito" }, { key: "cobre", label: "Fundo da XP que cobre" },
         { key: "master", label: "Master" },
         { key: "liq", label: "Resgate (gap / XP)" },
         { key: "sob", label: "Ativos em comum", num: true },
         { key: "conf", label: "Confiança" }],
        cobertos.map(f => {
          const v = f.veredito || {};
          return {
            name: f.name, cnpj_display: f.cnpj_display, houses_label: f.houses_label,
            veredito: v.rotulo || "–", cobre: v.nome_cobre || "–",
            master: v.nome_master || "–",
            liq: dmais(v.dmais) + " / " + dmais(v.dmais_cobre),
            sob: frac(v.sobreposicao), conf: v.confianca || "–",
          };
        })
      );
    } else {
      const vazio = document.createElement("p");
      vazio.className = "empty";
      vazio.textContent = comCarteira
        ? "Nenhum gap aparente coberto por espelho ou equivalente."
        : "Sem a carteira mensal da CVM nesta execução: espelho e equivalência não foram verificados.";
      cobertosMount.innerHTML = "";
      cobertosMount.appendChild(vazio);
    }
  }

  // Proveniencia - o rodape que evita a leitura errada.
  const parts = [];
  if (gaps.baseline_warning) parts.push(gaps.baseline_warning);
  const partial = gaps.competitors.filter(c => c.partial);
  if (partial.length) {
    parts.push("Coleta parcial, gaps podem estar subestimados: " + partial.map(c => c.label).join(", "));
  }
  if (gaps.excluded && gaps.excluded.length) {
    parts.push("Fora do universo: " + gaps.excluded.map(e => `${e.label} (${e.reason})`).join("; "));
  }
  if (gaps.funds_truncated) {
    parts.push(`Lista: os ${gaps.funds.length} primeiros; mais ${inteiro(gaps.funds_truncated)} na planilha`);
  }
  if (gaps.cobertos_truncated) {
    parts.push(`Cobertos: os ${gaps.cobertos.length} primeiros; mais ` +
      `${inteiro(gaps.cobertos_truncated)} na aba Gaps cobertos da planilha`);
  }
  (gaps.notas_cobertura || []).forEach(n => parts.push(n));
  parts.push("Possível equivalente XP: sinal de baixa confiança; não tira o fundo da lista");
  notas(document.getElementById("gaps-coverage"), parts);
}
renderGaps(DATA.gaps);

// --- captacao ---------------------------------------------------------------
(function renderFlow(flow) {
  const section = document.getElementById("flow-section");
  if (!flow || !flow.available) { section.remove(); return; }

  const mm = v => {
    const n = v / 1e6;
    const s = n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    return (n > 0 ? "+" : "") + s + " MM";
  };
  const int = v => (v > 0 ? "+" : "") + v.toLocaleString("pt-BR");
  const pctPl = v => (v === null || v === undefined) ? "-"
    : (v > 0 ? "+" : "") + v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "%";
  const share = (n, total) => total ? Math.round(n / total * 100) + "%" : "-";
  const rotulo = pid => (flow.rotulos || {})[pid] || pid;
  // Verde e vermelho so com o sinal escrito ao lado - nunca a unica pista.
  const celula = (texto, valor) => {
    const classe = valor > 0 ? "cell pos" : valor < 0 ? "cell neg" : "cell";
    return `<td><span class="${classe}">${texto}</span></td>`;
  };
  const meses = (flow.periodo && flow.periodo.meses_lidos) || [];
  const prazo = `${meses.length || "-"} ${meses.length === 1 ? "mês" : "meses"}`;
  const periodo = flow.periodo ? periodoBR(flow.periodo.de, flow.periodo.ate) : "";
  const fonteInforme = "Fonte: informe diário de fundos da CVM; vitrines das casas.";
  const base = flow.baseline_label || rotuloDaBase();

  // O prazo no topo da secao, antes de qualquer numero: a captacao cobre as
  // ultimas competencias do informe, a corrente ainda em curso. As frases de
  // leitura do Python (as do digest) sairam daqui em 27/09/2026: cada uma
  // narrava o que um grafico abaixo ja mostra.
  const prazoEl = document.getElementById("flow-prazo");
  if (prazoEl && flow.periodo) {
    prazoEl.innerHTML = `<strong>Prazo: ${prazo}</strong>, ` + periodo +
      (meses.length ? ` (${meses.map(competenciaBR).join(", ")})` : "") + ".";
  }

  // Captacao liquida por casa: barras divergentes a partir do zero vermelho.
  const tilesEl = document.getElementById("flow-tiles");
  tilesEl.className = "chart-scroll";
  const ordem = lider(flow.casas, c => c.captacao_liquida);
  const topo = ordem.melhor;
  figura(tilesEl, {
    titulo: topo && topo.platform_id === BASE_ID
      ? `${base} lidera a captação líquida em ${prazo}: ${reais(topo.captacao_liquida)}`
      : `${topo ? topo.label : "-"} lidera a captação em ${prazo}; ${base} é a ` +
        `${ordinalF(ordem.posicaoBase || ordem.total)} de ${ordem.total}`,
    sub: `Captação líquida, R$, ${periodo}; entre parênteses, % do PL de início.`,
    fonte: fonteInforme + " Fluxo por fundo, não por distribuidor.",
  });
  const porCaptacao = flow.casas.slice().sort((a, b) => b.captacao_liquida - a.captacao_liquida);
  barrasDivergentes(tilesEl, porCaptacao.map(c => ({
    label: c.label, value: c.captacao_liquida, color: corDaCasa(c.platform_id),
    base: c.platform_id === BASE_ID,
    display: `${reais(c.captacao_liquida)}` + (ok(c.sobre_pl) ? ` (${pctPl(c.sobre_pl)} do PL)` : ""),
  })), { aria: "Captação líquida por casa" });

  // De onde vem o saldo: compartilhado x so nesta vitrine, proprio x terceiros.
  // Com os dois cortes suprimidos (previdencia) a pagina nem traz a secao.
  const colunas = [["Captação líquida", c => c.captacao_liquida], ["% do PL", null]]
    .concat(SUPRIMIDOS.cnpj ? [] : [
      ["Compartilhados", c => c.captacao_compartilhados],
      ["Só nesta vitrine", c => c.captacao_exclusivos],
    ])
    .concat(SUPRIMIDOS.produto_proprio ? [] : [
      ["Produto próprio", c => c.captacao_propria],
      ["Terceiros", c => c.captacao_terceiros],
    ]);
  const composicaoEl = document.getElementById("flow-composicao");
  if (composicaoEl) {
    const tabelaHtml =
      '<table class="matrix"><thead><tr><th class="rowh">Casa</th>' +
      colunas.map(([t]) => `<th>${t}</th>`).join("") + "</tr></thead><tbody>" +
      flow.casas.map(c => `<tr><th class="rowh">${escapeHtml(c.label)}</th>` +
        colunas.map(([t, f]) => f === null ? celula(pctPl(c.sobre_pl), c.sobre_pl)
          : celula(mm(f(c) || 0), f(c) || 0)).join("") + "</tr>").join("") +
      "</tbody></table>";
    const cortes = [];
    const daBase = flow.casas.find(c => c.platform_id === BASE_ID);
    if (!SUPRIMIDOS.cnpj) {
      cortes.push({
        partes: [
          { chave: "a", rotulo: "Compartilhados com outra vitrine", cor: "var(--comp-2)" },
          { chave: "b", rotulo: "Só nesta vitrine", cor: "var(--comp-1)" },
        ],
        valores: c => ({ a: c.captacao_compartilhados || 0, b: c.captacao_exclusivos || 0 }),
        titulo: daBase
          ? `Na ${base}, o que só ela exibe soma ${reais(daBase.captacao_exclusivos)}; ` +
            `o compartilhado, ${reais(daBase.captacao_compartilhados)}`
          : "Saldo compartilhado e só nesta vitrine",
      });
    }
    if (!SUPRIMIDOS.produto_proprio) {
      cortes.push({
        partes: [
          { chave: "a", rotulo: "Produto de gestora da casa", cor: "var(--comp-1)" },
          { chave: "b", rotulo: "Terceiros", cor: "var(--comp-2)" },
        ],
        valores: c => ({ a: c.captacao_propria || 0, b: c.captacao_terceiros || 0 }),
        titulo: daBase
          ? `Na ${base}, produto próprio soma ${reais(daBase.captacao_propria)}; ` +
            `terceiros, ${reais(daBase.captacao_terceiros)}`
          : "Saldo de produto próprio e de terceiros",
      });
    }
    composicaoEl.innerHTML = '<div class="fig-pair"></div>' +
      `<details><summary>Ver tabela</summary><div class="matrix-scroll">${tabelaHtml}</div></details>`;
    const par = composicaoEl.querySelector(".fig-pair");
    cortes.forEach(corte => {
      const caixa = document.createElement("div");
      const leg = document.createElement("div");
      leg.className = "legend";
      const alvo = document.createElement("div");
      alvo.className = "chart-scroll";
      caixa.append(leg, alvo);
      par.appendChild(caixa);
      legenda(leg, corte.partes);
      figura(alvo, {
        titulo: corte.titulo,
        sub: `R$, ${periodo}; o traço preto marca o saldo.`,
        legenda: leg,
        fonte: fonteInforme,
      });
      empilhadas(alvo, porCaptacao.map(c => ({
        label: c.label, base: c.platform_id === BASE_ID, total: c.captacao_liquida,
        valores: corte.valores(c),
      })), corte.partes, { aria: corte.titulo });
    });
  }

  // Por familia: cada vitrine e o universo (cada fundo uma vez).
  const familias = flow.por_categoria || [];
  const famEl = document.getElementById("flow-familias");
  if (familias.length) {
    famEl.innerHTML = '<table class="matrix"><thead><tr><th class="rowh">Família</th>' +
      flow.casas.map(c => `<th>${escapeHtml(c.label)}</th>`).join("") +
      "<th>Todas as vitrines</th></tr></thead><tbody>" +
      familias.map(l => `<tr><th class="rowh">${escapeHtml(l.categoria)}</th>` +
        flow.casas.map(c => {
          const v = (l.casas || {})[c.platform_id];
          return v && v.fundos ? celula(mm(v.captacao_liquida), v.captacao_liquida)
            : '<td><span class="cell zero">-</span></td>';
        }).join("") +
        celula(`${mm(l.universo.captacao_liquida)} (${l.universo.fundos})`, l.universo.captacao_liquida) +
        "</tr>").join("") + "</tbody></table>";
  } else {
    famEl.innerHTML = '<p class="empty">Sem família de ativo nos fundos com informe.</p>';
  }

  // Tendencia por casa: parcela dos fundos em cada ritmo, 100% empilhada.
  const porCasa = flow.tendencia_por_casa || {};
  const captando = ["acelerando", "estavel", "desacelerando", "virou_entrada"];
  const ritmoEl = document.getElementById("flow-tendencia-casas");
  const ritmos = [
    { chave: "acelerando", rotulo: "Acelerando", cor: "#232323" },
    { chave: "estavel", rotulo: "Estável", cor: "#4d4d4d" },
    { chave: "desacelerando", rotulo: "Desacelerando", cor: "#9c9c9c", tinta: "#0a0a0a" },
    { chave: "virou_entrada", rotulo: "Virou entrada", cor: "#d7d7d7", tinta: "#0a0a0a" },
    { chave: "virou_saida", rotulo: "Virou saída", cor: "#efb4ae", tinta: "#0a0a0a" },
    { chave: "saida_persistente", rotulo: "Saída persistente", cor: "#c6423c" },
    { chave: "sem_serie", rotulo: "Sem série", cor: "#efefef", tinta: "#0a0a0a" },
  ];
  const ritmoTabela =
    '<table class="matrix"><thead><tr><th class="rowh">Casa</th><th>Fundos</th>' +
    "<th>Captando</th><th>Acelerando</th><th>Virou saída</th><th>Saída persistente</th>" +
    "</tr></thead><tbody>" +
    flow.casas.map(c => {
      const t = porCasa[c.platform_id] || {};
      const total = Object.values(t).reduce((a, b) => a + b, 0);
      const soma = captando.reduce((a, k) => a + (t[k] || 0), 0);
      return `<tr><th class="rowh">${escapeHtml(c.label)}</th>
        <td><span class="cell">${total}</span></td>
        <td><span class="cell">${share(soma, total)}</span></td>
        <td><span class="cell">${share(t.acelerando || 0, total)}</span></td>
        <td><span class="cell">${share(t.virou_saida || 0, total)}</span></td>
        <td><span class="cell">${share(t.saida_persistente || 0, total)}</span></td></tr>`;
    }).join("") + "</tbody></table>";
  const temRitmo = flow.casas.some(c => Object.values(porCasa[c.platform_id] || {}).some(v => v > 0));
  if (!temRitmo) {
    ritmoEl.innerHTML = '<p class="empty">Sem série mensal por fundo para medir o ritmo.</p>';
  } else {
    const ritmo = graficoComTabela(ritmoEl);
    ritmo.tabela.innerHTML = ritmoTabela;
    const leg = document.createElement("div");
    leg.className = "legend";
    ritmoEl.insertBefore(leg, ritmo.grafico);
    legenda(leg, ritmos.filter(r => flow.casas.some(c => (porCasa[c.platform_id] || {})[r.chave])));
    const captandoShare = c => {
      const t = porCasa[c.platform_id] || {};
      const total = Object.values(t).reduce((a, b) => a + b, 0);
      return total ? captando.reduce((a, k) => a + (t[k] || 0), 0) / total : null;
    };
    const ritmoLider = lider(flow.casas, captandoShare);
    figura(ritmo.grafico, {
      titulo: ritmoLider.melhor
        ? `${ritmoLider.melhor.label} tem a maior parcela de fundos captando: ` +
          `${parcela(captandoShare(ritmoLider.melhor))}`
        : "Ritmo dos fundos por casa",
      sub: `% dos fundos da vitrine em cada ritmo de captação, ${periodo}.`,
      legenda: leg,
      fonte: fonteInforme,
    });
    cemPorCento(ritmo.grafico, flow.casas.map(c => ({
      label: c.label, base: c.platform_id === BASE_ID, valores: porCasa[c.platform_id] || {},
    })), ritmos, { aria: "Ritmo de captação dos fundos por casa" });
  }

  // Serie por casa: colunas de ganho e perda por competencia.
  const comps = flow.competencias || [];
  const trendEl = document.getElementById("flow-trend");
  if (comps.length) {
    const head = '<tr><th class="rowh">Casa</th>' +
      comps.map(c => `<th>${c.slice(4)}/${c.slice(0, 4)}</th>`).join("") + "</tr>";
    const body = Object.entries(flow.serie_por_casa).map(([pid, serie]) => {
      const byComp = Object.fromEntries(serie.map(m => [m.competencia, m]));
      const casa = (flow.casas.find(c => c.platform_id === pid) || {}).label || pid;
      const cells = comps.map(c => {
        const m = byComp[c];
        if (!m) return '<td><span class="cell zero">-</span></td>';
        const classe = m.media_diaria > 0 ? "cell pos" : "cell neg";
        return `<td title="${m.dias} pregões observados"><span class="${classe}">${mm(m.media_diaria)}/dia</span></td>`;
      }).join("");
      return `<tr><th class="rowh">${casa}</th>${cells}</tr>`;
    }).join("");
    const tendencia = graficoComTabela(trendEl);
    tendencia.tabela.innerHTML =
      `<table class="matrix"><thead>${head}</thead><tbody>${body}</tbody></table>`;
    const ultima = comps[comps.length - 1];
    const noUltimo = flow.casas.map(c => {
      const m = (flow.serie_por_casa[c.platform_id] || []).find(x => x.competencia === ultima);
      return { platform_id: c.platform_id, label: c.label, v: m ? m.media_diaria : null };
    });
    const tl = lider(noUltimo, c => c.v);
    const mesBR = competenciaBR(ultima);
    figura(tendencia.grafico, {
      titulo: tl.melhor
        ? `Em ${mesBR}, ${tl.melhor.label} tem a maior captação média por pregão: ${reais(tl.melhor.v)}/dia`
        : "Captação média por pregão, por competência",
      sub: `R$ por pregão, por competência (${comps.map(competenciaBR).join(", ")}); a última em curso.`,
      fonte: fonteInforme + " Média diária porque o mês corrente tem menos pregões.",
    });
    const ordemCasas = porCaptacao.filter(c => flow.serie_por_casa[c.platform_id]);
    colunasPorCasa(tendencia.grafico, ordemCasas.map(c => {
      const porComp = Object.fromEntries((flow.serie_por_casa[c.platform_id] || []).map(m => [m.competencia, m]));
      return {
        label: c.label, cor: corDaCasa(c.platform_id), base: c.platform_id === BASE_ID,
        pontos: comps.map(k => ({
          rotulo: MESES_BR[Number(k.slice(4)) - 1],
          valor: porComp[k] ? porComp[k].media_diaria : null,
          dica: porComp[k]
            ? `${c.label} - ${competenciaBR(k)}: ${reais(porComp[k].media_diaria)}/dia, ${porComp[k].dias} pregões`
            : "",
        })),
      };
    }), { fmt: v => reais(v), fmtEixo: v => (v === 0 ? "0" : numBR(v / 1e6, 0) + " mi"), aria: "Captação média diária por competência" });
  }

  // Ticket por cotista novo: a CVM publica o fluxo do fundo inteiro, e so esse
  // numero mostra ao leitor quando a captacao nao tem cara de varejo.
  const mil = v => "R$ " + Math.round(v / 1000).toLocaleString("pt-BR") + " mil";
  // Cada lista de fundos e uma tabela: Fundo | Captacao | Cotistas | Casas.
  const lista = (tituloLista, fundos, classe) => {
    if (!fundos || !fundos.length) return "";
    const cabeca = "<th>Fundo</th><th class=\"num\">Captação</th>" +
      (SUPRIMIDOS.cotistas ? "" : "<th class=\"num\">Cotistas</th><th class=\"num\">Por cotista novo</th>") +
      "<th>Casas</th>";
    const linhas = fundos.map(f => `<tr><td>${escapeHtml(f.nome)}</td>` +
      `<td class="num"><span class="${classe}">${mm(f.captacao_liquida)}</span></td>` +
      (SUPRIMIDOS.cotistas ? "" :
        `<td class="num">${int(f.variacao_cotistas)}</td>` +
        `<td class="num">${f.ticket_por_cotista ? mil(f.ticket_por_cotista) : "–"}</td>`) +
      `<td>${(f.vitrines || []).map(rotulo).map(escapeHtml).join(", ")}</td></tr>`).join("");
    return `<h4 style="font-size:13px;font-weight:700;margin:18px 0 4px">${escapeHtml(tituloLista)}</h4>
      <div class="table-scroll"><table><thead><tr>${cabeca}</tr></thead><tbody>${linhas}</tbody></table></div>`;
  };
  const criterio = flow.com_cotistas === false ? "dinheiro" : "dinheiro e cotistas";

  const g = SUPRIMIDOS.cnpj ? {} : (flow.grade || {});
  const grade = g.available ? `
    <h4 style="font-size:13px;font-weight:700;margin:20px 0 6px">Oportunidade de grade</h4>
    <div class="table-scroll"><table><thead><tr><th>Fundos captando varejo fora da XP</th>
      <th class="num">Fundos</th><th class="num">Captação</th><th class="num">Cotistas</th></tr></thead><tbody>
      <tr><td>Todos</td><td class="num">${inteiro(g.total)}</td><td class="num">–</td><td class="num">–</td></tr>
      <tr><td>Produto próprio do concorrente (não se transfere)</td><td class="num">${inteiro(g.total_produto_do_concorrente)}</td><td class="num">–</td><td class="num">–</td></tr>
      <tr><td><strong>Transferíveis</strong></td><td class="num"><strong>${inteiro(g.total_transferiveis)}</strong></td>
        <td class="num"><strong>${mm(g.captacao_transferivel)}</strong></td><td class="num">${int(g.cotistas_transferivel)}</td></tr>
      <tr><td>· de gestora que a XP já distribui</td><td class="num">${inteiro(g.total_operacionais)}</td><td class="num">–</td><td class="num">–</td></tr>
      <tr><td>· exigem abrir gestora</td><td class="num">${inteiro(g.total_comerciais)}</td><td class="num">–</td><td class="num">–</td></tr>
    </tbody></table></div>
    ${lista("Mais varejo: quem trouxe mais cotistas", g.mais_varejo, "pos")}` : "";

  const listasEl = document.getElementById("flow-lists");
  listasEl.innerHTML =
    '<div class="chart-scroll flow-extremos"></div>' +
    lista(`Maiores captações - fundos ganhando ${criterio}`, flow.top_captacao, "pos") +
    lista(`Maiores resgates - fundos perdendo ${criterio}`, flow.top_resgate, "neg") +
    lista(`Na vitrine da ${flow.baseline_label || "XP"}, perdendo ${criterio}`,
      flow.baseline_perdendo, "neg") +
    lista("Acelerando - captando mais por dia que antes", flow.acelerando, "pos") +
    lista("Viraram saída - captavam e passaram a perder", flow.virou_saida, "neg") +
    grade;
  // Os extremos como barras divergentes: quem mais ganhou e quem mais perdeu.
  const extremos = (flow.top_captacao || []).slice(0, 5)
    .concat((flow.top_resgate || []).slice(0, 5).reverse());
  const extremosEl = listasEl.querySelector(".flow-extremos");
  if (extremos.length) {
    figura(extremosEl, {
      titulo: `O fundo que mais captou levou ${reais(extremos[0].captacao_liquida)}; ` +
        `o que mais perdeu, ${reais(extremos[extremos.length - 1].captacao_liquida)}`,
      sub: `Cinco maiores captações e cinco maiores resgates, R$, ${periodo}.`,
      fonte: fonteInforme,
    });
    barrasDivergentes(extremosEl, extremos.map(f => ({
      label: f.nome, value: f.captacao_liquida,
      color: f.captacao_liquida >= 0 ? "var(--comp-1)" : "var(--comp-2)",
      display: reais(f.captacao_liquida),
    })), { maxRotulo: 46, rowH: 26, barH: 12, aria: "Fundos com mais captação e mais resgate" });
  } else {
    extremosEl.remove();
  }

  const fora = (flow.excluded || []).map(e => `${e.label} (${e.reason})`);
  notas(document.getElementById("flow-coverage"), [
    flow.total_estruturais
      ? `Fora das somas: ${inteiro(flow.total_estruturais)} fundos com movimento estrutural ` +
        "(fluxo maior que o patrimônio, ou fundo esvaziado) - troca de veículo, não demanda"
      : "",
    fora.length ? `Fora da análise: ${fora.join("; ")}` : "",
  ]);
})(DATA.flow);

(function renderPerformance(perf3, perf12, custo, mandato) {
  // Mesma guarda do bloco de captacao: bloco indisponivel REMOVE a secao, em
  // vez de deixar um titulo com o corpo vazio. A janela mais longa vem
  // primeiro: 12 meses separa quem ficou para tras, 3 meses e a leitura recente.
  const section = document.getElementById("perf-section");
  const janelas = [perf12, perf3].filter(b => b && b.available);
  if (!janelas.length) { section.remove(); return; }

  const pct = v => (v === null || v === undefined) ? "-" : numBR(v) + "%";
  const pp = v => (v === null || v === undefined) ? "-" : (v > 0 ? "+" : "") + numBR(v) + "pp";
  const share = v => (v === null || v === undefined) ? "-" : Math.round(v * 100) + "%";
  const base = rotuloDaBase();
  // Bloco gravado antes do descritor de janela: o prazo sai do periodo.
  const janelaDe = b => b.janela || {
    rotulo: `${(b.periodo.meses_lidos || []).length || 3} meses`,
    de: b.periodo.de, ate: b.periodo.ate, pregoes: b.periodo.pregoes,
    base: "cota do primeiro pregão do período",
  };
  const fonteInforme = "Fonte: informe diário de fundos da CVM. Cota ex-provento; " +
    "mediana da categoria no universo oficial, no mesmo prazo.";

  function desenhar(perf) {
    const janela = janelaDe(perf);
    const universo = perf.universo || {};
    document.querySelectorAll(".perf-janela-rotulo").forEach(e => {
      e.textContent = "em " + janela.rotulo;
    });
    const nota = document.getElementById("perf-nota");
    if (nota) {
      nota.innerHTML = `<strong>Prazo: ${escapeHtml(janela.rotulo)}</strong>, ` +
        `${periodoBR(janela.de, janela.ate)}` +
        (janela.pregoes ? ` · ${janela.pregoes.toLocaleString("pt-BR")} pregões` : "") +
        (janela.base ? ` · base na ${escapeHtml(janela.base)}` : "") +
        ` · referência: mediana da categoria (${inteiro(universo.fundos_com_serie)} fundos, ` +
        `${universo.categorias_com_referencia} categorias).`;
    }

    // Os tiles repetiam os numeros do grafico; o dumbbell leva os tres e a
    // distancia entre eles. A cobertura de cada casa vai para a nota da linha.
    const tiles = document.getElementById("perf-tiles");
    tiles.innerHTML = "";

    // Dumbbell: mediana (traco), ponderada por PL (ponto cheio), por cotistas
    // (ponto vazado). Ponto, e nao barra: tres numeros entre 11% e 13% viravam
    // tres barras iguais, e a escala cortada da versao anterior mentia o tamanho.
    const chart = document.getElementById("perf-chart");
    const legendaEl = document.getElementById("perf-legend");
    const series = [
      { chave: "mediana", rotulo: "Mediana dos fundos", forma: "traco", cor: "var(--text-secondary)" },
      { chave: "por_pl", rotulo: "Ponderada por PL", forma: "cheio" },
      { chave: "por_cotista", rotulo: "Ponderada por cotistas", forma: "vazado" },
    ].filter(s => !(SUPRIMIDOS.cotistas && s.chave === "por_cotista"));
    legenda(legendaEl, series.map(s => ({
      rotulo: s.rotulo, forma: s.forma === "cheio" ? "ponto" : s.forma,
      cor: s.cor || "var(--text-primary)",
    })));
    const ordem = lider(perf.casas, c => c.por_pl);
    const topo = ordem.melhor;
    figura(chart, {
      titulo: !topo ? `Retorno em ${janela.rotulo}`
        : topo.platform_id === BASE_ID
          ? `Em ${janela.rotulo}, ${base} rende mais ponderada por PL: ${pct1(topo.por_pl)}`
          : `Em ${janela.rotulo}, ${topo.label} rende mais ponderada por PL (${pct1(topo.por_pl)}); ` +
            `${base} é a ${ordinalF(ordem.posicaoBase || ordem.total)} de ${ordem.total}`,
      sub: `%, ${periodoBR(janela.de, janela.ate)}` +
        (SUPRIMIDOS.cotistas ? "." : "; à direita, o gap entre real médio e cotista médio."),
      legenda: legendaEl,
      fonte: fonteInforme,
    });
    const linhas = perf.casas.slice()
      .sort((a, b) => (ok(b.por_pl) ? b.por_pl : -1e9) - (ok(a.por_pl) ? a.por_pl : -1e9))
      .map(c => ({
        label: c.label, cor: corDaCasa(c.platform_id), base: c.platform_id === BASE_ID,
        valores: { mediana: c.mediana, por_pl: c.por_pl, por_cotista: c.por_cotista },
        gap: c.gap, com_serie: c.com_serie, vitrine: c.vitrine,
      }));
    dumbbell(chart, linhas, series, {
      fmt: v => numBR(v) + "%", fmtEixo: v => numBR(v, Number.isInteger(v) ? 0 : 1) + "%",
      nota: l => (SUPRIMIDOS.cotistas
        ? `${inteiro(l.com_serie)} de ${inteiro(l.vitrine)} com série`
        : ok(l.gap) ? `gap ${pp(l.gap)}` : ""),
      aria: `Retorno em ${janela.rotulo} por casa, nas três leituras`,
    });

    // Massa mal alocada: quanto do dinheiro esta abaixo da mediana da propria
    // categoria. Grade de 100 pontos por casa (McKinsey), um ponto = 1% do PL.
    const massaEl = document.getElementById("perf-massa");
    const massa = graficoComTabela(massaEl);
    massa.tabela.innerHTML =
      '<table class="matrix"><thead><tr><th class="rowh">Casa</th>' +
      "<th>Fundos abaixo da mediana</th><th>% dos fundos</th><th>% do PL</th>" +
      "<th>Índice PL</th></tr></thead><tbody>" +
      perf.casas.map(c => {
        const m = c.alocacao_abaixo_da_mediana || {};
        const classe = !ok(m.indice_pl) ? "cell" : m.indice_pl < 1 ? "cell pos" : "cell neg";
        return `<tr><th class="rowh">${c.label}</th>
          <td><span class="cell">${m.fundos} de ${m.de}</span></td>
          <td><span class="cell">${share(m.share_fundos)}</span></td>
          <td><span class="cell">${share(m.share_pl)}</span></td>
          <td><span class="${classe}">${numBR(m.indice_pl)}</span></td></tr>`;
      }).join("") + "</tbody></table>";
    const comMassa = perf.casas.filter(c => c.alocacao_abaixo_da_mediana && ok(c.alocacao_abaixo_da_mediana.share_pl));
    const massaBase = comMassa.find(c => c.platform_id === BASE_ID);
    const menorMassa = lider(comMassa, c => c.alocacao_abaixo_da_mediana.share_pl, false).melhor;
    figura(massa.grafico, {
      titulo: massaBase
        ? `Na ${base}, ${share(massaBase.alocacao_abaixo_da_mediana.share_pl)} do PL está em fundos ` +
          `abaixo da mediana da categoria` +
          (menorMassa && menorMassa.platform_id !== BASE_ID
            ? `; na ${menorMassa.label}, ${share(menorMassa.alocacao_abaixo_da_mediana.share_pl)}` : "")
        : "Quanto do PL está em fundos abaixo da mediana da categoria",
      sub: `% do PL em fundos abaixo da mediana da própria categoria, ${janela.rotulo}; ` +
        "1 ponto = 1% do PL. Índice abaixo de 1: o dinheiro evita os piores.",
      fonte: fonteInforme,
    });
    massa.grafico.innerHTML = '<div class="waffles">' + comMassa.map(c => {
      const m = c.alocacao_abaixo_da_mediana;
      const cheios = Math.round(m.share_pl * 100);
      const pontos = Array.from({ length: 100 }, (_, i) =>
        `<i${i < cheios ? ` style="background:${corDaCasa(c.platform_id)}"` : ""}></i>`).join("");
      const eBase = c.platform_id === BASE_ID ? " base" : "";
      return `<div class="waffle" title="${escapeHtml(c.label)}: ${share(m.share_pl)} do PL, ${share(m.share_fundos)} dos fundos">
        <p class="nome${eBase}">${escapeHtml(c.label)}</p>
        <div class="num">${share(m.share_pl)}</div>
        <p class="sub">do PL · ${share(m.share_fundos)} dos fundos · índice ${numBR(m.indice_pl)}</p>
        <div class="grade" aria-hidden="true">${pontos}</div></div>`;
    }).join("") + "</div>";

    // Fundos expostos: deduplicado por CNPJ, com as casas numa coluna. Listado
    // por casa, o mesmo produto aparecia varias vezes seguidas com numeros
    // identicos - a CVM publica por fundo, nao por distribuidor.
    const expostosEl = document.getElementById("perf-expostos");
    if (expostosEl) {
      const porCnpj = new Map();
      const rotuloCasa = {};
      (perf.casas || []).forEach(c => { rotuloCasa[c.platform_id] = c.label; });
      Object.entries(perf.onde_esta_a_gente || {}).forEach(([pid, fundos]) => {
        (fundos || []).forEach(f => {
          if (f.excesso === null || f.excesso === undefined || f.excesso >= 0) return;
          const chave = f.cnpj || (pid + ":" + f.nome);
          const casa = rotuloCasa[pid] || pid;
          if (!porCnpj.has(chave)) porCnpj.set(chave, Object.assign({}, f, { casas: [casa] }));
          else if (!porCnpj.get(chave).casas.includes(casa)) porCnpj.get(chave).casas.push(casa);
        });
      });
      const expostos = Array.from(porCnpj.values())
        .sort((a, b) => b.cotistas - a.cotistas).slice(0, 10);
      expostosEl.innerHTML = expostos.length
        ? '<table class="matrix"><thead><tr><th class="rowh">Fundo</th><th>Cotistas</th>' +
          `<th>Retorno em ${janela.rotulo}</th><th>Mediana da categoria</th><th>Atrás em</th><th>Casas</th>` +
          "</tr></thead><tbody>" +
          expostos.map(f => `<tr><th class="rowh">${f.nome || f.cnpj}</th>
            <td><span class="cell">${(f.cotistas || 0).toLocaleString("pt-BR")}</span></td>
            <td><span class="cell">${pct(f.retorno)}</span></td>
            <td><span class="cell">${pct(f.mediana_categoria)}</span></td>
            <td><span class="cell neg">${numBR(f.excesso)}pp</span></td>
            <td><span class="cell">${f.casas.join(", ")}</span></td></tr>`).join("") +
          "</tbody></table>"
        : '<p class="card-sub">Nenhum fundo de destaque rendendo abaixo da própria categoria.</p>';
    }
  }

  // Seletor de prazo: so aparece quando as duas janelas estao disponiveis.
  const seletor = document.getElementById("perf-janelas");
  if (seletor && janelas.length > 1) {
    seletor.innerHTML = janelas.map((b, i) =>
      `<button type="button" data-i="${i}" aria-pressed="${i === 0}">` +
      `${escapeHtml(janelaDe(b).rotulo)}</button>`).join("");
    seletor.addEventListener("click", ev => {
      const botao = ev.target.closest("button[data-i]");
      if (!botao) return;
      seletor.querySelectorAll("button").forEach(
        b => b.setAttribute("aria-pressed", String(b === botao)));
      desenhar(janelas[Number(botao.dataset.i)]);
    });
  } else if (seletor) {
    seletor.remove();
  }
  desenhar(janelas[0]);

  // Custo: indice mediano, nao taxa mediana - um ponto por casa contra a
  // referencia de 1,00x (a mediana da categoria de cada fundo).
  const bloco = document.getElementById("custo-bloco");
  if (custo && custo.available && bloco) {
    const partes = graficoComTabela(bloco);
    partes.tabela.innerHTML =
      '<table class="matrix"><thead><tr><th class="rowh">Casa</th>' +
      "<th>Índice mediano</th><th>Caros</th><th>Baratos</th>" +
      "<th>Sem entrada/saída</th><th>Resgate até D+1</th></tr></thead><tbody>" +
      custo.casas.map(c => `<tr><th class="rowh">${c.label}</th>
        <td><span class="cell">${c.indice_mediano === null ? "-" : numBR(c.indice_mediano) + "x"}</span></td>
        <td><span class="cell">${share(c.share_caro)}</span></td>
        <td><span class="cell">${share(c.share_barato)}</span></td>
        <td><span class="cell">${share(c.share_sem_entrada_ou_saida)}</span></td>
        <td><span class="cell">${share(c.share_liquidez_imediata)}</span></td></tr>`).join("") +
      "</tbody></table>";
    const barata = lider(custo.casas, c => c.indice_mediano, false);
    const daBase = custo.casas.find(c => c.platform_id === BASE_ID);
    figura(partes.grafico, {
      titulo: !barata.melhor ? "Custo relativo à categoria"
        : barata.melhor.platform_id === BASE_ID
          ? `${base} tem o menor custo relativo à categoria: ${numBR(barata.melhor.indice_mediano)}x`
          : `${barata.melhor.label} tem o menor custo relativo (${numBR(barata.melhor.indice_mediano)}x)` +
            (daBase && ok(daBase.indice_mediano) ? `; ${base}, ${numBR(daBase.indice_mediano)}x` : ""),
      sub: "Taxa de administração ÷ mediana da categoria (1,00x); menor é mais barato.",
      fonte: "Fonte: extrato de informações de fundos da CVM. Vale a diferença entre as casas, " +
        "não a distância de 1,00x.",
    });
    dumbbell(partes.grafico, custo.casas.slice()
      .sort((a, b) => (ok(a.indice_mediano) ? a.indice_mediano : 1e9) - (ok(b.indice_mediano) ? b.indice_mediano : 1e9))
      .map(c => ({
        label: c.label, cor: corDaCasa(c.platform_id), base: c.platform_id === BASE_ID,
        valores: { indice: c.indice_mediano }, caro: c.share_caro, barato: c.share_barato,
      })), [{ chave: "indice", rotulo: "Índice mediano", forma: "cheio" }], {
      ref: 1, refRotulo: "mediana da categoria",
      fmt: v => numBR(v) + "x", fmtEixo: v => numBR(v, 1) + "x",
      nota: l => `${share(l.caro)} caros · ${share(l.barato)} baratos`,
      aria: "Custo relativo à categoria por casa",
    });
  } else if (bloco) {
    bloco.innerHTML = `<p class="card-sub">Custo não calculado: ${(custo || {}).reason || "sem base de mercado"}</p>`;
  }

  // Mandato: share por dimensao, com o indice contra as demais casas - mapa de
  // calor divergente (cinza acima das concorrentes, rosado abaixo), e a
  // seta escrita ao lado para a cor nunca ser a unica pista.
  const mbloco = document.getElementById("mandato-bloco");
  if (mandato && mandato.available && mbloco) {
    const dims = [];
    const porCasa = {};
    (mandato.linhas || []).forEach(l => {
      if (!dims.some(d => d[0] === l.chave)) dims.push([l.chave, l.rotulo]);
      (porCasa[l.platform_id] = porCasa[l.platform_id] || {})[l.chave] = l;
    });
    // O rotulo mantem "autorizado". Encurtar a coluna apagando justamente essa
    // palavra transformava "credito privado AUTORIZADO pelo regulamento" em
    // "credito privado", que o leitor entende como exposicao carregada - a
    // unica leitura que este bloco nao pode sustentar, porque mediria carteira
    // e exigiria a CDA. A ressalva ficava so no paragrafo acima da tabela.
    const head = '<tr><th class="rowh">Casa</th>' +
      dims.map(([, r]) => `<th>${r}</th>`).join("") + "</tr>";
    const body = (mandato.casas || []).map(c => {
      const cells = dims.map(([chave]) => {
        const l = (porCasa[c.platform_id] || {})[chave];
        if (!l) return '<td><span class="cell zero">s/dado</span></td>';
        const fundo = l.aposta === "acima" ? "background:#d7d7d7"
          : l.aposta === "abaixo" ? "background:#f6dcd9" : "";
        const seta = l.aposta === "acima" ? " ▲" : l.aposta === "abaixo" ? " ▼" : "";
        const idx = l.so_ela ? "só ela" : numBR(l.indice) + "x";
        return `<td title="${share(l.share_pares)} nas concorrentes (${idx})">
          <span class="cell" style="${fundo}">${share(l.share)}${seta}</span></td>`;
      }).join("");
      const eBase = c.platform_id === BASE_ID ? ' class="base"' : "";
      return `<tr${eBase}><th class="rowh">${c.label}</th>${cells}</tr>`;
    }).join("");
    mbloco.innerHTML = `<table class="matrix"><thead>${head}</thead><tbody>${body}</tbody></table>`;
    const apostaDaBase = a => dims.filter(([chave]) => ((porCasa[BASE_ID] || {})[chave] || {}).aposta === a).length;
    const acima = apostaDaBase("acima"), abaixo = apostaDaBase("abaixo");
    const algumaSeAfasta = (mandato.linhas || []).some(l => l.aposta === "acima" || l.aposta === "abaixo");
    figura(mbloco, {
      titulo: acima || abaixo
        ? `A vitrine da ${base} autoriza mais que as concorrentes em ${acima} de ${dims.length} ` +
          `dimensões e menos em ${abaixo}`
        : algumaSeAfasta
          ? `A vitrine da ${base} autoriza o mesmo que as concorrentes nas ${dims.length} dimensões`
          : `Nenhuma vitrine se afasta das concorrentes no que o regulamento autoriza`,
      sub: "% da vitrine com cada autorização no regulamento; ▲ acima das concorrentes, ▼ abaixo.",
      fonte: "Fonte: extrato de informações de fundos da CVM.",
    });
  } else if (mbloco) {
    mbloco.innerHTML = `<p class="card-sub">Mandato não calculado: ${(mandato || {}).reason || "sem base de mercado"}</p>`;
  }

  const fora = janelas.flatMap(b => b.excluded || []).concat((custo || {}).excluded || []);
  const vistos = new Set();
  const linhas = fora.filter(e => {
    const chave = e.platform_id + "|" + e.reason;
    if (vistos.has(chave)) return false;
    vistos.add(chave);
    return true;
  });
  if (linhas.length) {
    notas(document.getElementById("perf-coverage"),
      linhas.map(e => `Fora destes blocos: ${e.label} (${e.reason})`));
  }
})(DATA.performance, DATA.performance_12m, DATA.custo, DATA.mandato);


(function renderRestricted(block) {
  const mount = document.getElementById("table-restricted");
  const rows = (block && block.platforms || []).filter(p => p.total_snapshot > 0);
  if (!rows.length || !block.total) {
    mount.innerHTML = '<p class="empty">Nenhum fundo restrito/exclusivo identificado ' +
      'nesta coleta. A detecção depende da base CVM importada e das flags das casas.</p>';
    return;
  }
  table2(mount,
    ["Casa", "Restritos", "% do total coletado", "Na vitrine", "Principais motivos"],
    rows.map(p => {
      const reasons = Object.entries(p.by_reason || {})
        .sort((a, b) => b[1] - a[1])
        .map(([reason, count]) => `${reason} (${count})`).join("; ");
      return [
        p.label,
        p.restricted.toLocaleString("pt-BR"),
        `${Math.round(100 * p.restricted / p.total_snapshot)}%`,
        String(p.on_listing || 0),
        reasons || "–",
      ];
    }));
})(DATA.restricted);

// --- Amplitude por classe de ativo -------------------------------------------
// Um ponto por casa em cada classe, no mesmo eixo; o traco cinza liga a menor
// a maior prateleira da classe - onde a XP fica dentro dessa faixa e a leitura.
(function renderClasses() {
  const mount = document.getElementById("chart-classes");
  const legendaEl = document.getElementById("legend-classes");
  legendaDeCasas(legendaEl, usable);
  const classes = DATA.class_order || [];
  if (!usable.length || !classes.length) {
    mount.innerHTML = VAZIO;
    return;
  }
  figura(mount, {
    titulo: manchetePorLinhas(classes, usable, (c, p) => p.by_class[c] || 0,
      "classe de ativo", "classes de ativo") || "Fundos por classe de ativo em cada vitrine",
    sub: "Fundos da vitrine por classe; um ponto por casa.",
    legenda: legendaEl,
    fonte: "Fonte: vitrines das casas; classe pela classificação ANBIMA do registro da CVM.",
  });
  dumbbell(mount, classes.map(c => ({
    label: c, valores: Object.fromEntries(usable.map(p => [p.platform_id, p.by_class[c] || 0])),
  })), usable.map(p => ({
    chave: p.platform_id, rotulo: p.label, forma: "cheio", cor: corDaCasa(p.platform_id),
    base: p.platform_id === BASE_ID,
  })), { doZero: true, fmt: inteiro, fmtEixo: inteiro, rowH: 28, aria: "Número de fundos por classe de ativo em cada plataforma" });
  table(
    document.getElementById("table-classes"),
    [{ key: "label", label: "Plataforma" },
     ...classes.map(c => ({ key: c, label: c, num: true })),
     { key: "listing_funds", label: "Vitrine", num: true }],
    usable.map(p => ({ label: p.label, listing_funds: p.listing_funds,
      ...Object.fromEntries(classes.map(c => [c, p.by_class[c] || 0])) }))
  );
  notaFora(document.getElementById("table-classes"));
})();

// --- Profundidade: gestoras representadas -----------------------------------
(function renderGestoras() {
  const mount = document.getElementById("chart-managers");
  const linhas = usable.map(p => ({
    label: p.label, value: p.distinct_managers, color: corDaCasa(p.platform_id),
    base: p.platform_id === BASE_ID,
  })).sort((a, b) => b.value - a.value);
  // Sem gestora em vitrine nenhuma (a previdencia nao publica), nao ha
  // manchete nem grafico - "tem mais gestoras: 0" nao diz nada.
  if (!linhas.some(l => l.value > 0)) {
    mount.innerHTML = '<p class="empty">Nenhuma vitrine publica a gestora dos fundos.</p>';
  } else {
    const daBase = linhas.find(l => l.base);
    figura(mount, {
      titulo: `${linhas[0].label} tem mais gestoras na vitrine: ${inteiro(linhas[0].value)}` +
        (daBase && daBase !== linhas[0] ? `; ${daBase.label}, ${inteiro(daBase.value)}` : ""),
      sub: "Gestoras distintas com fundo na vitrine.",
      fonte: "Fonte: vitrines das casas; gestora pelo registro da CVM.",
    });
    barrasH(mount, linhas, { aria: "Gestoras distintas representadas por plataforma" });
  }
  notaFora(mount);
  // Quais gestoras concentram a prateleira - onde a casa realmente aposta.
  table(
    document.getElementById("table-top-managers"),
    [{ key: "platform", label: "Plataforma" }, { key: "manager", label: "Gestora" },
     { key: "count", label: "Fundos", num: true }],
    usable.flatMap(p =>
      (p.top_managers || []).slice(0, 5).map(m => ({
        platform: p.label, manager: m.name, count: m.count,
      }))
    )
  );
})();

// --- Faixas de aplicacao minima ---------------------------------------------
// O veredito vem pronto do Python (`aggregate.leitura_do_ticket`) para as
// quatro superficies direm a mesma coisa. Sem ele o bloco era um grafico de
// barras sem conclusao: mostrava a forma de cada prateleira e parava ai.
const lt = DATA.leitura_ticket;
const ltEl = document.getElementById("leitura-ticket");
if (lt && ltEl) {
  const teto = "R$ " + lt.teto.toLocaleString("pt-BR");
  // Sem amostra do minimo da propria vitrine, o que falta e dado da coleta:
  // nunca concluir "a casa nao tem fundo barato" (ver leitura_do_ticket).
  ltEl.innerHTML = lt.sem_base
    ? `<strong>Sem base para o ticket da ${lt.baseline_label}</strong>: mínimo coletado em
       ${lt.baseline_amostra} fundos (precisa de ${lt.amostra_minima}) · ${lt.melhor_label}:
       ${lt.melhor_varejo} abaixo de ${teto}.`
    : lt.baseline_varejo === 0
    ? `<strong>${lt.baseline_label}: nenhum fundo abaixo de ${teto}</strong> ·
       ${lt.melhor_label}: ${lt.melhor_varejo}. Falta a porta de entrada de varejo.`
    : `<strong>${lt.baseline_label}: ${lt.baseline_varejo} fundos abaixo de ${teto}</strong> ·
       ${lt.melhor_label}: ${lt.melhor_varejo}.`;
}

(function renderFaixas() {
  const withMins = usable.filter(p => Object.keys(p.min_bands).length);
  const mount = document.getElementById("chart-bands");
  const legendaEl = document.getElementById("legend-bands");
  // Faixa e ordenada (varejo -> alta renda): rampa ordinal de um matiz, do
  // passo que ainda aparece no branco ao mais escuro.
  const passos = ["#bcbcbc", "#9c9c9c", "#777777", "#4d4d4d", "#232323"];
  const faixas = (DATA.band_order || []).map((b, i, todas) => {
    const passo = Math.round((i / Math.max(1, todas.length - 1)) * (passos.length - 1));
    // Branco so a partir do passo em que ele passa de 4,5:1 sobre o fundo.
    return { chave: b, rotulo: b, cor: passos[passo], tinta: passo >= 2 ? "#ffffff" : "#121212" };
  });
  if (!withMins.length) {
    // Sem aplicacao minima publicada em vitrine nenhuma: nem legenda nem moldura.
    mount.innerHTML = VAZIO;
  } else {
    legenda(legendaEl, faixas);
    const varejo = p => {
      const total = Object.values(p.min_bands).reduce((a, b) => a + b, 0);
      return total ? (p.min_bands[faixas[0] && faixas[0].chave] || 0) / total : null;
    };
    const maisVarejo = lider(withMins, varejo).melhor;
    figura(mount, {
      titulo: maisVarejo
        ? `${maisVarejo.label} tem a maior parcela de fundos na menor faixa de aplicação mínima: ` +
          `${parcela(varejo(maisVarejo))}`
        : "Aplicação mínima por casa",
      sub: "% dos fundos da vitrine por faixa de aplicação mínima.",
      legenda: legendaEl,
      fonte: "Fonte: vitrines das casas (aplicação mínima publicada).",
    });
    cemPorCento(mount, withMins.map(p => ({
      label: p.label, base: p.platform_id === BASE_ID, valores: p.min_bands,
    })), faixas, { aria: "Distribuição de aplicação mínima por plataforma" });
  }
  notaFora(mount);
  table(
    document.getElementById("table-bands"),
    [{ key: "label", label: "Plataforma" },
     ...DATA.band_order.map(b => ({ key: b, label: b, num: true })),
     { key: "lowest", label: "Menor mínimo", num: true }],
    withMins.map(p => ({
      label: p.label,
      lowest: p.lowest_min === null ? "-" : "R$ " + p.lowest_min.toLocaleString("pt-BR"),
      ...Object.fromEntries(DATA.band_order.map(b => [b, p.min_bands[b] || 0])),
    }))
  );
})();

// --- Exclusividade ----------------------------------------------------------
// Sem o container quando o modulo suprime a comparacao por CNPJ: o HTML fixo
// ja traz o motivo no lugar do grafico.
const exclusiveEl = document.getElementById("chart-exclusive");
if (exclusiveEl) {
  const linhas = usable.map(p => ({
    label: p.label, value: p.exclusive_count, color: corDaCasa(p.platform_id),
    base: p.platform_id === BASE_ID,
  })).sort((a, b) => b.value - a.value);
  if (linhas.length) {
    figura(exclusiveEl, {
      titulo: `${linhas[0].label} tem mais fundos que só ela exibe: ${inteiro(linhas[0].value)}`,
      sub: "Fundos de varejo (por CNPJ) na vitrine de uma casa só.",
      fonte: "Fonte: vitrines das casas, cruzadas por CNPJ.",
    });
  }
  barrasH(exclusiveEl, linhas, { aria: "Fundos exclusivos de cada plataforma, por CNPJ" });
  notaFora(exclusiveEl);
}

// --- Radar CVM --------------------------------------------------------------
// A previdencia nao roda o radar (ele acompanha as gestoras da watchlist, no
// monitor de fundos) e a pagina dela nao tem o painel.
const cvm = DATA.cvm;
if (!document.getElementById("cvm-section")) {
  // nada a desenhar
} else if (cvm.available && cvm.houses && cvm.houses.length) {
  // A legenda ficou sem uso: cada barra ja traz o nome da casa na linha.
  document.getElementById("legend-cvm").innerHTML = "";
  const mount = document.getElementById("chart-cvm");
  const linhas = cvm.houses.map(h => ({
    label: h.label, value: h.pre_operational, color: corDaCasa(h.house),
    base: h.house === BASE_ID, extra: `de ${inteiro(h.total)} registrados`,
  })).sort((a, b) => b.value - a.value);
  figura(mount, {
    titulo: `${linhas[0].label} tem mais fundos em fase pré-operacional: ${inteiro(linhas[0].value)}`,
    sub: `Registrados e ainda não lançados; ao lado, o total ativo (sem os cancelados). Cadastro de ${dataBR(cvm.collected_at)}.`,
    fonte: "Fonte: cadastro de fundos da CVM.",
  });
  barrasH(mount, linhas, { aria: "Fundos em fase pré-operacional por casa" });
  table(
    document.getElementById("table-cvm"),
    [{ key: "label", label: "Casa" },
     { key: "total", label: "Ativos", num: true },
     { key: "pre_operational", label: "Pré-operacionais", num: true }],
    cvm.houses
  );
  table(
    document.getElementById("table-pre-op"),
    [{ key: "name", label: "Fundo" }, { key: "house_label", label: "Casa" },
     { key: "manager", label: "Gestor" }, { key: "registered_at", label: "Registro" }],
    cvm.pre_operational_funds || []
  );
} else {
  const cvmSection = document.getElementById("cvm-section");
  (cvmSection.querySelector(".panel-body") || cvmSection).innerHTML =
    `<p class="card-sub">Fundos registrados por gestoras ligadas às casas monitoradas.</p>
     <div class="note-box">Extrato da CVM indisponível nesta execução: ` +
    `${escapeHtml(cvm.error || "fonte não coletada")}.</div>`;
}

// --- Saude da coleta --------------------------------------------------------
table(
  document.getElementById("table-sources"),
  [{ key: "label", label: "Plataforma" },
   { key: "statusPill", label: "Status", raw: true },
   { key: "strategy", label: "Estratégia" },
   { key: "listing_funds", label: "Vitrine", num: true },
   { key: "catalog_funds", label: "Catálogo", num: true },
   { key: "coverage", label: "CNPJ na vitrine", num: true },
   { key: "detail", label: "Observação" }],
  DATA.platforms.map(p => ({
    label: p.label,
    statusPill: `<span class="pill ${p.status}">${p.status}</span>`,
    strategy: p.strategy,
    listing_funds: p.listing_funds.toLocaleString("pt-BR"),
    catalog_funds: p.catalog_funds.toLocaleString("pt-BR"),
    coverage: p.listing_funds ? Math.round(p.cnpj_coverage * 100) + "%" : "-",
    detail: p.error || p.listing_reason || "-",
  }))
);
