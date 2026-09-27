"""Area bruta do extrator: gravacao atomica, CSV/JSON e o manifesto da execucao.

Toda escrita passa por um `.part` trocado no fim (`os.replace`): uma captura
interrompida no meio - rede caida num backfill de um ano de pregoes - nao pode
deixar meio arquivo no lugar de um inteiro, porque a proxima execucao pula o
que ja esta em disco e o buraco viraria permanente.

O manifesto registra, por execucao, o que foi pedido, o estado de cada fonte e
cada arquivo gravado com sha256, tamanho, linhas e a URL de origem. Nunca leva
token da B3 nem credencial da Comdinheiro.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Sequence

# Planilha salva pelo Excel em pt-BR sai em cp1252 quando a pessoa escolhe
# "CSV (separado por virgulas)"; utf-8-sig primeiro porque cp1252 decodifica
# quase qualquer byte e mascararia um UTF-8 bom.
_ENCODINGS_DE_LEITURA = ("utf-8-sig", "cp1252")


def sha256_de(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def gravar_atomico(caminho: Path, conteudo: bytes) -> str:
    """Grava `conteudo` em `caminho` via `.part`; devolve o sha256."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(caminho.name + ".part")
    try:
        temporario.write_bytes(conteudo)
        os.replace(temporario, caminho)
    finally:
        temporario.unlink(missing_ok=True)
    return sha256_de(conteudo)


def csv_em_bytes(cabecalho: Sequence[str], linhas: Iterable[Sequence | dict]) -> tuple[bytes, int]:
    """CSV `;` em utf-8-sig, como todo CSV do monitor; devolve (bytes, linhas)."""
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    escritor.writerow(list(cabecalho))
    total = 0
    for linha in linhas:
        if isinstance(linha, dict):
            escritor.writerow(["" if linha.get(c) is None else linha.get(c) for c in cabecalho])
        else:
            escritor.writerow(["" if v is None else v for v in linha])
        total += 1
    return buffer.getvalue().encode("utf-8-sig"), total


def gravar_csv(caminho: Path, cabecalho: Sequence[str], linhas: Iterable[Sequence | dict]) -> tuple[str, int]:
    conteudo, total = csv_em_bytes(cabecalho, linhas)
    return gravar_atomico(caminho, conteudo), total


def json_em_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def gravar_json(caminho: Path, payload: object) -> str:
    return gravar_atomico(caminho, json_em_bytes(payload))


def ler_json(caminho: Path, padrao: object = None) -> object:
    if not caminho.exists():
        return padrao
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def ler_csv(caminho: Path) -> list[dict]:
    """Linhas de um CSV `;`, em utf-8-sig ou cp1252 (o que o Excel grava)."""
    if not caminho.exists():
        return []
    conteudo = caminho.read_bytes()
    for encoding in _ENCODINGS_DE_LEITURA:
        try:
            texto = conteudo.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 so falha em 5 bytes indefinidos
        texto = conteudo.decode("latin-1")
    return [dict(linha) for linha in csv.DictReader(io.StringIO(texto), delimiter=";")]


def cabecalho_csv(caminho: Path) -> list[str]:
    """Nomes das colunas de um CSV ja gravado (vazio quando nao existe)."""
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        primeira = handle.readline()
    return next(csv.reader([primeira], delimiter=";"), [])


# --- manifesto ----------------------------------------------------------------


@dataclass
class Manifesto:
    """O que uma execucao pediu, o que cada fonte respondeu e o que foi gravado."""

    inicio: datetime
    pedido: dict
    base: Path | None = None
    fontes: dict[str, dict] = field(default_factory=dict)
    arquivos: list[dict] = field(default_factory=list)
    ocorrencias: list[dict] = field(default_factory=list)

    def _relativo(self, caminho: Path | str) -> str:
        caminho = Path(caminho)
        if self.base is not None:
            try:
                return caminho.resolve().relative_to(self.base.resolve()).as_posix()
            except ValueError:
                pass
        return caminho.as_posix()

    def arquivo(self, fonte: str, caminho: Path, **extra) -> None:
        item = {"fonte": fonte, "caminho": self._relativo(caminho)}
        item.update({chave: valor for chave, valor in extra.items() if valor is not None})
        self.arquivos.append(item)

    def ocorrencia(self, fonte: str, referencia: str, motivo: str) -> None:
        self.ocorrencias.append({"fonte": fonte, "referencia": referencia, "motivo": motivo})

    def registrar_fonte(self, fonte: str, estado: str, detalhe: str, **extra) -> None:
        item = {"estado": estado, "detalhe": detalhe}
        item.update({chave: valor for chave, valor in extra.items() if valor is not None})
        self.fontes[fonte] = item

    def to_dict(self) -> dict:
        return {
            "inicio": self.inicio.isoformat(timespec="seconds"),
            "fim": datetime.now().isoformat(timespec="seconds"),
            "pedido": self.pedido,
            "fontes": self.fontes,
            "arquivos": self.arquivos,
            "ocorrencias": self.ocorrencias,
        }

    def gravar(self, pasta: Path) -> Path:
        # Sem ":" no nome: o Windows nao aceita.
        caminho = pasta / f"{self.inicio:%Y-%m-%dT%H%M%S}.json"
        gravar_json(caminho, self.to_dict())
        return caminho


# --- pasta de entrada ------------------------------------------------------------

_COLUNAS_DO_REGISTRO = ("sha256", "arquivo_original", "copiado_para", "bytes", "registrado_em")


def registrar_entradas(
    origem: Path,
    destino: Path,
    dia: date,
    manifesto: Manifesto,
    fonte: str,
) -> list[Path]:
    """Copia para a area bruta cada arquivo novo da pasta de entrada.

    Novo e por conteudo (sha256), nao por nome: a mesma exportacao salva duas
    vezes com nomes diferentes entra uma vez so, e um arquivo refeito com o
    mesmo nome e dado novo entra de novo. A pasta de entrada nao e tocada - e
    da pessoa.
    """
    if not origem.exists():
        return []
    registro_path = destino / "_registro.csv"
    registro = ler_csv(registro_path)
    vistos = {linha.get("sha256") for linha in registro}
    novos: list[Path] = []
    for arquivo in sorted(p for p in origem.rglob("*") if p.is_file()):
        # "~$x.xlsx" e a trava que o Excel cria com a planilha aberta.
        if arquivo.name.startswith(("~$", ".")):
            continue
        conteudo = arquivo.read_bytes()
        sha = sha256_de(conteudo)
        if sha in vistos:
            continue
        alvo = destino / dia.isoformat() / arquivo.relative_to(origem)
        if alvo.exists():
            alvo = alvo.with_name(f"{alvo.stem}-{sha[:8]}{alvo.suffix}")
        gravar_atomico(alvo, conteudo)
        vistos.add(sha)
        registro.append({
            "sha256": sha,
            "arquivo_original": arquivo.relative_to(origem).as_posix(),
            "copiado_para": alvo.relative_to(destino).as_posix(),
            "bytes": len(conteudo),
            "registrado_em": datetime.now().isoformat(timespec="seconds"),
        })
        manifesto.arquivo(fonte, alvo, origem=str(arquivo), sha256=sha, bytes=len(conteudo), acao="copiado da entrada")
        novos.append(alvo)
    if novos:
        gravar_csv(registro_path, _COLUNAS_DO_REGISTRO, registro)
    return novos
