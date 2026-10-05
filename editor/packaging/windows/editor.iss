#ifndef StageDir
  #error StageDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef OutputName
  #define OutputName "JSON-API-Forge-Editor-v0.5.2-windows-x64-setup"
#endif
#ifndef ProductName
  #define ProductName "JSON API Forge Editor"
#endif
#define ProductVersion "0.5.2"
#ifndef ProductId
  #define ProductId "{3E85A8B8-B157-4788-B0F7-52A9E71E3944}"
#endif

[Setup]
AppId={{#ProductId}
AppName={#ProductName}
AppVersion={#ProductVersion}
AppPublisher=YoungLionOrganization
AppPublisherURL=https://github.com/YoungLionOrganization/JSON-API-Forge
DefaultDirName={autopf}\JSON API Forge Editor
DefaultGroupName={#ProductName}
UsePreviousAppDir=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
UsePreviousPrivileges=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile=..\..\resources\forge-editor.ico
UninstallDisplayIcon={app}\bin\JSON-API-Forge-Editor.exe
UninstallDisplayName={#ProductName}
LicenseFile=..\..\..\LICENSE
WizardStyle=modern
DisableProgramGroupPage=yes
Compression=lzma2/normal
SolidCompression=yes
CloseApplications=yes
CloseApplicationsFilter=JSON-API-Forge-Editor.exe,QtWebEngineProcess.exe
RestartApplications=no
VersionInfoVersion=0.5.2.0

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\JSON API Forge Editor"; Filename: "{app}\bin\JSON-API-Forge-Editor.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall JSON API Forge Editor"; Filename: "{uninstallexe}"
Name: "{autodesktop}\JSON API Forge Editor"; Filename: "{app}\bin\JSON-API-Forge-Editor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\bin\JSON-API-Forge-Editor.exe"; Description: "Open JSON API Forge Editor"; Flags: nowait postinstall skipifsilent

[Code]
var
  ExistingDir, ExistingVersion: String;
  MaintenancePage: TInputOptionWizardPage;

function CompareVersions(A, B: String): Integer;
var I, P, Left, Right: Integer; Part: String;
begin
  Result := 0;
  for I := 1 to 4 do begin
    P := Pos('.', A); if P = 0 then P := Length(A) + 1;
    Part := Copy(A, 1, P - 1); Delete(A, 1, P); Left := StrToIntDef(Part, 0);
    P := Pos('.', B); if P = 0 then P := Length(B) + 1;
    Part := Copy(B, 1, P - 1); Delete(B, 1, P); Right := StrToIntDef(Part, 0);
    if Left < Right then begin Result := -1; Exit; end;
    if Left > Right then begin Result := 1; Exit; end;
  end;
end;

function ReadVersion(Directory: String): String;
begin
  if not GetVersionNumbersString(AddBackslash(Directory) + 'bin\JSON-API-Forge-Editor.exe', Result) then Result := '';
end;

procedure FindRegisteredInstallation(Root: Integer);
var Keys: TArrayOfString; I: Integer; Key, Name, Location, Version: String;
begin
  if not RegGetSubkeyNames(Root, 'Software\Microsoft\Windows\CurrentVersion\Uninstall', Keys) then Exit;
  for I := 0 to GetArrayLength(Keys) - 1 do begin
    Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Keys[I];
    if RegQueryStringValue(Root, Key, 'DisplayName', Name) and (CompareText(Name, '{#ProductName}') = 0) then begin
      if RegQueryStringValue(Root, Key, 'InstallLocation', Location) and DirExists(Location) then begin
        Version := ReadVersion(Location);
        if Version = '' then RegQueryStringValue(Root, Key, 'DisplayVersion', Version);
        if Version <> '' then begin ExistingDir := Location; ExistingVersion := Version; Exit; end;
      end;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  ExistingDir := ''; ExistingVersion := '';
  FindRegisteredInstallation(HKCU32);
  if ExistingDir = '' then FindRegisteredInstallation(HKLM32);
  if IsWin64 then begin
    if ExistingDir = '' then FindRegisteredInstallation(HKCU64);
    if ExistingDir = '' then FindRegisteredInstallation(HKLM64);
  end;
  Result := True;
  if (ExistingVersion <> '') and (CompareVersions(ExistingVersion, '{#ProductVersion}') > 0) then begin
    MsgBox('A newer JSON API Forge Editor (' + ExistingVersion + ') is already installed. Use its matching setup to repair it. This setup will not downgrade your installation.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure ConfigureMaintenance(Directory, Version: String);
var Older: Boolean;
begin
  ExistingDir := Directory; ExistingVersion := Version;
  Older := CompareVersions(Version, '{#ProductVersion}') < 0;
  MaintenancePage.Description := 'Installed version: ' + Version + #13#10 + 'Location: ' + Directory;
  MaintenancePage.CheckListBox.ItemEnabled[0] := Older;
  MaintenancePage.CheckListBox.ItemEnabled[1] := not Older;
  MaintenancePage.SelectedValueIndex := 1;
  if Older then MaintenancePage.SelectedValueIndex := 0;
  if Older then Log('Forge action: Upgrade; installed version: ' + Version)
  else Log('Forge action: Repair; installed version: ' + Version);
  if ExpandConstant('{param:FORGEMODE|}') = 'repair' then begin
    if not Older then MaintenancePage.SelectedValueIndex := 1;
  end;
end;

procedure InitializeWizard();
var Candidate, Version: String;
begin
  MaintenancePage := CreateInputOptionPage(wpSelectDir, 'Upgrade or Repair',
    'JSON API Forge Editor is already installed',
    'Your projects and settings are kept. Only application files are replaced.', True, False);
  MaintenancePage.Add('Upgrade to v{#ProductVersion}');
  MaintenancePage.Add('Repair v{#ProductVersion} — restore application files');
  if ExistingDir <> '' then begin
    WizardForm.DirEdit.Text := ExistingDir;
    ConfigureMaintenance(ExistingDir, ExistingVersion);
  end else begin
    Candidate := WizardForm.DirEdit.Text;
    Version := ReadVersion(Candidate);
    if Version <> '' then ConfigureMaintenance(Candidate, Version);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Version: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then Exit;
  Version := ReadVersion(WizardDirValue);
  if (Version = '') and (CompareText(WizardDirValue, ExistingDir) = 0) then Version := ExistingVersion;
  if Version = '' then begin ExistingDir := ''; ExistingVersion := ''; Exit; end;
  if CompareVersions(Version, '{#ProductVersion}') > 0 then begin
    MsgBox('This folder contains a newer Editor (' + Version + '). Downgrading is not supported.', mbError, MB_OK);
    Result := False; Exit;
  end;
  ConfigureMaintenance(WizardDirValue, Version);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = MaintenancePage.ID) and (ExistingDir = '');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Version: String;
begin
  Result := '';
  // Also protect silent deployments and /DIR overrides against downgrades.
  Version := ReadVersion(WizardDirValue);
  if (Version = '') and (CompareText(WizardDirValue, ExistingDir) = 0) then Version := ExistingVersion;
  if (Version <> '') and (CompareVersions(Version, '{#ProductVersion}') > 0) then
    Result := 'A newer Editor is installed in this folder. Nothing was changed.';
  if (Result = '') and (Version <> '') then ConfigureMaintenance(WizardDirValue, Version);
end;

procedure RemoveLegacyRegistration(Root: Integer);
var Keys: TArrayOfString; I: Integer; Key, Name, Location: String;
begin
  if not RegGetSubkeyNames(Root, 'Software\Microsoft\Windows\CurrentVersion\Uninstall', Keys) then Exit;
  for I := 0 to GetArrayLength(Keys) - 1 do begin
    Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Keys[I];
    if (CompareText(Keys[I], '{#ProductId}_is1') <> 0)
      and RegQueryStringValue(Root, Key, 'DisplayName', Name)
      and (CompareText(Name, '{#ProductName}') = 0)
      and RegQueryStringValue(Root, Key, 'InstallLocation', Location)
      and (CompareText(RemoveBackslashUnlessRoot(Location), RemoveBackslashUnlessRoot(WizardDirValue)) = 0) then
      RegDeleteKeyIncludingSubkeys(Root, Key);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssDone then Exit;
  // Retire only the legacy registration at this exact, successfully migrated location.
  RemoveLegacyRegistration(HKCU32);
  if IsAdmin then RemoveLegacyRegistration(HKLM32);
  if IsWin64 then begin
    RemoveLegacyRegistration(HKCU64);
    if IsAdmin then RemoveLegacyRegistration(HKLM64);
  end;
end;
