Meu chefe havia solicitado que fossem gerados clusters automaticamente por portadora, com base no EARFCN (que vem da nova ep).Isso vem de uma solicitação que eu havia feito, que solicitava o seguinte:

"Na aba visão geral de KPI's 4G inserir por padrão os clusters por portadora (EARFCN). Já adicionei o arquivo ep_default, para ter a referência de colunas atualizada e incluí o DLEARFCN lá, que é o que será usado. Em resumo, se existir essa coluna na engineer parameters, o Sistema deverá automaticamente gerar clusters agrupando as células por portadoras."

Isso fez com que essa função fosse implementada (corretamente inclusive)

Depois que eu fiz ele falou o seguinte:
tem que ter essa agregação a nivel de EARFCN por site tambem
quando filtrar o site conseguir ver o balanceamento dele por earfcn.

Não sei exatamente qual seria a melhor forma de fazer isso. Pode me ajudar trazendo as principais formas e qual você consideraria a melhor?