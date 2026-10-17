"""O recorte do digest semanal: as casas em foco, a edicao e a oportunidade.

O digest v2 (14/10/2026) mostra so XP, BTG e Itau (`Config.casas_do_digest`);
o resto da execucao - dashboard, planilha e deck - continua com todas as casas.
Este modulo monta, uma vez, o que o digest desenha:

- o relatorio RECORTADO as casas do digest: as vitrines e os diffs delas; a
  captacao com o mapa de ritmo das casas em foco e as listas de maiores
  captacoes e resgates refeitas so com fundos delas; rentabilidade e custo so
  com as linhas delas. Sem o radar da CVM: saiu do digest em 17/10/2026, a
  pedido da area, e segue no dashboard, na planilha e no deck - tirado aqui, o
  assunto e as fontes do e-mail tambem deixam de cita-lo;
- o bloco de gaps ranqueado e a oportunidade de grade (`report.oportunidade`)
  sobre esse recorte - os gaps sao medidos so contra as vitrines em foco;
- a edicao: o primeiro digest de cada mes e o mensal, com rentabilidade e
  custo; os outros sao semanais.

Recortar na renderizacao vale porque o numero de cada fundo nao depende de
quais casas entram: o informe da CVM e por fundo, nao por distribuidor. O unico
numero que dependia - "so nesta vitrine" - saiu junto com a captacao por casa.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date

from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.models import BLOCO_COTISTAS, RunReport
from shelf_monitor.report.aggregate import bloco_de_gaps, ranquear_gaps
from shelf_monitor.report.oportunidade import oportunidade_de_grade
from shelf_monitor.report.performance import com_rentabilidade, rentabilidade_por_cnpj
from shelf_monitor.report.retail_flow import _MIN_PL_RANKING

EDICAO_SEMANAL = "semanal"
EDICAO_MENSAL = "mensal"

_ARQUIVO_DO_DIGEST = re.compile(r"^digest-(\d{4})-(\d{2})-(\d{2})\.md$")

# Quantos fundos cada lista de captacao do recorte guarda (o digest mostra 5).
_LISTADOS = 15


def edicao_do_digest(config: Config | None, hoje: date) -> str:
    """Mensal no primeiro digest do mes; semanal nos outros.

    O primeiro e o que nao acha, na pasta dos relatorios, digest do mesmo mes
    com data anterior - rodar de novo no mesmo dia refaz a mesma edicao. Sem
    config (render direto, sem pasta), semanal.
    """
    if config is None:
        return EDICAO_SEMANAL
    for caminho in config.reports_dir.glob("digest-*.md"):
        achado = _ARQUIVO_DO_DIGEST.match(caminho.name)
        if not achado:
            continue
        try:
            dia = date(*(int(g) for g in achado.groups()))
        except ValueError:
            continue
        if (dia.year, dia.month) == (hoje.year, hoje.month) and dia < hoje:
            return EDICAO_SEMANAL
    return EDICAO_MENSAL


@dataclass
class VisaoDoDigest:
    """O que o digest desenha: o relatorio recortado e o que se calcula sobre ele."""

    report: RunReport
    casas: tuple[str, ...]
    base_id: str
    edicao: str
    gaps: dict
    oportunidade: dict
    # A lista da casa (`distribuivel.BaseDistribuivel`): o servico de cada
    # fundo que a XP tem, Distribuicao ou FaaS.
    distribuivel: object | None = None

    @property
    def concorrentes(self) -> tuple[str, ...]:
        return tuple(c for c in self.casas if c != self.base_id)

    @property
    def mensal(self) -> bool:
        return self.edicao == EDICAO_MENSAL

    def servico(self, cnpj: str | None) -> str | None:
        """"Distribuição" ou "FaaS", quando a lista da casa traz o fundo com ele."""
        lista = self.distribuivel
        return lista.rotulo_do_servico(cnpj) if lista is not None and cnpj else None

    def da_casa(self, cnpj: str | None) -> bool:
        """O fundo esta na lista da casa (a XP o tem, mesmo fora da vitrine)."""
        lista = self.distribuivel
        return bool(lista is not None and cnpj and lista.situacao_de(cnpj))


def montar_visao(
    report: RunReport, config: Config | None = None, edicao: str | None = None
) -> VisaoDoDigest:
    """O recorte do digest, com os gaps ranqueados e a oportunidade de grade."""
    identidade = config if config is not None else Config
    casas = tuple(identidade.casas_do_digest)
    base_id = next(
        (c for c in casas if c in PLATFORMS and PLATFORMS[c].is_baseline), casas[0]
    )
    recortado = recortar(report, casas)
    # A rentabilidade de cada fundo vai junto, como referencia, nos gaps e nas
    # listas de captacao (pedido de 16/10/2026): 12 meses e 3 meses, com a
    # mediana da categoria.
    rentabilidade = rentabilidade_por_cnpj(report.performance_12m, report.performance)
    fluxo = recortado.retail_flow
    if fluxo and fluxo.get("available") and fluxo is not report.retail_flow:
        for chave in ("top_captacao", "top_resgate"):
            fluxo[chave] = com_rentabilidade(fluxo.get(chave) or [], rentabilidade)
    gaps = bloco_de_gaps(recortado)
    if gaps.get("available"):
        gaps = {**gaps, "funds": com_rentabilidade(
            ranquear_gaps(gaps.get("funds") or [], recortado.retail_flow), rentabilidade
        )}
    oportunidade = oportunidade_de_grade(
        gaps, recortado.retail_flow, com_cotistas=not report.suprime(BLOCO_COTISTAS)
    )
    return VisaoDoDigest(
        report=recortado,
        casas=casas,
        base_id=base_id,
        edicao=edicao or edicao_do_digest(config, report.generated_at.date()),
        gaps=gaps,
        oportunidade=oportunidade,
        distribuivel=report.distribuivel,
    )


def recortar(report: RunReport, casas: tuple[str, ...]) -> RunReport:
    """O relatorio so com as casas do digest, e sem o radar da CVM (17/10/2026)."""
    foco = set(casas)
    com_cotistas = not report.suprime(BLOCO_COTISTAS)
    return replace(
        report,
        snapshots=[s for s in report.snapshots if s.platform in foco],
        shelf_diffs=[d for d in report.shelf_diffs if d.platform in foco],
        cvm_snapshot=None,
        cvm_diff=None,
        retail_flow=_recortar_fluxo(report.retail_flow, foco, com_cotistas),
        performance=_recortar_casas(report.performance, foco),
        performance_12m=_recortar_casas(report.performance_12m, foco),
        custo=_recortar_casas(report.custo, foco),
        mandato=_recortar_casas(report.mandato, foco),
    )


def _recortar_fluxo(flow: dict | None, foco: set[str], com_cotistas: bool) -> dict | None:
    """A captacao das casas em foco: ritmo delas e listas so com fundos delas.

    As listas sao refeitas dos fundos (`_fluxos`) com os mesmos criterios de
    `build_retail_flow`: recortar o top 15 de todas as casas deixaria a lista
    curta quando os maiores fossem de casa fora do foco. Sem os objetos (bloco
    lido de JSON), recorta o que veio pronto.
    """
    if not (flow and flow.get("available")):
        return flow

    def so_do_foco(dados: dict) -> dict:
        return {**dados, "vitrines": [p for p in dados.get("vitrines") or [] if p in foco]}

    fluxos = flow.get("_fluxos")
    if fluxos is None:
        captacao = [
            so_do_foco(f) for f in flow.get("top_captacao") or []
            if foco & set(f.get("vitrines") or [])
        ]
        resgate = [
            so_do_foco(f) for f in flow.get("top_resgate") or []
            if foco & set(f.get("vitrines") or [])
        ]
        estruturais = flow.get("total_estruturais", 0)
    else:
        ranqueaveis = [
            f for f in fluxos if foco & set(f.vitrines) and f.pl_inicio >= _MIN_PL_RANKING
        ]
        captadores = sorted(
            (f for f in ranqueaveis if f.entrou(com_cotistas)), key=lambda f: -f.captacao_liquida
        )
        perdedores = sorted(
            (f for f in ranqueaveis if f.saiu(com_cotistas)), key=lambda f: f.captacao_liquida
        )
        captacao = [so_do_foco(f.to_dict()) for f in captadores[:_LISTADOS]]
        resgate = [so_do_foco(f.to_dict()) for f in perdedores[:_LISTADOS]]
        estruturais = sum(1 for f in ranqueaveis if f.estrutural)
    return {
        **flow,
        "casas": [c for c in flow.get("casas") or [] if c.get("platform_id") in foco],
        "tendencia_por_casa": {
            pid: estados for pid, estados in (flow.get("tendencia_por_casa") or {}).items()
            if pid in foco
        },
        "top_captacao": captacao,
        "top_resgate": resgate,
        "total_estruturais": estruturais,
        "excluded": [
            e for e in flow.get("excluded") or [] if e.get("platform_id", "") in foco
        ],
    }


def _recortar_casas(bloco: dict | None, foco: set[str]) -> dict | None:
    """Um bloco por casa (rentabilidade, custo, mandato) so com as casas em foco."""
    if not (bloco and bloco.get("available")):
        return bloco
    saida = {**bloco}
    if "casas" in bloco:
        saida["casas"] = [c for c in bloco["casas"] if c.get("platform_id") in foco]
        if not saida["casas"]:
            saida["available"] = False
            saida["reason"] = "nenhuma das casas do digest no bloco"
    if isinstance(bloco.get("onde_esta_a_gente"), dict):
        saida["onde_esta_a_gente"] = {
            pid: fundos for pid, fundos in bloco["onde_esta_a_gente"].items() if pid in foco
        }
    if "excluded" in bloco:
        saida["excluded"] = [
            e for e in bloco["excluded"] if e.get("platform_id", "") in foco
        ]
    return saida
