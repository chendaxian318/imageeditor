[CmdletBinding()]
param(
    [string]$ReleasePath = "",
    [string]$Output = "",
    [string]$MultiEngineEvidence = "",
    [switch]$AllowUnsignedLocal,
    [switch]$AllowUnavailableLocal
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $ReleasePath) { $ReleasePath = Join-Path $Root "release" }
if (-not $Output) { $Output = Join-Path $ReleasePath "LOCAL_SECURITY_SCAN.json" }
$ReleasePath = [System.IO.Path]::GetFullPath($ReleasePath)
$Output = [System.IO.Path]::GetFullPath($Output)
$Version = & (Join-Path $Root ".venv\Scripts\python.exe") -c "from light_image_editor.version import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0) { throw "Unable to read application version." }
$releasePrefix = [System.IO.Path]::GetFullPath($ReleasePath).TrimEnd("\") + "\"

function Get-RelativeReleasePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($releasePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escaped release directory: $fullPath"
    }
    return $fullPath.Substring($releasePrefix.Length).Replace("\", "/")
}

$binaries = @(
    (Join-Path $ReleasePath "LightImageEditor\ImageEditor.exe"),
    (Join-Path $ReleasePath "CSImage-Portable-x64-$Version.exe"),
    (Join-Path $ReleasePath "CSImage-Setup-x64-$Version.exe")
)
$binaries += @(Get-ChildItem -LiteralPath $ReleasePath -Filter "CSImage-Rollback-x64-*.exe" -File | ForEach-Object { $_.FullName })
$signatureResults = @()
foreach ($binary in $binaries) {
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw "Missing binary: $binary" }
    $signature = Get-AuthenticodeSignature -LiteralPath $binary
    $thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { "" }
    $timestampSubject = if ($signature.TimeStamperCertificate) { $signature.TimeStamperCertificate.Subject } else { "" }
    $signatureResults += [PSCustomObject]@{
        File = Get-RelativeReleasePath $binary
        Status = $signature.Status.ToString()
        Thumbprint = $thumbprint
        TimestampSubject = $timestampSubject
        Sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
        Size = (Get-Item -LiteralPath $binary).Length
    }
}

$defenderStatus = "unavailable"
$defenderVersion = ""
$defenderResults = @()
try {
    $status = Get-MpComputerStatus -ErrorAction Stop
    $defenderVersion = $status.AntivirusSignatureVersion
    $platformRoots = Get-ChildItem `
        -LiteralPath (Join-Path $env:ProgramData "Microsoft\Windows Defender\Platform") `
        -Directory -ErrorAction Stop | Sort-Object Name -Descending
    $scanner = Join-Path $platformRoots[0].FullName "MpCmdRun.exe"
    foreach ($binary in $binaries) {
        & $scanner -Scan -ScanType 3 -File $binary -DisableRemediation | Out-Null
        $scanExit = $LASTEXITCODE
        $defenderResults += [PSCustomObject]@{
            File = Get-RelativeReleasePath $binary
            ExitCode = $scanExit
            Passed = $scanExit -eq 0
        }
    }
    $defenderStatus = if (@($defenderResults | Where-Object { -not $_.Passed }).Count) { "failed" } else { "passed" }
}
catch {
    $defenderStatus = "unavailable"
    $defenderResults = @([PSCustomObject]@{ Error = $_.Exception.Message; Passed = $false })
}

$allSigned = @($signatureResults | Where-Object { $_.Status -ne "Valid" -or -not $_.TimestampSubject }).Count -eq 0
$defenderPassed = $defenderStatus -eq "passed"
$multiEngineApproved = $false
if ($MultiEngineEvidence) {
    if (-not (Test-Path -LiteralPath $MultiEngineEvidence -PathType Leaf)) {
        throw "Multi-engine evidence does not exist: $MultiEngineEvidence"
    }
    $external = Get-Content -LiteralPath $MultiEngineEvidence -Raw | ConvertFrom-Json
    if (
        $external.Approved -eq $true -and
        $external.Provider -and
        $external.ReviewedUtc -and
        $external.Reviewer -and
        @($external.Artifacts).Count -eq $signatureResults.Count
    ) {
        $expected = @{}
        $multiEngineApproved = $true
        foreach ($artifact in $external.Artifacts) {
            $expected[$artifact.File] = $artifact.Sha256.ToLowerInvariant()
            if ($artifact.Result -ne "passed") { $multiEngineApproved = $false }
        }
        foreach ($signatureResult in $signatureResults) {
            if (-not $expected.ContainsKey($signatureResult.File) -or $expected[$signatureResult.File] -ne $signatureResult.Sha256) {
                $multiEngineApproved = $false
            }
        }
    }
}
$report = [ordered]@{
    SchemaVersion = 1
    GeneratedUtc = [DateTime]::UtcNow.ToString("o")
    ApplicationVersion = $Version
    AuthenticodePassed = $allSigned
    Signatures = $signatureResults
    DefenderStatus = $defenderStatus
    DefenderSignatureVersion = $defenderVersion
    DefenderScans = $defenderResults
    MultiEngineReview = if ($multiEngineApproved) { "approved" } else { "required-before-publication" }
    PublicReleaseReady = $allSigned -and $defenderPassed -and $multiEngineApproved
}
$json = $report | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($Output, $json + "`r`n", [System.Text.UTF8Encoding]::new($false))
$json
if (-not $defenderPassed -and -not $AllowUnavailableLocal) { exit 2 }
if (-not $allSigned -and -not $AllowUnsignedLocal) { exit 3 }
