; Inno Setup script - per-user installer, no admin rights needed.
; Built by .github/workflows/build.yml:  ISCC /DAppVersion=1.2.3 packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6A4F2C1E-8B1D-4E5A-9C3F-5A7E0D2B9F41}
AppName=Satsuma
AppVersion={#AppVersion}
AppVerName=Satsuma {#AppVersion}
AppPublisher=Kassem Alharakeh
AppPublisherURL=https://github.com/kalharakeh/satsuma
AppSupportURL=https://github.com/kalharakeh/satsuma/issues
DefaultDirName={localappdata}\Programs\Satsuma
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Satsuma-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Satsuma.exe
UninstallDisplayName=Satsuma
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Files]
Source: "..\dist\Satsuma\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Run]
; registers right-click menu, Send To, Start menu, autostart - and starts Satsuma in the tray
Filename: "{app}\Satsuma.exe"; Parameters: "--install"; Flags: nowait

[UninstallRun]
Filename: "{app}\Satsuma.exe"; Parameters: "--uninstall"; Flags: waituntilterminated runhidden; RunOnceId: "SatsumaUninstall"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { close a running copy so its files can be replaced }
  if FileExists(ExpandConstant('{app}\Satsuma.exe')) then
  begin
    Exec(ExpandConstant('{app}\Satsuma.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(1000);
  end;
  Result := '';
end;
