[Setup]
AppId=DTHR.EXE
AppName=DTHR.EXE
AppVersion={#AppVersion}
AppPublisher=Piranov
AppPublisherURL=https://github.com/Piranov/DTHR.EXE
DefaultDirName={localappdata}\Programs\DTHR
DefaultGroupName=DTHR.EXE
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\build\release\v{#AppVersion}
OutputBaseFilename=DTHR-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\DTHR.EXE
Uninstallable=not PortableMode
WizardStyle=modern
CloseApplications=no

[Files]
Source: "..\dist\DTHR\DTHR.EXE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\DTHR\dthr-cli.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\DTHR\dthr\*.py"; DestDir: "{app}\dthr"; Flags: ignoreversion
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.lock"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\release\v{#AppVersion}\generation.json"; DestDir: "{app}"; Flags: ignoreversion
#include "..\build\release\v" + AppVersion + "\editor-files.iss"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\generation-components"

[Icons]
Name: "{group}\DTHR.EXE"; Filename: "{app}\DTHR.EXE"; Check: not PortableMode

[Run]
Filename: "{app}\DTHR.EXE"; Description: "Open DTHR.EXE"; Flags: postinstall nowait skipifsilent

[Code]
function PortableMode: Boolean;
begin
  Result := ExpandConstant('{param:PORTABLE|0}') = '1';
end;
