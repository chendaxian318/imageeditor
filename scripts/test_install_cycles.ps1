[CmdletBinding()]
param(
    [string]$Installer = "",
    [string]$PreviousInstaller = "",
    [string]$TargetVersion = "",
    [string]$PreviousVersion = "1.1.3",
    [string]$Output = "",
    [ValidateRange(1, 10)]
    [int]$Iterations = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $TargetVersion) {
    $TargetVersion = & (Join-Path $Root ".venv\Scripts\python.exe") `
        -c "from light_image_editor.version import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read the product version."
    }
}
$TargetVersion = $TargetVersion.Trim()
$PreviousVersion = $PreviousVersion.Trim()
if ($TargetVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw "TargetVersion must use three-part semantic versioning: $TargetVersion"
}
if ($PreviousVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw "PreviousVersion must use three-part semantic versioning: $PreviousVersion"
}
if ([System.Version]$TargetVersion -le [System.Version]$PreviousVersion) {
    throw "TargetVersion must be newer than PreviousVersion."
}
if (-not $Installer) {
    $Installer = Join-Path $Root "release\LightImageEditor-Setup-x64-$TargetVersion.exe"
}
if (-not $PreviousInstaller) {
    $PreviousInstaller = Join-Path `
        $Root `
        "release\LightImageEditor-Rollback-x64-$PreviousVersion.exe"
}

$rootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd("\") + "\"
function Resolve-RepositoryInstaller {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        if ($Label -eq "Installer") {
            throw "Installer must be inside the repository: $resolved"
        }
        throw "PreviousInstaller must be inside the repository: $resolved"
    }
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label does not exist: $resolved"
    }
    return $resolved
}
$Installer = Resolve-RepositoryInstaller -Path $Installer -Label "Installer"
$PreviousInstaller = Resolve-RepositoryInstaller `
    -Path $PreviousInstaller `
    -Label "PreviousInstaller"
if ($Installer.Equals($PreviousInstaller, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Installer and PreviousInstaller must be different files."
}
if (-not $Output) {
    $Output = Join-Path $Root "release\V2_INSTALL_LIFECYCLE.json"
}
$Output = [System.IO.Path]::GetFullPath($Output)
if (-not $Output.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Lifecycle evidence output must be inside the repository: $Output"
}

$InstallDirectory = Join-Path $env:LOCALAPPDATA "Programs\LightImageEditor"
$Executable = Join-Path $InstallDirectory "ImageEditor.exe"
$Uninstaller = Join-Path $InstallDirectory "unins000.exe"
$RegisteredApplications = "HKCU:\Software\RegisteredApplications"
$AppRegistryRoot = "HKCU:\Software\LightImageEditor"
$Capabilities = "HKCU:\Software\LightImageEditor\Capabilities"
$ProgId = "HKCU:\Software\Classes\LightImageEditor.Image"
$ContextMenu = "HKCU:\Software\Classes\SystemFileAssociations\image\shell\LightImageEditor"
$UninstallRoot = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
$UninstallKey = Join-Path $UninstallRoot "io.github.chendaxian318.imageeditor_is1"
$UserSettings = Join-Path $env:USERPROFILE ".light_image_editor.json"
$UserDataRoot = Join-Path $env:LOCALAPPDATA "LightImageEditor"
$SmokeMarker = Join-Path $UserDataRoot "lifecycle-smoke.marker"

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "External command failed with exit code $($process.ExitCode): $FilePath"
    }
}

function Get-UninstallEntry {
    if (Test-Path -LiteralPath $UninstallKey) {
        Get-Item -LiteralPath $UninstallKey
    }
}

function Get-RegisteredApplicationValue {
    try {
        return (Get-ItemProperty `
            -LiteralPath $RegisteredApplications `
            -Name "LightImageEditor" `
            -ErrorAction Stop).LightImageEditor
    }
    catch [System.Management.Automation.PSArgumentException] {
        return $null
    }
}

function Assert-Installed {
    param([Parameter(Mandatory = $true)][string]$ExpectedVersion)

    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Installed executable is missing: $Executable"
    }
    if (-not (Test-Path -LiteralPath $Uninstaller -PathType Leaf)) {
        throw "Standard uninstall entry point is missing: $Uninstaller"
    }
    if (-not (Test-Path -LiteralPath $Capabilities)) {
        throw "Default-app capabilities were not registered."
    }
    if (-not (Test-Path -LiteralPath $ProgId)) {
        throw "Image ProgID was not registered."
    }
    if (-not (Test-Path -LiteralPath $ContextMenu)) {
        throw "Image context menu was not registered."
    }
    $registered = Get-RegisteredApplicationValue
    if ($registered -ne "Software\LightImageEditor\Capabilities") {
        throw "RegisteredApplications value is missing or incorrect: $registered"
    }
    $entries = @(Get-UninstallEntry)
    if ($entries.Count -ne 1) {
        throw "Expected one Apps & Features entry, found $($entries.Count)."
    }
    $displayVersion = (Get-ItemProperty -LiteralPath $UninstallKey).DisplayVersion
    if ($displayVersion -ne $ExpectedVersion) {
        throw "Installed DisplayVersion is '$displayVersion'; expected '$ExpectedVersion'."
    }
    $productVersion = (Get-Item -LiteralPath $Executable).VersionInfo.ProductVersion
    if (-not $productVersion.StartsWith($ExpectedVersion)) {
        throw "Installed executable version is '$productVersion'; expected '$ExpectedVersion'."
    }
}

function Assert-Uninstalled {
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((Test-Path -LiteralPath $InstallDirectory) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
    }
    if (Test-Path -LiteralPath $InstallDirectory) {
        throw "Installation directory remains after uninstall: $InstallDirectory"
    }
    foreach ($registryPath in @($AppRegistryRoot, $Capabilities, $ProgId, $ContextMenu)) {
        if (Test-Path -LiteralPath $registryPath) {
            throw "Registry residue remains after uninstall: $registryPath"
        }
    }
    $registered = Get-RegisteredApplicationValue
    if ($null -ne $registered) {
        throw "RegisteredApplications residue remains after uninstall: $registered"
    }
    if (@(Get-UninstallEntry).Count -ne 0) {
        throw "Apps & Features residue remains after uninstall."
    }
}

if (
    (Test-Path -LiteralPath $InstallDirectory) -or
    @(Get-UninstallEntry).Count -ne 0 -or
    (Test-Path -LiteralPath $UserSettings) -or
    (Test-Path -LiteralPath $UserDataRoot)
) {
    throw "A pre-existing installation or user-data directory was found; use a disposable clean profile."
}

$installArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CURRENTUSER",
    "/SP-",
    "/TASKS=contextmenu"
)
$results = @()
for ($iteration = 1; $iteration -le $Iterations; $iteration++) {
    $started = Get-Date
    Invoke-External -FilePath $PreviousInstaller -Arguments $installArguments
    Assert-Installed -ExpectedVersion $PreviousVersion

    New-Item -ItemType Directory -Path $UserDataRoot -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $UserSettings,
        "{`"schema_version`":1,`"upgrade_from`":`"$PreviousVersion`"}",
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText($SmokeMarker, "preserve-across-upgrade")

    # Exercise the actual previous-stable to target-version upgrade path.
    Invoke-External -FilePath $Installer -Arguments $installArguments
    Assert-Installed -ExpectedVersion $TargetVersion
    if (-not (Test-Path -LiteralPath $UserSettings) -or -not (Test-Path -LiteralPath $SmokeMarker)) {
        throw "UpgradeFromPrevious did not preserve settings and cache."
    }

    # A target-version repair install must remain idempotent and keep one registry entry.
    Invoke-External -FilePath $Installer -Arguments $installArguments
    Assert-Installed -ExpectedVersion $TargetVersion

    [System.IO.File]::WriteAllText($SmokeMarker, "preserve-after-upgrade")

    $uninstallCopy = $Uninstaller
    Invoke-External `
        -FilePath $uninstallCopy `
        -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    Assert-Uninstalled
    if (-not (Test-Path -LiteralPath $UserSettings) -or -not (Test-Path -LiteralPath $SmokeMarker)) {
        throw "Silent uninstall did not preserve user settings and cache by default."
    }

    Invoke-External -FilePath $Installer -Arguments $installArguments
    Assert-Installed -ExpectedVersion $TargetVersion
    $uninstallCopy = $Uninstaller
    Invoke-External `
        -FilePath $uninstallCopy `
        -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLEARUSERDATA")
    Assert-Uninstalled
    if ((Test-Path -LiteralPath $UserSettings) -or (Test-Path -LiteralPath $UserDataRoot)) {
        throw "CLEARUSERDATA uninstall left settings or cache behind."
    }

    $results += [PSCustomObject]@{
        Cycle = $iteration
        PreviousInstall = $true
        UpgradeFromPrevious = $true
        TargetRepairInstall = $true
        PreserveUninstall = $true
        ClearUninstall = $true
        ElapsedSeconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)
    }
}

$results | Format-Table -AutoSize
$evidence = [ordered]@{
    schema_version = 1
    captured_at_utc = [DateTime]::UtcNow.ToString("o")
    target_version = $TargetVersion
    previous_version = $PreviousVersion
    target_installer_sha256 = (
        Get-FileHash -LiteralPath $Installer -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    previous_installer_sha256 = (
        Get-FileHash -LiteralPath $PreviousInstaller -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    iteration_count = $Iterations
    cycles = @($results)
    passed = $true
}
$outputDirectory = Split-Path -Parent $Output
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$temporaryOutput = "$Output.tmp"
[System.IO.File]::WriteAllText(
    $temporaryOutput,
    (($evidence | ConvertTo-Json -Depth 5) + "`n"),
    [System.Text.UTF8Encoding]::new($false)
)
Move-Item -LiteralPath $temporaryOutput -Destination $Output -Force
Write-Host (
    "Validated $Iterations true upgrades ($PreviousVersion -> $TargetVersion), " +
    "target repairs, preserve-data uninstalls and clear-data uninstalls."
)
Write-Host "Lifecycle evidence: $Output"
