[CmdletBinding()]
param(
    [string]$Executable = "",
    [ValidateRange(2, 100)]
    [int]$ActivationCount = 20
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Executable) {
    $Executable = Join-Path $Root "release\LightImageEditor\CSImage.exe"
}
$Executable = [System.IO.Path]::GetFullPath($Executable)
$rootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd("\") + "\"
if (-not $Executable.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Executable must be inside the repository: $Executable"
}
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Application executable does not exist: $Executable"
}

$fixtureDirectory = Join-Path $env:TEMP ("light-image-editor-single-instance-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $fixtureDirectory | Out-Null

function Get-TestProcesses {
    @(Get-Process -Name "CSImage" -ErrorAction SilentlyContinue | Where-Object {
        try {
            [System.IO.Path]::GetFullPath($_.Path) -eq $Executable
        }
        catch {
            $false
        }
    })
}

try {
    Add-Type -AssemblyName System.Drawing
    $paths = @()
    for ($index = 0; $index -lt $ActivationCount; $index++) {
        $path = Join-Path $fixtureDirectory ("image-{0:D2}.png" -f $index)
        $bitmap = [System.Drawing.Bitmap]::new(96, 64)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.Clear([System.Drawing.Color]::FromArgb(($index * 31) % 255, 80, 140))
            }
            finally {
                $graphics.Dispose()
            }
            $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally {
            $bitmap.Dispose()
        }
        $paths += $path
    }

    foreach ($path in $paths) {
        Start-Process -FilePath $Executable -ArgumentList @("`"$path`"") | Out-Null
        Start-Sleep -Milliseconds 35
    }

    $expectedTitle = [System.IO.Path]::GetFileName($paths[-1])
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $processes = @()
    while ([DateTime]::UtcNow -lt $deadline) {
        $processes = @(Get-TestProcesses)
        if ($processes.Count -eq 1 -and $processes[0].MainWindowTitle -like "$expectedTitle*") {
            break
        }
        Start-Sleep -Milliseconds 100
    }
    if ($processes.Count -ne 1) {
        throw "Expected one packaged main process after $ActivationCount activations, found $($processes.Count)."
    }
    if ($processes[0].MainWindowTitle -notlike "$expectedTitle*") {
        throw "Final activation was not displayed. Window title: $($processes[0].MainWindowTitle)"
    }

    $exitRequest = Start-Process `
        -FilePath $Executable `
        -ArgumentList @("--request-exit-for-upgrade") `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($exitRequest.ExitCode -ne 0) {
        throw "Safe IPC exit request failed with code $($exitRequest.ExitCode)."
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (@(Get-TestProcesses).Count -ne 0 -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
    }
    if (@(Get-TestProcesses).Count -ne 0) {
        throw "The packaged main process did not exit through IPC."
    }

    [PSCustomObject]@{
        Activations = $ActivationCount
        MainProcesses = 1
        FinalFile = $expectedTitle
        SafeIpcExit = "passed"
    } | Format-List
}
finally {
    $remaining = @(Get-TestProcesses)
    if ($remaining.Count -gt 0) {
        Write-Warning "A packaged test process remains running; no forced termination was attempted."
    }
    $resolvedFixture = [System.IO.Path]::GetFullPath($fixtureDirectory)
    $tempPrefix = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd("\") + "\"
    if ($resolvedFixture.StartsWith($tempPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedFixture -Recurse -Force -ErrorAction SilentlyContinue
    }
}
