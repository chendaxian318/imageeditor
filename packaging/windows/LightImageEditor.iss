#ifndef MyAppVersion
  #error MyAppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

#define MyAppName "轻量图片编辑器"
#define MyAppNameEn "Light Image Editor"
#define MyAppPublisher "chendaxian318"
#define MyAppId "io.github.chendaxian318.imageeditor"
#define MyAppExeName "ImageEditor.exe"
#define RegistryName "LightImageEditor"
#define ProgId "LightImageEditor.Image"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/chendaxian318/imageeditor
AppSupportURL=https://github.com/chendaxian318/imageeditor/issues
AppUpdatesURL=https://github.com/chendaxian318/imageeditor/releases
DefaultDirName={localappdata}\Programs\LightImageEditor
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=LightImageEditor-Setup-x64-{#MyAppVersion}
SetupIconFile={#SourcePath}\..\..\resources\icons\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} ({#MyAppNameEn}) 安装程序
VersionInfoProductName={#MyAppName} ({#MyAppNameEn})
VersionInfoProductVersion={#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern dynamic
CloseApplications=no
RestartApplications=no
ChangesAssociations=yes
LicenseFile={#SourcePath}\..\..\LICENSE
InfoAfterFile={#SourcePath}\..\..\NOTICE.md
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked
Name: "contextmenu"; Description: "添加“用轻量图片编辑器查看”右键菜单"; GroupDescription: "Windows 集成："; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\{#RegistryName}"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppName} 图像"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "{#MyAppName} 图像"
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"",0"
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "查看、浏览和编辑常见图片文件"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: """{app}\{#MyAppExeName}"",0"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".jpg"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".jpeg"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".png"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".webp"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".bmp"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".tif"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".tiff"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#RegistryName}"; ValueData: "Software\{#RegistryName}\Capabilities"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\LightImageEditor"; ValueType: string; ValueName: ""; ValueData: "用{#MyAppName}查看"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\LightImageEditor"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\LightImageEditor\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: contextmenu

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动{#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "ms-settings:defaultapps?registeredAppUser={#RegistryName}"; Description: "打开 Windows 默认应用设置"; Flags: shellexec postinstall skipifsilent unchecked

[Code]
function RequestRunningAppExit(): Integer;
var
  ResultCode: Integer;
begin
  Result := 0;
  if not FileExists(ExpandConstant('{app}\{#MyAppExeName}')) then
    exit;
  if not Exec(
    ExpandConstant('{app}\{#MyAppExeName}'),
    '--request-exit-for-upgrade',
    ExpandConstant('{app}'),
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    Result := 3;
    exit;
  end;
  Result := ResultCode;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitResult: Integer;
begin
  Result := '';
  ExitResult := RequestRunningAppExit();
  if ExitResult = 2 then
    Result := '用户取消退出主程序，升级已安全取消。请保存内容后重试。'
  else if ExitResult <> 0 then
    Result := '无法确认主程序已安全退出，安装不会覆盖正在使用的文件。';
end;

function InitializeUninstall(): Boolean;
var
  ExitResult: Integer;
begin
  ExitResult := RequestRunningAppExit();
  Result := ExitResult = 0;
  if not Result then
    MsgBox('主程序仍在运行或用户取消退出，卸载已安全取消。', mbInformation, MB_OK);
end;
