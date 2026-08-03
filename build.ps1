[CmdletBinding()]
param(
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedPythonVersion = "3.12.10"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Build = Join-Path $Root "build"
$Dist = Join-Path $Root "dist"
$Release = Join-Path $Root "release"
$Icon = Join-Path $Root "resources\icons\app.ico"
$AppSpec = Join-Path $Root "packaging\windows\image_editor.spec"
$InstallerSpec = Join-Path $Root "packaging\windows\installer.spec"
$LegalDocuments = @("LICENSE", "NOTICE.md", "THIRD_PARTY_LICENSES.md")

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string]$FilePath,
        [Parameter(Mandatory = $true, Position = 1)]
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "External command failed with exit code ${LASTEXITCODE}: $FilePath $($ArgumentList -join ' ')"
    }
}

function Invoke-CheckedCapture {
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string]$FilePath,
        [Parameter(Mandatory = $true, Position = 1)]
        [string[]]$ArgumentList
    )

    $output = & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "External command failed with exit code ${LASTEXITCODE}: $FilePath $($ArgumentList -join ' ')"
    }
    return ($output | Out-String).Trim()
}

function Get-BootstrapPython {
    if ($env:IMAGEEDITOR_PYTHON) {
        if (-not (Test-Path -LiteralPath $env:IMAGEEDITOR_PYTHON -PathType Leaf)) {
            throw "IMAGEEDITOR_PYTHON does not point to a file: $env:IMAGEEDITOR_PYTHON"
        }
        return [PSCustomObject]@{
            Executable = $env:IMAGEEDITOR_PYTHON
            Prefix = @()
        }
    }

    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        return [PSCustomObject]@{
            Executable = $launcher.Source
            Prefix = @("-3.12")
        }
    }

    $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        return [PSCustomObject]@{
            Executable = $pythonCommand.Source
            Prefix = @()
        }
    }

    throw "Python $ExpectedPythonVersion was not found. Install that exact x64 version first."
}

function Remove-GeneratedDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $rootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd("\") + "\"
    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a directory outside the repository: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

Push-Location $Root
try {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        if (Test-Path -LiteralPath $Venv) {
            throw "The existing .venv is incomplete. Remove it and run build.ps1 again."
        }

        $bootstrap = Get-BootstrapPython
        $bootstrapArgs = @($bootstrap.Prefix) + @("-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))")
        $bootstrapVersion = Invoke-CheckedCapture $bootstrap.Executable $bootstrapArgs
        if ($bootstrapVersion -ne $ExpectedPythonVersion) {
            throw "Python $ExpectedPythonVersion is required, but $bootstrapVersion was selected."
        }

        Write-Host "Creating isolated Python $ExpectedPythonVersion environment..."
        $venvArgs = @($bootstrap.Prefix) + @("-m", "venv", $Venv)
        Invoke-Checked $bootstrap.Executable $venvArgs
    }

    $actualPythonVersion = Invoke-CheckedCapture $Python @(
        "-c",
        "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    )
    if ($actualPythonVersion -ne $ExpectedPythonVersion) {
        throw ".venv uses Python $actualPythonVersion; Python $ExpectedPythonVersion is required."
    }
    $pythonBits = Invoke-CheckedCapture $Python @(
        "-c", "import struct; print(struct.calcsize('P') * 8)"
    )
    if ($pythonBits -ne "64") {
        throw "A 64-bit Python interpreter is required, but the selected interpreter is ${pythonBits}-bit."
    }

    Write-Host "Installing the exact locked toolchain (pip is not upgraded)..."
    Invoke-Checked $Python @(
        "-m", "pip", "install",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--no-deps",
        "--requirement", (Join-Path $Root "requirements-dev.lock")
    )
    Invoke-Checked $Python @("-m", "pip", "check")
    Invoke-Checked $Python @(
        "-m", "scripts.verify_locked_environment",
        "--lock", (Join-Path $Root "requirements-dev.lock")
    )

    Write-Host "Checking formatting..."
    Invoke-Checked $Python @("-m", "ruff", "format", "--check", ".")

    Write-Host "Running static analysis..."
    Invoke-Checked $Python @("-m", "ruff", "check", ".")
    Invoke-Checked $Python @(
        "-m", "mypy",
        "image_editor.py", "image_ops.py", "installer.py",
        "light_image_editor", "scripts", "benchmarks",
        "test_architecture.py", "test_build_metadata.py", "test_identity_resources.py",
        "test_image_ops.py", "test_installer.py"
    )

    if (-not $SkipTests) {
        Write-Host "Running tests..."
        Invoke-Checked $Python @("-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v")
    }

    if (-not (Test-Path -LiteralPath $Icon -PathType Leaf)) {
        throw "The application icon is missing: $Icon"
    }

    Remove-GeneratedDirectory $Build
    Remove-GeneratedDirectory $Dist
    Remove-GeneratedDirectory $Release
    New-Item -ItemType Directory -Path (Join-Path $Build "version") -Force | Out-Null
    New-Item -ItemType Directory -Path $Release -Force | Out-Null

    $appVersionInfo = Join-Path $Build "version\ImageEditor.version.txt"
    $installerVersionInfo = Join-Path $Build "version\ImageEditorSetup.version.txt"
    Invoke-Checked $Python @(
        "-m", "scripts.generate_version_info", "--kind", "app", "--output", $appVersionInfo
    )
    Invoke-Checked $Python @(
        "-m", "scripts.generate_version_info", "--kind", "installer", "--output", $installerVersionInfo
    )

    $env:PYTHONHASHSEED = "0"
    Write-Host "Building the application..."
    Invoke-Checked $Python @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", (Join-Path $Dist "app"),
        "--workpath", (Join-Path $Build "pyinstaller\app"),
        $AppSpec
    )

    $appExecutable = Join-Path $Dist "app\ImageEditor.exe"
    if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
        throw "PyInstaller did not create $appExecutable"
    }
    Copy-Item -LiteralPath $appExecutable -Destination (Join-Path $Release "ImageEditor.exe")

    Write-Host "Building the installer..."
    Invoke-Checked $Python @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", (Join-Path $Dist "installer"),
        "--workpath", (Join-Path $Build "pyinstaller\installer"),
        $InstallerSpec
    )

    $installerExecutable = Join-Path $Dist "installer\ImageEditorSetup.exe"
    if (-not (Test-Path -LiteralPath $installerExecutable -PathType Leaf)) {
        throw "PyInstaller did not create $installerExecutable"
    }
    Copy-Item -LiteralPath $installerExecutable -Destination (Join-Path $Release "ImageEditorSetup.exe")

    foreach ($legalDocument in $LegalDocuments) {
        $legalSource = Join-Path $Root $legalDocument
        if (-not (Test-Path -LiteralPath $legalSource -PathType Leaf)) {
            throw "Required legal document is missing: $legalSource"
        }
        Copy-Item -LiteralPath $legalSource -Destination (Join-Path $Release $legalDocument)
    }

    $version = Invoke-CheckedCapture $Python @(
        "-c", "from light_image_editor.version import __version__; print(__version__)"
    )
    foreach ($binaryName in @("ImageEditor.exe", "ImageEditorSetup.exe")) {
        $binaryPath = Join-Path $Release $binaryName
        $productVersion = (Get-Item -LiteralPath $binaryPath).VersionInfo.ProductVersion
        if ($productVersion -ne $version) {
            throw "$binaryName contains product version '$productVersion'; expected '$version'."
        }
    }

    $checksumPath = Join-Path $Release "SHA256SUMS.txt"
    $checksumLines = Get-ChildItem -LiteralPath $Release -File |
        Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
        Sort-Object Name |
        ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $($_.Name)"
        }
    [System.IO.File]::WriteAllLines(
        $checksumPath,
        $checksumLines,
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Host "Build complete (version $version):"
    Write-Host "  $Release\ImageEditor.exe"
    Write-Host "  $Release\ImageEditorSetup.exe"
    Write-Host "  $Release\SHA256SUMS.txt"
}
finally {
    Pop-Location
}
