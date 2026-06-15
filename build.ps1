# Build OrynOutreach.exe with PyInstaller.
#
#   .\build.ps1            # build, output dist\OrynOutreach\
#   .\build.ps1 -OneFile   # build into a single .exe (slower cold start)
#   .\build.ps1 -Open      # open the dist folder when done
#   .\build.ps1 -Clean     # wipe build/dist/icon before building
#
# Output (default --onedir): dist\OrynOutreach\OrynOutreach.exe
# Place leads.json / emails.csv / config.json / demos\ next to the .exe.

param(
    [switch]$OneFile,
    [switch]$Open,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }

Step "Checking Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Host "Python not on PATH." -ForegroundColor Red; exit 1 }
python --version

Step "Installing/refreshing dependencies"
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python -m pip install -q pyinstaller

if ($Clean) {
    Step "Cleaning previous build outputs"
    foreach ($p in @("build", "dist", "OrynOutreach.spec", "icon.ico", "icon.png")) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
} else {
    foreach ($p in @("build", "dist", "OrynOutreach.spec")) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
}

if (-not (Test-Path "icon.ico")) {
    Step "Generating icon (icon.ico + icon.png)"
    python make_icon.py
}

Step "Building executable"
$args = @(
    "--noconfirm",
    "--windowed",
    "--name", "OrynOutreach",
    "--icon", "icon.ico",
    "--collect-all", "customtkinter",
    "--collect-data", "anthropic",
    "--hidden-import", "bs4",
    "--hidden-import", "certifi",
    "--add-data", "icon.ico$([System.IO.Path]::PathSeparator).",
    "--add-data", "icon.png$([System.IO.Path]::PathSeparator)."
)
if ($OneFile) { $args = @("--onefile") + $args }
else          { $args = @("--onedir")  + $args }
$args += "main.py"

python -m PyInstaller @args

$exePath = if ($OneFile) {
    "dist\OrynOutreach.exe"
} else {
    "dist\OrynOutreach\OrynOutreach.exe"
}

if (Test-Path $exePath) {
    Write-Host ""
    Write-Host "Done: $exePath" -ForegroundColor Green
    Write-Host "First run creates leads.json / emails.csv / config.json / demos\ next to the .exe."
    if ($Open) {
        $folder = Split-Path -Parent $exePath
        Start-Process explorer.exe $folder
    }
} else {
    Write-Host "Build failed." -ForegroundColor Red
    exit 1
}
