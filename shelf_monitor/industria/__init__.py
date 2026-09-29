"""Monitor da industria de fundos: a visao de mercado para a distribuicao.

O resto do pacote olha o mercado a partir de uma casa (a vitrine da XP e dos
concorrentes, os ETFs, a previdencia das plataformas). Este modulo olha a
industria inteira, sem casa em foco, e responde as perguntas de quem distribui
fundo: o que esta de fato disponivel para PF e PJ, para onde vai o dinheiro,
quem ganha, por qual canal e a que preco.

Tres ideias sustentam os numeros (a especificacao completa esta em
`docs/spec-industria-fundos.md`):

- **Universo disponivel.** Cada produto (subclasse, ou a classe quando ela nao
  tem subclasse) recebe o primeiro motivo de exclusao que se aplicar -
  previdencia, exclusivo, master, institucional, restrito... - e o que sobra e
  o que um cliente PF/PJ pode comprar. O funil mostra cada degrau.
- **Camada do investidor.** Uma parte grande do PL e de outros fundos (43,5%
  dos nao exclusivos em 08/2026): somar tudo conta o mesmo dinheiro duas
  vezes. O perfil mensal da CVM diz a fatia de cada classe em maos de fundos, e
  toda soma do monitor e so do dinheiro de quem nao e fundo.
- **Estrategia pela raiz.** Feeder e espelho herdam estrategia, gestora e
  rentabilidade do master; o veiculo fica como segunda dimensao, que e a que
  mostra quem distribui o que.

A politica de universo e deste modulo: publico profissional entra (como
filtro), ao contrario da vitrine (`profile.restriction_reason`), que o exclui.
"""
