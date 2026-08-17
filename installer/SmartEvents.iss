#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "SmartEvents"
#define MyAppPublisher "SmartEvents"
#define MyAppExeName "SmartEvents.exe"

[Setup]
AppId={{72D1D22D-A46F-42C4-8D5C-F82435BDD0AE}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SmartEvents
DefaultGroupName=SmartEvents
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=Setup_SmartEvents_RoadShow
SetupIconFile=..\assets\logoSmartEvents.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
CloseApplicationsFilter={#MyAppExeName}
ChangesAssociations=no
ExtraDiskSpaceRequired=700000000
RestartIfNeededByRun=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
Source: "..\dist\SmartEvents\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "prerequisites\NDP48-x86-x64-AllOS-ENU.exe"; Flags: dontcopy
Source: "prerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Flags: dontcopy

[Icons]
Name: "{group}\SmartEvents"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\SmartEvents"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

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

procedure ScheduleSelfTestAfterRestart;
var
  Command: String;
begin
  Command := '"' + ExpandConstant('{app}\{#MyAppExeName}') +
    '" --self-test --show-dialog';
  RegWriteStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\RunOnce',
    'SmartEventsSelfTest', Command);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  ReportPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    if PrerequisiteRestartRequired then
    begin
      ScheduleSelfTestAfterRestart;
      SelfTestPassed := False;
      exit;
    end;

    ReportPath := ExpandConstant('{localappdata}\SmartEvents\diagnostics\installer-self-test.json');
    ForceDirectories(ExtractFileDir(ReportPath));
    WizardForm.StatusLabel.Caption := 'Validando a instalacao do SmartEvents...';
    if not Exec(ExpandConstant('{app}\{#MyAppExeName}'),
      '--self-test --report "' + ReportPath + '"', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) then
      RaiseException('Nao foi possivel iniciar o diagnostico do SmartEvents.');
    if ResultCode <> 0 then
    begin
      MsgBox('A instalacao foi concluida, mas o diagnostico encontrou uma falha.' + #13#10 +
        'Relatorio tecnico: ' + ReportPath, mbCriticalError, MB_OK);
      RaiseException('O diagnostico interno do SmartEvents falhou.');
    end;
    SelfTestPassed := True;
  end;
end;

function CanLaunchApplication: Boolean;
begin
  Result := SelfTestPassed and not PrerequisiteRestartRequired;
end;
