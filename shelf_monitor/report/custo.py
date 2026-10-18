"""Custo real da vitrine, contra os pares da mesma categoria.

A metrica de custo que existia comparava a mediana da taxa de administracao da
vitrine inteira entre casas (`competitive.median_admin_fee`). Ela mede
**composicao, nao preco**: uma casa com mais renda fixa (~0,2% ao ano) aparece
"mais barata" que uma com mais acoes (~2%) sem que nenhum produto custe
diferente. Trocar a prateleira de mix muda o numero; trocar a taxa dos fundos,
quase nao.

O que este modulo faz no lugar: compara cada fundo com a **mediana da propria
categoria no universo oficial** e agrega o indice, nao a taxa. Um indice de 1,30
diz que o fundo cobra 30% acima dos pares dele - e isso e comparavel entre casas
de composicoes diferentes.

**O nivel absoluto do indice carrega um vies estrutural; a comparacao nao.**
Medido em 2026-09-20, as tres vitrines saem em 1,65x (XP), 1,50x (BTG) e 1,57x
(Inter). Nenhuma delas esta cobrando 60% a mais que o mercado: a vitrine exibe a
classe de VAREJO, que carrega a camada de distribuicao, enquanto o universo da
categoria inclui classes da mesma estrategia vendidas sem essa camada. O numero
que decide alguma coisa e a diferenca ENTRE as casas e o fundo individual muito
acima da propria categoria - nao a distancia de 1,00x.

Tres guardas que o dado exige
-----------------------------

**Taxa polimorfica.** `TAXA_ADM` carrega ora percentual ao ano, ora valor fixo em
reais (o regulamento admite texto livre para investidor qualificado). Entram no
indice apenas as classes com unidade `percentual`; as demais aparecem contadas a
parte. Sem isso, um fundo de "R$ 30.000 por ano" lidera o ranking de caro como
se cobrasse 30.000%.

**Idade da entrega.** O extrato guarda uma linha por classe por data-base, e a
data varia de 2015-10-02 a 2026-08-28. Uma taxa entregue em 2015 nao e a taxa de
hoje: acima de `_MAX_IDADE_MESES` a classe sai do indice, contada no bloco de
exclusao.

**A referencia e o universo de VAREJO, nao o universo inteiro.** Medido em
2026-09-20 sobre as 13.093 classes com taxa percentual e entrega recente: 47,2%
delas sao **exclusivas**, 21,1% cobram **taxa zero** e 1.368 sao de **publico
profissional**. As exclusivas sao veiculos de um cotista, que nao estao na
vitrine de ninguem; as de taxa zero sao masters e feeders cuja taxa e cobrada na
outra ponta da estrutura.

Com tudo dentro, a mediana de "Multimercados Livre" cai para 0,080% ao ano e
todo fundo de varejo aparece cobrando sete vezes a categoria - foi exatamente o
que a primeira execucao devolveu: indice mediano de **6,79x** e 82% da vitrine
do BTG marcada como "cara". Aplicando ao universo a mesma regra de varejo que
`profile.restriction_reason` ja aplica a vitrine, as medianas passam a 0,800% em
Multimercados Livre, 1,300% em Acoes Livre, 0,400% em Renda Fixa Duracao Livre
Credito Livre e 1,500% em Multimercados Macro - precos de prateleira
reconheciveis.

Um detalhe que a regra herda e nao inverte: **investidor qualificado nao e
motivo de corte** (`profile.py:12-13`). Cortar por qualificado levaria
Multimercados Livre a 1,000%, e seria uma decisao de produto diferente da que o
projeto ja tomou.

**Custo nao e ticket.** Taxa e propriedade da classe, identica em qualquer
distribuidor - por isso pode vir da base oficial. **Aplicacao minima nao**: o
minimo do registro e o institucional da classe, nao o que o site cobra, e
`competitive.vitrine_only_values` existe para impedir essa confusao. Aqui o
minimo oficial e usado para uma pergunta so, que e interna a base e nao envolve
o site: *existe outra classe do mesmo fundo com porta de entrada menor?*

Quebra por classe e a prateleira da XP (pedido de 16/10/2026)
-------------------------------------------------------------

**Por classe.** Cada casa sai tambem por classe de fundo - o nivel 1 da ANBIMA,
o eixo grosso do monitor (`competitive.classe_grossa`): renda fixa,
multimercados, acoes, cambial... A conta e a mesma da casa inteira, mediana dos
indices, dentro de cada classe. Classe com menos de
`MIN_COMPARAVEIS_CLASSE_CUSTO` fundos comparaveis na casa fica sem indice, com
a contagem ao lado.

**XP pela base interna.** O custo da XP sai da lista distribuivel - os fundos
que a casa distribui, inclusive os que nao estao na vitrine publica -, e o das
demais casas, da vitrine do site. Da lista entram os de situacao "distribui";
"pode distribuir" ainda nao esta na prateleira, e classe exclusiva e veiculo de
um cotista, nao produto de prateleira. Publico profissional fica: as vitrines
tambem exibem algumas (medido em 16/10/2026: 2 na XP, 8 no BTG, 4 no Itau), e
tira-las so da XP a deixaria com regra mais dura que a das outras. Sem lista na
pasta, a XP volta a vitrine e a nota do bloco diz por que.
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from shelf_monitor.caixa import sem_caixa
from shelf_monitor.classify import CATEGORIA_ESTRUTURADO, KEY_CATEGORIA, eh_estruturado
from shelf_monitor.config import PLATFORMS
from shelf_monitor.distribuivel import DISTRIBUI
from shelf_monitor.mercado_csv import categoria_da_linha
from shelf_monitor.models import FundOffer, ShelfSnapshot
from shelf_monitor.normalize import format_cnpj, normalize_cnpj, normalize_text, parse_number
from shelf_monitor.previdencia_fora import sem_previdencia
from shelf_monitor.report.aggregate import _platform_label, categoria_comparavel
from shelf_monitor.report.competitive import (
    KEY_CATEGORIA_GROSSA,
    classe_grossa,
    identifiable_vitrines,
    vitrine_snapshots,
)
from shelf_monitor.report.shared import (
    BARATO,
    CARO,
    MIN_AMOSTRA_CATEGORIA,
    MIN_COBERTURA_CUSTO,
    MIN_COMPARAVEIS_CLASSE_CUSTO,
)
from shelf_monitor.texto import contagem, data_br, milhar

# Acima disto a entrega e historico, nao condicao vigente. Dois anos cobrem
# classes que nao reentregam todo ano sem deixar passar a entrega de 2015.
_MAX_IDADE_MESES = 24

# Minimo de classes numa categoria para a mediana dela servir de referencia.
_MIN_AMOSTRA_CATEGORIA = MIN_AMOSTRA_CATEGORIA

# Acima deste indice o fundo cobra caro para a categoria dele; abaixo do outro,
# barato. A faixa no meio e ruido de arredondamento entre classes parecidas.
_CARO = CARO
_BARATO = BARATO

# Resgate ate D+1 e o que o varejo trata como liquidez imediata.
_LIQUIDEZ_IMEDIATA = 1

# Cobertura de taxa comparavel que a FONTE permite. Medido em 2026-09-20 sobre
# o universo com taxa: 13.154 classes com entrega de ate 24 meses contra 5.369
# entre 25 e 60 e 3.522 acima de 60. Cerca de 40% do mercado simplesmente nao
# reentrega o extrato, e exigir 50% de cobertura excluia toda casa - o numero
# passava a dizer "ninguem mensuravel" em vez de "custo da prateleira". O piso
# ficou onde a fonte permite, e a cobertura sai ao lado de todo indice.
_MIN_COBERTURA = MIN_COBERTURA_CUSTO

# Fundos comparaveis de uma classe numa casa para a classe ter indice.
_MIN_COMPARAVEIS_CLASSE = MIN_COMPARAVEIS_CLASSE_CUSTO

# De onde sai a prateleira de cada casa no custo.
FONTE_VITRINE = "vitrine"
FONTE_BASE_INTERNA = "base_interna"

# A ordem das classes nas tabelas: as tres que toda vitrine tem, as raras, e os
# baldes sem classificacao ANBIMA no fim. Classe fora da lista vem depois.
ORDEM_DAS_CLASSES = (
    "Renda Fixa", "Multimercados", "Ações", "Cambial", "Exterior", "Previdência",
    "Estruturado", "Sem classificação",
)


def _ordem_da_classe(classe: str) -> tuple[int, str]:
    if classe in ORDEM_DAS_CLASSES:
        return ORDEM_DAS_CLASSES.index(classe), classe
    return len(ORDEM_DAS_CLASSES), classe


def _idade_meses(referencia: date | None, hoje: date | None = None) -> int | None:
    if referencia is None:
        return None
    hoje = hoje or date.today()
    return (hoje.year - referencia.year) * 12 + (hoje.month - referencia.month)


@dataclass(frozen=True)
class CustoClasse:
    """O que a base oficial sabe sobre o custo de uma classe."""

    cnpj: str
    categoria: str | None = None
    taxa_adm: float | None = None
    taxa_percentual: bool = False
    data_base: date | None = None
    tem_taxa_entrada: str | None = None
    tem_taxa_saida: str | None = None
    taxa_custodia_max: float | None = None
    dias_cotizacao: float | None = None
    id_fundo: str = ""
    minimo: float | None = None
    exclusivo: bool = False
    publico_alvo: str = ""
    # A denominacao do cadastro: e o nome do fundo da base interna, que chega
    # so com o CNPJ.
    nome: str = ""
    # O tipo do cadastro ("Classes de Cotas de Fundos FIDC"): marca o
    # estruturado que nao tem categoria ANBIMA, quando o nome nao diz.
    tipo: str = ""

    @property
    def restrita(self) -> bool:
        """Classe que `profile.restriction_reason` tiraria da analise de varejo.

        Mesma regra do resto do monitor, aplicada do lado do universo: exclusiva
        ou de publico profissional sai; **qualificado nao** - a decisao esta em
        `profile.py:12-13` e nao deve ser desfeita aqui por conveniencia.
        """
        return self.exclusivo or "PROFISSIONAL" in self.publico_alvo

    @property
    def idade_meses(self) -> int | None:
        return _idade_meses(self.data_base)

    @property
    def vigente(self) -> bool:
        idade = self.idade_meses
        return idade is not None and idade <= _MAX_IDADE_MESES

    @property
    def comparavel(self) -> bool:
        """Entra no indice do proprio fundo: percentual, recente, com categoria."""
        return (
            self.taxa_percentual
            and self.taxa_adm is not None
            and self.vigente
            and bool(self.categoria)
        )

    @property
    def serve_de_referencia(self) -> bool:
        """Entra na MEDIANA da categoria: alem de comparavel, e produto de varejo.

        Classe exclusiva e veiculo de um cotista, e taxa zero e master ou feeder
        cobrando na outra ponta. Nenhuma das duas descreve o preco de um produto
        de prateleira - ver o docstring do modulo para o efeito medido.
        """
        return self.comparavel and not self.restrita and (self.taxa_adm or 0) > 0


@dataclass
class CustoFundo:
    """Um fundo da vitrine, com o custo dele contra os pares da categoria."""

    cnpj: str
    nome: str
    categoria: str | None
    taxa_adm: float | None
    mediana_categoria: float | None
    idade_meses: int | None
    tem_taxa_entrada: str | None
    tem_taxa_saida: str | None
    taxa_custodia_max: float | None
    dias_cotizacao: float | None
    minimo_classe: float | None = None
    minimo_do_fundo: float | None = None
    motivo_fora: str | None = None
    # Nivel 1 da categoria (`competitive.classe_grossa`): a quebra por classe.
    classe: str | None = None

    @property
    def indice(self) -> float | None:
        """Taxa do fundo sobre a mediana da categoria dele."""
        if self.taxa_adm is None or not self.mediana_categoria:
            return None
        return self.taxa_adm / self.mediana_categoria

    @property
    def faixa(self) -> str | None:
        indice = self.indice
        if indice is None:
            return None
        if indice >= _CARO:
            return "caro para a categoria"
        if indice <= _BARATO:
            return "barato para a categoria"
        return "na faixa da categoria"

    @property
    def porta_menor(self) -> float | None:
        """Quanto menor e a menor classe do mesmo fundo, quando existe.

        Nao e comparacao entre casas: e a mesma base olhando para si mesma. A
        leitura util seria "este fundo tem classe de entrada mais barata que a
        exibida", que resolveria sem produto novo.

        **A fonte quase nao sustenta esta pergunta, e o numero medido diz isso.**
        Das 22.027 estruturas com aplicacao minima no cadastro de 2026-09-20,
        apenas 13 tem duas ou mais classes com minimo e **5** tem minimos
        diferentes entre elas. O lugar onde as condicoes comerciais de fato
        variam e a SUBCLASSE - e `subclasses.csv` nao traz minimo nem taxa: o
        extrato e entregue por CNPJ de classe, e a subclasse partilha o CNPJ da
        classe-mae. Entao a coluna sai em zero quase sempre, e isso e um fato
        sobre o cadastro, nao um bug do calculo.
        """
        if self.minimo_classe is None or self.minimo_do_fundo is None:
            return None
        if self.minimo_do_fundo >= self.minimo_classe:
            return None
        return self.minimo_do_fundo

    @property
    def liquidez_imediata(self) -> bool | None:
        if self.dias_cotizacao is None:
            return None
        return self.dias_cotizacao <= _LIQUIDEZ_IMEDIATA

    def to_dict(self) -> dict:
        return {
            "cnpj": self.cnpj,
            "nome": self.nome,
            "categoria": self.categoria,
            "taxa_adm": self.taxa_adm,
            "mediana_categoria": self.mediana_categoria,
            "indice": None if self.indice is None else round(self.indice, 3),
            "faixa": self.faixa,
            "idade_meses": self.idade_meses,
            "tem_taxa_entrada": self.tem_taxa_entrada,
            "tem_taxa_saida": self.tem_taxa_saida,
            "taxa_custodia_max": self.taxa_custodia_max,
            "dias_cotizacao": self.dias_cotizacao,
            "liquidez_imediata": self.liquidez_imediata,
            "minimo_classe": self.minimo_classe,
            "porta_menor": self.porta_menor,
            "motivo_fora": self.motivo_fora,
            "classe": self.classe,
        }


class IndiceCusto:
    """Custo por CNPJ e mediana por categoria, a partir do `mercado.csv`."""

    def __init__(self, classes: dict[str, CustoClasse]) -> None:
        self._classes = classes
        comparaveis: dict[str, list[float]] = {}
        por_fundo: dict[str, list[float]] = {}
        for classe in classes.values():
            if classe.serve_de_referencia:
                comparaveis.setdefault(classe.categoria, []).append(classe.taxa_adm)
            if classe.id_fundo and classe.minimo is not None:
                por_fundo.setdefault(classe.id_fundo, []).append(classe.minimo)
        self.medianas = {
            categoria: statistics.median(valores)
            for categoria, valores in comparaveis.items()
            if len(valores) >= _MIN_AMOSTRA_CATEGORIA
        }
        self.amostras = {c: len(v) for c, v in comparaveis.items()}
        self._minimo_do_fundo = {f: min(v) for f, v in por_fundo.items()}

    def __len__(self) -> int:
        return len(self._classes)

    def get(self, cnpj: str | None) -> CustoClasse | None:
        return self._classes.get(cnpj) if cnpj else None

    def minimo_do_fundo(self, id_fundo: str) -> float | None:
        return self._minimo_do_fundo.get(id_fundo)

    @classmethod
    def load(cls, market_dir: Path) -> IndiceCusto | None:
        caminho = market_dir / "mercado.csv"
        if not caminho.is_file():
            return None

        classes: dict[str, CustoClasse] = {}
        with caminho.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=";"):
                cnpj = normalize_cnpj(row.get("cnpj"))
                if not cnpj:
                    continue
                categoria = categoria_da_linha(row)
                unidade = normalize_text(row.get("taxa_adm_unidade"))
                classes[cnpj] = CustoClasse(
                    cnpj=cnpj,
                    categoria=categoria or None,
                    taxa_adm=parse_number(row.get("taxa_adm")),
                    taxa_percentual=unidade.startswith("PERCENTUAL"),
                    data_base=_data(row.get("taxa_data_base")),
                    tem_taxa_entrada=(row.get("tem_taxa_entrada") or "").strip() or None,
                    tem_taxa_saida=(row.get("tem_taxa_saida") or "").strip() or None,
                    taxa_custodia_max=parse_number(row.get("taxa_custodia_max")),
                    dias_cotizacao=parse_number(row.get("dias_cotizacao_resgate")),
                    id_fundo=(row.get("id_fundo") or "").strip(),
                    minimo=parse_number(row.get("aplicacao_inicial_minima")),
                    exclusivo=normalize_text(row.get("exclusivo")).startswith("S"),
                    publico_alvo=normalize_text(row.get("publico_alvo")),
                    nome=(row.get("denominacao") or "").strip(),
                    tipo=(row.get("tipo") or "").strip(),
                )
        return cls(classes) if classes else None

    def avaliar(self, offer: FundOffer) -> CustoFundo:
        """Custo de um fundo da vitrine, com o motivo quando nao da para medir."""
        classe = self.get(offer.cnpj)
        categoria = categoria_comparavel(offer) or (classe.categoria if classe else None)
        grossa = classe_grossa(categoria, offer.extra.get(KEY_CATEGORIA_GROSSA))
        # O fundo da base interna chega sem o nome comercial que o classificador
        # le ("FII", "FIDC"): o tipo do cadastro diz o mesmo. So o rotulo da
        # classe muda - o indice e o motivo seguem os de qualquer fundo.
        if not categoria and classe is not None and eh_estruturado(classe.tipo):
            grossa = classe_grossa(CATEGORIA_ESTRUTURADO)
        if classe is None:
            return CustoFundo(
                cnpj=offer.cnpj or "", nome=offer.name, categoria=categoria,
                taxa_adm=None, mediana_categoria=None, idade_meses=None,
                tem_taxa_entrada=None, tem_taxa_saida=None, taxa_custodia_max=None,
                dias_cotizacao=None,
                motivo_fora="fundo não encontrado na base oficial por CNPJ",
                classe=grossa,
            )

        motivo = None
        if classe.taxa_adm is None:
            motivo = "classe sem taxa publicada no extrato"
        elif (classe.taxa_adm or 0) <= 0:
            motivo = (
                "taxa zero no extrato - master ou feeder que cobra na outra "
                "ponta da estrutura"
            )
        elif not classe.taxa_percentual:
            motivo = "taxa em reais ou texto livre - não comparável com percentual"
        elif not classe.vigente:
            # Motivo agrupavel: a idade exata vai no campo `idade_meses` do
            # fundo. Colocando-a no texto, cada fundo virava uma chave propria
            # e o resumo "por motivo" deixava de resumir.
            motivo = (
                f"entrega do extrato com mais de {_MAX_IDADE_MESES} meses"
                if classe.idade_meses is not None
                else "entrega do extrato sem data-base"
            )
        elif not categoria:
            motivo = "fundo sem categoria oficial"
        elif categoria not in self.medianas:
            motivo = (
                f"categoria com menos de {_MIN_AMOSTRA_CATEGORIA} classes "
                "comparáveis no mercado - sem referência para comparar"
            )

        return CustoFundo(
            cnpj=classe.cnpj,
            nome=offer.name,
            categoria=categoria,
            taxa_adm=classe.taxa_adm if classe.taxa_percentual else None,
            mediana_categoria=None if motivo else self.medianas.get(categoria),
            idade_meses=classe.idade_meses,
            tem_taxa_entrada=classe.tem_taxa_entrada,
            tem_taxa_saida=classe.tem_taxa_saida,
            taxa_custodia_max=classe.taxa_custodia_max,
            dias_cotizacao=classe.dias_cotizacao,
            minimo_classe=classe.minimo,
            minimo_do_fundo=self.minimo_do_fundo(classe.id_fundo),
            motivo_fora=motivo,
            classe=grossa,
        )


def _data(texto: str | None) -> date | None:
    try:
        return date.fromisoformat((texto or "").strip()[:10])
    except ValueError:
        return None


@dataclass
class CustoCasa:
    """O custo da prateleira de uma casa, medido contra as categorias dela.

    `vitrine` e quantos fundos a prateleira tem; `fonte` diz de onde ela saiu -
    a vitrine do site ou, para a XP, a base interna.
    """

    platform_id: str
    label: str
    vitrine: int
    fundos: list[CustoFundo] = field(default_factory=list)
    fonte: str = FONTE_VITRINE

    @property
    def comparaveis(self) -> list[CustoFundo]:
        return [f for f in self.fundos if f.indice is not None]

    def por_classe(self) -> list[dict]:
        """O custo da casa em cada classe de fundo, na ordem de `ORDEM_DAS_CLASSES`.

        A mesma conta da casa inteira - mediana dos indices, nao das taxas -
        dentro de cada classe. A taxa mediana vai junto como referencia: dentro
        da classe o mix pesa menos, mas ainda pesa (renda fixa simples contra
        credito livre), e por isso o indice continua sendo o numero. Classe com
        menos de `_MIN_COMPARAVEIS_CLASSE` comparaveis sai sem indice.
        """
        grupos: dict[str, list[CustoFundo]] = {}
        for fundo in self.fundos:
            grupos.setdefault(fundo.classe or classe_grossa(None), []).append(fundo)
        saida = []
        for classe in sorted(grupos, key=_ordem_da_classe):
            fundos = grupos[classe]
            comparaveis = [f for f in fundos if f.indice is not None]
            medida = len(comparaveis) >= _MIN_COMPARAVEIS_CLASSE

            def mediana(valores: list[float], casas: int) -> float | None:
                return round(statistics.median(valores), casas) if medida else None

            def share(selecao) -> float | None:
                if not medida:
                    return None
                return round(sum(1 for f in comparaveis if selecao(f)) / len(comparaveis), 4)

            saida.append({
                "classe": classe,
                "fundos": len(fundos),
                "comparaveis": len(comparaveis),
                # Duas casas, como se escreve: com tres, 1,515 saia 1,51x no
                # digest (Python) e 1,52x no painel (navegador).
                "indice_mediano": mediana([f.indice for f in comparaveis], 2),
                "taxa_mediana": mediana([f.taxa_adm for f in comparaveis], 4),
                "share_caro": share(lambda f: f.indice >= _CARO),
                "share_barato": share(lambda f: f.indice <= _BARATO),
            })
        return saida

    @property
    def cobertura(self) -> float:
        return len(self.comparaveis) / self.vitrine if self.vitrine else 0.0

    @property
    def indice_mediano(self) -> float | None:
        """Mediana dos indices, nao das taxas.

        E o que remove o efeito de composicao: cada fundo ja foi comparado com a
        propria categoria antes de a casa ser resumida num numero.
        """
        indices = [f.indice for f in self.comparaveis]
        return statistics.median(indices) if indices else None

    def _share(self, selecao, base: list[CustoFundo] | None = None) -> float | None:
        universo = base if base is not None else self.fundos
        conhecidos = [f for f in universo if selecao(f) is not None]
        if not conhecidos:
            return None
        return sum(1 for f in conhecidos if selecao(f)) / len(conhecidos)

    @property
    def share_caro(self) -> float | None:
        comparaveis = self.comparaveis
        if not comparaveis:
            return None
        return sum(1 for f in comparaveis if f.indice >= _CARO) / len(comparaveis)

    @property
    def share_barato(self) -> float | None:
        comparaveis = self.comparaveis
        if not comparaveis:
            return None
        return sum(1 for f in comparaveis if f.indice <= _BARATO) / len(comparaveis)

    @property
    def share_sem_entrada_ou_saida(self) -> float | None:
        def livre(f: CustoFundo) -> bool | None:
            if f.tem_taxa_entrada is None or f.tem_taxa_saida is None:
                return None
            return f.tem_taxa_entrada != "S" and f.tem_taxa_saida != "S"

        return self._share(livre)

    @property
    def share_liquidez_imediata(self) -> float | None:
        return self._share(lambda f: f.liquidez_imediata)

    @property
    def com_porta_menor(self) -> list[CustoFundo]:
        return sorted(
            [f for f in self.fundos if f.porta_menor is not None],
            key=lambda f: -(f.minimo_classe or 0),
        )

    def to_dict(self) -> dict:
        def r(v: float | None, casas: int = 3) -> float | None:
            return None if v is None else round(v, casas)

        return {
            "platform_id": self.platform_id,
            "label": self.label,
            "vitrine": self.vitrine,
            "fonte": self.fonte,
            "comparaveis": len(self.comparaveis),
            "cobertura": r(self.cobertura),
            # Duas casas, como se escreve (ver `por_classe`): com tres, 1,625
            # saia 1,62x no digest e 1,63x no painel.
            "indice_mediano": r(self.indice_mediano, 2),
            "share_caro": r(self.share_caro, 4),
            "share_barato": r(self.share_barato, 4),
            "share_sem_entrada_ou_saida": r(self.share_sem_entrada_ou_saida, 4),
            "share_liquidez_imediata": r(self.share_liquidez_imediata, 4),
            "com_porta_menor": len(self.com_porta_menor),
            "por_classe": self.por_classe(),
        }


@dataclass
class PrateleiraInterna:
    """A prateleira da casa-base pela base interna (a lista distribuivel).

    `motivo` diz por que ela nao entrou, quando nao entrou - e a casa volta a
    vitrine do site. `ofertas` sao os fundos da lista que a casa distribui;
    `caixa`, quantos deles o `sem_caixa` tirou depois, como tira das vitrines.
    """

    platform_id: str
    motivo: str | None = None
    ofertas: list[FundOffer] = field(default_factory=list)
    exclusivas: int = 0
    so_capacidade: int = 0
    data_referencia: date | None = None
    caixa: int = 0

    @property
    def usada(self) -> bool:
        return bool(self.ofertas)

    @property
    def entram(self) -> int:
        """Os fundos da base interna que entram no custo."""
        return len(self.ofertas) - self.caixa

    def nota(self) -> str:
        """A frase que toda superficie escreve ao lado do custo por casa."""
        rotulo = _platform_label(self.platform_id)
        if not self.usada:
            return (
                f"{rotulo} pela vitrine do site, como as demais casas: a base interna "
                f"não entrou - {self.motivo}."
            )
        lista = "lista distribuível" + (
            f" de {data_br(self.data_referencia)}" if self.data_referencia else ""
        )
        fora = []
        if self.exclusivas:
            fora.append(
                contagem(self.exclusivas, "classe exclusiva", "classes exclusivas")
                + " (veículo de um cotista, não produto de prateleira)"
            )
        if self.caixa:
            fora.append(contagem(self.caixa, "fundo caixa/simples", "fundos caixa/simples"))
        if self.so_capacidade:
            fora.append(
                contagem(self.so_capacidade, "fundo", "fundos")
                + " só com capacidade de distribuir"
            )
        if len(fora) > 1:
            fora = [", ".join(fora[:-1]) + " e " + fora[-1]]
        return (
            f"{rotulo} pela base interna ({lista}): entram "
            f"{contagem(self.entram, 'fundo', 'fundos')} que a casa distribui."
            + (f" Ficam fora {fora[0]}." if fora else "")
            + " As demais casas, pela vitrine do site."
        )

    def to_dict(self) -> dict:
        return {
            "platform_id": self.platform_id,
            "fonte": FONTE_BASE_INTERNA if self.usada else FONTE_VITRINE,
            "motivo": self.motivo,
            "fundos": self.entram,
            "exclusivas": self.exclusivas,
            "caixa": self.caixa,
            "so_capacidade": self.so_capacidade,
            "data_referencia": (
                self.data_referencia.isoformat() if self.data_referencia else None
            ),
        }


def _casa_base() -> str:
    return next((p.platform_id for p in PLATFORMS.values() if p.is_baseline), "xp")


def classes_com_indice(casas: list[dict]) -> list[str]:
    """As classes com indice em ao menos uma das casas, na ordem do eixo.

    Recebe as casas ja serializadas (`CustoCasa.to_dict`), para servir tanto ao
    bloco inteiro quanto ao recorte do digest, que tem menos casas.
    """
    return sorted(
        {
            c["classe"] for casa in casas for c in casa.get("por_classe") or []
            if c.get("indice_mediano") is not None
        },
        key=_ordem_da_classe,
    )


def nota_das_classes(casas: list[dict]) -> str:
    """O que a quebra por classe nao mostra, e por que - uma frase."""
    mostradas = set(classes_com_indice(casas))
    fora = sorted(
        {c["classe"] for casa in casas for c in casa.get("por_classe") or []} - mostradas,
        key=_ordem_da_classe,
    )
    texto = (
        f"Classe é o nível 1 da ANBIMA. Com menos de {_MIN_COMPARAVEIS_CLASSE} "
        "fundos comparáveis na casa, a classe fica sem índice."
    )
    if fora:
        nomes = ", ".join(fora[:-1]) + " e " + fora[-1] if len(fora) > 1 else fora[0]
        texto += (
            f" Sem {_MIN_COMPARAVEIS_CLASSE} comparáveis em nenhuma casa, fora da "
            f"tabela: {nomes}."
        )
    return texto


def prateleira_interna(distribuivel, indice: IndiceCusto, platform_id: str) -> PrateleiraInterna:
    """Os fundos da base interna que entram no custo da casa-base.

    Entram os de situacao "distribui". "Pode distribuir" ainda nao esta na
    prateleira, e classe exclusiva e veiculo de um cotista. Publico
    profissional fica, como fica nas vitrines (ver o docstring do modulo).
    Fundo fora do cadastro entra, e o `avaliar` o lista com o motivo.
    """
    interna = PrateleiraInterna(platform_id=platform_id)
    motivo = distribuivel.motivo_da_supressao()
    if motivo:
        interna.motivo = motivo
        return interna
    interna.data_referencia = distribuivel.data_referencia
    for cnpj in distribuivel.cnpjs():
        if distribuivel.situacao_de(cnpj) != DISTRIBUI:
            interna.so_capacidade += 1
            continue
        classe = indice.get(cnpj)
        if classe is not None and classe.exclusivo:
            interna.exclusivas += 1
            continue
        nome = (classe.nome if classe else "") or format_cnpj(cnpj)
        oferta = FundOffer(platform=platform_id, name=nome, cnpj=cnpj)
        # A categoria do cadastro vai na oferta, como o enriquecimento faz com
        # a da vitrine: e o que `caixa.oferta_caixa` le. Sem ela, todo fundo da
        # lista passava como "nao caixa", e a XP saia mais barata do que e.
        if classe is not None and classe.categoria:
            oferta.extra[KEY_CATEGORIA] = classe.categoria
        interna.ofertas.append(oferta)
    if not interna.ofertas:
        interna.motivo = "a lista não traz fundo que a casa distribua fora das classes exclusivas"
    return interna


def build_custo(
    config,
    snapshots: list[ShelfSnapshot],
    classe_indice: type[IndiceCusto] = IndiceCusto,
    distribuivel=None,
) -> dict:
    """Custo da prateleira de cada casa, relativo a categoria de cada fundo.

    `classe_indice` monta o universo de referencia. O modulo de previdencia
    passa o dele (`previdencia.custo.IndiceCustoPrevidencia`), em que a
    classe exclusiva de previdencia serve de referencia - todo FIE e
    exclusivo, e sem isso nenhuma categoria de previdencia tinha mediana.

    `distribuivel` e a lista distribuivel (`RunReport.distribuivel`): com ela,
    a prateleira da XP sai da base interna, e a das demais casas, da vitrine.
    None (a previdencia nao le a lista) deixa todas na vitrine, sem nota.
    """
    indice = classe_indice.load(config.market_dir)
    if indice is None:
        return {
            "available": False,
            "reason": (
                "base de mercado não importada - o custo comparável sai do "
                "extrato da CVM, não do que a casa publica"
            ),
            "excluded": [],
        }

    vitrines, fora = vitrine_snapshots(snapshots)
    cruzaveis, sem_identidade = identifiable_vitrines(vitrines)
    sem_cnpj = [
        {**item, "reason": (
            "sem CNPJ na vitrine não há como buscar a taxa da classe no extrato "
            f"({item['reason']})"
        )}
        for item in sem_identidade
    ]
    # A XP pela base interna (pedido de 16/10/2026): a vitrine dela, se veio,
    # da lugar a lista - e o que ficou de fora por falta de vitrine deixa de
    # valer para ela.
    interna = None
    if distribuivel is not None:
        base_id = _casa_base()
        interna = prateleira_interna(distribuivel, indice, base_id)
        if interna.usada:
            cruzaveis = {
                base_id: interna.ofertas,
                **{pid: funds for pid, funds in cruzaveis.items() if pid != base_id},
            }
            fora = [e for e in fora if e.get("platform_id") != base_id]
            sem_cnpj = [e for e in sem_cnpj if e.get("platform_id") != base_id]
    if not cruzaveis:
        return {
            "available": False,
            "reason": "nenhuma vitrine publica CNPJ o bastante para buscar o extrato",
            "excluded": fora + sem_cnpj,
        }
    # Fundo caixa/simples fica fora (pedido de 16/10/2026, ver
    # shelf_monitor.caixa): produto padrao, de taxa baixa por natureza. Roda
    # depois da troca da XP pela base interna, para a regra valer igual em
    # todas as casas - e a nota da XP diz quantos dela sairam.
    cruzaveis, caixa_fora = sem_caixa(cruzaveis)
    if interna is not None and interna.usada:
        interna.caixa = len(interna.ofertas) - len(cruzaveis.get(interna.platform_id, ()))
    # O fundo de previdencia sai do mesmo jeito (17/10/2026, ver
    # shelf_monitor.previdencia_fora); o monitor de previdencia o mantem.
    cruzaveis, previdencia_fora = sem_previdencia(cruzaveis, config)

    casas: list[CustoCasa] = []
    sem_amostra: list[dict] = []
    for platform_id, funds in cruzaveis.items():
        da_base = interna is not None and interna.usada and platform_id == interna.platform_id
        casa = CustoCasa(
            platform_id=platform_id,
            label=_platform_label(platform_id),
            vitrine=len(funds),
            fundos=[indice.avaliar(f) for f in funds],
            fonte=FONTE_BASE_INTERNA if da_base else FONTE_VITRINE,
        )
        if casa.cobertura < _MIN_COBERTURA:
            sem_amostra.append({
                "platform_id": platform_id,
                "label": casa.label,
                "reason": (
                    f"apenas {len(casa.comparaveis)} de {casa.vitrine} fundos com "
                    "taxa comparável (percentual, entrega recente e categoria com "
                    "referência) - o índice descreveria a amostra, não a "
                    + ("prateleira da base interna" if da_base else "vitrine")
                ),
            })
            continue
        casas.append(casa)

    if not casas:
        return {
            "available": False,
            "reason": "nenhuma vitrine com cobertura suficiente de taxa comparável",
            "excluded": fora + sem_cnpj + sem_amostra,
        }

    motivos: dict[str, int] = {}
    for casa in casas:
        for fundo in casa.fundos:
            if fundo.motivo_fora:
                motivos[fundo.motivo_fora] = motivos.get(fundo.motivo_fora, 0) + 1

    serializadas = [c.to_dict() for c in casas]
    return {
        "available": True,
        "participantes": [c.platform_id for c in casas],
        "excluded": fora + sem_cnpj + sem_amostra,
        "universo": {
            "classes_na_base": len(indice),
            "categorias_com_referencia": len(indice.medianas),
            "amostra_minima": _MIN_AMOSTRA_CATEGORIA,
            "idade_maxima_meses": _MAX_IDADE_MESES,
        },
        "motivos_fora": dict(sorted(motivos.items(), key=lambda kv: -kv[1])),
        "caixa_fora": caixa_fora,
        "previdencia_fora": previdencia_fora,
        # De onde sai a prateleira da XP, e a frase que toda superficie escreve.
        "prateleira_da_base": interna.to_dict() if interna is not None else None,
        "nota_prateleira": interna.nota() if interna is not None else "",
        # A quebra por classe: as colunas que tem indice e a nota do que ficou fora.
        "classes": classes_com_indice(serializadas),
        "nota_classes": nota_das_classes(serializadas),
        "casas": serializadas,
        "mais_caros": [
            f.to_dict()
            for f in sorted(
                (f for c in casas for f in c.comparaveis), key=lambda f: -f.indice
            )[:20]
        ],
        "porta_menor": {
            c.platform_id: [f.to_dict() for f in c.com_porta_menor[:15]] for c in casas
        },
        "_casas": casas,
    }


# --- renderizacao no console --------------------------------------------------


def print_custo(resultado: dict) -> None:
    """Imprime o indice de custo por casa, com quem ficou de fora e por que."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print()
    console.print(
        "[bold]Custo da vitrine[/] - taxa de cada fundo contra a mediana da "
        "categoria dele"
    )
    if not resultado.get("available"):
        console.print(f"   [yellow]{resultado.get('reason')}[/]")
        for e in resultado.get("excluded", []):
            console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
        return

    universo = resultado["universo"]
    console.print(
        f"   [dim]{milhar(universo['classes_na_base'])} classes na base, "
        f"{universo['categorias_com_referencia']} categorias com amostra "
        f">= {universo['amostra_minima']}. Entram só taxas em percentual ao ano "
        f"com entrega de até {universo['idade_maxima_meses']} meses.[/]"
    )

    def pct(valor: float | None) -> str:
        return "-" if valor is None else f"{valor:.0%}"

    table = Table(title="Índice de custo (1,00 = mediana da categoria)", title_style="bold")
    table.add_column("Casa")
    table.add_column("Comparáveis", justify="right")
    table.add_column("Índice mediano", justify="right")
    table.add_column("Caros", justify="right")
    table.add_column("Baratos", justify="right")
    table.add_column("Sem entrada/saída", justify="right")
    table.add_column("Resgate <= D+1", justify="right")
    table.add_column("Com porta menor", justify="right")
    for casa in resultado["casas"]:
        indice = casa["indice_mediano"]
        table.add_row(
            casa["label"],
            f"{casa['comparaveis']}/{casa['vitrine']}",
            "-" if indice is None else f"{indice:.2f}x".replace(".", ","),
            pct(casa["share_caro"]),
            pct(casa["share_barato"]),
            pct(casa["share_sem_entrada_ou_saida"]),
            pct(casa["share_liquidez_imediata"]),
            str(casa["com_porta_menor"]),
        )
    console.print(table)
    console.print(
        "   [dim]Índice mediano, não taxa mediana: comparar a taxa bruta entre "
        "casas mede o MIX da prateleira, não o preço dos produtos.[/]"
    )
    if resultado.get("nota_prateleira"):
        console.print(f"   [dim]{resultado['nota_prateleira']}[/]")

    classes = resultado.get("classes") or []
    if classes:
        por_classe = Table(title="Índice por classe (fundos comparáveis)", title_style="bold")
        por_classe.add_column("Casa")
        for classe in classes:
            por_classe.add_column(classe, justify="right")
        for casa in resultado["casas"]:
            celulas = {c["classe"]: c for c in casa.get("por_classe") or []}
            linha = [casa["label"]]
            for classe in classes:
                celula = celulas.get(classe)
                if celula is None:
                    linha.append("-")
                    continue
                indice = celula["indice_mediano"]
                texto = "-" if indice is None else f"{indice:.2f}x".replace(".", ",")
                linha.append(f"{texto} ({celula['comparaveis']})")
            por_classe.add_row(*linha)
        console.print(por_classe)
        console.print(f"   [dim]{resultado.get('nota_classes', '')}[/]")
    if resultado.get("motivos_fora"):
        console.print("   [dim]fundos fora do índice, por motivo:[/]")
        for motivo, quantos in resultado["motivos_fora"].items():
            console.print(f"      [dim]{quantos:5d}  {motivo}[/]")
    for e in resultado.get("excluded", []):
        console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")
