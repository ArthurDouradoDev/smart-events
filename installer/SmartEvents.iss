; SmartEvents - instalador.
;
; Dois caminhos usam este mesmo arquivo:
;
;   Fase 3 (padrao) - `build.py distribution --format setup` combina um BUILD-BASE
;   ja compilado com um pacote `.sepack`. O PyInstaller nao roda: o programa vem do
;   cache e so o Setup e recompilado. MyEventPackage e MyExpectedEventIds sao
;   definidos, o AppId e o estavel e os dados ficam em %LOCALAPPDATA%\SmartEvents.
;
;   Legado - `build.py legacy-profile` continua gerando um instalador por perfil,
;   com AppId e pasta de dados proprios. Sem MyEventPackage o wrapper simplesmente
;   nao tem etapa de importacao, e o comportamento e exatamente o anterior.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyAppName
  #define MyAppName "SmartEvents"
#endif
#ifndef MyAppId
  #define MyAppId "72D1D22D-A46F-42C4-8D5C-F82435BDD0AE"
#endif
#ifndef MyInstallDirName
  #define MyInstallDirName "SmartEvents"
#endif
#ifndef MyDataDirName
  #define MyDataDirName "SmartEvents"
#endif
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename "Setup_SmartEvents_RoadShow"
#endif
#ifndef MySourceDir
  #define MySourceDir "..\dist\SmartEvents"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\dist\installer"
#endif
#ifndef MyPrerequisitesDir
  #define MyPrerequisitesDir "prerequisites"
#endif
#ifndef MyIconFile
  #define MyIconFile "..\assets\logoSmartEvents.ico"
#endif

#define MyAppPublisher "SmartEvents"
#define MyAppExeName "SmartEvents.exe"

[Setup]
AppId={{{#MyAppId}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyInstallDirName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBaseFilename}
SetupIconFile={#MyIconFile}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
LZMANumBlockThreads=4
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
CloseApplicationsFilter={#MyAppExeName}
; A associacao do .sepack so existe quando a distribuicao carrega um pacote.
#ifdef MyEventPackage
ChangesAssociations=yes
#else
ChangesAssociations=no
#endif
ExtraDiskSpaceRequired=700000000
RestartIfNeededByRun=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
SolidCompression=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
; Copia recursiva do build-base: cada arquivo entra no log de desinstalacao, e e
; esse registro que faz atualizacao e uninstall nao deixarem orfaos.
;
; O payload pre-comprimido (`SmartEvents-base.zip` + `extractarchive`) foi
; prototipado e REPROVADO: o Inno Setup exige a flag `external` junto de
; `extractarchive`, ou seja, o ZIP teria de viajar FORA do Setup.exe. Isso quebra
; o requisito de o destinatario receber um unico arquivo autocontido. O custo
; disso e recomprimir o bundle a cada distribuicao; o ganho da fase (nao rodar o
; PyInstaller) continua intacto.
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef MyEventPackage
; O pacote fica ao lado do programa: a reinstalacao e o retry pos-reboot precisam
; do mesmo arquivo, e o operador consegue reimportar sem o Setup.
Source: "{#MyEventPackage}"; DestDir: "{app}\eventos"; Flags: ignoreversion
#endif
Source: "{#MyPrerequisitesDir}\NDP48-x86-x64-AllOS-ENU.exe"; Flags: dontcopy
Source: "{#MyPrerequisitesDir}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Flags: dontcopy

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

#ifdef MyEventPackage
[Registry]
; Associacao do .sepack: duplo clique importa o pacote sem reinstalar o programa.
; HKA respeita o escopo da instalacao (HKLM com admin, HKCU sem) e `uninsdeletekey`
; remove SOMENTE as chaves da associacao — nunca os dados do operador.
Root: HKA; Subkey: "Software\Classes\.sepack"; ValueType: string; ValueName: ""; ValueData: "SmartEvents.EventPackage"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\SmartEvents.EventPackage"; ValueType: string; ValueName: ""; ValueData: "Pacote de eventos do SmartEvents"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\SmartEvents.EventPackage\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\SmartEvents.EventPackage\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --import-event-package ""%1"" --show-dialog"; Flags: uninsdeletekey
#endif

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir o SmartEvents"; Flags: nowait postinstall skipifsilent; Check: CanLaunchApplication

[UninstallDelete]
; Dados do operador em LocalAppData sao deliberadamente preservados.
Type: filesandordirs; Name: "{app}"

[Code]
const
  DotNet48Release = 528040;
  WebView2Guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

var
  PrerequisitesChecked: Boolean;
  PrerequisiteRestartRequired: Boolean;
  SelfTestPassed: Boolean;
  ImportWarning: String;

function IsDotNet48OrNewer: Boolean;
var
  Release: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64,
    'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', Release) and
    (Release >= DotNet48Release);
end;

function IsWebView2Installed: Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM32,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2Guid, 'pv', Version) and
      (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU,
      'Software\Microsoft\EdgeUpdate\Clients\' + WebView2Guid, 'pv', Version) and
      (Version <> '') and (Version <> '0.0.0.0'));
end;

function RunPrerequisite(const FileName, Parameters, FriendlyName: String): String;
var
  ResultCode: Integer;
begin
  Result := '';
  ExtractTemporaryFile(FileName);
  WizardForm.StatusLabel.Caption := 'Instalando ' + FriendlyName + '...';
  if not Exec(ExpandConstant('{tmp}\') + FileName, Parameters, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
  begin
    Result := 'Nao foi possivel iniciar o instalador de ' + FriendlyName + '.';
    exit;
  end;
  if (ResultCode = 3010) or (ResultCode = 1641) then
    PrerequisiteRestartRequired := True
  else if ResultCode <> 0 then
    Result := FriendlyName + ' falhou com o codigo ' + IntToStr(ResultCode) + '.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if PrerequisitesChecked then
  begin
    NeedsRestart := PrerequisiteRestartRequired;
    exit;
  end;
  PrerequisitesChecked := True;

  if not IsDotNet48OrNewer then
    Result := RunPrerequisite('NDP48-x86-x64-AllOS-ENU.exe', '/q /norestart',
      '.NET Framework 4.8');
  if Result <> '' then exit;

  if not IsWebView2Installed then
    Result := RunPrerequisite('MicrosoftEdgeWebView2RuntimeInstallerX64.exe',
      '/silent /install', 'Microsoft Edge WebView2 Runtime');
  if Result <> '' then exit;

  if not IsDotNet48OrNewer then
    Result := '.NET Framework 4.8 nao foi detectado depois da instalacao.';
  if Result <> '' then exit;
  if not IsWebView2Installed then
    Result := 'Microsoft Edge WebView2 Runtime nao foi detectado depois da instalacao.';

  NeedsRestart := PrerequisiteRestartRequired;
end;

function NeedRestart: Boolean;
begin
  Result := PrerequisiteRestartRequired;
end;

function DataDir: String;
begin
  Result := ExpandConstant('{localappdata}\{#MyDataDirName}');
end;

function ImportReportPath: String;
begin
  Result := DataDir + '\diagnostics\imports\installer-import.json';
end;

function SelfTestReportPath: String;
begin
  Result := DataDir + '\diagnostics\installer-self-test.json';
end;

#ifdef MyEventPackage
function InstalledPackagePath: String;
begin
  Result := ExpandConstant('{app}\eventos\{#MyEventPackageName}');
end;

{ Valida o pacote ANTES de gravar qualquer coisa na pasta de dados: um .sepack
  corrompido na transferencia precisa parar aqui, e nao no meio da importacao. }
function ValidateEventPackage(var Detail: String): Boolean;
var
  ResultCode: Integer;
begin
  Detail := '';
  WizardForm.StatusLabel.Caption := 'Validando o pacote de eventos...';
  if not Exec(ExpandConstant('{app}\{#MyAppExeName}'),
    '--inspect-event-package "' + InstalledPackagePath + '"', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
  begin
    Detail := 'Nao foi possivel iniciar a validacao do pacote de eventos.';
    Result := False;
    exit;
  end;
  Result := ResultCode = 0;
  if not Result then
    Detail := 'O pacote de eventos foi recusado pela validacao (codigo ' +
      IntToStr(ResultCode) + ').';
end;

{ Le do relatorio os eventos que a politica `preserve` NAO substituiu. Uma
  atualizacao pode terminar com aviso, mas nunca sem dizer quais eventos ficaram
  como estavam — o operador precisa saber o que continua com a versao local. }
function PreservedEventsFromReport: String;
var
  Content, Section, Item: AnsiString;
  Start, Finish, Cut: Integer;
begin
  Result := '';
  { AnsiString porque e o que `LoadStringFromFile` devolve. So sao extraidos ids
    de evento (slugs ASCII); o resto do relatorio e descartado. }
  if not LoadStringFromFile(ImportReportPath, Content) then
    exit;
  Start := Pos('"preserved"', Content);
  if Start = 0 then
    exit;
  Section := Copy(Content, Start, Length(Content) - Start + 1);
  Start := Pos('[', Section);
  Finish := Pos(']', Section);
  if (Start = 0) or (Finish = 0) or (Finish < Start) then
    exit;
  Section := Copy(Section, Start + 1, Finish - Start - 1);
  { Cada entrada e um caminho do payload ("events/<id>.json"); so os eventos
    interessam ao operador, os cadastros compartilhados sao conciliados. }
  while Pos('events/', Section) > 0 do
  begin
    Cut := Pos('events/', Section);
    Item := Copy(Section, Cut + Length('events/'), Length(Section));
    Section := Item;
    Cut := Pos('.json', Item);
    if Cut = 0 then
      break;
    Item := Copy(Item, 1, Cut - 1);
    if Item <> '' then
    begin
      if Result <> '' then
        Result := Result + ', ';
      Result := Result + Item;
    end;
  end;
end;

{ Importa com `--conflict preserve`: alteracao local do operador nunca e
  sobrescrita por uma atualizacao do programa. Codigo 4 = concluido com conflito
  preservado; a instalacao segue, mas nomeando os eventos que nao foram trocados. }
function ImportEventPackage(var Detail: String): Boolean;
var
  ResultCode: Integer;
  Preserved: String;
begin
  Detail := '';
  ForceDirectories(ExtractFileDir(ImportReportPath));
  WizardForm.StatusLabel.Caption := 'Importando eventos...';
  if not Exec(ExpandConstant('{app}\{#MyAppExeName}'),
    '--import-event-package "' + InstalledPackagePath + '"' +
    ' --conflict preserve --report "' + ImportReportPath + '"', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
  begin
    Detail := 'Nao foi possivel iniciar a importacao dos eventos.';
    Result := False;
    exit;
  end;
  if ResultCode = 4 then
  begin
    Preserved := PreservedEventsFromReport;
    if Preserved = '' then
      Preserved := '{#MyExpectedEventIds}';
    ImportWarning :=
      'Estes eventos ja existiam com alteracoes locais e foram PRESERVADOS: ' +
      Preserved + #13#10 +
      'Nada foi sobrescrito. Relatorio: ' + ImportReportPath;
    Result := True;
    exit;
  end;
  Result := ResultCode = 0;
  if not Result then
    { `#13#10` nunca pode abrir uma linha: o pre-processador do Inno leria o `#`
      como diretiva e abortaria a compilacao. }
    Detail := 'A importacao dos eventos falhou (codigo ' + IntToStr(ResultCode) +
      ').' + #13#10 + 'Relatorio: ' + ImportReportPath;
end;
#endif

{ Ordem obrigatoria: validar -> importar -> diagnosticar. O self-test confere os
  eventos esperados, entao ele so tem sentido depois da importacao. }
function RunSelfTest(var Detail: String): Boolean;
var
  ResultCode: Integer;
  Parameters: String;
begin
  Detail := '';
  ForceDirectories(ExtractFileDir(SelfTestReportPath));
  WizardForm.StatusLabel.Caption := 'Validando a instalacao do SmartEvents...';
  Parameters := '--self-test --report "' + SelfTestReportPath + '"';
#ifdef MyEventPackage
  Parameters := Parameters + ' --expect-package-report "' + ImportReportPath + '"' +
    ' --expect-events "{#MyExpectedEventIds}"';
#endif
  if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), Parameters, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
  begin
    Detail := 'Nao foi possivel iniciar o diagnostico do SmartEvents.';
    Result := False;
    exit;
  end;
  Result := ResultCode = 0;
  if not Result then
    Detail := 'A instalacao foi concluida, mas o diagnostico encontrou uma falha.' + #13#10 +
      'Relatorio tecnico: ' + SelfTestReportPath;
end;

{ Reinicio exigido por pre-requisito: a importacao ainda NAO aconteceu, entao o
  RunOnce precisa reproduzir as duas etapas — importar e so entao diagnosticar.
  Agendar apenas o self-test deixaria a instalacao sem os eventos. }
procedure SchedulePostInstallAfterRestart;
var
  Command: String;
#ifdef MyEventPackage
  ScriptPath: String;
  Script: TArrayOfString;
#endif
begin
#ifdef MyEventPackage
  { Duas etapas encadeadas num script proprio, e nao num `cmd /c` com aspas
    aninhadas: a linha do RunOnce fica curta e o encadeamento nao depende de
    como o Windows reparte as aspas. }
  ScriptPath := ExpandConstant('{app}\post-install.cmd');
  SetArrayLength(Script, 5);
  Script[0] := '@echo off';
  Script[1] := '"' + ExpandConstant('{app}\{#MyAppExeName}') +
    '" --import-event-package "' + InstalledPackagePath +
    '" --conflict preserve --report "' + ImportReportPath + '"';
  Script[2] := 'set IMPORT_CODE=%ERRORLEVEL%';
  { 0 = importado, 4 = concluido com conflito preservado. Qualquer outro codigo e
    falha: o diagnostico nao pode "aprovar" uma instalacao sem os eventos. }
  Script[3] := 'if %IMPORT_CODE% NEQ 0 if %IMPORT_CODE% NEQ 4 exit /b %IMPORT_CODE%';
  Script[4] := '"' + ExpandConstant('{app}\{#MyAppExeName}') +
    '" --self-test --show-dialog --report "' + SelfTestReportPath +
    '" --expect-package-report "' + ImportReportPath +
    '" --expect-events "{#MyExpectedEventIds}"';
  SaveStringsToFile(ScriptPath, Script, False);
  Command := '"' + ScriptPath + '"';
#else
  Command := '"' + ExpandConstant('{app}\{#MyAppExeName}') +
    '" --self-test --show-dialog';
#endif
  RegWriteStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\RunOnce',
    'SmartEventsPostInstall-{#MyAppId}', Command);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Detail: String;
begin
  if CurStep = ssPostInstall then
  begin
    if PrerequisiteRestartRequired then
    begin
      SchedulePostInstallAfterRestart;
      SelfTestPassed := False;
      exit;
    end;

#ifdef MyEventPackage
    if not ValidateEventPackage(Detail) then
    begin
      MsgBox(Detail, mbCriticalError, MB_OK);
      RaiseException(Detail);
    end;
    if not ImportEventPackage(Detail) then
    begin
      MsgBox(Detail, mbCriticalError, MB_OK);
      RaiseException(Detail);
    end;
#endif

    if not RunSelfTest(Detail) then
    begin
      MsgBox(Detail, mbCriticalError, MB_OK);
      RaiseException('O diagnostico interno do SmartEvents falhou.');
    end;
    SelfTestPassed := True;
    if ImportWarning <> '' then
      MsgBox(ImportWarning, mbInformation, MB_OK);
  end;
end;

function CanLaunchApplication: Boolean;
begin
  Result := SelfTestPassed and not PrerequisiteRestartRequired;
end;
