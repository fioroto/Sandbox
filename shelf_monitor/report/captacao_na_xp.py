"""Captacao na XP dos fundos de vitrine: o numero ao lado do da CVM.

Pedido de 20/10/2026. O informe diario da CVM da a captacao do fundo no mercado
inteiro; as bases internas (`shelf_monitor.captacao_interna`) dao a da XP. Os
dois numeros so se comparam se medirem a mesma coisa:

- **a mesma janela**: a semana de referencia e os 3 meses do bloco de captacao
  (`retail_flow`), com os mesmos dias. O mes fechado sai da base mensal; o mes
  corrente e a semana, da diaria, ate o mesmo ultimo dia completo do informe (o
  mes fechado sem a mensal tambem sai da diaria, se ela o traz inteiro). A
  janela que as bases nao cobrem inteira fica sem numero, nunca com a soma
  parcial: dois meses da XP ao lado de tres da CVM pareceriam uma fatia menor do
  que e;
- **o mesmo fundo**: a captacao e a do fundo que a XP distribui - na vitrine ou
  na lista da casa (`distribuivel`, so "distribui"). O fundo que a XP nao
  distribui fica sem numero, e nao com zero. Sem o proprio fundo, contam os que
  a XP distribui com o mesmo master pela carteira da CVM (`familias.master_de`):
  o espelho do BTG ao lado do espelho da XP, ou o master ao lado dos espelhos
  dela, somados - os fundos que levam o cliente da XP a mesma carteira. Classe
  paralela e correlato ficam de fora: sao outra carteira, e o fluxo de uma nao
  e o da outra.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from shelf_monitor.distribuivel import DISTRIBUI
from shelf_monitor.report.aggregate import listing_funds
from shelf_monitor.texto import competencia_br, contagem, data_br, periodo_br

LIGACAO_MESMO_FUNDO = "mesmo fundo"
LIGACAO_MESMO_MASTER = "mesmo master"

# O que o cruzamento poe em cada linha de fundo (`com_captacao_na_xp`).
CAMPOS_NA_XP = ("captacao_xp_3m", "captacao_xp_semana", "xp_ligacao", "xp_fundos")

FONTE_MENSAL = "mensal"
FONTE_DIARIA = "diaria"


@dataclass(frozen=True)
class Pedaco:
    """Um trecho da janela e a base que o mede."""

    fonte: str
    de: date
    ate: date
    mes: str

    def to_dict(self) -> dict:
        return {
            "fonte": self.fonte, "de": self.de.isoformat(), "ate": self.ate.isoformat(),
            "mes": self.mes,
        }


@dataclass(frozen=True)
class Janela:
    """Uma janela do informe e como as bases da XP a medem."""

    de: date
    ate: date
    pedacos: tuple[Pedaco, ...] = ()
    # Por que a janela ficou sem numero; None quando as bases a cobrem inteira.
    motivo: str | None = None

    @property
    def medida(self) -> bool:
        return self.motivo is None

    def to_dict(self) -> dict:
        return {
            "de": self.de.isoformat(), "ate": self.ate.isoformat(),
            "medida": self.medida, "motivo": self.motivo,
            "pedacos": [p.to_dict() for p in self.pedacos],
        }


def _uteis(de: date, ate: date) -> list[date]:
    dias, dia = [], de
    while dia <= ate:
        if dia.weekday() < 5:
            dias.append(dia)
        dia += timedelta(days=1)
    return dias


def _dias_do_informe(flow: dict, de: date, ate: date) -> list[date]:
    """Os dias completos do informe na janela; sem o recorte (bloco lido de
    JSON), os dias uteis."""
    recorte = flow.get("_recorte")
    if recorte is not None:
        return [d for d in recorte.dias.completos if de <= d <= ate]
    return _uteis(de, ate)


def _falta_na_diaria(base, dias: list[date]) -> str | None:
    """Por que a base diaria nao mede os dias; None quando mede."""
    faltam = base.faltam(dias)
    if not faltam:
        return None
    if not base.dias:
        return "sem a base diária da XP"
    if min(faltam) > base.ultimo_dia:
        return f"a base diária da XP vai até {data_br(base.ultimo_dia)}"
    if max(faltam) < base.primeiro_dia:
        return f"a base diária da XP começa em {data_br(base.primeiro_dia)}"
    return (
        f"a base diária da XP não traz {contagem(len(faltam), 'dia', 'dias')} "
        f"{periodo_br(dias[0], dias[-1])}"
    )


def janela_de_3_meses(flow: dict, base) -> Janela | None:
    """Os 3 meses do informe, mes a mes: a mensal no mes fechado, a diaria no corrente."""
    periodo = flow.get("periodo") or {}
    if not periodo.get("de") or not periodo.get("ate"):
        return None
    de, ate = date.fromisoformat(periodo["de"]), date.fromisoformat(periodo["ate"])
    por_mes: dict[str, list[date]] = {}
    for dia in _dias_do_informe(flow, de, ate):
        por_mes.setdefault(f"{dia.year:04d}{dia.month:02d}", []).append(dia)
    corrente = f"{ate.year:04d}{ate.month:02d}"
    pedacos: list[Pedaco] = []
    for mes, dias in sorted(por_mes.items()):
        if mes != corrente and base.tem_mes(mes):
            pedacos.append(Pedaco(FONTE_MENSAL, dias[0], dias[-1], mes))
            continue
        falta = _falta_na_diaria(base, dias)
        if falta is None:
            pedacos.append(Pedaco(FONTE_DIARIA, dias[0], dias[-1], mes))
            continue
        if mes != corrente:
            falta = f"sem {competencia_br(mes)} na base mensal, e {falta}"
        return Janela(de, ate, motivo=falta)
    return Janela(de, ate, tuple(pedacos))


def janela_da_semana(flow: dict, base) -> Janela | None:
    """A semana de referencia, so pela base diaria."""
    recorte = flow.get("_recorte")
    if recorte is not None and recorte.semana.dias:
        dias = list(recorte.semana.dias)
    else:
        semana = (flow.get("recorte_semanal") or {}).get("semana") or {}
        if not semana.get("de") or not semana.get("ate"):
            return None
        dias = _uteis(date.fromisoformat(semana["de"]), date.fromisoformat(semana["ate"]))
    if not dias:
        return None
    falta = _falta_na_diaria(base, dias)
    if falta is not None:
        return Janela(dias[0], dias[-1], motivo=falta)
    mes = f"{dias[-1].year:04d}{dias[-1].month:02d}"
    return Janela(dias[0], dias[-1], (Pedaco(FONTE_DIARIA, dias[0], dias[-1], mes),))


def _soma(base, cnpjs: list[str], janela: Janela | None) -> float | None:
    if janela is None or not janela.medida:
        return None
    total = 0.0
    for pedaco in janela.pedacos:
        if pedaco.fonte == FONTE_MENSAL:
            total += base.no_mes(cnpjs, pedaco.mes)
        else:
            total += base.nos_dias(cnpjs, pedaco.de, pedaco.ate)
    return round(total, 2)


def distribuidos(snapshots, distribuivel, baseline_id: str = "xp") -> set[str]:
    """Os CNPJs que a XP distribui: a vitrine dela e o "distribui" da lista da casa.

    "Pode distribuir" fica de fora: e capacidade, nao fundo que o cliente
    compra hoje.
    """
    cnpjs: set[str] = set()
    for snapshot in snapshots:
        if snapshot.platform == baseline_id:
            cnpjs |= {f.cnpj for f in listing_funds(snapshot) if f.cnpj}
    if distribuivel is not None:
        cnpjs |= {c for c, situacao in distribuivel.situacao.items() if situacao == DISTRIBUI}
    return cnpjs


def _nomes(snapshots, baseline_id: str) -> dict[str, str]:
    nomes: dict[str, str] = {}
    for snapshot in snapshots:
        if snapshot.platform == baseline_id:
            for fundo in snapshot.funds:
                if fundo.cnpj and fundo.name:
                    nomes.setdefault(fundo.cnpj, fundo.name)
    return nomes


def cruzamento(cnpj: str, da_xp: set[str], indice, familias) -> tuple[str, list[dict]] | None:
    """(ligacao, fundos da XP que medem o fundo), ou None quando a XP nao o
    distribui nem distribui outro fundo do mesmo master."""
    if indice is None:
        return (LIGACAO_MESMO_FUNDO, [{"cnpj": cnpj}]) if cnpj in da_xp else None
    if cnpj in indice.classes:
        return LIGACAO_MESMO_FUNDO, [{"cnpj": cnpj}]
    master = familias.master_de(cnpj)
    veiculos = sorted(indice.por_master.get(master) or [])
    if not veiculos:
        return None
    return LIGACAO_MESMO_MASTER, [{"cnpj": c, "master": c == master} for c in veiculos]


def captacao_na_xp(
    flow: dict | None, snapshots, distribuivel, familias, base, baseline_id: str = "xp",
) -> dict:
    """A captacao na XP de cada fundo do bloco de captacao, nas janelas dele."""
    if not (flow and flow.get("available")):
        return {
            "available": False,
            "reason": "sem o informe diário da CVM nesta execução, não há janela para comparar",
        }
    motivo = base.motivo_da_supressao() if base is not None else "bases da XP não lidas"
    if motivo:
        return {"available": False, "reason": motivo}
    da_xp = distribuidos(snapshots, distribuivel, baseline_id)
    indice = familias.preparar_base(da_xp) if familias is not None else None
    nomes = _nomes(snapshots, baseline_id)
    tres_meses = janela_de_3_meses(flow, base)
    semana = janela_da_semana(flow, base)

    por_cnpj: dict[str, dict] = {}
    for fundo in flow.get("fundos") or []:
        cnpj = fundo.get("cnpj")
        achado = cruzamento(cnpj, da_xp, indice, familias) if cnpj else None
        if achado is None:
            continue
        ligacao, veiculos = achado
        for veiculo in veiculos:
            veiculo["nome"] = nomes.get(veiculo["cnpj"]) or (
                familias.nome(veiculo["cnpj"]) if familias is not None else ""
            )
        cnpjs = [v["cnpj"] for v in veiculos]
        por_cnpj[cnpj] = {
            "captacao_xp_3m": _soma(base, cnpjs, tres_meses),
            "captacao_xp_semana": _soma(base, cnpjs, semana),
            "xp_ligacao": ligacao,
            "xp_fundos": veiculos,
        }
    medidos = indice.classes if indice is not None else da_xp
    return {
        "available": True,
        "base": base.to_dict(),
        "descricao_da_base": base.descricao(),
        "tres_meses": tres_meses.to_dict() if tres_meses else None,
        "semana": semana.to_dict() if semana else None,
        "por_cnpj": por_cnpj,
        "cruzados": len(por_cnpj),
        # Fundo com captacao na base e fora da vitrine e da lista da casa: o
        # cruzamento nao o usa, e o console avisa.
        "fora_do_que_distribui": len(base.cnpjs() - set(medidos)),
    }


def com_captacao_na_xp(fundos: list[dict], bloco: dict | None) -> list[dict]:
    """Copias das linhas com `CAMPOS_NA_XP` (None onde a XP nao distribui o fundo)."""
    if not (bloco and bloco.get("available")):
        return fundos
    por_cnpj = bloco.get("por_cnpj") or {}
    return [
        {**fundo, **{
            campo: (por_cnpj.get(fundo.get("cnpj") or "") or {}).get(campo)
            for campo in CAMPOS_NA_XP
        }}
        for fundo in fundos
    ]


def com_numero(bloco: dict | None) -> bool:
    """Ha numero da XP para mostrar: o bloco veio e mede ao menos uma janela."""
    if not (bloco and bloco.get("available")):
        return False
    return any((bloco.get(j) or {}).get("medida") for j in ("tres_meses", "semana"))


def via(fundo: dict) -> str:
    """"pelo espelho", "pelo master" ou "por 2 fundos do mesmo master"; "" no
    proprio fundo ou fora da XP."""
    if fundo.get("xp_ligacao") != LIGACAO_MESMO_MASTER:
        return ""
    veiculos = fundo.get("xp_fundos") or []
    if len(veiculos) > 1:
        return f"por {len(veiculos)} fundos do mesmo master"
    return "pelo master" if veiculos and veiculos[0].get("master") else "pelo espelho"


def notas(bloco: dict | None) -> list[str]:
    """Como ler o numero da XP, e a janela que ficou sem ele - frases curtas."""
    if not com_numero(bloco):
        return []
    tres, semana = bloco.get("tres_meses") or {}, bloco.get("semana") or {}
    mensais = [p["mes"] for p in tres.get("pedacos") or [] if p["fonte"] == FONTE_MENSAL]
    fontes = (
        "a base mensal nos meses fechados e a diária no mês corrente e na semana"
        if mensais else "a base diária"
    )
    texto = [
        "Na XP: a captação líquida do fundo na XP, pelas bases internas "
        f"({bloco.get('descricao_da_base')}) - {fontes} -, nas mesmas janelas da CVM. "
        "Só do fundo que a XP distribui, na vitrine ou na lista da casa; sem ele, dos "
        "fundos que a XP distribui com o mesmo master pela carteira da CVM, somados. "
        "Sem número: a XP não distribui o fundo nem outro do mesmo master."
    ]
    if tres and not tres.get("medida"):
        texto.append(f"Na XP, os 3 meses ficaram sem número: {tres.get('motivo')}.")
    if semana and not semana.get("medida"):
        texto.append(f"Na XP, a semana ficou sem número: {semana.get('motivo')}.")
    return texto
