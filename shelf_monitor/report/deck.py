"""Deck executivo (.pptx) gerado a cada coleta.

O dashboard e para explorar; o deck e para decidir: 8 slides com a leitura
competitiva das vitrines, prontos para forum ou comite. Consome os mesmos
agregados do dashboard (build_dashboard_data) - um numero nunca diverge entre
as duas superficies.

Estetica: a mesma paleta validada do dashboard (dataviz), fundo claro, texto em
tinta - cor so nas marcas. Tabelas em vez de graficos onde poucos numeros
precisos decidem (custo, ticket); barras desenhadas como retangulos onde a
comparacao visual importa (arquitetura aberta).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.models import RunReport
from shelf_monitor.report.aggregate import build_dashboard_data

# Paleta de referencia (modo claro) - a mesma do dashboard.
_INK = "0B0B0B"
_INK_SOFT = "52514E"
_MUTED = "898781"
_SURFACE = "FCFCFB"
_GRID = "E1E0D9"
_SERIES = ["2A78D6", "EB6834", "1BAF7A", "EDA100", "E87BA4"]
_SEQ = ["CDE2FB", "9EC5F4", "6DA7EC", "3987E5", "256ABF", "184F95", "0D366B"]
_FONT = "Calibri"

_SLIDE_W_IN = 13.333
_SLIDE_H_IN = 7.5


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"{int(value):,}".replace(",", ".")


def render_deck(config: Config, report: RunReport) -> Path:
    from pptx import Presentation
    from pptx.util import Inches

    data = build_dashboard_data(report)
    listing = data.get("listing") or {}
    platforms = [p for p in listing.get("platforms", []) if p.get("total")]

    prs = Presentation()
    prs.slide_width = Inches(_SLIDE_W_IN)
    prs.slide_height = Inches(_SLIDE_H_IN)

    matrix = listing.get("matrix") or []
    # Matriz so de "sem classificacao" nao e matriz - e a ausencia da base.
    if len(matrix) == 1 and matrix[0].get("category") == "Sem classificacao":
        matrix = []

    _cover(prs, report)
    _headline(prs, data, platforms)
    if matrix:
        _matrix(prs, {**listing, "matrix": matrix}, platforms)
    if platforms:
        _architecture(prs, platforms)
        _costs(prs, platforms)
    _movements(prs, report)
    _flows(prs, data)
    _performance(prs, data)
    _investors(prs, data)
    _cvm_radar(prs, data)
    _methodology(prs, report, data)

    config.reports_dir.mkdir(parents=True, exist_ok=True)
    path = config.reports_dir / f"prateleiras-{report.generated_at.date().isoformat()}.pptx"
    prs.save(path)
    return path


# --- blocos de layout --------------------------------------------------------


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _text(slide, x, y, w, h, text, size, *, bold=False, color=_INK, align=None):
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    run.font.name = _FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    return box


def _title(slide, text, subtitle=None):
    _text(slide, 0.6, 0.35, 12.1, 0.6, text, 24, bold=True)
    if subtitle:
        _text(slide, 0.6, 0.95, 12.1, 0.5, subtitle, 12, color=_INK_SOFT)


def _rect(slide, x, y, w, h, color, *, line=False):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches
    from pptx.dml.color import RGBColor

    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(color)
    if line:
        shape.line.color.rgb = RGBColor.from_string(_GRID)
        shape.line.width = 12700  # 1pt
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _table(slide, x, y, w, headers, rows, *, col_widths=None, size=11):
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    height = Inches(0.32 * (len(rows) + 1))
    shape = slide.shapes.add_table(
        len(rows) + 1, len(headers), Inches(x), Inches(y), Inches(w), height
    )
    table = shape.table
    table.first_row = False
    table.horz_banding = False
    if col_widths:
        total = sum(col_widths)
        for index, fraction in enumerate(col_widths):
            table.columns[index].width = Inches(w * fraction / total)

    def style(cell, text, *, bold=False, color=_INK, fill="FFFFFF"):
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(fill)
        frame = cell.text_frame
        frame.word_wrap = True
        paragraph = frame.paragraphs[0]
        run = paragraph.add_run() if not paragraph.runs else paragraph.runs[0]
        run.text = text
        run.font.name = _FONT
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color)

    for index, header in enumerate(headers):
        style(table.cell(0, index), header, bold=True, color=_INK_SOFT, fill="F1F0EC")
    for row_index, row in enumerate(rows, start=1):
        for col_index, value in enumerate(row):
            if isinstance(value, tuple):
                text, fill, ink = value
                style(table.cell(row_index, col_index), text, fill=fill, color=ink)
            else:
                style(table.cell(row_index, col_index), str(value))
    return table


# --- slides ------------------------------------------------------------------


def _cover(prs, report: RunReport):
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _rect(slide, 0.6, 2.1, 1.6, 0.06, _SERIES[0])
    _text(slide, 0.6, 2.4, 11, 1.0, "Prateleiras de fundos", 40, bold=True)
    _text(slide, 0.6, 3.3, 11, 0.6, "Leitura competitiva das vitrines de varejo", 18,
          color=_INK_SOFT)
    houses = ", ".join(s.platform for s in report.snapshots)
    _text(slide, 0.6, 6.4, 11, 0.4,
          f"Coleta de {report.generated_at.strftime('%d/%m/%Y')} - casas: {houses}",
          11, color=_MUTED)


def _headline(prs, data: dict, platforms: list[dict]):
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _title(slide, "A vitrine em numeros",
           "Vitrine = a listagem que o cliente ve na casa; catalogo = tudo que ela "
           "administra (Open Finance)")

    tiles = [
        (p["label"], _fmt(p["total"]), f"catalogo: {_fmt(p.get('catalog_total'))}")
        for p in platforms
    ] or [("Sem vitrines coletadas", "-", "rode a coleta com navegador")]
    shared = (data.get("listing") or {}).get("shared_all")
    if shared:
        tiles.append(("Em 2+ vitrines", _fmt(shared), "mesmo CNPJ"))

    width = 2.85
    for index, (label, value, note) in enumerate(tiles[:4]):
        x = 0.6 + index * (width + 0.25)
        _rect(slide, x, 1.8, width, 1.7, "FFFFFF", line=True)
        _text(slide, x + 0.2, 1.95, width - 0.4, 0.35, label.upper(), 10.5, color=_INK_SOFT)
        _text(slide, x + 0.2, 2.35, width - 0.4, 0.7, value, 30, bold=True)
        _text(slide, x + 0.2, 3.05, width - 0.4, 0.35, note, 10, color=_MUTED)

    exclusives = [(p["label"], p.get("exclusive", 0)) for p in platforms]
    if exclusives:
        _text(slide, 0.6, 4.0, 12, 0.4, "Diferenciais na vitrine (por CNPJ)", 14, bold=True)
        _table(
            slide, 0.6, 4.5, 6.5,
            ["Casa", "Fundos de varejo que so ela oferta"],
            [[label, _fmt(count)] for label, count in exclusives],
            col_widths=[2, 3],
        )


def _matrix(prs, listing: dict, platforms: list[dict]):
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _title(slide, "Matriz vitrine - casa x classe ANBIMA",
           "Classificacao oficial (join por CNPJ com o registro CVM); "
           "mais escuro = mais fundos")

    matrix = listing["matrix"][:12]
    max_value = max(
        (v for row in matrix for v in row["counts"].values()), default=1
    ) or 1

    def cell(value: int):
        if not value:
            return ("-", "FFFFFF", _MUTED)
        index = min(len(_SEQ) - 1, int((value / max_value) * len(_SEQ)))
        ink = "FFFFFF" if index >= 3 else _INK
        return (_fmt(value), _SEQ[index], ink)

    headers = ["Classe ANBIMA", *(p["label"] for p in platforms)]
    rows = [
        [row["category"], *(cell(row["counts"].get(p["platform_id"], 0)) for p in platforms)]
        for row in matrix
    ]
    _table(slide, 0.6, 1.6, 12.1, headers, rows,
           col_widths=[3.2, *([1.0] * len(platforms))], size=10)


def _architecture(prs, platforms: list[dict]):
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _title(slide, "Arquitetura aberta",
           "Produto de gestora da propria casa vs. de terceiros, na vitrine")

    # Legenda com amostra de cor + rotulo em tinta.
    for index, (color, label) in enumerate(
        [(_SERIES[0], "Gestora da casa"), (_SERIES[1], "Terceiros"), (_GRID, "Sem gestor identificado")]
    ):
        x = 0.6 + index * 2.6
        _rect(slide, x, 1.55, 0.18, 0.18, color)
        _text(slide, x + 0.28, 1.47, 2.2, 0.32, label, 11, color=_INK_SOFT)

    max_total = max(p["total"] for p in platforms)
    plot_w = 8.6
    y = 2.3
    for p in platforms:
        _text(slide, 0.6, y - 0.06, 2.2, 0.35, p["label"], 12, color=_INK)
        x = 3.0
        parts = [
            (p.get("own_product", 0), _SERIES[0]),
            (p.get("third_party", 0), _SERIES[1]),
            (max(0, p["total"] - p.get("own_product", 0) - p.get("third_party", 0)), _GRID),
        ]
        for value, color in parts:
            if not value:
                continue
            width = max(0.03, (value / max_total) * plot_w - 0.03)
            _rect(slide, x, y, width, 0.3, color)
            x += width + 0.03  # gap na cor da superficie
        _text(slide, x + 0.08, y - 0.04, 1.2, 0.32, _fmt(p["total"]), 11, color=_INK_SOFT)
        y += 0.62

    _text(slide, 0.6, y + 0.3, 12, 0.4,
          '"Gestora da casa" segue a mesma watchlist do radar CVM - a definicao e uma so.',
          10, color=_MUTED)


def _costs(prs, platforms: list[dict]):
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _title(slide, "Custo e ticket de entrada",
           "Quartis da taxa de administracao e mediana do minimo - quartis mostram "
           "a forma da prateleira; media esconderia")

    def pct(value):
        return f"{_fmt(value)}%" if value is not None else "-"

    rows = []
    for p in platforms:
        fee = p.get("fee_quartiles") or {}
        minimum = p.get("min_quartiles") or {}
        rows.append([
            p["label"],
            pct(fee.get("q1")), pct(fee.get("median")), pct(fee.get("q3")),
            f"R$ {_fmt(minimum.get('median'))}" if minimum.get("median") is not None else "-",
            f"{round((p.get('cnpj_coverage') or 0) * 100)}%",
        ])
    _table(
        slide, 0.6, 1.7, 10.5,
        ["Casa", "Taxa q1", "Taxa mediana", "Taxa q3", "Minimo mediano", "Cobertura CNPJ"],
        rows, col_widths=[2.2, 1, 1.2, 1, 1.6, 1.4],
    )
    _text(slide, 0.6, 1.7 + 0.32 * (len(rows) + 1) + 0.3, 12, 0.5,
          "Taxa e a maxima publicada pela casa (mesma semantica do maxAdminFee do "
          "Open Finance). Celulas vazias = a fonte nao publica o campo.",
          10, color=_MUTED)


def _movements(prs, report: RunReport):
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _title(slide, "Movimentos desde a coleta anterior",
           "Entradas e saidas por casa; coletas suspeitas nao geram churn")

    rows = []
    for diff in report.shelf_diffs:
        if diff.is_first_run:
            rows.append([diff.platform, "-", "-", "primeira coleta (baseline)"])
            continue
        note = "coleta suspeita - saidas suprimidas" if diff.suspect else ""
        sample = ", ".join(f.name for f in diff.added[:3])
        if len(diff.added) > 3:
            sample += ", ..."
        rows.append([
            diff.platform,
            _fmt(len(diff.added)),
            "-" if diff.suspect else _fmt(len(diff.removed)),
            note or sample or "sem movimentos",
        ])
    if not rows:
        rows = [["-", "-", "-", "sem diffs nesta execucao"]]
    _table(slide, 0.6, 1.7, 12.1,
           ["Casa", "Entradas", "Saidas", "Observacao / exemplos de entrada"],
           rows, col_widths=[1.4, 1, 1, 6], size=10)


def _brl(value: object) -> str:
    """R$ compacto (bi/mi/mil) - captacao se le em ordem de grandeza."""
    if value is None:
        return "-"
    number = float(value)
    sign = "-" if number < 0 else ""
    magnitude = abs(number)
    if magnitude >= 1e9:
        return f"{sign}R$ {magnitude / 1e9:.2f} bi".replace(".", ",")
    if magnitude >= 1e6:
        return f"{sign}R$ {magnitude / 1e6:.1f} mi".replace(".", ",")
    if magnitude >= 1e3:
        return f"{sign}R$ {magnitude / 1e3:.0f} mil"
    return f"{sign}R$ {magnitude:.0f}"


def _flows(prs, data: dict):
    flows = data.get("flows") or {}
    if not flows.get("available"):
        return
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    window = (
        f"{flows.get('window_months') or ''}m fechados"
        + (f" ate {flows['as_of']}" if flows.get("as_of") else "")
    )
    _title(slide, "Captacao (CVM) - fluxo financeiro",
           f"Captacao liquida do informe diario, {window}; o mes corrente e "
           "parcial e fica fora das somas")

    platforms = [p for p in flows.get("platforms") or [] if p.get("matched")]
    if platforms:
        _text(slide, 0.6, 1.55, 6, 0.4, "Vitrine por casa", 13, bold=True)
        _table(slide, 0.6, 2.0, 5.9,
               ["Casa", "Liquido 3m", "Liquido 12m", "Cobertura"],
               [[p["label"], _brl(p["net_3m"]), _brl(p["net_12m"]),
                 f"{p['matched']}/{p['total']}"] for p in platforms],
               col_widths=[1.8, 1.4, 1.4, 1.1], size=10)

    managers = flows.get("managers") or {}
    houses = managers.get("houses") or []
    if houses:
        _text(slide, 7.0, 1.55, 5.7, 0.4, "Gestoras da watchlist - mercado inteiro",
              13, bold=True)
        _table(slide, 7.0, 2.0, 5.7,
               ["Casa", "Liquido 12m", "PL", "Classes"],
               [[h["label"], _brl(h["net_12m"]), _brl(h["pl"]), _fmt(h["classes"])]
                for h in houses],
               col_widths=[1.7, 1.5, 1.5, 1.0], size=10)

    movers = [("captador", item) for item in (flows.get("top_inflow") or [])[:3]]
    movers += [("resgate", item) for item in (flows.get("top_outflow") or [])[:3]]
    if movers:
        y = 2.0 + 0.32 * (max(len(platforms), len(houses)) + 1) + 0.4
        _text(slide, 0.6, y, 12, 0.4, "Maiores movimentos da prateleira (12m)", 13,
              bold=True)
        _table(slide, 0.6, y + 0.45, 12.1,
               ["Tipo", "Fundo", "Gestora", "Liquido 12m", "Prateleiras"],
               [[kind, item["name"][:60], (item.get("manager") or "-")[:36],
                 _brl(item["net_12m"]), ", ".join(item.get("platforms") or [])]
                for kind, item in movers],
               col_widths=[1.0, 4.6, 2.6, 1.5, 2.0], size=9)


def _pct(value: object, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.{digits}f}%".replace(".", ",")


def _int(value: object) -> str:
    return "-" if value is None else _fmt(int(round(float(value))))


def _performance(prs, data: dict):
    perf = data.get("performance") or {}
    if not perf.get("available"):
        return
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    cdi = ((perf.get("cdi") or {}).get("windows") or {}).get("12m")
    subtitle = "Retorno pela cota em meses fechados"
    if perf.get("window_end"):
        subtitle += f" ate {perf['window_end']}"
    subtitle += (
        f"; CDI 12m na mesma janela: {_pct(cdi, 2)}" if cdi is not None
        else "; CDI indisponivel (sem '% do CDI')"
    )
    _title(slide, "Rentabilidade e risco (CVM) - pela cota", subtitle)

    platforms = [p for p in perf.get("platforms") or [] if p.get("matched")]
    if platforms:
        _table(slide, 0.6, 1.7, 8.0,
               ["Casa", "Retorno 12m (mediana)", "Vol. 12m", "Acima do CDI", "Cobertura"],
               [[p["label"], _pct(p["return_median"]), _pct(p["vol_median"]),
                 "-" if p.get("above_cdi") is None else f"{p['above_cdi']} de {p['matched']}",
                 f"{p['matched']}/{p['total']}"] for p in platforms],
               col_widths=[2.0, 1.8, 1.2, 1.4, 1.2], size=10)

    movers = [("maior", item) for item in (perf.get("top") or [])[:3]]
    movers += [("menor", item) for item in (perf.get("bottom") or [])[:3]]
    if movers:
        y = 1.7 + 0.32 * (len(platforms) + 1) + 0.4
        _text(slide, 0.6, y, 12, 0.4, "Extremos da prateleira (retorno 12m)", 13, bold=True)
        _table(slide, 0.6, y + 0.45, 12.1,
               ["Tipo", "Fundo", "Classe", "Retorno 12m", "Vol. 12m", "% do CDI", "Prateleiras"],
               [[kind, item["name"][:52], (item.get("category") or "-")[:28],
                 _pct(item["return_12m"]), _pct(item.get("vol_12m")),
                 _pct(item.get("pct_cdi"), 0), ", ".join(item.get("platforms") or [])]
                for kind, item in movers],
               col_widths=[0.8, 3.8, 2.2, 1.2, 1.0, 1.0, 2.0], size=9)


def _investors(prs, data: dict):
    inv = data.get("investors") or {}
    if not inv.get("available"):
        return
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _title(slide, "Cotistas e ticket medio (CVM)",
           "PL por cotista do informe mais fresco; cotistas somam por prateleira; "
           "variacao sobre meses fechados")

    platforms = [p for p in inv.get("platforms") or [] if p.get("matched")]
    if platforms:
        _table(slide, 0.6, 1.7, 9.0,
               ["Casa", "Cotistas", "Ticket mediano", "Ticket < R$ 10 mil",
                "Var. cotistas 12m", "Cobertura"],
               [[p["label"], _int(p["shareholders"]), _brl(p["ticket_median"]),
                 _pct(p["small_ticket_share"], 0) if p.get("small_ticket_share") is not None else "-",
                 "-" if p.get("delta_12m") is None else (
                     _int(p["delta_12m"])
                     + (f" ({_pct(p['delta_12m_pct'])})" if p.get("delta_12m_pct") is not None else "")
                 ),
                 f"{p['matched']}/{p['total']}"] for p in platforms],
               col_widths=[1.8, 1.2, 1.5, 1.5, 1.8, 1.2], size=10)

    movers = [("ganho", item) for item in (inv.get("top_gain") or [])[:3]]
    movers += [("perda", item) for item in (inv.get("top_loss") or [])[:3]]
    if movers:
        y = 1.7 + 0.32 * (len(platforms) + 1) + 0.4
        _text(slide, 0.6, y, 12, 0.4, "Maiores movimentos de cotistas (12m)", 13, bold=True)
        _table(slide, 0.6, y + 0.45, 12.1,
               ["Tipo", "Fundo", "Gestora", "Cotistas", "Var. 12m", "Ticket", "Prateleiras"],
               [[kind, item["name"][:52], (item.get("manager") or "-")[:30],
                 _int(item["shareholders"]), _int(item["delta_12m"]),
                 _brl(item.get("ticket")), ", ".join(item.get("platforms") or [])]
                for kind, item in movers],
               col_widths=[0.8, 3.8, 2.4, 1.0, 1.0, 1.3, 1.8], size=9)


def _cvm_radar(prs, data: dict):
    cvm = data.get("cvm") or {}
    if not cvm.get("available"):
        return
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _title(slide, "Radar CVM - visao antecipada",
           "Fundos registrados por gestoras ligadas as casas; pre-operacional = "
           "registrado e ainda nao lancado")

    houses = cvm.get("houses") or []
    _table(slide, 0.6, 1.6, 7.5,
           ["Casa", "Registrados", "Pre-operacionais"],
           [[h["label"], _fmt(h["total"]), _fmt(h["pre_operational"])] for h in houses],
           col_widths=[2.5, 1.5, 1.5])

    recents = (cvm.get("pre_operational_funds") or [])[:6]
    if recents:
        y = 1.6 + 0.32 * (len(houses) + 1) + 0.4
        _text(slide, 0.6, y, 12, 0.4, "Registros pre-operacionais mais recentes", 13,
              bold=True)
        _table(slide, 0.6, y + 0.45, 12.1,
               ["Fundo", "Casa", "Gestora", "Registro"],
               [[f["name"][:70], f["house_label"], (f.get("manager") or "-")[:40],
                 f.get("registered_at") or "-"] for f in recents],
               col_widths=[5, 1.4, 2.6, 1.2], size=9)


def _methodology(prs, report: RunReport, data: dict):
    slide = _blank(prs)
    _rect(slide, 0, 0, _SLIDE_W_IN, _SLIDE_H_IN, _SURFACE)
    _title(slide, "Metodologia e proveniencia")

    restricted = (data.get("restricted") or {}).get("total") or 0
    lines = [
        ("Vitrine", "fundo presente no canal da propria casa (site, API do portal ou "
                    "area logada). Open Finance = catalogo regulatorio, usado para "
                    "enriquecer e como referencia."),
        ("Perfil de varejo",
         f"fundos exclusivos/restritos ({_fmt(restricted)} nesta coleta) ficam fora "
         "de toda analise; o inventario deles esta na aba 'Restritos (auxiliar)' "
         "da planilha e no dashboard."),
        ("Precedencia", "Open Finance > area logada > API do portal > navegador > HTML; "
                        "fontes que faltam preenchem, nunca sobrescrevem."),
    ]
    enrichment = data.get("enrichment")
    identifiable = max(
        (enrichment or {}).get("with_cnpj", 0),
        (enrichment or {}).get("with_cvm_code", 0),
    )
    if identifiable:
        pct = round(100 * enrichment["matched"] / identifiable)
        lines.append(("Normalizacao ANBIMA",
                      f"join com o registro oficial CVM por CNPJ e por codigo: "
                      f"{enrichment['matched']} de {identifiable} fundos "
                      f"identificaveis ({pct}%); a gestora vem do registro em "
                      f"{_fmt(enrichment.get('managers_from_cvm', 0))} deles."))
    else:
        lines.append(("Normalizacao ANBIMA",
                      "nao aplicada - base de mercado nao importada "
                      "(python -m shelf_monitor market)."))
    for snapshot in report.snapshots:
        listing_count = sum(1 for f in snapshot.funds if f.on_listing)
        lines.append((
            snapshot.platform,
            f"{snapshot.collected_at.strftime('%d/%m/%Y')} - status "
            f"{snapshot.status.value}; vitrine {listing_count} de {len(snapshot.funds)}"
            + (f"; obs: {snapshot.error[:120]}" if snapshot.error else ""),
        ))

    y = 1.5
    for key, value in lines:
        _text(slide, 0.6, y, 2.4, 0.4, key, 11, bold=True, color=_INK_SOFT)
        _text(slide, 3.1, y, 9.6, 0.62, value, 10.5)
        y += 0.62
