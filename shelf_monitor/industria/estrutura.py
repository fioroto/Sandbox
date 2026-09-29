"""Master, feeder, espelho e FoF: o papel de cada classe na estrutura.

A carteira mensal da CVM (bloco de cotas de fundos) diz em que classes cada uma
investe; o perfil mensal diz quanto de cada uma esta em maos de outros fundos.
Com as duas, cada classe recebe um papel:

- **master**: ao menos 90% do PL detido por outros fundos. Sai da soma do
  universo e empresta estrategia, gestora e rentabilidade aos veiculos;
- **feeder**: ao menos 95% do PL numa classe do MESMO grupo economico;
- **espelho**: ao menos 95% numa classe de OUTRO grupo - o veiculo que uma
  casa monta sobre a estrategia de terceiro;
- **feeder exterior**: ao menos 95% num unico veiculo coletivo no exterior;
- **FoF** (da casa ou multigestor): ao menos 67% em cotas, sem alvo unico;
- **hibrido**: de 10% a 90% detido por fundos, com carteira propria;
- **direto**: carteira propria e investidor final.

O flag `FUNDO_ESPELHO` do extrato so informa: marcava 44 dos 201 espelhos
reais entre os gaps da vitrine XP (26/09/2026). Sem carteira, a classe fica
"sem carteira" e o flag aparece como "espelho declarado".

**Casa dedicada.** Parte dos feeders e da propria gestora, mas feita para um
distribuidor ("Advisory" na XP, "Access" no Inter). O veiculo ganha a casa
dedicada quando e espelho montado por um grupo distribuidor (banco,
plataforma, cooperativa), ou quando o nome traz a marca de uma casa diferente
do grupo da estrategia. Toda casa recebe o mesmo tratamento.
"""
from __future__ import annotations

from dataclasses import dataclass

from shelf_monitor.familias import MapaDeFamilias
from shelf_monitor.industria.cadastro import Cadastro, Produto
from shelf_monitor.industria.cotistas import PerfilClasse
from shelf_monitor.industria.curadoria import TIPOS_DISTRIBUIDORES, Curadoria, Grupo
from shelf_monitor.report.shared.limiares import (
    MAX_DIVERGENCIA_PERFIL_CARTEIRA,
    MIN_FATIA_ALVO_UNICO,
    MIN_FATIA_FOF,
    MIN_FATIA_FUNDOS_MASTER,
    MIN_FATIA_HIBRIDO,
)

PAPEIS: dict[str, str] = {
    "direto": "Direto",
    "feeder": "Feeder",
    "espelho": "Espelho",
    "feeder_exterior": "Feeder de fundo no exterior",
    "fof_casa": "FoF da casa",
    "fof_multigestor": "FoF multigestor",
    "hibrido": "Híbrido",
    "master": "Master",
    "sem_carteira": "Sem carteira",
}
# Veiculos que carregam a estrategia de outra classe (a raiz).
PAPEIS_DE_VEICULO = ("feeder", "espelho")


@dataclass(slots=True)
class Estrutura:
    """O papel de uma classe e o que sustenta a classificacao."""

    cnpj: str
    papel: str
    raiz: str
    alvo: str | None
    fatia_detida: float
    fonte_detida: str
    fatia_cotas: float | None
    fatia_alvo: float | None
    exterior: str | None
    grupo: Grupo
    grupo_estrategia: Grupo
    casa_dedicada: str | None
    espelho_declarado: bool | None
    divergencia: float | None

    @property
    def alerta_de_divergencia(self) -> bool:
        return self.divergencia is not None and self.divergencia > MAX_DIVERGENCIA_PERFIL_CARTEIRA


def _gestor_da_classe(
    cnpj: str, cadastro: Cadastro, mapa: MapaDeFamilias | None
) -> tuple[str | None, str | None]:
    produtos = cadastro.classes.get(cnpj)
    if produtos:
        return produtos[0].gestor_cnpj or None, produtos[0].gestor or None
    classe = mapa.classe(cnpj) if mapa else None
    if classe is not None:
        return classe.cnpj_gestor, classe.gestor
    return None, None


class MapaDeEstrutura:
    """O papel de cada classe do cadastro, calculado sob demanda e memorizado."""

    def __init__(
        self,
        cadastro: Cadastro,
        perfil_fixo: dict[str, PerfilClasse],
        mapa: MapaDeFamilias | None,
        curadoria: Curadoria,
    ) -> None:
        self.cadastro = cadastro
        self.perfil_fixo = perfil_fixo
        self.mapa = mapa
        self.curadoria = curadoria
        self._cache: dict[str, Estrutura] = {}
        self._grupos: dict[str, Grupo] = {}

    @property
    def tem_carteira(self) -> bool:
        return self.mapa is not None

    def grupo_da_classe(self, cnpj: str) -> Grupo:
        if cnpj not in self._grupos:
            self._grupos[cnpj] = self.curadoria.grupo(*_gestor_da_classe(cnpj, self.cadastro, self.mapa))
        return self._grupos[cnpj]

    def raiz(self, cnpj: str) -> str:
        return self.mapa.master_de(cnpj) if self.mapa else cnpj

    def de(self, cnpj: str) -> Estrutura:
        if cnpj not in self._cache:
            self._cache[cnpj] = self._calcular(cnpj)
        return self._cache[cnpj]

    def _calcular(self, cnpj: str) -> Estrutura:
        mapa = self.mapa
        carteira = mapa.carteira(cnpj) if mapa else None
        pl = carteira.pl if carteira and carteira.pl and carteira.pl > 0 else None
        cotas = carteira.cotas if carteira else {}

        fatia_cotas = sum(cotas.values()) / pl if pl else None
        alvo, fatia_alvo = None, None
        if cotas and pl:
            alvo, valor = min(cotas.items(), key=lambda kv: (-kv[1], kv[0]))
            fatia_alvo = valor / pl
        exterior, fatia_exterior = None, 0.0
        if carteira and pl:
            externos = [(k, v) for k, v in carteira.ativos.items() if k.startswith("exterior:")]
            if externos:
                chave, valor = min(externos, key=lambda kv: (-kv[1], kv[0]))
                exterior, fatia_exterior = chave.split(":", 1)[1], valor / pl

        perfil = self.perfil_fixo.get(cnpj)
        detida_perfil = perfil.fundos if perfil is not None and perfil.informado else None
        detida_carteira = None
        if mapa and pl:
            detida_carteira = min(sum(mapa.investidores_de(cnpj).values()) / pl, 1.0)
        if detida_perfil is not None:
            fatia_detida, fonte = detida_perfil, "perfil"
        elif detida_carteira is not None:
            fatia_detida, fonte = detida_carteira, "carteira"
        else:
            fatia_detida, fonte = 0.0, "sem_dado"
        divergencia = (
            abs(detida_perfil - detida_carteira)
            if detida_perfil is not None and detida_carteira is not None else None
        )

        grupo = self.grupo_da_classe(cnpj)
        raiz = cnpj
        if fatia_detida >= MIN_FATIA_FUNDOS_MASTER:
            papel = "master"
        elif fatia_alvo is not None and fatia_alvo >= MIN_FATIA_ALVO_UNICO:
            raiz = self.raiz(cnpj)
            papel = "feeder" if self.grupo_da_classe(raiz).nome == grupo.nome else "espelho"
        elif fatia_exterior >= MIN_FATIA_ALVO_UNICO:
            papel = "feeder_exterior"
        elif fatia_cotas is not None and fatia_cotas >= MIN_FATIA_FOF:
            fora_do_grupo = sum(
                valor for alvo_fof, valor in cotas.items()
                if self.grupo_da_classe(alvo_fof).nome != grupo.nome
            )
            papel = "fof_multigestor" if fora_do_grupo > sum(cotas.values()) / 2 else "fof_casa"
        elif carteira is None:
            papel = "sem_carteira"
        elif fatia_detida >= MIN_FATIA_HIBRIDO:
            papel = "hibrido"
        else:
            papel = "direto"

        grupo_estrategia = self.grupo_da_classe(raiz)
        produtos = self.cadastro.classes.get(cnpj) or []
        espelho_declarado = next(
            (p.espelho_declarado for p in produtos if p.espelho_declarado is not None), None
        )
        return Estrutura(
            cnpj=cnpj,
            papel=papel,
            raiz=raiz,
            alvo=alvo if papel in PAPEIS_DE_VEICULO else None,
            fatia_detida=fatia_detida,
            fonte_detida=fonte,
            fatia_cotas=fatia_cotas,
            fatia_alvo=fatia_alvo,
            exterior=exterior if papel == "feeder_exterior" else None,
            grupo=grupo,
            grupo_estrategia=grupo_estrategia,
            casa_dedicada=self._casa_dedicada(papel, grupo, grupo_estrategia, produtos),
            espelho_declarado=espelho_declarado,
            divergencia=divergencia,
        )

    def _casa_dedicada(
        self, papel: str, grupo: Grupo, grupo_estrategia: Grupo, produtos: list[Produto]
    ) -> str | None:
        if papel not in PAPEIS_DE_VEICULO:
            return None
        if papel == "espelho" and (
            grupo.tipo in TIPOS_DISTRIBUIDORES or grupo.nome in self.curadoria.casas
        ):
            return grupo.nome
        nomes = " ".join(f"{p.nome} {p.nome_subclasse}" for p in produtos[:1])
        for casa in self.curadoria.marca_no_nome(nomes):
            if casa != grupo_estrategia.nome:
                return casa
        return None
