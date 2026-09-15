"""Ingestao generica do informe diario mensal da CVM (INF_DIARIO).

Baixa os arquivos mensais `inf_diario_fi_{YYYYMM}.zip`, filtra por um conjunto de
CNPJs alvo (classe ou fundo legado) e devolve os registros diarios prontos para
agregacao. E o parser que tanto o fluxo de ETF (`market/etf_flow.py`) quanto o
monitor de captacao (`captacao.py`) usam - o formato do arquivo e o mesmo, so
muda quem esta perguntando por qual CNPJ.

Ressalva de cobertura: o INF_DIARIO e o informe de FI/FIF. Fundos estruturados
(FIIM, FII, FIDC, FIP, FIAGRO) tem arquivos proprios na CVM, fora deste modulo -
um CNPJ desses tipos nunca aparece aqui, e quem consome precisa reportar isso
como cobertura, nao como "zero captacao".
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.datasets import inf_diario_dataset
from shelf_monitor.market.fetch import fetch
from shelf_monitor.normalize import parse_number

_CNPJ_TRANS = str.maketrans("", "", "./- ")


@dataclass
class DailyRecord:
    """Registro de informe diario de um fundo (ou classe) numa data-base."""

    cnpj: str
    dt_comptc: date
    vl_total: float = 0.0
    vl_quota: float = 0.0
    vl_patrim_liq: float = 0.0
    captc_dia: float = 0.0
    resg_dia: float = 0.0
    nr_cotst: int = 0
    # Rotulo bruto da coluna TP_FUNDO_CLASSE (v4 do informe), quando presente.
    # Usado so para desempatar duas linhas do mesmo CNPJ na mesma data.
    tp_fundo_classe: str | None = None

    @property
    def net_inflow(self) -> float:
        return self.captc_dia - self.resg_dia


def get_recent_months(count: int = 3, ref_date: date | None = None) -> list[str]:
    """Gera lista de sufixos YYYYMM para os ultimos `count` meses, mais antigo primeiro."""
    ref = ref_date or date.today()
    months = []
    curr_year = ref.year
    curr_month = ref.month

    for _ in range(count):
        months.append(f"{curr_year:04d}{curr_month:02d}")
        curr_month -= 1
        if curr_month == 0:
            curr_month = 12
            curr_year -= 1

    months.reverse()
    return months


def months_for_ytd(ref_date: date | None = None) -> list[str]:
    """Sufixos YYYYMM de janeiro ate o mes de `ref_date`, mais antigo primeiro.

    Inclui dezembro do ano anterior quando `ref_date` cai em janeiro: a semana
    ISO completa mais recente pode ter dias em dezembro (ex.: 02/01/2026 e
    quinta-feira da semana que comecou em 29/12/2025), e sem esse mes o inicio
    da janela "semana" ficaria sem dado.
    """
    ref = ref_date or date.today()
    months = [f"{ref.year:04d}{m:02d}" for m in range(1, ref.month + 1)]
    if ref.month == 1:
        months.insert(0, f"{ref.year - 1:04d}12")
    return months


def fetch_inf_diario_month(config: Config, yyyymm: str, force: bool = False) -> Path | None:
    """Baixa o zip mensal do INF_DIARIO da CVM com cache condicional.

    Com o arquivo ja em `market_raw_dir`, nao ha requisicao nenhuma a menos que
    `force=True` - e o mecanismo que permite `download=False` no chamador
    (captacao.py) recusar rede e so aceitar o que ja esta em disco.
    """
    filename = f"inf_diario_fi_{yyyymm}.zip"
    dest = config.market_raw_dir / filename
    if dest.exists() and not force:
        return dest

    ds = inf_diario_dataset(yyyymm)

    try:
        res = fetch(config, ds, force=force)
        return res.path
    except Exception:  # noqa: BLE001
        return None


def read_daily_records(zip_path: Path, target_cnpjs: set[str]) -> list[DailyRecord]:
    """Le o CSV do zip mensal do INF_DIARIO filtrando apenas os CNPJs alvo.

    O arquivo real tem ~200 mil linhas por mes; a leitura e feita membro por
    membro (io.TextIOWrapper), nao com `.read().decode().splitlines()` do zip
    inteiro na memoria - baixar o ano inteiro (~9 meses) para o YTD nao pode
    multiplicar o pico de memoria por mes lido.
    """
    if not zip_path or not zip_path.exists() or not zipfile.is_zipfile(zip_path):
        return []

    records: list[DailyRecord] = []

    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            return []

        with archive.open(csv_names[0]) as raw:
            text_stream = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace")

            header_line = text_stream.readline()
            if not header_line:
                return []
            header = header_line.rstrip("\r\n").split(";")
            header_map = {col.strip().upper(): idx for idx, col in enumerate(header)}

            def find_col(*names: str) -> int | None:
                for name in names:
                    if name.upper() in header_map:
                        return header_map[name.upper()]
                return None

            idx_cnpj = find_col("CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO", "CNPJ_CLASSE")
            idx_dt = find_col("DT_COMPTC")
            idx_total = find_col("VL_TOTAL")
            idx_quota = find_col("VL_QUOTA")
            idx_pl = find_col("VL_PATRIM_LIQ")
            idx_captc = find_col("CAPTC_DIA")
            idx_resg = find_col("RESG_DIA")
            idx_cotst = find_col("NR_COTST")
            idx_tipo = find_col("TP_FUNDO_CLASSE", "TP_FUNDO")

            if idx_cnpj is None or idx_dt is None:
                return []

            for line in text_stream:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                cols = line.split(";")
                if len(cols) <= idx_cnpj:
                    continue
                cnpj_raw = cols[idx_cnpj]
                if not cnpj_raw:
                    continue

                cnpj_clean = cnpj_raw.translate(_CNPJ_TRANS)
                if len(cnpj_clean) < 14:
                    cnpj_clean = cnpj_clean.zfill(14)

                if cnpj_clean not in target_cnpjs:
                    continue

                dt_str = cols[idx_dt] if len(cols) > idx_dt else None
                if not dt_str:
                    continue
                try:
                    dt = datetime.strptime(dt_str.strip(), "%Y-%m-%d").date()
                except ValueError:
                    continue

                vl_total = parse_number(cols[idx_total]) if idx_total is not None and len(cols) > idx_total else 0.0
                vl_quota = parse_number(cols[idx_quota]) if idx_quota is not None and len(cols) > idx_quota else 0.0
                vl_pl = parse_number(cols[idx_pl]) if idx_pl is not None and len(cols) > idx_pl else 0.0
                captc = parse_number(cols[idx_captc]) if idx_captc is not None and len(cols) > idx_captc else 0.0
                resg = parse_number(cols[idx_resg]) if idx_resg is not None and len(cols) > idx_resg else 0.0
                tipo = cols[idx_tipo].strip() if idx_tipo is not None and len(cols) > idx_tipo else None

                cotst_raw = cols[idx_cotst] if idx_cotst is not None and len(cols) > idx_cotst else None
                try:
                    cotst = int(float(cotst_raw)) if cotst_raw else 0
                except (ValueError, TypeError):
                    cotst = 0

                records.append(
                    DailyRecord(
                        cnpj=cnpj_clean,
                        dt_comptc=dt,
                        vl_total=vl_total or 0.0,
                        vl_quota=vl_quota or 0.0,
                        vl_patrim_liq=vl_pl or 0.0,
                        captc_dia=captc or 0.0,
                        resg_dia=resg or 0.0,
                        nr_cotst=cotst,
                        tp_fundo_classe=tipo,
                    )
                )

    return records


def dedupe_by_day(records: list[DailyRecord]) -> list[DailyRecord]:
    """Uma linha por (CNPJ, data).

    O arquivo mensal do INF_DIARIO e "atualizado diariamente com retificacoes"
    (a propria CVM documenta isso) - o mesmo mes pode conter mais de uma linha
    para o mesmo CNPJ na mesma data quando uma retificacao foi publicada. Sem
    dedupe, o agregador ve duas datas iguais como dois dias de uma semana e a
    cota compara valores de duas versoes do mesmo dia. Quando as duas linhas
    trazem TP_FUNDO_CLASSE, a que tiver "CLASSE" no rotulo vence (e o grao que
    a base de mercado usa); sem esse sinal, fica a ultima ocorrencia lida - a
    retificacao, no arquivo real, vem depois da linha original.
    """
    best: dict[tuple[str, date], DailyRecord] = {}
    for rec in records:
        key = (rec.cnpj, rec.dt_comptc)
        current = best.get(key)
        if current is None:
            best[key] = rec
            continue
        current_is_classe = bool(current.tp_fundo_classe) and "CLASSE" in current.tp_fundo_classe.upper()
        new_is_classe = bool(rec.tp_fundo_classe) and "CLASSE" in rec.tp_fundo_classe.upper()
        if new_is_classe or not current_is_classe:
            best[key] = rec
    return list(best.values())
