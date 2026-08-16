# Plano de distribuição do SmartEvents com Inno Setup

## Objetivo

Criar um instalador confiável do SmartEvents para distribuição aos operadores, com os eventos que possuem `RoadShow` no nome e o cliente `TIM` pré-cadastrados.

A meta é garantir o funcionamento em todas as máquinas pertencentes ao ambiente homologado: Windows 10 ou Windows 11 x64, com permissão administrativa, VPN e políticas corporativas compatíveis.

O Inno Setup será utilizado para instalar e validar o aplicativo, mas a geração do instalador deverá ser precedida pela estabilização do executável. Apenas colocar o pacote atual dentro de um instalador poderia reproduzir o erro de carregamento do `pythonnet`.

## 1. Estabilização do executável

- Recriar o ambiente de compilação usando Python 3.12 x64, mais consolidado para uso com PyInstaller e `pythonnet`.
- Fixar versões exatas de todas as dependências utilizadas no build.
- Criar inicialmente um executável mínimo que apenas abra uma janela do `pywebview`.
- Testar esse executável no computador que apresentou o erro de carregamento da `Python.Runtime.dll`.
- Somente depois da aprovação desse teste gerar o SmartEvents completo.
- Manter um arquivo de versões do ambiente utilizado para que futuros builds sejam reproduzíveis.

## 2. Preparação da aplicação para instalação

- Instalar os binários do programa em `C:\Program Files\SmartEvents`.
- Armazenar credenciais, banco local, sessões, configurações e logs em `%LOCALAPPDATA%\SmartEvents`.
- Fornecer como sementes somente:
  - os eventos cujo nome contém `RoadShow`;
  - o cliente `TIM` e suas informações relacionadas.
- Na primeira execução, copiar as sementes para a pasta de dados do operador.
- Em atualizações, nunca sobrescrever credenciais, sessões ou alterações já realizadas pelo operador.
- Continuar incluindo os dois navegadores do Playwright:
  - `chromium_headless_shell`, utilizado na renovação automática da sessão;
  - `chromium`, utilizado no login visual e em situações com CAPTCHA.

## 3. Diagnóstico interno

Adicionar ao executável um modo de diagnóstico, por exemplo `main.exe --self-test`, que verifique:

- carregamento do .NET Framework e do `pythonnet`;
- carregamento da `Python.Runtime.dll`;
- inicialização do `pywebview`;
- disponibilidade do Microsoft Edge WebView2 Runtime;
- inicialização do Chromium headless;
- possibilidade de gravação na pasta de dados do usuário;
- criação e leitura do banco local;
- integridade das sementes;
- presença dos eventos RoadShow;
- presença do cliente TIM.

O instalador deverá executar esse diagnóstico ao final. Em caso de falha, deverá mostrar uma mensagem compreensível e gerar um relatório técnico, evitando apresentar apenas um traceback ao operador.

## 4. Instalador Inno Setup

Utilizar o Inno Setup 7 x64 com as seguintes características:

- permitir instalação somente em Windows x64;
- solicitar privilégios administrativos;
- instalar o aplicativo em `C:\Program Files\SmartEvents`;
- criar atalhos no menu Iniciar e, opcionalmente, na área de trabalho;
- fornecer desinstalador;
- permitir atualização sobre a versão anterior;
- fechar o SmartEvents antes de substituir arquivos em uso;
- usar compressão LZMA2;
- gerar log detalhado da instalação;
- validar espaço em disco antes de iniciar;
- executar o diagnóstico interno ao final;
- oferecer a opção de abrir o SmartEvents após uma instalação bem-sucedida.

O Inno Setup oferece suporte específico para instaladores e instalações x64. Consulte a [documentação oficial do modo 64 bits](https://jrsoftware.org/ishelp/topic_64bit.htm).

## 5. Pré-requisitos

### .NET Framework

- Exigir .NET Framework 4.8 ou superior.
- Verificar no Registro do Windows se o valor `Release` é maior ou igual a `528040`.
- Caso esteja ausente, executar o instalador offline oficial do .NET Framework.
- Se a instalação exigir reinicialização, informar isso claramente e continuar a instalação depois do reinício.

A Microsoft documenta o valor mínimo necessário para identificar o .NET Framework 4.8 em [Como determinar quais versões do .NET Framework estão instaladas](https://learn.microsoft.com/pt-br/dotnet/framework/install/how-to-determine-which-versions-are-installed).

### Microsoft Edge WebView2 Runtime

- Detectar se o WebView2 Runtime está disponível.
- Se estiver ausente, executar silenciosamente o Evergreen Standalone Installer x64 oficial.
- Verificar novamente a disponibilidade depois da instalação.

A Microsoft documenta a instalação silenciosa e a distribuição offline em [Distribuir seu aplicativo e o WebView2 Runtime](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution?form=MA13LM).

### Estratégia offline

Para maior confiabilidade, incluir dentro do instalador:

- redistribuível oficial do .NET Framework 4.8;
- Evergreen Standalone Installer x64 do WebView2 Runtime;
- todos os arquivos do pacote ONEDIR do SmartEvents;
- os dois navegadores do Playwright e seus auxiliares.

Isso aumenta o tamanho do instalador, mas evita depender da internet durante a instalação.

## 6. Assinatura e integridade

Preferencialmente, assinar digitalmente:

- `main.exe`;
- `Setup_SmartEvents_RoadShow.exe`.

A assinatura reduz alertas do Microsoft Defender SmartScreen e facilita a aprovação em ambientes corporativos. Sem um certificado válido, ainda poderá aparecer o aviso de publicador desconhecido.

Também deverão ser gerados:

- hash SHA-256 do instalador;
- número da versão;
- manifesto das versões das dependências;
- log do processo de compilação.

## 7. Homologação

O instalador deverá ser testado, preferencialmente em máquinas virtuais limpas, nos seguintes cenários:

1. Windows 10 x64 homologado.
2. Windows 11 x64 homologado.
3. Máquina sem WebView2 Runtime.
4. Máquina com versão antiga ou ausente do .NET Framework necessário.
5. Instalação após transferência por WeLink, e-mail ou ferramenta corporativa.
6. Instalação offline.
7. Reinstalação da mesma versão.
8. Atualização sobre uma versão anterior.
9. Reinicialização do computador após a instalação.
10. Desinstalação e reinstalação.
11. Preservação dos dados do operador durante uma atualização.
12. Login automático.
13. Login visual e tratamento de CAPTCHA.
14. Renovação automática da sessão usando o Chromium headless.
15. Abertura da interface pelo WebView2.
16. Coleta real com a VPN conectada.
17. Presença exclusiva dos eventos RoadShow e do cliente TIM na primeira execução.

## 8. Critérios de aceite

O instalador será considerado pronto quando:

- concluir a instalação sem exigir ações técnicas do operador;
- instalar ou validar automaticamente os pré-requisitos;
- abrir a interface sem erros de `pythonnet`, .NET ou WebView2;
- passar no diagnóstico interno;
- permitir login automático e visual;
- iniciar os navegadores do Playwright corretamente;
- apresentar os eventos RoadShow e o cliente TIM pré-cadastrados;
- preservar credenciais e dados nas atualizações;
- funcionar nos ambientes Windows 10 e Windows 11 x64 homologados;
- gerar informações suficientes para diagnóstico caso uma política corporativa, antivírus ou VPN impeça o funcionamento.

## 9. Entregáveis

- `Setup_SmartEvents_RoadShow.exe`;
- eventos RoadShow e cliente TIM pré-cadastrados;
- pré-requisitos integrados ou validados automaticamente;
- modo de diagnóstico interno;
- log de instalação;
- hash SHA-256 do instalador;
- manifesto de versões;
- instrução curta para os operadores;
- roteiro e evidências dos testes de homologação.

## Observações sobre tamanho e garantia

O Inno Setup comprimirá a distribuição, mas o tamanho instalado continuará próximo do pacote atual, pois os dois Chromiums são necessários para manter os fluxos automático e visual. A inclusão dos instaladores offline do .NET Framework e do WebView2 também aumentará o tamanho do `Setup.exe`.

Não é possível garantir funcionamento em qualquer computador sem delimitar o ambiente, porque políticas corporativas, antivírus, falta de privilégio administrativo e ausência da VPN podem impedir a execução. A garantia deverá se aplicar ao conjunto de versões e configurações efetivamente homologadas.
