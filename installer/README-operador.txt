SmartEvents RoadShow - instrucao rapida

1. Conecte-se a VPN corporativa quando for realizar login ou coleta.
2. Execute Setup_SmartEvents_RoadShow.exe como administrador.
3. O instalador valida .NET Framework 4.8 e WebView2 automaticamente.
4. Ao final, o diagnostico precisa ser aprovado antes da abertura do aplicativo.
5. Se uma reinicializacao for solicitada, reinicie o Windows; o diagnostico sera executado no proximo logon.

Dados do operador ficam em %LOCALAPPDATA%\SmartEvents e sao preservados em atualizacoes e desinstalacoes.
Em caso de falha, envie os arquivos de %LOCALAPPDATA%\SmartEvents\diagnostics e \logs ao suporte.

Este e o fluxo por PERFIL (um instalador por evento/cliente), mantido como alternativa historica.
O caminho recomendado para distribuicoes novas e "build-base + selecao de eventos" (ver README.md,
secao 6-7). Para conferir se este instalador foi assinado digitalmente: clique com o botao direito
no Setup.exe, Propriedades, aba "Assinaturas Digitais". Sem assinatura, o SmartScreen do Windows
pode exibir um aviso de editor desconhecido; isso nao indica adulteracao, apenas ausencia de
certificado nesta build.


TECNOLOGIA NA EP
Ao importar uma EP nova, preencha a coluna tecnologia em todas as linhas:
4G ou LTE para 4G; 5G ou NR para 5G. O modelo ep_default.xlsx inclui o exemplo 4G.
Exemplos: célula 5G-X com tecnologia 4G continua 4G; célula CELULA-A com NR é 5G.
Banda e DLEARFCN não substituem a tecnologia declarada. Dados ausentes, inválidos
ou duplicidades com tecnologias divergentes rejeitam a importação completa;
consulte as linhas indicadas na mensagem de erro e corrija a EP.
Eventos antigos já salvos continuam disponíveis por fallback legado.
