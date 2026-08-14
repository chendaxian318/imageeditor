#ifndef MyAppVersion
  #error MyAppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif

#define MyAppName "CSImage"
#define MyAppNameEn "CSImage"
#define MyAppPublisher "chendaxian318"
#define MyAppId "io.github.chendaxian318.imageeditor"
#define MyAppExeName "CSImage.exe"
#define LegacyAppExeName "ImageEditor.exe"
#define RegistryName "LightImageEditor"
#define ProgId "LightImageEditor.Image"
#ifndef ReleaseNotesFile
#define ReleaseNotesFile SourcePath + "\..\..\docs\release\RELEASE_NOTES-v1.0.0.md"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=D:\CSImage
DisableDirPage=no
AllowNetworkDrive=no
AllowUNCPath=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=CSImage-Setup-x64-{#MyAppVersion}
SetupIconFile={#SourcePath}\..\..\resources\icons\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern dynamic
CloseApplications=no
RestartApplications=no
ChangesAssociations=yes
LicenseFile={#SourcePath}\INSTALLER_LICENSE.txt
InfoAfterFile={#SourcePath}\INSTALLER_NOTICE.txt
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked
Name: "contextmenu"; Description: "添加“用 CSImage 查看”右键菜单"; GroupDescription: "Windows 集成："; Flags: checkedonce

[InstallDelete]
; PyInstaller's runtime directory changes between releases. Remove the old
; generated runtime first so an upgrade cannot retain excluded heavyweight DLLs.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#LegacyAppExeName}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}\..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\PRIVACY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\docs\USER_MANUAL.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ReleaseNotesFile}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\..\..\docs\DEFAULT_APP_HELP.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; Remove the obsolete executable registration after upgrading from builds that
; still shipped ImageEditor.exe. The stable ProgID and user data stay intact.
Root: HKCU; Subkey: "Software\Classes\Applications\{#LegacyAppExeName}"; ValueType: none; Flags: deletekey dontcreatekey
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
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".apng"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".webp"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".avif"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".heic"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".heif"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".bmp"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".tif"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".tiff"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".gif"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".psd"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".psdt"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\{#RegistryName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#RegistryName}"; ValueData: "Software\{#RegistryName}\Capabilities"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\Applications\{#MyAppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\LightImageEditor"; ValueType: string; ValueName: ""; ValueData: "用{#MyAppName}查看"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\LightImageEditor"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\LightImageEditor\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.psd\shell\LightImageEditor"; ValueType: string; ValueName: ""; ValueData: "用{#MyAppName}查看"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.psd\shell\LightImageEditor"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.psd\shell\LightImageEditor\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.psdt\shell\LightImageEditor"; ValueType: string; ValueName: ""; ValueData: "用{#MyAppName}查看"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.psdt\shell\LightImageEditor"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.psdt\shell\LightImageEditor\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: contextmenu

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动{#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "ms-settings:defaultapps?registeredAppUser={#RegistryName}"; Description: "打开 Windows 默认应用设置"; Flags: shellexec postinstall skipifsilent unchecked

[Code]
var
  PreserveUserData: Boolean;
  FolderBrowseForm: TSetupForm;
  FolderDriveList: TNewListBox;
  FolderDriveRoots: TStringList;
  FolderListBox: TNewListBox;
  FolderCurrentLabel: TNewStaticText;
  FolderPathEdit: TNewPathEdit;
  FolderCurrentDirectory: String;

function GetDriveTypeW(lpRootPathName: String): Integer;
  external 'GetDriveTypeW@kernel32.dll stdcall';

function FixedDriveRoot(const Path: String): String;
var
  Root: String;
begin
  Result := '';
  if (Length(Path) < 3) or (Path[2] <> ':') then
    exit;
  Root := Uppercase(Copy(Path, 1, 3));
  if GetDriveTypeW(Root) = 3 then
    Result := Root;
end;

function FirstFixedDrive(): String;
var
  DriveLetter: Integer;
  DrivePath: String;
begin
  Result := '';
  for DriveLetter := Ord('A') to Ord('Z') do
  begin
    DrivePath := Chr(DriveLetter) + ':\';
    if GetDriveTypeW(DrivePath) = 3 then
    begin
      Result := DrivePath;
      exit;
    end;
  end;
end;

function ExistingLocalFolder(const Path: String): String;
var
  Candidate: String;
  Root: String;
begin
  Root := FixedDriveRoot(Path);
  if Root = '' then
    Root := FirstFixedDrive();
  Candidate := RemoveBackslashUnlessRoot(Path);
  if CompareText(Copy(Candidate, 1, 3), Root) <> 0 then
    Candidate := Root;
  while (Length(Candidate) > 3) and (not DirExists(Candidate)) do
    Candidate := ExtractFileDir(Candidate);
  if not DirExists(Candidate) then
    Candidate := Root;
  Result := Candidate;
end;

function SuggestedInstallPath(const Folder: String): String;
var
  Normalized: String;
begin
  Normalized := RemoveBackslashUnlessRoot(Folder);
  if CompareText(ExtractFileName(Normalized), 'CSImage') = 0 then
    Result := Normalized
  else
    Result := AddBackslash(Normalized) + 'CSImage';
end;

procedure RefreshLocalFolderList(const Folder: String);
var
  FindRec: TFindRec;
begin
  if FixedDriveRoot(Folder) = '' then
    exit;
  FolderCurrentDirectory := RemoveBackslashUnlessRoot(Folder);
  FolderCurrentLabel.Caption := FolderCurrentDirectory;
  FolderListBox.Items.Clear();
  if FindFirst(AddBackslash(FolderCurrentDirectory) + '*', FindRec) then
  begin
    try
      repeat
        if ((FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
           ((FindRec.Attributes and FILE_ATTRIBUTE_HIDDEN) = 0) and
           ((FindRec.Attributes and FILE_ATTRIBUTE_SYSTEM) = 0) and
           ((FindRec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0) and
           ((FindRec.Attributes and FILE_ATTRIBUTE_OFFLINE) = 0) and
           (FindRec.Name <> '.') and (FindRec.Name <> '..') then
          FolderListBox.Items.Add(FindRec.Name);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure FolderListClicked(Sender: TObject);
var
  SelectedFolder: String;
begin
  if FolderListBox.ItemIndex >= 0 then
  begin
    SelectedFolder := AddBackslash(FolderCurrentDirectory) +
      FolderListBox.Items[FolderListBox.ItemIndex];
    FolderPathEdit.Text := SuggestedInstallPath(SelectedFolder);
  end;
end;

procedure FolderListDoubleClicked(Sender: TObject);
var
  SelectedFolder: String;
begin
  if FolderListBox.ItemIndex >= 0 then
  begin
    SelectedFolder := AddBackslash(FolderCurrentDirectory) +
      FolderListBox.Items[FolderListBox.ItemIndex];
    if DirExists(SelectedFolder) then
    begin
      RefreshLocalFolderList(SelectedFolder);
      FolderPathEdit.Text := SuggestedInstallPath(SelectedFolder);
    end;
  end;
end;

procedure FolderDriveChanged(Sender: TObject);
begin
  if FolderDriveList.ItemIndex >= 0 then
  begin
    RefreshLocalFolderList(FolderDriveRoots[FolderDriveList.ItemIndex]);
    FolderPathEdit.Text := SuggestedInstallPath(FolderCurrentDirectory);
  end;
end;

procedure ParentFolderClicked(Sender: TObject);
var
  ParentFolder: String;
  Root: String;
begin
  Root := FixedDriveRoot(FolderCurrentDirectory);
  ParentFolder := ExtractFileDir(RemoveBackslashUnlessRoot(FolderCurrentDirectory));
  if (ParentFolder = '') or
     (CompareText(Copy(ParentFolder, 1, 3), Root) <> 0) then
    ParentFolder := Root;
  RefreshLocalFolderList(ParentFolder);
  FolderPathEdit.Text := SuggestedInstallPath(ParentFolder);
end;

procedure ShowLocalFolderBrowser(Sender: TObject);
var
  DriveLetter: Integer;
  DrivePath: String;
  InitialFolder: String;
  InitialRoot: String;
  SelectedIndex: Integer;
  HeaderPanel: TPanel;
  TitleLabel: TNewStaticText;
  HintLabel: TNewStaticText;
  DriveLabel: TNewStaticText;
  FolderLabel: TNewStaticText;
  FolderHintLabel: TNewStaticText;
  PathLabel: TNewStaticText;
  ParentFolderButton: TNewButton;
  OKButton: TNewButton;
  CancelButton: TNewButton;
begin
  FolderBrowseForm := CreateCustomForm(ScaleX(660), ScaleY(510), True, True);
  try
    FolderBrowseForm.Caption := '选择 CSImage 安装位置';

    HeaderPanel := TPanel.Create(FolderBrowseForm);
    HeaderPanel.Parent := FolderBrowseForm;
    HeaderPanel.BevelOuter := bvNone;
    HeaderPanel.Color := clBtnFace;
    HeaderPanel.SetBounds(0, 0, ScaleX(660), ScaleY(76));

    TitleLabel := TNewStaticText.Create(FolderBrowseForm);
    TitleLabel.Parent := HeaderPanel;
    TitleLabel.Caption := '选择 CSImage 安装位置';
    TitleLabel.Font.Size := 15;
    TitleLabel.Font.Style := [fsBold];
    TitleLabel.SetBounds(ScaleX(20), ScaleY(14), ScaleX(610), ScaleY(26));

    HintLabel := TNewStaticText.Create(FolderBrowseForm);
    HintLabel.Parent := HeaderPanel;
    HintLabel.Caption := '只按需读取本地文件夹，不扫描网络磁盘，选择过程保持流畅。';
    HintLabel.SetBounds(ScaleX(20), ScaleY(43), ScaleX(610), ScaleY(21));

    DriveLabel := TNewStaticText.Create(FolderBrowseForm);
    DriveLabel.Parent := FolderBrowseForm;
    DriveLabel.Caption := '本地磁盘';
    DriveLabel.Font.Style := [fsBold];
    DriveLabel.SetBounds(ScaleX(16), ScaleY(90), ScaleX(142), ScaleY(22));

    FolderLabel := TNewStaticText.Create(FolderBrowseForm);
    FolderLabel.Parent := FolderBrowseForm;
    FolderLabel.Caption := '文件夹';
    FolderLabel.Font.Style := [fsBold];
    FolderLabel.SetBounds(ScaleX(174), ScaleY(90), ScaleX(120), ScaleY(22));

    FolderHintLabel := TNewStaticText.Create(FolderBrowseForm);
    FolderHintLabel.Parent := FolderBrowseForm;
    FolderHintLabel.Caption := '单击选择，双击进入';
    FolderHintLabel.Alignment := taRightJustify;
    FolderHintLabel.SetBounds(ScaleX(440), ScaleY(90), ScaleX(204), ScaleY(22));

    FolderDriveList := TNewListBox.Create(FolderBrowseForm);
    FolderDriveList.Parent := FolderBrowseForm;
    FolderDriveList.Font.Size := 10;
    FolderDriveList.SetBounds(ScaleX(16), ScaleY(116), ScaleX(142), ScaleY(280));
    FolderDriveRoots := TStringList.Create();

    SelectedIndex := -1;
    InitialFolder := ExistingLocalFolder(WizardForm.DirEdit.Text);
    InitialRoot := FixedDriveRoot(InitialFolder);
    for DriveLetter := Ord('A') to Ord('Z') do
    begin
      DrivePath := Chr(DriveLetter) + ':\';
      if GetDriveTypeW(DrivePath) = 3 then
      begin
        FolderDriveRoots.Add(DrivePath);
        if CompareText(DrivePath, FixedDriveRoot(ExpandConstant('{win}'))) = 0 then
          FolderDriveList.Items.Add('系统盘  ' + DrivePath)
        else
          FolderDriveList.Items.Add('本地磁盘  ' + DrivePath);
        if CompareText(DrivePath, InitialRoot) = 0 then
          SelectedIndex := FolderDriveList.Items.Count - 1;
      end;
    end;
    FolderDriveList.ItemIndex := SelectedIndex;
    FolderDriveList.OnClick := @FolderDriveChanged;

    ParentFolderButton := TNewButton.Create(FolderBrowseForm);
    ParentFolderButton.Parent := FolderBrowseForm;
    ParentFolderButton.Caption := '‹  返回上级';
    ParentFolderButton.SetBounds(ScaleX(174), ScaleY(116), ScaleX(98), ScaleY(28));
    ParentFolderButton.OnClick := @ParentFolderClicked;

    FolderCurrentLabel := TNewStaticText.Create(FolderBrowseForm);
    FolderCurrentLabel.Parent := FolderBrowseForm;
    FolderCurrentLabel.SetBounds(ScaleX(286), ScaleY(121), ScaleX(358), ScaleY(20));

    FolderListBox := TNewListBox.Create(FolderBrowseForm);
    FolderListBox.Parent := FolderBrowseForm;
    FolderListBox.Font.Size := 10;
    FolderListBox.Sorted := True;
    FolderListBox.SetBounds(ScaleX(174), ScaleY(152), ScaleX(470), ScaleY(244));
    FolderListBox.OnClick := @FolderListClicked;
    FolderListBox.OnDblClick := @FolderListDoubleClicked;
    RefreshLocalFolderList(InitialFolder);

    PathLabel := TNewStaticText.Create(FolderBrowseForm);
    PathLabel.Parent := FolderBrowseForm;
    PathLabel.Caption := '最终安装路径';
    PathLabel.Font.Style := [fsBold];
    PathLabel.SetBounds(ScaleX(16), ScaleY(412), ScaleX(628), ScaleY(20));

    FolderPathEdit := TNewPathEdit.Create(FolderBrowseForm);
    FolderPathEdit.Parent := FolderBrowseForm;
    FolderPathEdit.Font.Size := 10;
    FolderPathEdit.AutoCompleteFiles := False;
    FolderPathEdit.Text := WizardForm.DirEdit.Text;
    FolderPathEdit.SetBounds(ScaleX(16), ScaleY(436), ScaleX(628), ScaleY(28));

    OKButton := TNewButton.Create(FolderBrowseForm);
    OKButton.Parent := FolderBrowseForm;
    OKButton.Caption := '确定';
    OKButton.Default := True;
    OKButton.ModalResult := mrOk;
    OKButton.SetBounds(ScaleX(466), ScaleY(476), ScaleX(82), ScaleY(27));

    CancelButton := TNewButton.Create(FolderBrowseForm);
    CancelButton.Parent := FolderBrowseForm;
    CancelButton.Caption := '取消';
    CancelButton.Cancel := True;
    CancelButton.ModalResult := mrCancel;
    CancelButton.SetBounds(ScaleX(562), ScaleY(476), ScaleX(82), ScaleY(27));

    FolderBrowseForm.ActiveControl := FolderListBox;
    FolderBrowseForm.FlipAndCenterIfNeeded(True, WizardForm, False);
    if FolderBrowseForm.ShowModal() = mrOk then
      WizardForm.DirEdit.Text := Trim(FolderPathEdit.Text);
  finally
    FolderDriveRoots.Free();
    FolderBrowseForm.Free();
  end;
end;

procedure InitializeWizard();
begin
  { Keep the license page visible, but default to the affirmative option so
    the user can continue without an unnecessary extra click. }
  WizardForm.LicenseAcceptedRadio.Checked := True;
  { Avoid Windows Shell autocomplete/folder picker enumeration of disconnected
    network drives. The replacement browser enumerates only the local directory
    that the user explicitly opens. }
  WizardForm.DirEdit.AutoCompleteFiles := False;
  WizardForm.DirBrowseButton.Caption := '浏览...';
  WizardForm.DirBrowseButton.OnClick := @ShowLocalFolderBrowser;
  WizardForm.SelectDirBrowseLabel.Caption :=
    '可直接输入完整路径，或点击“浏览”用图形界面选择本地文件夹。';
end;

function ExecutableCanBeReplaced(const ExecutablePath: String): Boolean;
var
  ProbePath: String;
begin
  Result := False;
  if not FileExists(ExecutablePath) then
  begin
    Result := True;
    exit;
  end;
  ProbePath := ExecutablePath + '.upgrade-probe';
  DeleteFile(ProbePath);
  if not RenameFile(ExecutablePath, ProbePath) then
    exit;
  if RenameFile(ProbePath, ExecutablePath) then
    Result := True
  else
    RaiseException('升级占用检测后无法恢复主程序文件名，请检查安装目录权限。');
end;

function RequestExecutableExit(const ExecutablePath: String): Integer;
var
  ResultCode: Integer;
begin
  Result := 0;
  if not FileExists(ExecutablePath) then
    exit;
  { Do not launch an idle installed executable merely to ask it to exit. This
    also lets a repaired installer replace an older executable that cannot
    start because one of its bundled modules is damaged or missing. }
  if ExecutableCanBeReplaced(ExecutablePath) then
    exit;
  if not Exec(
    ExecutablePath,
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

function RequestRunningAppExit(): Integer;
begin
  Result := RequestExecutableExit(ExpandConstant('{app}\{#MyAppExeName}'));
  if Result <> 0 then
    exit;
  Result := RequestExecutableExit(ExpandConstant('{app}\{#LegacyAppExeName}'));
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
  begin
    MsgBox('主程序仍在运行或用户取消退出，卸载已安全取消。', mbInformation, MB_OK);
    exit;
  end;

  PreserveUserData := True;
  if Pos('/CLEARUSERDATA', Uppercase(GetCmdTail)) > 0 then
    PreserveUserData := False
  else if not UninstallSilent then
    PreserveUserData := MsgBox(
      '是否保留设置、最近文件、恢复会话、诊断日志和更新缓存？' + #13#10 + #13#10 +
      '选择“是”可在重新安装后继续使用；选择“否”将清除这些本机数据。',
      mbConfirmation,
      MB_YESNO
    ) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not PreserveUserData) then
  begin
    DeleteFile(ExpandConstant('{%USERPROFILE}\.light_image_editor.json'));
    DeleteFile(ExpandConstant('{%USERPROFILE}\.light_image_editor.json.pre-v1.bak'));
    DeleteFile(ExpandConstant('{%USERPROFILE}\.light_image_editor_session.json'));
    DeleteFile(ExpandConstant('{%USERPROFILE}\.light_image_editor_session.recovery.png'));
    DeleteFile(ExpandConstant('{%USERPROFILE}\.light_image_editor_batch.json'));
    DelTree(ExpandConstant('{localappdata}\LightImageEditor'), True, True, True);
  end;
end;
