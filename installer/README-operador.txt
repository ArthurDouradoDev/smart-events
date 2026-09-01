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
