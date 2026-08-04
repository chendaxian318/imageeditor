[CmdletBinding()]
param(
    [string]$Installer = "",
    [ValidateRange(1, 10)]
    [int]$Iterations = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Installer) {
    $Version = & (Join-Path $Root ".venv\Scripts\python.exe") `
        -c "from light_image_editor.version import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read the product version."
    }
    $Installer = Join-Path $Root "release\LightImageEditor-Setup-x64-$Version.exe"
}
$Installer = [System.IO.Path]::GetFullPath($Installer)
$rootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd("\") + "\"
if (-not $Installer.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Installer must be inside the repository: $Installer"
}
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "Installer does not exist: $Installer"
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
    Invoke-External -FilePath $Installer -Arguments $installArguments
    Assert-Installed

    # Installing the same signed recipe again exercises the overwrite-upgrade path.
    Invoke-External -FilePath $Installer -Arguments $installArguments
    Assert-Installed

    New-Item -ItemType Directory -Path $UserDataRoot -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $UserSettings,
        '{"schema_version":1,"lifecycle_smoke":true}',
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText($SmokeMarker, "preserve-me")

    $uninstallCopy = $Uninstaller
    Invoke-External `
        -FilePath $uninstallCopy `
        -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    Assert-Uninstalled
    if (-not (Test-Path -LiteralPath $UserSettings) -or -not (Test-Path -LiteralPath $SmokeMarker)) {
        throw "Silent uninstall did not preserve user settings and cache by default."
    }

    Invoke-External -FilePath $Installer -Arguments $installArguments
    Assert-Installed
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
        FreshInstall = "passed"
        OverwriteUpgrade = "passed"
        PreserveUninstall = "passed"
        ClearUninstall = "passed"
        ElapsedSeconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)
    }
}

$results | Format-Table -AutoSize
Write-Host "Validated $Iterations installs/upgrades plus preserve-data and clear-data uninstalls."
