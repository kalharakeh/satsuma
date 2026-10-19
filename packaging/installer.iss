; Inno Setup script - per-user installer, no admin rights needed.
; Built by .github/workflows/build.yml:  ISCC /DAppVersion=1.2.3 packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef NumVersion
  #define NumVersion "0.0.0"
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
InfoBeforeFile=before-install.txt
LicenseFile=..\LICENSE
VersionInfoVersion={#NumVersion}
VersionInfoProductName=Satsuma
VersionInfoDescription=Satsuma installer
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
  Code, I: Integer;
begin
  { Ask a running copy to quit and wait for it (up to 15 s). Then force-close any Satsuma.exe that is
    left (including running conversions) so no file is locked. Only Satsuma.exe itself is closed, never
    its process tree: during an update this installer was started by Satsuma, and /T would kill it too. }
  if FileExists(ExpandConstant('{app}\Satsuma.exe')) then
    Exec(ExpandConstant('{app}\Satsuma.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
  for I := 1 to 30 do
  begin
    if not CheckForMutexes('SatsumaAppRunning') then
      Break;
    Sleep(500);
  end;
  Sleep(1000);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM Satsuma.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Log('PrepareToInstall: taskkill exit code ' + IntToStr(Code) + ' (128 = nothing was running)');
  if Code = 0 then
    Sleep(1500);  { let Windows release the files of the closed processes }
  Result := '';
end;
