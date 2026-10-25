; Inno Setup Script for Video Cut
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{8B840901-5AE3-4D90-84E3-D6E4B1F83E1A}
AppName=Video Cut
AppVersion={#AppVersion}
AppPublisher=Thomaz
AppPublisherURL=https://github.com/thor27/video-cut
AppSupportURL=https://github.com/thor27/video-cut/issues
AppUpdatesURL=https://github.com/thor27/video-cut/releases
DefaultDirName={autopf}\Video Cut
DefaultGroupName=Video Cut
AllowNoIcons=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=VideoCut-{#AppVersion}-Setup
SetupIconFile=videocut.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\VideoCut\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "videocut.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Video Cut"; Filename: "{app}\VideoCut.exe"; IconFilename: "{app}\videocut.ico"
Name: "{autodesktop}\Video Cut"; Filename: "{app}\VideoCut.exe"; IconFilename: "{app}\videocut.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\VideoCut.exe"; Description: "{cm:LaunchProgram,Video Cut}"; Flags: nowait postinstall skipifsilent
