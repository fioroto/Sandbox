"""O mapeamento "Indicadores de ETF e a melhor origem de cada um" como dado.

Transcricao do documento de referencia da prateleira de ETF da XP (set/2026):
as fontes, e cada indicador com quem o ve (cliente), a origem recomendada, a
alternativa, o estado do dado no Brasil e a nota. O extrator usa isto em dois
lugares: `--listar-fontes` (o que cada fonte da e qual adaptador a captura) e
a cobertura (para cada indicador, quantos ETFs ja tem o insumo em disco).

Cada `Via` aponta os insumos que a cobertura mede (chaves de
`cobertura.MEDIDORES`); insumos de uma via se somam (E), vias alternativas se
excluem (OU). `coletada=False` marca a origem que o extrator nao captura - o
feed intradiario da XP, o book de ofertas, o site do fundo -, e o motivo vai
na descricao.

Onde o extrator captura algo que o mapeamento nao lista (cadastro e carteira
mensal da CVM, carteira do dia dos indices B3), a via entra como alternativa
marcada "extra", para a cobertura mostrar o ganho sem reescrever o mapeamento.

Os textos daqui saem na tela e no CSV de cobertura, entao seguem a acentuacao
do portugues.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from shelf_monitor.report.shared.fontes import (
    B3_ARQUIVOS,
    B3_FUNDOS_LISTADOS,
    B3_INDICES,
    COMDINHEIRO,
    CVM_CADASTRO,
    CVM_CDA,
    FUNDOS_NET_DOCUMENTOS,
    Fonte,
)


class Estado(str, Enum):
    PRONTO = "Pronto"
    DERIVAVEL = "Derivável"
    DISPERSO = "Disperso"
    INTERNO = "Interno"
    NAO_EXISTE = "Não existe como número"
    REGRA = "Regra"


@dataclass(frozen=True)
class Origem:
    """Uma fonte do mapeamento e como o extrator a captura."""

    chave: str
    nome: str
    oferece: str
    como: str
    fonte: Fonte | None = None
    # Chaves de --fontes que capturam esta origem (vazio: planilha manual ou regra).
    adaptadores: tuple[str, ...] = ()
    # Onde fica o que gente preenche, quando a origem e manual.
    planilha: str | None = None
    extra: bool = False


@dataclass(frozen=True)
class Via:
    """Um caminho ate o indicador: a origem, como o mapeamento o descreve, e os
    insumos que a cobertura mede."""

    origem: str
    descricao: str
    insumos: tuple[str, ...] = ()
    coletada: bool = True


@dataclass(frozen=True)
class Indicador:
    chave: str
    secao: str
    nome: str
    cliente: str
    estado: Estado
    recomendada: Via | None
    alternativas: tuple[Via, ...] = ()
    nota: str = ""
    # Estado com ressalva ("D+1", "licenca"), como no mapeamento.
    qualificador: str = ""
    # Texto da regra, quando o indicador e regra e nao dado.
    regra: str = ""
    # O que o extrator faz diferente do mapeamento, e por que (medido).
    captura: str = ""


# --- fontes -------------------------------------------------------------------

ORIGENS: dict[str, Origem] = {
    o.chave: o
    for o in (
        Origem(
            "b3_arquivos", "B3, portal de arquivos (arquivos.b3.com.br)",
            "Negócios consolidados por instrumento (preço, volume, quantidade, número de negócios), "
            "cadastro de instrumentos (categoria, ISIN, lote, início de negociação), série COTAHIST",
            "CSV por pregão, grátis, D+0 após o fechamento; API em duas chamadas "
            "(requestname → token → download)",
            B3_ARQUIVOS, ("b3-arquivos", "cotahist"),
        ),
        Origem(
            "fnet", "FNET (gerenciador de documentos da B3, filtro ETF)",
            "Informe diário: valor da cota, patrimônio, captação, resgate, número de cotistas; "
            "regulamento; demonstrações; relatório anual",
            "Grátis, D+1. Os fundos de índice não estão no portal de dados abertos da CVM",
            FUNDOS_NET_DOCUMENTOS, ("fnet-informe", "fnet-mensal", "fnet-documentos"),
        ),
        Origem(
            "regulamento", "Regulamento e site do fundo",
            "Taxa de administração e máxima, taxa do fundo investido, custos extras, índice, "
            "público-alvo, PMRC, política de distribuição, erro de aderência, composição diária",
            "Documento por fundo; sem padrão. Leitura manual ou extração assistida",
            FUNDOS_NET_DOCUMENTOS, ("fnet-documentos", "regulamento"),
            planilha="manual/regulamento.csv",
        ),
        Origem(
            "gestora", "Gestora, arquivo de composição",
            "Carteira completa do dia (cesta de criação e resgate)",
            "Obrigatório e diário, formato varia por gestora",
            None, ("gestoras",), planilha="manual/gestoras_urls.csv",
        ),
        Origem(
            "indices_b3", "Provedor do índice",
            "Série do índice de referência",
            "Índices B3: grátis. MSCI, S&P, Bloomberg, Nasdaq, Teva: licença ou via Comdinheiro",
            B3_INDICES, ("indices-b3",), planilha="manual/indices.csv",
        ),
        Origem(
            "comdinheiro", "Comdinheiro",
            "Retornos, volatilidade, beta e DY prontos por janela; retorno de índices; pesos dos "
            "índices B3; taxa e taxa máxima como triagem",
            "Custo de assinatura. Cadastro incompleto e não comparável; sem cota nem patrimônio",
            COMDINHEIRO, ("comdinheiro",), planilha="manual/comdinheiro_consultas.csv",
        ),
        Origem(
            "xp_cadastro", "XP, cadastro interno",
            "Classe, risco em pontos, elegibilidade por suitability, recorrência, categoria da régua",
            "Definição da casa",
            None, (), planilha="manual/xp_cadastro.csv",
        ),
        Origem(
            "norma", "Norma",
            "Regras de tributação (Lei 13.043/2014, IN RFB 1.585/2015, Lei 14.754/2023), fórmula "
            "do erro de aderência (Res. CVM 175, Anexo V, art. 27)",
            "Regra, não dado",
        ),
        # Fora da lista do mapeamento, capturadas porque sao gratis e cobrem
        # campos que ele deixa como disperso ou pago.
        Origem(
            "b3_fundos", "B3, fundos listados",
            "Nome de pregão, CNPJ, código de negociação e tipo (ETF, ETF-RF, ETF-CRIPTO, ETF-FII)",
            "API pública do portal de fundos listados, um detalhe por fundo",
            B3_FUNDOS_LISTADOS, ("b3-fundos",), extra=True,
        ),
        Origem(
            "cvm_cadastro", "CVM, cadastro de fundos e classes",
            "Razão social, gestor, administrador, custodiante, auditor e data de constituição",
            "Arquivo diário do portal de dados abertos da CVM",
            CVM_CADASTRO, ("cvm-cadastro",), extra=True,
        ),
        Origem(
            "cvm_cda", "CVM, composição da carteira (CDA)",
            "Carteira mensal de cada ETF (arquivo cda_fie)",
            "Mensal, grátis, publicada algumas semanas depois do fim do mês",
            CVM_CDA, ("cvm-cda",), extra=True,
        ),
        Origem(
            "formador", "Contrato do formador de mercado (emissor)",
            "Formador contratado, spread máximo e quantidade mínima de cotas",
            "Documento do emissor; sem base central",
            None, (), planilha="manual/formador.csv",
        ),
        Origem("derivado", "Cálculo sobre insumos capturados", "Indicador derivado", "Etapa seguinte à captura"),
        Origem("nome", "Razão social (triagem)", "Leitura do nome do fundo", "Só para triagem, não é dado conferido"),
        Origem("fora", "Fora do alcance do extrator", "Fonte interna ou sem acesso automatizado", "Não coletada"),
    )
}


# --- indicadores ------------------------------------------------------------------

_B3_INSTR = Via("b3_arquivos", "B3, cadastro de instrumentos", ("b3_instrumentos",))
_COMDINHEIRO = Via("comdinheiro", "Comdinheiro", ("comdinheiro",))
_TRIAGEM = Via("nome", "Razão social só para triagem", ("nome",))
_CDA_EXTRA = Via("cvm_cda", "CVM, carteira mensal (CDA) - extra ao mapeamento", ("cvm_cda",))
_CARTEIRA_INDICE = Via("indices_b3", "Carteira do dia do índice B3 - extra ao mapeamento", ("indice_carteira",))


def _com(descricao: str) -> Via:
    return Via("comdinheiro", descricao, ("comdinheiro",))


def _reg(campo: str, descricao: str = "Regulamento") -> Via:
    return Via("regulamento", descricao, (f"regulamento:{campo}",))


_ID = "1. Identificação"
_PRECO = "2. Preço e negociação"
_CUSTO = "3. Custo"
_RETORNO = "4. Retorno"
_RISCO = "5. Risco"
_CARTEIRA = "6. Carteira"
_PATRIMONIO = "7. Patrimônio e base de cotistas"
_REGRAS = "8. Regras e elegibilidade"
SECOES = (_ID, _PRECO, _CUSTO, _RETORNO, _RISCO, _CARTEIRA, _PATRIMONIO, _REGRAS)

INDICADORES: tuple[Indicador, ...] = (
    # 1. Identificacao
    Indicador(
        "ticker_isin_lote", _ID, "Ticker, ISIN, lote", "Sim", Estado.PRONTO, _B3_INSTR, (_COMDINHEIRO,),
        nota="Um fundo pode trocar de ticker; o ISIN muda, o CNPJ não",
    ),
    Indicador(
        "cnpj_prestadores", _ID, "CNPJ, razão social, gestor, administrador, custodiante, auditor",
        "Assessor", Estado.PRONTO, Via("fnet", "FNET / regulamento", ("fnet_regulamento",)),
        (_com("Comdinheiro (100% preenchido)"),
         Via("cvm_cadastro", "CVM, cadastro de classes - extra ao mapeamento", ("cvm_cadastro",))),
        nota="Chave de deduplicação é o CNPJ",
        captura="O CNPJ do regulamento vem do nome do arquivo que o FNET entrega; custodiante e "
                "auditor, do cadastro de classes da CVM",
    ),
    Indicador(
        "nome_pregao", _ID, "Nome de pregão", "Sim", Estado.PRONTO,
        Via("b3_fundos", "B3", ("b3_fundos",)),
    ),
    Indicador(
        "categoria_b3", _ID, "Categoria B3 (ações, renda fixa, índice estrangeiro)", "Não", Estado.PRONTO,
        Via("b3_arquivos", "B3, cadastro", ("b3_instrumentos",)),
        nota="Base da regra de tributação",
        captura="ETF de renda fixa não está nas categorias \"ETF\" do cadastro: é FIXED INCOME "
                "TRADABLE INSTRUMENT T1, segmento FORWARD (68 tickers em 23/09/2026)",
    ),
    Indicador(
        "classe_xp", _ID, "Classe XP (Inflação, RV Brasil, RV Internacional, Câmbio…)", "Sim", Estado.INTERNO,
        Via("xp_cadastro", "XP, cadastro interno", ("manual:xp_cadastro:classe_xp",)), (_TRIAGEM,),
        nota="Benchmark e classe Anbima vêm vazios na Comdinheiro",
    ),
    Indicador(
        "indice_referencia", _ID, "Índice de referência", "Sim", Estado.DISPERSO,
        _reg("indice_referencia"), (Via("nome", "Razão social (triagem)", ("nome",)),),
        nota="Define a categoria da régua; \"S&P 500 com dólar\" e \"sem dólar\" são índices diferentes",
    ),
    Indicador(
        "inicio_constituicao", _ID, "Início de negociação e constituição", "Sim, como \"fundo novo\"",
        Estado.PRONTO,
        Via("b3_arquivos", "B3 (negociação), FNET (constituição)", ("b3_instrumentos", "cvm_cadastro")),
        (_com("Comdinheiro (constituição)"),),
        nota="\"Início das atividades\" da Comdinheiro é a data da CVM 175; não usar. Ticker renomeado "
             "reinicia a data na B3",
        captura="A constituição vem do cadastro da CVM (Data_Constituicao), que o FNET não traz como "
                "campo; o BOVA11, de 2008, aparece com início de negociação em 16/07/2026 no cadastro B3",
    ),
    # 2. Preco e negociacao
    Indicador(
        "preco_fechamento", _PRECO, "Preço da cota (fechamento)", "Sim", Estado.PRONTO,
        Via("b3_arquivos", "B3, negócios consolidados", ("b3_preco",)), (_COMDINHEIRO,),
        nota="É o investimento mínimo (lote de 1 cota)",
    ),
    Indicador(
        "preco_intradiario", _PRECO, "Preço intradiário", "Sim", Estado.PRONTO,
        Via("fora", "Feed de mercado da XP", coletada=False),
        nota="Fora dos arquivos públicos",
    ),
    Indicador(
        "valor_patrimonial", _PRECO, "Valor patrimonial da cota", "Sim, no detalhe", Estado.PRONTO,
        Via("fnet", "FNET, informe diário", ("fnet_informe",)),
        nota="A Comdinheiro não tem", qualificador="D+1",
    ),
    Indicador(
        "premio_desconto", _PRECO, "Prêmio ou desconto", "Assessor", Estado.DERIVAVEL,
        Via("derivado", "Derivar: preço B3 ÷ cota FNET − 1", ("b3_preco", "fnet_informe")),
        nota="ETF de um ativo e ETF pouco negociado carregam prêmio",
    ),
    Indicador(
        "volume_diario", _PRECO, "Volume diário", "Sim", Estado.DERIVAVEL,
        Via("b3_arquivos", "B3, mediana de 63 pregões, dias sem negócio como zero, sem lotes de bloco",
            ("b3_janela_63",)),
        (_com("Comdinheiro VOLUME_MEDIO(63), só para conferir"),),
        nota="A média é mais que o dobro da mediana em 86 de 226 ETFs",
        captura="Lote de bloco é um ticker à parte (sufixo Q ou M, segmento Equity Block Trading Lot); "
                "a linha fica marcada como \"bloco\" para a mediana poder excluí-la",
    ),
    Indicador(
        "negocios_dia", _PRECO, "Negócios por dia", "Sim", Estado.DERIVAVEL,
        Via("b3_arquivos", "B3, mediana de 63 pregões", ("b3_janela_63",)),
        (_com("Comdinheiro NEGOCIOS_MEDIO()"),),
        nota="Um bloco é um negócio só",
    ),
    Indicador(
        "presenca_pregoes", _PRECO, "Presença em pregões", "Não, critério", Estado.DERIVAVEL,
        Via("b3_arquivos", "B3: dias com negócio ÷ pregões, 63 dias", ("b3_janela_63",)),
        (_com("Comdinheiro PRESENCA_BOLSA()"),),
        nota="Elegibilidade: 90% ou mais",
    ),
    Indicador(
        "spread_medio", _PRECO, "Spread médio", "Sim, como \"spread garantido até N cotas\"", Estado.DISPERSO,
        Via("formador", "Contrato do formador (B3, emissor)", ("manual:formador:spread_maximo",)),
        (Via("fora", "Book de ofertas da XP", coletada=False),),
        nota="O boletim mensal de ETF da B3 foi descontinuado",
    ),
    Indicador(
        "formador_mercado", _PRECO, "Formador de mercado contratado", "Não, critério", Estado.DISPERSO,
        Via("formador", "Emissor / site do fundo", ("manual:formador:formador",)),
        (Via("fora", "Planilha pública da B3", coletada=False),),
        nota="A planilha da B3 está defasada: marcava 7 de 25 da XP Asset, todos têm",
    ),
    Indicador(
        "liquidacao", _PRECO, "Liquidação", "Sim", Estado.REGRA,
        Via("norma", "Regra: D+2", ("b3_instrumentos",)),
        nota="Os prazos de cotização da Comdinheiro não valem para ETF",
        regra="D+2",
        captura="O cadastro de instrumentos traz o prazo por ETF (DaysToSttlm): 2 nos ETFs de ações e "
                "índice estrangeiro, 1 nos de renda fixa (medido em 23/09/2026)",
    ),
    # 3. Custo
    Indicador(
        "taxa_administracao", _CUSTO, "Taxa de administração", "Sim", Estado.DISPERSO,
        _reg("taxa_administracao"), (_com("Comdinheiro, campo \"Taxa de Administracao\", só como triagem"),),
        nota="Na Comdinheiro o campo é o total em seis gestoras, a parcela do administrador na Hashdex e "
             "a camada local nos ETFs de ETF da XP e da Investo",
    ),
    Indicador(
        "taxa_fundo_investido", _CUSTO, "Taxa do fundo investido", "Não, compõe o custo", Estado.DISPERSO,
        _reg("taxa_fundo_investido"), (_com("Comdinheiro \"Taxa máxima\" (coincide com o total nos ETFs de ETF)"),),
        nota="GOLD11 0,30% + 0,25%; ACWI11 0,30% + 0,32%; XINA11 0,30% + 0,59%",
    ),
    Indicador(
        "custos_extras", _CUSTO, "Custos fora da taxa (consultor, mínimo mensal, banco liquidante)",
        "Não, compõe o custo", Estado.DISPERSO,
        _reg("custos_extras"), (_com("Comdinheiro \"Observações\" e \"Taxa financeira\""),),
        nota="GPUS11 0,18% vira 0,35%; mínimo de R$ 10 mil/mês pesa mais que a taxa abaixo de R$ 24 mi "
             "a R$ 80 mi de PL",
    ),
    Indicador(
        "taxa_faixa_pl", _CUSTO, "Taxa por faixa de patrimônio", "Não", Estado.DERIVAVEL,
        Via("regulamento", "Regulamento + PL do FNET", ("regulamento:taxa_administracao", "fnet_informe")),
        (_com("Comdinheiro (texto)"),),
        nota="BOVX11: 0% até R$ 1 bi",
    ),
    Indicador(
        "custo_total", _CUSTO, "Custo total exibido", "Sim", Estado.DERIVAVEL,
        Via("derivado", "Derivar: taxa + fundo investido + extras, conferido no regulamento",
            ("regulamento:taxa_administracao", "regulamento:taxa_fundo_investido", "regulamento:custos_extras")),
        nota="O único número de custo que vai à tela",
    ),
    Indicador(
        "taxa_performance", _CUSTO, "Taxa de performance", "Sim, \"não tem\"", Estado.PRONTO,
        _reg("taxa_performance"), (_com("Comdinheiro (zero em todos)"),),
    ),
    Indicador(
        "custos_transacao", _CUSTO, "Custos de transação do fundo (% do PL)", "Assessor", Estado.DISPERSO,
        Via("fora", "Site do fundo, anual", coletada=False),
        nota="Obrigatório, sem padrão",
        captura="Os sites de gestora testados (XP Asset, BlackRock) respondem 403 a cliente HTTP",
    ),
    Indicador(
        "receita_aluguel", _CUSTO, "Receita de aluguel repassada", "Assessor", Estado.NAO_EXISTE,
        Via("fora", "Emissor", coletada=False),
        (Via("fnet", "Demonstrações anuais", ("fnet_demonstracoes",)),),
        nota="Explica o Ibovespa acima do índice; a XP Asset pode publicar primeiro",
    ),
    # 4. Retorno
    Indicador(
        "rentabilidade_12m_ano", _RETORNO, "Rentabilidade 12 meses e no ano (preço)", "Sim", Estado.PRONTO,
        _com("Comdinheiro (ajustada por proventos)"),
        (Via("b3_arquivos", "B3 COTAHIST, calcular", ("b3_janela_12m",)),),
        nota="Só com 12 meses completos e liquidez; 143 de 235 têm",
    ),
    Indicador(
        "rentabilidade_inicio", _RETORNO, "Rentabilidade desde o início", "Sim, nos novos", Estado.DERIVAVEL,
        _com("Comdinheiro RET() com data inicial"),
        (Via("b3_arquivos", "B3, calcular", ("b3_historico",)),),
        nota="Não veio na extração",
    ),
    Indicador(
        "rentabilidade_anual", _RETORNO, "Rentabilidade por ano e gráfico", "Sim, no detalhe", Estado.DERIVAVEL,
        Via("b3_arquivos", "B3 COTAHIST (série ajustada)", ("b3_historico",)),
        (_com("Comdinheiro PRECO_AJ()"),),
        nota="Retornos anuais da Comdinheiro vieram vazios",
        captura="ETF de renda fixa não está no COTAHIST (0 de 65 em 23/09/2026): a série deles vem dos "
                "negócios consolidados",
    ),
    Indicador(
        "rentabilidade_cota", _RETORNO, "Rentabilidade da cota (NAV)", "Assessor", Estado.DERIVAVEL,
        Via("fnet", "FNET, informe diário", ("fnet_mensal_12m",)),
        (Via("fnet", "FNET, informe diário dia a dia - extra", ("fnet_informe_12m",)),),
        nota="A base certa para aderência; livre de prêmio e desconto",
        captura="Mede-se pelos fechamentos mensais do FNET (um informe por ETF por mês)",
    ),
    Indicador(
        "retorno_indice", _RETORNO, "Retorno do índice no mesmo período", "Não, insumo", Estado.PRONTO,
        Via("indices_b3", "Índices B3 (grátis); Comdinheiro INFO_INDICE() para os estrangeiros", ("indice_serie",)),
        (_com("Comdinheiro INFO_INDICE()"), Via("fora", "Provedor do índice", coletada=False)),
        nota="Sem ele não há diferença para o índice", qualificador="licença",
    ),
    Indicador(
        "tracking_difference", _RETORNO, "Diferença para o índice (tracking difference)", "Sim, no detalhe",
        Estado.DERIVAVEL,
        Via("derivado", "Derivar: cota FNET − índice", ("fnet_mensal_12m", "indice_serie")),
        (Via("derivado", "Preço B3 − índice, só para ETF líquido", ("b3_janela_12m", "indice_serie")),),
        nota="É o custo real",
    ),
    Indicador(
        "erro_aderencia", _RETORNO, "Erro de aderência e gatilhos da norma", "Assessor, e selo \"dentro do limite\"",
        Estado.DERIVAVEL,
        Via("derivado", "Derivar com a fórmula do art. 27", ("fnet_informe_12m", "indice_serie")),
        (Via("fora", "Site do fundo", coletada=False),),
        nota="Obrigatório em norma, sem base central",
        regra="Res. CVM 175, Anexo V, art. 27",
    ),
    Indicador(
        "distribuicoes_dy", _RETORNO, "Distribuições e DY", "Sim, nos 18 que distribuem", Estado.PRONTO,
        _com("Comdinheiro DY_12M e PROVENTOS()"),
        (Via("fora", "B3, eventos", coletada=False),),
        nota="Nos demais, \"reinveste\"",
        captura="O endpoint de eventos do portal de fundos listados da B3 responde 404 para ETF (24/09/2026)",
    ),
    # 5. Risco
    Indicador(
        "volatilidade_12m", _RISCO, "Volatilidade 12 meses", "Não, insumo", Estado.PRONTO,
        _com("Comdinheiro vol_ano_12m"), (Via("b3_arquivos", "B3, calcular", ("b3_janela_12m",)),),
        nota="61% têm",
    ),
    Indicador(
        "risco_escala", _RISCO, "Risco em escala (pontos ou 1–7)", "Sim", Estado.INTERNO,
        Via("xp_cadastro", "XP, metodologia interna sobre a vol", ("manual:xp_cadastro:risco_pontos",)),
        nota="ETF não tem lâmina; a escala é da casa",
    ),
    Indicador(
        "drawdown", _RISCO, "Queda máxima (drawdown)", "Sim, no detalhe", Estado.DERIVAVEL,
        _com("Comdinheiro MDD()"), (Via("b3_arquivos", "B3, calcular", ("b3_historico",)),),
        nota="\"Quanto já caiu\" é mais legível que vol",
    ),
    Indicador(
        "beta", _RISCO, "Beta", "Assessor", Estado.PRONTO, _COMDINHEIRO,
        nota="Só faz sentido contra o Ibovespa, em ETF de ações Brasil",
    ),
    Indicador(
        "duration", _RISCO, "Duration (renda fixa)", "Assessor", Estado.DISPERSO,
        Via("gestora", "Gestora / regulamento", ("gestora_arquivo",)),
        (Via("cvm_cda", "Composição × dados do Tesouro", ("cvm_cda",)),),
    ),
    Indicador(
        "pmrc", _RISCO, "PMRC (renda fixa)", "Sim, via alíquota", Estado.DISPERSO,
        _reg("pmrc", "Regulamento e material da gestora"),
        nota="Define 25%, 20% ou 15%; LTBX11 é leitura do nome, conferir",
    ),
    Indicador(
        "var", _RISCO, "VaR", "Não", Estado.PRONTO, None, (_COMDINHEIRO,),
        nota="Derivado da vol; não vai à tela",
    ),
    # 6. Carteira
    Indicador(
        "composicao", _CARTEIRA, "Composição completa", "Sim, no detalhe", Estado.DISPERSO,
        Via("gestora", "Gestora, arquivo diário", ("gestora_arquivo",)), (_CDA_EXTRA,),
        nota="Obrigatório e diário, sem padrão entre gestoras",
    ),
    Indicador(
        "maiores_posicoes", _CARTEIRA, "Maiores posições", "Sim", Estado.DERIVAVEL,
        Via("derivado", "Derivar da composição", ("gestora_arquivo",)),
        (_com("Comdinheiro PESO_INDICE() para índices B3 (IBOV, IBrX, SMLL, IDIV, IFIX)"), _CARTEIRA_INDICE,
         _CDA_EXTRA),
        nota="Para índices estrangeiros só a gestora",
    ),
    Indicador(
        "numero_ativos", _CARTEIRA, "Número de ativos e concentração das 10 maiores", "Sim", Estado.DERIVAVEL,
        Via("derivado", "Derivar da composição", ("gestora_arquivo",)),
        (Via("indices_b3", "Provedor do índice", ("indice_carteira",)), _CDA_EXTRA),
    ),
    Indicador(
        "setor_pais", _CARTEIRA, "Setor e país", "Sim, no detalhe", Estado.DERIVAVEL,
        Via("derivado", "Derivar da composição", ("gestora_arquivo",)),
        (Via("indices_b3", "Provedor do índice", ("indice_carteira",)), _CDA_EXTRA),
    ),
    Indicador(
        "sobreposicao", _CARTEIRA, "Sobreposição entre ETFs", "Assessor", Estado.DERIVAVEL,
        Via("derivado", "Derivar de duas composições", ("gestora_arquivo",)), (_CDA_EXTRA,),
        nota="NASD11 e UTEC11 dividem as maiores posições",
    ),
    Indicador(
        "politica_distribuicao", _CARTEIRA, "Política de distribuição (acumula ou distribui)", "Sim",
        Estado.DISPERSO, _reg("politica_distribuicao"), (_com("Comdinheiro (DY > 0)"),),
    ),
    # 7. Patrimonio e base de cotistas
    Indicador(
        "patrimonio", _PATRIMONIO, "Patrimônio líquido", "Sim, no detalhe", Estado.PRONTO,
        Via("fnet", "FNET, informe diário", ("fnet_informe",)),
        nota="Vazio na Comdinheiro", qualificador="D+1",
    ),
    Indicador(
        "cotistas", _PATRIMONIO, "Número de cotistas", "Sim, no detalhe", Estado.PRONTO,
        Via("fnet", "FNET", ("fnet_informe",)), qualificador="D+1",
    ),
    Indicador(
        "captacao_resgate", _PATRIMONIO, "Cotas emitidas, captação e resgate", "Assessor", Estado.PRONTO,
        Via("fnet", "FNET", ("fnet_informe",)), (Via("derivado", "PL ÷ cota", ("fnet_informe",)),),
        nota="Sinal de fechamento de fundo", qualificador="D+1",
    ),
    Indicador(
        "idade_fundo_novo", _PATRIMONIO, "Idade e \"fundo novo\"", "Sim", Estado.PRONTO,
        Via("b3_arquivos", "B3 início de negociação; FNET constituição", ("b3_instrumentos", "cvm_cadastro")),
        nota="Menos de 12 meses = fundo novo",
    ),
    # 8. Regras e elegibilidade
    Indicador(
        "tributacao", _REGRAS, "Tributação", "Sim", Estado.REGRA,
        Via("norma", "Regra por categoria B3 (15% ações e exterior; 25/20/15% renda fixa pelo PMRC)",
            ("tributacao",)),
        nota="Campo da Comdinheiro vem vazio",
        regra="Lei 13.043/2014, IN RFB 1.585/2015 e Lei 14.754/2023: 15% em ETF de ações e de índice "
              "estrangeiro; 25%, 20% ou 15% em ETF de renda fixa, pelo PMRC",
    ),
    Indicador(
        "publico_alvo", _REGRAS, "Público-alvo e qualificado", "Sim", Estado.DISPERSO,
        _reg("publico_alvo"), (_com("Comdinheiro, só com validação"),),
        nota="NASD11 e BOVX11 vêm como \"qualificado\" na Comdinheiro",
    ),
    Indicador(
        "aceita_mensal", _REGRAS, "Aceita investimento mensal", "Sim", Estado.INTERNO,
        Via("xp_cadastro", "XP, cadastro interno", ("manual:xp_cadastro:aceita_investimento_mensal",)),
    ),
    Indicador(
        "documentos", _REGRAS, "Documentos (regulamento, informe, composição)", "Sim, links", Estado.PRONTO,
        Via("fnet", "FNET; site da gestora", ("fnet_regulamento",)),
        (Via("gestora", "Site da gestora", ("gestora_arquivo",)),),
        nota="ETF não tem lâmina",
    ),
)


def indicador(chave: str) -> Indicador:
    for item in INDICADORES:
        if item.chave == chave:
            return item
    raise KeyError(chave)


def insumos_do_mapa() -> set[str]:
    """Toda chave de insumo citada por alguma via - a cobertura precisa medir todas."""
    chaves: set[str] = set()
    for item in INDICADORES:
        vias = ((item.recomendada,) if item.recomendada else ()) + item.alternativas
        for via in vias:
            chaves.update(via.insumos)
    return chaves
