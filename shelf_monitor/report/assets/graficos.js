/* Primitivos de pagina e de grafico, comuns aos paineis do monitor.

   Navegacao dos paineis recolhidos, desenho sob demanda na largura real,
   formatos brasileiros e as formas de grafico em SVG (barras, divergentes,
   dumbbell, empilhadas, 100%, colunas, fluxo, tabelas) e a rampa de cinza.
   Sai de `dashboard.js` para servir tambem o painel da industria; cada
   pagina embute este arquivo e o seu proprio na MESMA tag de script.

   Nada aqui roda codigo que dependa dos dados da pagina no carregamento:
   `legendaDeCasas` e `colunasPorCasa` leem `platformColor`/`BASE_ID`, que so
   o dashboard das vitrines define - e so ele as chama. */

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

/* --- Desenho na largura real --------------------------------------------------
   Os paineis nascem recolhidos: um grafico desenhado no load teria largura
   zero. Cada grafico se registra aqui e e desenhado quando o container ganha
   largura (painel aberto) e de novo quando a largura muda (janela, celular
   girado) - na largura de verdade, com o texto no tamanho de verdade, em vez
   de um SVG fixo encolhido ate a letra ficar ilegivel no celular. */
const _desenhos = new Map();
function quandoVisivel(mount, desenhar) {
  if (!mount) return;
  const item = { mount, desenhar, largura: 0 };
  _desenhos.set(mount, item);
  _tentarDesenho(item);
}
function _tentarDesenho(item) {
  const largura = Math.floor(item.mount.clientWidth);
  if (largura > 0 && Math.abs(largura - item.largura) > 6) {
    item.largura = largura;
    try {
      item.desenhar(largura);
    } catch (erro) {
      // Um grafico que quebra nao leva junto os outros nem a navegacao.
      item.mount.innerHTML = '<p class="empty">Não foi possível desenhar este gráfico.</p>';
      console.error(erro);
    }
  }
}
function _redesenhar() { _desenhos.forEach(_tentarDesenho); }
document.addEventListener("toggle", _redesenhar, true);
let _espera = null;
window.addEventListener("resize", () => {
  clearTimeout(_espera);
  _espera = setTimeout(_redesenhar, 120);
});
window.addEventListener("load", _redesenhar);

/* --- Formatos em portugues ---------------------------------------------------- */
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
function inteiro(v) { return (v || 0).toLocaleString("pt-BR"); }
const ok = v => v !== null && v !== undefined && !Number.isNaN(v);
// "+R$ 1,3 bi", "-R$ 690 mi" - o mesmo formato de retail_flow._reais.
function reais(v) {
  if (!ok(v)) return "-";
  const sinal = v > 0 ? "+" : v < 0 ? "-" : "";
  const a = Math.abs(v);
  const texto = a >= 1e9 ? numBR(a / 1e9, 1) + " bi"
    : a >= 1e7 ? numBR(a / 1e6, 0) + " mi" : numBR(a / 1e6, 1) + " mi";
  return `${sinal}R$ ${texto}`;
}
function pct1(v) { return ok(v) ? numBR(v, 1) + "%" : "-"; }
function parcela(v) { return ok(v) ? Math.round(v * 100) + "%" : "-"; }
function ordinalF(n) { return `${n}ª`; }
function plural(n, um, varios) { return n === 1 ? um : varios; }
/* Quem lidera mais linhas de uma matriz (classe, categoria): a manchete fala
   da casa-base quando ela lidera alguma, e senao da casa que lidera mais -
   "a XP tem a maior prateleira em 0 de 7 classes" nao e manchete. */
function manchetePorLinhas(linhas, casas, valor, coisa, coisas) {
  const conta = casas.map(c => ({
    casa: c,
    n: linhas.filter(l => {
      const v = valor(l, c);
      return v > 0 && casas.every(o => valor(l, o) <= v);
    }).length,
  }));
  const daBase = conta.find(x => x.casa.platform_id === BASE_ID);
  const topo = conta.slice().sort((a, b) => b.n - a.n)[0];
  const alvo = daBase && daBase.n > 0 ? daBase : topo;
  if (!alvo || !alvo.n) return "";
  const total = linhas.length;
  return `${alvo.casa.label} tem a maior prateleira em ${alvo.n} de ${total} ${plural(total, coisa, coisas)}`;
}
function juntar(itens) {
  if (itens.length <= 1) return itens.join("");
  return itens.slice(0, -1).join(", ") + " e " + itens[itens.length - 1];
}
const cortar = (s, n) => (s && s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s || "");

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

// Rodape de proveniencia como lista de notas curtas: um item por ressalva,
// em vez de um paragrafo corrido de seis frases.
function notas(el, itens) {
  if (!el) return;
  const validos = itens.filter(Boolean);
  el.innerHTML = validos.length
    ? "<ul>" + validos.map(n => `<li>${escapeHtml(n)}</li>`).join("") + "</ul>" : "";
}

/* --- Figura: etiqueta, titulo que afirma, subtitulo, corpo, fonte ------------
   A anatomia de todo grafico (The Economist, McKinsey). O titulo diz a
   leitura, nao o nome do grafico; o subtitulo diz o que esta medido, a unidade
   e o prazo; o rodape diz de onde vem o numero. Idempotente: redesenhar (o
   seletor de prazo da rentabilidade) so troca os textos. */
function figura(mount, o) {
  if (!mount) return null;
  let fig = mount.closest("figure.fig");
  if (!fig) {
    fig = document.createElement("figure");
    fig.className = "fig";
    mount.parentNode.insertBefore(fig, mount);
    const titulo = document.createElement("h4");
    titulo.className = "fig-title";
    const sub = document.createElement("p");
    sub.className = "fig-sub";
    fig.append(titulo, sub);
    if (o.legenda) fig.appendChild(o.legenda);
    fig.appendChild(mount);
    const fonte = document.createElement("p");
    fonte.className = "fig-source";
    fig.appendChild(fonte);
  }
  fig.querySelector(".fig-title").textContent = o.titulo || "";
  const sub = fig.querySelector(".fig-sub");
  sub.textContent = o.sub || "";
  sub.hidden = !o.sub;
  const fonte = fig.querySelector(".fig-source");
  fonte.textContent = o.fonte || "";
  fonte.hidden = !o.fonte;
  return fig;
}

function legenda(mount, itens) {
  if (!mount) return;
  mount.innerHTML = itens.map(i => {
    const forma = i.forma === "vazado"
      ? `<i class="swatch dot" style="background:var(--surface-1);box-shadow:inset 0 0 0 2.5px ${i.cor}"></i>`
      : i.forma === "traco"
        ? `<i class="swatch" style="width:3px;height:13px;border-radius:0;background:${i.cor}"></i>`
        : `<i class="swatch${i.forma === "ponto" ? " dot" : ""}" style="background:${i.cor}"></i>`;
    return `<span>${forma}${escapeHtml(i.rotulo)}</span>`;
  }).join("");
}
function legendaDeCasas(mount, casas) {
  legenda(mount, casas.map(c => ({ rotulo: c.label, cor: corDaCasa(c.platform_id), forma: "ponto" })));
}

/* --- Primitivas de SVG ------------------------------------------------------ */
const SVG_NS = "http://www.w3.org/2000/svg";
function el(name, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}
function svgRaiz(largura, altura, rotulo) {
  return el("svg", {
    viewBox: `0 0 ${largura} ${altura}`, width: largura, height: altura,
    role: "img", "aria-label": rotulo || "gráfico",
  });
}
const VAZIO = '<p class="empty">Sem dados coletados para este painel.</p>';
// Largura aproximada de um texto: bastante para reservar a coluna de rotulos
// sem medir no DOM (o painel pode estar fechado na hora).
function larguraTexto(s, px = 12.5) { return String(s).length * px * 0.55; }
function titulo(no, texto) { no.appendChild(el("title", {}, texto)); return no; }

// Passo "redondo" do eixo: 1, 2, 2,5 ou 5 vezes uma potencia de dez.
function passoRedondo(bruto) {
  const mag = Math.pow(10, Math.floor(Math.log10(bruto)));
  const r = bruto / mag;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * mag;
}
function escala(min, max, alvo = 5) {
  if (!(max > min)) { max = min + 1; }
  const passo = passoRedondo((max - min) / alvo);
  const lo = Math.floor(min / passo + 1e-9) * passo;
  const hi = Math.ceil(max / passo - 1e-9) * passo;
  const ticks = [];
  for (let v = lo; v <= hi + passo / 2; v += passo) ticks.push(Number(v.toFixed(10)));
  return { lo, hi, passo, ticks };
}

// Barra deitada: ponta arredondada em 3px do lado do dado, base reta no zero.
function barra(x, y, w, h, cor, dica, paraDireita = true) {
  const r = Math.min(3, w, h / 2);
  const d = paraDireita
    ? `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}` +
      `Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`
    : `M${x + w},${y}H${x + r}Q${x},${y} ${x},${y + r}V${y + h - r}` +
      `Q${x},${y + h} ${x + r},${y + h}H${x + w}Z`;
  const p = el("path", { class: "bar", d, fill: cor });
  if (dica) titulo(p, dica);
  return p;
}
// Coluna em pe: ponta arredondada do lado do dado (em cima se positiva).
function coluna(x, yZero, w, yValor, cor, dica) {
  const h = Math.abs(yValor - yZero);
  if (h <= 0) return null;
  const r = Math.min(2, w / 2, h);
  const d = yValor < yZero
    ? `M${x},${yZero}V${yValor + r}Q${x},${yValor} ${x + r},${yValor}H${x + w - r}` +
      `Q${x + w},${yValor} ${x + w},${yValor + r}V${yZero}Z`
    : `M${x},${yZero}V${yValor - r}Q${x},${yValor} ${x + r},${yValor}H${x + w - r}` +
      `Q${x + w},${yValor} ${x + w},${yValor - r}V${yZero}Z`;
  const p = el("path", { class: "bar", d, fill: cor });
  if (dica) titulo(p, dica);
  return p;
}

/* Barras deitadas de serie unica (The Economist): rotulo da casa a esquerda,
   valor escrito na ponta - nenhum valor fica so no tooltip -, a casa-base em
   negrito. `extra` e um texto fraco depois do valor ("de 775 no catalogo"). */
/* Celular: abaixo desta largura, o rotulo da linha sai da coluna da esquerda
   e sobe para cima da marca, com o valor alinhado a direita na mesma linha -
   a marca ganha a largura toda. Com rotulo de 150px e valor de 150px ao lado,
   sobravam 40px de grafico numa tela de 360. */
const COMPACTO = 520;
function linhaDeRotulo(svg, y, largura, rotulo, valor, base, extra) {
  const classe = base ? " base" : "";
  svg.appendChild(el("text", { class: "axis-label" + classe, x: 0, y }, rotulo));
  if (valor !== undefined && valor !== "") {
    const t = el("text", { class: "val" + classe, x: largura, y, "text-anchor": "end" }, valor);
    if (extra) t.appendChild(el("tspan", { class: "val fraco", dx: 4 }, extra));
    svg.appendChild(t);
  }
}

function barrasH(mount, linhas, o = {}) {
  quandoVisivel(mount, largura => {
    if (!linhas.length) { mount.innerHTML = VAZIO; return; }
    const fmt = o.fmt || inteiro;
    const texto = l => (l.display !== undefined ? l.display : fmt(l.value));
    if (largura < COMPACTO) {
      const W = largura, rowH = 42, barH = 12;
      const max = o.max || Math.max(1e-9, ...linhas.map(l => l.value));
      const svg = svgRaiz(W, linhas.length * rowH, o.aria);
      linhas.forEach((l, i) => {
        const y = i * rowH;
        linhaDeRotulo(svg, y + 13, W, cortar(l.label, 30), texto(l), l.base, l.extra);
        const w = Math.max(0, (l.value / max) * W);
        if (w > 0) svg.appendChild(barra(0, y + 20, w, barH, l.color || "var(--comp-1)", `${l.label}: ${texto(l)}`));
      });
      mount.replaceChildren(svg);
      return;
    }
    const W = Math.max(largura, 260);
    const rotW = Math.min(W * 0.4, Math.max(80, ...linhas.map(l => larguraTexto(l.label) + 14)));
    const valW = Math.max(...linhas.map(l =>
      larguraTexto(texto(l), 12) + (l.extra ? larguraTexto(" " + l.extra, 11.5) : 0))) + 10;
    const plotW = Math.max(40, W - rotW - valW);
    const rowH = o.rowH || 28, barH = o.barH || 14, padT = 2;
    const H = padT + linhas.length * rowH + 2;
    const max = o.max || Math.max(1e-9, ...linhas.map(l => l.value));
    const svg = svgRaiz(W, H, o.aria);
    linhas.forEach((l, i) => {
      const y = padT + i * rowH + (rowH - barH) / 2;
      const w = Math.max(0, (l.value / max) * plotW);
      const base = l.base ? " base" : "";
      svg.appendChild(el("text", {
        class: "axis-label" + base, x: rotW - 10, y: y + barH / 2 + 4, "text-anchor": "end",
      }, cortar(l.label, 40)));
      if (w > 0) svg.appendChild(barra(rotW, y, w, barH, l.color || "var(--comp-1)", `${l.label}: ${texto(l)}`));
      const val = el("text", { class: "val" + base, x: rotW + w + 6, y: y + barH / 2 + 4 }, texto(l));
      if (l.extra) {
        val.appendChild(el("tspan", { class: "val fraco", dx: 4 }, l.extra));
      }
      svg.appendChild(val);
    });
    svg.appendChild(el("line", { class: "baseline", x1: rotW, x2: rotW, y1: padT, y2: H - 2 }));
    mount.replaceChildren(svg);
  });
}

/* Barras divergentes a partir de um zero vermelho (ganho e perda, The
   Economist): negativo para a esquerda, positivo para a direita, valor na
   ponta de cada barra, do lado de fora. */
function barrasDivergentes(mount, linhas, o = {}) {
  quandoVisivel(mount, largura => {
    if (!linhas.length) { mount.innerHTML = VAZIO; return; }
    const fmt = o.fmt || reais;
    const texto = l => (l.display !== undefined ? l.display : fmt(l.value));
    if (largura < COMPACTO) {
      const W = largura, rowH = 42, barH = 12;
      const lo = Math.min(0, ...linhas.map(l => l.value));
      const hi = Math.max(0, ...linhas.map(l => l.value));
      const x = v => ((v - lo) / ((hi - lo) || 1)) * W;
      const H = linhas.length * rowH;
      const svg = svgRaiz(W, H, o.aria);
      linhas.forEach((l, i) => {
        const y = i * rowH;
        linhaDeRotulo(svg, y + 13, W, cortar(l.label, 28), texto(l), l.base);
        const xv = x(l.value), x0 = x(0), w = Math.abs(xv - x0);
        if (w > 0.5) {
          svg.appendChild(barra(Math.min(xv, x0), y + 20, w, barH, l.color || "var(--comp-1)",
            `${l.label}: ${texto(l)}`, l.value >= 0));
        }
      });
      svg.appendChild(el("line", { class: "zeroline", x1: x(0), x2: x(0), y1: 16, y2: H }));
      mount.replaceChildren(svg);
      return;
    }
    const W = Math.max(largura, 280);
    const maxRot = o.maxRotulo || 40;
    const rotW = Math.min(W * 0.42, Math.max(80, ...linhas.map(l => larguraTexto(cortar(l.label, maxRot)) + 14)));
    const valW = Math.max(...linhas.map(l => larguraTexto(texto(l), 12))) + 10;
    const lo = Math.min(0, ...linhas.map(l => l.value));
    const hi = Math.max(0, ...linhas.map(l => l.value));
    const padE = lo < 0 ? valW : 6, padD = hi > 0 ? valW + (o.extraW || 0) : 6;
    const plotW = Math.max(40, W - rotW - padE - padD);
    const x = v => rotW + padE + ((v - lo) / ((hi - lo) || 1)) * plotW;
    const rowH = o.rowH || 28, barH = o.barH || 14, padT = 2;
    const H = padT + linhas.length * rowH + 2;
    const svg = svgRaiz(W, H, o.aria);
    const x0 = x(0);
    linhas.forEach((l, i) => {
      const y = padT + i * rowH + (rowH - barH) / 2;
      const xv = x(l.value);
      const base = l.base ? " base" : "";
      svg.appendChild(el("text", {
        class: "axis-label" + base, x: rotW - 10, y: y + barH / 2 + 4, "text-anchor": "end",
      }, cortar(l.label, maxRot)));
      const w = Math.abs(xv - x0);
      if (w > 0.5) {
        svg.appendChild(barra(Math.min(xv, x0), y, w, barH, l.color || "var(--comp-1)",
          `${l.label}: ${texto(l)}`, l.value >= 0));
      }
      const positivo = l.value >= 0;
      svg.appendChild(el("text", {
        class: "val" + base, x: positivo ? xv + 6 : xv - 6, y: y + barH / 2 + 4,
        "text-anchor": positivo ? "start" : "end",
      }, texto(l)));
    });
    svg.appendChild(el("line", { class: "zeroline", x1: x0, x2: x0, y1: 0, y2: H }));
    mount.replaceChildren(svg);
  });
}

/* Dumbbell (The Economist): uma linha por item, um ponto por serie num eixo
   comum, o traco cinza ligando o menor ao maior. Ponto pode cortar o eixo sem
   mentir - barra nao -, entao e a forma para diferencas pequenas perto do nivel
   (retorno de 12 meses entre 11% e 13%). Serie com `cor` pinta todas as linhas
   dela; sem `cor`, o ponto leva a cor da linha (a casa). */
function dumbbell(mount, linhas, series, o = {}) {
  quandoVisivel(mount, largura => {
    const valores = linhas.flatMap(l => series.map(s => l.valores[s.chave])).filter(ok);
    if (!valores.length) { mount.innerHTML = VAZIO; return; }
    const compacto = largura < COMPACTO;
    const W = compacto ? largura : Math.max(largura, 300);
    const fmtEixo = o.fmtEixo || (v => numBR(v, 0));
    const fmt = o.fmt || (v => numBR(v, 1));
    let lo = Math.min(...valores), hi = Math.max(...valores);
    if (o.ref !== undefined) { lo = Math.min(lo, o.ref); hi = Math.max(hi, o.ref); }
    if (o.doZero) lo = Math.min(0, lo);
    const folga = (hi - lo) * 0.06 || Math.abs(hi) * 0.05 || 1;
    const esc = escala(o.doZero ? lo : lo - folga, hi + folga, W < 520 ? 4 : 6);
    // No compacto o rotulo e a nota sobem para uma linha acima dos pontos.
    const rotW = compacto ? 10
      : Math.min(W * 0.36, Math.max(70, ...linhas.map(l => larguraTexto(cortar(l.label, 34)) + 14)));
    const notas = linhas.map(l => (o.nota ? o.nota(l) : ""));
    const notaW = compacto ? 0 : Math.max(0, ...notas.map(n => (n ? larguraTexto(n, 12) + 14 : 0)));
    const plotW = Math.max(60, W - rotW - notaW - 10);
    const x = v => rotW + ((v - esc.lo) / ((esc.hi - esc.lo) || 1)) * plotW;
    const rowH = compacto ? 46 : (o.rowH || 30), padT = 24;
    // O rotulo da referencia mora numa faixa propria embaixo, sem encostar no
    // ponto da ultima linha.
    const faixaRef = o.ref !== undefined && o.refRotulo ? 18 : 0;
    const fimPlot = padT + linhas.length * rowH + 6;
    const H = fimPlot + faixaRef;
    const svg = svgRaiz(W, H, o.aria);
    esc.ticks.forEach(t => {
      svg.appendChild(el("line", { class: "gridline", x1: x(t), x2: x(t), y1: padT - 6, y2: fimPlot - 4 }));
      svg.appendChild(el("text", { class: "tick", x: x(t), y: 12, "text-anchor": "middle" }, fmtEixo(t)));
    });
    if (o.ref !== undefined) {
      svg.appendChild(el("line", { class: "refline", x1: x(o.ref), x2: x(o.ref), y1: padT - 8, y2: fimPlot - 2 }));
    }
    linhas.forEach((l, i) => {
      const cy = padT + i * rowH + (compacto ? rowH - 13 : rowH / 2);
      const base = l.base ? " base" : "";
      if (compacto) {
        linhaDeRotulo(svg, cy - 16, W, cortar(l.label, 30), notas[i], l.base);
      } else {
        svg.appendChild(el("text", {
          class: "axis-label" + base, x: rotW - 12, y: cy + 4, "text-anchor": "end",
        }, cortar(l.label, 34)));
      }
      const vs = series.map(s => l.valores[s.chave]).filter(ok);
      if (vs.length > 1) {
        svg.appendChild(el("line", {
          class: "connector", x1: x(Math.min(...vs)), x2: x(Math.max(...vs)), y1: cy, y2: cy,
        }));
      }
      series.forEach(s => {
        const v = l.valores[s.chave];
        if (!ok(v)) return;
        const cor = s.cor || l.cor || "var(--comp-1)";
        const dica = `${l.label} - ${s.rotulo}: ${fmt(v)}`;
        let marca;
        if (s.forma === "traco") {
          marca = el("rect", { x: x(v) - 1.5, y: cy - 8, width: 3, height: 16, fill: cor });
        } else if (s.forma === "vazado") {
          marca = el("circle", { cx: x(v), cy, r: 5.5, fill: "var(--surface-1)", stroke: cor, "stroke-width": 2.5 });
          // O anel amarelo da XP some no branco: um anel preto fino por fora.
          if (cor === "var(--casa-xp)") {
            svg.appendChild(el("circle", {
              cx: x(v), cy, r: 7, fill: "none", stroke: "var(--contorno-xp)", "stroke-width": 1,
            }));
          }
        } else {
          marca = el("circle", { class: "dot", cx: x(v), cy, r: l.base || s.base ? 7 : 6, fill: cor });
        }
        svg.appendChild(titulo(marca, dica));
      });
      if (notas[i] && !compacto) {
        svg.appendChild(el("text", {
          class: "val" + base, x: rotW + plotW + 14, y: cy + 4,
        }, notas[i]));
      }
      const alvo = el("rect", { class: "hit", x: 0, y: padT + i * rowH, width: W, height: rowH });
      svg.appendChild(titulo(alvo, series.filter(s => ok(l.valores[s.chave]))
        .map(s => `${s.rotulo}: ${fmt(l.valores[s.chave])}`).join(" · ") + ` (${l.label})`));
    });
    if (faixaRef) {
      svg.appendChild(el("text", {
        class: "tick", x: x(o.ref), y: H - 4, "text-anchor": "middle",
      }, `${(o.fmtRef || fmtEixo)(o.ref)} = ${o.refRotulo}`));
    }
    mount.replaceChildren(svg);
  });
}

/* Barras empilhadas deitadas, com sinal (McKinsey): os componentes positivos
   empilham para a direita do zero, os negativos para a esquerda, e o traco
   preto marca o liquido - a soma que o leitor procura -, escrito a direita.
   Sem nenhum negativo, o zero vira a linha de base comum. */
function empilhadas(mount, linhas, partes, o = {}) {
  quandoVisivel(mount, largura => {
    if (!linhas.length) { mount.innerHTML = VAZIO; return; }
    const fmt = o.fmt || reais;
    const compacto = largura < COMPACTO;
    const W = compacto ? largura : Math.max(largura, 280);
    const rotW = compacto ? 0 : Math.min(W * 0.36, Math.max(70, ...linhas.map(l => larguraTexto(l.label) + 14)));
    const pos = l => partes.reduce((a, p) => a + Math.max(0, l.valores[p.chave] || 0), 0);
    const neg = l => partes.reduce((a, p) => a + Math.min(0, l.valores[p.chave] || 0), 0);
    const lo = Math.min(0, ...linhas.map(neg));
    const hi = Math.max(0, ...linhas.map(pos), ...linhas.map(l => (ok(l.total) ? l.total : 0)));
    const totW = compacto || o.semTotal ? 4 : Math.max(...linhas.map(l => larguraTexto(fmt(l.total), 12))) + 18;
    const plotW = Math.max(40, W - rotW - totW - 8);
    const x = v => rotW + 4 + ((v - lo) / ((hi - lo) || 1)) * plotW;
    const rowH = compacto ? 44 : (o.rowH || 30), barH = o.barH || 14, padT = 2;
    const H = padT + linhas.length * rowH + 2;
    const svg = svgRaiz(W, H, o.aria);
    const x0 = x(0);
    linhas.forEach((l, i) => {
      const y = compacto ? padT + i * rowH + 22 : padT + i * rowH + (rowH - barH) / 2;
      const base = l.base ? " base" : "";
      if (compacto) {
        linhaDeRotulo(svg, y - 8, W, l.label, o.semTotal || !ok(l.total) ? "" : fmt(l.total), l.base);
      } else {
        svg.appendChild(el("text", {
          class: "axis-label" + base, x: rotW - 10, y: y + barH / 2 + 4, "text-anchor": "end",
        }, l.label));
      }
      let direita = x0, esquerda = x0;
      partes.forEach(p => {
        const v = l.valores[p.chave] || 0;
        if (!v) return;
        const w = Math.abs(x(v) - x0);
        if (w < 0.5) return;
        const dica = `${l.label} - ${p.rotulo}: ${fmt(v)}`;
        if (v > 0) {
          svg.appendChild(barra(direita, y, Math.max(0.5, w - 2), barH, p.cor, dica));
          direita += w;
        } else {
          svg.appendChild(barra(esquerda - w + 2, y, Math.max(0.5, w - 2), barH, p.cor, dica, false));
          esquerda -= w;
        }
      });
      if (!o.semTotal && ok(l.total)) {
        const xt = x(l.total);
        svg.appendChild(titulo(el("rect", {
          x: xt - 1.5, y: y - 4, width: 3, height: barH + 8, fill: "var(--text-primary)",
        }), `${l.label} - líquido: ${fmt(l.total)}`));
        if (!compacto) {
          svg.appendChild(el("text", {
            class: "val" + base, x: rotW + plotW + 12, y: y + barH / 2 + 4,
          }, fmt(l.total)));
        }
      }
    });
    svg.appendChild(el("line", {
      class: lo < 0 ? "zeroline" : "baseline", x1: x0, x2: x0, y1: 0, y2: H,
    }));
    mount.replaceChildren(svg);
  });
}

/* Barras 100% empilhadas: uma por casa, a parcela de cada parte. O rotulo sai
   dentro do segmento so quando cabe com folga - nunca cortado -; o resto fica
   na legenda, no tooltip e na tabela. */
function cemPorCento(mount, linhas, partes, o = {}) {
  quandoVisivel(mount, largura => {
    const validas = linhas.filter(l => partes.some(p => (l.valores[p.chave] || 0) > 0));
    if (!validas.length) { mount.innerHTML = VAZIO; return; }
    const compacto = largura < COMPACTO;
    const W = compacto ? largura : Math.max(largura, 280);
    const rotW = compacto ? 0 : Math.min(W * 0.36, Math.max(70, ...validas.map(l => larguraTexto(l.label) + 14)));
    const plotW = Math.max(60, W - rotW - (compacto ? 0 : 8));
    const rowH = compacto ? 46 : (o.rowH || 32), barH = o.barH || 18, padT = 2;
    const H = padT + validas.length * rowH + 2;
    const svg = svgRaiz(W, H, o.aria);
    validas.forEach((l, i) => {
      const y = compacto ? padT + i * rowH + 22 : padT + i * rowH + (rowH - barH) / 2;
      const total = partes.reduce((a, p) => a + (l.valores[p.chave] || 0), 0);
      const base = l.base ? " base" : "";
      if (compacto) {
        linhaDeRotulo(svg, y - 8, W, l.label, "", l.base);
      } else {
        svg.appendChild(el("text", {
          class: "axis-label" + base, x: rotW - 10, y: y + barH / 2 + 4, "text-anchor": "end",
        }, l.label));
      }
      let xx = rotW;
      partes.forEach(p => {
        const v = l.valores[p.chave] || 0;
        if (!v) return;
        const w = (v / total) * plotW;
        const share = Math.round((v / total) * 100);
        const r = el("rect", { class: "bar", x: xx, y, width: Math.max(0.5, w - 2), height: barH, fill: p.cor });
        svg.appendChild(titulo(r, `${l.label} - ${p.rotulo}: ${share}% (${inteiro(v)})`));
        const rotulo = `${share}%`;
        if (w - 2 >= larguraTexto(rotulo, 11.5) + 10) {
          svg.appendChild(el("text", {
            x: xx + (w - 2) / 2, y: y + barH / 2 + 4, "text-anchor": "middle",
            "font-size": 11.5, "font-weight": 600, fill: p.tinta || "#ffffff",
          }, rotulo));
        }
        xx += w;
      });
    });
    mount.replaceChildren(svg);
  });
}

/* Colunas de ganho e perda por competencia, uma miniatura por casa, todas na
   mesma escala (The Economist): o zero em vermelho, o valor escrito so na
   ultima coluna de cada casa - o resto fica no eixo e no tooltip. */
function colunasPorCasa(mount, paineis, o = {}) {
  quandoVisivel(mount, largura => {
    const valores = paineis.flatMap(p => p.pontos.map(pt => pt.valor)).filter(ok);
    if (!valores.length) { mount.innerHTML = VAZIO; return; }
    const fmt = o.fmt || reais;
    const fmtEixo = o.fmtEixo || fmt;
    const esc = escala(Math.min(0, ...valores), Math.max(0, ...valores), 4);
    const tickW = Math.max(...esc.ticks.map(t => larguraTexto(fmtEixo(t), 11.5))) + 10;
    const porLinha = largura < 560 ? 1 : Math.min(paineis.length, 3);
    const gapX = 26;
    const painelW = (largura - tickW - gapX * (porLinha - 1)) / porLinha;
    const tituloH = 22, plotH = o.altura || 120, eixoH = 20, gapY = 18;
    const linhas = Math.ceil(paineis.length / porLinha);
    const H = linhas * (tituloH + plotH + eixoH) + (linhas - 1) * gapY;
    const svg = svgRaiz(largura, H, o.aria);
    const y = (v, topo) => topo + tituloH + ((esc.hi - v) / ((esc.hi - esc.lo) || 1)) * plotH;
    paineis.forEach((p, k) => {
      const lin = Math.floor(k / porLinha), col = k % porLinha;
      const topo = lin * (tituloH + plotH + eixoH + gapY);
      const esq = tickW + col * (painelW + gapX);
      svg.appendChild(el("rect", { x: esq, y: topo + 5, width: 10, height: 10, rx: 2, fill: p.cor }));
      svg.appendChild(el("text", { class: "facet-title", x: esq + 16, y: topo + 14 }, p.label));
      esc.ticks.forEach(t => {
        svg.appendChild(el("line", {
          class: t === 0 ? "zeroline" : "gridline", x1: esq, x2: esq + painelW, y1: y(t, topo), y2: y(t, topo),
        }));
        if (col === 0) {
          svg.appendChild(el("text", {
            class: "tick", x: esq - 8, y: y(t, topo) + 4, "text-anchor": "end",
          }, fmtEixo(t)));
        }
      });
      const n = p.pontos.length;
      const slot = painelW / Math.max(1, n);
      const w = Math.min(28, slot * 0.58);
      p.pontos.forEach((pt, j) => {
        const cx = esq + slot * j + slot / 2;
        if (ok(pt.valor)) {
          const c = coluna(cx - w / 2, y(0, topo), w, y(pt.valor, topo), p.cor, pt.dica || `${p.label} - ${pt.rotulo}: ${fmt(pt.valor)}`);
          if (c) svg.appendChild(c);
          if (j === n - 1) {
            const acima = pt.valor >= 0;
            svg.appendChild(el("text", {
              class: "val", x: cx, y: acima ? y(pt.valor, topo) - 5 : y(pt.valor, topo) + 13,
              "text-anchor": "middle",
            }, fmt(pt.valor)));
          }
        }
        svg.appendChild(el("text", {
          class: "tick", x: cx, y: topo + tituloH + plotH + 15, "text-anchor": "middle",
        }, pt.rotulo));
      });
    });
    mount.replaceChildren(svg);
  });
}

/* Fluxo em tres colunas (McKinsey): cada no e uma soma, cada faixa leva a
   parte que passa de um no ao seguinte, com espessura proporcional. Os nos de
   uma coluna empilham na ordem dada; o rotulo mora ao lado do no, com halo da
   superficie quando cruza uma faixa. */
function fluxo(mount, nos, ligacoes, o = {}) {
  quandoVisivel(mount, largura => {
    if (!nos.length) { mount.innerHTML = VAZIO; return; }
    // Tres colunas de rotulo nao cabem em 360px: abaixo de 600 o fluxo mantem
    // a largura e rola dentro da propria caixa, como as tabelas largas.
    const W = Math.max(largura, 600);
    const colunas = Math.max(...nos.map(n => n.coluna)) + 1;
    const noW = 12, gap = o.gap || 14;
    const rotE = Math.max(...nos.filter(n => n.coluna === 0).map(n => larguraTexto(n.rotulo, 12.5))) + 16;
    const rotD = Math.max(...nos.filter(n => n.coluna === colunas - 1)
      .map(n => Math.max(larguraTexto(n.rotulo, 12.5), larguraTexto(inteiro(n.valor), 13)))) + 16;
    const H = o.altura || 300;
    const somaPorColuna = [...Array(colunas)].map((_, c) =>
      nos.filter(n => n.coluna === c).reduce((a, n) => a + n.valor, 0));
    const vivosPorColuna = [...Array(colunas)].map((_, c) => nos.filter(n => n.coluna === c && n.valor > 0).length);
    const k = Math.min(...somaPorColuna.map((s, c) => (H - gap * Math.max(0, vivosPorColuna[c] - 1)) / (s || 1)));
    const areaW = W - rotE - rotD - noW;
    const xCol = c => rotE + (colunas === 1 ? 0 : (c / (colunas - 1)) * areaW);
    const pos = {};
    for (let c = 0; c < colunas; c++) {
      let yy = 0;
      nos.filter(n => n.coluna === c && n.valor > 0).forEach(n => {
        const h = Math.max(2, n.valor * k);
        pos[n.id] = { x: xCol(c), y: yy, h, saida: yy, entrada: yy };
        yy += h + gap;
      });
    }
    const svg = svgRaiz(W, H + 4, o.aria);
    ligacoes.forEach(l => {
      const a = pos[l.de], b = pos[l.para];
      if (!a || !b || l.valor <= 0) return;
      const t = Math.max(1, l.valor * k);
      const x1 = a.x + noW, x2 = b.x, y1 = a.saida, y2 = b.entrada;
      const mx = (x1 + x2) / 2;
      const d = `M${x1},${y1}C${mx},${y1} ${mx},${y2} ${x2},${y2}` +
        `V${y2 + t}C${mx},${y2 + t} ${mx},${y1 + t} ${x1},${y1 + t}Z`;
      svg.appendChild(titulo(el("path", { d, fill: l.cor, opacity: l.opacidade || 0.32 }),
        `${l.rotulo || ""}: ${inteiro(l.valor)}`));
      a.saida += t; b.entrada += t;
    });
    // Rotulos: duas linhas (nome e numero) quando o no tem altura para isso;
    // uma linha ("nome 59") no no fino. Numa coluna, cada rotulo desce ate
    // nao encostar no anterior - no de 1 fundo ao lado de um de 14 empilhava
    // os dois textos um sobre o outro.
    let fundo = H;
    for (let c = 0; c < colunas; c++) {
      const rotulos = nos.filter(n => n.coluna === c && pos[n.id]).map(n => {
        const p = pos[n.id];
        const duas = p.h >= 32;
        const alto = duas ? 30 : 15;
        return { n, p, duas, alto, y: p.y + p.h / 2 - alto / 2 };
      });
      let limite = -Infinity;
      rotulos.forEach(r => {
        r.y = Math.max(r.y, limite);
        limite = r.y + r.alto + 2;
      });
      fundo = Math.max(fundo, limite);
      rotulos.forEach(({ n, p, duas, y }) => {
        const ultima = n.coluna === colunas - 1, primeira = n.coluna === 0;
        const tx = primeira ? p.x - 8 : p.x + noW + 8;
        const texto = el("text", {
          x: tx, y: y + 11, "text-anchor": primeira ? "end" : "start",
          class: "axis-label" + (n.forte ? " base" : ""),
          "paint-order": "stroke", stroke: "var(--surface-1)", "stroke-width": ultima || primeira ? 0 : 4,
        });
        texto.appendChild(el("tspan", { x: tx, dy: 0 }, n.rotulo));
        if (duas) {
          texto.appendChild(el("tspan", { x: tx, dy: 15, "font-weight": 700 }, inteiro(n.valor)));
        } else {
          texto.appendChild(el("tspan", { dx: 5, "font-weight": 700 }, inteiro(n.valor)));
        }
        svg.appendChild(texto);
      });
    }
    nos.forEach(n => {
      const p = pos[n.id];
      if (!p) return;
      svg.appendChild(titulo(el("rect", { x: p.x, y: p.y, width: noW, height: p.h, fill: n.cor }),
        `${n.rotulo}: ${inteiro(n.valor)}`));
    });
    const alturaFinal = Math.ceil(fundo + 4);
    svg.setAttribute("viewBox", `0 0 ${W} ${alturaFinal}`);
    svg.setAttribute("height", alturaFinal);
    // Sem isto o `max-width: 100%` geral encolheria o SVG em vez de rolar.
    if (W > largura) svg.style.maxWidth = "none";
    mount.replaceChildren(svg);
  });
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

function table2(mount, columns, rows) {
  const cols = columns.map((c, i) => ({ label: c, key: "c" + i, num: i > 0 }));
  table(mount, cols, rows.map(r =>
    Object.fromEntries(r.map((v, i) => ["c" + i, v]))));
}

// Grafico em cima, tabela equivalente embaixo, recolhida: a tabela e o gemeo
// de todo grafico (a regra de contraste da paleta pede; o valor exato tambem).
function graficoComTabela(mount, resumoTabela = "Ver tabela") {
  mount.innerHTML = `<div class="chart-scroll"></div>` +
    `<details><summary>${escapeHtml(resumoTabela)}</summary><div class="matrix-scroll"></div></details>`;
  return {
    grafico: mount.querySelector(".chart-scroll"),
    tabela: mount.querySelector("details .matrix-scroll"),
  };
}

/* Rampa sequencial em cinza neutro, claro->escuro: todo matiz ja e de alguma
   casa, e uma celula azul leria como "BTG". Os mesmos passos de
   estilo.RAMPA; o texto da celula escolhe tinta pelo passo do preenchimento,
   nunca a cor da serie. */
const SEQ_STEPS = ["#efefef", "#d7d7d7", "#bcbcbc", "#9c9c9c", "#777777", "#4d4d4d", "#232323"];
function heatColor(value, max) {
  if (!value) return null;
  const idx = Math.min(SEQ_STEPS.length - 1,
    Math.floor((value / max) * SEQ_STEPS.length));
  return { fill: SEQ_STEPS[idx], ink: idx >= 4 ? "#ffffff" : "#121212" };
}
