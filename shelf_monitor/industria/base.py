"""O que o monitor da industria le para uma competencia, carregado uma vez.

Tres fontes, cada uma no seu cache:

- o **cadastro** (retrato mensal em `data/historico/cadastro/`): o ultimo
  retrato vale para a janela inteira - atributos fixos, ver `universo.py`;
- o **perfil mensal** de cada competencia da janela (compacto em
  `data/cache/industria/perfil/`): so as competencias mais recentes vao a
  rede, por requisicao condicional;
- a **serie mensal do informe diario** (`data/cache/serie/`, a mesma do bloco
  de rentabilidade), terminada na competencia pedida com `ate=`.

A competencia padrao e a ultima fechada com perfil publicado. A CVM publica o
perfil e a carteira de M por volta do dia 15 do mes seguinte (08/2026 estava no
ar em 28/09/2026; 09/2026 dava 404).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from shelf_monitor.config import Config
from shelf_monitor.familias import MapaDeFamilias
from shelf_monitor.industria.cadastro import Cadastro, carregar_cadastro
from shelf_monitor.industria.cotistas import PerfilClasse, carregar_perfil
from shelf_monitor.industria.curadoria import Curadoria, carregar_curadoria
from shelf_monitor.industria.estrutura import MapaDeEstrutura
from shelf_monitor.industria.taxonomia import Taxonomia
from shelf_monitor.market.datasets import COMPETENCIAS_PERFIL_REVALIDADAS
from shelf_monitor.report.rentabilidade import SerieMensal
from shelf_monitor.report.retail_flow import months_back
from shelf_monitor.texto import competencia_br

# Janela padrao: 13 competencias, a de referencia e as 12 anteriores - o
# bastante para 12 meses com a competencia-base (o fechamento anterior).
MESES_PADRAO = 13


def competencia_anterior(competencia: str) -> str:
    ano, mes = int(competencia[:4]), int(competencia[4:6])
    return f"{ano - 1:04d}12" if mes == 1 else f"{ano:04d}{mes - 1:02d}"


def ultima_competencia_fechada(hoje: date | None = None) -> str:
    """O mes anterior ao de hoje, em AAAAMM."""
    hoje = hoje or date.today()
    return competencia_anterior(f"{hoje.year:04d}{hoje.month:02d}")


@dataclass
class BaseIndustria:
    """Cadastro, perfis e serie de uma competencia de referencia."""

    competencia: str
    meses: list[str]
    cadastro: Cadastro
    serie: SerieMensal
    perfis: dict[str, dict[str, PerfilClasse]]
    avisos: list[str] = field(default_factory=list)
    # A carteira (CDA) das ultimas competencias, pelo mapa de familias. None
    # quando nao ha carteira em disco nem na CVM: a estrutura cai para o que o
    # perfil diz (master) e o resto fica "sem carteira".
    mapa: MapaDeFamilias | None = None
    curadoria: Curadoria | None = None
    _perfil_fixo: dict[str, PerfilClasse] | None = field(default=None, repr=False)
    _estrutura: MapaDeEstrutura | None = field(default=None, repr=False)
    _taxonomia: Taxonomia | None = field(default=None, repr=False)

    def perfil_do_mes(self, cnpj: str, competencia: str) -> PerfilClasse | None:
        """O perfil da classe no mes; sem ele, o mais recente anterior."""
        for mes in reversed(self.meses):
            if mes > competencia:
                continue
            perfil = self.perfis.get(mes, {}).get(cnpj)
            if perfil is not None and perfil.informado:
                return perfil
        return None

    @property
    def perfil_fixo(self) -> dict[str, PerfilClasse]:
        """O perfil mais recente de cada classe na janela: e o que decide as
        regras de universo que dependem do dono do dinheiro."""
        if self._perfil_fixo is None:
            fixo: dict[str, PerfilClasse] = {}
            for mes in self.meses:
                for cnpj, perfil in self.perfis.get(mes, {}).items():
                    if perfil.informado:
                        fixo[cnpj] = perfil
            self._perfil_fixo = fixo
        return self._perfil_fixo

    @property
    def meses_com_perfil(self) -> list[str]:
        return [m for m in self.meses if self.perfis.get(m)]

    @property
    def estrutura(self) -> MapaDeEstrutura:
        if self._estrutura is None:
            self._estrutura = MapaDeEstrutura(
                self.cadastro, self.perfil_fixo, self.mapa,
                self.curadoria or Curadoria({}, [], [], [], {}),
            )
        return self._estrutura

    @property
    def taxonomia(self) -> Taxonomia:
        if self._taxonomia is None:
            self._taxonomia = Taxonomia(
                self.cadastro, self.estrutura, self.mapa,
                self.curadoria or Curadoria({}, [], [], [], {}),
            )
        return self._taxonomia


def carregar_base(
    config: Config,
    competencia: str | None = None,
    *,
    meses: int = MESES_PADRAO,
    baixar: bool = True,
    force: bool = False,
    progresso=None,
    carteira: bool = True,
) -> BaseIndustria | None:
    """Carrega tudo o que a competencia pede; None sem cadastro nenhum.

    `progresso(etapa, detalhe)` recebe o andamento, para o console.
    """
    cadastro = carregar_cadastro(config)
    if cadastro is None:
        return None
    competencia = competencia or ultima_competencia_fechada()
    janela = months_back(meses, referencia=date(int(competencia[:4]), int(competencia[4:6]), 1))
    avisos: list[str] = []

    recentes = set(janela[-COMPETENCIAS_PERFIL_REVALIDADAS:])
    perfis: dict[str, dict[str, PerfilClasse]] = {}
    for mes in janela:
        lido, aviso = carregar_perfil(
            config, mes, revalidar=mes in recentes, baixar=baixar, force=force and mes in recentes,
        )
        if aviso:
            avisos.append(aviso)
        if lido:
            perfis[mes] = lido
        if progresso:
            progresso("perfil", f"{competencia_br(mes)}: {len(lido) if lido else 0} classes")

    serie = SerieMensal.build(
        config, meses=meses, ate=competencia,
        progresso=(lambda mes, n: progresso("informe", f"{competencia_br(mes)}: {n or 0} subclasses"))
        if progresso else None,
    )
    faltando = [m for m in janela if m not in serie.meses]
    if faltando:
        avisos.append(
            "informe diário ausente em " + ", ".join(competencia_br(m) for m in faltando)
        )

    mapa = None
    if carteira:
        try:
            mapa = MapaDeFamilias.carregar(config, baixar=baixar)
        except Exception as exc:  # noqa: BLE001 - sem carteira o universo ainda sai
            avisos.append(f"carteira da CVM indisponível: {exc}")
        if mapa is None:
            avisos.append(
                "sem a carteira mensal da CVM: master, feeder e espelho saem só do "
                "perfil mensal, e o resto fica sem carteira"
            )
        else:
            avisos.extend(mapa.avisos)
        if progresso:
            progresso("carteira", ", ".join(competencia_br(c) for c in mapa.competencias) if mapa else "ausente")
    return BaseIndustria(
        competencia=competencia,
        meses=janela,
        cadastro=cadastro,
        serie=serie,
        perfis=perfis,
        avisos=avisos,
        mapa=mapa,
        curadoria=carregar_curadoria(config),
    )
