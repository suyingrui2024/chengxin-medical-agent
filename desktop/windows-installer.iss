; Native per-user Windows installer. Interpreters live in the application folder.
#ifndef AppVersion
  #error AppVersion must be provided by the build script
#endif
#ifndef AppSource
  #error AppSource must point at the verified Windows application directory
#endif
#ifndef OutputDirectory
  #error OutputDirectory is required
#endif

[Setup]
AppId={{79EB871E-B8AC-4E0B-A6D5-3EE559833AC1}
AppName=Chengxin
AppVersion={#AppVersion}
AppPublisher=Chengxin Project
DefaultDirName={localappdata}\Programs\Chengxin
DefaultGroupName=Chengxin
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19044
OutputDir={#OutputDirectory}
OutputBaseFilename=Chengxin-Setup-{#AppVersion}-Windows-x64
UninstallDisplayIcon={app}\Chengxin.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Chengxin"; Filename: "{app}\Chengxin.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Chengxin"; Filename: "{app}\Chengxin.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Chengxin.exe"; Description: "{cm:LaunchProgram,Chengxin}"; Flags: nowait postinstall skipifsilent
