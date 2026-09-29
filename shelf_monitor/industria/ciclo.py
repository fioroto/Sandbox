"""Preparacao de uma execucao: retrato do cadastro e competencia de referencia.

O retrato do cadastro e o que da atributos ao universo (`cadastro.py`). Ele sai
de toda importacao da base de mercado; aqui, a execucao so garante que o mes
corrente tem o seu. Sem rede, o retrato e refeito dos arquivos que ja estao em
`data/market/raw` - a mesma importacao, lendo do disco.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

from shelf_monitor.config import Config
from shelf_monitor.industria.base import competencia_anterior, ultima_competencia_fechada
from shelf_monitor.industria.cotistas import carregar_perfil
from shelf_monitor.market.historico import competencias_com_retrato
from shelf_monitor.market.importer import run_import


def garantir_retrato(
    config: Config, *, baixar: bool = True, force: bool = False, hoje: date | None = None
) -> tuple[str | None, list[str]]:
    """(competencia do retrato mais recente, avisos).

    Importa a base quando o mes corrente ainda nao tem retrato (ou com
    `force`). A importacao usa requisicao condicional: sem mudanca na CVM, sao
    tres respostas 304 e a releitura do que ja esta em disco.
    """
    hoje = hoje or date.today()
    mes = f"{hoje.year:04d}{hoje.month:02d}"
    avisos: list[str] = []
    if force or mes not in competencias_com_retrato(config):
        local = None if baixar else config.market_raw_dir
        if local is not None and not local.is_dir():
            avisos.append("sem rede e sem a base de mercado em disco: rode `market` antes")
        else:
            database, caminhos = run_import(config, local_dir=local, force=force)
            if "historico" not in caminhos:
                avisos.append(
                    "retrato do cadastro não gravado nesta execução: "
                    + "; ".join(database.notes[-2:])
                )
    disponiveis = competencias_com_retrato(config)
    return (disponiveis[-1] if disponiveis else None), avisos


def escolher_competencia(
    config: Config,
    pedida: str | None = None,
    *,
    baixar: bool = True,
    hoje: date | None = None,
) -> tuple[str, str | None]:
    """(competencia AAAAMM, aviso). A pedida vale como veio; sem pedido, a
    ultima fechada que ja tem perfil mensal publicado, recuando um mes se o
    da ultima ainda nao saiu."""
    if pedida:
        return pedida, None
    candidata = ultima_competencia_fechada(hoje)
    perfis, _ = carregar_perfil(config, candidata, revalidar=True, baixar=baixar)
    if perfis:
        return candidata, None
    anterior = competencia_anterior(candidata)
    return anterior, (
        f"perfil mensal de {candidata[4:]}/{candidata[:4]} ainda não publicado: "
        f"usada a competência {anterior[4:]}/{anterior[:4]}"
    )


def publicada(config: Config, competencia: str) -> bool:
    """A competencia ja tem o bloco publicado (a trilha em data/historico/industria)."""
    return (config.historico_dir / "industria" / f"{competencia}.json").is_file()


def carteira_publicada(config: Config, competencia: str, *, baixar: bool = True) -> bool:
    """A CVM ja publicou a carteira (CDA) da competencia? Baixa e resume se sim:
    o ciclo mensal que vem em seguida le o mesmo resumo do cache."""
    from shelf_monitor.market.carteira import carregar_competencia

    resumo, _ = carregar_competencia(config, competencia, baixar=baixar)
    return resumo is not None


def decidir_ciclo(
    config: Config,
    *,
    baixar: bool = True,
    hoje: date | None = None,
    competencia: str | None = None,
    carteira: Callable[[str], bool] | None = None,
) -> tuple[str, str]:
    """("mensal" | "flash" | "nada", o porque), para o `--ciclo auto`.

    Mensal quando a ultima competencia com perfil publicado ainda nao saiu do
    monitor e a carteira dela ja esta na CVM; senao o flash, se o ultimo tem 7
    dias ou mais; senao nada. `competencia` e `carteira` substituem as
    consultas a CVM (testes).
    """
    from shelf_monitor.industria.flash import INTERVALO_DO_FLASH_DIAS, ultimo_flash

    hoje = hoje or date.today()
    if competencia is None:
        competencia, _ = escolher_competencia(config, None, baixar=baixar, hoje=hoje)
    carteira = carteira or (lambda c: carteira_publicada(config, c, baixar=baixar))
    rotulo = f"{competencia[4:]}/{competencia[:4]}"
    if not publicada(config, competencia):
        if carteira(competencia):
            return "mensal", f"{rotulo} ainda não publicada, com perfil e carteira na CVM"
        situacao = f"carteira de {rotulo} ainda não publicada pela CVM"
    else:
        situacao = f"{rotulo} já publicada"
    ultimo = ultimo_flash(config)
    if ultimo is None:
        return "flash", f"{situacao}; nenhum flash anterior"
    dias = (hoje - ultimo).days
    quando = f"último flash em {ultimo:%d/%m/%Y}"
    if dias >= INTERVALO_DO_FLASH_DIAS:
        return "flash", f"{situacao}; {quando}"
    return "nada", f"{situacao}; {quando}, há menos de {INTERVALO_DO_FLASH_DIAS} dias"


def normalizar_competencia(texto: str | None) -> str | None:
    """"2026-08", "08/2026" ou "202608" -> "202608"; None se vazio."""
    if not texto:
        return None
    digitos = "".join(ch for ch in texto if ch.isdigit())
    if len(digitos) != 6:
        raise ValueError(f"competência inválida: {texto!r} (use AAAA-MM)")
    if texto.strip()[2:3] == "/":  # MM/AAAA
        digitos = digitos[2:] + digitos[:2]
    ano, mes = int(digitos[:4]), int(digitos[4:])
    if not 1 <= mes <= 12 or ano < 2000:
        raise ValueError(f"competência inválida: {texto!r} (use AAAA-MM)")
    return f"{ano:04d}{mes:02d}"
