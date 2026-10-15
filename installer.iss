; ============================================================================
;  Wifi Tracker - assistant d'installation Windows (Inno Setup 6)
;
;  Ne se compile pas à la main : lancez build_installer.bat, qui fabrique d'abord
;  dist\WifiTracker.exe et dist\WifiTracker-console.exe, puis appelle :
;      ISCC.exe /DServerUrl=https://mon-serveur.onrender.com installer.iss
;  Résultat : installers\WifiTracker-Setup.exe  (à publier avec le serveur)
; ============================================================================
#define MyAppName "Wifi Tracker"
#define MyAppVersion "2.0"
#ifndef ServerUrl
  #define ServerUrl ""
#endif

[Setup]
AppId={{6F1D2B8E-3C54-4A7B-9E0D-5B2A71C4F8A3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Installation pour l'utilisateur courant : pas besoin d'être administrateur.
PrivilegesRequired=lowest
OutputDir=installers
OutputBaseFilename=WifiTracker-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\WifiTracker.exe

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "autostart"; Description: "Démarrer Wifi Tracker automatiquement avec Windows"; GroupDescription: "Options :"
Name: "openbrowser"; Description: "Ouvrir la page de position dans le navigateur (position plus précise)"; GroupDescription: "Options :"

[Files]
Source: "dist\WifiTracker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\WifiTracker-console.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Configurer Wifi Tracker"; Filename: "{app}\WifiTracker.exe"; Parameters: "--setup"
Name: "{group}\Journal de Wifi Tracker"; Filename: "{userappdata}\WifiTracker\client.log"
Name: "{group}\Diagnostic (console)"; Filename: "{app}\WifiTracker-console.exe"; Parameters: "--debug"
Name: "{group}\Désinstaller Wifi Tracker"; Filename: "{uninstallexe}"

[UninstallRun]
; Ferme le client, retire le démarrage automatique et la configuration, avant la suppression des fichiers.
Filename: "{app}\WifiTracker.exe"; Parameters: "--uninstall --silent"; Flags: runhidden waituntilterminated; RunOnceId: "WifiTrackerCleanup"

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\WifiTracker"

[Code]
const
  CodeAlphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';   { sans I, O, 0, 1 : comme le serveur }

var
  CfgPage: TInputQueryWizardPage;

function IsCode(S: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  if Length(S) <> 14 then Exit;                      { XXXX-XXXX-XXXX }
  for I := 1 to 14 do
  begin
    if (I = 5) or (I = 10) then
    begin
      if Copy(S, I, 1) <> '-' then Exit;
    end
    else if Pos(Copy(S, I, 1), CodeAlphabet) = 0 then Exit;
  end;
  Result := True;
end;

{ Le serveur nomme le fichier WifiTracker-Setup-XXXX-XXXX-XXXX.exe : on y lit le code. }
function CodeFromFileName: String;
var
  Name: String;
  I: Integer;
begin
  Result := '';
  Name := UpperCase(ExtractFileName(ExpandConstant('{srcexe}')));
  for I := 1 to Length(Name) - 13 do
  begin
    if IsCode(Copy(Name, I, 14)) then
    begin
      Result := Copy(Name, I, 14);
      Exit;
    end;
  end;
end;

procedure InitializeWizard;
begin
  CfgPage := CreateInputQueryPage(wpSelectTasks,
    'Connexion au serveur',
    'Associez cet ordinateur à votre compte Wifi Tracker',
    'Saisissez l''adresse du serveur et le code d''activation affiché dans le tableau de bord (bouton « Windows » du client). Si vous avez téléchargé cet installateur depuis le tableau de bord, le code est déjà rempli.');
  CfgPage.Add('Adresse du serveur :', False);
  CfgPage.Add('Code d''activation (valable 30 minutes, une seule utilisation) :', False);
  CfgPage.Values[0] := '{#ServerUrl}';
  CfgPage.Values[1] := CodeFromFileName;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Srv, Code: String;
begin
  Result := True;
  if CurPageID = CfgPage.ID then
  begin
    Srv := LowerCase(Trim(CfgPage.Values[0]));
    Code := UpperCase(Trim(CfgPage.Values[1]));
    if (Pos('https://', Srv) <> 1) and (Pos('http://', Srv) <> 1) then
    begin
      MsgBox('L''adresse du serveur doit commencer par https:// (ou http:// pour un serveur local).', mbError, MB_OK);
      Result := False;
    end
    else if not IsCode(Code) then
    begin
      MsgBox('Le code d''activation a la forme XXXX-XXXX-XXXX. Générez-en un dans le tableau de bord (bouton « Windows » du client).', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Mise à jour : ferme l'ancien client avant de remplacer ses fichiers. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM WifiTracker.exe /IM WifiTracker-console.exe', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params, Msg: String;
  RC: Integer;
  Launched: Boolean;
begin
  if CurStep <> ssPostInstall then Exit;

  { Échange le code contre le jeton, enregistre la configuration (+ démarrage automatique). }
  Params := '--enroll ' + UpperCase(Trim(CfgPage.Values[1])) + ' --server "' + Trim(CfgPage.Values[0]) + '" --silent';
  if WizardIsTaskSelected('autostart') then Params := Params + ' --autostart';
  if not WizardIsTaskSelected('openbrowser') then Params := Params + ' --no-open';

  RC := -1;
  Launched := Exec(ExpandConstant('{app}\WifiTracker.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, RC);
  if Launched and (RC = 0) then
  begin
    { Lance le client tout de suite (sans fenêtre). }
    Exec(ExpandConstant('{app}\WifiTracker.exe'), '', '', SW_HIDE, ewNoWait, RC);
  end
  else
  begin
    if not Launched then
      Msg := 'Wifi Tracker a été copié mais n''a pas pu démarrer.'
    else if RC = 2 then
      Msg := 'Le code d''activation est invalide, expiré ou déjà utilisé.'
    else if RC = 3 then
      Msg := 'Le serveur est injoignable (vérifiez l''adresse et la connexion Internet).'
    else
      Msg := 'L''enregistrement auprès du serveur a échoué (code ' + IntToStr(RC) + ').';
    MsgBox(Msg + #13#10#13#10 + 'Wifi Tracker est installé. Générez un nouveau code dans le tableau de bord, ' +
           'puis lancez « Configurer Wifi Tracker » depuis le menu Démarrer.', mbError, MB_OK);
  end;
end;
