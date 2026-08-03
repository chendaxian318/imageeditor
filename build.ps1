param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
$Release = Join-Path $Root "release"

if (!(Test-Path $Python)) {
    python -m venv $Venv
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "requirements.txt")

if (!$SkipTests) {
    & $Python -m unittest -v
}

if (Test-Path $Dist) { Remove-Item -LiteralPath $Dist -Recurse -Force }
if (Test-Path $Build) { Remove-Item -LiteralPath $Build -Recurse -Force }
if (Test-Path $Release) { Remove-Item -LiteralPath $Release -Recurse -Force }
New-Item -ItemType Directory -Path $Release | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name ImageEditor `
    (Join-Path $Root "image_editor.py")

Copy-Item -LiteralPath (Join-Path $Dist "ImageEditor.exe") -Destination (Join-Path $Release "ImageEditor.exe")

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name ImageEditorSetup `
    --add-binary "$(Join-Path $Release "ImageEditor.exe");." `
    (Join-Path $Root "installer.py")

Copy-Item -LiteralPath (Join-Path $Dist "ImageEditorSetup.exe") -Destination (Join-Path $Release "ImageEditorSetup.exe")

Write-Host "Build complete:"
Write-Host "  $Release\ImageEditor.exe"
Write-Host "  $Release\ImageEditorSetup.exe"
