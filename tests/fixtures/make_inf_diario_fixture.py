"""Gera os zips mensais sinteticos do INF_DIARIO usados por tests/test_captacao.py.

Cobre 2026-07 a 2026-09 (ate 11/09, sexta-feira - a data mais recente usada nos
testes de janela). Fluxo diario constante por CNPJ, so em dias uteis, para que
os testes verifiquem a soma contando dias uteis no periodo em vez de repetir
numeros escritos a mao.

CNPJs usam os mesmos da fixture de enrich (tests/fixtures/enrich/mercado.csv),
para exercitar o join real:
- 63.446.494/0001-52 - classe RF isolada (envelope == classe). +1000/dia; tem
  uma linha "errada" seguida da retificacao no mesmo dia (mesmo TP_FUNDO_CLASSE),
  para testar que o dedupe fica com a ultima.
- 11.111.111/0001-11 - classe RF isolada. Sangria: -1000/dia liquido.
- 99.999.999/0001-99 - fundo legado (Acoes). +100/dia.
- 77.777.777/0001-77 e 88.888.888/0001-88 - duas classes do MESMO envelope
  66.666.666/0001-66. +300/dia e +200/dia - somam 500/dia quando agregado por
  fundo.
- 10.101.010/0001-10 - classe RF com PL sempre zero. +200/dia, mas
  `captacao_liquida_pct_pl` deve sair None (PL de abertura nao positivo).
- 55.555.555/0001-55 - CNPJ fora de qualquer vitrine (ruido): nunca deve
  aparecer no resultado, mesmo com fluxo grande.

44.444.444/0001-44 e usado nos testes como CNPJ de vitrine SEM nenhuma linha
neste informe (cobertura "N de M").

Rode com `python tests/fixtures/make_inf_diario_fixture.py` para regerar.
"""
from __future__ import annotations

import zipfile
from datetime import date, timedelta
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "inf_diario"

HEADER = (
    "TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;ID_SUBCLASSE;DT_COMPTC;VL_TOTAL;"
    "VL_QUOTA;VL_PATRIM_LIQ;CAPTC_DIA;RESG_DIA;NR_COTST"
)

# cnpj (com mascara, como o arquivo real publica) -> (tipo, captc/dia, resg/dia, pl_inicial)
FUNDOS = {
    "63.446.494/0001-52": ("CLASSE", 1000.0, 0.0, 10_000_000.0),
    "11.111.111/0001-11": ("CLASSE", 500.0, 1500.0, 50_000_000.0),
    "99.999.999/0001-99": ("FI", 100.0, 0.0, 1_000_000.0),
    "77.777.777/0001-77": ("CLASSE", 300.0, 0.0, 3_000_000.0),
    "88.888.888/0001-88": ("CLASSE", 200.0, 0.0, 4_000_000.0),
    "10.101.010/0001-10": ("CLASSE", 200.0, 0.0, 0.0),
    "55.555.555/0001-55": ("CLASSE", 99_999.0, 0.0, 1_000_000.0),
}

START = date(2026, 7, 1)
END = date(2026, 9, 11)


def _business_days(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # 0=segunda ... 4=sexta
            days.append(current)
        current += timedelta(days=1)
    return days


def _rows_for_month(yyyymm: str) -> list[str]:
    year, month = int(yyyymm[:4]), int(yyyymm[4:])
    month_start = date(year, month, 1)
    month_end = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    days = [d for d in _business_days(START, END) if month_start <= d <= min(month_end, END)]

    lines = [HEADER]
    for cnpj, (tipo, captc, resg, pl_inicial) in FUNDOS.items():
        pl = pl_inicial
        # PL antes do primeiro dia deste mes: soma do fluxo liquido de todos
        # os dias uteis anteriores ao mes corrente, para pl_inicio "conhecido"
        # (nao aproximado) valer nos testes de janela que comecam no meio do
        # historico (ex.: a semana de 31/08-06/09).
        dias_antes = [d for d in _business_days(START, END) if d < month_start]
        pl += len(dias_antes) * (captc - resg)

        for d in days:
            pl += captc - resg
            lines.append(
                f"{tipo};{cnpj};;{d.isoformat()};{pl:.2f};10.000000;{pl:.2f};"
                f"{captc:.2f};{resg:.2f};100"
            )
            if cnpj == "63.446.494/0001-52" and d == date(2026, 9, 1):
                # Linha errada, escrita ANTES da retificacao - como no arquivo
                # real, a retificacao vem depois e deve prevalecer no dedupe.
                lines.insert(
                    -1,
                    f"CLASSE;{cnpj};;{d.isoformat()};999999.00;10.000000;999999.00;"
                    f"50000.00;0.00;100",
                )
    return lines


def build() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for yyyymm in ("202607", "202608", "202609"):
        lines = _rows_for_month(yyyymm)
        zip_path = FIXTURES_DIR / f"inf_diario_fi_{yyyymm}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"inf_diario_fi_{yyyymm}.csv", "\n".join(lines) + "\n")
        print(f"gerado {zip_path} ({len(lines) - 1} linhas)")


if __name__ == "__main__":
    build()
