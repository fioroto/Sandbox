"""Dashboard HTML comparativo, self-contained.

Os graficos sao SVG desenhado no proprio arquivo: sem dependencia de CDN, o
arquivo abre offline e o commit fica auditavel. Cada grafico tem uma tabela
equivalente (exigida pela regra de contraste da paleta em modo claro).
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.models import RunReport
from shelf_monitor.report.aggregate import build_dashboard_data

# Paleta categorica validada (scripts/validate_palette.js) - slots 1..5, na ordem.
# A cor segue a plataforma, nunca a posicao no ranking.
_SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
_SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]

_STYLE = """
:root {
  color-scheme: light;
  --surface-1: #fcfcfb;
  --page: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --axis: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --good: #0ca30c;
  --warning: #fab219;
  --critical: #d03b3b;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s5: #e87ba4;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --page: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --axis: #383835;
    --border: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1: #1a1a19;
  --page: #0d0d0d;
  --text-primary: #ffffff;
  --text-secondary: #c3c2b7;
  --muted: #898781;
  --grid: #2c2c2a;
  --axis: #383835;
  --border: rgba(255,255,255,0.10);
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--page);
  color: var(--text-primary);
  font: 14px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 40px 24px 72px; }
header.page { margin-bottom: 32px; }
h1 { font-size: 26px; font-weight: 620; margin: 0 0 6px; letter-spacing: -0.01em; }
.sub { color: var(--text-secondary); margin: 0; }
h2 { font-size: 17px; font-weight: 600; margin: 0 0 4px; }
.card-sub { color: var(--text-secondary); font-size: 13px; margin: 0 0 20px; }
.card {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 22px 24px;
  margin-bottom: 20px;
}
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 14px; margin-bottom: 24px; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
.tile .label { color: var(--text-secondary); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
.tile .value { font-size: 30px; font-weight: 600; line-height: 1.15; margin-top: 6px; }
.tile .note { color: var(--muted); font-size: 12px; margin-top: 2px; }

.subgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 24px; }
.subgrid h3 { font-size: 13.5px; font-weight: 600; margin: 0 0 4px; }
.subgrid .card-sub { margin-bottom: 12px; }
.matrix-scroll { overflow-x: auto; }
.matrix { border-collapse: collapse; font-size: 13px; width: 100%; }
.matrix th { color: var(--text-secondary); font-weight: 600; font-size: 12px; padding: 6px 10px; text-align: right; }
.matrix th.rowh { text-align: left; padding-left: 0; }
.matrix td { padding: 0; border: 2px solid var(--surface-1); }
.matrix td .cell { display: block; padding: 7px 10px; text-align: right; font-variant-numeric: tabular-nums; border-radius: 4px; min-width: 52px; }
.matrix td .zero { color: var(--muted); }
.matrix td.rowlabel { color: var(--text-primary); padding: 7px 12px 7px 0; white-space: nowrap; max-width: 320px; overflow: hidden; text-overflow: ellipsis; }
.coverage { color: var(--text-secondary); font-size: 12.5px; border-top: 1px solid var(--grid); margin-top: 18px; padding-top: 12px; }

.legend { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 16px; }
.legend span { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; color: var(--text-secondary); }
.swatch { width: 11px; height: 11px; border-radius: 3px; flex: none; }

.chart-scroll { overflow-x: auto; }
svg { display: block; max-width: 100%; }
svg text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.axis-label { fill: var(--text-secondary); font-size: 12px; }
.gridline { stroke: var(--grid); stroke-width: 1; }
.baseline { stroke: var(--axis); stroke-width: 1; }
.bar { transition: opacity .12s ease; }
.bar-group:hover .bar { opacity: .45; }
.bar-group .bar:hover { opacity: 1; }

.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
.table-scroll table { min-width: max-content; }
.table-scroll th:first-child, .table-scroll td:first-child { padding-left: 0; }
th, td { text-align: left; padding: 8px 12px 8px 0; border-bottom: 1px solid var(--grid); }
th { color: var(--text-secondary); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.03em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:last-child td { border-bottom: none; }
details { margin-top: 18px; }
summary { cursor: pointer; color: var(--text-secondary); font-size: 13px; padding: 6px 0; }
summary:hover { color: var(--text-primary); }
details[open] summary { margin-bottom: 10px; }

.pill { display: inline-block; padding: 2px 9px; border-radius: 99px; font-size: 11.5px; font-weight: 600; border: 1px solid var(--border); }
.pill.ok { color: var(--good); }
.pill.partial { color: var(--warning); }
.pill.unavailable { color: var(--critical); }
.note-box { background: var(--page); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; color: var(--text-secondary); font-size: 13px; margin-top: 14px; }
.empty { color: var(--muted); font-style: italic; padding: 12px 0; }
footer { color: var(--muted); font-size: 12px; margin-top: 36px; border-top: 1px solid var(--border); padding-top: 16px; }
"""

_SCRIPT = r"""
const DATA = JSON.parse(document.getElementById("dashboard-data").textContent);
const SERIES_VARS = ["--s1", "--s2", "--s3", "--s4", "--s5"];
const platformColor = {};
// A cor segue a casa, ancorada na ordem canonica - nao na ordem em que ela
// aparece nesta execucao. Assim uma casa mantem sua cor entre execucoes, e as
// casas que so aparecem no radar CVM tambem recebem a sua.
(DATA.house_order || DATA.platforms.map(p => p.platform_id)).forEach((id, i) => {
  platformColor[id] = `var(${SERIES_VARS[i % SERIES_VARS.length]})`;
});

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
    role: "img", "aria-label": opts.ariaLabel || "grafico de barras agrupadas",
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
    role: "img", "aria-label": opts.ariaLabel || "grafico de barras",
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

const usable = DATA.platforms.filter(p => p.status !== "unavailable" && p.total_funds > 0);

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
    section.querySelectorAll(".tiles, .matrix-scroll, .subgrid, details").forEach(n => n.remove());
    document.getElementById("vitrine-coverage").textContent =
      "Nenhuma vitrine coletada nesta execucao - rode a coleta com navegador " +
      "(SHELF_BROWSER_HEADED=1 para o Itau) para materializar as listagens.";
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
      ? `de ${catalog.toLocaleString("pt-BR")} no catalogo` : "vitrine = total coletado") +
      (p.exclusive ? ` - ${p.exclusive} so nela` : "");
    tile.append(label, value, note);
    tiles.appendChild(tile);
  });

  // Matriz casa x classe ANBIMA (heatmap sequencial).
  let matrix = listing.matrix || [];
  const mount = document.getElementById("matrix-vitrine");
  // Matriz so de "sem classificacao" nao e matriz - e a ausencia da base.
  if (matrix.length === 1 && matrix[0].category === "Sem classificacao") {
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
    mount.innerHTML = '<p class="empty">Sem classificacao ANBIMA disponivel - importe a base ' +
      'de mercado (python -m shelf_monitor market) para normalizar a vitrine.</p>';
  }

  // Arquitetura aberta: propria vs terceiros (2 series fixas, gap de 2px).
  const archMount = document.getElementById("chart-arch");
  const archLegend = document.getElementById("legend-arch");
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

  // Custo e ticket: tabela compacta - poucos numeros precisos pedem tabela.
  table2(document.getElementById("table-costs"),
    ["Casa", "Taxa adm q1", "mediana", "q3", "Minimo mediano (R$)", "Cobertura CNPJ"],
    withListing.map(p => {
      const fee = p.fee_quartiles, min = p.min_quartiles;
      const pct = v => v === undefined || v === null ? "\u2013" : v.toLocaleString("pt-BR",
        { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "%";
      const money = v => v === undefined || v === null ? "\u2013"
        : "R$ " + v.toLocaleString("pt-BR");
      return [
        p.label,
        fee ? pct(fee.q1) : "\u2013", fee ? pct(fee.median) : "\u2013",
        fee ? pct(fee.q3) : "\u2013",
        min ? money(min.median) : "\u2013",
        Math.round((p.cnpj_coverage || 0) * 100) + "%",
      ];
    }));

  // Cobertura e casas sem vitrine - o rodape que um comite pergunta primeiro.
  const parts = [];
  const missing = (listing.platforms || []).filter(p => !p.total);
  if (missing.length) {
    parts.push("Sem vitrine alcancavel: " + missing.map(p => p.label).join(", ") + ".");
  }
  const identifiable = DATA.enrichment
    ? Math.max(DATA.enrichment.with_cnpj || 0, DATA.enrichment.with_cvm_code || 0)
    : 0;
  if (identifiable) {
    const e = DATA.enrichment;
    parts.push(`Normalizacao ANBIMA: ${e.matched} de ${identifiable} fundos com CNPJ ` +
      `ou codigo CVM casaram com o registro oficial ` +
      `(${Math.round(100 * e.matched / identifiable)}%).`);
    if (e.managers_from_cvm) {
      parts.push(`${e.managers_from_cvm} fundos tiveram a gestora carimbada pelo ` +
        `registro oficial da CVM.`);
    }
    if (e.matched_by_cvm_code) {
      parts.push(`${e.matched_by_cvm_code} casaram pelo codigo CVM, sem CNPJ na fonte.`);
    }
    if (e.managers_inferred) {
      parts.push(`${e.managers_inferred} gestoras inferidas pelo prefixo do nome ` +
        `(marcadas como inferidas, nunca como oficiais).`);
    }
  } else {
    parts.push("Normalizacao ANBIMA nao aplicada: base de mercado nao importada " +
      "(python -m shelf_monitor market).");
  }
  if (listing.shared_all) {
    parts.push(`${listing.shared_all} fundos aparecem em duas ou mais vitrines (por CNPJ).`);
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

(function renderRestricted(block) {
  const mount = document.getElementById("table-restricted");
  const rows = (block && block.platforms || []).filter(p => p.total_snapshot > 0);
  if (!rows.length || !block.total) {
    mount.innerHTML = '<p class="empty">Nenhum fundo restrito/exclusivo identificado ' +
      'nesta coleta. A deteccao depende da base CVM importada e das flags das casas.</p>';
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
  { height: 250, ariaLabel: "Numero de fundos por classe de ativo em cada plataforma" }
);
table(
  document.getElementById("table-classes"),
  [{ key: "label", label: "Plataforma" },
   ...DATA.class_order.map(c => ({ key: c, label: c, num: true })),
   { key: "total_funds", label: "Total", num: true }],
  usable.map(p => ({ label: p.label, total_funds: p.total_funds,
    ...Object.fromEntries(DATA.class_order.map(c => [c, p.by_class[c] || 0])) }))
);

// --- Profundidade: gestoras representadas -----------------------------------
horizontalBars(
  document.getElementById("chart-managers"),
  usable.map(p => ({
    label: p.label, value: p.distinct_managers, color: platformColor[p.platform_id],
  })).sort((a, b) => b.value - a.value),
  { ariaLabel: "Gestoras distintas representadas por plataforma" }
);
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
const withMins = usable.filter(p => Object.keys(p.min_bands).length);
renderLegend(document.getElementById("legend-bands"), withMins);
groupedBars(
  document.getElementById("chart-bands"),
  DATA.band_order,
  withMins.map(p => ({
    id: p.platform_id, label: p.label,
    values: DATA.band_order.map(b => p.min_bands[b] || 0),
  })),
  { height: 220, ariaLabel: "Distribuicao de aplicacao minima por plataforma" }
);
table(
  document.getElementById("table-bands"),
  [{ key: "label", label: "Plataforma" },
   ...DATA.band_order.map(b => ({ key: b, label: b, num: true })),
   { key: "lowest", label: "Menor minimo", num: true }],
  withMins.map(p => ({
    label: p.label,
    lowest: p.lowest_min === null ? "-" : "R$ " + p.lowest_min.toLocaleString("pt-BR"),
    ...Object.fromEntries(DATA.band_order.map(b => [b, p.min_bands[b] || 0])),
  }))
);

// --- Exclusividade ----------------------------------------------------------
horizontalBars(
  document.getElementById("chart-exclusive"),
  usable.map(p => ({
    label: p.label, value: p.exclusive_count, color: platformColor[p.platform_id],
  })).sort((a, b) => b.value - a.value),
  { ariaLabel: "Fundos exclusivos de cada plataforma, por CNPJ" }
);

// --- Captacao (CVM) ---------------------------------------------------------
/* R$ compacto (bi/mi/mil): captacao se le em ordem de grandeza, nao em
   centavos. O sinal fica no numero - barra e texto contam a mesma historia. */
function formatBRL(value) {
  if (value === null || value === undefined) return "–";
  const sign = value < 0 ? "-" : "";
  const v = Math.abs(value);
  if (v >= 1e9) return `${sign}R$ ${(v / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 2 })} bi`;
  if (v >= 1e6) return `${sign}R$ ${(v / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  if (v >= 1e3) return `${sign}R$ ${(v / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mil`;
  return `${sign}R$ ${v.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;
}

/* Barras horizontais com sinal: eixo central, captacao para a direita e
   resgate liquido para a esquerda - a direcao E o dado. */
function signedBars(mount, rows) {
  if (!rows.length) {
    mount.innerHTML = '<p class="empty">Sem dados coletados para este painel.</p>';
    return;
  }
  const rowH = 30, barH = 15, padL = 118, padR = 70, padT = 6, padB = 10;
  const plotW = 440;
  const width = padL + plotW + padR;
  const height = padT + rows.length * rowH + padB;
  const maxAbs = Math.max(1, ...rows.map(r => Math.abs(r.value || 0)));
  const zero = padL + plotW / 2;
  const scale = (plotW / 2 - 8) / maxAbs;

  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height,
    role: "img", "aria-label": "captacao liquida por casa" });

  rows.forEach((row, i) => {
    const y = padT + i * rowH;
    const value = row.value || 0;
    svg.appendChild(el("text", { class: "axis-label", x: padL - 12, y: y + barH - 2,
      "text-anchor": "end" }, row.label));
    const w = Math.abs(value) * scale;
    if (w > 0.5) {
      const x = value >= 0 ? zero : zero - w;
      const rect = el("rect", { class: "bar", x, y, width: w, height: barH, rx: 3,
        fill: row.color || "var(--s1)" });
      rect.appendChild(el("title", {}, `${row.label}: ${formatBRL(value)}`));
      svg.appendChild(rect);
    }
    const labelX = value >= 0 ? zero + w + 8 : zero - w - 8;
    svg.appendChild(el("text", {
      class: "tick", x: labelX, y: y + barH - 2,
      "text-anchor": value >= 0 ? "start" : "end",
    }, row.display !== undefined ? row.display : formatBRL(value)));
  });

  svg.appendChild(el("line", { class: "baseline", x1: zero, x2: zero,
    y1: padT, y2: padT + rows.length * rowH - (rowH - barH) }));

  mount.innerHTML = "";
  mount.appendChild(svg);
}

(function renderFlows(flows) {
  const section = document.getElementById("flows-section");
  if (!flows || !flows.available) {
    section.innerHTML =
      `<h2>Captacao (CVM)</h2>
       <div class="note-box">Captacao indisponivel nesta execucao: importe o informe
       diario com <code>python -m shelf_monitor market</code>.</div>`;
    return;
  }

  const windowLabel = (flows.window_months ? `${flows.window_months}m fechados` : "meses fechados") +
    (flows.as_of ? ` ate ${flows.as_of}` : "");
  const withFlows = (flows.platforms || []).filter(p => p.matched > 0);
  const managers = flows.managers;

  // Tiles: o total da vitrine, a casa lider e o mercado inteiro.
  const tiles = [];
  if (withFlows.length) {
    const total12 = withFlows.reduce((a, p) => a + (p.net_12m || 0), 0);
    tiles.push(["Vitrines somadas (12m)", formatBRL(total12), windowLabel]);
    const leader = [...withFlows].sort((a, b) => (b.net_12m || 0) - (a.net_12m || 0))[0];
    tiles.push(["Casa lider em captacao", leader.label, `${formatBRL(leader.net_12m)} em 12m`]);
  }
  if (managers && managers.market) {
    tiles.push(["Mercado inteiro (12m)", formatBRL(managers.market.net_12m),
      `${(managers.market.classes || 0).toLocaleString("pt-BR")} classes com informe`]);
    const houses = managers.houses || [];
    const watchTotal = houses.reduce((a, h) => a + (h.net_12m || 0), 0);
    if (houses.length) {
      tiles.push(["Gestoras da watchlist (12m)", formatBRL(watchTotal),
        "todo o mercado, nao so prateleira"]);
    }
  }
  document.getElementById("flows-tiles").innerHTML = tiles.map(([label, value, note]) =>
    `<div class="tile"><div class="label">${escapeHtml(label)}</div>` +
    `<div class="value" style="font-size:22px">${escapeHtml(value)}</div>` +
    `<div class="note">${escapeHtml(note)}</div></div>`).join("");

  signedBars(document.getElementById("chart-flows"),
    withFlows.map(p => ({
      label: p.label, value: p.net_12m, color: platformColor[p.platform_id],
    })).sort((a, b) => (b.value || 0) - (a.value || 0)));

  const housesMount = document.getElementById("table-flows-houses");
  if (managers && (managers.houses || []).length) {
    table2(housesMount,
      ["Casa", "Liquido 3m", "Liquido 12m", "PL", "Classes"],
      managers.houses.map(h => [
        h.label, formatBRL(h.net_3m), formatBRL(h.net_12m), formatBRL(h.pl),
        (h.classes || 0).toLocaleString("pt-BR"),
      ]));
  } else {
    housesMount.innerHTML = '<p class="empty">Visao de gestoras indisponivel - requer ' +
      'a base de mercado e a captacao importadas juntas.</p>';
  }

  const moverCols = [
    { key: "name", label: "Fundo" }, { key: "manager", label: "Gestora" },
    { key: "net", label: "Liquido 12m", num: true },
    { key: "pl", label: "PL", num: true }, { key: "houses", label: "Prateleiras" },
  ];
  const moverRow = m => ({
    name: m.name, manager: m.manager || "–", net: formatBRL(m.net_12m),
    pl: formatBRL(m.pl), houses: (m.platforms || []).join(", "),
  });
  table(document.getElementById("table-flows-inflow"), moverCols,
    (flows.top_inflow || []).map(moverRow));
  table(document.getElementById("table-flows-outflow"), moverCols,
    (flows.top_outflow || []).map(moverRow));

  // Cobertura: somar so quem tem informe muda o significado do total, entao
  // o "N de M" acompanha a secao inteira.
  const parts = [];
  if (withFlows.length || (flows.platforms || []).length) {
    const coverage = (flows.platforms || []).filter(p => p.total)
      .map(p => `${p.label} ${p.matched}/${p.total}`).join(" · ");
    if (coverage) parts.push(`Fundos da vitrine com informe casado: ${coverage}.`);
  }
  if (managers && managers.coverage) {
    parts.push(`Mercado: gestora identificada em ` +
      `${(managers.coverage.classes_com_gestora || 0).toLocaleString("pt-BR")} de ` +
      `${(managers.coverage.classes_total || 0).toLocaleString("pt-BR")} classes com informe.`);
  }
  parts.push("Janela: " + windowLabel + "; o mes corrente (parcial) fica fora das somas.");
  document.getElementById("flows-coverage").textContent = parts.join(" ");
})(DATA.flows);

// --- Radar CVM --------------------------------------------------------------
const cvm = DATA.cvm;
if (cvm.available && cvm.houses && cvm.houses.length) {
  // A legenda acompanha as casas do proprio grafico - uma casa sem prateleira
  // coletada (Nu, por exemplo) ainda aparece aqui e precisa ser nomeada.
  renderLegend(
    document.getElementById("legend-cvm"),
    cvm.houses.map(h => ({ platform_id: h.house, label: h.label }))
  );
  groupedBars(
    document.getElementById("chart-cvm"),
    ["Registrados na CVM", "Em fase pre-operacional"],
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
     { key: "pre_operational", label: "Pre-operacionais", num: true }],
    cvm.houses
  );
  table(
    document.getElementById("table-pre-op"),
    [{ key: "name", label: "Fundo" }, { key: "house_label", label: "Casa" },
     { key: "manager", label: "Gestor" }, { key: "registered_at", label: "Registro" }],
    cvm.pre_operational_funds || []
  );
} else {
  document.getElementById("cvm-section").innerHTML =
    `<h2>Radar CVM</h2><p class="card-sub">Fundos registrados por gestoras ligadas as casas monitoradas.</p>
     <div class="note-box">Extrato da CVM indisponivel nesta execucao: ` +
    `${escapeHtml(cvm.error || "fonte nao coletada")}.</div>`;
}

// --- Saude da coleta --------------------------------------------------------
table(
  document.getElementById("table-sources"),
  [{ key: "label", label: "Plataforma" },
   { key: "statusPill", label: "Status", raw: true },
   { key: "strategy", label: "Estrategia" }, { key: "total_funds", label: "Fundos", num: true },
   { key: "coverage", label: "Com CNPJ", num: true }, { key: "detail", label: "Observacao" }],
  DATA.platforms.map(p => ({
    label: p.label,
    statusPill: `<span class="pill ${p.status}">${p.status}</span>`,
    strategy: p.strategy,
    total_funds: p.total_funds,
    coverage: p.total_funds ? Math.round(p.cnpj_coverage * 100) + "%" : "-",
    detail: p.error || "-",
  }))
);
"""


def _stat_tiles(data: dict) -> str:
    usable = [p for p in data["platforms"] if p["status"] != "unavailable" and p["total_funds"]]
    total_funds = sum(p["total_funds"] for p in usable)
    cvm = data.get("cvm", {})
    pre_op = sum(h["pre_operational"] for h in cvm.get("houses", [])) if cvm.get("available") else 0
    xp = next((p for p in data["platforms"] if p["platform_id"] == "xp"), None)

    tiles = [
        ("Fundos mapeados", f"{total_funds:,}".replace(",", "."),
         f"{len(usable)} de {len(data['platforms'])} plataformas coletadas"),
        ("Prateleira da XP", f"{xp['total_funds']:,}".replace(",", ".") if xp and xp["total_funds"] else "-",
         "baseline de comparacao"),
        ("Registrados na CVM", f"{cvm.get('total', 0):,}".replace(",", ".") if cvm.get("available") else "-",
         "gestoras ligadas as casas monitoradas"),
        ("Em fase pre-operacional", str(pre_op) if cvm.get("available") else "-",
         "registrados e ainda nao lancados"),
    ]
    return "\n".join(
        f'<div class="tile"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div>'
        f'<div class="note">{html.escape(note)}</div></div>'
        for label, value, note in tiles
    )


def _embed_json(data: dict) -> str:
    """Serializa para dentro de <script type="application/json">.

    Um nome de fundo contendo "</script>" encerraria o bloco e o resto viraria
    markup executavel; escapar "<" fecha essa via sem mudar o valor lido pelo
    JSON.parse.
    """
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def render_dashboard(config: Config, report: RunReport) -> Path:
    data = build_dashboard_data(report)
    generated = report.generated_at.strftime("%d/%m/%Y %H:%M")

    document = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor de Prateleiras de Fundos</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="wrap">
  <header class="page">
    <h1>Monitor de Prateleiras de Fundos</h1>
    <p class="sub">Concorrentes da XP &middot; prateleiras publicas e radar de registros na CVM
      &middot; gerado em {html.escape(generated)}</p>
  </header>

  <div class="tiles">{_stat_tiles(data)}</div>

  <section class="card" id="vitrine-section">
    <h2>Vitrine de varejo &mdash; o que o cliente ve</h2>
    <p class="card-sub">Fundos presentes na listagem da propria casa (site, API do portal ou
      area logada). O Open Finance nao entra aqui: ele e o catalogo regulatorio &mdash; tudo
      que a casa administra &mdash; e serve de referencia, nao de prateleira.</p>
    <div class="tiles" id="vitrine-tiles"></div>

    <h3 style="font-size:13.5px;font-weight:600;margin:18px 0 4px">Matriz casa &times; classe ANBIMA</h3>
    <p class="card-sub">Contagem de fundos da vitrine por classificacao ANBIMA oficial
      (join por CNPJ com o registro CVM). Mais escuro = mais fundos.</p>
    <div class="matrix-scroll" id="matrix-vitrine"></div>

    <div class="subgrid" style="margin-top:22px">
      <div>
        <h3>Arquitetura aberta</h3>
        <p class="card-sub">Produto de gestora da propria casa vs. de terceiros, na vitrine.</p>
        <div class="legend" id="legend-arch"></div>
        <div class="chart-scroll" id="chart-arch"></div>
      </div>
      <div>
        <h3>Custo e ticket por casa</h3>
        <p class="card-sub">Quartis da taxa de administracao e mediana do minimo &mdash;
          quartis mostram a forma da prateleira; media esconderia.</p>
        <div id="table-costs"></div>
      </div>
    </div>

    <details><summary>Ver a matriz como tabela</summary><div id="table-vitrine"></div></details>
    <div class="coverage" id="vitrine-coverage"></div>
  </section>

  <section class="card">
    <h2>Amplitude por classe de ativo</h2>
    <p class="card-sub">Quantos fundos cada casa oferta em cada classe - a leitura mais direta
      de onde cada prateleira e forte.</p>
    <div class="legend" id="legend-classes"></div>
    <div class="chart-scroll" id="chart-classes"></div>
    <details><summary>Ver tabela</summary><div id="table-classes"></div></details>
  </section>

  <section class="card">
    <h2>Profundidade: gestoras representadas</h2>
    <p class="card-sub">Numero de gestoras distintas na prateleira. Amplitude sem profundidade
      indica concentracao em poucas casas de gestao.</p>
    <div class="chart-scroll" id="chart-managers"></div>
    <details><summary>Ver as maiores gestoras de cada prateleira</summary>
      <div id="table-top-managers"></div></details>
  </section>

  <section class="card">
    <h2>Aplicacao minima</h2>
    <p class="card-sub">Distribuicao dos fundos por faixa de investimento minimo - separa quem
      mira varejo de quem mira alta renda.</p>
    <div class="legend" id="legend-bands"></div>
    <div class="chart-scroll" id="chart-bands"></div>
    <details><summary>Ver tabela</summary><div id="table-bands"></div></details>
  </section>

  <section class="card">
    <h2>Diferenciais de prateleira</h2>
    <p class="card-sub">Fundos de varejo (por CNPJ) que so uma das casas coletadas oferta -
      o conceito competitivo, nada a ver com "fundo exclusivo" regulatorio, que fica fora
      desta analise. Considera somente casas com coleta bem-sucedida.</p>
    <div class="chart-scroll" id="chart-exclusive"></div>
  </section>

  <section class="card">
    <h2>Fundos exclusivos e restritos &mdash; auxiliar</h2>
    <p class="card-sub">Veiculos fora do perfil de varejo (exclusivo no registro CVM,
      publico profissional, marcados pela casa ou pelo nome). Ficam fora de toda analise
      acima; aqui e o inventario deles.</p>
    <div id="table-restricted"></div>
  </section>

  <section class="card" id="flows-section">
    <h2>Captacao (CVM) &mdash; fluxo financeiro</h2>
    <p class="card-sub">Captacao liquida (aplicacoes menos resgates) do informe diario da
      CVM, em meses fechados &mdash; o mes corrente e parcial e fica fora das somas. As
      barras somam so os fundos da vitrine com informe casado; a cobertura esta no rodape
      da secao.</p>
    <div class="tiles" id="flows-tiles"></div>
    <div class="subgrid" style="margin-top:8px">
      <div>
        <h3>Captacao liquida da vitrine por casa (12m)</h3>
        <p class="card-sub">Barras para a esquerda sao resgate liquido.</p>
        <div class="chart-scroll" id="chart-flows"></div>
      </div>
      <div>
        <h3>Gestoras da watchlist no mercado inteiro</h3>
        <p class="card-sub">Captacao das gestoras ligadas a cada casa em TODO o mercado
          &mdash; independe de a prateleira ofertar o fundo.</p>
        <div id="table-flows-houses"></div>
      </div>
    </div>
    <details open><summary>Maiores captadores da prateleira (12m)</summary>
      <div id="table-flows-inflow"></div></details>
    <details><summary>Maiores resgates da prateleira (12m)</summary>
      <div id="table-flows-outflow"></div></details>
    <div class="coverage" id="flows-coverage"></div>
  </section>

  <section class="card" id="cvm-section">
    <h2>Radar CVM &mdash; visao antecipada</h2>
    <p class="card-sub">Fundos registrados na CVM por gestoras ligadas a cada casa. Um fundo em
      fase pre-operacional foi registrado mas ainda nao lancado: e o sinal mais antecipado
      da estrategia de produto da casa.</p>
    <div class="legend" id="legend-cvm"></div>
    <div class="chart-scroll" id="chart-cvm"></div>
    <details><summary>Ver tabela por casa</summary><div id="table-cvm"></div></details>
    <details open><summary>Fundos em fase pre-operacional</summary><div id="table-pre-op"></div></details>
  </section>

  <section class="card">
    <h2>Saude da coleta</h2>
    <p class="card-sub">Uma prateleira que aparece pequena pode ser fonte bloqueada, nao
      prateleira enxuta. Confira o status antes de comparar numeros.</p>
    <div id="table-sources"></div>
  </section>

  <footer>
    Fontes publicas: sites das plataformas, API de Dados Abertos de Investimentos do Open
    Finance Brasil, cadastro e informe diario de fundos da CVM (dados.cvm.gov.br).
  </footer>
</div>

<script type="application/json" id="dashboard-data">{_embed_json(data)}</script>
<script>{_SCRIPT}</script>
</body>
</html>
"""

    config.reports_dir.mkdir(parents=True, exist_ok=True)
    path = config.reports_dir / "dashboard.html"
    path.write_text(document, encoding="utf-8")
    return path
