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

# A paleta categorica validada (scripts/validate_palette.js) vive nas variaveis
# CSS --serie-1..5 de `_STYLE`, que e de onde o JS a le. As duas listas Python
# que existiam aqui eram uma terceira copia dos mesmos hexes, nunca referenciada.

# CSS e JS moram em `assets/` e nao em strings aqui.
#
# O JS tinha 984 linhas dentro de uma string Python: nenhum editor o destacava,
# nenhum linter o via, e qualquer aspa ou barra invertida dependia de o literal
# ser `r"""`. Como arquivo, ele e um arquivo - e o teste que verifica que o
# dashboard nao quebra passa a poder ler o fonte dele.
#
# Sao embutidos no HTML (e nao referenciados por <link>/<src>) de proposito: o
# dashboard e um arquivo unico que se abre por file:// sem servidor, e essa
# propriedade ja e coberta por teste.
_ASSETS = Path(__file__).parent / "assets"
_STYLE = (_ASSETS / "dashboard.css").read_text(encoding="utf-8")
_SCRIPT = (_ASSETS / "dashboard.js").read_text(encoding="utf-8")


def _stat_tiles(data: dict) -> str:
    """As quatro manchetes - em vitrine, que e a unidade que o resto do painel usa.

    O tile de topo somava os catalogos ("13.967 fundos mapeados", dos quais
    9.413 eram o Open Finance do BTG sozinho) e o segundo chamava de
    "Prateleira da XP" o catalogo dela. Dois numeros grandes que nao descreviam
    prateleira nenhuma, no lugar da tela em que o leitor mais confia.
    """
    usable = [p for p in data["platforms"] if p["status"] != "unavailable" and p["listing_funds"]]
    listing_total = sum(p["listing_funds"] for p in usable)
    catalog_total = sum(
        p["catalog_funds"] for p in data["platforms"] if p["status"] != "unavailable"
    )
    cvm = data.get("cvm", {})
    pre_op = sum(h["pre_operational"] for h in cvm.get("houses", [])) if cvm.get("available") else 0
    milhar = lambda n: f"{n:,}".replace(",", ".")  # noqa: E731
    gaps = data.get("gaps") or {}

    # O segundo tile era "Vitrine da XP", repetindo o tile que o bloco de gaps
    # ja mostra oitenta pixels abaixo. No lugar entra o numero que o leitor tem
    # o que fazer com: quantos fundos de varejo os concorrentes vitrinam e a XP
    # nao. Era a unica manchete acionavel da pagina e estava enterrada.
    tiles = [
        ("Fundos em vitrine (soma das casas)", milhar(listing_total),
         f"{len(usable)} de {len(data['platforms'])} casas com vitrine alcancavel. "
         "Um fundo em tres vitrines conta tres vezes: e a soma das prateleiras, "
         f"nao fundos distintos. Catalogo Open Finance: {milhar(catalog_total)}"),
        ("Fundos que faltam na XP",
         milhar(gaps.get("total_gaps", 0)) if gaps.get("available") else "-",
         f"de varejo, em ao menos uma vitrine concorrente e fora da vitrine da XP "
         f"({milhar(gaps.get('baseline_listing_total') or 0)} fundos)"
         if gaps.get("available") else "bloco de gaps suprimido nesta coleta"),
        ("Registrados na CVM",
         milhar(cvm.get("total", 0)) if cvm.get("available") else "-",
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
        <h3>Ticket de entrada por casa</h3>
        <p class="card-sub">Quartis da aplicacao minima &mdash; quartis mostram a forma da
          prateleira; media esconderia. A taxa saiu daqui: quartil de taxa bruta entre casas
          mede o <strong>mix</strong> da prateleira, nao o preco, e a leitura que presta esta
          em &ldquo;Custo relativo a categoria&rdquo;.</p>
        <div id="table-costs"></div>
      </div>
    </div>

    <details><summary>Ver a matriz como tabela</summary><div id="table-vitrine"></div></details>
    <div class="coverage" id="vitrine-coverage"></div>
  </section>

  <section class="card" id="gaps-section">
    <h2>Gaps de prateleira da XP</h2>
    <p class="card-sub">Fundos de varejo presentes na vitrine de ao menos um concorrente e
      ausentes da vitrine da XP (por CNPJ). Comparacao vitrine &times; vitrine: o catalogo
      Open Finance nao conta em nenhum dos lados.</p>
    <div class="tiles" id="gaps-tiles"></div>

    <h3 style="font-size:13.5px;font-weight:600;margin:18px 0 4px">Onde a vitrine da XP esta rasa</h3>
    <p class="card-sub">Categorias com mais fundos de gap; cada celula conta a vitrine da casa
      na categoria. Mais escuro = mais fundos.</p>
    <div class="matrix-scroll" id="matrix-gaps"></div>

    <details><summary>Ver os fundos de gap (ordenados por captacao, com o esforco de incluir)</summary><div id="table-gaps"></div></details>
    <div class="coverage" id="gaps-coverage"></div>
  </section>

  <section class="card" id="flow-section">
    <h2>Captacao dos fundos de vitrine</h2>
    <p class="card-sub">Fluxo do informe diario da CVM para os fundos que cada casa exibe.
      A CVM publica captacao por <strong>fundo</strong>, nunca por distribuidor: um fundo em
      tres vitrines entra inteiro nas tres somas. A coluna &quot;so ela exibe&quot; e onde a
      atribuicao a uma casa e inequivoca.</p>
    <div class="tiles" id="flow-tiles"></div>

    <h3 style="font-size:13.5px;font-weight:600;margin:18px 0 4px">Tendencia</h3>
    <p class="card-sub">Media diaria de captacao liquida por competencia. A media diaria
      existe porque o mes corrente fecha com menos pregoes que os meses cheios - comparar
      totais brutos marcaria toda casa como desacelerando, por efeito de calendario.</p>
    <div class="matrix-scroll" id="flow-trend"></div>

    <details><summary>Fundos por tendencia, e quais sao</summary>
      <div id="flow-lists"></div>
    </details>
    <div class="coverage" id="flow-coverage"></div>
  </section>

  <section class="card" id="perf-section">
    <h2>Rentabilidade e custo da vitrine</h2>
    <p class="card-sub">Tres leituras do mesmo periodo: quanto rende o fundo <strong>mediano</strong>
      da prateleira, quanto rendeu o <strong>real medio</strong> (ponderado por PL) e quanto rendeu o
      <strong>cotista medio</strong> (ponderado por numero de cotistas). A diferenca entre as duas
      ultimas diz onde esta o desalinhamento: gap positivo, o dinheiro grande esta nos fundos bons e
      o varejo nos piores; negativo, o inverso. E o retorno dos fundos que a casa exibe, com o peso
      que eles tem no <strong>mercado</strong> - nao o retorno dos clientes dela: a CVM publica cota,
      PL e cotistas por fundo, somando todos os distribuidores. Cota ex-provento.</p>
    <p class="card-sub" id="perf-nota"></p>
    <div class="tiles" id="perf-tiles"></div>
    <div class="legend" id="perf-legend"></div>
    <div class="chart-scroll" id="perf-chart"></div>

    <h3 style="font-size:13.5px;font-weight:600;margin:18px 0 4px">Onde esta o dinheiro em relacao a mediana da categoria</h3>
    <p class="card-sub">Quanto do PL da vitrine esta em fundo que rende abaixo da mediana da
      <strong>propria categoria</strong> no universo oficial. O numero que decide e o
      <strong>indice</strong> &mdash; share do PL dividido pelo share de fundos: abaixo de 1 o
      dinheiro <strong>evita</strong> os piores da categoria, acima de 1 se concentra neles.
      Cerca de metade de qualquer universo fica abaixo da mediana por construcao, entao a
      contagem de fundos sozinha nao e sinal de nada. Fundo sem categoria com amostra
      suficiente fica fora do denominador.</p>
    <div class="matrix-scroll" id="perf-massa"></div>

    <h3 style="font-size:13.5px;font-weight:600;margin:18px 0 4px">Fundos de muita gente rendendo abaixo da categoria</h3>
    <p class="card-sub">O recorte com o que fazer: produto especifico, com muita gente dentro,
      atras da mediana da propria categoria. Deduplicado por CNPJ &mdash; a CVM publica cotistas e
      retorno por <strong>fundo</strong>, somando todos os distribuidores, entao o mesmo produto em
      tres vitrines tem os mesmos numeros nas tres e a coluna Casas lista quem o exibe.</p>
    <div class="matrix-scroll" id="perf-expostos"></div>

    <h3 style="font-size:13.5px;font-weight:600;margin:18px 0 4px">Custo relativo a categoria</h3>
    <p class="card-sub">Indice mediano, nao taxa mediana: comparar a taxa bruta entre casas mede o
      <strong>mix</strong> da prateleira (renda fixa custa ~0,2% ao ano, acoes ~2%), nao o preco dos
      produtos. 1,00x e exatamente a mediana da categoria de cada fundo.</p>
    <div class="matrix-scroll" id="custo-bloco"></div>

    <h3 style="font-size:13.5px;font-weight:600;margin:18px 0 4px">Mandato autorizado pelo regulamento</h3>
    <p class="card-sub">Share da vitrine cujo regulamento <strong>autoriza</strong> cada coisa - nunca
      o que a carteira carrega. Um fundo pode ter autorizacao para 100% de credito privado e carregar
      10%; medir exposicao realizada exigiria a carteira mensal (CDA), fora do escopo. Verde acima das
      concorrentes, vermelho abaixo.</p>
    <div class="matrix-scroll" id="mandato-bloco"></div>
    <div class="coverage" id="perf-coverage"></div>
  </section>

  <section class="card">
    <h2>Amplitude por classe de ativo</h2>
    <p class="card-sub">Quantos fundos cada casa <strong>exibe</strong> em cada classe - a leitura
      mais direta de onde cada prateleira e forte. Conta a vitrine; o catalogo Open Finance
      mede o que a casa administra e nao entra em nenhum eixo comparativo.</p>
    <div class="legend" id="legend-classes"></div>
    <div class="chart-scroll" id="chart-classes"></div>
    <details><summary>Ver tabela</summary><div id="table-classes"></div></details>
  </section>

  <section class="card">
    <h2>Profundidade: gestoras representadas</h2>
    <p class="card-sub">Numero de gestoras distintas na <strong>vitrine</strong>. Amplitude sem
      profundidade indica concentracao em poucas casas de gestao. Contada no catalogo, a
      distribuidora que tambem administra muito fundo fechado aparece com o dobro de
      gestoras sem ter posto uma so a mais na tela do cliente.</p>
    <div class="chart-scroll" id="chart-managers"></div>
    <details><summary>Ver as maiores gestoras de cada prateleira</summary>
      <div id="table-top-managers"></div></details>
  </section>

  <section class="card">
    <h2>Aplicacao minima</h2>
    <p class="card-sub">Distribuicao dos fundos da vitrine por faixa de investimento minimo -
      separa quem mira varejo de quem mira alta renda.</p>
    <div class="legend" id="legend-bands"></div>
    <div class="chart-scroll" id="chart-bands"></div>
    <details><summary>Ver tabela</summary><div id="table-bands"></div></details>
  </section>

  <section class="card">
    <h2>Fundos so nesta vitrine</h2>
    <p class="card-sub">Fundos de varejo (por CNPJ) que so uma das casas coletadas <strong>exibe
      na vitrine</strong> - o conceito competitivo, nada a ver com "fundo exclusivo" regulatorio,
      que fica fora desta analise. Vitrine dos dois lados: o que a casa apenas administra e
      ninguem ve na tela nao e diferencial de prateleira. Considera somente casas com coleta
      bem-sucedida.</p>
    <div class="chart-scroll" id="chart-exclusive"></div>
  </section>

  <section class="card">
    <h2>Fundos exclusivos e restritos &mdash; auxiliar</h2>
    <p class="card-sub">Veiculos fora do perfil de varejo (exclusivo no registro CVM,
      publico profissional, marcados pela casa ou pelo nome). Ficam fora de toda analise
      acima; aqui e o inventario deles.</p>
    <div id="table-restricted"></div>
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
    Finance Brasil e cadastro de fundos da CVM (dados.cvm.gov.br).
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
