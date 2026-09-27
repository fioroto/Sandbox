"""FNET (Fundos.NET): informe diario bruto, fechamentos mensais e documentos.

Informe diario: o XML de cada documento vai para um zip por data de
referencia e cada tag vira coluna de um CSV por data, com o nome original
(PATRIM_LIQ, VL_QUOTA, CAPTC_DIA, RESG_DIA, NR_COTST, VL_TOTAL_ATV,
DATA_PROX_PL...). O parser do `etf` fica com meia duzia de campos e perde o
resto; aqui nada se perde.

Documentos: o indice de regulamentos (categoria 5), demonstracoes financeiras
(tipo 30) e relatorios anuais (tipo 52), com o link de cada um, e o PDF dos
tres regulamentos mais recentes de cada ETF. Medido em 2026-09-24:
- a busca devolve `cnpjFundo` nulo, mas o nome do arquivo no Content-Disposition
  comeca pelo CNPJ ("69131602000184-REG17092026V01-001322547.pdf");
- filtrar a busca por `cnpjFundo` estoura o tempo (60-90 s) e `cnpj` e
  ignorado: lista-se a categoria inteira (669 regulamentos em tipoFundo 3, 4
  paginas) e casa-se pelo nome;
- a primeira pagina de uma busca pode levar 60 s: a sessao daqui tem timeout
  minimo de 180 s e nova tentativa.
"""
from __future__ import annotations

import copy
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.market import etf_sources, fundosnet
from shelf_monitor.market.etf_bruto import armazem
from shelf_monitor.market.etf_bruto.contexto import EM_DISCO, OK, PARCIAL, Contexto, ResumoFonte
from shelf_monitor.normalize import name_slug, normalize_cnpj, normalize_text
from shelf_monitor.texto import contagem, data_br, milhar, periodo_br

FONTE_INFORME = "fnet-informe"
FONTE_MENSAL = "fnet-mensal"
FONTE_DOCUMENTOS = "fnet-documentos"

CATEGORIA_REGULAMENTO = 5
TIPO_DEMONSTRACOES = 30
TIPO_RELATORIO_ANUAL = 52
# (categoria, tipo de documento, rotulo) de cada documento indexado.
DOCUMENTOS = (
    (CATEGORIA_REGULAMENTO, 0, "Regulamento"),
    (fundosnet.CATEGORY_INFORMES_PERIODICOS, TIPO_DEMONSTRACOES, "Demonstrações Financeiras"),
    (fundosnet.CATEGORY_INFORMES_PERIODICOS, TIPO_RELATORIO_ANUAL, "Relatório Anual"),
)
# Regulamentos baixados por fundo, do mais recente para tras. Um so nao basta:
# em 2026-09-24 o de 05/2026 do GPUS11 era so a parte geral (sem a taxa), que
# estava no de 05/2025.
REGULAMENTOS_POR_FUNDO = 3
# Informes entregues nos ultimos dias: o do dia util anterior sai em D+1, e a
# folga cobre fim de semana e reapresentacao.
JANELA_INFORMES_DIAS = 5
TIMEOUT_MINIMO_FNET = 180

COLUNAS_DOCUMENTO = (
    "doc_id", "fundo", "nome_pregao", "data_referencia", "data_entrega", "versao", "situacao",
    "status", "cnpj_arquivo", "capturado_em",
)
COLUNAS_INDICE = (
    "categoria", "tipo", "doc_id", "fundo", "nome_pregao", "data_referencia", "data_entrega",
    "versao", "situacao", "status", "link", "chave_nome", "cnpj_catalogo", "cnpj_arquivo", "arquivo",
)

_NOME_ARQUIVO = re.compile(r"""filename\*?=(?:UTF-8'')?["']?([^"';]+)""", re.I)


def pasta_informe(config: Config) -> Path:
    return config.etf_bruto_dir / "fnet_informe"


def pasta_documentos(config: Config) -> Path:
    return config.etf_bruto_dir / "fnet_documentos"


def pasta_regulamentos(config: Config) -> Path:
    return pasta_documentos(config) / "regulamento"


def cnpj_do_content_disposition(cabecalho: str | None) -> str | None:
    """"attachment; filename="69131602000184-REG...pdf"" -> "69131602000184"."""
    achado = _NOME_ARQUIVO.search(cabecalho or "")
    if not achado:
        return None
    prefixo = re.match(r"(\d{14})\D", achado.group(1).strip())
    return normalize_cnpj(prefixo.group(1)) if prefixo else None


def _cliente(ctx: Contexto) -> fundosnet.FundosNetClient:
    cliente = fundosnet.FundosNetClient(ctx.config)
    cliente.session = ctx.sessao("fnet", intervalo=0.3, timeout_minimo=TIMEOUT_MINIMO_FNET)
    # A pagina principal so serve para o token CSRF, que o servidor nao exige
    # (medido em 2026-09-19); pula-la economiza a requisicao mais lenta do FNET
    # (30 s ou mais em 2026-09-24).
    cliente._main_page = ""
    return cliente


# --- XML do informe ------------------------------------------------------------


def _tag(elemento: ET.Element) -> str:
    return elemento.tag.rsplit("}", 1)[-1]


def _folhas(elemento: ET.Element) -> dict[str, str]:
    return {_tag(filho): (filho.text or "").strip() for filho in elemento if len(filho) == 0}


def _conteudo(elemento: ET.Element) -> object:
    """Lista aninhada (LISTA_COTST_SIGNIF) como estrutura JSON-serializavel."""
    if len(elemento) == 0:
        return (elemento.text or "").strip()
    return [
        {_tag(neto): _conteudo(neto) for neto in filho} if len(filho) else (filho.text or "").strip()
        for filho in elemento
    ]


def achatar_xml(conteudo: bytes) -> list[dict]:
    """Uma linha por INFORM, com as folhas do cabecalho repetidas; tags originais.

    Lista aninhada vira JSON na coluna (LISTA_COTST_SIGNIF). XML quebrado
    devolve lista vazia - o documento continua guardado no zip.
    """
    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError:
        return []
    informes = [e for e in raiz.iter() if _tag(e) == "INFORM"]
    cabecalho: dict[str, str] = {}
    for elemento in raiz.iter():
        if _tag(elemento) == "CAB_INFORM":
            cabecalho.update(_folhas(elemento))
    if not informes:
        linha = dict(cabecalho)
        linha.update({_tag(e): (e.text or "").strip() for e in raiz.iter() if len(e) == 0 and e is not raiz})
        return [linha] if linha else []
    linhas = []
    for informe in informes:
        linha = dict(cabecalho)
        for filho in informe:
            if len(filho) == 0:
                linha[_tag(filho)] = (filho.text or "").strip()
            else:
                linha[_tag(filho)] = json.dumps(_conteudo(filho), ensure_ascii=False)
        linhas.append(linha)
    return linhas


def _data_do_informe(linha: dict, doc: fundosnet.DocumentoFnet) -> date | None:
    for chave in ("DT_COMPT", "DT_COMPTC", "DT_REF"):
        texto = (linha.get(chave) or "").strip()
        for formato in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(texto[:10], formato).date()
            except ValueError:
                continue
    return doc.data_referencia


# --- informe diario --------------------------------------------------------------


def _ler_documentos(config: Config) -> dict[str, dict]:
    return {linha["doc_id"]: linha for linha in armazem.ler_csv(pasta_informe(config) / "documentos.csv")
            if linha.get("doc_id")}


def _juntar_no_zip(caminho: Path, novos: dict[str, bytes]) -> str:
    """Reescreve o zip do dia com os membros antigos e os novos (atomico)."""
    membros: dict[str, bytes] = {}
    if caminho.exists():
        with zipfile.ZipFile(caminho) as antigo:
            membros = {nome: antigo.read(nome) for nome in antigo.namelist()}
    membros.update(novos)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        for nome in sorted(membros):
            arquivo.writestr(nome, membros[nome])
    return armazem.gravar_atomico(caminho, buffer.getvalue())


def _juntar_no_csv(caminho: Path, novas: list[dict]) -> tuple[str, int]:
    """Soma linhas novas ao CSV do dia, unindo as colunas (a CVM ja acrescentou tag)."""
    linhas = armazem.ler_csv(caminho) + novas
    colunas: list[str] = []
    for linha in linhas:
        for coluna in linha:
            if coluna not in colunas:
                colunas.append(coluna)
    linhas.sort(key=lambda l: (l.get("CNPJ_FDO") or "", l.get("doc_id") or ""))
    return armazem.gravar_csv(caminho, colunas, linhas)


def _janelas(inicio: date, fim: date) -> list[tuple[date, date]]:
    """Janelas mensais de busca, da mais recente para a mais antiga."""
    janelas = []
    ate = fim
    while ate >= inicio:
        de = max(inicio, ate.replace(day=1))
        janelas.append((de, ate))
        ate = de - timedelta(days=1)
    return janelas


def capturar_informes(ctx: Contexto) -> ResumoFonte:
    """Informes diarios entregues na janela, um XML por documento, sem descartar tag.

    Sem `--desde` (ou com ele, mas sem pedir a fonte pelo nome): os ultimos
    JANELA_INFORMES_DIAS dias de entrega. Com `--desde` e a fonte pedida: a
    janela inteira, mes a mes - um ano sao ~55 mil documentos, horas de
    download; retomavel, porque documento ja baixado nao e baixado de novo.
    """
    fim = min(ctx.pedido.dia, ctx.hoje)
    inicio = fim - timedelta(days=JANELA_INFORMES_DIAS)
    if ctx.pedido.desde and ctx.explicita(FONTE_INFORME):
        inicio = ctx.pedido.desde
    conhecidos = _ler_documentos(ctx.config)
    cliente = _cliente(ctx)
    pasta = pasta_informe(ctx.config)

    baixados = falhas = 0
    datas: set[date] = set()
    for de, ate in _janelas(inicio, fim):
        por_data: dict[date, tuple[dict[str, bytes], list[dict]]] = {}
        novos_documentos: list[dict] = []
        for tipo in fundosnet.ETF_FUND_TYPES.values():
            for doc in cliente.search(tipo, doc_type_id=fundosnet.DOC_TYPE_INFORME_DIARIO, start=de, end=ate):
                if str(doc.id) in conhecidos:
                    continue
                try:
                    conteudo, cabecalhos = cliente.download_with_headers(doc.id)
                except SourceUnavailable as exc:
                    falhas += 1
                    ctx.manifesto.ocorrencia(FONTE_INFORME, f"doc {doc.id}", str(exc)[:200])
                    continue
                baixados += 1
                cnpj_arquivo = cnpj_do_content_disposition(cabecalhos.get("Content-Disposition"))
                meta = {
                    "doc_id": str(doc.id), "fundo": doc.fundo, "nome_pregao": doc.nome_pregao or "",
                    "data_referencia": doc.data_referencia.isoformat() if doc.data_referencia else "",
                    "data_entrega": doc.data_entrega.isoformat(timespec="minutes") if doc.data_entrega else "",
                    "versao": "" if doc.versao is None else str(doc.versao),
                    "situacao": doc.situacao or "", "status": doc.status or "",
                    "cnpj_arquivo": cnpj_arquivo or "",
                    "capturado_em": datetime.now().isoformat(timespec="seconds"),
                }
                linhas = achatar_xml(conteudo)
                referencia = _data_do_informe(linhas[0], doc) if linhas else doc.data_referencia
                referencia = referencia or (doc.data_entrega.date() if doc.data_entrega else ate)
                xmls, csv_linhas = por_data.setdefault(referencia, ({}, []))
                xmls[f"{doc.id}.xml"] = conteudo
                for linha in linhas:
                    csv_linhas.append({**{k: meta[k] for k in COLUNAS_DOCUMENTO}, **linha})
                novos_documentos.append(meta)
                conhecidos[str(doc.id)] = meta
        # Grava a cada janela: um backfill interrompido guarda o que ja veio.
        for referencia, (xmls, csv_linhas) in sorted(por_data.items()):
            zip_path = pasta / "xml" / f"{referencia.isoformat()}.zip"
            sha_zip = _juntar_no_zip(zip_path, xmls)
            ctx.manifesto.arquivo(FONTE_INFORME, zip_path, sha256=sha_zip, linhas=len(xmls),
                                  url=f"{fundosnet.BASE_URL}downloadDocumento")
            if csv_linhas:
                csv_path = pasta / f"{referencia.isoformat()}.csv"
                sha, total = _juntar_no_csv(csv_path, csv_linhas)
                ctx.manifesto.arquivo(FONTE_INFORME, csv_path, sha256=sha, linhas=total)
            datas.add(referencia)
        if novos_documentos:
            armazem.gravar_csv(pasta / "documentos.csv", COLUNAS_DOCUMENTO,
                               sorted(conhecidos.values(), key=lambda l: int(l["doc_id"])))

    janela = periodo_br(inicio, fim)
    if not baixados and not falhas:
        return ResumoFonte(EM_DISCO, f"nenhum informe novo entregue {janela}", janela=janela)
    detalhe = (f"{contagem(baixados, 'informe novo', 'informes novos')}, "
               f"{contagem(len(datas), 'data de referência', 'datas de referência')}")
    if datas:
        detalhe += f" (de {data_br(min(datas))} a {data_br(max(datas))})"
    if falhas:
        return ResumoFonte(PARCIAL, f"{detalhe}; {contagem(falhas, 'download falhou', 'downloads falharam')}",
                           janela=janela, arquivos=len(datas))
    return ResumoFonte(OK, detalhe, janela=janela, arquivos=len(datas))


# --- fechamentos mensais -----------------------------------------------------------


def capturar_fechamentos(ctx: Contexto) -> ResumoFonte:
    """O ultimo informe de cada ETF em cada mes - delega ao coletor do `etf`,
    que grava `data/etf/fnet_mensal/AAAAMM.csv` e nao rebaixa mes em disco."""
    config = copy.copy(ctx.config)
    config.http_timeout = max(config.http_timeout, TIMEOUT_MINIMO_FNET)
    resumo = etf_sources.collect_fechamentos_mensais(config, ctx.catalogo, ctx.hoje, force=ctx.pedido.forcar)
    meses = resumo.get("meses") or {}
    novos = {mes: texto for mes, texto in meses.items() if texto != "em disco"}
    if not novos:
        return ResumoFonte(EM_DISCO, f"{contagem(len(meses), 'mês', 'meses')} em disco")
    detalhe = ", ".join(f"{mes}: {texto}" for mes, texto in sorted(novos.items()))
    return ResumoFonte(OK, f"{detalhe} ({contagem(resumo.get('documentos', 0), 'documento', 'documentos')})",
                       arquivos=len(novos))


# --- documentos (regulamento, demonstracoes, relatorio anual) -------------------------


class _Nomes:
    """Nome de fundo -> CNPJ do ETF: pelo nome inteiro normalizado e, na falta,
    pelo `name_slug` - so quando o slug e de um ETF so.

    O slug descarta palavras genericas, e duas delas bastam para juntar fundos
    diferentes: em 2026-09-24 "NU IBOV SMART DIVIDENDOS" e "NU RENDA IBOV SMART
    DIVIDENDOS" tinham o mesmo slug, e o regulamento de um tomava o lugar do outro.
    """

    def __init__(self, ctx: Contexto):
        self.por_nome: dict[str, str] = {}
        slugs: dict[str, set[str]] = {}
        pares = [(meta.name, cnpj) for cnpj, meta in ctx.catalogo.items() if getattr(meta, "is_etf", False)]
        pares += [(linha.get("nome"), linha.get("cnpj")) for linha in ctx.universo]
        for nome, cnpj in pares:
            if not nome or not cnpj:
                continue
            self.por_nome.setdefault(normalize_text(nome), cnpj)
            slugs.setdefault(name_slug(nome), set()).add(cnpj)
        self.por_slug = {slug: next(iter(cnpjs)) for slug, cnpjs in slugs.items() if slug and len(cnpjs) == 1}

    def cnpj(self, nome: str | None) -> str:
        return self.por_nome.get(normalize_text(nome)) or self.por_slug.get(name_slug(nome), "")


def _parece_etf(nome: str) -> bool:
    texto = normalize_text(nome)
    return " INDICE" in f" {texto}" or " ETF" in f" {texto}"


def _regulamentos_em_disco(config: Config) -> dict[str, Path]:
    """doc_id -> PDF ja baixado."""
    pasta = pasta_regulamentos(config)
    if not pasta.exists():
        return {}
    return {caminho.stem: caminho for caminho in pasta.glob("*/*.pdf")}


def capturar_documentos(ctx: Contexto) -> ResumoFonte:
    """Indice de regulamentos, demonstracoes e relatorios anuais dos ETFs, e o
    PDF do regulamento mais recente de cada um."""
    cliente = _cliente(ctx)
    nomes = _Nomes(ctx)
    em_disco = _regulamentos_em_disco(ctx.config)
    indice: list[dict] = []
    for categoria, tipo_doc, rotulo in DOCUMENTOS:
        for tipo_fundo in fundosnet.ETF_FUND_TYPES.values():
            for doc in cliente.search(tipo_fundo, doc_type_id=tipo_doc, category_id=categoria):
                indice.append({
                    "categoria": rotulo, "tipo": doc.tipo or "", "doc_id": str(doc.id), "fundo": doc.fundo,
                    "nome_pregao": doc.nome_pregao or "",
                    "data_referencia": doc.data_referencia.isoformat() if doc.data_referencia else "",
                    "data_entrega": doc.data_entrega.isoformat(timespec="minutes") if doc.data_entrega else "",
                    "versao": "" if doc.versao is None else str(doc.versao),
                    "situacao": doc.situacao or "", "status": doc.status or "", "link": doc.url,
                    "chave_nome": normalize_text(doc.fundo), "cnpj_catalogo": nomes.cnpj(doc.fundo),
                })

    # Os regulamentos mais recentes de cada fundo - pelo nome inteiro, porque a
    # busca nao traz CNPJ. Fundo renomeado vira dois grupos; o CNPJ do arquivo
    # junta os dois depois. Ativo antes de inativo, depois versao e id.
    por_fundo: dict[str, list[dict]] = {}
    for item in indice:
        if item["categoria"] != "Regulamento":
            continue
        if not (item["cnpj_catalogo"] or _parece_etf(item["fundo"])):
            continue
        por_fundo.setdefault(item["chave_nome"], []).append(item)
    selecionados = [
        item for lista in por_fundo.values()
        for item in sorted(lista, key=_ordem, reverse=True)[:REGULAMENTOS_POR_FUNDO]
    ]

    baixados = falhas = 0
    recusados = 0
    for item in selecionados:
        doc_id = item["doc_id"]
        if doc_id in em_disco and not ctx.pedido.forcar:
            continue
        try:
            conteudo, cabecalhos = cliente.download_with_headers(int(doc_id))
        except SourceUnavailable as exc:
            falhas += 1
            ctx.manifesto.ocorrencia(FONTE_DOCUMENTOS, f"regulamento {doc_id}", str(exc)[:200])
            continue
        if not conteudo.startswith(b"%PDF"):
            recusados += 1
            ctx.manifesto.ocorrencia(FONTE_DOCUMENTOS, f"regulamento {doc_id}",
                                     f"não é PDF ({cabecalhos.get('Content-Type')})")
            continue
        cnpj = cnpj_do_content_disposition(cabecalhos.get("Content-Disposition")) or item["cnpj_catalogo"]
        destino = pasta_regulamentos(ctx.config) / (cnpj or "_sem_cnpj") / f"{doc_id}.pdf"
        sha = armazem.gravar_atomico(destino, conteudo)
        em_disco[doc_id] = destino
        baixados += 1
        ctx.manifesto.arquivo(FONTE_DOCUMENTOS, destino, url=item["link"], sha256=sha, bytes=len(conteudo),
                              referencia=item["fundo"])

    for item in indice:
        caminho = em_disco.get(item["doc_id"])
        item["arquivo"] = caminho.relative_to(ctx.config.etf_bruto_dir).as_posix() if caminho else ""
        item["cnpj_arquivo"] = caminho.parent.name if caminho and caminho.parent.name.isdigit() else ""
    indice_path = pasta_documentos(ctx.config) / "indice.csv"
    sha, total = armazem.gravar_csv(indice_path, COLUNAS_INDICE, indice)
    ctx.manifesto.arquivo(FONTE_DOCUMENTOS, indice_path, sha256=sha, linhas=total)

    contagens = {rotulo: sum(1 for i in indice if i["categoria"] == rotulo) for _, _, rotulo in DOCUMENTOS}
    detalhe = (
        f"índice: " + ", ".join(f"{rotulo.lower()} {n}" for rotulo, n in contagens.items())
        + f"; regulamentos: {contagem(baixados, 'PDF novo', 'PDFs novos')}, {milhar(len(em_disco))} em disco"
    )
    if falhas or recusados:
        detalhe += f"; {contagem(falhas + recusados, 'falha', 'falhas')}"
        return ResumoFonte(PARCIAL, detalhe, arquivos=baixados + 1)
    return ResumoFonte(OK, detalhe, arquivos=baixados + 1)


def _ordem(item: dict) -> tuple:
    return (item["data_entrega"], item["situacao"] == "A", int(item["versao"] or 0), int(item["doc_id"]))


def ler_indice(config: Config) -> list[dict]:
    return armazem.ler_csv(pasta_documentos(config) / "indice.csv")
