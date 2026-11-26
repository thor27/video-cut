; Inno Setup Script for Video Cut
#ifndef AppVersion
  #define AppVersion "0.6.1"
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
OutputBaseFilename=VideoCut-{#AppVersion}-Windows-x86_64-Setup
SetupIconFile=videocut.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

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

[Registry]
Root: HKA; Subkey: "Software\Classes\.vcut"; ValueType: string; ValueName: ""; ValueData: "VideoCut.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.vcut"; ValueType: string; ValueName: "Content Type"; ValueData: "application/x-videocut"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\VideoCut.Project"; ValueType: string; ValueName: ""; ValueData: "Video Cut Project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\VideoCut.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\videocut.ico,0"
Root: HKA; Subkey: "Software\Classes\VideoCut.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\VideoCut.exe"" ""%1"""

[Run]
Filename: "{app}\VideoCut.exe"; Description: "{cm:LaunchProgram,Video Cut}"; Flags: nowait postinstall skipifsilent

