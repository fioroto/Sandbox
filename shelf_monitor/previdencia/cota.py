"""CNPJ pela cota do dia: o veiculo da vitrine achado no informe diario da CVM.

O catalogo do Itau Private publica, para cada plano, a cota com seis casas e a
data dela - e nenhum CNPJ. O informe diario da CVM publica a cota de cada
classe, todo dia, por CNPJ. A mesma cota, no mesmo dia, numa classe
previdenciaria e uma so, e o veiculo do plano.

Medido na vitrine de 2026-09-25 (41 planos, 39 com cota):

    via                                         com CNPJ    errado
    cota do dia                                 37 de 41    0
    nome (de registro, depois o comercial)      17 de 41    0
    nome, com os nomes alternativos da pagina   18 de 41    1

O erro e o caso que a previdencia tem e os fundos nao: cada seguradora tem o
proprio veiculo para a mesma estrategia. "IBIUNA PREV MM", o outro nome que a
pagina da ao "Private Prev Vision Plus MM", casa pelo nome com o "IBIUNA
PREVIDÊNCIA FIF CIC" generico; a cota diz que o do Itau e o "IBIUNA I
PREVIDÊNCIA ITAÚ". Por isso a cota vem antes, os nomes alternativos ficam de
fora, e o nome so serve a quem a cota nao identifica (sem cota, cota velha, ou
informe que ainda nao tem o dia). Os 17 do nome batiam todos com a cota.

A regra e a do nome: um candidato casa, dois ou mais nao. Cota redonda (10,0
de um fundo que acabou de nascer) empata e fica sem CNPJ, que e o certo.

A pagina da a data sem o ano ("9/09"): vale a mais recente que nao passa do dia
da coleta - "20/11" coletado em 25/09/2026 e 20/11/2025.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shelf_monitor.cnpj_por_nome import ResolvedorPorNome
from shelf_monitor.config import Config
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import normalize_cnpj
from shelf_monitor.texto import contagem

# Chave gravada em FundOffer.extra pela via da cota.
KEY_CNPJ_PELA_COTA = "cnpj_pela_cota"

FONTE_INFORME = "informe diário CVM"

# A pagina publica a cota com seis casas; o informe, com doze.
_CASAS = 6

_DIA_MES = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*$")


def data_da_cota(texto: str | None, hoje: date) -> date | None:
    """"9/09" -> a data mais recente com esse dia e mes que nao passa de hoje."""
    achado = _DIA_MES.match(texto or "")
    if not achado:
        return None
    dia, mes, ano = int(achado.group(1)), int(achado.group(2)), achado.group(3)
    try:
        if ano:
            return date(int(ano) + (2000 if len(ano) == 2 else 0), mes, dia)
        candidata = date(hoje.year, mes, dia)
        return candidata if candidata <= hoje else date(hoje.year - 1, mes, dia)
    except ValueError:
        return None


@dataclass
class ResumoPelaCota:
    """O que a via da cota fez - entra na observacao do snapshot."""

    examinados: int = 0
    identificados: int = 0
    # Mais de uma classe previdenciaria com a mesma cota no dia.
    empates: int = 0
    # Nenhuma classe previdenciaria com essa cota no dia (cota velha, por exemplo).
    sem_par: int = 0
    # O informe do mes nao estava disponivel, ou ainda nao tinha o dia.
    sem_informe: int = 0

    def nota(self) -> str:
        texto = (
            f"CNPJ pela cota do dia em {self.identificados} de "
            f"{contagem(self.examinados, 'fundo', 'fundos')} com cota"
        )
        partes = []
        if self.empates:
            partes.append(f"{contagem(self.empates, 'cota repetida', 'cotas repetidas')} no informe")
        if self.sem_par:
            partes.append(f"{self.sem_par} sem par no informe")
        if self.sem_informe:
            partes.append(f"{self.sem_informe} sem o dia no informe")
        return f"{texto} ({', '.join(partes)})" if partes else texto


@dataclass
class IndiceDeCotas:
    """(dia, cota com seis casas) -> CNPJs das classes, so dos dias pedidos."""

    cnpjs: dict[tuple[date, float], set[str]] = field(default_factory=lambda: defaultdict(set))
    dias_lidos: set[date] = field(default_factory=set)

    def ler_zip(self, caminho: Path, dias: set[date]) -> None:
        """Le o zip do mes e guarda so as linhas dos dias pedidos."""
        procurados = {dia.isoformat(): dia for dia in dias}
        with zipfile.ZipFile(caminho) as arquivo:
            for membro in arquivo.namelist():
                if not membro.lower().endswith(".csv"):
                    continue
                with arquivo.open(membro) as bruto:
                    leitor = csv.DictReader(
                        io.TextIOWrapper(bruto, encoding="latin-1"), delimiter=";"
                    )
                    for linha in leitor:
                        dia = procurados.get(linha.get("DT_COMPTC") or "")
                        if dia is None:
                            continue
                        cnpj = normalize_cnpj(linha.get("CNPJ_FUNDO_CLASSE"))
                        try:
                            cota = round(float(linha.get("VL_QUOTA") or ""), _CASAS)
                        except ValueError:
                            continue
                        if cnpj:
                            self.cnpjs[(dia, cota)].add(cnpj)
                            self.dias_lidos.add(dia)

    def candidatos(self, dia: date, cota: float) -> set[str]:
        return self.cnpjs.get((dia, round(cota, _CASAS)), set())


def carregar_indice(config: Config, dias: set[date], *, baixar: bool = True) -> IndiceDeCotas:
    """Os dias pedidos, do zip mensal do informe (baixado com cache condicional).

    O mes corrente muda todo dia: se o zip em disco ainda nao tem um dia pedido,
    ele e baixado de novo uma vez. Sem rede, fica o que o disco tiver - e o
    fundo do dia que falta segue para a via do nome.
    """
    from shelf_monitor.market.informe_diario import fetch_inf_diario_month

    indice = IndiceDeCotas()
    por_mes: dict[str, set[date]] = defaultdict(set)
    for dia in dias:
        por_mes[f"{dia.year}{dia.month:02d}"].add(dia)

    for mes, dias_do_mes in sorted(por_mes.items()):
        local = config.market_raw_dir / f"inf_diario_fi_{mes}.zip"
        caminho = (
            fetch_inf_diario_month(config, mes) if baixar else (local if local.is_file() else None)
        )
        if caminho is None:
            continue
        indice.ler_zip(caminho, dias_do_mes)
        faltam = dias_do_mes - indice.dias_lidos
        if faltam and baixar:
            novo = fetch_inf_diario_month(config, mes, force=True)
            if novo is not None:
                indice.ler_zip(novo, faltam)
    return indice


def _cota_e_dia(oferta: FundOffer, hoje: date) -> tuple[float, date] | None:
    cota = oferta.extra.get("cota")
    dia = data_da_cota(oferta.extra.get("data_cota"), hoje)
    if cota is None or dia is None:
        return None
    return float(cota), dia


def identificar_pela_cota(
    ofertas: list[FundOffer],
    indice: IndiceDeCotas,
    resolvedor: ResolvedorPorNome,
    hoje: date,
) -> ResumoPelaCota:
    """Preenche o CNPJ das ofertas cuja cota do dia aponta uma classe so.

    So conta classe do universo do resolvedor (ativa e previdenciaria, na
    previdencia): a cota de um fundo qualquer que coincida no centavo nao e o
    veiculo do plano.
    """
    resumo = ResumoPelaCota()
    for oferta in ofertas:
        if oferta.cnpj:
            continue
        cota_e_dia = _cota_e_dia(oferta, hoje)
        if cota_e_dia is None:
            continue
        cota, dia = cota_e_dia
        resumo.examinados += 1
        if dia not in indice.dias_lidos:
            resumo.sem_informe += 1
            continue
        candidatos = {
            cnpj: nome
            for cnpj in indice.candidatos(dia, cota)
            if (nome := resolvedor.nome_no_cadastro(cnpj))
        }
        if not candidatos:
            resumo.sem_par += 1
            continue
        if len(candidatos) > 1:
            resumo.empates += 1
            continue
        (cnpj, nome), = candidatos.items()
        oferta.cnpj = cnpj
        oferta.extra[KEY_CNPJ_PELA_COTA] = {
            "fonte": FONTE_INFORME,
            "dia": dia.isoformat(),
            "cota": round(cota, _CASAS),
            "nome": nome,
        }
        resumo.identificados += 1
    return resumo


def dias_das_cotas(ofertas: list[FundOffer], hoje: date) -> set[date]:
    """Os dias que o informe precisa ter, das ofertas ainda sem CNPJ."""
    return {
        cota_e_dia[1]
        for oferta in ofertas
        if not oferta.cnpj and (cota_e_dia := _cota_e_dia(oferta, hoje))
    }
