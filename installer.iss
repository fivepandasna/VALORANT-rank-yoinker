#define MyAppName "vry"
#define MyAppVersion GetEnv("GITHUB_REF_NAME")
#define MyAppExeName "vry.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Ashaan Dalgliesh
AppPublisherURL=https://github.com/fivepandasna/VALORANT-rank-yoinker
AppSupportURL=https://github.com/fivepandasna/VALORANT-rank-yoinker/issues
AppComments=Based on VALORANT rank yoinker by isaacKenyon
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=dist
OutputBaseFilename=vry-{#MyAppVersion}-setup
SetupIconFile=assets\Logo.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "dist\vry\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch vry"; Flags: nowait postinstall skipifsilent