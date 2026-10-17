#ifndef PayloadDir
  #error PayloadDir must point to a verified offline payload
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\setup"
#endif
[Setup]
AppId={{C6EF8023-4883-4F24-A134-043E8D091F09}
AppName=iracing中文播报助手
AppVersion=0.2.8
AppPublisher=honokaoshi83-max
DefaultDirName={localappdata}\Programs\iRacingChineseAssistant
DefaultGroupName=iracing中文播报助手
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=iracing-chinese-assistant-0.2.8-setup
Compression=zip/1
DiskSpanning=yes
DiskSliceSize=max
SolidCompression=no
WizardStyle=modern
SetupIconFile={#PayloadDir}\app\assets\app-icon.ico
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\iracing-assistant.exe
CloseApplications=no
LicenseFile={#PayloadDir}\LICENSE

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.log,*.jsonl,*.pyc,*.pid,*.lock,*.tmp,__pycache__\*,config\local.json,work\race-voices\voice_library.json,work\race-voices\voice_import_request.json,work\race-voices\playback_settings.json,work\race-voices\models\custom\*,work\race-voices\cache\*,work\race-voices\samples\*,work\race-voices\status_button_status.json,work\race-voices\active_inference.yaml,work\race-voices\watchdog.stop,work\race-voices\simulator_controller_owner.json,work\race-voices\app_owner.json,work\race-voices\status_button_settings.json,work\race-voices\driver_numbers.json,work\race-voices\component_speech_priority.json,work\race-voices\opening_active.json,work\race-voices\emotion_settings.json,work\race-voices\tire_settings.json,work\race-voices\voices.json"

Source: "{#PayloadDir}\work\race-voices\status_button_settings.json"; DestDir: "{app}\work\race-voices"; Flags: onlyifdoesntexist
Source: "{#PayloadDir}\work\race-voices\emotion_settings.json"; DestDir: "{app}\work\race-voices"; Flags: onlyifdoesntexist
Source: "{#PayloadDir}\work\race-voices\tire_settings.json"; DestDir: "{app}\work\race-voices"; Flags: onlyifdoesntexist
Source: "{#PayloadDir}\work\race-voices\voices.json"; DestDir: "{app}\work\race-voices"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\iracing中文播报助手"; Filename: "{app}\iracing-assistant.exe"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\app-icon.ico"
Name: "{autodesktop}\iracing中文播报助手"; Filename: "{app}\iracing-assistant.exe"; WorkingDir: "{app}"; IconFilename: "{app}\app\assets\app-icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\iracing-assistant.exe"; Description: "启动 iracing中文播报助手"; Flags: nowait postinstall skipifsilent
Filename: "{app}\iracing-assistant.exe"; Parameters: "--install-simulator"; Description: "一键安装并接入 Simulator Controller（需联网；取消后无邻车、旗号、策略播报）"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\stop.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "StopOwnedServices"
