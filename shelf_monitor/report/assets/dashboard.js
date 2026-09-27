const DATA = JSON.parse(document.getElementById("dashboard-data").textContent);
// Blocos que o modulo declarou sem sentido (chave -> motivo); vazio em fundos.
// "cotistas": nada que conte cotistas; "cnpj": nada que cruze vitrines por CNPJ.
const SUPRIMIDOS = DATA.suprimidos || {};
const SERIES_VARS = ["--s1", "--s2", "--s3", "--s4", "--s5"];
const platformColor = {};
// A cor segue a casa, ancorada na ordem canonica - nao na ordem em que ela
// aparece nesta execucao. Assim uma casa mantem sua cor entre execucoes, e as
// casas que so aparecem no radar CVM tambem recebem a sua.
(DATA.house_order || DATA.platforms.map(p => p.platform_id)).forEach((id, i) => {
  platformColor[id] = `var(${SERIES_VARS[i % SERIES_VARS.length]})`;
});

/* --- Paineis sob demanda ----------------------------------------------------
   A visao executiva fica sempre a vista; cada bloco analitico mora num
   <details> recolhido. Link interno (tile de manchete, topico das leituras,
   "Fontes", rodape) abre o painel de destino antes de rolar ate ele - sem
   isso o clique levaria a um titulo fechado. Fica no topo do script de
   proposito: um bloco que quebre ao desenhar nao leva junto a navegacao. */
function abrirDestino(id) {
  const alvo = id ? document.getElementById(id) : null;
  if (!alvo) return false;
  const proprio = alvo.matches(".panel") ? alvo.querySelector(":scope > details") : null;
  if (proprio) proprio.open = true;
  for (let d = alvo.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) {
    d.open = true;
  }
  // Salto direto, sem rolagem suave: a suave anda por quadro de animacao e
  // fica parada quando a pagina nao esta sendo pintada (aba em segundo plano).
  alvo.scrollIntoView({ block: "start" });
  return true;
}
document.addEventListener("click", ev => {
  const link = ev.target.closest ? ev.target.closest('a[href^="#"]') : null;
  if (!link) return;
  const id = decodeURIComponent(link.getAttribute("href").slice(1));
  if (!abrirDestino(id)) return;
  ev.preventDefault();
  try { history.replaceState(null, "", "#" + id); } catch (e) { /* file:// sem history */ }
});
window.addEventListener("hashchange", () => abrirDestino(decodeURIComponent(location.hash.slice(1))));
// No load, e nao agora: o destino so tem altura depois de desenhado.
window.addEventListener("load", () => {
  if (location.hash) abrirDestino(decodeURIComponent(location.hash.slice(1)));
});

(function botaoAbrirTudo() {
  const botao = document.getElementById("abrir-tudo");
  if (!botao) return;
  const paineis = () => Array.from(document.querySelectorAll(".panel > details"));
  const sincronizar = () => {
    const todos = paineis();
    botao.textContent = todos.length && todos.every(d => d.open) ? "Recolher todos" : "Abrir todos";
  };
  // Sem script o botao nao faria nada: nasce escondido e so aparece aqui.
  botao.hidden = false;
  botao.addEventListener("click", () => {
    const abrir = !paineis().every(d => d.open);
    paineis().forEach(d => { d.open = abrir; });
    sincronizar();
  });
  // "toggle" nao borbulha, mas passa pelo document na fase de captura.
  document.addEventListener("toggle", sincronizar, true);
  sincronizar();
})();

const SVG_NS = "http://www.w3.org/2000/svg";
function el(name, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function niceMax(value) {
  if (value <= 5) return 5;
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  for (const step of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    if (value <= step * magnitude) return step * magnitude;
  }
  return 10 * magnitude;
}

/* Barras agrupadas: categorias no eixo x, uma serie por plataforma.
   Gap de 2px entre barras vizinhas (a superficie separa, nao uma borda);
   topo arredondado em 4px ancorado na linha de base. */
function groupedBars(mount, categories, series, opts = {}) {
  if (!categories.length || !series.length) {
    mount.innerHTML = '<p class="empty">Sem dados coletados para este painel.</p>';
    return;
  }
  const barW = opts.barWidth || 15;
  const gap = 2;
  const groupPad = 26;
  const padL = 52, padR = 16, padT = 12, padB = 62;
  const groupW = series.length * barW + (series.length - 1) * gap;
  const plotW = categories.length * (groupW + groupPad) + groupPad;
  const plotH = opts.height || 240;
  const width = padL + plotW + padR;
  const height = padT + plotH + padB;

  const maxValue = niceMax(Math.max(1, ...series.flatMap(s => s.values)));
  const y = v => padT + plotH - (v / maxValue) * plotH;

  const svg = el("svg", {
    viewBox: `0 0 ${width} ${height}`, width, height,
    role: "img", "aria-label": opts.ariaLabel || "gráfico de barras agrupadas",
  });

  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const value = (maxValue / ticks) * i;
    const yy = y(value);
    svg.appendChild(el("line", {
      class: i === 0 ? "baseline" : "gridline",
      x1: padL, x2: padL + plotW, y1: yy, y2: yy,
    }));
    svg.appendChild(el("text", {
      class: "tick", x: padL - 9, y: yy + 4, "text-anchor": "end",
    }, String(Math.round(value))));
  }

  categories.forEach((category, ci) => {
    const groupX = padL + groupPad + ci * (groupW + groupPad);
    const group = el("g", { class: "bar-group" });

    series.forEach((s, si) => {
      const value = s.values[ci] || 0;
      const x = groupX + si * (barW + gap);
      const barH = Math.max(0, padT + plotH - y(value));
      if (barH > 0) {
        const r = Math.min(4, barW / 2, barH);
        // Topo arredondado, base reta ancorada no eixo.
        const path = el("path", {
          class: "bar",
          d: `M ${x} ${padT + plotH} L ${x} ${y(value) + r}
              Q ${x} ${y(value)} ${x + r} ${y(value)}
              L ${x + barW - r} ${y(value)} Q ${x + barW} ${y(value)} ${x + barW} ${y(value) + r}
              L ${x + barW} ${padT + plotH} Z`,
          fill: platformColor[s.id] || "var(--s1)",
        });
        path.appendChild(el("title", {}, `${s.label} - ${category}: ${value}`));
        group.appendChild(path);
      }
      // Alvo de hover maior que a marca, cobrindo toda a altura do plot.
      const hit = el("rect", {
        x: x - gap / 2, y: padT, width: barW + gap, height: plotH, fill: "transparent",
      });
      hit.appendChild(el("title", {}, `${s.label} - ${category}: ${value}`));
      group.appendChild(hit);
    });

    const labelX = groupX + groupW / 2;
    wrapLabel(svg, category, labelX, padT + plotH + 18, groupW + groupPad - 6);
    svg.appendChild(group);
  });

  mount.innerHTML = "";
  mount.appendChild(svg);
}

function wrapLabel(svg, text, x, y, maxWidth) {
  const words = String(text).split(" ");
  const perLine = maxWidth < 70 ? 1 : 2;
  const lines = [];
  for (let i = 0; i < words.length; i += perLine) {
    lines.push(words.slice(i, i + perLine).join(" "));
  }
  lines.slice(0, 3).forEach((line, i) => {
    svg.appendChild(el("text", {
      class: "tick", x, y: y + i * 13, "text-anchor": "middle",
    }, line));
  });
}

/* Barras horizontais de serie unica: uma cor por plataforma (a entidade),
   com o valor rotulado direto na ponta - nenhum valor fica so no tooltip. */
function horizontalBars(mount, rows, opts = {}) {
  if (!rows.length) {
    mount.innerHTML = '<p class="empty">Sem dados coletados para este painel.</p>';
    return;
  }
  const rowH = 30, barH = 15, padL = 148, padR = 60, padT = 6, padB = 10;
  const plotW = opts.width || 620;
  const width = padL + plotW + padR;
  const height = padT + rows.length * rowH + padB;
  const maxValue = niceMax(Math.max(1, ...rows.map(r => r.value)));

  const svg = el("svg", {
    viewBox: `0 0 ${width} ${height}`, width, height,
    role: "img", "aria-label": opts.ariaLabel || "gráfico de barras",
  });

  rows.forEach((row, i) => {
    const y = padT + i * rowH;
    const barW = (row.value / maxValue) * plotW;
    svg.appendChild(el("text", {
      class: "axis-label", x: padL - 12, y: y + barH - 2, "text-anchor": "end",
    }, row.label));

    if (barW > 0) {
      const r = Math.min(4, barW, barH / 2);
      const path = el("path", {
        class: "bar",
        d: `M ${padL} ${y} L ${padL + barW - r} ${y}
            Q ${padL + barW} ${y} ${padL + barW} ${y + r}
            L ${padL + barW} ${y + barH - r} Q ${padL + barW} ${y + barH} ${padL + barW - r} ${y + barH}
            L ${padL} ${y + barH} Z`,
        fill: row.color || "var(--s1)",
      });
      path.appendChild(el("title", {}, `${row.label}: ${row.value}`));
      svg.appendChild(path);
    }
    svg.appendChild(el("text", {
      class: "tick", x: padL + barW + 8, y: y + barH - 2,
    }, row.display !== undefined ? row.display : String(row.value)));
  });

  svg.appendChild(el("line", {
    class: "baseline", x1: padL, x2: padL, y1: padT, y2: padT + rows.length * rowH - (rowH - barH),
  }));

  mount.innerHTML = "";
  mount.appendChild(svg);
}

function renderLegend(mount, platforms) {
  mount.innerHTML = platforms.map(p =>
    `<span><i class="swatch" style="background:${platformColor[p.platform_id] || "var(--s1)"}"></i>` +
    `${escapeHtml(p.label)}</span>`
  ).join("");
}

/* Nomes de fundo e de gestora vem de fontes externas: qualquer valor que entre
   no innerHTML passa por aqui antes. So colunas marcadas com `raw` (montadas
   por este proprio script) escapam da regra. */
// Datas e numeros no formato brasileiro. Os blocos guardam ISO ("2026-07-01")
// e ponto decimal, que e o que o JSON compara; o painel escreve "01/07/2026",
// "jul/2026" e "3,13". Em 2026-09-24 o prazo da rentabilidade saia como
// "2026-07-01 a 2026-09-23", sem dizer que eram tres meses.
const MESES_BR = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
function dataBR(iso) {
  if (!iso) return "-";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso);
}
function competenciaBR(yyyymm) {
  if (!/^\d{6}$/.test(yyyymm || "")) return yyyymm || "-";
  return `${MESES_BR[Number(yyyymm.slice(4)) - 1]}/${yyyymm.slice(0, 4)}`;
}
function periodoBR(de, ate) { return `de ${dataBR(de)} a ${dataBR(ate)}`; }
function numBR(v, casas = 2) {
  return (v === null || v === undefined) ? "-"
    : v.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function table(mount, columns, rows) {
  if (!rows.length) {
    mount.innerHTML = '<p class="empty">Sem dados.</p>';
    return;
  }
  const head = columns.map(c =>
    `<th class="${c.num ? "num" : ""}">${escapeHtml(c.label)}</th>`).join("");
  const body = rows.map(r =>
    `<tr>${columns.map(c => {
      const value = r[c.key] ?? "-";
      return `<td class="${c.num ? "num" : ""}">${c.raw ? value : escapeHtml(value)}</td>`;
    }).join("")}</tr>`).join("");
  // Tabela larga rola dentro do proprio card; a pagina nunca rola na horizontal.
  mount.innerHTML =
    `<div class="table-scroll"><table><thead><tr>${head}</tr></thead>` +
    `<tbody>${body}</tbody></table></div>`;
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

// --- Matriz de classes ------------------------------------------------------
/* --- Vitrine de varejo ---------------------------------------------------- */
/* Rampa sequencial (um matiz, claro->escuro) da paleta validada; o texto da
   celula escolhe tinta pelo passo do preenchimento, nunca a cor da serie. */
const SEQ_STEPS = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
function heatColor(value, max) {
  if (!value) return null;
  const idx = Math.min(SEQ_STEPS.length - 1,
    Math.floor((value / max) * SEQ_STEPS.length));
  return { fill: SEQ_STEPS[idx], ink: idx >= 3 ? "#ffffff" : "#0b0b0b" };
}

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

  // Tiles: vitrine por casa, catalogo como nota secundaria.
  const tiles = document.getElementById("vitrine-tiles");
  tiles.innerHTML = "";
  withListing.forEach(p => {
    const tile = document.createElement("div");
    tile.className = "tile";
    const label = document.createElement("div");
    label.className = "label"; label.textContent = p.label;
    const value = document.createElement("div");
    value.className = "value"; value.textContent = p.total.toLocaleString("pt-BR");
    const note = document.createElement("div");
    note.className = "note";
    const catalog = p.catalog_total || 0;
    note.textContent = (catalog > p.total
      ? `de ${catalog.toLocaleString("pt-BR")} no catálogo` : "vitrine = total coletado") +
      (p.exclusive && !SUPRIMIDOS.cnpj ? ` - ${p.exclusive} só nesta vitrine` : "");
    tile.append(label, value, note);
    tiles.appendChild(tile);
  });

  // Matriz casa x classe ANBIMA (heatmap sequencial).
  let matrix = listing.matrix || [];
  const mount = document.getElementById("matrix-vitrine");
  // Matriz so de "sem classificacao" nao e matriz - e a ausencia da base.
  if (matrix.length === 1 && matrix[0].category === "Sem classificação") {
    matrix = [];
  }
  if (matrix.length) {
    const max = Math.max(1, ...matrix.flatMap(r => Object.values(r.counts)));
    const table = document.createElement("table");
    table.className = "matrix";
    const head = table.createTHead().insertRow();
    const corner = document.createElement("th");
    corner.className = "rowh"; corner.textContent = "Classe ANBIMA";
    head.appendChild(corner);
    withListing.forEach(p => {
      const th = document.createElement("th");
      th.textContent = p.label; head.appendChild(th);
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
          span.textContent = "\u2013";
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

  // Arquitetura aberta: propria vs terceiros (2 series fixas, gap de 2px).
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
    archLegend.innerHTML =
      '<span><span class="swatch" style="background:var(--s1)"></span>Gestora da casa</span>' +
      '<span><span class="swatch" style="background:var(--s2)"></span>Terceiros</span>' +
      '<span><span class="swatch" style="background:var(--grid)"></span>Sem gestor identificado</span>';
    stackedRows(archMount, withListing.map(p => ({
      label: p.label,
      parts: [
        { value: p.own_product || 0, color: "var(--s1)", name: "gestora da casa" },
        { value: p.third_party || 0, color: "var(--s2)", name: "terceiros" },
        { value: Math.max(0, p.total - (p.own_product || 0) - (p.third_party || 0)),
          color: "var(--grid)", name: "sem gestor identificado" },
      ],
    })));
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
    parts.push("Sem vitrine alcançável: " + missing.map(p => p.label).join(", ") + ".");
  }
  const identifiable = DATA.enrichment
    ? Math.max(DATA.enrichment.with_cnpj || 0, DATA.enrichment.with_cvm_code || 0)
    : 0;
  if (identifiable) {
    const e = DATA.enrichment;
    parts.push(`Normalização ANBIMA: ${e.matched} de ${identifiable} fundos com CNPJ ` +
      `ou código CVM casaram com o registro oficial ` +
      `(${Math.round(100 * e.matched / identifiable)}%).`);
    if (e.managers_from_cvm) {
      parts.push(`${e.managers_from_cvm} fundos tiveram a gestora carimbada pelo ` +
        `registro oficial da CVM.`);
    }
    if (e.matched_by_cvm_code) {
      parts.push(`${e.matched_by_cvm_code} casaram pelo código CVM, sem CNPJ na fonte.`);
    }
    if (e.managers_inferred) {
      parts.push(`${e.managers_inferred} gestoras inferidas pelo prefixo do nome ` +
        `(marcadas como inferidas, nunca como oficiais).`);
    }
  } else {
    parts.push("Normalização ANBIMA não aplicada: base de mercado não importada " +
      "(python -m shelf_monitor market).");
  }
  // Cruza vitrines por CNPJ: some com a supressao, como gaps e exclusivos.
  if (listing.shared_all && !SUPRIMIDOS.cnpj) {
    parts.push(listing.shared_all === 1
      ? "1 fundo aparece em duas ou mais vitrines (por CNPJ)."
      : `${listing.shared_all.toLocaleString("pt-BR")} fundos aparecem em duas ou mais ` +
        "vitrines (por CNPJ).");
  }
  document.getElementById("vitrine-coverage").textContent = parts.join(" ");
}

/* Barra horizontal empilhada: segmentos separados por gap de 2px da superficie,
   rotulo do total na ponta; valores por segmento no tooltip e na legenda. */
function stackedRows(mount, rows) {
  if (!rows.length) {
    mount.innerHTML = '<p class="empty">Sem dados coletados para este painel.</p>';
    return;
  }
  const rowH = 30, barH = 15, padL = 118, padR = 56, padT = 6, padB = 10;
  const plotW = 420;
  const width = padL + plotW + padR;
  const height = padT + rows.length * rowH + padB;
  const max = Math.max(1, ...rows.map(r => r.parts.reduce((a, s) => a + s.value, 0)));

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height,
    role: "img", "aria-label": "arquitetura aberta por casa" });

  rows.forEach((row, i) => {
    const y = padT + i * rowH;
    svg.appendChild(el("text", { class: "axis-label", x: padL - 12, y: y + barH - 2,
      "text-anchor": "end" }, row.label));
    let x = padL;
    const total = row.parts.reduce((a, s) => a + s.value, 0);
    row.parts.forEach(part => {
      if (!part.value) return;
      const w = Math.max(1, (part.value / max) * plotW - 2);
      const rect = el("rect", { class: "bar", x, y, width: w, height: barH, rx: 3,
        fill: part.color });
      rect.appendChild(el("title", {},
        `${row.label} - ${part.name}: ${part.value} de ${total}`));
      svg.appendChild(rect);
      x += w + 2; // gap de 2px na cor da superficie
    });
    svg.appendChild(el("text", { class: "tick", x: x + 6, y: y + barH - 2 },
      String(total)));
  });
  mount.innerHTML = "";
  mount.appendChild(svg);
}

function table2(mount, columns, rows) {
  const cols = columns.map((c, i) => ({ label: c, key: "c" + i, num: i > 0 }));
  table(mount, cols, rows.map(r =>
    Object.fromEntries(r.map((v, i) => ["c" + i, v]))));
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

  const tiles = document.getElementById("gaps-tiles");
  tiles.innerHTML = "";
  // Com a carteira da CVM o bloco mede estrategia, nao so CNPJ: os tiles abrem
  // pelo funil. Sem ela, os de sempre.
  const comCarteira = gaps.carteira && gaps.carteira.available;
  (comCarteira ? [
    ["Gaps reais", gaps.total_gaps, "sem espelho nem equivalente na XP"],
    ["Gaps aparentes", gaps.gaps_aparentes, "CNPJ em vitrine concorrente, fora da XP"],
    ["Já cobertos", gaps.total_cobertos, "espelho ou equivalente, com a prova"],
    ["Em outra liquidez", gaps.gaps_liquidez, "a XP tem a estratégia em outro D+"],
  ] : [
    ["Fundos de gap", gaps.total_gaps, "em vitrine concorrente, fora da vitrine XP"],
    ["Em 2+ vitrines", gaps.multi_house_gaps, "gap confirmado por mais de uma casa"],
    ["Vitrine da XP", gaps.baseline_listing_total, "a base de comparação"],
    ["Concorrentes no universo", gaps.competitors.length,
     gaps.competitors.map(c => c.label).join(", ") || "nenhum"],
  ]).forEach(([label, value, note]) => {
    const tile = document.createElement("div");
    tile.className = "tile";
    const l = document.createElement("div"); l.className = "label"; l.textContent = label;
    const v = document.createElement("div"); v.className = "value";
    v.textContent = Number(value).toLocaleString("pt-BR");
    const n = document.createElement("div"); n.className = "note"; n.textContent = note;
    tile.append(l, v, n);
    tiles.appendChild(tile);
  });
  const funil = document.getElementById("gaps-funil");
  if (funil) funil.textContent = gaps.frase_funil || "";

  // Categorias em deficit: vitrine XP vs. vitrine de cada concorrente, mais a
  // coluna de quantos gaps caem na categoria.
  const mount = document.getElementById("matrix-gaps");
  const categories = gaps.categories || [];
  if (categories.length) {
    const houses = [{ platform_id: gaps.baseline_id, label: gaps.baseline_label }]
      .concat(gaps.competitors);
    const max = Math.max(1, ...categories.flatMap(c =>
      [c.xp || 0, ...Object.values(c.by_platform || {})]));
    const tbl = document.createElement("table");
    tbl.className = "matrix";
    const head = tbl.createTHead().insertRow();
    const corner = document.createElement("th");
    corner.className = "rowh"; corner.textContent = "Categoria";
    head.appendChild(corner);
    houses.forEach(h => {
      const th = document.createElement("th");
      th.textContent = h.label; head.appendChild(th);
    });
    const gapTh = document.createElement("th");
    gapTh.textContent = "Gaps reais"; head.appendChild(gapTh);
    const cobTh = document.createElement("th");
    cobTh.textContent = "Cobertos"; head.appendChild(cobTh);
    const body = tbl.createTBody();
    categories.forEach(c => {
      const row = body.insertRow();
      const rowLabel = row.insertCell();
      rowLabel.className = "rowlabel";
      rowLabel.textContent = c.category; rowLabel.title = c.category;
      houses.forEach(h => {
        const cell = row.insertCell();
        const value = h.platform_id === gaps.baseline_id
          ? (c.xp || 0) : (c.by_platform || {})[h.platform_id] || 0;
        const span = document.createElement("span");
        if (value) {
          const heat = heatColor(value, max);
          span.className = "cell";
          span.style.background = heat.fill; span.style.color = heat.ink;
          span.textContent = value.toLocaleString("pt-BR");
          span.title = `${h.label} - ${c.category}: ${value}`;
        } else {
          span.className = "cell zero"; span.textContent = "–";
        }
        cell.appendChild(span);
      });
      const gapCell = row.insertCell();
      const gapSpan = document.createElement("span");
      gapSpan.className = "cell";
      gapSpan.textContent = (c.gap_count || 0).toLocaleString("pt-BR");
      gapCell.appendChild(gapSpan);
      const cobCell = row.insertCell();
      const cobSpan = document.createElement("span");
      cobSpan.className = "cell";
      cobSpan.textContent = (c.cobertos || 0).toLocaleString("pt-BR");
      cobCell.appendChild(cobSpan);
    });
    mount.innerHTML = "";
    mount.appendChild(tbl);
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
  const parts = ["Comparação vitrine x vitrine: o catálogo Open Finance não conta " +
    "em nenhum dos lados."];
  if (gaps.baseline_warning) parts.push(gaps.baseline_warning + ".");
  const partial = gaps.competitors.filter(c => c.partial);
  if (partial.length) {
    parts.push("Coleta parcial (gaps podem estar subestimados): " +
      partial.map(c => c.label).join(", ") + ".");
  }
  if (gaps.excluded && gaps.excluded.length) {
    parts.push("Fora do universo: " +
      gaps.excluded.map(e => `${e.label} (${e.reason})`).join("; ") + ".");
  }
  if (gaps.funds_truncated) {
    parts.push(`Lista limitada aos ${gaps.funds.length} primeiros gaps; ` +
      `os ${gaps.funds_truncated} restantes estão na planilha.`);
  }
  if (gaps.cobertos_truncated) {
    parts.push(`Cobertos limitados aos ${gaps.cobertos.length} primeiros; ` +
      `os ${gaps.cobertos_truncated} restantes estão na aba Gaps cobertos da planilha.`);
  }
  (gaps.notas_cobertura || []).forEach(n => parts.push(n));
  parts.push("Possível equivalente XP é sinal de baixa confiança - " +
    "informa, nunca remove um fundo da lista.");
  document.getElementById("gaps-coverage").textContent = parts.join(" ");
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
  const celula = (texto, valor) => {
    const cor = valor > 0 ? "var(--good)" : valor < 0 ? "var(--critical)" : "var(--text-primary)";
    return `<td><span class="cell" style="color:${cor}">${texto}</span></td>`;
  };

  // As frases de abertura vem prontas do Python - as mesmas do digest.
  // O prazo no topo da secao, antes de qualquer numero: a captacao cobre as
  // ultimas competencias do informe, a corrente ainda em curso.
  const prazoEl = document.getElementById("flow-prazo");
  if (prazoEl && flow.periodo) {
    const meses = flow.periodo.meses_lidos || [];
    prazoEl.innerHTML = `<strong>Prazo: ${meses.length || "-"} ${meses.length === 1 ? "mês" : "meses"}</strong>, ` +
      periodoBR(flow.periodo.de, flow.periodo.ate) +
      (meses.length ? ` (competências ${meses.map(competenciaBR).join(", ")})` : "") + ".";
  }
  const leituraEl = document.getElementById("flow-leitura");
  if (flow.leitura && flow.leitura.length) {
    leituraEl.innerHTML = flow.leitura.map(f => `<li>${escapeHtml(f)}</li>`).join("");
  } else {
    leituraEl.remove();
  }

  document.getElementById("flow-tiles").innerHTML = flow.casas.map(c => `
    <div class="tile">
      <div class="label">${c.label}</div>
      <div class="value" style="color:${c.captacao_liquida > 0 ? "var(--good)" : "var(--critical)"}">
        ${mm(c.captacao_liquida)}</div>
      <div class="note">${c.com_informe} de ${c.vitrine} fundos com informe${SUPRIMIDOS.cnpj ? ""
        : ` &middot; só ela exibe: ${c.exclusivos}, ${mm(c.captacao_exclusivos)}`}</div>
    </div>`).join("");

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
  if (composicaoEl) composicaoEl.innerHTML =
    '<table class="matrix"><thead><tr><th class="rowh">Casa</th>' +
    colunas.map(([t]) => `<th>${t}</th>`).join("") + "</tr></thead><tbody>" +
    flow.casas.map(c => `<tr><th class="rowh">${escapeHtml(c.label)}</th>` +
      colunas.map(([t, f]) => f === null ? celula(pctPl(c.sobre_pl), c.sobre_pl)
        : celula(mm(f(c) || 0), f(c) || 0)).join("") + "</tr>").join("") +
    "</tbody></table>";

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

  // Tendencia por casa: a contagem global descreve o mercado; por casa, compara.
  const porCasa = flow.tendencia_por_casa || {};
  const captando = ["acelerando", "estavel", "desacelerando", "virou_entrada"];
  document.getElementById("flow-tendencia-casas").innerHTML =
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

  // Serie por casa: uma linha por casa, uma coluna por competencia.
  const comps = flow.competencias || [];
  if (comps.length) {
    const head = '<tr><th class="rowh">Casa</th>' +
      comps.map(c => `<th>${c.slice(4)}/${c.slice(0, 4)}</th>`).join("") + "</tr>";
    const body = Object.entries(flow.serie_por_casa).map(([pid, serie]) => {
      const byComp = Object.fromEntries(serie.map(m => [m.competencia, m]));
      const casa = (flow.casas.find(c => c.platform_id === pid) || {}).label || pid;
      const cells = comps.map(c => {
        const m = byComp[c];
        if (!m) return '<td><span class="cell zero">-</span></td>';
        const cor = m.media_diaria > 0 ? "var(--good)" : "var(--critical)";
        return `<td title="${m.dias} pregões observados"><span class="cell" style="color:${cor}">${mm(m.media_diaria)}/dia</span></td>`;
      }).join("");
      return `<tr><th class="rowh">${casa}</th>${cells}</tr>`;
    }).join("");
    document.getElementById("flow-trend").innerHTML =
      `<table class="matrix"><thead>${head}</thead><tbody>${body}</tbody></table>`;
  }

  // Ticket por cotista novo: a CVM publica o fluxo do fundo inteiro, e so esse
  // numero mostra ao leitor quando a captacao nao tem cara de varejo.
  const mil = v => "R$ " + Math.round(v / 1000).toLocaleString("pt-BR") + " mil";
  const lista = (titulo, fundos, sinal) => {
    if (!fundos || !fundos.length) return "";
    const itens = fundos.map(f => {
      const cot = SUPRIMIDOS.cotistas ? "" : `${int(f.variacao_cotistas)} cotistas` +
        (f.ticket_por_cotista ? ` (${mil(f.ticket_por_cotista)} por cotista novo)` : "") + " &middot; ";
      return `
      <li><strong style="color:${sinal}">${mm(f.captacao_liquida)}</strong>
        ${escapeHtml(f.nome)} <span class="dim">${cot}${(f.vitrines || []).map(rotulo).map(escapeHtml).join(", ")}</span></li>`;
    }).join("");
    return `<h4 style="font-size:12.5px;font-weight:600;margin:14px 0 4px">${escapeHtml(titulo)}</h4>
      <ul class="flow-list">${itens}</ul>`;
  };
  const criterio = flow.com_cotistas === false ? "dinheiro" : "dinheiro e cotistas";

  const g = SUPRIMIDOS.cnpj ? {} : (flow.grade || {});
  const grade = g.available ? `
    <h4 style="font-size:12.5px;font-weight:600;margin:16px 0 4px">Oportunidade de grade</h4>
    <p class="card-sub">${g.total} fundos captam varejo fora da vitrine da XP, mas
      ${g.total_produto_do_concorrente} são produto próprio da casa que os exibe e não se
      transferem. Restam <strong>${g.total_transferiveis} fundos, ${mm(g.captacao_transferivel)}</strong>
      e ${int(g.cotistas_transferivel)} cotistas - ${g.total_operacionais} de gestora que a XP
      já distribui, ${g.total_comerciais} exigem abrir gestora.</p>
    ${lista("Mais varejo: quem trouxe mais cotistas", g.mais_varejo, "var(--good)")}` : "";

  document.getElementById("flow-lists").innerHTML =
    lista(`Maiores captações - fundos ganhando ${criterio}`, flow.top_captacao, "var(--good)") +
    lista(`Maiores resgates - fundos perdendo ${criterio}`, flow.top_resgate, "var(--critical)") +
    lista(`Na vitrine da ${flow.baseline_label || "XP"}, perdendo ${criterio}`,
      flow.baseline_perdendo, "var(--critical)") +
    lista("Acelerando - captando mais por dia que antes", flow.acelerando, "var(--good)") +
    lista("Viraram saída - captavam e passaram a perder", flow.virou_saida, "var(--critical)") +
    grade;

  const fora = (flow.excluded || []).map(e => `${e.label} (${e.reason})`);
  const estrut = flow.total_estruturais
    ? `${flow.total_estruturais} fundos com movimento estrutural (fluxo maior que o próprio
       patrimônio, ou fundo esvaziado) ficam fora de todas as somas: é dinheiro trocando de
       veículo, não demanda. `
    : "";
  document.getElementById("flow-coverage").textContent =
    `Período ${periodoBR(flow.periodo.de, flow.periodo.ate)}. ${estrut}` +
    (fora.length ? `Fora da análise: ${fora.join("; ")}.` : "");
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
  // Bloco gravado antes do descritor de janela: o prazo sai do periodo.
  const janelaDe = b => b.janela || {
    rotulo: `${(b.periodo.meses_lidos || []).length || 3} meses`,
    de: b.periodo.de, ate: b.periodo.ate, pregoes: b.periodo.pregoes,
    base: "cota do primeiro pregão do período",
  };

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
        (janela.pregoes
          ? ` (${janela.pregoes.toLocaleString("pt-BR")} pregões, base na ${escapeHtml(janela.base || "")})`
          : "") +
        `. Referência: mediana da mesma categoria no universo oficial, no mesmo prazo - ` +
        `${(universo.fundos_com_serie || 0).toLocaleString("pt-BR")} fundos, ` +
        `${universo.categorias_com_referencia} categorias com amostra >= ${universo.amostra_minima}.`;
    }

    document.getElementById("perf-tiles").innerHTML = perf.casas.map(c => {
      const cor = c.gap > 0 ? "var(--good)" : "var(--critical)";
      if (SUPRIMIDOS.cotistas) return `
      <div class="tile">
        <div class="label">${c.label}</div>
        <div class="value">${pct(c.por_pl)}</div>
        <div class="note">por PL em ${janela.rotulo} &middot; mediana ${pct(c.mediana)}<br>
          ${c.com_serie} de ${c.vitrine} fundos com série</div>
      </div>`;
      return `
      <div class="tile">
        <div class="label">${c.label}</div>
        <div class="value">${pct(c.por_cotista)}</div>
        <div class="note">por cotista em ${janela.rotulo} &middot; por PL ${pct(c.por_pl)} &middot;
          mediana ${pct(c.mediana)}<br>
          gap <strong style="color:${cor}">${pp(c.gap)}</strong> &middot;
          ${c.com_serie} de ${c.vitrine} fundos com série</div>
      </div>`;
    }).join("");

    // Tres barras por casa: mediana, por PL, por cotista. A escala comeca no
    // menor valor menos uma folga, senao tres numeros entre 2,9% e 3,6% viram
    // tres barras visualmente iguais.
    const valores = perf.casas.flatMap(c => SUPRIMIDOS.cotistas
      ? [c.mediana, c.por_pl] : [c.mediana, c.por_pl, c.por_cotista])
      .filter(v => v !== null && v !== undefined);
    const chart = document.getElementById("perf-chart");
    const legenda = document.getElementById("perf-legend");
    chart.innerHTML = "";
    legenda.innerHTML = "";
    if (valores.length) {
      const min = Math.min(...valores), max = Math.max(...valores);
      const base = min - (max - min) * 0.25 - 0.01;
      const span = (max - base) || 1;
      const larguraCasa = 150, alturaMax = 120;
      const w = perf.casas.length * larguraCasa + 40, h = alturaMax + 70;
      const series = [
        ["mediana", "Mediana dos fundos", "var(--muted)"],
        ["por_pl", "Ponderada por PL", "var(--s1)"],
        ["por_cotista", "Ponderada por cotistas", "var(--good)"],
      ].filter(([chave]) => !(SUPRIMIDOS.cotistas && chave === "por_cotista"));
      const barras = perf.casas.map((c, i) => {
        const x0 = 30 + i * larguraCasa;
        return series.map(([chave, rotulo, cor], j) => {
          const v = c[chave];
          if (v === null || v === undefined) return "";
          const alt = Math.max(2, ((v - base) / span) * alturaMax);
          const x = x0 + j * 38;
          const y = 20 + alturaMax - alt;
          return `<rect x="${x}" y="${y}" width="30" height="${alt}" fill="${cor}" rx="2">
              <title>${c.label} - ${rotulo} em ${janela.rotulo}: ${pct(v)}</title></rect>
            <text x="${x + 15}" y="${y - 5}" text-anchor="middle" font-size="10"
              fill="var(--text-primary)">${pct(v)}</text>`;
        }).join("") +
        `<text x="${x0 + 57}" y="${alturaMax + 40}" text-anchor="middle" font-size="11"
          fill="var(--text-primary)">${c.label}</text>`;
      }).join("");
      chart.innerHTML =
        `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img">
          <title>Retorno em ${janela.rotulo} por casa, nas três leituras</title>${barras}</svg>`;
      legenda.innerHTML = series.map(
        ([, rotulo, cor]) => `<span><span class="swatch" style="background:${cor}"></span>${rotulo}</span>`).join("");
    }

    // Massa mal alocada: quanto do dinheiro e da gente esta abaixo da mediana da
    // propria categoria. E a linha que o resto do relatorio nao responde.
    document.getElementById("perf-massa").innerHTML =
      '<table class="matrix"><thead><tr><th class="rowh">Casa</th>' +
      "<th>Fundos abaixo da mediana</th><th>% dos fundos</th><th>% do PL</th>" +
      "<th>Índice PL</th></tr></thead><tbody>" +
      perf.casas.map(c => {
        const m = c.alocacao_abaixo_da_mediana || {};
        const cor = m.indice_pl === null || m.indice_pl === undefined ? "var(--text-primary)"
          : m.indice_pl < 1 ? "var(--good)" : "var(--critical)";
        return `<tr><th class="rowh">${c.label}</th>
          <td><span class="cell">${m.fundos} de ${m.de}</span></td>
          <td><span class="cell">${share(m.share_fundos)}</span></td>
          <td><span class="cell">${share(m.share_pl)}</span></td>
          <td><span class="cell" style="color:${cor}">${numBR(m.indice_pl)}</span></td></tr>`;
      }).join("") + "</tbody></table>";

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
            <td><span class="cell" style="color:var(--critical)">${numBR(f.excesso)}pp</span></td>
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

  // Custo: indice mediano, nao taxa mediana.
  const bloco = document.getElementById("custo-bloco");
  if (custo && custo.available && bloco) {
    bloco.innerHTML =
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
  } else if (bloco) {
    bloco.innerHTML = `<p class="card-sub">Custo não calculado: ${(custo || {}).reason || "sem base de mercado"}</p>`;
  }

  // Mandato: share por dimensao, com o indice contra as demais casas.
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
        const cor = l.aposta === "acima" ? "var(--good)"
          : l.aposta === "abaixo" ? "var(--critical)" : "var(--text-primary)";
        const idx = l.so_ela ? "só ela" : numBR(l.indice) + "x";
        return `<td title="${share(l.share_pares)} nas concorrentes (${idx})">
          <span class="cell" style="color:${cor}">${share(l.share)}</span></td>`;
      }).join("");
      return `<tr><th class="rowh">${c.label}</th>${cells}</tr>`;
    }).join("");
    mbloco.innerHTML = `<table class="matrix"><thead>${head}</thead><tbody>${body}</tbody></table>`;
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
    document.getElementById("perf-coverage").innerHTML =
      "<strong>Fora destes blocos:</strong> " +
      linhas.map(e => `${e.label} (${e.reason})`).join(" &middot; ");
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

renderLegend(document.getElementById("legend-classes"), usable);
groupedBars(
  document.getElementById("chart-classes"),
  DATA.class_order,
  usable.map(p => ({
    id: p.platform_id, label: p.label,
    values: DATA.class_order.map(c => p.by_class[c] || 0),
  })),
  { height: 250, ariaLabel: "Número de fundos por classe de ativo em cada plataforma" }
);
table(
  document.getElementById("table-classes"),
  [{ key: "label", label: "Plataforma" },
   ...DATA.class_order.map(c => ({ key: c, label: c, num: true })),
   { key: "listing_funds", label: "Vitrine", num: true }],
  usable.map(p => ({ label: p.label, listing_funds: p.listing_funds,
    ...Object.fromEntries(DATA.class_order.map(c => [c, p.by_class[c] || 0])) }))
);
notaFora(document.getElementById("table-classes"));

// --- Profundidade: gestoras representadas -----------------------------------
horizontalBars(
  document.getElementById("chart-managers"),
  usable.map(p => ({
    label: p.label, value: p.distinct_managers, color: platformColor[p.platform_id],
  })).sort((a, b) => b.value - a.value),
  { ariaLabel: "Gestoras distintas representadas por plataforma" }
);
notaFora(document.getElementById("chart-managers"));
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
    ? `<strong>Sem base para comparar o ticket da ${lt.baseline_label}.</strong> A
       coleta trouxe o mínimo da própria vitrine em ${lt.baseline_amostra} fundos,
       abaixo dos ${lt.amostra_minima} necessários. A melhor concorrente nessa
       faixa, ${lt.melhor_label}, tem ${lt.melhor_varejo} fundos abaixo de ${teto}.`
    : lt.baseline_varejo === 0
    ? `<strong>A vitrine da ${lt.baseline_label} não tem nenhum fundo abaixo de
       ${teto}.</strong> A melhor concorrente nessa faixa, ${lt.melhor_label}, tem
       ${lt.melhor_varejo}. Não é diferença de composição: é a ausência de uma
       porta de entrada de varejo.`
    : `<strong>${lt.baseline_label}: ${lt.baseline_varejo} fundos abaixo de ${teto}</strong>,
       contra ${lt.melhor_varejo} da ${lt.melhor_label}.`;
}

const withMins = usable.filter(p => Object.keys(p.min_bands).length);
renderLegend(document.getElementById("legend-bands"), withMins);
groupedBars(
  document.getElementById("chart-bands"),
  DATA.band_order,
  withMins.map(p => ({
    id: p.platform_id, label: p.label,
    values: DATA.band_order.map(b => p.min_bands[b] || 0),
  })),
  { height: 220, ariaLabel: "Distribuição de aplicação mínima por plataforma" }
);
notaFora(document.getElementById("chart-bands"));
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

// --- Exclusividade ----------------------------------------------------------
// Sem o container quando o modulo suprime a comparacao por CNPJ: o HTML fixo
// ja traz o motivo no lugar do grafico.
const exclusiveEl = document.getElementById("chart-exclusive");
if (exclusiveEl) {
  horizontalBars(
    exclusiveEl,
    usable.map(p => ({
      label: p.label, value: p.exclusive_count, color: platformColor[p.platform_id],
    })).sort((a, b) => b.value - a.value),
    { ariaLabel: "Fundos exclusivos de cada plataforma, por CNPJ" }
  );
  notaFora(exclusiveEl);
}

// --- Radar CVM --------------------------------------------------------------
// A previdencia nao roda o radar (ele acompanha as gestoras da watchlist, no
// monitor de fundos) e a pagina dela nao tem o painel.
const cvm = DATA.cvm;
if (!document.getElementById("cvm-section")) {
  // nada a desenhar
} else if (cvm.available && cvm.houses && cvm.houses.length) {
  // A legenda acompanha as casas do proprio grafico - uma casa sem prateleira
  // coletada (Nu, por exemplo) ainda aparece aqui e precisa ser nomeada.
  renderLegend(
    document.getElementById("legend-cvm"),
    cvm.houses.map(h => ({ platform_id: h.house, label: h.label }))
  );
  groupedBars(
    document.getElementById("chart-cvm"),
    ["Registrados na CVM", "Em fase pré-operacional"],
    cvm.houses.map(h => ({
      id: h.house, label: h.label,
      values: [h.total, h.pre_operational],
    })),
    { height: 210, barWidth: 18, ariaLabel: "Fundos registrados na CVM por casa" }
  );
  table(
    document.getElementById("table-cvm"),
    [{ key: "label", label: "Casa" },
     { key: "total", label: "Registrados", num: true },
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
