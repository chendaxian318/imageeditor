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
$PortableSpec = Join-Path $Root "packaging\windows\image_editor_portable.spec"
$InnoScript = Join-Path $Root "packaging\windows\LightImageEditor.iss"
$LegalDocuments = @("LICENSE", "NOTICE.md", "PRIVACY.md", "THIRD_PARTY_LICENSES.md")
$ReleaseDocuments = @(
    "docs\release\RELEASE_NOTES-v0.9.0-rc.1.md",
    "docs\beta\KNOWN_ISSUES.md",
    "docs\beta\BETA_PLAN.md",
    "docs\beta\P0_P1_REGISTER.md",
    "docs\release\UPDATE_SECURITY.md",
    "docs\release\COMPATIBILITY_MATRIX.md",
    "docs\release\RC_STABILITY_LOG.json",
    "docs\release\RC_GATES.json",
    "docs\release\v0.9.0-rc.1-validation.json",
    "docs\USER_MANUAL.md"
)
$InnoVersion = "7.0.2"
$InnoInstallerUrl = "https://github.com/jrsoftware/issrc/releases/download/is-7_0_2/innosetup-7.0.2-x64.exe"
$InnoInstallerSha256 = "5ad54ca3def786f8f4212552e54cc6d8d61329e2d24a1cfee0571d42c2684ff1"

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

function Get-InnoCompiler {
    $localInno = Join-Path $Root ".tools\inno"
    $candidates = @(
        (Join-Path $localInno "ISCC.exe"),
        (Join-Path ${env:ProgramFiles} "Inno Setup 7\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }

    $downloads = Join-Path $Root ".tools\downloads"
    New-Item -ItemType Directory -Path $downloads -Force | Out-Null
    New-Item -ItemType Directory -Path $localInno -Force | Out-Null
    $installer = Join-Path $downloads "innosetup-$InnoVersion-x64.exe"
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        Write-Host "Downloading pinned Inno Setup $InnoVersion..."
        Invoke-WebRequest -UseBasicParsing -Uri $InnoInstallerUrl -OutFile $installer
    }
    $actualHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $InnoInstallerSha256) {
        throw "Inno Setup hash mismatch: expected $InnoInstallerSha256, got $actualHash"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notlike "*Pyrsys B.V.*") {
        throw "The pinned Inno Setup installer does not have a valid Pyrsys B.V. signature."
    }
    Write-Host "Installing the verified Inno Setup compiler into .tools..."
    $innoInstallArguments = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CURRENTUSER",
        "/DIR=$localInno"
    )
    $innoInstallProcess = Start-Process `
        -FilePath $installer `
        -ArgumentList $innoInstallArguments `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($innoInstallProcess.ExitCode -ne 0) {
        throw "Inno Setup installation failed with exit code $($innoInstallProcess.ExitCode)."
    }
    $compiler = Join-Path $localInno "ISCC.exe"
    if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
        throw "Inno Setup installation did not create $compiler"
    }
    return $compiler
}

function Assert-PublisherSignature {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Thumbprint
    )

    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $actualThumbprint = ""
    if ($null -ne $signature.SignerCertificate) {
        $actualThumbprint = $signature.SignerCertificate.Thumbprint.Replace(" ", "").ToUpperInvariant()
    }
    $expectedThumbprint = $Thumbprint.Replace(" ", "").ToUpperInvariant()
    if ($signature.Status -ne "Valid" -or $actualThumbprint -ne $expectedThumbprint) {
        throw "Publisher signature verification failed for $Path"
    }
}

function Sign-PublisherBinary {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not $env:IMAGEEDITOR_SIGNTOOL -or -not $env:IMAGEEDITOR_AUTHENTICODE_THUMBPRINT) {
        return
    }
    $timestampUrl = $env:IMAGEEDITOR_TIMESTAMP_URL
    if (-not $timestampUrl) {
        $timestampUrl = "http://timestamp.digicert.com"
    }
    Invoke-Checked $env:IMAGEEDITOR_SIGNTOOL @(
        "sign", "/sha1", $env:IMAGEEDITOR_AUTHENTICODE_THUMBPRINT,
        "/fd", "SHA256", "/td", "SHA256", "/tr", $timestampUrl, $Path
    )
    Assert-PublisherSignature $Path $env:IMAGEEDITOR_AUTHENTICODE_THUMBPRINT
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
        "test_image_ops.py", "test_installer.py", "test_v03_safety.py",
        "test_viewer_services.py", "test_v07_experience.py", "test_v08_release_system.py",
        "test_v09_release_candidate.py"
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
    Invoke-Checked $Python @(
        "-m", "scripts.generate_version_info", "--kind", "app", "--output", $appVersionInfo
    )
    $version = Invoke-CheckedCapture $Python @(
        "-c", "from light_image_editor.version import __version__; print(__version__)"
    )

    $env:PYTHONHASHSEED = "0"
    Write-Host "Building the installed onedir application..."
    Invoke-Checked $Python @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", (Join-Path $Dist "app"),
        "--workpath", (Join-Path $Build "pyinstaller\app"),
        $AppSpec
    )

    $appDirectory = Join-Path $Dist "app\LightImageEditor"
    $appExecutable = Join-Path $appDirectory "ImageEditor.exe"
    if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
        throw "PyInstaller did not create $appExecutable"
    }
    Sign-PublisherBinary $appExecutable
    Copy-Item -LiteralPath $appDirectory -Destination (Join-Path $Release "LightImageEditor") -Recurse

    Write-Host "Building the portable onefile application..."
    Invoke-Checked $Python @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", (Join-Path $Dist "portable"),
        "--workpath", (Join-Path $Build "pyinstaller\portable"),
        $PortableSpec
    )

    $portableSource = Join-Path $Dist "portable\LightImageEditorPortable.exe"
    $portableName = "LightImageEditor-Portable-x64-$version.exe"
    $portableExecutable = Join-Path $Release $portableName
    if (-not (Test-Path -LiteralPath $portableSource -PathType Leaf)) {
        throw "PyInstaller did not create $portableSource"
    }
    Sign-PublisherBinary $portableSource
    Copy-Item -LiteralPath $portableSource -Destination $portableExecutable

    Write-Host "Building the standard per-user installer..."
    $innoCompiler = Get-InnoCompiler
    Invoke-Checked $innoCompiler @(
        "/Qp",
        "/DMyAppVersion=$version",
        "/DSourceDir=$appDirectory",
        "/DOutputDir=$Release",
        $InnoScript
    )
    $setupName = "LightImageEditor-Setup-x64-$version.exe"
    $installerExecutable = Join-Path $Release $setupName
    if (-not (Test-Path -LiteralPath $installerExecutable -PathType Leaf)) {
        throw "Inno Setup did not create $installerExecutable"
    }
    Sign-PublisherBinary $installerExecutable

    foreach ($legalDocument in $LegalDocuments) {
        $legalSource = Join-Path $Root $legalDocument
        if (-not (Test-Path -LiteralPath $legalSource -PathType Leaf)) {
            throw "Required legal document is missing: $legalSource"
        }
        Copy-Item -LiteralPath $legalSource -Destination (Join-Path $Release $legalDocument)
    }

    foreach ($binaryPath in @($appExecutable, $portableExecutable, $installerExecutable)) {
        $productVersion = (Get-Item -LiteralPath $binaryPath).VersionInfo.ProductVersion
        if (-not $productVersion.StartsWith($version)) {
            throw "$(Split-Path -Leaf $binaryPath) contains product version '$productVersion'; expected '$version'."
        }
    }

    foreach ($releaseDocument in $ReleaseDocuments) {
        $documentSource = Join-Path $Root $releaseDocument
        if (-not (Test-Path -LiteralPath $documentSource -PathType Leaf)) {
            throw "Required Beta document is missing: $documentSource"
        }
        Copy-Item -LiteralPath $documentSource -Destination (Join-Path $Release (Split-Path -Leaf $documentSource))
    }

    Invoke-Checked $Python @(
        "-m", "scripts.generate_sbom",
        "--lock", (Join-Path $Root "requirements.lock"),
        "--output", (Join-Path $Release "SBOM.cdx.json")
    )

    $diagnosticsDirectory = Join-Path $Release "diagnostics"
    New-Item -ItemType Directory -Path $diagnosticsDirectory -Force | Out-Null
    Copy-Item `
        -LiteralPath (Join-Path $Build "pyinstaller\app\image_editor\warn-image_editor.txt") `
        -Destination (Join-Path $diagnosticsDirectory "pyinstaller-warnings.txt")

    $signingReady = (
        $env:IMAGEEDITOR_SIGNTOOL -and
        $env:IMAGEEDITOR_AUTHENTICODE_THUMBPRINT -and
        $env:IMAGEEDITOR_UPDATE_PRIVATE_KEY
    )
    if ($signingReady) {
        Assert-PublisherSignature $appExecutable $env:IMAGEEDITOR_AUTHENTICODE_THUMBPRINT
        Assert-PublisherSignature $portableExecutable $env:IMAGEEDITOR_AUTHENTICODE_THUMBPRINT
        Assert-PublisherSignature $installerExecutable $env:IMAGEEDITOR_AUTHENTICODE_THUMBPRINT
        foreach ($channel in @("stable", "beta")) {
            $manifestName = "update-$channel.json"
            $assetUrl = "https://github.com/chendaxian318/imageeditor/releases/download/v$version/$setupName"
            Invoke-Checked $Python @(
                "-m", "scripts.generate_update_manifest",
                "--installer", $installerExecutable,
                "--version", $version,
                "--channel", $channel,
                "--url", $assetUrl,
                "--thumbprint", $env:IMAGEEDITOR_AUTHENTICODE_THUMBPRINT,
                "--private-key", $env:IMAGEEDITOR_UPDATE_PRIVATE_KEY,
                "--output", (Join-Path $Release $manifestName)
            )
        }
    }
    else {
        $gate = Join-Path $Release "SIGNING_REQUIRED.txt"
        [System.IO.File]::WriteAllText(
            $gate,
            "PUBLIC BETA BLOCKED: configure IMAGEEDITOR_SIGNTOOL, " +
            "IMAGEEDITOR_AUTHENTICODE_THUMBPRINT and IMAGEEDITOR_UPDATE_PRIVATE_KEY, " +
            "then rebuild. Unsigned binaries must not be published to an update channel.`r`n",
            [System.Text.UTF8Encoding]::new($false)
        )
        Write-Warning "Unsigned local build created. Public Beta release remains blocked."
    }

    $securityScanScript = Join-Path $Root "scripts\inspect_release.ps1"
    Invoke-Checked "powershell.exe" @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $securityScanScript,
        "-ReleasePath", $Release,
        "-Output", (Join-Path $Release "LOCAL_SECURITY_SCAN.json"),
        "-AllowUnsignedLocal",
        "-AllowUnavailableLocal"
    )

    Invoke-Checked $Python @(
        "-m", "scripts.generate_release_evidence",
        "--root", $Root,
        "--release", $Release,
        "--output", (Join-Path $Release "BUILD_PROVENANCE.json"),
        "--tests-executed", $(if ($SkipTests) { "false" } else { "true" })
    )
    Invoke-Checked $Python @(
        "-m", "scripts.verify_release_candidate",
        "--gates", (Join-Path $Root "docs\release\RC_GATES.json"),
        "--stability", (Join-Path $Root "docs\release\RC_STABILITY_LOG.json"),
        "--evidence", (Join-Path $Release "BUILD_PROVENANCE.json"),
        "--output", (Join-Path $Release "RELEASE_GATE_STATUS.json"),
        "--allow-blocked"
    )

    $checksumPath = Join-Path $Release "SHA256SUMS.txt"
    $releasePrefix = [System.IO.Path]::GetFullPath($Release).TrimEnd("\") + "\"
    $checksumLines = Get-ChildItem -LiteralPath $Release -File -Recurse |
        Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
        Sort-Object FullName |
        ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            $fullName = [System.IO.Path]::GetFullPath($_.FullName)
            if (-not $fullName.StartsWith($releasePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Release file escaped the release directory: $fullName"
            }
            $relative = $fullName.Substring($releasePrefix.Length).Replace("\", "/")
            "$hash  $relative"
        }
    [System.IO.File]::WriteAllLines(
        $checksumPath,
        $checksumLines,
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Host "Build complete (version $version):"
    Write-Host "  $Release\LightImageEditor\ImageEditor.exe"
    Write-Host "  $Release\$portableName"
    Write-Host "  $Release\$setupName"
    Write-Host "  $Release\SHA256SUMS.txt"
    Write-Host "  $Release\SBOM.cdx.json"
}
finally {
    Pop-Location
}
