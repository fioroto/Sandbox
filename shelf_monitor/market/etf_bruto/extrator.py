"""Orquestrador do `etf-bruto`: roda as fontes na ordem, grava o manifesto,
refaz o universo e as planilhas manuais e mede a cobertura.

Uma fonte que falha nao derruba as outras (mesma regra do `etf
--collect-sources`); o codigo de saida so e 1 quando todas as fontes pedidas
falharam.
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market.etf_bruto import (
    armazem,
    b3_arquivos,
    cobertura,
    comparacao,
    consolidacao,
    comdinheiro,
    composicao,
    fnet,
    indices_b3,
    manual,
    regulamento,
    universo,
)
from shelf_monitor.market.etf_bruto.armazem import Manifesto
from shelf_monitor.market.etf_bruto.contexto import ERRO, Contexto, FabricaDeSessao, Pedido, ResumoFonte
from shelf_monitor.market.etf_bruto.mapa import ORIGENS
from shelf_monitor.report import etf_comparacao
from shelf_monitor.report.etf_consolidado import escrever_csv, escrever_planilha
from shelf_monitor.report.shared.fontes import FONTES_ETF_BRUTO
from shelf_monitor.texto import contagem, data_br

# Ordem de execucao: o universo (B3 fundos, CVM, cadastro de instrumentos)
# antes do que precisa dele para filtrar e casar nomes.
ADAPTADORES: dict[str, tuple[str, Callable[[Contexto], ResumoFonte]]] = {
    "b3-fundos": ("B3, fundos listados", universo.capturar_b3_fundos),
    "cvm-cadastro": ("CVM, cadastro de classes", universo.capturar_cvm_cadastro),
    "b3-arquivos": ("B3, portal de arquivos (instrumentos e negócios)", b3_arquivos.capturar),
    "fnet-informe": ("FNET, informe diário", fnet.capturar_informes),
    "fnet-mensal": ("FNET, fechamentos mensais", fnet.capturar_fechamentos),
    "fnet-documentos": ("FNET, regulamentos e demonstrações", fnet.capturar_documentos),
    "regulamento": ("Regulamento, texto e sugestões", regulamento.extrair),
    "cvm-cda": ("CVM, carteira mensal (CDA)", composicao.capturar_cda),
    "indices-b3": ("B3, índices", indices_b3.capturar),
    "gestoras": ("Gestoras, arquivo de carteira", composicao.capturar_gestoras),
    "comdinheiro": ("Comdinheiro", comdinheiro.capturar),
    "cotahist": ("B3, COTAHIST anual", b3_arquivos.capturar_cotahist),
}
# So com a fonte pedida pelo nome: ~90 MB por ano, e a serie curta ja vem dos negocios.
OPCIONAIS = ("cotahist",)
PADRAO = tuple(fonte for fonte in ADAPTADORES if fonte not in OPCIONAIS)
# Depois destas o universo e refeito: sao elas que dizem quem e ETF.
_REFAZ_UNIVERSO = ("b3-fundos", "b3-arquivos")


def ler_fontes(texto: str | None) -> tuple[tuple[str, ...], frozenset[str], list[str]]:
    """(fontes na ordem de execucao, fontes nomeadas, desconhecidas)."""
    if not texto:
        return PADRAO, frozenset(), []
    pedidas = [parte.strip().lower() for parte in texto.split(",") if parte.strip()]
    desconhecidas = [fonte for fonte in pedidas if fonte not in ADAPTADORES]
    nomeadas = frozenset(fonte for fonte in pedidas if fonte in ADAPTADORES)
    return tuple(fonte for fonte in ADAPTADORES if fonte in nomeadas), nomeadas, desconhecidas


def _refazer_universo(ctx: Contexto) -> None:
    linhas = universo.montar_universo(ctx.config, ctx.catalogo, ctx.hoje)
    if linhas:
        universo.gravar_universo(ctx.config, linhas)
        ctx.universo = linhas


def extrair(
    config: Config,
    pedido: Pedido,
    *,
    hoje: date | None = None,
    fabrica_sessao: FabricaDeSessao | None = None,
    console: Console | None = None,
    catalogo: dict | None = None,
    adaptadores: dict[str, tuple[str, Callable[[Contexto], ResumoFonte]]] | None = None,
) -> tuple[dict[str, ResumoFonte], Contexto]:
    """Roda as fontes do pedido e devolve o resumo de cada uma e o contexto final."""
    hoje = hoje or date.today()
    console = console or Console()
    adaptadores = adaptadores or ADAPTADORES
    manifesto = Manifesto(
        inicio=datetime.now(),
        pedido={"fontes": list(pedido.fontes), "dia": pedido.dia.isoformat(),
                "desde": pedido.desde.isoformat() if pedido.desde else None, "forcar": pedido.forcar},
        base=config.data_dir,
    )
    ctx = Contexto(config, pedido, hoje, manifesto, fabrica_sessao=fabrica_sessao, console=console)
    if catalogo is not None:
        ctx.catalogo = catalogo
    ctx.universo = universo.ler_universo(config)

    resumos: dict[str, ResumoFonte] = {}
    for fonte in pedido.fontes:
        rotulo, adaptador = adaptadores[fonte]
        console.print(f"[bold]{rotulo}[/] [dim]({fonte})[/]")
        inicio = time.monotonic()
        try:
            resumo = adaptador(ctx)
        except SourceUnavailable as exc:
            resumo = ResumoFonte(ERRO, str(exc)[:200])
        except Exception as exc:  # noqa: BLE001 - uma fonte nao derruba as outras
            resumo = ResumoFonte(ERRO, f"{type(exc).__name__}: {str(exc)[:200]}")
        resumos[fonte] = resumo
        manifesto.registrar_fonte(fonte, resumo.estado, resumo.detalhe, janela=resumo.janela,
                                  arquivos=resumo.arquivos, duracao_s=round(time.monotonic() - inicio, 1))
        _imprimir_resumo(console, resumo)
        if fonte in _REFAZ_UNIVERSO and not resumo.falhou:
            try:
                _refazer_universo(ctx)
            except Exception as exc:  # noqa: BLE001 - o universo antigo segue valendo
                console.print(f"  [yellow]Universo não refeito:[/] {type(exc).__name__}: {str(exc)[:160]}")

    if ctx.universo:
        for resultado in manual.atualizar_modelos(config, ctx.universo):
            estilo = "yellow" if resultado.travada else "dim"
            console.print(f"  [{estilo}]manual/{resultado.texto()}[/]")
    caminho = manifesto.gravar(config.etf_bruto_dir / "_execucoes")
    console.print(f"[dim]Manifesto: {caminho}[/]")
    return resumos, ctx


def _imprimir_resumo(console: Console, resumo: ResumoFonte) -> None:
    cor = {"ok": "green", "em disco": "dim", "erro": "red"}.get(resumo.estado, "yellow")
    janela = f" [dim]- janela {resumo.janela}[/]" if resumo.janela else ""
    console.print(f"  [{cor}]{resumo.estado}[/]: {resumo.detalhe}{janela}")


def imprimir_fontes(console: Console) -> None:
    console.print("[bold]Fontes consultadas[/]")
    for fonte in FONTES_ETF_BRUTO:
        console.print(f"  {fonte.nome}: {fonte.url}")


def imprimir_resumo_final(console: Console, resumos: dict[str, ResumoFonte]) -> None:
    tabela = Table(title="Captura bruta de ETF", title_justify="left")
    tabela.add_column("Fonte")
    tabela.add_column("Estado")
    tabela.add_column("Arquivos", justify="right")
    tabela.add_column("Detalhe")
    for fonte, resumo in resumos.items():
        cor = {"ok": "green", "em disco": "dim", "erro": "red"}.get(resumo.estado, "yellow")
        tabela.add_row(fonte, f"[{cor}]{resumo.estado}[/]", str(resumo.arquivos), resumo.detalhe[:110])
    console.print(tabela)


def listar_fontes(config: Config, console: Console) -> None:
    """As fontes do mapeamento, como cada uma e capturada e o que ha em disco (sem rede)."""
    tabela = Table(title="Fontes do mapeamento de ETF", title_justify="left", show_lines=True)
    tabela.add_column("Fonte")
    tabela.add_column("O que dá")
    tabela.add_column("Como")
    tabela.add_column("Captura")
    for origem in ORIGENS.values():
        if origem.chave in ("derivado", "nome", "fora"):
            continue
        captura = ", ".join(origem.adaptadores) or "—"
        if origem.planilha:
            captura += f" | planilha {origem.planilha}"
        nome = origem.nome + (" [dim](extra)[/]" if origem.extra else "")
        tabela.add_row(nome, origem.oferece, origem.como, captura)
    console.print(tabela)
    console.print(
        f"[dim]Padrão: {', '.join(PADRAO)}. Só quando pedida pelo nome: {', '.join(OPCIONAIS)}.[/]"
    )
    bruto = config.etf_bruto_dir
    if bruto.exists():
        console.print("[bold]Em disco[/]")
        for pasta in sorted(p for p in bruto.iterdir() if p.is_dir() and not p.name.startswith("_")):
            arquivos = [p for p in pasta.rglob("*") if p.is_file()]
            tamanho = sum(p.stat().st_size for p in arquivos) / (1024 * 1024)
            console.print(f"  {pasta.name}: {contagem(len(arquivos), 'arquivo', 'arquivos')}, "
                          f"{tamanho:.1f} MB".replace(".", ","))


def mostrar_cobertura(config: Config, console: Console, universo_linhas: list[dict], hoje: date) -> None:
    if not universo_linhas:
        console.print("[yellow]Cobertura indisponível:[/] sem data/etf/universo.csv - rode a captura uma vez.")
        return
    linhas = cobertura.medir(config, universo_linhas, hoje)
    caminho = cobertura.gravar(config, linhas, hoje)
    cobertura.imprimir(linhas, console)
    console.print(f"[dim]Cobertura em {caminho} (medida em {data_br(hoje)}).[/]")


def consolidar_e_gravar(config: Config, console: Console, hoje: date) -> tuple:
    """A planilha unica (uma linha por ETF) e o CSV com as mesmas colunas, a partir do disco."""
    resultado = consolidacao.consolidar(config, hoje)
    if not resultado.linhas:
        console.print("[yellow]Consolidação indisponível:[/] sem data/etf/universo.csv - rode a captura uma vez.")
        return None, None
    planilha = escrever_planilha(resultado, config.reports_dir / f"etf-consolidado-{hoje.isoformat()}.xlsx")
    tabela = escrever_csv(resultado, config.reports_dir / "etf_consolidado.csv")
    pendentes = sum(1 for linha in resultado.linhas for valor in linha.values() if valor == consolidacao.PENDENTE_PDF)
    negociam = sum(1 for linha in resultado.linhas if linha.get("situacao") == "negocia na B3")
    console.print(
        f"[bold]Consolidação[/]: {contagem(len(resultado.linhas), 'ETF', 'ETFs')} ({negociam} negociando na B3), "
        f"{contagem(len(resultado.colunas), 'coluna', 'colunas')}; "
        f"{contagem(pendentes, 'célula', 'células')} com \"{consolidacao.PENDENTE_PDF}\""
    )
    console.print(f"  Planilha: {planilha}")
    console.print(f"  CSV:      {tabela}")
    return planilha, tabela


def comparar_com_comdinheiro(config: Config, console: Console, caminhos: list[str], hoje: date):
    """Compara a captura com exportacoes da Comdinheiro e grava a planilha da comparacao.

    As exportacoes vao tambem para data/etf/entrada/comdinheiro/, de onde a
    proxima captura as registra na area bruta (uma vez por conteudo).
    """
    arquivos = [Path(c) for c in caminhos]
    faltando = [str(a) for a in arquivos if not a.exists()]
    if faltando:
        console.print(f"[red]Arquivo não encontrado:[/] {', '.join(faltando)}")
        return None
    exportacao = comparacao.ler_exportacoes(arquivos)
    resultado = comparacao.comparar(config, exportacao, hoje)
    data = (exportacao.data_analise or hoje).isoformat()
    planilha = etf_comparacao.escrever(resultado, config.reports_dir / f"etf-comparacao-comdinheiro-{data}.xlsx")
    for arquivo in arquivos:
        destino = config.etf_entrada_dir / "comdinheiro" / arquivo.name
        if not destino.exists():
            armazem.gravar_atomico(destino, arquivo.read_bytes())

    analise = data_br(exportacao.data_analise) if exportacao.data_analise else "-"
    console.print(f"[bold]Captura x Comdinheiro[/] - análise da Comdinheiro em {analise}; "
                  f"{resultado.em_ambos} ETFs nos dois lados, {len(resultado.so_comdinheiro)} só na Comdinheiro, "
                  f"{len(resultado.so_captura)} só na captura")
    tabela = Table(title_justify="left")
    for titulo in ("Campo", "Comparáveis", "Iguais", "Próximos", "Diferentes", "Só Comdinheiro", "Só captura"):
        tabela.add_column(titulo, justify="left" if titulo == "Campo" else "right")
    for linha in comparacao.resumo(resultado):
        tabela.add_row(linha["campo"], str(linha["comparaveis"]), str(linha[comparacao.IGUAL]),
                       str(linha[comparacao.PROXIMO]), str(linha[comparacao.DIFERENTE]),
                       str(linha[comparacao.SO_COMDINHEIRO]), str(linha[comparacao.SO_CAPTURA]))
    console.print(tabela)
    console.print(f"  Planilha: {planilha}")
    return planilha


# --- linha de comando --------------------------------------------------------------------


def _data(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"data inválida: {texto} (use AAAA-MM-DD)") from exc


def registrar_subcomando(subparsers, common: argparse.ArgumentParser) -> None:
    parser = subparsers.add_parser(
        "etf-bruto",
        help="Capturar os dados brutos de ETF de cada fonte do mapeamento (B3, FNET, CVM, índices, Comdinheiro)",
        parents=[common],
    )
    parser.add_argument(
        "--fontes",
        metavar="LISTA",
        help=(
            "Fontes separadas por vírgula. Padrão: " + ", ".join(PADRAO)
            + ". Só quando pedida: " + ", ".join(OPCIONAIS)
        ),
    )
    parser.add_argument("--dia", type=_data, help="Dia de referência, AAAA-MM-DD (padrão: hoje)")
    parser.add_argument(
        "--desde",
        type=_data,
        help=(
            "Início da janela de histórico, AAAA-MM-DD (padrão: 13 meses de negócios da B3; o informe "
            "diário do FNET só volta tudo isso quando a fonte é pedida pelo nome)"
        ),
    )
    parser.add_argument(
        "--forcar",
        action="store_true",
        help="Capturar de novo o que já está em disco (colunas preenchidas por gente nunca são tocadas)",
    )
    parser.add_argument(
        "--cobertura", action="store_true",
        help="Só medir a cobertura do que já está em disco, sem acessar a rede",
    )
    parser.add_argument(
        "--listar-fontes", action="store_true",
        help="Listar as fontes do mapeamento, como cada uma é capturada e o que há em disco, sem rede",
    )
    parser.add_argument(
        "--comparar-comdinheiro", nargs="+", metavar="ARQUIVO",
        help=(
            "Comparar a captura, campo a campo, com exportações da Comdinheiro (xlsx cadastral e de mercado), "
            "sem rede; gera a planilha da comparação"
        ),
    )
    parser.add_argument(
        "--consolidar", action="store_true",
        help=(
            "Só gerar a planilha consolidada (uma linha por ETF) a partir do que está em disco, sem rede; "
            "a captura já a gera no fim"
        ),
    )
    parser.set_defaults(func=cmd_etf_bruto)


def cmd_etf_bruto(config: Config, args: argparse.Namespace) -> int | None:
    console = Console()
    config.ensure_dirs()
    hoje = date.today()
    if args.listar_fontes:
        listar_fontes(config, console)
        return None
    if args.cobertura:
        mostrar_cobertura(config, console, universo.ler_universo(config), hoje)
        imprimir_fontes(console)
        return None
    if getattr(args, "comparar_comdinheiro", None):
        comparar_com_comdinheiro(config, console, args.comparar_comdinheiro, hoje)
        imprimir_fontes(console)
        return None
    if getattr(args, "consolidar", False):
        consolidar_e_gravar(config, console, hoje)
        imprimir_fontes(console)
        return None

    fontes, nomeadas, desconhecidas = ler_fontes(args.fontes)
    if desconhecidas:
        console.print(f"[red]Fonte desconhecida:[/] {', '.join(desconhecidas)}. "
                      f"Use: {', '.join(ADAPTADORES)}.")
        return 2
    dia = min(args.dia or hoje, hoje)
    pedido = Pedido(fontes=fontes, dia=dia, desde=args.desde, forcar=args.forcar, explicitas=nomeadas)
    console.print(f"[bold]Captura bruta de ETF[/] - dia {data_br(dia)}"
                  + (f", histórico desde {data_br(args.desde)}" if args.desde else ""))
    resumos, ctx = extrair(config, pedido, hoje=hoje, console=console)
    console.print()
    imprimir_resumo_final(console, resumos)
    mostrar_cobertura(config, console, ctx.universo, hoje)
    consolidar_e_gravar(config, console, hoje)
    imprimir_fontes(console)
    if resumos and all(resumo.falhou for resumo in resumos.values()):
        return 1
    return None
